#!/usr/bin/env python3
"""
Finance Hack: the GSTR-2B vs Books ITC workpaper - an Excel file, built offline.

This is the command-line tool for the monthly ITC reconciliation. It reads the
GSTR-2B download and the purchase register (and last month's carried-forward
registers), runs the month through gstr2b_itc_workflow_hack, and writes:

    GSTR2B_vs_Books_<Mon-YY>.xlsx     the workpaper
    carry_forward_books_deferred.csv  next month's opening: booked, not in 2B
    carry_forward_2b_pending.csv      next month's opening: in 2B, not booked
    summary.txt                       the headline figures as text

The workbook follows the layout accountants already work in:

    Summary          checks, GSTR-3B Table 4, the books-to-return bridge, the
                     credit-reversal statement, tie-outs, action items
    2B Detail        every GSTR-2B document with its books match and a TREATMENT
                     you can change - the Summary recalculates from it
    Books Only       books documents GSTR-2B does not carry
    Differences      matched documents that disagree, with a suggested action
    Reclaimed 4(D)(1) pending items booked this month
    Carry-forward    the two closing registers, with ageing and the s.16(4) limit
    Party-wise       the exposure by supplier
    Notes            the method, the legend, the data-quality notes

Colour convention: blue on pale blue = an input or a tag you may change, black =
a formula, yellow = needs a look. Nothing leaves the machine: standard library
only, no network.

    python3 gstr2b_workpaper_hack.py --gstr2b GSTR2B_192026_092026.xlsx \\
        --books purchase_register.xlsx --period 2026-09 --outdir sep26
"""

import argparse
import os
import re
import sys
import tempfile
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from gstr2b_itc_workflow_hack import (
    CARRY_2B_FILE,
    CARRY_BOOKS_FILE,
    CLAIM,
    CLAIM_PM,
    CLASS_CM,
    CLASS_FUTURE,
    CLASS_NONE,
    CLASS_OPENING,
    CLASS_PM,
    CN_ALREADY,
    CN_HOLD,
    CN_NOT_OURS,
    CN_POLICIES,
    CN_REVERSE,
    CN_TAKEN,
    DEFER_BOOKS,
    INELIGIBLE,
    NO_ITC,
    NOT_THIS_PERIOD,
    RECLAIM_4D1,
    REVERSE_4B1,
    REVERSE_4B2,
    TREATMENT_HELP,
    ZERO,
    Period,
    Row2B,
    Tax,
    Workpaper,
    build_workpaper,
    itc_time_limit,
    load_opening_items,
    origin_of,
    parse_period,
    require_period,
    sum_tax,
    write_carry_forward,
)
from gstr2b_reconciliation_hack import (
    DEFAULT_DATE_TOLERANCE_DAYS,
    DEFAULT_TAX_TOLERANCE,
    DEFAULT_TAXABLE_TOLERANCE,
    SOURCE_BOOKS,
    SOURCE_GSTR2B,
    DocumentType,
    GSTR2BReconciler,
    InvoiceRecord,
    MatchBasis,
    ReconStatus,
    load_records,
    split_file_spec,
)
from xlsx_writer_hack import (
    NUM_DATE,
    NUM_INTEGER,
    NUM_MONEY,
    Dxf,
    Sheet,
    Style,
    Workbook,
    col_letter,
    quote_sheet,
)

# ------------------------------------------------------------------- house style
NAVY, ORANGE, PEACH = "1F3864", "C55A11", "F4B083"
PALE_PEACH, INPUT_FILL, REVIEW_FILL, GREY = "FBE5D6", "EFF9FF", "FFF2CC", "595959"
BLUE = "0000FF"

BASE = Style(font_size=9)
TITLE = Style(font_size=12, bold=True, font_color="FFFFFF", fill=NAVY, halign="center",
              valign="center")
SUBTITLE = Style(font_size=11, bold=True, font_color="FFFFFF", fill=ORANGE, halign="center",
                 valign="center")
NOTE = Style(font_size=9, italic=True, font_color=GREY, wrap=True, valign="top")
SECTION = Style(font_size=10, bold=True, fill=PALE_PEACH, border="thin")
HEAD = Style(font_size=9, bold=True, fill=PEACH, halign="center", valign="center", wrap=True,
             border="thin")
TEXT = Style(font_size=9, border="thin", valign="top")
WRAP = TEXT.but(wrap=True)
BOLD = TEXT.but(bold=True)
MONEY = Style(font_size=9, border="thin", num_format=NUM_MONEY)
MONEY_BOLD = MONEY.but(bold=True)
MONEY_TOTAL = MONEY.but(bold=True, border="total")
INTEGER = Style(font_size=9, border="thin", num_format=NUM_INTEGER, halign="center")
DATE = Style(font_size=9, border="thin", num_format=NUM_DATE, halign="center")
INPUT_MONEY = MONEY.but(font_color=BLUE, fill=INPUT_FILL)
INPUT_TEXT = TEXT.but(font_color=BLUE, fill=INPUT_FILL)
TAG = TEXT.but(font_color=BLUE, fill=INPUT_FILL, bold=True)
REVIEW = WRAP.but(fill=REVIEW_FILL)
CHECK = Style(font_size=10, bold=True, border="thin", halign="center")
LINK = Style(font_size=9, font_color="0563C1", underline=True)

OK_DXF = Dxf(font_color="006100", fill="C6EFCE", bold=True)
BAD_DXF = Dxf(font_color="9C0006", fill="FFC7CE", bold=True)
ENTER_DXF = Dxf(font_color="7F6000", fill="FFEB9C", bold=True)
EXPIRED_DXF = Dxf(font_color="9C0006", fill="FFC7CE")
EXPIRING_DXF = Dxf(font_color="7F6000", fill="FFEB9C")

HEADS = ("IGST", "CGST", "SGST", "Cess")
SHEET_SUMMARY = "Summary"
SHEET_2B = "2B Detail"
SHEET_BOOKS = "Books Only"
SHEET_DIFF = "Differences"
SHEET_RECLAIM = "Reclaimed 4(D)(1)"
SHEET_CARRY = "Carry-forward"
SHEET_PARTY = "Party-wise"
SHEET_NOTES = "Notes"

TAGS_2B_INVOICE = (CLAIM, CLAIM_PM, REVERSE_4B2, REVERSE_4B1, INELIGIBLE)
TAGS_2B_CREDIT = (CN_REVERSE, CN_ALREADY, CN_NOT_OURS)
TAGS_BOOKS_INVOICE = (DEFER_BOOKS, RECLAIM_4D1, NO_ITC, NOT_THIS_PERIOD)
TAGS_BOOKS_CREDIT = (CN_TAKEN, CN_HOLD, NO_ITC)
ALL_TAGS = (CLAIM, CLAIM_PM, REVERSE_4B2, REVERSE_4B1, INELIGIBLE, CN_REVERSE, CN_ALREADY,
            CN_NOT_OURS, DEFER_BOOKS, RECLAIM_4D1, CN_TAKEN, CN_HOLD, NO_ITC, NOT_THIS_PERIOD)

# ------------------------------------------------------------------ column specs
# (key, heading, width). The order here is the order on the sheet; the formulas on the
# Summary find their columns by key, so a column can be moved without breaking them.
SPEC_2B: Tuple[Tuple[str, str, float], ...] = (
    ("ref", "Unique GSTR-2B no.", 13), ("sheet", "2B sheet", 10), ("row", "Row", 6),
    ("gstin", "Supplier GSTIN", 17), ("name", "Supplier", 26), ("type", "Type", 10),
    ("no", "Document no.", 17), ("date", "Document date", 12),
    ("taxable", "Taxable value", 14), ("igst", "IGST", 12), ("cgst", "CGST", 12),
    ("sgst", "SGST", 12), ("cess", "Cess", 9), ("tax", "Total tax", 13),
    ("avail", "ITC available", 9), ("reason", "Reason (GSTR-2B)", 20),
    ("status", "Match status", 16), ("basis", "Matched on", 17), ("class", "Books entry", 9),
    ("bref", "Books voucher / doc no.", 17), ("bdate", "Booked on", 12),
    ("btaxable", "Books taxable", 14), ("bigst", "Books IGST", 12), ("bcgst", "Books CGST", 12),
    ("bsgst", "Books SGST", 12), ("bcess", "Books Cess", 9),
    ("dtax", "Tax difference (books - 2B)", 14), ("flags", "Match flags", 24),
    ("treat", "TREATMENT", 18), ("flag", "Claimed?", 8), ("cigst", "Claim IGST", 12),
    ("ccgst", "Claim CGST", 12), ("csgst", "Claim SGST", 12), ("ccess", "Claim Cess", 9),
    ("ctax", "Total claim", 13), ("notes", "Review notes", 46),
    ("limit", "Time limit s.16(4)", 12), ("remarks", "Your remarks", 24),
)
SPEC_BOOKS: Tuple[Tuple[str, str, float], ...] = (
    ("ref", "Voucher / doc no.", 17), ("row", "Row", 6), ("gstin", "Supplier GSTIN", 17),
    ("name", "Supplier", 26), ("type", "Type", 10), ("no", "Supplier doc no.", 17),
    ("vch", "Voucher no.", 16), ("date", "Document date", 12), ("bdate", "Booked on", 12),
    ("taxable", "Taxable value", 14), ("igst", "IGST", 12), ("cgst", "CGST", 12),
    ("sgst", "SGST", 12), ("cess", "Cess", 9), ("tax", "Total tax", 13),
    ("class", "Books entry", 9), ("pref", "Reclaims 2B line", 14),
    ("pigst", "ITC if claimed: IGST", 12), ("pcgst", "CGST", 12), ("psgst", "SGST", 12),
    ("pcess", "Cess", 9), ("treat", "TREATMENT", 18), ("booked", "In this month's books?", 9),
    ("flag", "Claimed?", 8), ("cigst", "Claim IGST", 12), ("ccgst", "Claim CGST", 12),
    ("csgst", "Claim SGST", 12), ("ccess", "Claim Cess", 9), ("ctax", "Total claim", 13),
    ("notes", "Review notes", 46), ("remarks", "Your remarks", 24),
)
HEAD_ROW = 3                 # 0-based row of the column headings on the detail sheets
TOTALS_ROW = 2               # filter-aware totals sit just above the headings
FIRST_DATA_ROW = 4           # 0-based

TYPE_LABEL = {DocumentType.INVOICE: "Invoice", DocumentType.CREDIT_NOTE: "Credit note",
              DocumentType.DEBIT_NOTE: "Debit note"}
BASIS_LABEL = {
    MatchBasis.EXACT_INVOICE_NO: "Invoice no. (exact)",
    MatchBasis.NORMALISED_INVOICE_NO: "Invoice no. (format differs)",
    MatchBasis.INVOICE_TRAILING_NUMBER: "Trailing number",
    MatchBasis.TAXABLE_VALUE_AND_DATE: "Value and date",
    MatchBasis.SUPPLIER_VALUE_ONLY: "Supplier and value only",
}
STATUS_LABEL = {ReconStatus.MATCHED: "Matched", ReconStatus.MATCHED_WITH_DIFFERENCE:
                "Matched - differs"}
