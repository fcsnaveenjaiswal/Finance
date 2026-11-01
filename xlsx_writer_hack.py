#!/usr/bin/env python3
"""
Finance Hack: a small, standard-library-only .xlsx writer.

The reconciliation tools hand their results to an accountant, and an accountant
works in Excel. This module writes a real workbook - styled cells, live
formulas, frozen headers, filters, drop-down lists and conditional colours -
using nothing but `zipfile` and hand-built XML, so it runs on an air-gapped
machine with no `openpyxl` or `xlsxwriter` installed.

It is deliberately narrow: it writes workbooks, it does not read or edit them.

    book = Workbook(title="ITC workpaper")
    sheet = book.add_sheet("Summary")
    sheet.write(0, 0, "Particulars", Style(bold=True, fill="F4B083"))
    sheet.write(1, 0, 100.0)
    sheet.write(2, 0, 50.0)
    sheet.formula(3, 0, "SUM(A2:A3)", cached=150.0)
    book.save("out.xlsx")

Rows and columns are 0-based in the API. A formula is written together with a
cached value, so viewers that do not recalculate (a phone preview, a mail
client) still show numbers; Excel recalculates everything on opening anyway.

The output is deterministic: the same calls always give the same bytes, which
lets a work-paper be checksummed and compared between runs.
"""

import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape, quoteattr

NUM_GENERAL = "General"
NUM_INTEGER = "#,##0"
NUM_MONEY = '#,##0.00_);[Red](#,##0.00);"-"_)'
NUM_DATE = "dd-mmm-yyyy"
NUM_PERCENT = "0.00%"
NUM_TEXT = "@"

MAX_ROWS = 1_048_576
MAX_COLUMNS = 16_384
MAX_TEXT = 32_767

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_EPOCH = date(1899, 12, 30)
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_BAD_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_BUILTIN_FORMATS = {
    "General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10,
    "@": 49,
}

Value = bool | int | float | str | date | datetime | None


@dataclass(frozen=True)
class Style:
    """The look of a cell. Frozen, so identical styles share one workbook format."""
    font_name: str = "Arial"
    font_size: float = 10
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_color: Optional[str] = None      # "RRGGBB"
    fill: Optional[str] = None            # "RRGGBB"
    num_format: str = NUM_GENERAL
    halign: Optional[str] = None          # left | center | right
    valign: Optional[str] = None          # top | center | bottom
    wrap: bool = False
    border: str = ""                      # "" | "thin" | "total" (thin top, double bottom)
    indent: int = 0

    def but(self, **changes) -> "Style":
        """A copy of this style with some attributes changed."""
        return replace(self, **changes)


@dataclass(frozen=True)
class Dxf:
    """The look applied by a conditional-format rule."""
    font_color: Optional[str] = None
    fill: Optional[str] = None
    bold: bool = False


