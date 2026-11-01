"""Unit tests for gstr2b_workpaper_hack."""

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from xml.etree import ElementTree as ET

import gstr2b_itc_workflow_hack as wf
import gstr2b_workpaper_hack as wp
from gstr2b_itc_workflow_hack import (
    CARRY_2B_FILE,
    CARRY_BOOKS_FILE,
    Period,
    build_workpaper,
)
from gstr2b_reconciliation_hack import read_xlsx_sheets
from xlsx_writer_hack import Workbook

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SEP = Period(2026, 9)


def demo_work():
    two_b, books, opening = wf._demo_records()
    return build_workpaper(two_b, books, SEP, opening, client="Demo Traders",
                           gstin="19AAAAA0000A1Z5")


def number(text):
    return float(text) if text not in ("", None) else 0.0


def _column_number(letters):
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


def self_referencing_formulas(path):
    """[(sheet, cell, formula)] for formulas whose own cell is inside a range they sum."""
    found = []
    with zipfile.ZipFile(path) as archive:
        names = [sheet.get("name") for sheet in ET.fromstring(
            archive.read("xl/workbook.xml")).iter(f"{{{NS['m']}}}sheet")]
        for index, name in enumerate(names, start=1):
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            for cell in root.iter(f"{{{NS['m']}}}c"):
                formula = cell.find("m:f", NS)
                if formula is None or not formula.text:
                    continue
                ref = cell.get("r")
                column, row = re.match(r"([A-Z]+)(\d+)", ref).groups()
                # ranges on this sheet only: one with a sheet name in front belongs elsewhere
                for left, right in re.findall(
                        r"(?<![!\w$])(\$?[A-Z]{1,3}\$?\d+):(\$?[A-Z]{1,3}\$?\d+)", formula.text):
                    (c1, r1), (c2, r2) = (re.match(r"\$?([A-Z]+)\$?(\d+)", end).groups()
                                          for end in (left, right))
                    if (_column_number(c1) <= _column_number(column) <= _column_number(c2)
                            and min(int(r1), int(r2)) <= int(row) <= max(int(r1), int(r2))):
                        found.append((name, ref, formula.text))
    return found


