"""Unit tests for xlsx_writer_hack."""

import os
import re
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET

from xlsx_writer_hack import (
    NUM_DATE,
    NUM_MONEY,
    Dxf,
    Style,
    Workbook,
    cell_ref,
    col_letter,
    excel_serial,
    quote_sheet,
    range_ref,
)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class SavedWorkbook:
    """Writes a workbook to a temp file and offers the parsed parts for assertions."""

    def __init__(self, book):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "book.xlsx")
        book.save(self.path)
        with zipfile.ZipFile(self.path) as archive:
            self.names = archive.namelist()
            self.raw = {name: archive.read(name) for name in self.names}
            self.infos = archive.infolist()

    def close(self):
        self.dir.cleanup()

    def xml(self, name):
        return ET.fromstring(self.raw[name])

    def sheet(self, number=1):
        return self.xml(f"xl/worksheets/sheet{number}.xml")

    def cell(self, ref, number=1):
        found = self.sheet(number).find(f".//m:c[@r='{ref}']", NS)
        return found

    def strings(self):
        root = self.xml("xl/sharedStrings.xml")
        return ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
                for si in root.findall("m:si", NS)]


class AddressTests(unittest.TestCase):
    def test_column_letters(self):
        self.assertEqual(col_letter(0), "A")
        self.assertEqual(col_letter(25), "Z")
        self.assertEqual(col_letter(26), "AA")
        self.assertEqual(col_letter(701), "ZZ")
        self.assertEqual(col_letter(702), "AAA")
        self.assertEqual(col_letter(16383), "XFD")

    def test_columns_outside_the_sheet_are_rejected(self):
        with self.assertRaises(ValueError):
            col_letter(-1)
        with self.assertRaises(ValueError):
            col_letter(16384)

    def test_cell_and_range_references(self):
        self.assertEqual(cell_ref(0, 0), "A1")
        self.assertEqual(cell_ref(9, 27, absolute=True), "$AB$10")
        self.assertEqual(range_ref(1, 0, 4, 2), "A2:C5")
        self.assertEqual(range_ref(1, 0, 4, 2, absolute=True), "$A$2:$C$5")

    def test_sheet_names_are_quoted_for_formulas(self):
        self.assertEqual(quote_sheet("2B Detail"), "'2B Detail'")
        self.assertEqual(quote_sheet("O'Brien"), "'O''Brien'")

    def test_excel_serials(self):
        self.assertEqual(excel_serial(date(2025, 6, 1)), 45809.0)
        self.assertEqual(excel_serial(date(1900, 3, 1)), 61.0)
        self.assertAlmostEqual(excel_serial(datetime(2025, 6, 1, 12, 0)), 45809.5)


