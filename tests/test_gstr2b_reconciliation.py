"""Unit tests for gstr2b_reconciliation_hack."""

import io
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from unittest import mock

import gstr2b_reconciliation_hack as reco
from gstr2b_reconciliation_hack import (
    FLAG_INVOICE_DATE_DIFF,
    FLAG_INVOICE_NO_FORMAT_DIFF,
    FLAG_INVOICE_NO_MISMATCH,
    FLAG_ITC_NOT_AVAILABLE,
    FLAG_NO_DOC_REFERENCE,
    FLAG_RATE_DIFFERENCE,
    FLAG_TAX_AMOUNT_DIFF,
    FLAG_TAX_HEAD_DIFF,
    FLAG_TAXABLE_VALUE_DIFF,
    REGISTER_FILENAME,
    SOURCE_BOOKS,
    SOURCE_GSTR2B,
    SUMMARY_FILENAME,
    DocumentType,
    GSTR2BReconciler,
    InvoiceRecord,
    MatchBasis,
    ReconStatus,
    aggregate_records,
    clean_doc_number,
    demo_data,
    detect_header_row,
    find_duplicate_documents,
    invoice_trailing_number,
    load_records,
    looks_like_gstin,
    main,
    map_columns,
    normalise_gstin,
    normalise_invoice_no,
    parse_amount,
    parse_date,
    parse_document_type,
    parse_itc_available,
    parse_yes_no,
    read_sheets,
    reconcile_files,
    record_from_row,
    split_file_spec,
    split_reverse_charge,
)

GSTIN_A = "27AAFCD1234A1Z5"
GSTIN_B = "29XYZAB5678C1Z2"


def make_record(source, row_id, gstin, invoice_no, day=1, taxable=1000.0, **kwargs):
    """Build a record with sensible defaults for the matching tests."""
    return InvoiceRecord(
        source=source,
        row_id=row_id,
        gstin=gstin,
        invoice_no=invoice_no,
        invoice_date=date(2025, 6, day) if day else None,
        taxable_value=taxable,
        **kwargs,
    )


def write_xlsx(path, sheets, drop_empty_rows=False):
    """
    Write the subset of the .xlsx format the reader consumes, using shared
    strings (what Excel and the GST portal actually emit).
    """
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    pkg_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    doc_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    strings = []
    for _name, rows in sheets:
        for row in rows:
            for value in row:
                if isinstance(value, str) and value and value not in strings:
                    strings.append(value)
    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="{main_ns}">{shared}</sst>',
        )
        sheet_tags, rel_tags = [], []
        for index, (name, rows) in enumerate(sheets, start=1):
            sheet_tags.append(f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>')
            rel_tags.append(
                f'<Relationship Id="rId{index}" Target="worksheets/sheet{index}.xml" '
                f'Type="{doc_ns}/worksheet"/>'
            )
            body = []
            for row_number, row in enumerate(rows, start=1):
                if drop_empty_rows and not any(value not in (None, "") for value in row):
                    continue
                cells = []
                for column, value in enumerate(row):
                    if value is None or value == "":
                        continue
                    ref = f"{chr(65 + column)}{row_number}"
                    if isinstance(value, str):
                        cells.append(f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                f'<?xml version="1.0"?><worksheet xmlns="{main_ns}">'
                f'<sheetData>{"".join(body)}</sheetData></worksheet>',
            )
        archive.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0"?><workbook xmlns="{main_ns}" xmlns:r="{doc_ns}">'
            f'<sheets>{"".join(sheet_tags)}</sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{pkg_ns}">'
            f'{"".join(rel_tags)}</Relationships>',
        )


class NormalisationTests(unittest.TestCase):
    def test_gstin_is_upper_cased_and_stripped(self):
        self.assertEqual(normalise_gstin(" 27aafcd1234a1z5 "), GSTIN_A)
        self.assertEqual(normalise_gstin("27-AAFCD/1234 A1Z5"), GSTIN_A)

    def test_invoice_numbers_collapse_to_one_key(self):
        for raw in ("INV/001", "inv-001", "INV 1", "INV#0001"):
            self.assertEqual(normalise_invoice_no(raw), "INV1", raw)

    def test_invoice_key_keeps_internal_numbers_distinct(self):
        self.assertNotEqual(normalise_invoice_no("INV-1"), normalise_invoice_no("INV-2"))
        self.assertEqual(normalise_invoice_no("2526/118"), "2526118")

    def test_trailing_number(self):
        self.assertEqual(invoice_trailing_number("INV/2024-25/007"), "7")
        self.assertEqual(invoice_trailing_number("B-77"), "77")
        self.assertEqual(invoice_trailing_number("NOSUFFIX"), "")

    def test_gstin_shape_separates_suppliers_from_total_rows(self):
        self.assertTrue(looks_like_gstin(GSTIN_A))
        self.assertTrue(looks_like_gstin("27aafcd1234a1z5"))
        self.assertFalse(looks_like_gstin("Total"))
        self.assertFalse(looks_like_gstin(""))
        self.assertFalse(looks_like_gstin("27AAFCD1234A1Z"))