class WorkbookFixture(unittest.TestCase):
    """The demo workpaper, written once per test class and read back with stdlib tools."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.work = demo_work()
        cls.path = os.path.join(cls.temp.name, "wp.xlsx")
        wp.build_workbook(cls.work, {"gstr2b": "g.xlsx", "books": "b.xlsx"}).save(cls.path)
        cls.sheets = {name: rows for name, rows in read_xlsx_sheets(cls.path)}
        with zipfile.ZipFile(cls.path) as archive:
            cls.parts = {name: archive.read(name) for name in archive.namelist()}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def row(self, sheet, label):
        for row in self.sheets[sheet]:
            if row and row[0] == label:
                return row
        raise AssertionError(f"no row labelled {label!r} on {sheet}")

    def figures(self, label, sheet=wp.SHEET_SUMMARY):
        row = self.row(sheet, label)
        return [number(cell) for cell in (row + [""] * 6)[1:6]]


class WorkbookStructureTests(WorkbookFixture):
    def test_sheets_and_their_order(self):
        names = [name for name, _rows in read_xlsx_sheets(self.path)]
        self.assertEqual(names, [wp.SHEET_SUMMARY, wp.SHEET_2B, wp.SHEET_BOOKS, wp.SHEET_DIFF,
                                 wp.SHEET_RECLAIM, wp.SHEET_CARRY, wp.SHEET_PARTY,
                                 wp.SHEET_NOTES])

    def test_summary_is_the_selected_first_sheet(self):
        first = ET.fromstring(self.parts["xl/worksheets/sheet1.xml"])
        self.assertEqual(first.find("m:sheetViews/m:sheetView", NS).get("tabSelected"), "1")

    def test_the_banner_names_the_client_and_period(self):
        banner = self.sheets[wp.SHEET_SUMMARY]
        self.assertIn("Demo Traders", banner[0][0])
        self.assertIn("19AAAAA0000A1Z5", banner[0][0])
        self.assertIn("September 2026", banner[1][0])
        self.assertIn("2026-27", banner[1][0])

    def test_the_workbook_is_formula_driven(self):
        counts = {}
        for number_, name in enumerate(self.sheets, start=1):
            root = ET.fromstring(self.parts[f"xl/worksheets/sheet{number_}.xml"])
            counts[name] = len(root.findall(".//m:f", NS))
        self.assertGreater(counts[wp.SHEET_SUMMARY], 150)
        self.assertGreater(counts[wp.SHEET_2B], 40)
        self.assertGreater(counts[wp.SHEET_BOOKS], 20)
        self.assertEqual(counts[wp.SHEET_NOTES], 0)

    def test_summary_formulas_point_at_the_detail_sheets(self):
        root = ET.fromstring(self.parts["xl/worksheets/sheet1.xml"])
        formulas = " ".join(f.text or "" for f in root.findall(".//m:f", NS))
        self.assertIn("SUMIFS('2B Detail'!", formulas)
        self.assertIn("'Books Only'!", formulas)
        self.assertIn('"REVERSE_4B2"', formulas)
        self.assertIn("SUMPRODUCT(ABS(", formulas)

    def test_treatment_cells_have_drop_downs_and_the_detail_sheets_filter_and_freeze(self):
        root = ET.fromstring(self.parts["xl/worksheets/sheet2.xml"])
        lists = [v.find("m:formula1", NS).text for v in
                 root.findall("m:dataValidations/m:dataValidation", NS)]
        self.assertTrue(any("CLAIM" in item and "REVERSE_4B2" in item for item in lists))
        self.assertTrue(any("CN_REVERSE" in item for item in lists))
        self.assertIsNotNone(root.find("m:autoFilter", NS))
        self.assertEqual(root.find(".//m:pane", NS).get("state"), "frozen")

    def test_no_stray_dependencies_on_other_files(self):
        self.assertFalse([n for n in self.parts if "externalLinks" in n])
        self.assertFalse(re.search(rb"https?://(?!schemas\.|purl\.)", b" ".join(
            data for name, data in self.parts.items() if name.startswith("xl/"))))


class SummaryFigureTests(WorkbookFixture):
    """The figures a reviewer reads first, against the hand-worked demo."""

    def test_table_4(self):
        self.assertEqual(self.figures("4(A)(5) All other ITC")[4], 11500)
        self.assertEqual(self.figures("4(C) Net ITC available = 4(A)(5) - 4(B)")[4], 9500)
        self.assertEqual(self.figures(
            "4(B)(2) Others - reclaimable (reversed now, reclaimed when booked)")[4], 2000)
        self.assertEqual(self.figures(
            "4(D)(1) ITC reclaimed which was reversed under 4(B)(2) earlier")[4], 1500)
        igst_a5 = self.figures("4(A)(5) All other ITC")
        self.assertEqual(igst_a5[:4], [7000, 2250, 2250, 0])

    def test_bridge_closes(self):
        self.assertEqual(self.figures("Net ITC as per the books - this month's purchases "
                                      "less returns")[4], 8900)
        self.assertEqual(self.figures("Net ITC claimed - Table 4(C)")[4], 9500)
        self.assertEqual(self.figures("Net ITC reconciled to the return")[4], 9500)
        self.assertEqual(self.figures("Difference (must be nil)"), [0, 0, 0, 0, 0])

    def test_credit_reversal_statement(self):
        self.assertEqual(self.figures("Opening balance - items brought forward")[4], 1500)
        self.assertEqual(self.figures("Closing balance")[4], 2000)
        self.assertEqual(self.figures("Closing per the bill-wise register")[4], 2000)
        self.assertEqual(self.figures(
            "Difference (nil unless a pending line was booked, not reclaimed)")[4], 0)

    def test_the_checks_block(self):
        rows = {row[0]: row[1] for row in self.sheets[wp.SHEET_SUMMARY][5:16] if row and row[0]}
        self.assertEqual(rows["The books-to-return bridge closes (section B)"], "OK")
        self.assertEqual(rows["Every line carries a recognised treatment"], "OK")
        self.assertEqual(rows["GSTR-2B as read agrees to the portal summary"], "enter")
        self.assertEqual(rows["ITC in the books agrees to the Tally register"], "enter")
        self.assertEqual(
            rows["No ITC in the closing registers is past its section 16(4) time limit"], "OK")

    def test_action_items_are_listed(self):
        text = " ".join(row[0] for row in self.sheets[wp.SHEET_SUMMARY] if row)
        self.assertIn("Chase 1 supplier(s)", text)

    def test_the_inputs_start_blank(self):
        for label in ("Rule 37A / other manual 4(B)(2) reversal  (enter)",
                      "ITC on B2B / B2BA invoices per the portal summary  (enter)"):
            self.assertEqual(self.figures(label), [0, 0, 0, 0, 0])


class DetailSheetTests(WorkbookFixture):
    def test_every_gstr2b_line_is_there_with_its_tag(self):
        rows = self.sheets[wp.SHEET_2B][wp.FIRST_DATA_ROW:]
        header = self.sheets[wp.SHEET_2B][wp.HEAD_ROW]
        treat = header.index("TREATMENT")
        number_col = header.index("Document no.")
        tags = {row[number_col]: row[treat] for row in rows if row and row[0]}
        self.assertEqual(tags, {"A/101": "CLAIM", "B-77": "CLAIM", "C/9": "REVERSE_4B2",
                                "D-12": "CLAIM_PM_BOOKED", "CN-4": "CN_REVERSE"})

    def test_match_columns(self):
        header = self.sheets[wp.SHEET_2B][wp.HEAD_ROW]
        rows = {row[header.index("Document no.")]: row
                for row in self.sheets[wp.SHEET_2B][wp.FIRST_DATA_ROW:] if row and row[0]}
        self.assertEqual(rows["A/101"][header.index("Match status")], "Matched")
        self.assertEqual(rows["C/9"][header.index("Match status")], "Not in books")
        self.assertEqual(rows["D-12"][header.index("Books entry")], "OPENING")
        self.assertEqual(rows["CN-4"][header.index("Matched on")], "Supplier and value only")
        self.assertEqual(rows["A/101"][header.index("Unique GSTR-2B no.")], "1 Sep-26")

    def test_books_only_sheet(self):
        header = self.sheets[wp.SHEET_BOOKS][wp.HEAD_ROW]
        rows = {row[header.index("Voucher / doc no.")]: row
                for row in self.sheets[wp.SHEET_BOOKS][wp.FIRST_DATA_ROW:] if row and row[0]}
        self.assertEqual(rows["E-5"][header.index("TREATMENT")], "DEFER_BOOKS")
        self.assertEqual(rows["F-3"][header.index("TREATMENT")], "RECLAIM_4D1")
        self.assertEqual(number(rows["F-3"][header.index("Total claim")]), 1500)
        self.assertEqual(number(rows["E-5"][header.index("Total claim")]), 0)

    def test_filter_aware_totals_sit_above_the_headings(self):
        totals = self.sheets[wp.SHEET_2B][wp.TOTALS_ROW]
        header = self.sheets[wp.SHEET_2B][wp.HEAD_ROW]
        self.assertEqual(number(totals[header.index("Total tax")]), 10000)
        self.assertEqual(number(totals[header.index("Total claim")]), 9500 - 1500)
        root = ET.fromstring(self.parts["xl/worksheets/sheet2.xml"])
        subtotals = [f.text for f in root.findall(".//m:f", NS)
                     if f.text.startswith("SUBTOTAL(109")]
        self.assertGreater(len(subtotals), 10)

    def test_differences_sheet_explains_the_weak_match(self):
        rows = [r for r in self.sheets[wp.SHEET_DIFF][wp.FIRST_DATA_ROW:] if r and r[0]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "5 Sep-26")
        self.assertIn("NO_DOC_REFERENCE", rows[0][13])
        self.assertIn("confirm it is the same document", rows[0][14])

    def test_reclaimed_sheet_agrees_to_table_4d1(self):
        rows = self.sheets[wp.SHEET_RECLAIM][wp.FIRST_DATA_ROW:]
        data = [r for r in rows if r and r[0] and not r[0].startswith("Total")]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0][1:4][2], "F-3")
        self.assertEqual(data[0][5], "Aug-26")
        self.assertEqual(number(data[0][14]), 1500)
        total = [r for r in rows if r and r[0].startswith("Total")][0]
        self.assertEqual(number(total[14]), 1500)

    def test_carry_forward_registers(self):
        text = [" | ".join(r) for r in self.sheets[wp.SHEET_CARRY] if r]
        joined = "\n".join(text)
        self.assertIn("REGISTER 1", joined)
        self.assertIn("REGISTER 2", joined)
        self.assertIn("E-5", joined)
        self.assertIn("C/9", joined)

    def test_party_wise_lists_every_supplier_once(self):
        rows = [r for r in self.sheets[wp.SHEET_PARTY][wp.FIRST_DATA_ROW:] if r and r[0]]
        gstins = [r[0] for r in rows]
        self.assertEqual(len(gstins), len(set(gstins)))
        self.assertEqual(len(gstins), 6)
        by = {r[0]: r for r in rows}
        pending = by["24CCCCC3333C1Z3"]
        self.assertEqual(number(pending[5]), 2000)        # reversed - 2B not booked

    def test_notes_sheet_carries_the_glossary_and_the_read_notes(self):
        text = "\n".join(" ".join(r) for r in self.sheets[wp.SHEET_NOTES] if r)
        for tag in wf.TREATMENT_HELP:
            self.assertIn(tag, text)
        self.assertIn("HOW THE WORKPAPER WORKS", text)
        self.assertIn("ASSUMPTIONS AND LIMITS", text)


class EmptyAndEdgeTests(unittest.TestCase):
    def test_an_empty_month_builds_cleanly(self):
        work = build_workpaper([], [], SEP)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "empty.xlsx")
            wp.build_workbook(work).save(path)
            sheets = dict(read_xlsx_sheets(path))
        summary = sheets[wp.SHEET_SUMMARY]
        checks = {row[0]: row[1] for row in summary[5:16] if row and row[0]}
        self.assertEqual(checks["The books-to-return bridge closes (section B)"], "OK")
        self.assertEqual(checks["Every line carries a recognised treatment"], "OK")
        self.assertIn("No action required", " ".join(r[0] for r in summary if r))

    def test_no_formula_sums_a_range_that_contains_itself(self):
        # An empty register or an empty reclaim list left its totals row summing itself - a
        # circular reference Excel warns about on opening. It only shows when a list is empty.
        empty_registers = build_workpaper([], [], SEP)
        with tempfile.TemporaryDirectory() as folder:
            for label, work in (("empty", empty_registers), ("demo", demo_work())):
                path = os.path.join(folder, f"{label}.xlsx")
                wp.build_workbook(work).save(path)
                self.assertEqual(self_referencing_formulas(path), [], label)

    def test_identical_inputs_give_identical_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = (os.path.join(folder, name) for name in ("a.xlsx", "b.xlsx"))
            wp.build_workbook(demo_work(), generated_at="").save(first)
            wp.build_workbook(demo_work(), generated_at="").save(second)
            with open(first, "rb") as a, open(second, "rb") as b:
                self.assertEqual(a.read(), b.read())

    def test_text_summary(self):
        text = wp.text_summary(demo_work())
        self.assertIn("September 2026", text)
        self.assertIn("4(A)(5) All other ITC", text)
        self.assertIn("11,500.00", text)
        self.assertIn("9,500.00", text)
        self.assertIn("Chase 1 supplier(s)", text)
        self.assertNotIn("₹", text)           # prints on a Windows console

    def test_file_name_hints(self):
        gstin, period = wp.infer_from_filename("/tmp/GSTR2B_19AAAAA0000A1Z5_092026.xlsx")
        self.assertEqual((gstin, period), ("19AAAAA0000A1Z5", SEP))
        self.assertEqual(wp.infer_from_filename("returns.xlsx"), ("", None))
        gstin, period = wp.infer_from_filename("gstr2b-19AAAAA0000A1Z5-042026.xlsx")
        self.assertEqual(period, Period(2026, 4))

    def test_file_name_hints_in_the_month_first_form(self):
        # the portal also names its download <MMYYYY>_<GSTIN>_GSTR2B_<date generated>.xlsx
        name = "/x/112025_19AAAAA0000A1Z5_GSTR2B_15122025.xlsx"
        self.assertEqual(wp.infer_from_filename(name), ("19AAAAA0000A1Z5", Period(2025, 11)))
        prefixed = "7f3a-112025_19AAAAA0000A1Z5_GSTR2B_15122025.xlsx"
        self.assertEqual(wp.infer_from_filename(prefixed)[1], Period(2025, 11))
        self.assertEqual(wp.infer_from_filename("112025_19AAAAA0000A1Z5_GSTR3B_15122025.xlsx"),
                         ("", None))
        self.assertEqual(wp.infer_from_filename("132025_19AAAAA0000A1Z5_GSTR2B_1.xlsx")[1], None)

    def test_the_period_from_the_portal_header(self):
        details = {"financial_year": "2025-26", "tax_period": "November"}
        self.assertEqual(wp.period_from_details(details), Period(2025, 11))
        details = {"financial_year": "2025-26", "tax_period": "February"}
        self.assertEqual(wp.period_from_details(details), Period(2026, 2))
        self.assertIsNone(wp.period_from_details({"financial_year": "2025-26"}))
        self.assertIsNone(wp.period_from_details({"tax_period": "March"}))
        self.assertIsNone(wp.period_from_details({"financial_year": "2025-26",
                                                  "tax_period": "Quarter 3"}))

    def test_a_disagreeing_header_is_flagged(self):
        details = {"financial_year": "2025-26", "tax_period": "October",
                   "gstin": "19AAAAA0000A1Z5"}
        self.assertEqual(wp.check_against_portal(Period(2025, 10), "19AAAAA0000A1Z5", details),
                         [])
        notes = wp.check_against_portal(Period(2025, 11), "27BBBBB2222B1Z2", details)
        self.assertEqual(len(notes), 2)
        self.assertIn("October 2025", notes[0])
        self.assertIn("19AAAAA0000A1Z5", notes[1])
        self.assertEqual(wp.check_against_portal(Period(2025, 11), "", {}), [])

    def test_treatment_lists_cover_every_tag(self):
        self.assertEqual(set(wp.ALL_TAGS), set(wf.TREATMENT_HELP))


# ---------------------------------------------------------------------------- CLI


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


PORTAL_HEADER = ["GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice type",
                 "Invoice Date", "Taxable Value (Rs)", "Integrated Tax(Rs)", "Central Tax(Rs)",
                 "State/UT Tax(Rs)", "ITC Availability"]
BOOKS_HEADER = ["Date", "Particulars", "GSTIN/UIN", "Vch Type", "Vch No.", "Doc No.",
                "Doc date", "Taxable", "Integrated Tax", "Central Tax", "State Tax"]
PURCHASE = "Registered Purchase"
A, B = "19AAAAA1111A1Z1", "27BBBBB2222B1Z2"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = wp.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = self.temp.name

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def august(self):
        two_b, books = self.path("GSTR2B_19AAAAA1111A1Z1_082026.csv"), self.path("books_aug.csv")
        write_csv(two_b, PORTAL_HEADER, [
            [A, "Alpha", "X1", "Regular", "03-08-2026", 1000, 180, 0, 0, "Yes"],
            [A, "Alpha", "X2", "Regular", "05-08-2026", 2000, 360, 0, 0, "Yes"],
        ])
        write_csv(books, BOOKS_HEADER, [
            ["04-08-2026", "Alpha", A, PURCHASE, "V1", "X1", "03-08-2026",
                1000, 180, 0, 0],
            ["10-08-2026", "Beta", B, PURCHASE, "V2", "Y1", "09-08-2026",
                3000, 540, 0, 0],
            ["12-08-2026", "Alpha", A, "Debit Note", "DN/1", "", "", -500, -90, 0, 0],
        ])
        return two_b, books

    def september(self):
        two_b, books = self.path("GSTR2B_19AAAAA1111A1Z1_092026.csv"), self.path("books_sep.csv")
        write_csv(two_b, PORTAL_HEADER, [
            [B, "Beta", "Y1", "Regular", "09-08-2026", 3000, 540, 0, 0, "Yes"],
            [A, "Alpha", "CN-9", "Credit Note", "02-09-2026", 500, 90, 0, 0, "Yes"],
            [A, "Alpha", "Z1", "Regular", "06-09-2026", 4000, 720, 0, 0, "Yes"],
        ])
        write_csv(books, BOOKS_HEADER, [
            ["03-09-2026", "Alpha", A, PURCHASE, "V3", "X2", "05-08-2026",
                2000, 360, 0, 0],
            ["07-09-2026", "Alpha", A, PURCHASE, "V4", "Z1", "06-09-2026",
                4000, 720, 0, 0],
        ])
        return two_b, books

    def test_one_month_end_to_end(self):
        two_b, books = self.august()
        code, out, err = run_cli("--gstr2b", two_b, "--books", books, "--outdir", self.path("aug"),
                                 "--no-timestamp", "--client", "Alpha Traders")
        self.assertEqual((code, err), (0, ""))
        for name in (wp.workpaper_filename(Period(2026, 8)), CARRY_BOOKS_FILE, CARRY_2B_FILE,
                     "summary.txt"):
            self.assertTrue(os.path.exists(self.path("aug", name)), name)
        self.assertIn("August 2026", out)
        self.assertIn("19AAAAA1111A1Z1", out)          # read from the portal file name
        self.assertIn("written:", out)
        with open(self.path("aug", CARRY_2B_FILE), newline="", encoding="utf-8") as handle:
            pending = list(csv.DictReader(handle))
        self.assertEqual([r["Invoice No"] for r in pending], ["X2"])
        with open(self.path("aug", CARRY_BOOKS_FILE), newline="", encoding="utf-8") as handle:
            deferred = list(csv.DictReader(handle))
        self.assertEqual(sorted(r["Invoice No"] or r["Voucher No"] for r in deferred),
                         ["DN/1", "Y1"])
        self.assertEqual([r["Taken In 3B"] for r in deferred if r["Voucher No"] == "DN/1"],
                         ["Yes"])

    def test_two_months_chained_through_the_output_folder(self):
        two_b, books = self.august()
        self.assertEqual(run_cli("--gstr2b", two_b, "--books", books, "--outdir",
                                 self.path("aug"), "--no-timestamp", "--quiet")[0], 0)
        two_b, books = self.september()
        code, out, err = run_cli("--gstr2b", two_b, "--books", books, "--opening-dir",
                                 self.path("aug"), "--outdir", self.path("sep"), "--no-timestamp")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("1,620.00", out)                  # 4(A)(5) = 540 + 720 + 360
        self.assertIn("Carried forward: 0 books deferred, 0 2B pending", out)
        sheets = dict(read_xlsx_sheets(self.path("sep", wp.workpaper_filename(SEP))))
        summary = sheets[wp.SHEET_SUMMARY]
        diff = [r for r in summary if r and r[0] == "Difference (must be nil)"][0]
        self.assertEqual([number(c) for c in diff[1:6]], [0, 0, 0, 0, 0])
        detail = sheets[wp.SHEET_2B]
        header = detail[wp.HEAD_ROW]
        tags = {r[header.index("Document no.")]: r[header.index("TREATMENT")]
                for r in detail[wp.FIRST_DATA_ROW:] if r and r[0]}
        self.assertEqual(tags, {"Y1": "CLAIM_PM_BOOKED", "CN-9": "CN_ALREADY_TAKEN", "Z1": "CLAIM"})
        for name in (CARRY_BOOKS_FILE, CARRY_2B_FILE):
            with open(self.path("sep", name), encoding="utf-8") as handle:
                self.assertEqual(len(handle.readlines()), 1)      # header only: nothing open

    def test_explicit_opening_files_and_a_sheet_selector(self):
        two_b, books = self.september()
        two_b_pending = self.path("pending.csv")
        write_csv(two_b_pending, ["GSTIN", "Invoice No", "Invoice Date", "Taxable Value", "IGST",
                                  "Origin Period"],
                  [[A, "X2", "05-08-2026", 2000, 360, "Aug-26"]])
        code, out, _err = run_cli("--gstr2b", two_b, "--books", books, "--opening-2b-pending",
                                  two_b_pending, "--outdir", self.path("sep"), "--no-timestamp",
                                  "--quiet")
        self.assertEqual(code, 0)
        self.assertIn("written:", out)
        sheets = dict(read_xlsx_sheets(self.path("sep", wp.workpaper_filename(SEP))))
        books_only = sheets[wp.SHEET_BOOKS]
        header = books_only[wp.HEAD_ROW]
        tags = {r[header.index("Voucher / doc no.")]: r[header.index("TREATMENT")]
                for r in books_only[wp.FIRST_DATA_ROW:] if r and r[0]}
        self.assertEqual(tags.get("V3"), "RECLAIM_4D1")

    def test_period_is_required_when_the_file_name_does_not_carry_it(self):
        two_b, books = self.august()
        renamed = self.path("download.csv")
        shutil.copy(two_b, renamed)
        code, _out, err = run_cli("--gstr2b", renamed, "--books", books)
        self.assertEqual(code, 2)
        self.assertIn("--period", err)

    def portal_download(self, name, tax_period="November"):
        """A workbook laid out like the portal's: a 'Read me' sheet and a stacked-heading B2B."""
        book = Workbook()
        readme = book.add_sheet("Read me")
        for row, cells in enumerate([
            ["Goods and Services Tax  - GSTR-2B"], [], [],
            ["Financial Year", "", "2025-26"], ["Tax Period", "", tax_period],
            ["GSTIN", "", A], ["Legal Name", "", "Alpha Traders"],
            ["Trade Name (if any)", "", "Alpha Traders"], ["Date of generation", "", "14/12/2025"],
        ]):
            for column, value in enumerate(cells):
                if value:
                    readme.write(row, column, value)
        b2b = book.add_sheet("B2B")
        top = ["GSTIN of supplier", "Trade/Legal name", "Invoice Details", "", "", "",
               "Taxable Value (₹)", "Tax Amount", "", "", "", "ITC Availability"]
        sub = ["", "", "Invoice number", "Invoice type", "Invoice Date", "Invoice Value(₹)", "",
               "Integrated Tax(₹)", "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)", ""]
        data = [A, "Alpha", "X1", "B2B", "03/11/2025", 1180, 1000, 180, 0, 0, 0, "Yes"]
        for row, cells in enumerate([["Taxable inward supplies"], top, sub, data], start=3):
            for column, value in enumerate(cells):
                if value != "":
                    b2b.write(row, column, value)
        path = self.path(name)
        book.save(path)
        return path

    def november_books(self):
        books = self.path("books_nov.csv")
        write_csv(books, BOOKS_HEADER, [
            ["04-11-2025", "Alpha", A, PURCHASE, "V1", "X1", "03-11-2025", 1000, 180, 0, 0],
        ])
        return books

    def test_month_gstin_and_client_come_from_the_portal_header(self):
        two_b = self.portal_download("download.xlsx")        # a name that carries no hints
        code, out, err = run_cli("--gstr2b", two_b, "--books", self.november_books(),
                                 "--outdir", self.path("nov"), "--no-timestamp")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("November 2025", out)
        self.assertIn(A, out)
        self.assertTrue(os.path.exists(self.path("nov", wp.workpaper_filename(Period(2025, 11)))))
        sheets = dict(read_xlsx_sheets(self.path("nov", wp.workpaper_filename(Period(2025, 11)))))
        banner = " ".join(" ".join(r) for r in sheets[wp.SHEET_SUMMARY][:4] if r)
        self.assertIn("Alpha Traders", banner)

    def test_a_file_that_contradicts_its_own_header_is_flagged_in_the_notes(self):
        two_b = self.portal_download("download.xlsx", tax_period="October")
        code, _out, err = run_cli("--gstr2b", two_b, "--books", self.november_books(),
                                  "--period", "2025-11", "--gstin", "27BBBBB2222B1Z2",
                                  "--outdir", self.path("nov"), "--no-timestamp", "--quiet")
        self.assertEqual((code, err), (0, ""))
        sheets = dict(read_xlsx_sheets(self.path("nov", wp.workpaper_filename(Period(2025, 11)))))
        notes = "\n".join(" ".join(r) for r in sheets[wp.SHEET_NOTES] if r)
        self.assertIn("the file's own header says October 2025", notes)
        self.assertIn(f"names GSTIN {A}", notes)

    def test_period_flag_overrides_the_file_name(self):
        two_b, books = self.august()
        code, out, _err = run_cli("--gstr2b", two_b, "--books", books, "--period", "Aug-26",
                                  "--outdir", self.path("o"), "--no-timestamp", "--quiet")
        self.assertEqual(code, 0)
        self.assertIn(wp.workpaper_filename(Period(2026, 8)), out)

    def test_errors_are_reported_without_a_traceback(self):
        two_b, books = self.august()
        cases = [
            (["--gstr2b", two_b, "--books", books, "--period", "soon"], "cannot read"),
            (["--gstr2b", self.path("nope.csv"), "--books", books, "--period", "2026-08"],
             "file not found"),
            (["--gstr2b", two_b, "--books", books, "--opening-dir", self.path("empty")],
             "not found"),
            (["--gstr2b", two_b, "--books", self.path("nope.csv")], "file not found"),
        ]
        os.makedirs(self.path("empty"))
        for argv, fragment in cases:
            code, _out, err = run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(fragment, err)
            self.assertNotIn("Traceback", err)

    def test_tolerance_and_policy_flags_are_applied(self):
        two_b, books = self.august()
        code, out, _err = run_cli("--gstr2b", two_b, "--books", books, "--outdir", self.path("o"),
                                  "--cn-policy", "2b", "--value-tolerance", "5",
                                  "--date-tolerance", "1", "--no-timestamp")
        self.assertEqual(code, 0)
        sheet = dict(read_xlsx_sheets(self.path("o", wp.workpaper_filename(Period(2026, 8)))))
        info = sheet[wp.SHEET_SUMMARY][2][0]
        self.assertIn("+/-1 day(s)", info)
        self.assertIn("Rs.5 taxable", info)
        self.assertIn("only once", info)
        self.assertIn("written:", out)