FLAG_ACTION = (
    ("NO_DOC_REFERENCE", "Matched on supplier and value only - confirm it is the same document."),
    ("RATE_DIFFERENCE", "Same taxable value, different tax: confirm the rate on the supplier's "
                        "invoice, then correct the books or ask the supplier to amend."),
    ("TAXABLE_VALUE_DIFF", "Compare the invoice value - discount, freight or a part booking?"),
    ("TAX_HEAD_DIFF", "Total tax agrees but IGST/CGST/SGST differ - check the place of supply."),
    ("TAX_AMOUNT_DIFF", "Tax differs - check the invoice and the books entry."),
    ("INVOICE_DATE_DIFF", "Dates are far apart - confirm the period the credit belongs to."),
    ("INVOICE_NO_MISMATCH", "Invoice numbers differ; matched on value and date - confirm."),
    ("DOCUMENT_TYPE_DIFF", "Invoice on one side, credit or debit note on the other."),
    ("ITC_NOT_AVAILABLE", "GSTR-2B marks the ITC not available - do not claim it."),
)


class Layout:
    """Where a detail sheet's columns and rows are, for building formulas."""

    def __init__(self, sheet: str, spec: Sequence[Tuple[str, str, float]], count: int) -> None:
        self.sheet = sheet
        self.index = {key: number for number, (key, _h, _w) in enumerate(spec)}
        self.first = FIRST_DATA_ROW + 1                      # 1-based Excel rows
        self.last = max(self.first, FIRST_DATA_ROW + count)
        self.count = count

    def col(self, key: str) -> int:
        return self.index[key]

    def letter(self, key: str) -> str:
        return col_letter(self.index[key])

    def rng(self, key: str) -> str:
        letter = self.letter(key)
        return f"{quote_sheet(self.sheet)}!${letter}${self.first}:${letter}${self.last}"


def _needs_review_2b(row: Row2B) -> bool:
    return bool(
        (row.pair is not None and row.pair.status is ReconStatus.MATCHED_WITH_DIFFERENCE)
        or row.books_class in (CLASS_PM, CLASS_FUTURE)
        or (row.credit_like and row.book is None)
        or row.treatment == INELIGIBLE
    )


def _time_limit(doc: InvoiceRecord) -> Optional[date]:
    stamp = doc.invoice_date or doc.booking_date
    return itc_time_limit(stamp) if stamp else None


# ------------------------------------------------------------------- sheet header


def _banner(sheet: Sheet, work: Workpaper, title: str, width: int) -> None:
    name = work.client or "GSTR-2B vs BOOKS"
    gstin = f" | GSTIN: {work.gstin}" if work.gstin else ""
    sheet.write(0, 0, f"{name}{gstin}", TITLE)
    sheet.merge(0, 0, 0, width - 1)
    sheet.write(1, 0, f"{title} | {work.period.long_label} | F.Y. {work.period.fy_label}",
                SUBTITLE)
    sheet.merge(1, 0, 1, width - 1)
    for col in range(1, width):
        sheet.write(0, col, None, TITLE)
        sheet.write(1, col, None, SUBTITLE)
    sheet.set_row_height(0, 22)
    sheet.set_row_height(1, 18)


def _header(sheet: Sheet, spec: Sequence[Tuple[str, str, float]], row: int = HEAD_ROW) -> None:
    for number, (key, heading, width) in enumerate(spec):
        sheet.write(row, number, heading, HEAD)
        sheet.set_column_width(number, number, width)
    sheet.set_row_height(row, 36)


def _treatment_validation(sheet: Sheet, layout: Layout, rows: Sequence, invoice_tags: Sequence[str],
                          credit_tags: Sequence[str]) -> None:
    """A drop-down on every treatment cell, with a list that suits the kind of document."""
    column = layout.col("treat")
    start = 0
    for position in range(1, len(rows) + 1):
        if position == len(rows) or rows[position].credit_like != rows[start].credit_like:
            tags = credit_tags if rows[start].credit_like else invoice_tags
            sheet.validation_list(FIRST_DATA_ROW + start, column,
                                  FIRST_DATA_ROW + position - 1, column, tags)
            start = position


# ------------------------------------------------------------------ detail sheets


def _money_cells(sheet: Sheet, row: int, layout: Layout, keys: Sequence[str],
                 values: Sequence[float], style: Style = MONEY) -> None:
    for key, value in zip(keys, values):
        sheet.write(row, layout.col(key), value, style)


def _totals_row(sheet: Sheet, layout: Layout, keys: Sequence[str],
                totals: Dict[str, float]) -> None:
    """Filter-aware totals above the headings: SUBTOTAL(109) ignores hidden rows."""
    sheet.write(TOTALS_ROW, 0, "Total of the rows shown", BOLD)
    for key in keys:
        letter = layout.letter(key)
        sheet.formula(TOTALS_ROW, layout.col(key),
                      f"SUBTOTAL(109,{letter}{layout.first}:{letter}{layout.last})",
                      cached=round(totals[key], 2), style=MONEY_BOLD)


def write_2b_detail(book: Workbook, work: Workpaper) -> Layout:
    """One row per GSTR-2B document: the portal's figures, its books match, its treatment."""
    sheet = book.add_sheet(SHEET_2B, tab_color=ORANGE)
    layout = Layout(SHEET_2B, SPEC_2B, len(work.rows_2b))
    _banner(sheet, work, "GSTR-2B DETAIL - TAG EVERY LINE", len(SPEC_2B))
    _header(sheet, SPEC_2B)
    ix = layout.col
    heads2b = ("igst", "cgst", "sgst", "cess")
    headsbk = ("bigst", "bcgst", "bsgst", "bcess")
    headscl = ("cigst", "ccgst", "csgst", "ccess")
    totals: Dict[str, float] = {key: 0.0 for key in
                                ("taxable", *heads2b, "tax", "btaxable", *headsbk, "dtax",
                                 *headscl, "ctax")}

    for number, row in enumerate(work.rows_2b):
        r = FIRST_DATA_ROW + number
        e = r + 1
        doc, book_doc, pair = row.doc, row.book, row.pair
        review = _needs_review_2b(row)
        sheet.write(r, ix("ref"), row.ref, BOLD)
        sheet.write(r, ix("sheet"), doc.sheet, TEXT)
        sheet.write(r, ix("row"), doc.source_rows[0], INTEGER)
        sheet.write(r, ix("gstin"), doc.gstin_key, TEXT)
        sheet.write(r, ix("name"), doc.supplier_name, TEXT)
        sheet.write(r, ix("type"), TYPE_LABEL[doc.document_type], TEXT)
        sheet.write(r, ix("no"), doc.invoice_no, TEXT)
        sheet.write(r, ix("date"), doc.invoice_date, DATE)
        sheet.write(r, ix("taxable"), doc.taxable_value, MONEY)
        _money_cells(sheet, r, layout, heads2b, row.tax.as_list())
        tax_total = row.tax.total
        sheet.formula(r, ix("tax"), f"SUM({layout.letter('igst')}{e}:{layout.letter('cess')}{e})",
                      cached=tax_total, style=MONEY_BOLD)
        sheet.write(r, ix("avail"), "Yes" if doc.itc_available else "No", TEXT.but(halign="center"))
        sheet.write(r, ix("reason"), doc.reason, TEXT)
        sheet.write(r, ix("status"),
                    STATUS_LABEL[pair.status] if pair else "Not in books", TEXT)
        sheet.write(r, ix("basis"), BASIS_LABEL[pair.basis] if pair else "", TEXT)
        sheet.write(r, ix("class"), row.books_class, TEXT.but(halign="center"))
        sheet.write(r, ix("bref"),
                    (book_doc.invoice_no or book_doc.voucher_no) if book_doc else "", TEXT)
        sheet.write(r, ix("bdate"), book_doc.booking_date if book_doc else None, DATE)
        sheet.write(r, ix("btaxable"), book_doc.taxable_value if book_doc else None, MONEY)
        for key, value in zip(headsbk, row.books_tax.as_list()):
            sheet.write(r, ix(key), value if book_doc else None, MONEY)
        diff = round(row.books_tax.total - tax_total, 2) if book_doc else 0.0
        sheet.formula(
            r, ix("dtax"),
            f'IF({layout.letter("class")}{e}="{CLASS_NONE}",0,'
            f'SUM({layout.letter("bigst")}{e}:{layout.letter("bcess")}{e})-'
            f'{layout.letter("tax")}{e})', cached=diff, style=MONEY)
        sheet.write(r, ix("flags"), pair.flag_text if pair and pair.flags else "",
                    REVIEW if review else WRAP)
        sheet.write(r, ix("treat"), row.treatment, TAG)
        t = f'{layout.letter("treat")}{e}'
        claimed = 1 if row.treatment in (CLAIM, CLAIM_PM, CN_REVERSE) else 0
        sheet.formula(r, ix("flag"),
                      f'IF(OR({t}="{CLAIM}",{t}="{CLAIM_PM}",{t}="{CN_REVERSE}"),1,0)',
                      cached=claimed, style=INTEGER)
        for key, source, value in zip(headscl, heads2b, row.claim.as_list()):
            flag, amount = layout.letter("flag"), layout.letter(source)
            sheet.formula(r, ix(key), f"IF({flag}{e}=1,{amount}{e},0)", cached=value,
                          style=MONEY)
        sheet.formula(r, ix("ctax"),
                      f"SUM({layout.letter('cigst')}{e}:{layout.letter('ccess')}{e})",
                      cached=row.claim.total, style=MONEY_BOLD)
        sheet.write(r, ix("notes"), "; ".join(row.notes), REVIEW if review else WRAP)
        sheet.write(r, ix("limit"), _time_limit(doc), DATE)
        sheet.write(r, ix("remarks"), "", INPUT_TEXT)
        totals["taxable"] += doc.taxable_value
        for key, value in zip(heads2b, row.tax.as_list()):
            totals[key] += value
        totals["tax"] += tax_total
        if book_doc:
            totals["btaxable"] += book_doc.taxable_value
            for key, value in zip(headsbk, row.books_tax.as_list()):
                totals[key] += value
        totals["dtax"] += diff
        for key, value in zip(headscl, row.claim.as_list()):
            totals[key] += value
        totals["ctax"] += row.claim.total

    _totals_row(sheet, layout, ["taxable", *heads2b, "tax", "btaxable", *headsbk, "dtax",
                                *headscl, "ctax"], totals)
    sheet.freeze_panes(FIRST_DATA_ROW, 5)
    sheet.autofilter(HEAD_ROW, 0, max(HEAD_ROW + 1, FIRST_DATA_ROW + len(work.rows_2b) - 1),
                     len(SPEC_2B) - 1)
    sheet.print_titles(0, HEAD_ROW)
    sheet.landscape()
    _treatment_validation(sheet, layout, work.rows_2b, TAGS_2B_INVOICE, TAGS_2B_CREDIT)
    if work.rows_2b:
        first, last = FIRST_DATA_ROW, FIRST_DATA_ROW + len(work.rows_2b) - 1
        col = ix("avail")
        sheet.conditional_cell(first, col, last, col, "equal", '"No"', BAD_DXF)
    return layout