class ParsingTests(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount("1,23,456.78"), 123456.78)
        self.assertEqual(parse_amount("Rs. 1,000"), 1000.0)
        self.assertEqual(parse_amount("(500.50)"), -500.50)
        self.assertEqual(parse_amount("-250"), -250.0)
        self.assertEqual(parse_amount(1500), 1500.0)

    def test_blank_and_unreadable_amounts_are_zero(self):
        for value in ("", "   ", "-", "NIL", "N/A", None, "abc"):
            self.assertEqual(parse_amount(value), 0.0, value)

    def test_dates_are_day_first(self):
        self.assertEqual(parse_date("01-06-2025"), date(2025, 6, 1))
        self.assertEqual(parse_date("01/06/2025"), date(2025, 6, 1))
        self.assertEqual(parse_date("1-Jun-2025"), date(2025, 6, 1))
        self.assertEqual(parse_date("2025-06-01"), date(2025, 6, 1))
        self.assertEqual(parse_date("2025-06-01T00:00:00"), date(2025, 6, 1))

    def test_excel_serial_dates(self):
        self.assertEqual(parse_date("45809"), date(2025, 6, 1))
        self.assertEqual(parse_date(45809), date(2025, 6, 1))

    def test_unreadable_dates_are_none(self):
        for value in ("", "   ", "not a date", None):
            self.assertIsNone(parse_date(value))

    def test_document_types(self):
        self.assertIs(parse_document_type(""), DocumentType.INVOICE)
        self.assertIs(parse_document_type("R"), DocumentType.INVOICE)
        self.assertIs(parse_document_type("C"), DocumentType.CREDIT_NOTE)
        self.assertIs(parse_document_type("Credit Note"), DocumentType.CREDIT_NOTE)
        self.assertIs(parse_document_type("Debit Note"), DocumentType.DEBIT_NOTE)
        self.assertIs(parse_document_type("DE"), DocumentType.INVOICE)

    def test_itc_availability(self):
        self.assertTrue(parse_itc_available(""))
        self.assertTrue(parse_itc_available("Yes"))
        self.assertFalse(parse_itc_available("No"))
        self.assertFalse(parse_itc_available("Not Available"))


class RecordTests(unittest.TestCase):
    def test_total_tax_and_document_value(self):
        record = make_record(SOURCE_BOOKS, 1, GSTIN_A, "INV-1", taxable=1000, cgst=90, sgst=90)
        self.assertEqual(record.total_tax, 180)
        self.assertEqual(record.document_value, 1180)

    def test_credit_notes_are_signed_negative_either_way(self):
        positive = make_record(
            SOURCE_BOOKS, 1, GSTIN_A, "CRN-1", taxable=1000, igst=180,
            document_type=DocumentType.CREDIT_NOTE,
        )
        negative = make_record(
            SOURCE_GSTR2B, 1, GSTIN_A, "CRN-1", taxable=-1000, igst=-180,
            document_type=DocumentType.CREDIT_NOTE,
        )
        self.assertEqual(positive.taxable_value, -1000)
        self.assertEqual(positive.total_tax, -180)
        self.assertEqual(negative.taxable_value, -1000)
        self.assertEqual(negative.total_tax, -180)

    def test_zero_amounts_do_not_become_negative_zero(self):
        record = make_record(
            SOURCE_BOOKS, 1, GSTIN_A, "CRN-1", taxable=1000, igst=180,
            document_type=DocumentType.CREDIT_NOTE,
        )
        self.assertEqual(f"{record.cgst:.2f}", "0.00")


class AggregationTests(unittest.TestCase):
    def test_multi_line_document_is_summed(self):
        rows = [
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV/001", taxable=60000, cgst=5400),
            make_record(SOURCE_BOOKS, 3, GSTIN_A, "INV/001", taxable=40000, cgst=3600),
        ]
        merged = aggregate_records(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].taxable_value, 100000)
        self.assertEqual(merged[0].cgst, 9000)
        self.assertEqual(merged[0].line_count, 2)
        self.assertEqual(merged[0].source_rows, [2, 3])

    def test_aggregation_does_not_mutate_the_inputs(self):
        rows = [
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV/001", taxable=60000),
            make_record(SOURCE_BOOKS, 3, GSTIN_A, "INV/001", taxable=40000),
        ]
        aggregate_records(rows)
        self.assertEqual(rows[0].taxable_value, 60000)
        self.assertEqual(rows[0].source_rows, [2])

    def test_different_suppliers_stay_separate(self):
        rows = [
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1"),
            make_record(SOURCE_BOOKS, 3, GSTIN_B, "INV-1"),
        ]
        self.assertEqual(len(aggregate_records(rows)), 2)

    def test_duplicate_documents_are_reported(self):
        rows = aggregate_records([
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", day=1),
            make_record(SOURCE_BOOKS, 3, GSTIN_A, "INV-1", day=9),
        ])
        warnings = find_duplicate_documents(rows)
        self.assertEqual(len(warnings), 1)
        self.assertIn("appears 2 times", warnings[0])