@unittest.skipUnless(shutil.which("soffice"), "LibreOffice is not installed")
class RecalculationTests(unittest.TestCase):
    """Let a real spreadsheet engine recompute every formula and compare with what was cached."""

    def test_every_formula_recomputes_to_its_cached_value(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "wp.xlsx")
            wp.build_workbook(demo_work()).save(source)
            stripped = os.path.join(folder, "stripped.xlsx")
            pattern = re.compile(r'(<c [^>]*?)( t="(?:str|b|e)")?(>)(<f>.*?</f>)<v>.*?</v>(</c>)',
                                 re.S)
            with zipfile.ZipFile(source) as zin, zipfile.ZipFile(stripped, "w") as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename.startswith("xl/worksheets/sheet"):
                        text = pattern.sub(lambda m: m.group(1) + m.group(3) + m.group(4)
                                           + m.group(5), data.decode("utf-8"))
                        data = text.encode("utf-8")
                    zout.writestr(item, data)
            out = os.path.join(folder, "out")
            subprocess.run(["soffice", "--headless", "--calc", "--convert-to",
                            "xlsx:Calc MS Excel 2007 XML", "--outdir", out, stripped],
                           capture_output=True, timeout=240, check=True)
            cached = dict(read_xlsx_sheets(source))
            fresh = dict(read_xlsx_sheets(os.path.join(out, "stripped.xlsx")))
            compared = 0
            for name, rows in cached.items():
                for r, row in enumerate(rows):
                    for c, value in enumerate(row):
                        other = fresh[name][r][c] if r < len(fresh[name]) and c < len(
                            fresh[name][r]) else ""
                        try:
                            same = abs(float(value) - float(other)) < 0.006
                        except ValueError:
                            same = value == other
                        self.assertTrue(same, f"{name} row {r + 1} col {c + 1}: "
                                              f"{value!r} vs {other!r}")
                        compared += 1
            self.assertGreater(compared, 500)


if __name__ == "__main__":
    unittest.main()