def write_books_only(book: Workbook, work: Workpaper) -> Layout:
    """One row per books document that GSTR-2B does not carry."""
    sheet = book.add_sheet(SHEET_BOOKS, tab_color=ORANGE)
    layout = Layout(SHEET_BOOKS, SPEC_BOOKS, len(work.rows_books))
    _banner(sheet, work, "BOOKS ONLY - NOT IN GSTR-2B", len(SPEC_BOOKS))
    _header(sheet, SPEC_BOOKS)
    ix = layout.col
    keys_tax = ("igst", "cgst", "sgst", "cess")
    keys_basis = ("pigst", "pcgst", "psgst", "pcess")
    keys_claim = ("cigst", "ccgst", "csgst", "ccess")
    totals: Dict[str, float] = {key: 0.0 for key in
                                ("taxable", *keys_tax, "tax", *keys_basis, *keys_claim, "ctax")}
    for number, row in enumerate(work.rows_books):
        r = FIRST_DATA_ROW + number
        e = r + 1
        doc = row.doc
        review = row.books_class == CLASS_PM or (row.pending is not None and row.credit_like)
        sheet.write(r, ix("ref"), row.ref, BOLD)
        sheet.write(r, ix("row"), doc.source_rows[0], INTEGER)
        sheet.write(r, ix("gstin"), doc.gstin_key, TEXT)
        sheet.write(r, ix("name"), doc.supplier_name, TEXT)
        sheet.write(r, ix("type"), TYPE_LABEL[doc.document_type], TEXT)
        sheet.write(r, ix("no"), doc.invoice_no, TEXT)
        sheet.write(r, ix("vch"), doc.voucher_no, TEXT)
        sheet.write(r, ix("date"), doc.invoice_date, DATE)
        sheet.write(r, ix("bdate"), doc.booking_date, DATE)
        sheet.write(r, ix("taxable"), doc.taxable_value, MONEY)
        _money_cells(sheet, r, layout, keys_tax, row.tax.as_list())
        sheet.formula(r, ix("tax"), f"SUM({layout.letter('igst')}{e}:{layout.letter('cess')}{e})",
                      cached=row.tax.total, style=MONEY_BOLD)
        sheet.write(r, ix("class"), row.books_class, TEXT.but(halign="center"))
        sheet.write(r, ix("pref"), row.pending.ref if row.pending and row.pending.ref else
                    (row.pending.invoice_no if row.pending else ""), TEXT)
        _money_cells(sheet, r, layout, keys_basis, row.basis.as_list())
        sheet.write(r, ix("treat"), row.treatment, TAG)
        t, c = f'{layout.letter("treat")}{e}', f'{layout.letter("class")}{e}'
        sheet.formula(r, ix("booked"), f'IF(AND({c}="{CLASS_CM}",{t}<>"{NO_ITC}"),1,0)',
                      cached=1 if row.counts_as_booked else 0, style=INTEGER)
        claimed = 1 if row.treatment in (RECLAIM_4D1, CN_TAKEN) else 0
        sheet.formula(r, ix("flag"), f'IF(OR({t}="{RECLAIM_4D1}",{t}="{CN_TAKEN}"),1,0)',
                      cached=claimed, style=INTEGER)
        for key, source, value in zip(keys_claim, keys_basis, row.claim.as_list()):
            flag, amount = layout.letter("flag"), layout.letter(source)
            sheet.formula(r, ix(key), f"IF({flag}{e}=1,{amount}{e},0)", cached=value,
                          style=MONEY)
        sheet.formula(r, ix("ctax"),
                      f"SUM({layout.letter('cigst')}{e}:{layout.letter('ccess')}{e})",
                      cached=row.claim.total, style=MONEY_BOLD)
        sheet.write(r, ix("notes"), "; ".join(row.notes), REVIEW if review else WRAP)
        sheet.write(r, ix("remarks"), "", INPUT_TEXT)
        totals["taxable"] += doc.taxable_value
        for key, value in zip(keys_tax, row.tax.as_list()):
            totals[key] += value
        totals["tax"] += row.tax.total
        for key, value in zip(keys_basis, row.basis.as_list()):
            totals[key] += value
        for key, value in zip(keys_claim, row.claim.as_list()):
            totals[key] += value
        totals["ctax"] += row.claim.total
    _totals_row(sheet, layout, ["taxable", *keys_tax, "tax", *keys_basis, *keys_claim, "ctax"],
                totals)
    sheet.freeze_panes(FIRST_DATA_ROW, 4)
    sheet.autofilter(HEAD_ROW, 0, max(HEAD_ROW + 1, FIRST_DATA_ROW + len(work.rows_books) - 1),
                     len(SPEC_BOOKS) - 1)
    sheet.print_titles(0, HEAD_ROW)
    sheet.landscape()
    _treatment_validation(sheet, layout, work.rows_books, TAGS_BOOKS_INVOICE, TAGS_BOOKS_CREDIT)
    return layout


# ------------------------------------------------------------------------ summary


KEYS_TAX = ("igst", "cgst", "sgst", "cess")
KEYS_IN_2B = ("bigst", "bcgst", "bsgst", "bcess")        # books amounts on the 2B Detail sheet
KEYS_CLAIM = ("cigst", "ccgst", "csgst", "ccess")
KEYS_BASIS = ("pigst", "pcgst", "psgst", "pcess")


def _sumifs(total: str, *pairs: Tuple[str, str]) -> str:
    """SUMIFS over `total`, each pair being (criteria range, criteria)."""
    return f"SUMIFS({total}," + ",".join(f"{rng},{crit}" for rng, crit in pairs) + ")"


def _quoted(tag: str) -> str:
    return f'"{tag}"'