class MatchingTests(unittest.TestCase):
    def reconcile(self, portal, books, **kwargs):
        return GSTR2BReconciler(**kwargs).reconcile(portal, books)

    def test_exact_invoice_number(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", igst=180)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", igst=180)],
        )
        self.assertEqual(len(result.matched), 1)
        pair = result.matched[0]
        self.assertIs(pair.basis, MatchBasis.EXACT_INVOICE_NO)
        self.assertIs(pair.status, ReconStatus.MATCHED)
        self.assertEqual(pair.flags, [])

    def test_invoice_number_format_difference_still_matches(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", igst=180)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV/001", igst=180)],
        )
        pair = result.matched[0]
        self.assertIs(pair.basis, MatchBasis.NORMALISED_INVOICE_NO)
        self.assertIs(pair.status, ReconStatus.MATCHED)
        self.assertEqual(pair.flags, [FLAG_INVOICE_NO_FORMAT_DIFF])

    def test_trailing_number_match_needs_date_and_value_agreement(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "2025-26/114", taxable=5000)]
        close = [make_record(SOURCE_BOOKS, 2, GSTIN_A, "114", day=2, taxable=5000)]
        result = self.reconcile(portal, close)
        self.assertIs(result.matched[0].basis, MatchBasis.INVOICE_TRAILING_NUMBER)

        far = [make_record(SOURCE_BOOKS, 2, GSTIN_A, "114", day=25, taxable=5000)]
        result = self.reconcile(portal, far)
        self.assertEqual(result.matched, [])
        self.assertEqual(len(result.only_in_books), 1)

    def test_value_and_date_match_when_invoice_numbers_are_unusable(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "ABC-1", taxable=5000, igst=900)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "XYZ-9", taxable=5000, igst=900)],
        )
        pair = result.matched[0]
        self.assertIs(pair.basis, MatchBasis.TAXABLE_VALUE_AND_DATE)
        self.assertIs(pair.status, ReconStatus.MATCHED_WITH_DIFFERENCE)
        self.assertIn(FLAG_INVOICE_NO_MISMATCH, pair.flags)

    def test_date_inside_tolerance_is_a_clean_match(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", day=12)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", day=10)],
        )
        self.assertIs(result.matched[0].status, ReconStatus.MATCHED)
        self.assertEqual(result.matched[0].date_diff_days, 2)

    def test_date_outside_tolerance_is_a_difference(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", day=20)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", day=10)],
        )
        pair = result.matched[0]
        self.assertIs(pair.status, ReconStatus.MATCHED_WITH_DIFFERENCE)
        self.assertIn(FLAG_INVOICE_DATE_DIFF, pair.flags)

    def test_value_difference_inside_and_outside_tolerance(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", taxable=50000)]
        inside = [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", taxable=50050)]
        self.assertIs(self.reconcile(portal, inside).matched[0].status, ReconStatus.MATCHED)

        outside = [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", taxable=49000)]
        pair = self.reconcile(portal, outside).matched[0]
        self.assertIs(pair.status, ReconStatus.MATCHED_WITH_DIFFERENCE)
        self.assertIn(FLAG_TAXABLE_VALUE_DIFF, pair.flags)
        self.assertEqual(pair.taxable_diff, -1000)

    def test_tax_head_difference_is_flagged_when_the_total_agrees(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", taxable=50000, cgst=4500,
                         sgst=4500)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", taxable=50000, igst=9000)],
        )
        pair = result.matched[0]
        self.assertIs(pair.status, ReconStatus.MATCHED_WITH_DIFFERENCE)
        self.assertIn(FLAG_TAX_HEAD_DIFF, pair.flags)
        self.assertEqual(pair.tax_diff, 0)

    def test_itc_not_available_is_flagged(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", igst=900, itc_available=False)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", igst=900)],
        )
        pair = result.matched[0]
        self.assertIn(FLAG_ITC_NOT_AVAILABLE, pair.flags)
        self.assertEqual(pair.itc_claimable, 0)
        self.assertEqual(pair.itc_at_risk, 900)

    def test_unmatched_documents_are_split_by_side(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "ONLY-2B", taxable=1000, igst=180)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_B, "ONLY-BK", taxable=2000, igst=360)],
        )
        self.assertEqual(result.matched, [])
        self.assertEqual([r.invoice_no for r in result.only_in_gstr2b], ["ONLY-2B"])
        self.assertEqual([r.invoice_no for r in result.only_in_books], ["ONLY-BK"])

    def test_matching_is_one_to_one(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", day=1)]
        books = [
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", day=1),
            make_record(SOURCE_BOOKS, 3, GSTIN_A, "INV-1", day=9),
        ]
        result = GSTR2BReconciler().reconcile(portal, books)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(len(result.only_in_books), 1)

    def test_rows_without_a_gstin_are_never_matched(self):
        result = self.reconcile(
            [make_record(SOURCE_GSTR2B, 7, "", "INV-1", igst=180)],
            [make_record(SOURCE_BOOKS, 2, "", "INV-1", igst=180)],
        )
        self.assertEqual(result.matched, [])
        self.assertEqual(len(result.only_in_gstr2b), 1)
        self.assertEqual(len(result.only_in_books), 1)

    def test_negative_tolerances_are_rejected(self):
        with self.assertRaises(ValueError):
            GSTR2BReconciler(date_tolerance_days=-1)
        with self.assertRaises(ValueError):
            GSTR2BReconciler(taxable_tolerance=-1)
        with self.assertRaises(ValueError):
            GSTR2BReconciler(tax_tolerance=-1)


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_give_identical_output(self):
        portal, books = demo_data()
        first = GSTR2BReconciler().reconcile(portal, books).to_rows()
        second = GSTR2BReconciler().reconcile(*demo_data()).to_rows()
        self.assertEqual(first, second)

    def test_input_order_does_not_change_the_result(self):
        portal, books = demo_data()
        straight = GSTR2BReconciler().reconcile(portal, books).to_rows()
        reversed_rows = GSTR2BReconciler().reconcile(
            list(reversed(portal)), list(reversed(books))
        ).to_rows()
        def key(row):
            return (row["status"], row["gstin"], row["gstr2b_invoice_no"],
                    row["books_invoice_no"], row["itc_claimable"])

        self.assertEqual(sorted(map(key, straight)), sorted(map(key, reversed_rows)))


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.result = GSTR2BReconciler().reconcile(*demo_data())

    def test_demo_counts(self):
        counts = self.result.summary()["counts"]
        self.assertEqual(counts["matched"], 3)
        self.assertEqual(counts["matched_with_difference"], 2)
        self.assertEqual(counts["only_in_gstr2b"], 1)
        self.assertEqual(counts["only_in_books"], 1)

    def test_demo_itc_position(self):
        itc = self.result.itc_position()
        self.assertEqual(itc["claimable_on_matched"], 30420.0)
        self.assertEqual(itc["in_gstr2b_not_in_books"], 5400.0)
        self.assertEqual(itc["booked_but_not_in_gstr2b"], 7200.0)
        self.assertEqual(itc["ineligible_in_gstr2b"], 14400.0)
        self.assertEqual(itc["at_risk"], 21600.0)

    def test_value_totals_net_off_credit_notes(self):
        summary = self.result.summary()
        self.assertEqual(summary["gstr2b"]["taxable_value"], 280000.0)
        self.assertEqual(summary["books"]["taxable_value"], 289000.0)
        self.assertEqual(summary["differences"]["taxable_value"], 9000.0)

    def test_register_has_one_row_per_document(self):
        rows = self.result.to_rows()
        self.assertEqual(len(rows), 7)
        self.assertEqual(
            sorted({row["status"] for row in rows}),
            ["MATCHED", "MATCHED_WITH_DIFFERENCE", "ONLY_IN_BOOKS", "ONLY_IN_GSTR2B"],
        )

    def test_report_covers_every_section(self):
        report = self.result.generate_report()
        for heading in (
            "1. DOCUMENTS READ",
            "2. MATCH SUMMARY",
            "3. VALUE RECONCILIATION",
            "4. INPUT TAX CREDIT POSITION",
            "5. MATCHED WITH DIFFERENCE",
            "6. IN GSTR-2B, NOT IN BOOKS",
            "7. IN BOOKS, NOT IN GSTR-2B",
            "9. ACTION ITEMS",
        ):
            self.assertIn(heading, report)

    def test_report_rejects_a_zero_row_limit(self):
        with self.assertRaises(ValueError):
            self.result.generate_report(max_rows=0)

    def test_action_items_mention_both_follow_ups(self):
        items = " ".join(self.result.action_items())
        self.assertIn("Chase", items)
        self.assertIn("Book", items)

    def test_clean_reconciliation_has_no_action_items(self):
        record = make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", igst=180)
        book = make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", igst=180)
        result = GSTR2BReconciler().reconcile([record], [book])
        self.assertEqual(result.action_items(), ["No action required - both sides agree "
                                                 "within the tolerances."])


class ColumnMappingTests(unittest.TestCase):
    def test_portal_headers(self):
        mapping = map_columns([
            "GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice type",
            "Invoice Date", "Invoice Value(Rs)", "Place of supply", "Rate(%)",
            "Taxable Value (Rs)", "Integrated Tax(Rs)", "Central Tax(Rs)",
            "State/UT Tax(Rs)", "Cess(Rs)", "ITC Availability", "IRN date",
        ])
        self.assertEqual(mapping["gstin"], 0)
        self.assertEqual(mapping["supplier_name"], 1)
        self.assertEqual(mapping["invoice_no"], 2)
        self.assertEqual(mapping["document_type"], 3)
        self.assertEqual(mapping["invoice_date"], 4)
        self.assertEqual(mapping["taxable_value"], 8)
        self.assertEqual(mapping["igst"], 9)
        self.assertEqual(mapping["cgst"], 10)
        self.assertEqual(mapping["sgst"], 11)
        self.assertEqual(mapping["cess"], 12)
        self.assertEqual(mapping["itc_available"], 13)

    def test_tally_headers(self):
        mapping = map_columns([
            "Date", "Particulars", "Voucher Type", "Voucher No.", "GSTIN/UIN",
            "Taxable Value", "Integrated Tax Amount", "Central Tax Amount",
            "State Tax Amount", "Cess",
        ])
        self.assertEqual(mapping["invoice_date"], 0)
        self.assertEqual(mapping["supplier_name"], 1)
        self.assertEqual(mapping["voucher_type"], 2)
        self.assertNotIn("document_type", mapping)
        self.assertEqual(mapping["invoice_no"], 3)
        self.assertEqual(mapping["gstin"], 4)
        self.assertEqual(mapping["taxable_value"], 5)

    def test_header_row_is_found_below_a_banner(self):
        rows = [
            ["Goods and Services Tax - GSTR-2B"],
            ["B2B - Invoices"],
            [],
            ["GSTIN of supplier", "Invoice number", "Invoice Date", "Taxable Value (Rs)"],
            [GSTIN_A, "INV-1", "01-06-2025", "1000"],
        ]
        index, mapping = detect_header_row(rows)
        self.assertEqual(index, 3)
        self.assertEqual(mapping["invoice_no"], 1)

    def test_missing_required_columns_is_an_error(self):
        with self.assertRaises(ValueError):
            detect_header_row([["Date", "Amount"], ["01-06-2025", "1000"]])


class FileLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def path(self, name):
        return os.path.join(self.temp.name, name)

    def write_csv(self, name, text):
        target = self.path(name)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        return target

    BOOKS_CSV = (
        "Purchase Register (GST) - 1-Jun-2025 to 30-Jun-2025\n"
        "Dharam Enterprises\n"
        "\n"
        "Date,Particulars,Voucher Type,Voucher No.,GSTIN/UIN,Taxable Value,"
        "Integrated Tax Amount,Central Tax Amount,State Tax Amount,Cess\n"
        "01-06-2025,Dharam Traders,Purchase,INV/001,27AAFCD1234A1Z5,\"60,000.00\",0,"
        "\"5,400.00\",\"5,400.00\",0\n"
        "01-06-2025,Dharam Traders,Purchase,INV/001,27AAFCD1234A1Z5,\"40,000.00\",0,"
        "\"3,600.00\",\"3,600.00\",0\n"
        "08-06-2025,Dharam Traders,Credit Note,CRN/1,27AAFCD1234A1Z5,\"5,000.00\",0,"
        "450.00,450.00,0\n"
        ",Grand Total,,,,\"95,000.00\",0,\"8,550.00\",\"8,550.00\",0\n"
    )

    PORTAL_CSV = (
        "GSTIN of supplier,Trade/Legal name,Invoice number,Invoice type,Invoice Date,"
        "Taxable Value (Rs),Integrated Tax(Rs),Central Tax(Rs),State/UT Tax(Rs),"
        "Cess(Rs),ITC Availability\n"
        "27AAFCD1234A1Z5,Dharam Traders,INV-001,R,01-06-2025,100000,0,9000,9000,0,Yes\n"
        "27AAFCD1234A1Z5,Dharam Traders,CRN-1,C,08-06-2025,5000,0,450,450,0,Yes\n"
    )

    def test_csv_with_banner_and_total_rows(self):
        path = self.write_csv("books.csv", self.BOOKS_CSV)
        records, notes = load_records(path, SOURCE_BOOKS)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].taxable_value, 60000.0)
        self.assertEqual(records[0].gstin, GSTIN_A)
        self.assertEqual(records[0].invoice_date, date(2025, 6, 1))
        self.assertIs(records[2].document_type, DocumentType.CREDIT_NOTE)
        self.assertEqual(records[2].taxable_value, -5000.0)
        self.assertTrue(any("total or repeated-header row" in note for note in notes))

    def test_csv_rows_aggregate_into_documents(self):
        books = self.write_csv("books.csv", self.BOOKS_CSV)
        portal = self.write_csv("portal.csv", self.PORTAL_CSV)
        result = reconcile_files(portal, books)
        self.assertEqual(len(result.book_documents), 2)
        self.assertEqual(result.book_documents[0].taxable_value, 100000.0)
        self.assertEqual(len(result.matched), 2)
        self.assertEqual(result.summary()["counts"]["matched"], 2)

    def test_semicolon_delimited_csv(self):
        path = self.write_csv(
            "books.csv",
            "GSTIN;Invoice No;Invoice Date;Taxable Value;IGST\n"
            f"{GSTIN_A};INV-1;01-06-2025;1000;180\n",
        )
        records, _notes = load_records(path, SOURCE_BOOKS)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].igst, 180.0)

    def test_xlsx_with_banner_sheet_serial_dates_and_rate_wise_rows(self):
        path = self.path("gstr2b.xlsx")
        header = [
            "GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice type",
            "Invoice Date", "Taxable Value (Rs)", "Integrated Tax(Rs)", "Central Tax(Rs)",
            "State/UT Tax(Rs)", "Cess(Rs)", "ITC Availability",
        ]
        write_xlsx(path, [
            ("Read me", [["GSTR-2B"], ["This sheet explains the download."]]),
            ("B2B", [
                ["Goods and Services Tax - GSTR-2B"],
                ["B2B - Invoices"],
                [],
                header,
                [GSTIN_A, "Dharam Traders", "INV-001", "R", 45809, 80000, 0, 7200, 7200, 0,
                 "Yes"],
                [GSTIN_A, "Dharam Traders", "INV-001", "R", 45809, 20000, 0, 1200, 1200, 0,
                 "Yes"],
                ["Total", "", "", "", "", 100000, 0, 8400, 8400, 0, ""],
            ]),
        ])
        records, notes = load_records(path, SOURCE_GSTR2B)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].invoice_date, date(2025, 6, 1))
        self.assertEqual(records[0].sheet, "B2B")
        self.assertTrue(any("Read me" in note for note in notes))
        documents = aggregate_records(records)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].taxable_value, 100000.0)
        self.assertEqual(documents[0].total_tax, 16800.0)

    def test_every_usable_sheet_is_read(self):
        path = self.path("gstr2b.xlsx")
        invoice_header = ["GSTIN of supplier", "Invoice number", "Invoice Date",
                          "Taxable Value (Rs)", "Integrated Tax(Rs)"]
        note_header = ["GSTIN of supplier", "Note number", "Note type", "Note date",
                       "Taxable Value (Rs)", "Integrated Tax(Rs)"]
        write_xlsx(path, [
            ("B2B", [invoice_header, [GSTIN_A, "INV-1", 45809, 1000, 180]]),
            ("B2B-CDNR", [note_header, [GSTIN_A, "CRN-1", "C", 45816, 500, 90]]),
        ])
        records, _notes = load_records(path, SOURCE_GSTR2B)
        self.assertEqual([record.sheet for record in records], ["B2B", "B2B-CDNR"])
        self.assertIs(records[1].document_type, DocumentType.CREDIT_NOTE)
        self.assertEqual(records[1].taxable_value, -500.0)

    def test_file_without_reconcilable_columns_is_an_error(self):
        path = self.write_csv("junk.csv", "Date,Amount\n01-06-2025,1000\n")
        with self.assertRaises(ValueError):
            load_records(path, SOURCE_BOOKS)

    def test_missing_file_and_unsupported_type_are_errors(self):
        with self.assertRaises(ValueError):
            load_records(self.path("nope.csv"), SOURCE_BOOKS)
        legacy = self.write_csv("books.xls", "anything")
        with self.assertRaises(ValueError):
            load_records(legacy, SOURCE_BOOKS)