class SheetRuleTests(unittest.TestCase):
    def test_sheet_name_rules(self):
        book = Workbook()
        book.add_sheet("Summary")
        for bad in ("", "x" * 32, "a/b", "a\\b", "a[b", "a]b", "a:b", "a*b", "a?b", "'a", "a'"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                book.add_sheet(bad)
        with self.assertRaises(ValueError):
            book.add_sheet("summary")          # sheet names are case-insensitive
        book.add_sheet("x" * 31)
        book.add_sheet("O'Brien")

    def test_an_empty_workbook_cannot_be_saved(self):
        with self.assertRaises(ValueError):
            Workbook().save(os.path.join(tempfile.gettempdir(), "never.xlsx"))

    def test_cells_outside_the_sheet_and_non_finite_numbers_are_rejected(self):
        sheet = Workbook().add_sheet("A")
        with self.assertRaises(ValueError):
            sheet.write(-1, 0, 1)
        with self.assertRaises(ValueError):
            sheet.write(0, 16384, 1)
        with self.assertRaises(ValueError):
            sheet.write(1048576, 0, 1)
        book = Workbook()
        bad = book.add_sheet("B")
        bad.write(0, 0, float("nan"))
        with self.assertRaises(ValueError):
            book.save(os.path.join(tempfile.gettempdir(), "never.xlsx"))

    def test_an_empty_formula_is_rejected(self):
        sheet = Workbook().add_sheet("A")
        with self.assertRaises(ValueError):
            sheet.formula(0, 0, "=")

    def test_bad_colours_and_borders_are_rejected(self):
        sheet = Workbook().add_sheet("A")
        with self.assertRaises(ValueError):
            sheet.write(0, 0, "x", Style(fill="not-a-colour"))
        with self.assertRaises(ValueError):
            sheet.write(0, 0, "x", Style(font_color="12345"))
        with self.assertRaises(ValueError):
            sheet.write(0, 0, "x", Style(border="dotted"))
        with self.assertRaises(ValueError):
            sheet.conditional_cell(0, 0, 0, 0, "equal", "1", Dxf(fill="red"))


class CellContentTests(unittest.TestCase):
    def build(self):
        book = Workbook(title="T", author="A")
        sheet = book.add_sheet("Data")
        sheet.write(0, 0, "plain")
        sheet.write(0, 1, 12.5)
        sheet.write(0, 2, 7)
        sheet.write(0, 3, True)
        sheet.write(0, 4, date(2025, 6, 1))
        sheet.write(0, 5, datetime(2025, 6, 1, 6, 0))
        sheet.write(0, 6, None)
        sheet.write(0, 7, "")
        sheet.write(1, 0, 'A & B <Ltd> "q" \' ₹ ü')
        sheet.write(1, 1, "bell\x07 and\x00null")
        sheet.write(1, 2, "line1\nline2")
        sheet.formula(2, 0, "SUM(B1:C1)", cached=19.5)
        sheet.formula(2, 1, 'IF(A3>1,"yes","no")', cached="yes")
        sheet.formula(2, 2, "A3>1", cached=True)
        sheet.formula(2, 3, "A3*2")                       # no cached value
        sheet.formula(2, 4, "=1+1", cached=2)             # a leading '=' is accepted
        self.saved = SavedWorkbook(book)
        self.addCleanup(self.saved.close)
        return self.saved

    def test_numbers_strings_booleans_and_dates(self):
        saved = self.build()
        self.assertEqual(saved.cell("B1").find("m:v", NS).text, "12.5")
        self.assertEqual(saved.cell("C1").find("m:v", NS).text, "7")
        boolean = saved.cell("D1")
        self.assertEqual((boolean.get("t"), boolean.find("m:v", NS).text), ("b", "1"))
        self.assertEqual(saved.cell("E1").find("m:v", NS).text, "45809")
        self.assertEqual(saved.cell("F1").find("m:v", NS).text, "45809.25")

    def test_a_date_gets_a_date_format_automatically(self):
        saved = self.build()
        style_index = int(saved.cell("E1").get("s"))
        xfs = saved.xml("xl/styles.xml").find("m:cellXfs", NS)
        fmt_id = xfs[style_index].get("numFmtId")
        codes = {nf.get("numFmtId"): nf.get("formatCode")
                 for nf in saved.xml("xl/styles.xml").findall(".//m:numFmt", NS)}
        self.assertEqual(codes[fmt_id], NUM_DATE)

    def test_strings_are_shared_and_escaped_and_scrubbed(self):
        saved = self.build()
        strings = saved.strings()
        self.assertIn('A & B <Ltd> "q" \' ₹ ü', strings)
        self.assertIn("bell and" + "null", strings)           # control characters removed
        self.assertIn("line1\nline2", strings)
        self.assertNotIn("\x07", "".join(strings))
        self.assertIn(b"A &amp; B &lt;Ltd&gt;", saved.raw["xl/sharedStrings.xml"])
        cell = saved.cell("A1")
        self.assertEqual(cell.get("t"), "s")
        self.assertEqual(strings[int(cell.find("m:v", NS).text)], "plain")

    def test_none_and_empty_text_write_a_blank_styled_cell(self):
        saved = self.build()
        for ref in ("G1", "H1"):
            self.assertIsNone(saved.cell(ref).find("m:v", NS))

    def test_formulas_carry_cached_results(self):
        saved = self.build()
        number = saved.cell("A3")
        self.assertEqual(number.find("m:f", NS).text, "SUM(B1:C1)")
        self.assertEqual(number.find("m:v", NS).text, "19.5")
        text = saved.cell("B3")
        self.assertEqual((text.get("t"), text.find("m:v", NS).text), ("str", "yes"))
        flag = saved.cell("C3")
        self.assertEqual((flag.get("t"), flag.find("m:v", NS).text), ("b", "1"))
        self.assertIsNone(saved.cell("D3").find("m:v", NS))
        self.assertEqual(saved.cell("E3").find("m:f", NS).text, "1+1")

    def test_workbook_recalculates_on_open(self):
        saved = self.build()
        calc = saved.xml("xl/workbook.xml").find("m:calcPr", NS)
        self.assertEqual(calc.get("fullCalcOnLoad"), "1")

    def test_reading_back_what_was_stored(self):
        book = Workbook()
        sheet = book.add_sheet("Data")
        sheet.write(0, 0, "x")
        sheet.formula(1, 0, "A1", cached="x")
        self.assertEqual(sheet.get(0, 0), "x")
        self.assertEqual(sheet.get(1, 0), "x")
        self.assertEqual(sheet.formula_at(1, 0), "A1")
        self.assertIsNone(sheet.formula_at(0, 0))
        self.assertIsNone(sheet.get(5, 5))


class StyleTests(unittest.TestCase):
    def test_identical_styles_share_one_format(self):
        book = Workbook()
        sheet = book.add_sheet("A")
        style = Style(bold=True, fill="F4B083", border="thin")
        for row in range(10):
            sheet.write(row, 0, row, style)
            sheet.write(row, 1, row, Style(bold=True, fill="F4B083", border="thin"))
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        xfs = saved.xml("xl/styles.xml").find("m:cellXfs", NS)
        self.assertEqual(len(xfs), 2)         # the default format plus this one
        self.assertEqual(xfs.get("count"), "2")

    def test_style_attributes_reach_the_stylesheet(self):
        book = Workbook()
        sheet = book.add_sheet("A")
        sheet.write(0, 0, 1.5, Style(
            font_name="Times New Roman", font_size=12, bold=True, italic=True, underline=True,
            font_color="FF0000", fill="EFF9FF", num_format=NUM_MONEY, halign="right",
            valign="top", wrap=True, border="total", indent=2,
        ))
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        styles = saved.xml("xl/styles.xml")
        font = styles.findall("m:fonts/m:font", NS)[1]
        self.assertIsNotNone(font.find("m:b", NS))
        self.assertIsNotNone(font.find("m:i", NS))
        self.assertIsNotNone(font.find("m:u", NS))
        self.assertEqual(font.find("m:sz", NS).get("val"), "12")
        self.assertEqual(font.find("m:color", NS).get("rgb"), "FFFF0000")
        self.assertEqual(font.find("m:name", NS).get("val"), "Times New Roman")
        fills = styles.findall("m:fills/m:fill", NS)
        self.assertEqual(fills[0].find("m:patternFill", NS).get("patternType"), "none")
        self.assertEqual(fills[1].find("m:patternFill", NS).get("patternType"), "gray125")
        self.assertEqual(fills[2].find(".//m:fgColor", NS).get("rgb"), "FFEFF9FF")
        border = styles.findall("m:borders/m:border", NS)[1]
        self.assertEqual(border.find("m:bottom", NS).get("style"), "double")
        self.assertEqual(border.find("m:top", NS).get("style"), "thin")
        xf = styles.findall("m:cellXfs/m:xf", NS)[1]
        alignment = xf.find("m:alignment", NS)
        self.assertEqual(alignment.get("horizontal"), "right")
        self.assertEqual(alignment.get("vertical"), "top")
        self.assertEqual(alignment.get("wrapText"), "1")
        self.assertEqual(alignment.get("indent"), "2")
        codes = [nf.get("formatCode") for nf in styles.findall(".//m:numFmt", NS)]
        self.assertEqual(codes, [NUM_MONEY])

    def test_builtin_number_formats_use_builtin_ids(self):
        book = Workbook()
        sheet = book.add_sheet("A")
        sheet.write(0, 0, 1, Style(num_format="0.00%"))
        sheet.write(0, 1, 1, Style(num_format="#,##0"))
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        self.assertIsNone(saved.xml("xl/styles.xml").find("m:numFmts", NS))

    def test_style_but_returns_a_modified_copy(self):
        base = Style(bold=True)
        changed = base.but(fill="FFFF00")
        self.assertTrue(changed.bold)
        self.assertEqual(changed.fill, "FFFF00")
        self.assertIsNone(base.fill)


class LayoutTests(unittest.TestCase):
    def build(self):
        book = Workbook()
        first = book.add_sheet("Summary", tab_color="1F3864")
        second = book.add_sheet("2B Detail")
        lists = book.add_sheet("Lists")
        first.write(0, 0, "Title", Style(bold=True))
        first.merge(0, 0, 0, 3)
        first.merge(2, 2, 2, 2)                      # a one-cell "merge" is ignored
        first.set_column_width(0, 1, 20)
        first.set_row_height(0, 28)
        first.hide_gridlines()
        first.set_zoom(85)
        second.write_row(0, 0, ["a", "b", "c"])
        second.write_row(1, 0, [1, 2, 3])
        second.freeze_panes(1, 2)
        second.autofilter(0, 0, 1, 2)
        second.print_titles(0, 0)
        second.landscape()
        second.validation_list(1, 2, 20, 2, ["CLAIM", "REVERSE_4B2"])
        second.validation_list(1, 1, 20, 1, "=Lists!$A$1:$A$3")
        green = Dxf(font_color="006100", fill="C6EFCE")
        second.conditional_cell(1, 0, 1, 0, "equal", '"OK"', green)
        second.conditional_expression(1, 1, 5, 1, "B2<0", Dxf(font_color="9C0006", bold=True))
        lists.write_row(0, 0, ["x", "y", "z"])
        first.write(4, 0, "go", Style(underline=True))
        first.link(4, 0, "2B Detail", "A1")
        first.link(5, 0, "O'Brien", "B2")
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        return saved

    def test_merges_widths_heights_and_view(self):
        sheet = self.build().sheet(1)
        merges = sheet.findall("m:mergeCells/m:mergeCell", NS)
        self.assertEqual([m.get("ref") for m in merges], ["A1:D1"])
        self.assertEqual(sheet.find("m:mergeCells", NS).get("count"), "1")
        cols = sheet.findall("m:cols/m:col", NS)
        self.assertEqual([(c.get("min"), c.get("max")) for c in cols], [("1", "1"), ("2", "2")])
        row = sheet.find(".//m:row[@r='1']", NS)
        self.assertEqual((row.get("ht"), row.get("customHeight")), ("28", "1"))
        view = sheet.find("m:sheetViews/m:sheetView", NS)
        self.assertEqual(view.get("showGridLines"), "0")
        self.assertEqual(view.get("zoomScale"), "85")
        self.assertEqual(view.get("tabSelected"), "1")
        self.assertEqual(sheet.find("m:sheetPr/m:tabColor", NS).get("rgb"), "FF1F3864")

    def test_only_the_first_sheet_is_selected(self):
        saved = self.build()
        self.assertIsNone(saved.sheet(2).find("m:sheetViews/m:sheetView", NS).get("tabSelected"))

    def test_freeze_filter_print_setup(self):
        saved = self.build()
        sheet = saved.sheet(2)
        pane = sheet.find("m:sheetViews/m:sheetView/m:pane", NS)
        self.assertEqual((pane.get("xSplit"), pane.get("ySplit")), ("2", "1"))
        self.assertEqual(pane.get("topLeftCell"), "C2")
        self.assertEqual(pane.get("state"), "frozen")
        self.assertEqual(sheet.find("m:autoFilter", NS).get("ref"), "A1:C2")
        setup = sheet.find("m:pageSetup", NS)
        self.assertEqual(setup.get("orientation"), "landscape")
        self.assertEqual((setup.get("fitToWidth"), setup.get("fitToHeight")), ("1", "0"))
        self.assertEqual(sheet.find("m:sheetPr/m:pageSetUpPr", NS).get("fitToPage"), "1")
        defined = saved.xml("xl/workbook.xml").findall(".//m:definedName", NS)
        names = {n.get("name"): n for n in defined}
        self.assertEqual(names["_xlnm._FilterDatabase"].text, "'2B Detail'!$A$1:$C$2")
        self.assertEqual(names["_xlnm._FilterDatabase"].get("localSheetId"), "1")
        self.assertEqual(names["_xlnm.Print_Titles"].text, "'2B Detail'!$1:$1")

    def test_freezing_only_rows_or_only_columns(self):
        book = Workbook()
        rows_only = book.add_sheet("R")
        rows_only.write(0, 0, 1)
        rows_only.freeze_panes(3)
        cols_only = book.add_sheet("C")
        cols_only.write(0, 0, 1)
        cols_only.freeze_panes(0, 2)
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        pane = saved.sheet(1).find(".//m:pane", NS)
        self.assertEqual((pane.get("ySplit"), pane.get("xSplit"), pane.get("activePane")),
                         ("3", None, "bottomLeft"))
        pane = saved.sheet(2).find(".//m:pane", NS)
        self.assertEqual((pane.get("xSplit"), pane.get("ySplit"), pane.get("activePane")),
                         ("2", None, "topRight"))

    def test_validation_lists(self):
        sheet = self.build().sheet(2)
        items = sheet.findall("m:dataValidations/m:dataValidation", NS)
        self.assertEqual(sheet.find("m:dataValidations", NS).get("count"), "2")
        by_range = {item.get("sqref"): item.find("m:formula1", NS).text for item in items}
        self.assertEqual(by_range["C2:C21"], '"CLAIM,REVERSE_4B2"')
        self.assertEqual(by_range["B2:B21"], "Lists!$A$1:$A$3")
        self.assertTrue(all(item.get("type") == "list" for item in items))

    def test_inline_list_limits(self):
        sheet = Workbook().add_sheet("A")
        with self.assertRaises(ValueError):
            sheet.validation_list(0, 0, 5, 0, ["a,b", "c"])
        with self.assertRaises(ValueError):
            sheet.validation_list(0, 0, 5, 0, ["x" * 100] * 4)

    def test_conditional_formats_and_dxfs(self):
        saved = self.build()
        sheet = saved.sheet(2)
        rules = sheet.findall("m:conditionalFormatting", NS)
        self.assertEqual([r.get("sqref") for r in rules], ["A2", "B2:B6"])
        first = rules[0].find("m:cfRule", NS)
        self.assertEqual((first.get("type"), first.get("operator"), first.get("priority")),
                         ("cellIs", "equal", "1"))
        self.assertEqual(first.find("m:formula", NS).text, '"OK"')
        second = rules[1].find("m:cfRule", NS)
        self.assertEqual((second.get("type"), second.get("priority")), ("expression", "2"))
        self.assertEqual(second.find("m:formula", NS).text, "B2<0")
        dxfs = saved.xml("xl/styles.xml").findall("m:dxfs/m:dxf", NS)
        self.assertEqual(len(dxfs), 2)
        self.assertEqual(int(first.get("dxfId")), 0)
        self.assertEqual(dxfs[0].find(".//m:bgColor", NS).get("rgb"), "FFC6EFCE")
        self.assertIsNotNone(dxfs[1].find("m:font/m:b", NS))

    def test_internal_links(self):
        sheet = self.build().sheet(1)
        links = sheet.findall("m:hyperlinks/m:hyperlink", NS)
        self.assertEqual([(link.get("ref"), link.get("location")) for link in links],
                         [("A5", "'2B Detail'!A1"), ("A6", "'O''Brien'!B2")])
        self.assertEqual(links[0].get("display"), "go")
        self.assertIsNone(links[1].get("display"))

    def test_sheet_xml_children_are_in_schema_order(self):
        order = [child.tag.split("}")[1] for child in self.build().sheet(2)]
        expected = ["sheetPr", "dimension", "sheetViews", "sheetFormatPr", "sheetData",
                    "autoFilter", "conditionalFormatting", "conditionalFormatting",
                    "dataValidations", "pageMargins", "pageSetup"]
        self.assertEqual(order, expected)
        first_order = [child.tag.split("}")[1] for child in self.build().sheet(1)]
        self.assertEqual(first_order, ["sheetPr", "dimension", "sheetViews", "sheetFormatPr",
                                       "cols", "sheetData", "mergeCells", "hyperlinks",
                                       "pageMargins"])

    def test_rows_and_cells_are_written_in_ascending_order(self):
        book = Workbook()
        sheet = book.add_sheet("A")
        for row, col in ((5, 3), (0, 9), (5, 0), (2, 2), (0, 1)):
            sheet.write(row, col, 1)
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        rows = saved.sheet(1).findall(".//m:row", NS)
        self.assertEqual([r.get("r") for r in rows], ["1", "3", "6"])
        refs = [c.get("r") for c in rows[0].findall("m:c", NS)]
        self.assertEqual(refs, ["B1", "J1"])
        self.assertEqual(saved.sheet(1).find("m:dimension", NS).get("ref"), "A1:J6")


class PackageTests(unittest.TestCase):
    def build_book(self):
        book = Workbook(title="Title & <co>", author="Author")
        sheet = book.add_sheet("S")
        sheet.write(0, 0, "x")
        sheet.formula(1, 0, "1+1", cached=2)
        return book

    def test_every_part_is_wellformed_xml_and_declared(self):
        saved = SavedWorkbook(self.build_book())
        self.addCleanup(saved.close)
        for name in saved.names:
            ET.fromstring(saved.raw[name])
        content = saved.raw["[Content_Types].xml"].decode()
        for name in saved.names:
            if name == "[Content_Types].xml" or name.endswith(".rels"):
                continue
            self.assertIn("/" + name, content, name)

    def test_package_relationships_resolve(self):
        saved = SavedWorkbook(self.build_book())
        self.addCleanup(saved.close)
        rels = ET.fromstring(saved.raw["xl/_rels/workbook.xml.rels"])
        targets = {r.get("Target") for r in rels}
        self.assertEqual(targets, {"worksheets/sheet1.xml", "styles.xml", "sharedStrings.xml"})
        for target in targets:
            self.assertIn("xl/" + target, saved.names)

    def test_document_properties_are_escaped(self):
        saved = SavedWorkbook(self.build_book())
        self.addCleanup(saved.close)
        core = saved.raw["docProps/core.xml"].decode()
        self.assertIn("Title &amp; &lt;co&gt;", core)
        self.assertIn("Author", core)

    def test_output_is_deterministic(self):
        first = SavedWorkbook(self.build_book())
        second = SavedWorkbook(self.build_book())
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        with open(first.path, "rb") as a, open(second.path, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_zip_entries_carry_a_fixed_timestamp(self):
        saved = SavedWorkbook(self.build_book())
        self.addCleanup(saved.close)
        self.assertEqual({info.date_time for info in saved.infos}, {(1980, 1, 1, 0, 0, 0)})

    def test_content_types_come_first(self):
        saved = SavedWorkbook(self.build_book())
        self.addCleanup(saved.close)
        self.assertEqual(saved.names[0], "[Content_Types].xml")

    def test_shared_string_counts(self):
        book = Workbook()
        sheet = book.add_sheet("S")
        for row in range(5):
            sheet.write(row, 0, "same")
        sheet.write(5, 0, "other")
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        root = saved.xml("xl/sharedStrings.xml")
        self.assertEqual((root.get("count"), root.get("uniqueCount")), ("6", "2"))

    def test_large_sheet_round_trip(self):
        book = Workbook()
        sheet = book.add_sheet("Big")
        for row in range(2000):
            sheet.write(row, 0, f"supplier {row % 50}")
            sheet.write(row, 1, row * 1.25)
        saved = SavedWorkbook(book)
        self.addCleanup(saved.close)
        rows = saved.sheet(1).findall(".//m:row", NS)
        self.assertEqual(len(rows), 2000)
        self.assertEqual(len(saved.strings()), 50)
        dimension = saved.sheet(1).find("m:dimension", NS).get("ref")
        self.assertTrue(re.fullmatch(r"A1:B2000", dimension))


if __name__ == "__main__":
    unittest.main()