class SummarySheet:
    """Writes the Summary and remembers where each figure landed, for the formulas."""

    HEAD_COLS = (1, 2, 3, 4)          # B..E
    TOTAL_COL = 5                     # F
    NOTE_COL = 6                      # G

    def __init__(self, sheet: Sheet, work: Workpaper, l2b: Layout, lbk: Layout) -> None:
        self.sheet, self.work, self.l2b, self.lbk = sheet, work, l2b, lbk
        self.row = 0
        self.pos: Dict[str, int] = {}

    # ------------------------------------------------------------- primitives
    def at(self, key: str) -> int:
        """The 1-based Excel row of a named line."""
        return self.pos[key] + 1

    def ref(self, key: str, head: int) -> str:
        return f"{col_letter(self.HEAD_COLS[head])}{self.at(key)}"

    def total_ref(self, key: str) -> str:
        return f"{col_letter(self.TOTAL_COL)}{self.at(key)}"

    def span(self, key: str) -> str:
        """B..E of a line, as a range."""
        return f"B{self.at(key)}:E{self.at(key)}"

    def section(self, title: str) -> None:
        for col in range(0, 7):
            self.sheet.write(self.row, col, title if col == 0 else None, SECTION)
        self.sheet.merge(self.row, 0, self.row, 6)
        self.row += 1

    def columns(self, first: str = "Particulars", last: str = "Source / how computed") -> None:
        for col, text in enumerate([first, *HEADS, "Total", last]):
            self.sheet.write(self.row, col, text, HEAD)
        self.row += 1

    def tax_line(self, key: str, label: str, formula, value: Tax, style: Style = MONEY,
                 label_style: Style = TEXT, note: str = "", inputs: bool = False,
                 blank: bool = False) -> None:
        """
        One line of four heads and a total. `formula` is a function of the head (0-3) giving
        that cell's formula, or None for stored figures; `inputs` makes the cells blue input
        cells; `blank` marks a line whose formulas give "" until something is entered.
        """
        r = self.row
        self.pos[key] = r
        self.sheet.write(r, 0, label, label_style)
        for head, col in enumerate(self.HEAD_COLS):
            figure = value.as_list()[head]
            if inputs:
                self.sheet.write(r, col, figure, INPUT_MONEY)
            elif formula is not None:
                self.sheet.formula(r, col, formula(head), cached="" if blank else figure,
                                   style=style)
            else:
                self.sheet.write(r, col, figure, style)
        self.sheet.formula(r, self.TOTAL_COL, f"SUM(B{r + 1}:E{r + 1})", cached=value.total,
                           style=style)
        self.sheet.write(r, self.NOTE_COL, note, NOTE)
        self.row += 1

    # --------------------------------------------------- formula building blocks
    def two_b(self, head: int, *tags: str) -> str:
        """GSTR-2B Detail tax for the head, over the lines carrying any of the tags."""
        column, treat = self.l2b.rng(KEYS_TAX[head]), self.l2b.rng("treat")
        return "+".join(_sumifs(column, (treat, _quoted(tag))) for tag in tags)

    def books_only(self, keys: Sequence[str], head: int, *conditions: Tuple[str, str]) -> str:
        """Books Only column for the head, over this month's lines meeting the conditions."""
        pairs = [(self.lbk.rng("booked"), "1")]
        pairs += [(self.lbk.rng("treat"), crit) for _name, crit in conditions]
        return _sumifs(self.lbk.rng(keys[head]), *pairs)

    def by_tag(self, keys: Sequence[str], head: int, tag: str) -> str:
        return self.books_only(keys, head, ("treat", _quoted(tag)))

    def cells(self, head: int, first: str, last: str) -> str:
        """SUM of the same head column between two named lines."""
        letter = col_letter(self.HEAD_COLS[head])
        return f"SUM({letter}{self.at(first)}:{letter}{self.at(last)})"

    # ----------------------------------------------------------- the sections
    def table4(self) -> None:
        t4 = self.work.table4()
        basis_sum = lambda h, tag: _sumifs(  # noqa: E731
            self.lbk.rng(KEYS_BASIS[h]), (self.lbk.rng("treat"), _quoted(tag)))
        self.section("A. GSTR-3B TABLE 4 - ITC AS PER THIS WORKPAPER")
        self.columns()
        self.tax_line(
            "t_inv", "B2B / B2BA invoices and debit notes in GSTR-2B",
            lambda h: self.two_b(h, CLAIM, CLAIM_PM, REVERSE_4B2, REVERSE_4B1), t4["invoices"],
            note="Every invoice line with ITC available (CLAIM, CLAIM_PM_BOOKED, REVERSE_4B2, "
                 "REVERSE_4B1).")
        self.tax_line("t_cn2b", "Less: credit notes in GSTR-2B reversed in this return",
                      lambda h: self.two_b(h, CN_REVERSE), t4["credit_2b"],
                      note="Tag CN_REVERSE. Negative figures.")
        self.tax_line("t_cnbk", "Less: purchase returns reversed from the books (no 2B CN)",
                      lambda h: basis_sum(h, CN_TAKEN), t4["credit_books"],
                      note="Books-only lines tagged CN_TAKEN_BOOKS. Negative figures.")
        self.tax_line("t_d1", "Add: ITC reclaimed under 4(D)(1) - pending GSTR-2B lines now booked",
                      lambda h: basis_sum(h, RECLAIM_4D1), t4["reclaim"],
                      note="Books-only lines tagged RECLAIM_4D1, at the GSTR-2B amount. Reported "
                           "in 4(A)(5) as well as 4(D)(1).")
        self.tax_line("t_a5", "4(A)(5) All other ITC", lambda h: self.cells(h, "t_inv", "t_d1"),
                      t4["a5"], MONEY_BOLD, BOLD,
                      "Kept tied to GSTR-2B: what is not booked is reversed below.")
        self.tax_line("t_b1", "4(B)(1) Reversed under rules 38, 42, 43 and section 17(5)",
                      lambda h: self.two_b(h, REVERSE_4B1), t4["b1"],
                      note="Tag REVERSE_4B1 - permanent reversals.")
        self.tax_line("t_man", "Rule 37A / other manual 4(B)(2) reversal  (enter)", None, ZERO,
                      label_style=INPUT_TEXT, inputs=True,
                      note="Reversals kept outside the bill-wise register, e.g. the supplier has "
                           "not filed GSTR-3B.")
        self.tax_line("t_b2", "4(B)(2) Others - reclaimable (reversed now, reclaimed when booked)",
                      lambda h: f"{self.two_b(h, REVERSE_4B2)}+{self.ref('t_man', h)}", t4["b2"],
                      note="Tag REVERSE_4B2 - in GSTR-2B but not booked - plus the manual line.")
        self.tax_line("t_net", "4(C) Net ITC available = 4(A)(5) - 4(B)",
                      lambda h: (f"{self.ref('t_a5', h)}-{self.ref('t_b1', h)}-"
                                 f"{self.ref('t_b2', h)}"), t4["net"], MONEY_TOTAL, BOLD)
        self.tax_line("t_d1i", "4(D)(1) ITC reclaimed which was reversed under 4(B)(2) earlier",
                      lambda h: self.ref("t_d1", h), t4["d1"],
                      note="Information line; already inside 4(A)(5).")
        self.tax_line("t_d2", "4(D)(2) Ineligible ITC (section 16(4), place of supply)",
                      lambda h: self.two_b(h, INELIGIBLE), t4["d2"],
                      note="GSTR-2B lines marked ITC not available; kept out of 4(A)(5).")
        self.row += 1

    def bridge(self) -> None:
        lines = dict(self.work.bridge())
        cls, flag = self.l2b.rng("class"), self.l2b.rng("flag")
        cm = _quoted(CLASS_CM)
        in_2b = lambda keys, h, *pairs: _sumifs(  # noqa: E731
            self.l2b.rng(keys[h]), *pairs)
        both = lambda tag: (self.lbk.rng("treat"), _quoted(tag))  # noqa: E731
        others = [(self.lbk.rng("treat"), f'"<>{tag}"')
                  for tag in (DEFER_BOOKS, RECLAIM_4D1, CN_HOLD, CN_TAKEN)]

        def claim_vs_books(h, pairs):
            """Claim less books amount over Books Only lines meeting `pairs`."""
            claim = _sumifs(self.lbk.rng(KEYS_CLAIM[h]), (self.lbk.rng("booked"), "1"), *pairs)
            books = _sumifs(self.lbk.rng(KEYS_TAX[h]), (self.lbk.rng("booked"), "1"), *pairs)
            return f"{claim}-{books}"

        self.section("B. BRIDGE - FROM THE ITC IN THE BOOKS TO THE ITC CLAIMED IN GSTR-3B")
        self.columns("Particulars", "What it is")
        self.tax_line(
            "b_books", "Net ITC as per the books - this month's purchases less returns",
            lambda h: (in_2b(KEYS_IN_2B, h, (cls, cm)) + "+"
                       + _sumifs(self.lbk.rng(KEYS_TAX[h]), (self.lbk.rng("booked"), "1"))),
            lines["books"], MONEY_BOLD, BOLD,
            "This month's books documents, matched or not (NO_ITC lines excluded).")
        self.tax_line(
            "b_diff", "Add / (less): GSTR-2B vs books difference on matched documents",
            lambda h: (in_2b(KEYS_CLAIM, h, (cls, cm)) + "-"
                       + in_2b(KEYS_IN_2B, h, (cls, cm), (flag, "1"))),
            lines["matched_difference"], note="Rate, value or tax-head differences on claims.")
        self.tax_line(
            "b_nocl", "Less: matched documents not claimed (reversed, ineligible, CN not taken)",
            lambda h: "-" + in_2b(KEYS_IN_2B, h, (cls, cm), (flag, "0")),
            lines["matched_not_claimed"])
        self.tax_line(
            "b_early", "Add: documents booked in earlier months, claimed now (were deferred)",
            lambda h: (in_2b(KEYS_CLAIM, h, (cls, _quoted(CLASS_PM))) + "+"
                       + in_2b(KEYS_CLAIM, h, (cls, _quoted(CLASS_OPENING)))),
            lines["claimed_from_earlier_books"], note="Books deferred earlier, now in GSTR-2B.")
        self.tax_line(
            "b_nobk", "Add: claimed with no entry in this month's books",
            lambda h: (in_2b(KEYS_CLAIM, h, (cls, _quoted(CLASS_NONE))) + "+"
                       + in_2b(KEYS_CLAIM, h, (cls, _quoted(CLASS_FUTURE)))),
            lines["claimed_without_books"], note="Normally nil: only if a 2B line was re-tagged.")
        self.tax_line("b_def", "Less: booked this month, not in GSTR-2B (deferred)",
                      lambda h: "-" + self.by_tag(KEYS_TAX, h, DEFER_BOOKS), lines["deferred"],
                      note="Books-only lines tagged DEFER_BOOKS.")
        self.tax_line("b_rec", "Add / (less): reclaimed lines - GSTR-2B amount vs books amount",
                      lambda h: claim_vs_books(h, [both(RECLAIM_4D1)]), lines["reclaim_difference"])
        self.tax_line("b_held", "Add: purchase returns booked but not yet reversed",
                      lambda h: "-" + self.by_tag(KEYS_TAX, h, CN_HOLD), lines["returns_held"],
                      note="Books-only lines tagged CN_HOLD.")
        self.tax_line("b_ret", "Add / (less): returns reversed - GSTR-2B amount vs books amount",
                      lambda h: claim_vs_books(h, [both(CN_TAKEN)]), lines["returns_difference"])
        self.tax_line("b_oth", "Other books-only lines (re-tagged)",
                      lambda h: claim_vs_books(h, others), lines["books_only_other"],
                      note="Only if a books-only line was given another tag.")
        self.tax_line("b_out", "Claims on books-only lines booked outside this month",
                      lambda h: _sumifs(self.lbk.rng(KEYS_CLAIM[h]), (self.lbk.rng("booked"), "0")),
                      lines["books_only_outside"])
        self.tax_line("b_man", "Less: manual 4(B)(2) reversal (Rule 37A)",
                      lambda h: "-" + self.ref("t_man", h), lines["manual_reversal"])
        self.tax_line("b_m1", "Other adjustment 1  (describe here, enter figures)", None, ZERO,
                      label_style=INPUT_TEXT, inputs=True,
                      note="For a known item the lines above cannot carry - e.g. a unique "
                           "GSTR-2B number with an explanation.")
        self.tax_line("b_m2", "Other adjustment 2  (describe here, enter figures)", None, ZERO,
                      label_style=INPUT_TEXT, inputs=True)
        self.tax_line("b_rec_total", "Net ITC reconciled to the return",
                      lambda h: self.cells(h, "b_books", "b_m2"), lines["reconciled"],
                      MONEY_BOLD, BOLD)
        self.tax_line("b_claimed", "Net ITC claimed - Table 4(C)", lambda h: self.ref("t_net", h),
                      lines["claimed"], MONEY_BOLD, BOLD)
        self.tax_line("b_zero", "Difference (must be nil)",
                      lambda h: f"{self.ref('b_claimed', h)}-{self.ref('b_rec_total', h)}",
                      lines["difference"], MONEY_TOTAL, BOLD)
        self.row += 1

    def statement(self) -> None:
        work = self.work
        s = work.ecrrs()
        carried = sum_tax(i.tax for i in work.closing_pending_2b
                          if i.carried and i.doc.sign_class != "C")
        new_pending = _sum_rows(work.rows_2b, REVERSE_4B2)
        self.section("C. ELECTRONIC CREDIT REVERSAL AND RE-CLAIMED STATEMENT")
        self.columns("Particulars", "How computed")
        self.tax_line("e_open", "Opening balance - items brought forward", None, s["opening"],
                      note="From the opening pending-2B register.")
        self.tax_line("e_add", "Add: reversed under 4(B)(2) this month",
                      lambda h: f"{self.two_b(h, REVERSE_4B2)}+{self.ref('t_man', h)}", s["added"])
        self.tax_line("e_rec", "Less: reclaimed under 4(D)(1)", lambda h: self.ref("t_d1", h),
                      s["reclaimed"])
        self.tax_line("e_close", "Closing balance",
                      lambda h: f"{self.ref('e_open', h)}+{self.ref('e_add', h)}-"
                                f"{self.ref('e_rec', h)}", s["closing"], MONEY_BOLD, BOLD)
        self.tax_line("e_reg_open", "Bill-wise register: brought forward and still open", None,
                      carried, note="Opening items not reclaimed this month.")
        self.tax_line("e_reg_new", "Bill-wise register: reversed this month, not yet booked",
                      lambda h: self.two_b(h, REVERSE_4B2), new_pending)
        self.tax_line("e_reg", "Closing per the bill-wise register",
                      lambda h: f"{self.ref('e_reg_open', h)}+{self.ref('e_reg_new', h)}",
                      s["register"], MONEY_BOLD, BOLD)
        self.tax_line("e_zero", "Difference (nil unless a pending line was booked, not reclaimed)",
                      lambda h: f"{self.ref('e_close', h)}-{self.ref('e_reg', h)}-"
                                f"{self.ref('t_man', h)}", s["difference"], MONEY_TOTAL, BOLD)
        self.tax_line("e_portal", "Balance per the portal's statement  (enter)", None, ZERO,
                      label_style=INPUT_TEXT, inputs=True,
                      note="Ledgers > Electronic Credit Reversal and Re-claimed Statement.")
        self.tax_line("e_pdiff", "Difference: portal balance less closing balance",
                      lambda h: self._diff("e_portal", "e_close", h), ZERO,
                      note="Blank until the portal balance is entered.", blank=True)
        self.row += 1

    def _diff(self, entered: str, computed: str, head: int) -> str:
        """Entered less computed, or "" while nothing has been entered."""
        span = f"$B{self.at(entered)}:$E{self.at(entered)}"
        return (f'IF(COUNTIF({span},"<>0")=0,"",'
                f"{self.ref(entered, head)}-{self.ref(computed, head)})")

    def tie_outs(self) -> None:
        work = self.work
        l2b = self.l2b
        self.section("D. TIE-OUTS TO INDEPENDENT SOURCES  (enter the figures in blue)")
        self.columns("Particulars", "Where to find it")
        self.tax_line("x_2b_in", "ITC on B2B / B2BA invoices per the portal summary  (enter)",
                      None, ZERO, label_style=INPUT_TEXT, inputs=True,
                      note="GSTR-2B > Summary > ITC available, invoices.")
        self.tax_line(
            "x_2b", "ITC on those invoices as read from the file",
            lambda h: _sumifs(l2b.rng(KEYS_TAX[h]), (l2b.rng("type"), '"<>Credit note"'),
                              (l2b.rng("avail"), '"Yes"')),
            work.gstr2b_available(), note="Detects a missing or partly pasted GSTR-2B.")
        self.tax_line("x_2b_d", "Difference", lambda h: self._diff("x_2b_in", "x_2b", h), ZERO,
                      blank=True)
        self.tax_line("x_3b_in", "All other ITC per the system-generated GSTR-3B  (enter)", None,
                      ZERO, label_style=INPUT_TEXT, inputs=True,
                      note="GSTR-3B draft, Table 4(A)(5).")
        self.tax_line("x_3b", "4(A)(5) per this workpaper", lambda h: self.ref("t_a5", h),
                      work.table4()["a5"])
        self.tax_line("x_3b_d", "Difference (reclaims and credit-note timing explain it)",
                      lambda h: self._diff("x_3b_in", "x_3b", h), ZERO, blank=True)
        self.tax_line("x_bk_in", "ITC per the books - Tally GST register / ledger  (enter)", None,
                      ZERO, label_style=INPUT_TEXT, inputs=True)
        self.tax_line("x_bk", "Net ITC per the books as read from the file",
                      lambda h: self.ref("b_books", h), work.books_itc())
        self.tax_line("x_bk_d", "Difference (reverse-charge and unregistered rows are kept out)",
                      lambda h: self._diff("x_bk_in", "x_bk", h), ZERO, blank=True)
        self.row += 1

    def registers(self) -> None:
        work = self.work
        self.section("E. REGISTERS CARRIED FORWARD")
        self.columns("Register", "Documents")

        def tot(items):
            return sum_tax(i.tax for i in items if i.doc.sign_class != "C")

        brought_b = sum_tax(Tax.of(r) for r in work.opening_books_deferred if r.sign_class != "C")
        brought_p = sum_tax(Tax.of(r) for r in work.opening_pending_2b if r.sign_class != "C")
        exposure = work.expiry_exposure()
        rows = (
            ("r_bb", "Books deferred - brought forward", brought_b,
             len(work.opening_books_deferred)),
            ("r_bc", "Books deferred - closing (booked, not in GSTR-2B)",
             tot(work.closing_books_deferred), len(work.closing_books_deferred)),
            ("r_pb", "2B pending - brought forward", brought_p, len(work.opening_pending_2b)),
            ("r_pc", "2B pending - closing (in GSTR-2B, not booked)", tot(work.closing_pending_2b),
             len(work.closing_pending_2b)),
            ("r_exp", "Closing items past the section 16(4) time limit", exposure["EXPIRED"], None),
            ("r_soon", "Closing items within about 4 months of the time limit",
             exposure["EXPIRING"], None),
        )
        for key, label, value, count in rows:
            self.tax_line(key, label, None, value,
                          note="" if count is None else f"{count} document(s)")
        self.row += 1

    def counts(self) -> None:
        counts = self.work.counts()
        self.section("F. DOCUMENTS")
        for label, key in (
            ("Documents in GSTR-2B", "gstr2b_documents"), ("Matched to the books", "matched"),
            ("  of which clean", "matched_clean"), ("  of which with a difference", "differences"),
            ("In GSTR-2B, not in the books", "not_in_books"),
            ("In the books, not in GSTR-2B", "books_only"),
            ("Pending lines reclaimed this month", "reclaimed"),
        ):
            self.sheet.write(self.row, 0, label, TEXT)
            self.sheet.write(self.row, 1, counts[key], INTEGER)
            self.row += 1
        self.row += 1

    def actions(self) -> None:
        self.section("G. ACTION ITEMS")
        for number, item in enumerate(self.work.action_items(), start=1):
            self.sheet.write(self.row, 0, f"{number}. {item}", WRAP)
            for col in range(1, 7):
                self.sheet.write(self.row, col, None, WRAP)
            self.sheet.merge(self.row, 0, self.row, 6)
            self.sheet.set_row_height(self.row, 30 if len(item) > 110 else 16)
            self.row += 1
        self.row += 1

    def index(self, descriptions: Sequence[Tuple[str, str]]) -> None:
        self.section("H. SHEETS IN THIS WORKBOOK")
        for name, text in descriptions:
            self.sheet.write(self.row, 0, name, LINK)
            self.sheet.link(self.row, 0, name)
            self.sheet.write(self.row, 1, text, TEXT)
            for col in range(2, 7):
                self.sheet.write(self.row, col, None, TEXT)
            self.sheet.merge(self.row, 1, self.row, 6)
            self.row += 1

    def checks(self, start: int) -> None:
        """The checks block, written last because it points at lines written above."""
        sheet, work, l2b, lbk = self.sheet, self.work, self.l2b, self.lbk
        lines = dict(work.bridge())
        statement = work.ecrrs()
        table = work.table4()
        claims = sum_tax(r.claim for r in work.rows_2b) + sum_tax(r.claim for r in work.rows_books)
        recognised = "{" + ",".join(_quoted(tag) for tag in ALL_TAGS) + "}"
        terms = []
        for layout in (l2b, lbk):
            if layout.count:
                terms.append(f"SUMPRODUCT(--ISNA(MATCH({layout.rng('treat')},{recognised},0)))")
        unrecognised = "+".join(terms) if terms else "0"
        ctax2 = l2b.rng("ctax")
        ctaxb = lbk.rng("ctax")
        manual_total = self.total_ref("t_man")
        r = start
        for col in range(0, 7):
            sheet.write(r, col, "CHECKS" if col == 0 else None, SECTION)
        sheet.merge(r, 0, r, 6)
        for col, text in enumerate(
                ["Check", "Result", "Difference", "", "", "", "If it says CHECK"]):
            sheet.write(r + 1, col, text, HEAD)
        rows = [
            ("The books-to-return bridge closes (section B)",
             f'IF(SUMPRODUCT(ABS({self.span("b_zero")}))<1,"OK","CHECK")',
             f'SUMPRODUCT(ABS({self.span("b_zero")}))', "OK",
             abs_total(lines["difference"]),
             "A tag or a manual line is inconsistent - read the bridge lines for the non-nil one."),
            ("The credit-reversal statement agrees to the bill-wise register (section C)",
             f'IF(SUMPRODUCT(ABS({self.span("e_zero")}))<1,"OK","CHECK")',
             f'SUMPRODUCT(ABS({self.span("e_zero")}))', "OK", abs_total(statement["difference"]),
             "A pending line was booked but not tagged RECLAIM_4D1, or the other way round."),
            ("Table 4(C) equals the sum of the claim columns",
             f'IF(ABS({self.total_ref("t_net")}+{manual_total}-SUM({ctax2})-SUM({ctaxb}))<1,"OK","CHECK")',
             f'{self.total_ref("t_net")}+{manual_total}-SUM({ctax2})-SUM({ctaxb})', "OK",
             round(table["net"].total - claims.total, 2),
             "A claim formula was overwritten on a detail sheet."),
            ("Every line carries a recognised treatment",
             f'IF({unrecognised}=0,"OK","CHECK")', unrecognised, "OK", 0,
             "A treatment was typed rather than chosen from the list, or left blank."),
            ("GSTR-2B as read agrees to the portal summary",
             f'IF(COUNTIF({self.span("x_2b_in")},"<>0")=0,"enter",'
             f'IF(SUMPRODUCT(ABS({self.span("x_2b_d")}))<1,"OK","CHECK"))',
             f'IF(COUNTIF({self.span("x_2b_in")},"<>0")=0,"",SUMPRODUCT(ABS({self.span("x_2b_d")})))',
             "enter", "", "The file is incomplete or a sheet was not read - see the Notes sheet."),
            ("ITC in the books agrees to the Tally register",
             f'IF(COUNTIF({self.span("x_bk_in")},"<>0")=0,"enter",'
             f'IF(SUMPRODUCT(ABS({self.span("x_bk_d")}))<1,"OK","CHECK"))',
             f'IF(COUNTIF({self.span("x_bk_in")},"<>0")=0,"",SUMPRODUCT(ABS({self.span("x_bk_d")})))',
             "enter", "", "Reverse-charge, unregistered or other-period rows explain most gaps."),
            ("No ITC in the closing registers is past its section 16(4) time limit",
             f'IF({self.total_ref("r_exp")}=0,"OK","CHECK")', self.total_ref("r_exp"),
             "OK" if work.expiry_exposure()["EXPIRED"].total == 0 else "CHECK",
             work.expiry_exposure()["EXPIRED"].total, "That credit can no longer be claimed."),
        ]
        for offset, (label, formula, diff_formula, cached, diff_cached, advice) in enumerate(rows):
            row = r + 2 + offset
            sheet.write(row, 0, label, TEXT)
            sheet.formula(row, 1, formula, cached=cached, style=CHECK)
            sheet.formula(row, 2, diff_formula, cached=diff_cached, style=MONEY)
            for col in (3, 4, 5):
                sheet.write(row, col, None, TEXT)
            sheet.write(row, 6, advice, NOTE)
        last = r + 1 + len(rows)
        sheet.conditional_cell(r + 2, 1, last, 1, "equal", '"OK"', OK_DXF)
        sheet.conditional_cell(r + 2, 1, last, 1, "equal", '"CHECK"', BAD_DXF)
        sheet.conditional_cell(r + 2, 1, last, 1, "equal", '"enter"', ENTER_DXF)
        self.pos["checks_end"] = last