REAL_TALLY_HEADERS = [
    "Date", "Particulars", "GSTIN/UIN", "Vch Type", "Vch No.", "Doc No.", "Doc date",
    "Taxable", "Integrated Tax", "Central Tax", "State Tax", "GSTR 2B Period",
    "Unique GSTR 2B No", "Remarks",
]
REAL_PORTAL_B2B_HEADERS = [
    "GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice type", "Invoice Date",
    "Invoice Value(Rs)", "Place of supply", "Supply Attract Reverse Charge",
    "Taxable Value (Rs)", "Integrated Tax(Rs)", "Central Tax(Rs)", "State/UT Tax(Rs)",
    "Cess(Rs)", "GSTR-1/IFF/GSTR-5 Period", "GSTR-1/IFF/GSTR-5 Filing Date",
    "ITC Availability", "Reason", "Applicable % of Tax Rate", "Source", "IRN", "IRN Date",
]


class RealLayoutMappingTests(unittest.TestCase):
    def test_tally_register_keeps_supplier_and_voucher_numbers_apart(self):
        mapping = map_columns(REAL_TALLY_HEADERS)
        self.assertEqual(mapping["invoice_no"], 5)       # Doc No.
        self.assertEqual(mapping["voucher_no"], 4)       # Vch No.
        self.assertEqual(mapping["invoice_date"], 6)     # Doc date
        self.assertEqual(mapping["booking_date"], 0)     # Date
        self.assertEqual(mapping["voucher_type"], 3)
        self.assertEqual(mapping["gstin"], 2)

    def test_alias_rank_beats_column_position(self):
        mapping = map_columns(["Voucher No.", "Supplier Invoice No", "GSTIN", "Taxable Value"])
        self.assertEqual(mapping["invoice_no"], 1)
        self.assertEqual(mapping["voucher_no"], 0)

    def test_voucher_number_is_the_fallback_document_number(self):
        mapping = map_columns(["Date", "Voucher No.", "GSTIN", "Taxable Value"])
        self.assertEqual(mapping["invoice_no"], 1)
        self.assertEqual(mapping["voucher_no"], 1)

    def test_a_lone_date_column_serves_as_both_dates(self):
        mapping = map_columns(["Date", "Invoice No", "GSTIN", "Taxable Value"])
        self.assertEqual(mapping["invoice_date"], 0)
        self.assertEqual(mapping["booking_date"], 0)

    def test_portal_b2b_extras_are_picked_up_and_lookalikes_ignored(self):
        mapping = map_columns(REAL_PORTAL_B2B_HEADERS)
        self.assertEqual(mapping["rcm"], 7)
        self.assertEqual(mapping["reason"], 16)
        self.assertEqual(mapping["gstr1_period"], 13)
        self.assertEqual(mapping["gstr1_filing_date"], 14)
        self.assertEqual(mapping["invoice_date"], 4)
        mapped = set(mapping.values())
        for lookalike in (17, 19, 20):      # Applicable % of Tax Rate, IRN, IRN Date
            self.assertNotIn(lookalike, mapped)

    def test_amended_sheets_use_the_revised_document(self):
        mapping = map_columns([
            "GSTIN of supplier", "Original invoice number", "Original invoice date",
            "Invoice number", "Invoice Date", "Taxable Value (Rs)",
        ])
        self.assertEqual(mapping["invoice_no"], 3)
        self.assertEqual(mapping["invoice_date"], 4)

    def test_credit_note_sheet_headers(self):
        mapping = map_columns([
            "GSTIN of supplier", "Note number", "Note type", "Note Supply type", "Note date",
            "Taxable Value (Rs)",
        ])
        self.assertEqual(mapping["invoice_no"], 1)
        self.assertEqual(mapping["document_type"], 2)
        self.assertEqual(mapping["invoice_date"], 4)

    def test_the_firms_deferral_month_column_is_recognised(self):
        mapping = map_columns(["GSTIN of supplier", "Invoice number", "Taxable Value (Rs)",
                               "DEFFERRED & INELIGIABLE  MONTH", "UNIQUE GSTR 2B PERIOD",
                               "GSTR 2B PERIOD"])
        self.assertEqual(mapping["origin_period"], 3)
        self.assertEqual(mapping["ref"], 4)
        self.assertEqual(mapping["period_ref"], 5)