def col_letter(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    if index < 0 or index >= MAX_COLUMNS:
        raise ValueError(f"column {index} is outside the sheet")
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def cell_ref(row: int, col: int, absolute: bool = False) -> str:
    """(0, 0) -> 'A1'; with absolute=True -> '$A$1'."""
    if row < 0 or row >= MAX_ROWS:
        raise ValueError(f"row {row} is outside the sheet")
    mark = "$" if absolute else ""
    return f"{mark}{col_letter(col)}{mark}{row + 1}"


def range_ref(row1: int, col1: int, row2: int, col2: int, absolute: bool = False) -> str:
    """The A1 range covering the two corners."""
    return f"{cell_ref(row1, col1, absolute)}:{cell_ref(row2, col2, absolute)}"


def sqref(row1: int, col1: int, row2: int, col2: int) -> str:
    """A range as Excel writes it in sqref: a lone cell is 'A2', not 'A2:A2'."""
    if (row1, col1) == (row2, col2):
        return cell_ref(row1, col1)
    return range_ref(row1, col1, row2, col2)


def quote_sheet(name: str) -> str:
    """The sheet name as it must appear inside a formula."""
    return "'" + name.replace("'", "''") + "'"


def excel_serial(value: date | datetime) -> float:
    """Excel's day number for a date or datetime (1900 date system)."""
    if isinstance(value, datetime):
        delta = value - datetime(1899, 12, 30)
        return delta.days + delta.seconds / 86400.0
    return float((value - _EPOCH).days)


def _clean(text: str) -> str:
    text = _ILLEGAL_XML.sub("", text)
    return text[:MAX_TEXT]


def _rgb(color: str) -> str:
    color = color.lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", color):
        raise ValueError(f"colour must be six hex digits, got {color!r}")
    return "FF" + color


def _number(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("NaN and infinity cannot be stored in a spreadsheet cell")
    if isinstance(value, int):
        return str(value)
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


class _Cell:
    """One stored cell: a value or a formula, plus the index of its format."""

    __slots__ = ("value", "formula", "xf")

    def __init__(self, value: Value, formula: Optional[str], xf: int) -> None:
        self.value = value
        self.formula = formula
        self.xf = xf


class Sheet:
    """One worksheet. Create it through Workbook.add_sheet()."""

    def __init__(self, book: "Workbook", name: str, tab_color: Optional[str]) -> None:
        self.book = book
        self.name = name
        self.tab_color = tab_color
        self._cells: Dict[Tuple[int, int], _Cell] = {}
        self._widths: Dict[int, float] = {}
        self._heights: Dict[int, float] = {}
        self._merges: List[Tuple[int, int, int, int]] = []
        self._freeze: Optional[Tuple[int, int]] = None
        self._filter: Optional[Tuple[int, int, int, int]] = None
        self._validations: List[Tuple[Tuple[int, int, int, int], str]] = []
        self._conditions: List[Tuple[Tuple[int, int, int, int], str, int, str]] = []
        self._links: List[Tuple[int, int, str]] = []
        self._print_titles: Optional[Tuple[int, int]] = None
        self._landscape = False
        self._fit_width = False
        self._gridlines = True
        self._zoom: Optional[int] = None

    # ------------------------------------------------------------------ cells
    def write(self, row: int, col: int, value: Value, style: Optional[Style] = None) -> None:
        """Store a value. Dates get a date format unless the style names one."""
        style = style or Style()
        if isinstance(value, (date, datetime)) and style.num_format == NUM_GENERAL:
            style = style.but(num_format=NUM_DATE)
        self._put(row, col, _Cell(value, None, self.book._xf(style)))

    def formula(
        self,
        row: int,
        col: int,
        formula: str,
        cached: Value = None,
        style: Optional[Style] = None,
    ) -> None:
        """
        Store a formula (written without the leading '=') and its cached result.

        The cached value is what a viewer that never recalculates will show; give
        it whenever it is known.
        """
        formula = _clean(formula.lstrip("="))
        if not formula:
            raise ValueError("empty formula")
        self._put(row, col, _Cell(cached, formula, self.book._xf(style or Style())))

    def write_row(
        self,
        row: int,
        col: int,
        values: Sequence[Value],
        style: Optional[Style] = None,
    ) -> None:
        """Store several values left to right."""
        for offset, value in enumerate(values):
            self.write(row, col + offset, value, style)

    def _put(self, row: int, col: int, cell: _Cell) -> None:
        if not (0 <= row < MAX_ROWS and 0 <= col < MAX_COLUMNS):
            raise ValueError(f"cell ({row}, {col}) is outside the sheet")
        self._cells[(row, col)] = cell

    def get(self, row: int, col: int) -> Value:
        """The value (or cached formula result) stored at a cell, for tests."""
        cell = self._cells.get((row, col))
        return None if cell is None else cell.value

    def formula_at(self, row: int, col: int) -> Optional[str]:
        """The formula stored at a cell, if any."""
        cell = self._cells.get((row, col))
        return None if cell is None else cell.formula

    # --------------------------------------------------------------- layout
    def set_column_width(self, first: int, last: int, characters: float) -> None:
        """Width, in characters, of columns first..last (inclusive)."""
        if characters <= 0:
            raise ValueError("column width must be positive")
        for col in range(first, last + 1):
            self._widths[col] = characters

    def set_row_height(self, row: int, points: float) -> None:
        self._heights[row] = points

    def merge(self, row1: int, col1: int, row2: int, col2: int) -> None:
        """Merge a block; write the text to its top-left cell."""
        if (row2, col2) == (row1, col1):
            return
        self._merges.append((row1, col1, row2, col2))

    def freeze_panes(self, row: int, col: int = 0) -> None:
        """Keep the rows above `row` and the columns left of `col` in view."""
        self._freeze = (row, col)

    def autofilter(self, row1: int, col1: int, row2: int, col2: int) -> None:
        """Put filter buttons on the header row `row1` of a table."""
        self._filter = (row1, col1, row2, col2)

    def hide_gridlines(self) -> None:
        self._gridlines = False

    def set_zoom(self, percent: int) -> None:
        self._zoom = percent

    def landscape(self, fit_width: bool = True) -> None:
        """Print landscape; with fit_width every column lands on one page width."""
        self._landscape = True
        self._fit_width = fit_width

    def print_titles(self, first_row: int, last_row: int) -> None:
        """Repeat these rows at the top of every printed page."""
        self._print_titles = (first_row, last_row)

    # ------------------------------------------------- validation and colour
    def validation_list(
        self,
        row1: int,
        col1: int,
        row2: int,
        col2: int,
        source: Sequence[str] | str,
    ) -> None:
        """
        A drop-down list on a block of cells. `source` is either the allowed
        values, or a range formula such as "=Lists!$A$1:$A$9".
        """
        if isinstance(source, str):
            formula = source.lstrip("=")
        else:
            items = [str(item) for item in source]
            if any("," in item or '"' in item for item in items):
                raise ValueError("list items cannot contain commas or quotes; use a range")
            formula = '"' + ",".join(items) + '"'
            if len(formula) > 257:
                raise ValueError("an inline list is limited to 255 characters; use a range")
        self._validations.append(((row1, col1, row2, col2), formula))

    def conditional_cell(
        self,
        row1: int,
        col1: int,
        row2: int,
        col2: int,
        operator: str,
        formula: str,
        dxf: Dxf,
    ) -> None:
        """Colour cells whose value passes a test, e.g. operator='equal', formula='"OK"'."""
        self._conditions.append((
            (row1, col1, row2, col2),
            f'type="cellIs" operator={quoteattr(operator)}',
            self.book._dxf(dxf),
            formula,
        ))

    def conditional_expression(
        self,
        row1: int,
        col1: int,
        row2: int,
        col2: int,
        formula: str,
        dxf: Dxf,
    ) -> None:
        """Colour cells for which a formula (relative to the top-left cell) is true."""
        self._conditions.append((
            (row1, col1, row2, col2),
            'type="expression"',
            self.book._dxf(dxf),
            formula,
        ))

    def link(self, row: int, col: int, target_sheet: str, target_cell: str = "A1") -> None:
        """Make a cell jump to another sheet of this workbook."""
        self._links.append((row, col, f"{quote_sheet(target_sheet)}!{target_cell}"))

    # ------------------------------------------------------------------ xml
    def _dimension(self) -> str:
        if not self._cells:
            return "A1"
        rows = [key[0] for key in self._cells]
        cols = [key[1] for key in self._cells]
        return range_ref(min(rows), min(cols), max(rows), max(cols))

    def _cell_xml(self, row: int, col: int, cell: _Cell) -> str:
        ref = cell_ref(row, col)
        style = f' s="{cell.xf}"' if cell.xf else ""
        value = cell.value
        if cell.formula is not None:
            body = f"<f>{escape(cell.formula)}</f>"
            if value is None:
                return f'<c r="{ref}"{style}>{body}</c>'
            if isinstance(value, bool):
                return f'<c r="{ref}"{style} t="b">{body}<v>{int(value)}</v></c>'
            if isinstance(value, str):
                return f'<c r="{ref}"{style} t="str">{body}<v>{escape(_clean(value))}</v></c>'
            if isinstance(value, (date, datetime)):
                value = excel_serial(value)
            return f'<c r="{ref}"{style}>{body}<v>{_number(value)}</v></c>'
        if value is None or value == "":
            return f'<c r="{ref}"{style}/>'
        if isinstance(value, bool):
            return f'<c r="{ref}"{style} t="b"><v>{int(value)}</v></c>'
        if isinstance(value, str):
            index = self.book._shared(_clean(value))
            return f'<c r="{ref}"{style} t="s"><v>{index}</v></c>'
        if isinstance(value, (date, datetime)):
            return f'<c r="{ref}"{style}><v>{_number(excel_serial(value))}</v></c>'
        return f'<c r="{ref}"{style}><v>{_number(value)}</v></c>'

    def to_xml(self, selected: bool) -> str:
        parts: List[str] = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
            f'<worksheet xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">',
        ]
        props = ""
        if self.tab_color:
            props += f'<tabColor rgb="{_rgb(self.tab_color)}"/>'
        if self._fit_width:
            props += '<pageSetUpPr fitToPage="1"/>'
        if props:
            parts.append(f"<sheetPr>{props}</sheetPr>")
        parts.append(f'<dimension ref="{self._dimension()}"/>')

        view = '<sheetView workbookViewId="0"'
        if not self._gridlines:
            view += ' showGridLines="0"'
        if self._zoom:
            view += f' zoomScale="{self._zoom}" zoomScaleNormal="{self._zoom}"'
        if selected:
            view += ' tabSelected="1"'
        view += ">"
        if self._freeze and (self._freeze[0] or self._freeze[1]):
            row, col = self._freeze
            top_left = cell_ref(row, col)
            split = ""
            if col:
                split += f' xSplit="{col}"'
            if row:
                split += f' ySplit="{row}"'
            pane = "bottomRight" if row and col else ("bottomLeft" if row else "topRight")
            view += f'<pane{split} topLeftCell="{top_left}" activePane="{pane}" state="frozen"/>'
            if row and col:
                view += '<selection pane="topRight"/><selection pane="bottomLeft"/>'
            view += f'<selection pane="{pane}" activeCell="{top_left}" sqref="{top_left}"/>'
        view += "</sheetView>"
        parts.append(f"<sheetViews>{view}</sheetViews>")
        parts.append('<sheetFormatPr defaultRowHeight="15"/>')

        if self._widths:
            cols = []
            for col in sorted(self._widths):
                width = round((self._widths[col] * 7 + 5) / 7, 7)
                cols.append(
                    f'<col min="{col + 1}" max="{col + 1}" width="{width}" customWidth="1"/>'
                )
            parts.append("<cols>" + "".join(cols) + "</cols>")

        by_row: Dict[int, List[Tuple[int, _Cell]]] = {}
        for (row, col), cell in self._cells.items():
            by_row.setdefault(row, []).append((col, cell))
        for row in self._heights:
            by_row.setdefault(row, [])
        parts.append("<sheetData>")
        for row in sorted(by_row):
            height = ""
            if row in self._heights:
                height = f' ht="{self._heights[row]}" customHeight="1"'
            ordered = sorted(by_row[row], key=lambda item: item[0])
            cells = "".join(self._cell_xml(row, col, cell) for col, cell in ordered)
            parts.append(f'<row r="{row + 1}"{height}>{cells}</row>')
        parts.append("</sheetData>")

        if self._filter:
            parts.append(f'<autoFilter ref="{range_ref(*self._filter)}"/>')
        if self._merges:
            merged = "".join(f'<mergeCell ref="{range_ref(*block)}"/>' for block in self._merges)
            parts.append(f'<mergeCells count="{len(self._merges)}">{merged}</mergeCells>')
        for priority, (block, rule, dxf_id, formula) in enumerate(self._conditions, start=1):
            parts.append(
                f'<conditionalFormatting sqref="{sqref(*block)}">'
                f'<cfRule {rule} dxfId="{dxf_id}" priority="{priority}">'
                f"<formula>{escape(formula)}</formula></cfRule></conditionalFormatting>"
            )
        if self._validations:
            items = "".join(
                f'<dataValidation type="list" allowBlank="1" showInputMessage="1" '
                f'showErrorMessage="1" sqref="{sqref(*block)}">'
                f"<formula1>{escape(formula)}</formula1></dataValidation>"
                for block, formula in self._validations
            )
            count = len(self._validations)
            parts.append(f'<dataValidations count="{count}">{items}</dataValidations>')
        if self._links:
            links = []
            for row, col, location in self._links:
                shown = self._cells[(row, col)].value if (row, col) in self._cells else None
                display = f" display={quoteattr(_clean(shown))}" if isinstance(shown, str) else ""
                links.append(
                    f'<hyperlink ref="{cell_ref(row, col)}" location={quoteattr(location)}'
                    f"{display}/>"
                )
            parts.append(f"<hyperlinks>{''.join(links)}</hyperlinks>")
        parts.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" '
                     'header="0.3" footer="0.3"/>')
        if self._landscape:
            fit = ' fitToWidth="1" fitToHeight="0"' if self._fit_width else ""
            parts.append(f'<pageSetup paperSize="9" orientation="landscape"{fit}/>')
        parts.append("</worksheet>")
        return "".join(parts)