def abs_total(value: Tax) -> float:
    """Sum of the absolute differences, as the SUMPRODUCT(ABS()) check reports it."""
    return round(sum(abs(figure) for figure in value.as_list()), 2)


def _sum_rows(rows: Sequence[Row2B], tag: str) -> Tax:
    return sum_tax(r.tax for r in rows if r.treatment == tag)


CHECK_ROWS = 7


def write_summary(book: Workbook, sheet: Sheet, work: Workpaper, l2b: Layout, lbk: Layout,
                  files: Dict[str, str], generated_at: str,
                  descriptions: Sequence[Tuple[str, str]]) -> None:
    """Lay out the Summary. Written section by section; the checks go in last, at the top."""
    summary = SummarySheet(sheet, work, l2b, lbk)
    _banner(sheet, work, "ITC RECONCILIATION - GSTR-2B vs BOOKS", 7)
    tolerance = (f"+/-{work.date_tolerance_days} day(s), Rs.{work.taxable_tolerance:,.0f} taxable, "
                 f"Rs.{work.tax_tolerance:,.0f} tax")
    policy = {"books": "purchase returns reversed in the month booked",
              "2b": "purchase returns reversed only once in GSTR-2B"}[work.cn_policy]
    info = (f"{('Generated ' + generated_at + ' | ') if generated_at else ''}"
            f"GSTR-2B: {files.get('gstr2b', '')} | Books: {files.get('books', '')} | "
            f"Tolerances {tolerance} | Credit notes: {policy}")
    sheet.write(2, 0, info, NOTE)
    sheet.merge(2, 0, 2, 6)
    sheet.write(3, 0, "Blue on pale blue = an input or a tag you may change  |  Black = formula  "
                      "|  Yellow = needs a look.  Change a TREATMENT on '2B Detail' or "
                      "'Books Only' and every figure here recalculates.", NOTE)
    sheet.merge(3, 0, 3, 6)
    sheet.set_row_height(3, 24)
    checks_start = 5
    summary.row = checks_start + 2 + CHECK_ROWS + 1
    summary.table4()
    summary.bridge()
    summary.statement()
    summary.tie_outs()
    summary.registers()
    summary.counts()
    summary.actions()
    summary.index(descriptions)
    summary.checks(checks_start)
    sheet.set_column_width(0, 0, 70)
    sheet.set_column_width(1, 5, 15)
    sheet.set_column_width(6, 6, 62)
    sheet.hide_gridlines()
    sheet.freeze_panes(2, 1)
    sheet.landscape()
    sheet.set_zoom(90)