class RealLayoutRecordTests(unittest.TestCase):
    def tally_row(self, **cells):
        base = dict.fromkeys(REAL_TALLY_HEADERS, "")
        base.update(cells)
        return [base[name] for name in REAL_TALLY_HEADERS]

    def record(self, row, books=True, headers=REAL_TALLY_HEADERS):
        return record_from_row(row, map_columns(headers), SOURCE_BOOKS, 9, books=books,
                               headers=headers)

    def test_tally_debit_note_is_a_number_less_itc_reduction(self):
        row = self.tally_row(**{
            "Date": "04-05-2026", "Particulars": "Supplier", "GSTIN/UIN": GSTIN_A,
            "Vch Type": "Debit Note", "Vch No.": "DN/GST/003/26-27", "Doc No.": "",
            "Taxable": "-1177", "Integrated Tax": "-58.85",
        })
        record = self.record(row)
        self.assertIs(record.document_type, DocumentType.CREDIT_NOTE)
        self.assertEqual(record.invoice_no, "")
        self.assertEqual(record.voucher_no, "DN/GST/003/26-27")
        self.assertEqual(record.taxable_value, -1177)
        self.assertEqual(record.igst, -58.85)
        self.assertEqual(record.booking_date, date(2026, 5, 4))
        self.assertIsNone(record.invoice_date)

    def test_tally_purchase_keeps_both_dates(self):
        row = self.tally_row(**{
            "Date": "23-07-2026", "GSTIN/UIN": GSTIN_A, "Vch Type": "Registered Purchase",
            "Vch No.": "533/26-27", "Doc No.": "074069601131831", "Doc date": "20-07-2026",
            "Taxable": "50", "Integrated Tax": "9",
        })
        record = self.record(row)
        self.assertIs(record.document_type, DocumentType.INVOICE)
        self.assertEqual(record.invoice_no, "074069601131831")
        self.assertEqual(record.booking_date, date(2026, 7, 23))
        self.assertEqual(record.invoice_date, date(2026, 7, 20))

    def test_reverse_charge_voucher_type_marks_the_row(self):
        row = self.tally_row(**{
            "GSTIN/UIN": GSTIN_A, "Vch Type": "RCM Purchase", "Doc No.": "F-1", "Taxable": "100",
        })
        self.assertTrue(self.record(row).rcm)

    def test_portal_reverse_charge_column(self):
        headers = REAL_PORTAL_B2B_HEADERS
        row = dict.fromkeys(headers, "")
        row.update({"GSTIN of supplier": GSTIN_A, "Invoice number": "A-1",
                    "Supply Attract Reverse Charge": "Yes", "Taxable Value (Rs)": "100"})
        record = record_from_row([row[h] for h in headers], map_columns(headers),
                                 SOURCE_GSTR2B, 7, headers=headers)
        self.assertTrue(record.rcm)

    def test_repeated_header_row_is_not_a_document(self):
        row = self.tally_row(**{"GSTIN/UIN": "GSTIN/UIN", "Doc No.": "Doc No.",
                                "Taxable": "Taxable"})
        self.assertIsNone(self.record(row))

    def test_books_credit_purchase_is_not_a_return(self):
        self.assertIs(parse_document_type("Credit Purchase", books=True), DocumentType.INVOICE)
        self.assertIs(parse_document_type("Registered Purchase", books=True),
                      DocumentType.INVOICE)
        for text in ("Debit Note", "Credit Note", "Purchase Return", "DN", "Vendor Credit Note"):
            self.assertIs(parse_document_type(text, books=True), DocumentType.CREDIT_NOTE, text)

    def test_portal_debit_note_still_raises_credit(self):
        self.assertIs(parse_document_type("Debit Note"), DocumentType.DEBIT_NOTE)
        self.assertIs(parse_document_type("Credit Note"), DocumentType.CREDIT_NOTE)

    def test_numeric_document_numbers_are_restored(self):
        self.assertEqual(clean_doc_number("385.0"), "385")
        self.assertEqual(clean_doc_number("7.4069601131831E+13"), "74069601131831")
        self.assertEqual(clean_doc_number("074069601131831"), "074069601131831")
        self.assertEqual(clean_doc_number(" INV/12 "), "INV/12")
        self.assertEqual(clean_doc_number("12.5"), "12.5")
        self.assertEqual(clean_doc_number(None), "")

    def test_yes_no_cells(self):
        self.assertTrue(parse_yes_no("Yes"))
        self.assertTrue(parse_yes_no("Y"))
        self.assertFalse(parse_yes_no("No"))
        self.assertFalse(parse_yes_no(""))