class Workbook:
    """A workbook under construction. Add sheets, write cells, then save()."""

    def __init__(self, title: str = "", author: str = "", created: str = "2000-01-01T00:00:00Z"):
        self.title = title
        self.author = author
        self.created = created
        self._sheets: List[Sheet] = []
        self._strings: Dict[str, int] = {}
        self._string_refs = 0
        self._fonts: Dict[Tuple, int] = {}
        self._fills: Dict[Optional[str], int] = {None: 0, "__gray125__": 1}
        self._borders: Dict[str, int] = {"": 0}
        self._formats: Dict[str, int] = {}
        self._xfs: Dict[Tuple[int, int, int, int, Tuple], int] = {}
        self._dxfs: Dict[Dxf, int] = {}
        self._xf(Style())          # format 0 is the workbook default

    # ---------------------------------------------------------------- sheets
    def add_sheet(self, name: str, tab_color: Optional[str] = None) -> Sheet:
        """Append a worksheet; names are 1-31 characters, unique, and free of []:*?/\\ ."""
        if not name or len(name) > 31:
            raise ValueError("a sheet name must be 1 to 31 characters")
        if _BAD_SHEET_NAME.search(name) or name.startswith("'") or name.endswith("'"):
            raise ValueError(f"sheet name {name!r} contains a character Excel does not allow")
        if any(sheet.name.lower() == name.lower() for sheet in self._sheets):
            raise ValueError(f"duplicate sheet name {name!r}")
        sheet = Sheet(self, name, tab_color)
        self._sheets.append(sheet)
        return sheet

    @property
    def sheets(self) -> List[Sheet]:
        return list(self._sheets)

    def sheet(self, name: str) -> Sheet:
        for sheet in self._sheets:
            if sheet.name == name:
                return sheet
        raise KeyError(name)

    # ---------------------------------------------------------- shared tables
    def _shared(self, text: str) -> int:
        self._string_refs += 1
        if text not in self._strings:
            self._strings[text] = len(self._strings)
        return self._strings[text]

    def _font(self, style: Style) -> int:
        if style.font_color:
            _rgb(style.font_color)          # fail here, where the mistake was made
        key = (style.font_name, style.font_size, style.bold, style.italic, style.underline,
               style.font_color)
        return self._fonts.setdefault(key, len(self._fonts))

    def _fill(self, color: Optional[str]) -> int:
        if color is None:
            return 0
        color = _rgb(color)
        if color not in self._fills:
            self._fills[color] = len(self._fills)
        return self._fills[color]

    def _border(self, kind: str) -> int:
        if kind not in ("", "thin", "total"):
            raise ValueError(f"unknown border style {kind!r}")
        return self._borders.setdefault(kind, len(self._borders))

    def _format(self, code: str) -> int:
        if code in _BUILTIN_FORMATS:
            return _BUILTIN_FORMATS[code]
        if code not in self._formats:
            self._formats[code] = 164 + len(self._formats)
        return self._formats[code]

    def _xf(self, style: Style) -> int:
        key = (
            self._format(style.num_format),
            self._font(style),
            self._fill(style.fill),
            self._border(style.border),
            (style.halign, style.valign, style.wrap, style.indent),
        )
        return self._xfs.setdefault(key, len(self._xfs))

    def _dxf(self, dxf: Dxf) -> int:
        for color in (dxf.font_color, dxf.fill):
            if color:
                _rgb(color)
        return self._dxfs.setdefault(dxf, len(self._dxfs))

    # ---------------------------------------------------------------- xml parts
    def _styles_xml(self) -> str:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               f'<styleSheet xmlns="{_MAIN_NS}">']
        if self._formats:
            items = "".join(
                f'<numFmt numFmtId="{ident}" formatCode={quoteattr(code)}/>'
                for code, ident in sorted(self._formats.items(), key=lambda kv: kv[1])
            )
            out.append(f'<numFmts count="{len(self._formats)}">{items}</numFmts>')
        fonts = []
        for (name, size, bold, italic, underline, color), _ident in sorted(
                self._fonts.items(), key=lambda kv: kv[1]):
            fonts.append(
                "<font>" + ("<b/>" if bold else "") + ("<i/>" if italic else "")
                + ("<u/>" if underline else "") + f'<sz val="{size}"/>'
                + (f'<color rgb="{_rgb(color)}"/>' if color else "")
                + f"<name val={quoteattr(name)}/><family val=\"2\"/></font>"
            )
        out.append(f'<fonts count="{len(fonts)}">{"".join(fonts)}</fonts>')
        fills = ['<fill><patternFill patternType="none"/></fill>',
                 '<fill><patternFill patternType="gray125"/></fill>']
        for color, _ident in sorted(self._fills.items(), key=lambda kv: kv[1]):
            if color in (None, "__gray125__"):
                continue
            fills.append(f'<fill><patternFill patternType="solid"><fgColor rgb="{color}"/>'
                         f'<bgColor indexed="64"/></patternFill></fill>')
        out.append(f'<fills count="{len(fills)}">{"".join(fills)}</fills>')
        thin = '<{0} style="thin"><color rgb="FF808080"/></{0}>'
        borders = []
        for kind, _ident in sorted(self._borders.items(), key=lambda kv: kv[1]):
            if kind == "thin":
                sides = "".join(thin.format(side) for side in ("left", "right", "top", "bottom"))
                borders.append(f"<border>{sides}<diagonal/></border>")
            elif kind == "total":
                borders.append("<border><left/><right/>" + thin.format("top")
                               + '<bottom style="double"><color rgb="FF808080"/></bottom>'
                               + "<diagonal/></border>")
            else:
                borders.append("<border><left/><right/><top/><bottom/><diagonal/></border>")
        out.append(f'<borders count="{len(borders)}">{"".join(borders)}</borders>')
        out.append('<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
                   "</cellStyleXfs>")
        xfs = []
        for (fmt, font, fill, border, align), _ident in sorted(
                self._xfs.items(), key=lambda kv: kv[1]):
            halign, valign, wrap, indent = align
            attrs = (f'numFmtId="{fmt}" fontId="{font}" fillId="{fill}" borderId="{border}" '
                     f'xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"')
            if halign or valign or wrap or indent:
                inner = "<alignment"
                if halign:
                    inner += f' horizontal="{halign}"'
                if valign:
                    inner += f' vertical="{valign}"'
                if wrap:
                    inner += ' wrapText="1"'
                if indent:
                    inner += f' indent="{indent}"'
                xfs.append(f'<xf {attrs} applyAlignment="1">{inner}/></xf>')
            else:
                xfs.append(f"<xf {attrs}/>")
        out.append(f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>')
        out.append('<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
                   "</cellStyles>")
        dxfs = []
        for dxf, _ident in sorted(self._dxfs.items(), key=lambda kv: kv[1]):
            font = ""
            if dxf.bold or dxf.font_color:
                font = ("<font>" + ("<b/>" if dxf.bold else "")
                        + (f'<color rgb="{_rgb(dxf.font_color)}"/>' if dxf.font_color else "")
                        + "</font>")
            fill = (f'<fill><patternFill><bgColor rgb="{_rgb(dxf.fill)}"/></patternFill></fill>'
                    if dxf.fill else "")
            dxfs.append(f"<dxf>{font}{fill}</dxf>")
        out.append(f'<dxfs count="{len(dxfs)}">{"".join(dxfs)}</dxfs>')
        out.append('<tableStyles count="0" defaultTableStyle="TableStyleMedium9" '
                   'defaultPivotStyle="PivotStyleLight16"/>')
        out.append("</styleSheet>")
        return "".join(out)

    def _shared_xml(self) -> str:
        items = "".join(
            f'<si><t xml:space="preserve">{escape(text)}</t></si>'
            for text, _ident in sorted(self._strings.items(), key=lambda kv: kv[1])
        )
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<sst xmlns="{_MAIN_NS}" count="{self._string_refs}" '
                f'uniqueCount="{len(self._strings)}">{items}</sst>')

    def _workbook_xml(self) -> str:
        sheets = "".join(
            f'<sheet name={quoteattr(sheet.name)} sheetId="{number}" r:id="rId{number}"/>'
            for number, sheet in enumerate(self._sheets, start=1)
        )
        names = []
        for index, sheet in enumerate(self._sheets):
            if sheet._filter:
                row1, col1, row2, col2 = sheet._filter
                names.append(
                    f'<definedName name="_xlnm._FilterDatabase" localSheetId="{index}" hidden="1">'
                    f"{escape(quote_sheet(sheet.name))}!{range_ref(row1, col1, row2, col2, True)}"
                    f"</definedName>"
                )
            if sheet._print_titles:
                first, last = sheet._print_titles
                names.append(
                    f'<definedName name="_xlnm.Print_Titles" localSheetId="{index}">'
                    f"{escape(quote_sheet(sheet.name))}!${first + 1}:${last + 1}</definedName>"
                )
        defined = f"<definedNames>{''.join(names)}</definedNames>" if names else ""
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">'
                f'<workbookPr/>'
                f'<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" '
                f'windowHeight="12300" activeTab="0"/></bookViews>'
                f"<sheets>{sheets}</sheets>{defined}"
                f'<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')

    def _parts(self) -> List[Tuple[str, str]]:
        if not self._sheets:
            raise ValueError("a workbook needs at least one sheet")
        sheet_xml = [
            sheet.to_xml(selected=(number == 0)) for number, sheet in enumerate(self._sheets)
        ]
        count = len(self._sheets)
        content = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
        ]
        for number in range(1, count + 1):
            content.append(
                f'<Override PartName="/xl/worksheets/sheet{number}.xml" ContentType='
                f'"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        content.append(
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-'
            'package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.extended-properties+xml"/></Types>'
        )
        root_rels = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{_PKG_REL_NS}">'
            f'<Relationship Id="rId1" Type="{_REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
            f'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            f'<Relationship Id="rId3" Type="{_REL_NS}/extended-properties" '
            f'Target="docProps/app.xml"/></Relationships>'
        )
        book_rels = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     f'<Relationships xmlns="{_PKG_REL_NS}">']
        for number in range(1, count + 1):
            book_rels.append(
                f'<Relationship Id="rId{number}" Type="{_REL_NS}/worksheet" '
                f'Target="worksheets/sheet{number}.xml"/>'
            )
        book_rels.append(
            f'<Relationship Id="rId{count + 1}" Type="{_REL_NS}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId{count + 2}" Type="{_REL_NS}/sharedStrings" '
            f'Target="sharedStrings.xml"/></Relationships>'
        )
        core = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/'
            'dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(_clean(self.title))}</dc:title>"
            f"<dc:creator>{escape(_clean(self.author))}</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{escape(self.created)}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{escape(self.created)}</dcterms:modified>'
            "</cp:coreProperties>"
        )
        app = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'extended-properties"><Application>Microsoft Excel</Application></Properties>'
        )
        parts = [
            ("[Content_Types].xml", "".join(content)),
            ("_rels/.rels", root_rels),
            ("docProps/core.xml", core),
            ("docProps/app.xml", app),
            ("xl/workbook.xml", self._workbook_xml()),
            ("xl/_rels/workbook.xml.rels", "".join(book_rels)),
        ]
        for number, xml in enumerate(sheet_xml, start=1):
            parts.append((f"xl/worksheets/sheet{number}.xml", xml))
        # Written after the sheets: producing them registers the strings and formats.
        parts.append(("xl/styles.xml", self._styles_xml()))
        parts.append(("xl/sharedStrings.xml", self._shared_xml()))
        return parts

    def save(self, path: str) -> None:
        """Write the workbook to `path`. Identical calls give identical bytes."""
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, xml in self._parts():
                info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, xml.encode("utf-8"))