# --------------------------------------------------------------- the other sheets


def _suggested_action(row: Row2B) -> str:
    flags = set(row.pair.flags) if row.pair else set()
    return " ".join(text for flag, text in FLAG_ACTION if flag in flags)


def write_differences(book: Workbook, work: Workpaper) -> None:
    sheet = book.add_sheet(SHEET_DIFF, tab_color="BF9000")
    rows = work.differences()
    spec = (
        ("Unique GSTR-2B no.", 13), ("Supplier GSTIN", 17), ("Supplier", 26),
        ("GSTR-2B doc no.", 16), ("Books doc no.", 16), ("GSTR-2B date", 12), ("Books date", 12),
        ("GSTR-2B taxable", 14), ("Books taxable", 14), ("Taxable difference", 14),
        ("GSTR-2B tax", 13), ("Books tax", 13), ("Tax difference", 13), ("Flags", 26),
        ("Suggested action", 60), ("Your remarks", 24),
    )
    _banner(sheet, work, "MATCHED DOCUMENTS THAT DIFFER", len(spec))
    for number, (heading, width) in enumerate(spec):
        sheet.write(HEAD_ROW, number, heading, HEAD)
        sheet.set_column_width(number, number, width)
    sheet.set_row_height(HEAD_ROW, 30)
    for number, row in enumerate(rows):
        r = FIRST_DATA_ROW + number
        e = r + 1
        doc, book_doc = row.doc, row.book
        sheet.write(r, 0, row.ref, BOLD)
        sheet.write(r, 1, doc.gstin_key, TEXT)
        sheet.write(r, 2, doc.supplier_name, TEXT)
        sheet.write(r, 3, doc.invoice_no, TEXT)
        sheet.write(r, 4, (book_doc.invoice_no or book_doc.voucher_no) if book_doc else "", TEXT)
        sheet.write(r, 5, doc.invoice_date, DATE)
        sheet.write(r, 6, book_doc.invoice_date if book_doc else None, DATE)
        sheet.write(r, 7, doc.taxable_value, MONEY)
        sheet.write(r, 8, book_doc.taxable_value if book_doc else 0.0, MONEY)
        sheet.formula(r, 9, f"I{e}-H{e}", cached=round(
            (book_doc.taxable_value if book_doc else 0.0) - doc.taxable_value, 2), style=MONEY_BOLD)
        sheet.write(r, 10, row.tax.total, MONEY)
        sheet.write(r, 11, row.books_tax.total, MONEY)
        sheet.formula(r, 12, f"L{e}-K{e}", cached=round(row.books_tax.total - row.tax.total, 2),
                      style=MONEY_BOLD)
        sheet.write(r, 13, row.pair.flag_text if row.pair else "", REVIEW)
        sheet.write(r, 14, _suggested_action(row), WRAP)
        sheet.write(r, 15, "", INPUT_TEXT)
    sheet.freeze_panes(FIRST_DATA_ROW, 3)
    if rows:
        sheet.autofilter(HEAD_ROW, 0, FIRST_DATA_ROW + len(rows) - 1, len(spec) - 1)
    sheet.print_titles(0, HEAD_ROW)
    sheet.landscape()


def write_reclaimed(book: Workbook, work: Workpaper) -> None:
    sheet = book.add_sheet(SHEET_RECLAIM, tab_color="70AD47")
    rows = work.reclaimed()
    spec = (
        ("Unique GSTR-2B no.", 13), ("Supplier GSTIN", 17), ("Supplier", 26), ("Document no.", 16),
        ("Document date", 12), ("GSTR-2B month", 11), ("Booked on", 12), ("Voucher / doc no.", 17),
        ("Months deferred", 10), ("Taxable value", 14), ("IGST", 12), ("CGST", 12), ("SGST", 12),
        ("Cess", 9), ("Total ITC reclaimed", 14), ("Your remarks", 24),
    )
    _banner(sheet, work, "ITC RECLAIMED UNDER TABLE 4(D)(1) THIS MONTH", len(spec))
    for number, (heading, width) in enumerate(spec):
        sheet.write(HEAD_ROW, number, heading, HEAD)
        sheet.set_column_width(number, number, width)
    sheet.set_row_height(HEAD_ROW, 30)
    totals = [0.0] * 5
    for number, row in enumerate(rows):
        r = FIRST_DATA_ROW + number
        e = r + 1
        pending, doc = row.pending, row.doc
        origin = origin_of(pending) if pending else None
        amounts = row.basis.as_list()
        ref = ""
        if pending is not None:
            ref = pending.ref or (f"{pending.serial} {origin.label}" if origin else "")
        sheet.write(r, 0, ref, BOLD)
        sheet.write(r, 1, doc.gstin_key, TEXT)
        sheet.write(r, 2, (pending.supplier_name if pending and pending.supplier_name
                           else doc.supplier_name), TEXT)
        sheet.write(r, 3, doc.invoice_no, TEXT)
        sheet.write(r, 4, doc.invoice_date, DATE)
        sheet.write(r, 5, origin.label if origin else "", TEXT.but(halign="center"))
        sheet.write(r, 6, doc.booking_date, DATE)
        sheet.write(r, 7, doc.voucher_no or doc.invoice_no, TEXT)
        sheet.write(r, 8, max(0, work.period.months_since(origin)) if origin else None, INTEGER)
        sheet.write(r, 9, pending.taxable_value if pending else doc.taxable_value, MONEY)
        for offset, value in enumerate(amounts):
            sheet.write(r, 10 + offset, value, MONEY)
            totals[offset] += value
        sheet.formula(r, 14, f"SUM(K{e}:N{e})", cached=row.basis.total, style=MONEY_BOLD)
        sheet.write(r, 15, "", INPUT_TEXT)
        totals[4] += row.basis.total
    total_row = FIRST_DATA_ROW + len(rows)
    sheet.write(total_row, 0, "Total reclaimed - agrees to Table 4(D)(1)", Style(
        font_size=9, bold=True, border="total"))
    for col in range(1, 10):
        sheet.write(total_row, col, None, MONEY_TOTAL)
    for offset in range(5):
        letter = col_letter(10 + offset)
        top, bottom = FIRST_DATA_ROW + 1, max(FIRST_DATA_ROW + 1, total_row)
        sheet.formula(total_row, 10 + offset, f"SUM({letter}{top}:{letter}{bottom})",
                      cached=round(totals[offset], 2), style=MONEY_TOTAL)
    sheet.freeze_panes(FIRST_DATA_ROW, 3)
    sheet.landscape()


class CarryLayout:
    """Where the two closing registers sit on the Carry-forward sheet."""

    def __init__(self) -> None:
        self.first: Dict[str, int] = {}
        self.last: Dict[str, int] = {}


CARRY_SPEC = (
    ("Origin", 9), ("Age (months)", 8), ("Supplier GSTIN", 17), ("Supplier", 26),
    ("Document no.", 16), ("Voucher no.", 16), ("Document date", 12), ("Booked on", 12),
    ("Type", 10), ("Taxable value", 14), ("IGST", 12), ("CGST", 12), ("SGST", 12), ("Cess", 9),
    ("Total tax", 13), ("Time limit s.16(4)", 12), ("Status", 10), ("Brought forward?", 9),
    ("Taken in 3B?", 9), ("Remarks", 40),
)


def write_carry_forward_sheet(book: Workbook, work: Workpaper) -> CarryLayout:
    sheet = book.add_sheet(SHEET_CARRY, tab_color="7030A0")
    layout = CarryLayout()
    _banner(sheet, work, "CARRIED FORWARD TO NEXT MONTH", len(CARRY_SPEC))
    for number, (_heading, width) in enumerate(CARRY_SPEC):
        sheet.set_column_width(number, number, width)
    sheet.write(2, 0, f"These are next month's opening registers; the same lists are written as "
                      f"{CARRY_BOOKS_FILE} and {CARRY_2B_FILE}. Status compares the "
                      f"section 16(4) time limit with the due date of this return.", NOTE)
    sheet.merge(2, 0, 2, len(CARRY_SPEC) - 1)
    r = 4
    for key, title, items in (
        ("books", "REGISTER 1 - BOOKED, NOT IN GSTR-2B (books deferred)",
         work.closing_books_deferred),
        ("pending", "REGISTER 2 - IN GSTR-2B, NOT BOOKED (2B pending; reversed under 4(B)(2))",
         work.closing_pending_2b),
    ):
        for col in range(len(CARRY_SPEC)):
            sheet.write(r, col, title if col == 0 else None, SECTION)
        sheet.merge(r, 0, r, len(CARRY_SPEC) - 1)
        r += 1
        for number, (heading, _width) in enumerate(CARRY_SPEC):
            sheet.write(r, number, heading, HEAD)
        sheet.set_row_height(r, 30)
        r += 1
        layout.first[key] = r + 1
        ordered = sorted(items, key=lambda i: (i.doc.gstin_key, i.doc.invoice_no, i.doc.voucher_no))
        totals = [0.0] * 5
        for item in ordered:
            doc = item.doc
            e = r + 1
            sheet.write(r, 0, item.origin.label if item.origin else "", TEXT.but(halign="center"))
            sheet.write(r, 1, item.age_months, INTEGER)
            sheet.write(r, 2, doc.gstin_key, TEXT)
            sheet.write(r, 3, doc.supplier_name, TEXT)
            sheet.write(r, 4, doc.invoice_no, TEXT)
            sheet.write(r, 5, doc.voucher_no, TEXT)
            sheet.write(r, 6, doc.invoice_date, DATE)
            sheet.write(r, 7, doc.booking_date, DATE)
            sheet.write(r, 8, TYPE_LABEL[doc.document_type], TEXT)
            sheet.write(r, 9, doc.taxable_value, MONEY)
            for offset, value in enumerate(item.tax.as_list()):
                sheet.write(r, 10 + offset, value, MONEY)
                totals[offset] += value
            sheet.formula(r, 14, f"SUM(K{e}:N{e})", cached=item.tax.total, style=MONEY_BOLD)
            totals[4] += item.tax.total
            sheet.write(r, 15, item.deadline, DATE)
            sheet.write(r, 16, item.deadline_status, TEXT.but(halign="center"))
            sheet.write(r, 17, "Yes" if item.carried else "New", TEXT.but(halign="center"))
            sheet.write(r, 18, "Yes" if item.taken_in_3b else "", TEXT.but(halign="center"))
            sheet.write(r, 19, item.note, WRAP)
            r += 1
        layout.last[key] = max(layout.first[key], r)
        sheet.write(r, 0, f"Total - {len(ordered)} document(s)", Style(font_size=9, bold=True,
                                                                       border="total"))
        for col in range(1, 10):
            sheet.write(r, col, None, MONEY_TOTAL)
        for offset in range(5):
            letter = col_letter(10 + offset)
            top, bottom = layout.first[key], layout.last[key]
            sheet.formula(r, 10 + offset, f"SUM({letter}{top}:{letter}{bottom})",
                          cached=round(totals[offset], 2), style=MONEY_TOTAL)
        for col in range(15, len(CARRY_SPEC)):
            sheet.write(r, col, None, MONEY_TOTAL)
        if ordered:
            sheet.conditional_cell(layout.first[key] - 1, 16, r - 1, 16, "equal", '"EXPIRED"',
                                   EXPIRED_DXF)
            sheet.conditional_cell(layout.first[key] - 1, 16, r - 1, 16, "equal", '"EXPIRING"',
                                   EXPIRING_DXF)
        r += 3
    sheet.freeze_panes(2, 4)
    sheet.landscape()
    return layout