class NumberLessDocumentTests(unittest.TestCase):
    def test_number_less_documents_from_one_supplier_stay_separate(self):
        rows = [
            InvoiceRecord(source=SOURCE_BOOKS, row_id=2, gstin=GSTIN_A, voucher_no="DN/1",
                          document_type=DocumentType.CREDIT_NOTE, taxable_value=-100, igst=-18),
            InvoiceRecord(source=SOURCE_BOOKS, row_id=3, gstin=GSTIN_A, voucher_no="DN/2",
                          document_type=DocumentType.CREDIT_NOTE, taxable_value=-200, igst=-36),
        ]
        self.assertEqual(len(aggregate_records(rows)), 2)

    def test_two_lines_of_one_voucher_do_merge(self):
        rows = [
            InvoiceRecord(source=SOURCE_BOOKS, row_id=2, gstin=GSTIN_A, voucher_no="DN/1",
                          document_type=DocumentType.CREDIT_NOTE, taxable_value=-100, igst=-18),
            InvoiceRecord(source=SOURCE_BOOKS, row_id=3, gstin=GSTIN_A, voucher_no="DN/1",
                          document_type=DocumentType.CREDIT_NOTE, taxable_value=-200, igst=-36),
        ]
        merged = aggregate_records(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].taxable_value, -300)

    def test_same_invoice_under_two_vouchers_is_a_warning(self):
        rows = aggregate_records([
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", voucher_no="V1"),
            make_record(SOURCE_BOOKS, 3, GSTIN_A, "INV-1", voucher_no="V2"),
        ])
        warnings = find_duplicate_documents(rows)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 vouchers", warnings[0])

    def test_number_less_debit_note_matches_the_suppliers_credit_note_on_value(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "CN-77", day=20, taxable=-1177,
                              igst=-58.85, document_type=DocumentType.CREDIT_NOTE)]
        book = InvoiceRecord(source=SOURCE_BOOKS, row_id=2, gstin=GSTIN_A, voucher_no="DN/GST/3",
                             document_type=DocumentType.CREDIT_NOTE, taxable_value=-1177,
                             igst=-58.85, booking_date=date(2026, 5, 4))
        result = GSTR2BReconciler().reconcile(portal, [book])
        self.assertEqual(len(result.matched), 1)
        pair = result.matched[0]
        self.assertIs(pair.basis, MatchBasis.SUPPLIER_VALUE_ONLY)
        self.assertIs(pair.status, ReconStatus.MATCHED_WITH_DIFFERENCE)
        self.assertIn(FLAG_NO_DOC_REFERENCE, pair.flags)

    def test_value_only_match_needs_the_value_to_agree(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "CN-77", taxable=-1177, igst=-58.85,
                              document_type=DocumentType.CREDIT_NOTE)]
        book = InvoiceRecord(source=SOURCE_BOOKS, row_id=2, gstin=GSTIN_A, voucher_no="DN/1",
                             document_type=DocumentType.CREDIT_NOTE, taxable_value=-5000,
                             igst=-250)
        result = GSTR2BReconciler().reconcile(portal, [book])
        self.assertEqual(result.matched, [])

    def test_an_invoice_and_a_credit_note_with_one_number_never_pair(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "12", taxable=1000, igst=180)]
        book = make_record(SOURCE_BOOKS, 2, GSTIN_A, "12", taxable=1000, igst=180,
                           document_type=DocumentType.CREDIT_NOTE)
        result = GSTR2BReconciler().reconcile(portal, [book])
        self.assertEqual(result.matched, [])

    def test_best_candidate_wins_not_the_first_in_the_file(self):
        portal = [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", day=20)]
        books = [
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", day=1),     # far, but listed first
            make_record(SOURCE_BOOKS, 3, GSTIN_A, "INV-1", day=19),    # near
        ]
        result = GSTR2BReconciler().reconcile(portal, books)
        self.assertEqual(result.matched[0].books.invoice_date, date(2025, 6, 19))
        self.assertEqual(result.only_in_books[0].invoice_date, date(2025, 6, 1))

    def test_rate_difference_is_named(self):
        result = GSTR2BReconciler().reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", taxable=10000, igst=1200)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", taxable=10000, igst=500)],
        )
        pair = result.matched[0]
        self.assertIn(FLAG_RATE_DIFFERENCE, pair.flags)
        self.assertIn(FLAG_TAX_AMOUNT_DIFF, pair.flags)

    def test_a_value_difference_is_not_called_a_rate_difference(self):
        result = GSTR2BReconciler().reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", taxable=10000, igst=1800)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", taxable=9000, igst=1620)],
        )
        pair = result.matched[0]
        self.assertNotIn(FLAG_RATE_DIFFERENCE, pair.flags)
        self.assertIn(FLAG_TAXABLE_VALUE_DIFF, pair.flags)

    def test_a_missing_date_alone_is_not_a_difference(self):
        result = GSTR2BReconciler().reconcile(
            [make_record(SOURCE_GSTR2B, 7, GSTIN_A, "INV-1", igst=180)],
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "INV-1", day=None, igst=180)],
        )
        self.assertIs(result.matched[0].status, ReconStatus.MATCHED)


class SheetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "register.xlsx")
        header = ["GSTIN of supplier", "Invoice number", "Invoice Date", "Taxable Value (Rs)",
                  "Integrated Tax(Rs)"]
        write_xlsx(self.path, [
            ("First", [header, [GSTIN_A, "A-1", 45809, 1000, 180]]),
            ("Second", [header, [GSTIN_A, "B-1", 45809, 2000, 360]]),
        ])

    def test_sheet_selector_reads_one_sheet(self):
        records, _notes = load_records(self.path + "#Second", SOURCE_GSTR2B)
        self.assertEqual([record.invoice_no for record in records], ["B-1"])

    def test_without_a_selector_every_usable_sheet_is_read(self):
        records, _notes = load_records(self.path, SOURCE_GSTR2B)
        self.assertEqual([record.invoice_no for record in records], ["A-1", "B-1"])

    def test_unknown_sheet_lists_the_available_ones(self):
        with self.assertRaises(ValueError) as caught:
            read_sheets(self.path + "#Nope")
        self.assertIn("'First'", str(caught.exception))

    def test_a_real_path_containing_a_hash_is_not_split(self):
        odd = os.path.join(self.temp.name, "reg#1.xlsx")
        os.rename(self.path, odd)
        self.assertEqual(split_file_spec(odd), (odd, ""))

    def test_blank_rows_keep_their_true_excel_row_numbers(self):
        path = os.path.join(self.temp.name, "gappy.xlsx")
        header = ["GSTIN of supplier", "Invoice number", "Invoice Date", "Taxable Value (Rs)"]
        write_xlsx(path, [("B2B", [
            ["Banner"], [], [], header,
            [GSTIN_A, "A-1", 45809, 1000],
            [], [], [],
            [GSTIN_A, "A-2", 45809, 2000],
        ])], drop_empty_rows=True)
        records, _notes = load_records(path, SOURCE_GSTR2B)
        self.assertEqual([(r.invoice_no, r.row_id) for r in records], [("A-1", 5), ("A-2", 9)])
        self.assertEqual([r.serial for r in records], [1, 2])