def _demo() -> None:
    book = Workbook(title="xlsx_writer_hack demo", author="finance-hacks")
    head = Style(bold=True, fill="F4B083", border="thin", halign="center")
    money = Style(num_format=NUM_MONEY, border="thin")
    sheet = book.add_sheet("Demo", tab_color="1F3864")
    sheet.write_row(0, 0, ["Item", "Amount", "Check"], head)
    items = [("Alpha", 1200.5), ("Beta", 799.5), ("Gamma", 0.0)]
    for offset, (name, amount) in enumerate(items, start=1):
        sheet.write(offset, 0, name, Style(border="thin"))
        sheet.write(offset, 1, amount, money)
    total = sum(amount for _name, amount in items)
    sheet.write(4, 0, "Total", Style(bold=True, border="total"))
    sheet.formula(4, 1, "SUM(B2:B4)", cached=total, style=money.but(bold=True, border="total"))
    sheet.formula(4, 2, 'IF(B5=2000,"OK","CHECK")', cached="OK", style=Style(bold=True))
    sheet.conditional_cell(4, 2, 4, 2, "equal", '"OK"', Dxf(font_color="006100", fill="C6EFCE"))
    sheet.set_column_width(0, 0, 18)
    sheet.set_column_width(1, 2, 14)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, 3, 2)
    path = tempfile.gettempdir() + "/xlsx_writer_hack_demo.xlsx"
    book.save(path)
    with zipfile.ZipFile(path) as archive:
        parts = len(archive.namelist())
    print(f"Wrote {path}: {len(book.sheets)} sheet(s), {parts} package parts")
    print(f"Total: {total}")


if __name__ == "__main__":
    _demo()
    sys.exit(0)