def write_party_wise(book: Workbook, work: Workpaper, l2b: Layout, lbk: Layout,
                     carry: CarryLayout) -> None:
    sheet = book.add_sheet(SHEET_PARTY, tab_color="7030A0")
    names: Dict[str, str] = {}
    exposure: Dict[str, float] = {}

    def note(gstin: str, name: str, amount: float) -> None:
        if not gstin:
            return
        names.setdefault(gstin, "")
        if name and not names[gstin]:
            names[gstin] = name
        exposure[gstin] = exposure.get(gstin, 0.0) + abs(amount)

    for row in work.rows_2b:
        note(row.doc.gstin_key, row.doc.supplier_name,
             row.tax.total if row.treatment == REVERSE_4B2 else 0.0)
    for row in work.rows_books:
        note(row.doc.gstin_key, row.doc.supplier_name,
             row.tax.total if row.treatment == DEFER_BOOKS else 0.0)
    for item in work.closing_books_deferred + work.closing_pending_2b:
        note(item.doc.gstin_key, item.doc.supplier_name, item.tax.total)
    gstins = sorted(names, key=lambda g: (-exposure[g], g))

    spec = (
        ("Supplier GSTIN", 17), ("Supplier", 30), ("Lines in GSTR-2B", 9),
        ("ITC in GSTR-2B (all lines)", 15), ("ITC claimed", 15),
        ("Reversed - 2B not booked", 15), ("Booked - not in 2B", 15),
        ("Open register: books deferred", 15), ("Open register: 2B pending", 15),
        ("Total open exposure", 15),
    )
    _banner(sheet, work, "SUPPLIER-WISE POSITION", len(spec))
    for number, (heading, width) in enumerate(spec):
        sheet.write(HEAD_ROW, number, heading, HEAD)
        sheet.set_column_width(number, number, width)
    sheet.set_row_height(HEAD_ROW, 36)
    sheet.write(2, 0, "Everything here is a SUMIFS over the other sheets, so it follows any tag "
                      "you change. Sorted by open exposure when the workbook was written.", NOTE)
    sheet.merge(2, 0, 2, len(spec) - 1)

    def carry_range(key: str, col: str) -> str:
        return f"{quote_sheet(SHEET_CARRY)}!${col}${carry.first[key]}:${col}${carry.last[key]}"

    totals = [0.0] * 6
    for number, gstin in enumerate(gstins):
        r = FIRST_DATA_ROW + number
        e = r + 1
        g = f"$A{e}"
        in2b = [x for x in work.rows_2b if x.doc.gstin_key == gstin]
        claimed = sum_tax(x.claim for x in in2b).total + sum_tax(
            x.claim for x in work.rows_books if x.doc.gstin_key == gstin).total
        reversed_ = sum_tax(x.tax for x in in2b if x.treatment == REVERSE_4B2).total
        deferred = sum_tax(x.tax for x in work.rows_books if x.doc.gstin_key == gstin
                           and x.counts_as_booked and x.treatment == DEFER_BOOKS).total
        open_b = sum_tax(i.tax for i in work.closing_books_deferred
                         if i.doc.gstin_key == gstin).total
        open_p = sum_tax(i.tax for i in work.closing_pending_2b if i.doc.gstin_key == gstin).total
        sheet.write(r, 0, gstin, BOLD)
        sheet.write(r, 1, names[gstin], TEXT)
        sheet.formula(r, 2, f"COUNTIF({l2b.rng('gstin')},{g})", cached=len(in2b), style=INTEGER)
        sheet.formula(r, 3, _sumifs(l2b.rng("tax"), (l2b.rng("gstin"), g)),
                      cached=round(sum_tax(x.tax for x in in2b).total, 2), style=MONEY)
        sheet.formula(r, 4, _sumifs(l2b.rng("ctax"), (l2b.rng("gstin"), g)) + "+"
                      + _sumifs(lbk.rng("ctax"), (lbk.rng("gstin"), g)),
                      cached=round(claimed, 2), style=MONEY)
        sheet.formula(r, 5, _sumifs(l2b.rng("tax"), (l2b.rng("gstin"), g),
                                    (l2b.rng("treat"), _quoted(REVERSE_4B2))),
                      cached=round(reversed_, 2), style=MONEY)
        sheet.formula(r, 6, _sumifs(lbk.rng("tax"), (lbk.rng("gstin"), g), (lbk.rng("booked"), "1"),
                                    (lbk.rng("treat"), _quoted(DEFER_BOOKS))),
                      cached=round(deferred, 2), style=MONEY)
        sheet.formula(r, 7, _sumifs(carry_range("books", "O"), (carry_range("books", "C"), g)),
                      cached=round(open_b, 2), style=MONEY)
        sheet.formula(r, 8, _sumifs(carry_range("pending", "O"), (carry_range("pending", "C"), g)),
                      cached=round(open_p, 2), style=MONEY)
        sheet.formula(r, 9, f"H{e}+I{e}", cached=round(open_b + open_p, 2), style=MONEY_BOLD)
        for offset, value in enumerate((len(in2b), sum_tax(x.tax for x in in2b).total, claimed,
                                        reversed_, deferred, open_b)):
            totals[offset] += value
    sheet.freeze_panes(FIRST_DATA_ROW, 2)
    if gstins:
        sheet.autofilter(HEAD_ROW, 0, FIRST_DATA_ROW + len(gstins) - 1, len(spec) - 1)
    sheet.print_titles(0, HEAD_ROW)
    sheet.landscape()


NOTES_METHOD = (
    "GSTR-3B Table 4(A)(5) is kept tied to GSTR-2B. A GSTR-2B line that is not in the books is "
    "reversed under 4(B)(2) and tracked as 'pending'; the month it is booked it is reclaimed "
    "under 4(D)(1). A books entry that is not in GSTR-2B is 'deferred': not claimed until the "
    "supplier's return puts it into GSTR-2B (section 16(2)(aa)).",
    "Both registers are carried forward, so the Carry-forward sheet - or the two CSV files - is "
    "next month's opening. The credit-reversal statement (opening + 4(B)(2) - 4(D)(1) = closing) "
    "must agree with the pending register, and the ITC booked must bridge to the ITC claimed "
    "with a difference of nil.",
    "Every GSTR-2B line carries a TREATMENT chosen by the tool from how it matched. Change it "
    "from the drop-down and the Summary, Party-wise and the checks recalculate. A tag you type "
    "instead of choosing fails the 'recognised treatment' check.",
    "Documents are matched per supplier GSTIN in this order: exact invoice number, invoice "
    "number ignoring punctuation and leading zeros, the trailing number with date and value "
    "agreeing, taxable value with date, and - for number-less purchase returns only - supplier "
    "and value. The best candidate wins; nothing is matched twice.",
    "A document is 'booked this month' (CM) when its voucher date falls in the return month; "
    "earlier is PM, later is FUTURE. Without a voucher-date column every row is treated as "
    "booked this month.",
)
NOTES_LIMITS = (
    "Not covered here: ISD credit, import of goods (IMPG), the outward-supply reconciliation "
    "(GSTR-1 vs books vs GSTR-3B), the reverse-charge workings (only their rows are listed), "
    "Rule 42/43 reversals and the 180-day payment rule. Reverse-charge rows are kept out of the "
    "match and listed below.",
    "Credit notes in GSTR-2B that are not in the books are reversed here (the cautious choice). "
    "If a credit note is not yours, reject it in IMS and tag it CN_NOT_OURS.",
    "The section 16(4) time limit used is 30 November following the end of the financial year "
    "of the document, or the annual return if filed earlier. It is shown so that ageing items "
    "can be chased; confirm the limit that applies to each document.",
    "Amended documents (B2BA / CDNRA) are read as their revised version. The matching "
    "tolerances are review thresholds, not law. This is an educational tool: review every "
    "treatment before filing.",
)