class ReverseChargeSplitTests(unittest.TestCase):
    def test_reverse_charge_rows_are_reported_not_dropped(self):
        rows = [
            make_record(SOURCE_BOOKS, 2, GSTIN_A, "A-1", igst=180),
            make_record(SOURCE_BOOKS, 3, GSTIN_B, "F-1", igst=50, rcm=True),
        ]
        ordinary, reverse, notes = split_reverse_charge(rows, SOURCE_BOOKS)
        self.assertEqual([r.invoice_no for r in ordinary], ["A-1"])
        self.assertEqual([r.invoice_no for r in reverse], ["F-1"])
        self.assertIn("1 reverse-charge row", notes[0])
        self.assertIn("50.00", notes[0])

    def test_no_note_when_there_is_no_reverse_charge(self):
        _ordinary, _reverse, notes = split_reverse_charge(
            [make_record(SOURCE_BOOKS, 2, GSTIN_A, "A-1")], SOURCE_BOOKS)
        self.assertEqual(notes, [])


class HostileWorkbookTests(unittest.TestCase):
    """A workbook can arrive by e-mail from anyone; a booby-trapped one must be refused."""

    BOMB = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
            b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            b'<si><t>&lol2;</t></si></sst>')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.good = os.path.join(self.temp.name, "good.xlsx")
        header = ["GSTIN of supplier", "Invoice number", "Invoice Date", "Taxable Value (Rs)"]
        write_xlsx(self.good, [("B2B", [header, [GSTIN_A, "A-1", 45809, 1000]])])

    def tampered(self, member, payload):
        path = os.path.join(self.temp.name, "bad.xlsx")
        with zipfile.ZipFile(self.good) as source, zipfile.ZipFile(path, "w") as target:
            for item in source.infolist():
                data = payload if item.filename == member else source.read(item.filename)
                target.writestr(item.filename, data)
        return path

    def test_a_dtd_in_the_shared_strings_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            read_sheets(self.tampered("xl/sharedStrings.xml", self.BOMB))
        self.assertIn("DTD", str(caught.exception))

    def test_a_dtd_in_a_sheet_is_refused(self):
        bomb = self.BOMB.replace(b"<sst", b"<worksheet").replace(b"</sst>", b"</worksheet>")
        with self.assertRaises(ValueError):
            read_sheets(self.tampered("xl/worksheets/sheet1.xml", bomb))

    def test_a_dtd_in_the_workbook_part_is_refused(self):
        bomb = self.BOMB.replace(b"<sst", b"<workbook").replace(b"</sst>", b"</workbook>")
        with self.assertRaises(ValueError):
            read_sheets(self.tampered("xl/workbook.xml", bomb))

    def test_utf16_parts_cannot_smuggle_a_dtd_past_the_check(self):
        payload = self.BOMB.decode("utf-8").replace('<?xml version="1.0"?>', "").encode("utf-16")
        with self.assertRaises(ValueError) as caught:
            read_sheets(self.tampered("xl/sharedStrings.xml", payload))
        self.assertIn("UTF-8", str(caught.exception))
        raw_le = self.BOMB.decode("utf-8").encode("utf-16-le")
        with self.assertRaises(ValueError):
            read_sheets(self.tampered("xl/sharedStrings.xml", raw_le))

    def test_an_oversized_part_is_refused(self):
        with mock.patch.object(reco, "MAX_XLSX_PART_BYTES", 10):
            with self.assertRaises(ValueError) as caught:
                read_sheets(self.good)
        self.assertIn("too large", str(caught.exception))

    def test_a_normal_workbook_is_unaffected(self):
        self.assertEqual(len(read_sheets(self.good)), 1)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.portal = os.path.join(self.temp.name, "portal.csv")
        self.books = os.path.join(self.temp.name, "books.csv")
        with open(self.portal, "w", encoding="utf-8") as handle:
            handle.write(FileLoadingTests.PORTAL_CSV)
        with open(self.books, "w", encoding="utf-8") as handle:
            handle.write(FileLoadingTests.BOOKS_CSV)

    def test_cli_writes_the_register_and_the_summary(self):
        outdir = os.path.join(self.temp.name, "out")
        report = os.path.join(self.temp.name, "reco.txt")
        with redirect_stdout(io.StringIO()):
            exit_code = main([
                "--gstr2b", self.portal, "--books", self.books,
                "--outdir", outdir, "--report", report, "--quiet",
            ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(os.path.join(outdir, REGISTER_FILENAME)))
        self.assertTrue(os.path.exists(os.path.join(outdir, SUMMARY_FILENAME)))
        with open(report, encoding="utf-8") as handle:
            self.assertIn("INPUT TAX CREDIT POSITION", handle.read())

    def test_cli_reports_a_bad_file_without_a_traceback(self):
        missing = os.path.join(self.temp.name, "absent.csv")
        stderr = io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            exit_code = main(["--gstr2b", missing, "--books", self.books, "--quiet"])
        self.assertEqual(exit_code, 2)
        self.assertIn("file not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