def write_notes(book: Workbook, work: Workpaper, files: Dict[str, str]) -> None:
    sheet = book.add_sheet(SHEET_NOTES, tab_color="808080")
    _banner(sheet, work, "NOTES, LEGEND AND DATA QUALITY", 4)
    sheet.set_column_width(0, 0, 24)
    sheet.set_column_width(1, 1, 20)
    sheet.set_column_width(2, 2, 80)
    sheet.set_column_width(3, 3, 40)
    r = 3

    def block(title: str, lines: Sequence[str]) -> None:
        nonlocal r
        for col in range(4):
            sheet.write(r, col, title if col == 0 else None, SECTION)
        sheet.merge(r, 0, r, 3)
        r += 1
        for line in lines:
            sheet.write(r, 0, line, WRAP)
            for col in range(1, 4):
                sheet.write(r, col, None, WRAP)
            sheet.merge(r, 0, r, 3)
            sheet.set_row_height(r, 14 * max(1, -(-len(line) // 150)))
            r += 1
        r += 1

    block("HOW THE WORKPAPER WORKS", NOTES_METHOD)
    for col in range(4):
        sheet.write(r, col, "TREATMENTS" if col == 0 else None, SECTION)
    sheet.merge(r, 0, r, 3)
    r += 1
    for col, text in enumerate(["Treatment", "Applies to", "Meaning", ""]):
        sheet.write(r, col, text, HEAD)
    r += 1
    for tag in ALL_TAGS:
        applies = ("GSTR-2B line" if tag in TAGS_2B_INVOICE + TAGS_2B_CREDIT else "Books-only line")
        sheet.write(r, 0, tag, BOLD)
        sheet.write(r, 1, applies, TEXT)
        sheet.write(r, 2, TREATMENT_HELP[tag], WRAP)
        sheet.write(r, 3, None, TEXT)
        r += 1
    r += 1
    block("ASSUMPTIONS AND LIMITS", NOTES_LIMITS)
    block("HOW THE FILES WERE READ", work.notes or ["Nothing to report."])
    block("DATA-QUALITY WARNINGS", work.warnings or ["None."])
    rcm = ([f"GSTR-2B  {d.gstin_key}  {d.invoice_no}  tax Rs.{d.total_tax:,.2f}"
            for d in work.rcm_2b] + [f"Books  {d.gstin_key}  {d.invoice_no or d.voucher_no}  "
                                     f"tax Rs.{d.total_tax:,.2f}" for d in work.rcm_books])
    block("REVERSE-CHARGE ROWS KEPT OUT OF THE MATCH", rcm or ["None."])
    sheet.landscape()


# ---------------------------------------------------------------------- assembly

SHEET_DESCRIPTIONS = (
    (SHEET_2B, "Every GSTR-2B document with its books match - tag each line; the Summary follows."),
    (SHEET_BOOKS, "Books documents GSTR-2B does not carry: deferred, reclaiming, returns."),
    (SHEET_DIFF, "Matched documents that disagree in value, rate, tax head or date."),
    (SHEET_RECLAIM, "Pending GSTR-2B lines booked this month - the 4(D)(1) reclaim."),
    (SHEET_CARRY, "The two closing registers with ageing and the section 16(4) time limit."),
    (SHEET_PARTY, "Supplier-wise position and open exposure."),
    (SHEET_NOTES, "The method, the treatment glossary, assumptions, and how the files were read."),
)


def build_workbook(work: Workpaper, files: Optional[Dict[str, str]] = None,
                   generated_at: str = "") -> Workbook:
    """Assemble the whole workpaper in memory."""
    files = files or {}
    book = Workbook(
        title=f"GSTR-2B vs Books ITC workpaper - {work.period.long_label}",
        author="gstr2b_workpaper_hack",
        created=f"{work.period.last_day.isoformat()}T00:00:00Z",
    )
    l2b = Layout(SHEET_2B, SPEC_2B, len(work.rows_2b))
    lbk = Layout(SHEET_BOOKS, SPEC_BOOKS, len(work.rows_books))
    summary = book.add_sheet(SHEET_SUMMARY, tab_color=NAVY)      # first tab, filled last
    write_2b_detail(book, work)
    write_books_only(book, work)
    write_differences(book, work)
    write_reclaimed(book, work)
    carry = write_carry_forward_sheet(book, work)
    write_party_wise(book, work, l2b, lbk, carry)
    write_notes(book, work, files)
    write_summary(book, summary, work, l2b, lbk, files, generated_at, SHEET_DESCRIPTIONS)
    return book


def workpaper_filename(period: Period) -> str:
    return f"GSTR2B_vs_Books_{period.label}.xlsx"


def write_workpaper(work: Workpaper, outdir: str, files: Optional[Dict[str, str]] = None,
                    generated_at: str = "") -> List[str]:
    """Write the workbook, the carry-forward CSVs and summary.txt. Returns the paths."""
    os.makedirs(outdir, exist_ok=True)
    book_path = os.path.join(outdir, workpaper_filename(work.period))
    build_workbook(work, files, generated_at).save(book_path)
    paths = [book_path] + write_carry_forward(work, outdir)
    summary_path = os.path.join(outdir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write(text_summary(work) + "\n")
    return paths + [summary_path]


def text_summary(work: Workpaper) -> str:
    """The headline figures as plain text (Rs. is used so a Windows console can print it)."""
    t4, lines = work.table4(), dict(work.bridge())
    statement, counts = work.ecrrs(), work.counts()
    bar = "=" * 78

    def row(label: str, tax: Tax) -> str:
        figures = "".join(f"{figure:>13,.2f}" for figure in tax.as_list())
        return f"  {label:<26}{figures}{tax.total:>14,.2f}"

    out = [bar, f"GSTR-2B vs BOOKS - ITC WORKPAPER - {work.period.long_label} "
                f"(F.Y. {work.period.fy_label})", bar]
    if work.client or work.gstin:
        out.append(f"{work.client}  {work.gstin}".strip())
    out.append(f"  {'':<26}{'IGST':>13}{'CGST':>13}{'SGST':>13}{'Cess':>13}{'Total':>14}")
    out.append("GSTR-3B Table 4")
    for key, label in (("a5", "4(A)(5) All other ITC"), ("b1", "4(B)(1) Reversed"),
                       ("b2", "4(B)(2) Reclaimable"), ("net", "4(C) Net ITC"),
                       ("d1", "4(D)(1) Reclaimed"), ("d2", "4(D)(2) Ineligible")):
        out.append(row(label, t4[key]))
    out.append("Bridge")
    out.append(row("ITC per books", lines["books"]))
    out.append(row("ITC claimed", lines["claimed"]))
    out.append(row("Difference", lines["difference"]))
    out.append("Credit-reversal statement")
    for key, label in (("opening", "Opening"), ("added", "Add 4(B)(2)"),
                       ("reclaimed", "Less 4(D)(1)"), ("closing", "Closing"),
                       ("register", "Per register")):
        out.append(row(label, statement[key]))
    out.append("")
    out.append(f"Documents: {counts['gstr2b_documents']} in GSTR-2B | {counts['matched']} matched "
               f"({counts['differences']} differ) | {counts['not_in_books']} not in books | "
               f"{counts['books_only']} books-only")
    out.append(f"Carried forward: {counts['closing_books_deferred']} books deferred, "
               f"{counts['closing_pending_2b']} 2B pending")
    out.append("")
    out.append("Action items")
    out += [f"  {number}. {item}" for number, item in enumerate(work.action_items(), start=1)]
    out.append(bar)
    return "\n".join(out)


# --------------------------------------------------------------------------- CLI

_PORTAL_NAME = re.compile(r"GSTR2B[_\- ]+([0-9A-Z]{15})[_\- ]+(\d{2})(\d{4})", re.I)


def infer_from_filename(spec: str) -> Tuple[str, Optional[Period]]:
    """The portal names its download GSTR2B_<GSTIN>_<MMYYYY>.xlsx: read GSTIN and month from it."""
    path, _sheet = split_file_spec(spec)
    found = _PORTAL_NAME.search(os.path.basename(path))
    if not found:
        return "", None
    return found.group(1).upper(), parse_period(f"{found.group(2)}{found.group(3)}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gstr2b_workpaper_hack.py",
        description="Monthly GSTR-2B vs Books ITC workpaper (Excel), built entirely offline.",
        epilog="Nothing is uploaded: files are read from disk and the workpaper is written to "
               "disk. Run with no arguments for a demo.",
    )
    parser.add_argument("--gstr2b", "-g", required=True, metavar="FILE[#SHEET]",
                        help="GSTR-2B download from the portal (.xlsx) or an equivalent CSV")
    parser.add_argument("--books", "-b", required=True, metavar="FILE[#SHEET]",
                        help="purchase register for the month (.xlsx or .csv)")
    parser.add_argument("--period", "-p", metavar="MONTH",
                        help="return month, e.g. 2026-09 or Sep-26 (read from the GSTR-2B file "
                             "name when it follows the portal's naming)")
    parser.add_argument("--opening-books-deferred", metavar="FILE[#SHEET]",
                        help="last month's books-deferred register")
    parser.add_argument("--opening-2b-pending", metavar="FILE[#SHEET]",
                        help="last month's 2B-pending register")
    parser.add_argument("--opening-dir", metavar="DIR",
                        help=f"a previous run's output folder; uses its {CARRY_BOOKS_FILE} and "
                             f"{CARRY_2B_FILE}")
    parser.add_argument("--client", default="", help="client name for the sheet headings")
    parser.add_argument("--gstin", default="", help="client GSTIN for the sheet headings")
    parser.add_argument("--cn-policy", choices=CN_POLICIES, default="books",
                        help="'books': reverse ITC on a purchase return in the month it is "
                             "booked; '2b': only once the credit note is in GSTR-2B")
    parser.add_argument("--date-tolerance", type=int, default=DEFAULT_DATE_TOLERANCE_DAYS,
                        metavar="DAYS")
    parser.add_argument("--value-tolerance", type=float, default=DEFAULT_TAXABLE_TOLERANCE,
                        metavar="RUPEES")
    parser.add_argument("--tax-tolerance", type=float, default=DEFAULT_TAX_TOLERANCE,
                        metavar="RUPEES")
    parser.add_argument("--outdir", "-o", metavar="DIR",
                        help="where to write the workpaper (default: ./gstr2b_<month>)")
    parser.add_argument("--no-timestamp", action="store_true",
                        help="leave the run time out, so identical inputs give identical files")
    parser.add_argument("--quiet", "-q", action="store_true", help="do not print the summary")
    return parser


def _opening_path(explicit: Optional[str], directory: Optional[str], name: str) -> str:
    if explicit:
        return explicit
    if directory:
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
        raise ValueError(f"{candidate} not found - was that folder written by this tool?")
    return ""


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the workpaper from the command line. Returns an exit code."""
    args = build_arg_parser().parse_args(argv)
    try:
        gstin_hint, period_hint = infer_from_filename(args.gstr2b)
        period = require_period(args.period) if args.period else period_hint
        if period is None:
            raise ValueError("say which month this is with --period, e.g. --period 2026-09")
        gstr2b, notes_2b = load_records(args.gstr2b, SOURCE_GSTR2B, side="portal")
        books, notes_books = load_records(args.books, SOURCE_BOOKS, side="books")
        opening = load_opening_items(
            _opening_path(args.opening_books_deferred, args.opening_dir, CARRY_BOOKS_FILE),
            _opening_path(args.opening_2b_pending, args.opening_dir, CARRY_2B_FILE))
        work = build_workpaper(
            gstr2b, books, period, opening,
            reconciler=GSTR2BReconciler(args.date_tolerance, args.value_tolerance,
                                        args.tax_tolerance),
            cn_policy=args.cn_policy, client=args.client,
            gstin=(args.gstin or gstin_hint).upper(), read_notes=notes_2b + notes_books)
        outdir = args.outdir or f"gstr2b_{period.label}"
        stamp = "" if args.no_timestamp else datetime.now().strftime("%d-%b-%Y %H:%M")
        files = {"gstr2b": os.path.basename(split_file_spec(args.gstr2b)[0]),
                 "books": os.path.basename(split_file_spec(args.books)[0])}
        written = write_workpaper(work, outdir, files, stamp)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: could not read or write a file ({error})", file=sys.stderr)
        return 2
    if not args.quiet:
        print(text_summary(work))
    for path in written:
        print(f"written: {path}")
    return 0


def _run_demo() -> None:
    from gstr2b_itc_workflow_hack import _demo_records
    two_b, books, opening = _demo_records()
    work = build_workpaper(two_b, books, Period(2026, 9), opening, client="Demo Traders",
                           gstin="19AAAAA0000A1Z5")
    outdir = os.path.join(tempfile.gettempdir(), "gstr2b_workpaper_demo")
    paths = write_workpaper(work, outdir, {"gstr2b": "demo GSTR-2B", "books": "demo books"})
    print(text_summary(work))
    for path in paths:
        print(f"written: {path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    _run_demo()
