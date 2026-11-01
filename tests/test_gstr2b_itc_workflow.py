"""Unit tests for gstr2b_itc_workflow_hack."""

import os
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import date

import gstr2b_itc_workflow_hack as wf
from gstr2b_itc_workflow_hack import (
    CLAIM,
    CLAIM_PM,
    CLASS_CM,
    CLASS_FUTURE,
    CLASS_OPENING,
    CLASS_PM,
    CN_ALREADY,
    CN_HOLD,
    CN_NOT_OURS,
    CN_REVERSE,
    CN_TAKEN,
    DEFER_BOOKS,
    INELIGIBLE,
    NO_ITC,
    NOT_THIS_PERIOD,
    OPENING_2B_SHEET,
    OPENING_BOOKS_SHEET,
    RECLAIM_4D1,
    REVERSE_4B1,
    REVERSE_4B2,
    ZERO,
    OpeningItems,
    Period,
    Tax,
    build_workpaper,
    itc_time_limit,
    load_opening_items,
    make_open_item,
    origin_of,
    parse_period,
    require_period,
    sum_tax,
    write_carry_forward,
)
from gstr2b_reconciliation_hack import (
    SOURCE_BOOKS,
    SOURCE_GSTR2B,
    DocumentType,
    GSTR2BReconciler,
    InvoiceRecord,
)

SEP = Period(2026, 9)
GSTIN_A = "19AAAAA1111A1Z1"
GSTIN_B = "27BBBBB2222B1Z2"
GSTIN_C = "24CCCCC3333C1Z3"


def mk(source, row, gstin, number, when, taxable, igst=0.0, cgst=0.0, sgst=0.0, **kwargs):
    return InvoiceRecord(
        source=source, row_id=row, serial=row, gstin=gstin, invoice_no=number,
        invoice_date=when, taxable_value=taxable, igst=igst, cgst=cgst, sgst=sgst, **kwargs,
    )


def portal(row, gstin, number, when, taxable, **kwargs):
    return mk(SOURCE_GSTR2B, row, gstin, number, when, taxable, **kwargs)


def books(row, gstin, number, when, taxable, booked=date(2026, 9, 10), **kwargs):
    return mk(SOURCE_BOOKS, row, gstin, number, when, taxable, booking_date=booked, **kwargs)


def treatments(work):
    return {row.doc.invoice_no or row.doc.voucher_no: row.treatment for row in work.rows_2b}


def books_treatments(work):
    return {row.doc.invoice_no or row.doc.voucher_no: row.treatment for row in work.rows_books}


class TaxTests(unittest.TestCase):
    def test_arithmetic(self):
        a, b = Tax(1, 2, 3, 4), Tax(10, 20, 30, 40)
        self.assertEqual(a + b, Tax(11, 22, 33, 44))
        self.assertEqual(b - a, Tax(9, 18, 27, 36))
        self.assertEqual(-a, Tax(-1, -2, -3, -4))
        self.assertEqual(a.total, 10)
        self.assertEqual(a.as_list(), [1, 2, 3, 4])

    def test_rounding_does_not_leave_negative_zero(self):
        rounded = Tax(-0.001, 0.0, 0.0, 0.0).rounded()
        self.assertEqual(repr(rounded.igst), "0.0")

    def test_sum_and_zero(self):
        self.assertEqual(sum_tax([Tax(0.1, 0, 0, 0)] * 3), Tax(0.3, 0, 0, 0))
        self.assertTrue(Tax(0.001, -0.002).is_zero())
        self.assertFalse(Tax(0.5).is_zero())
        self.assertEqual(sum_tax([]), ZERO)


class PeriodTests(unittest.TestCase):
    def test_parsing_the_forms_used_in_practice(self):
        cases = {
            "2026-09": (2026, 9), "09-2026": (2026, 9), "092026": (2026, 9),
            "Sep-26": (2026, 9), "Sept 2026": (2026, 9), "September 2026": (2026, 9),
            "Deffered In MAY-25": (2025, 5), "Def - Books Sept-26 (not in 2B)": (2026, 9),
            "ALL OTHER ITC July -26": (2026, 7), "235 May-26": (2026, 5),
            "19/05/2026": (2026, 5), "2026-05-01 00:00:00": (2026, 5),
        }
        for text, expected in cases.items():
            period = parse_period(text)
            self.assertEqual((period.year, period.month), expected, text)
        self.assertEqual(parse_period(date(2026, 9, 30)), SEP)

    def test_text_without_a_month(self):
        for text in ("", None, "nonsense", "DEFFERRED & INELIGIABLE  MONTH", "13-2026"):
            self.assertIsNone(parse_period(text), text)

    def test_require_period_explains_itself(self):
        self.assertEqual(require_period("2026-09"), SEP)
        with self.assertRaises(ValueError) as caught:
            require_period("soon")
        self.assertIn("2026-09", str(caught.exception))

    def test_period_properties(self):
        self.assertEqual(SEP.label, "Sep-26")
        self.assertEqual(SEP.long_label, "September 2026")
        self.assertEqual(SEP.first_day, date(2026, 9, 1))
        self.assertEqual(SEP.last_day, date(2026, 9, 30))
        self.assertEqual(Period(2028, 2).last_day, date(2028, 2, 29))
        self.assertEqual(Period(2026, 12).last_day, date(2026, 12, 31))
        self.assertEqual(SEP.due_date, date(2026, 10, 20))
        self.assertEqual(Period(2026, 12).due_date, date(2027, 1, 20))
        self.assertEqual(SEP.months_since(Period(2026, 5)), 4)

    def test_financial_year(self):
        self.assertEqual(Period(2026, 4).fy_label, "2026-27")
        self.assertEqual(Period(2027, 3).fy_label, "2026-27")
        self.assertEqual(Period(2026, 1).fy_label, "2025-26")
        self.assertEqual(Period(2026, 1).fy_start, date(2025, 4, 1))
        self.assertEqual(Period(2099, 4).fy_label, "2099-00")

    def test_invalid_periods(self):
        with self.assertRaises(ValueError):
            Period(2026, 13)
        with self.assertRaises(ValueError):
            Period(1999, 1)

    def test_section_16_4_time_limit(self):
        self.assertEqual(itc_time_limit(date(2026, 8, 10)), date(2027, 11, 30))
        self.assertEqual(itc_time_limit(date(2027, 3, 31)), date(2027, 11, 30))
        self.assertEqual(itc_time_limit(date(2026, 4, 1)), date(2027, 11, 30))
        self.assertEqual(itc_time_limit(date(2026, 3, 31)), date(2026, 11, 30))

    def test_origin_of_prefers_the_registers_own_label(self):
        record = mk(SOURCE_BOOKS, 2, GSTIN_A, "X", date(2026, 1, 1), 1, origin_period="Mar-26",
                    booking_date=date(2026, 2, 2))
        self.assertEqual(origin_of(record), Period(2026, 3))
        plain = mk(SOURCE_BOOKS, 2, GSTIN_A, "X", date(2026, 1, 15), 1)
        self.assertEqual(origin_of(plain), Period(2026, 1))
        self.assertIsNone(origin_of(mk(SOURCE_BOOKS, 2, GSTIN_A, "X", None, 1)))
        by_ref = mk(SOURCE_GSTR2B, 2, GSTIN_A, "X", None, 1, ref="235 May-26")
        self.assertEqual(origin_of(by_ref), Period(2026, 5))


class TreatmentTests(unittest.TestCase):
    """One rule at a time, with the numbers worked out by hand."""

    def run_month(self, two_b, book_rows, opening=None, **kwargs):
        return build_workpaper(two_b, book_rows, SEP, opening, **kwargs)

    def test_booked_this_month_and_in_2b_is_claimed(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)],
            [books(2, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)],
        )
        self.assertEqual(treatments(work), {"A-1": CLAIM})
        self.assertEqual(work.rows_2b[0].books_class, CLASS_CM)
        self.assertEqual(work.table4()["net"], Tax(180))

    def test_in_2b_but_not_booked_is_reversed_and_pending(self):
        work = self.run_month([portal(1, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)], [])
        self.assertEqual(treatments(work), {"A-1": REVERSE_4B2})
        table = work.table4()
        self.assertEqual((table["a5"], table["b2"], table["net"]), (Tax(180), Tax(180), ZERO))
        self.assertEqual([i.doc.invoice_no for i in work.closing_pending_2b], ["A-1"])
        self.assertFalse(work.closing_pending_2b[0].carried)

    def test_booked_but_not_in_2b_is_deferred(self):
        work = self.run_month([], [books(2, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)])
        self.assertEqual(books_treatments(work), {"A-1": DEFER_BOOKS})
        self.assertEqual(work.table4()["net"], ZERO)
        self.assertEqual([i.doc.invoice_no for i in work.closing_books_deferred], ["A-1"])
        self.assertEqual(work.books_itc(), Tax(180))
        self.assertEqual(dict(work.bridge())["deferred"], Tax(-180))

    def test_a_deferred_bill_is_claimed_when_it_reaches_2b(self):
        opening = OpeningItems(books_deferred=[_tag(
            books(2, GSTIN_A, "A-1", date(2026, 8, 20), 1000, booked=date(2026, 8, 22), igst=180),
            OPENING_BOOKS_SHEET)])
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 8, 20), 1000, igst=180)], [], opening)
        self.assertEqual(treatments(work), {"A-1": CLAIM_PM})
        self.assertEqual(work.rows_2b[0].books_class, CLASS_OPENING)
        self.assertEqual(work.table4()["net"], Tax(180))
        self.assertEqual(work.closing_books_deferred, [])
        self.assertEqual(dict(work.bridge())["claimed_from_earlier_books"], Tax(180))

    def test_an_earlier_booking_missing_from_the_register_is_flagged(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 8, 20), 1000, igst=180)],
            [books(2, GSTIN_A, "A-1", date(2026, 8, 20), 1000, booked=date(2026, 8, 25),
                   igst=180)],
        )
        row = work.rows_2b[0]
        self.assertEqual((row.treatment, row.books_class), (CLAIM_PM, CLASS_PM))
        self.assertTrue(any("opening deferred register" in note for note in row.notes))
        self.assertTrue(any("not in the opening deferred register" in item
                            for item in work.action_items()))

    def test_a_pending_2b_line_is_reclaimed_when_booked(self):
        opening = OpeningItems(pending_2b=[_tag(
            portal(7, GSTIN_B, "B-9", date(2026, 8, 10), 30000, igst=1500, origin_period="Aug-26"),
            OPENING_2B_SHEET)])
        work = self.run_month(
            [], [books(2, GSTIN_B, "B-9", date(2026, 8, 10), 30000, booked=date(2026, 9, 18),
                       igst=1500)], opening)
        self.assertEqual(books_treatments(work), {"B-9": RECLAIM_4D1})
        table = work.table4()
        self.assertEqual((table["reclaim"], table["d1"], table["a5"]), (Tax(1500),) * 3)
        self.assertEqual(table["net"], Tax(1500))
        self.assertEqual(work.closing_pending_2b, [])
        self.assertEqual(len(work.reclaimed()), 1)

    def test_a_pending_line_not_yet_booked_stays_open_and_ages(self):
        opening = OpeningItems(pending_2b=[_tag(
            portal(7, GSTIN_B, "B-9", date(2026, 5, 10), 30000, igst=1500, origin_period="May-26"),
            OPENING_2B_SHEET)])
        work = self.run_month([], [], opening)
        item = work.closing_pending_2b[0]
        self.assertTrue(item.carried)
        self.assertEqual((item.origin, item.age_months), (Period(2026, 5), 4))
        self.assertEqual(work.ecrrs()["opening"], Tax(1500))
        self.assertEqual(work.ecrrs()["closing"], Tax(1500))

    def test_2b_ineligible_credit_is_kept_out_of_4a5(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180, itc_available=False,
                    reason="POS differs")],
            [books(2, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)],
        )
        row = work.rows_2b[0]
        self.assertEqual(row.treatment, INELIGIBLE)
        self.assertTrue(any("POS differs" in note for note in row.notes))
        table = work.table4()
        self.assertEqual((table["a5"], table["d2"], table["net"]), (ZERO, Tax(180), ZERO))
        self.assertEqual(dict(work.bridge())["matched_not_claimed"], Tax(-180))
        self.assertEqual(work.gstr2b_available(), ZERO)

    def test_booked_after_the_period_is_reversed_and_pending(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 9, 28), 1000, igst=180)],
            [books(2, GSTIN_A, "A-1", date(2026, 9, 28), 1000, booked=date(2026, 10, 3),
                   igst=180)],
        )
        row = work.rows_2b[0]
        self.assertEqual((row.treatment, row.books_class), (REVERSE_4B2, CLASS_FUTURE))
        self.assertEqual(work.books_itc(), ZERO)

    def test_books_only_documents_without_gstin_or_in_the_future(self):
        work = self.run_month([], [
            books(2, "", "U-1", date(2026, 9, 3), 1000, igst=0),
            books(3, GSTIN_A, "F-1", date(2026, 10, 2), 1000, booked=date(2026, 10, 2), igst=180),
        ])
        self.assertEqual(books_treatments(work), {"U-1": NO_ITC, "F-1": NOT_THIS_PERIOD})
        self.assertEqual(work.closing_books_deferred, [])

    def test_a_credit_note_matched_to_a_number_less_return_is_reversed(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "CN-7", date(2026, 9, 12), -1000, igst=-180,
                    document_type=DocumentType.CREDIT_NOTE)],
            [books(2, GSTIN_A, "", None, -1000, igst=-180, voucher_no="DN/1",
                   document_type=DocumentType.CREDIT_NOTE)],
        )
        row = work.rows_2b[0]
        self.assertEqual(row.treatment, CN_REVERSE)
        self.assertEqual(row.pair.basis.value, "SUPPLIER_VALUE_ONLY")
        self.assertEqual(work.table4()["a5"], Tax(-180))
        self.assertEqual(work.table4()["net"], Tax(-180))

    def test_a_credit_note_not_in_the_books_is_reversed_with_a_warning(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "CN-7", date(2026, 9, 12), 1000, igst=180,
                    document_type=DocumentType.CREDIT_NOTE)], [])
        row = work.rows_2b[0]
        self.assertEqual(row.treatment, CN_REVERSE)
        self.assertTrue(any("reject" in note.lower() for note in row.notes))
        self.assertEqual(len(work.credit_notes_not_in_books()), 1)
        self.assertTrue(any("IMS" in item for item in work.action_items()))

    def test_return_policy_books_reverses_before_the_supplier_reports(self):
        book = books(2, GSTIN_A, "", None, -1000, igst=-180, voucher_no="DN/1",
                     document_type=DocumentType.CREDIT_NOTE)
        work = self.run_month([], [book], cn_policy="books")
        self.assertEqual(books_treatments(work), {"DN/1": CN_TAKEN})
        self.assertEqual(work.table4()["a5"], Tax(-180))
        item = work.closing_books_deferred[0]
        self.assertTrue(item.taken_in_3b)

    def test_return_policy_2b_holds_until_the_supplier_reports(self):
        book = books(2, GSTIN_A, "", None, -1000, igst=-180, voucher_no="DN/1",
                     document_type=DocumentType.CREDIT_NOTE)
        work = self.run_month([], [book], cn_policy="2b")
        self.assertEqual(books_treatments(work), {"DN/1": CN_HOLD})
        self.assertEqual(work.table4()["a5"], ZERO)
        self.assertFalse(work.closing_books_deferred[0].taken_in_3b)
        self.assertEqual(dict(work.bridge())["returns_held"], Tax(180))

    def test_a_credit_note_already_taken_is_not_reversed_twice(self):
        opening = OpeningItems(books_deferred=[_tag(
            books(2, GSTIN_A, "", None, -1000, igst=-180, voucher_no="DN/1",
                  booked=date(2026, 8, 4), document_type=DocumentType.CREDIT_NOTE,
                  taken_in_3b=True), OPENING_BOOKS_SHEET)])
        work = self.run_month(
            [portal(1, GSTIN_A, "CN-7", date(2026, 9, 2), -1000, igst=-180,
                    document_type=DocumentType.CREDIT_NOTE)], [], opening)
        self.assertEqual(treatments(work), {"CN-7": CN_ALREADY})
        self.assertEqual(work.table4()["a5"], ZERO)
        self.assertEqual(work.closing_books_deferred, [])

    def test_a_pending_credit_note_is_reversed_when_the_return_is_booked(self):
        opening = OpeningItems(pending_2b=[_tag(
            portal(7, GSTIN_A, "CN-7", date(2026, 8, 12), -1000, igst=-180, origin_period="Aug-26",
                   document_type=DocumentType.CREDIT_NOTE), OPENING_2B_SHEET)])
        work = self.run_month([], [
            books(2, GSTIN_A, "CN-7", date(2026, 8, 12), -1000, igst=-180,
                  document_type=DocumentType.CREDIT_NOTE)], opening)
        self.assertEqual(books_treatments(work), {"CN-7": CN_TAKEN})
        self.assertEqual(work.table4()["a5"], Tax(-180))
        self.assertEqual(work.table4()["d1"], ZERO)         # a reversal is not a reclaim
        self.assertEqual(work.closing_pending_2b, [])

    def test_reverse_charge_rows_stay_out_of_the_match(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "R-1", date(2026, 9, 3), 1000, igst=50, rcm=True)],
            [books(2, GSTIN_A, "R-1", date(2026, 9, 3), 1000, igst=50, rcm=True)],
        )
        self.assertEqual((len(work.rows_2b), len(work.rows_books)), (0, 0))
        self.assertEqual((len(work.rcm_2b), len(work.rcm_books)), (1, 1))
        self.assertTrue(any("reverse-charge" in note for note in work.notes))
        self.assertTrue(any("reverse-charge" in item for item in work.action_items()))

    def test_books_without_voucher_dates_are_all_treated_as_this_month(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)],
            [mk(SOURCE_BOOKS, 2, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)],
        )
        self.assertEqual(treatments(work), {"A-1": CLAIM})
        self.assertTrue(any("no voucher-date column" in note for note in work.notes))

    def test_rate_and_value_differences_surface(self):
        work = self.run_month(
            [portal(1, GSTIN_A, "A-1", date(2026, 9, 3), 10000, igst=1200)],
            [books(2, GSTIN_A, "A-1", date(2026, 9, 3), 10000, igst=500)],
        )
        self.assertEqual(len(work.differences()), 1)
        row = work.rows_2b[0]
        self.assertEqual(row.treatment, CLAIM)
        self.assertEqual(row.difference, Tax(-700))
        self.assertEqual(dict(work.bridge())["matched_difference"], Tax(700))
        self.assertTrue(any("RATE_DIFFERENCE" in note for note in row.notes))

    def test_duplicate_booking_is_reported(self):
        work = self.run_month([], [
            books(2, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180),
            books(3, GSTIN_A, "A-1", date(2026, 9, 9), 1000, igst=180),
        ])
        self.assertTrue(any("appears 2 times" in warning for warning in work.warnings))

    def test_invalid_policy(self):
        with self.assertRaises(ValueError):
            build_workpaper([], [], SEP, cn_policy="whenever")

    def test_references_follow_the_firms_unique_number_convention(self):
        work = self.run_month(
            [portal(5, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)], [])
        self.assertEqual(work.rows_2b[0].ref, "5 Sep-26")


def _tag(record, sheet):
    record.sheet = sheet
    return record


class DemoScenarioTests(unittest.TestCase):
    """The module's own demo, against figures worked out by hand."""

    def setUp(self):
        two_b, book_rows, opening = wf._demo_records()
        self.work = build_workpaper(two_b, book_rows, SEP, opening)

    def test_treatments(self):
        self.assertEqual(treatments(self.work), {
            "A/101": CLAIM, "B-77": CLAIM, "C/9": REVERSE_4B2, "D-12": CLAIM_PM,
            "CN-4": CN_REVERSE,
        })
        self.assertEqual(books_treatments(self.work), {"E-5": DEFER_BOOKS, "F-3": RECLAIM_4D1})

    def test_table_4(self):
        table = self.work.table4()
        self.assertEqual(table["invoices"].total, 10500)
        self.assertEqual(table["credit_2b"].total, -500)
        self.assertEqual(table["reclaim"].total, 1500)
        self.assertEqual(table["a5"].total, 11500)
        self.assertEqual(table["b2"].total, 2000)
        self.assertEqual(table["d1"].total, 1500)
        self.assertEqual(table["net"].total, 9500)

    def test_bridge_from_books_to_the_return(self):
        lines = dict(self.work.bridge())
        self.assertEqual(lines["books"].total, 8900)
        self.assertEqual(lines["claimed_from_earlier_books"].total, 1000)
        self.assertEqual(lines["deferred"].total, -400)
        self.assertEqual(lines["matched_difference"], ZERO)
        self.assertEqual(lines["reconciled"].total, 9500)
        self.assertEqual(lines["claimed"].total, 9500)
        self.assertEqual(lines["difference"], ZERO)

    def test_credit_reversal_statement(self):
        statement = self.work.ecrrs()
        self.assertEqual(statement["opening"].total, 1500)
        self.assertEqual(statement["added"].total, 2000)
        self.assertEqual(statement["reclaimed"].total, 1500)
        self.assertEqual(statement["closing"].total, 2000)
        self.assertEqual(statement["register"].total, 2000)
        self.assertEqual(statement["difference"], ZERO)

    def test_closing_registers(self):
        self.assertEqual([i.doc.invoice_no for i in self.work.closing_books_deferred], ["E-5"])
        self.assertEqual([i.doc.invoice_no for i in self.work.closing_pending_2b], ["C/9"])

    def test_counts(self):
        counts = self.work.counts()
        self.assertEqual(counts["gstr2b_documents"], 5)
        self.assertEqual(counts["matched"], 4)
        self.assertEqual(counts["not_in_books"], 1)
        self.assertEqual(counts["reclaimed"], 1)

    def test_a_manual_reversal_flows_through_the_bridge_and_the_statement(self):
        manual = Tax(300)
        self.assertEqual(self.work.table4(manual)["b2"].total, 2300)
        self.assertEqual(self.work.table4(manual)["net"].total, 9200)
        lines = dict(self.work.bridge(manual_4b2=manual))
        self.assertEqual(lines["manual_reversal"], Tax(-300))
        self.assertEqual(lines["difference"], ZERO)
        self.assertEqual(self.work.ecrrs(manual)["difference"], ZERO)

    def test_manual_bridge_lines_move_the_difference(self):
        lines = dict(self.work.bridge(manual_1=Tax(50)))
        self.assertEqual(lines["difference"], Tax(-50))


class TimeLimitTests(unittest.TestCase):
    def item(self, invoice_date):
        doc = mk(SOURCE_GSTR2B, 2, GSTIN_A, "X", invoice_date, 1000, igst=180)
        return make_open_item("PENDING_2B", doc, SEP, False)

    def test_status_by_financial_year(self):
        self.assertEqual(self.item(date(2025, 2, 10)).deadline_status, "EXPIRED")
        self.assertEqual(self.item(date(2026, 3, 15)).deadline_status, "EXPIRING")
        self.assertEqual(self.item(date(2026, 8, 10)).deadline_status, "OK")
        self.assertEqual(self.item(date(2026, 3, 15)).deadline, date(2026, 11, 30))

    def test_undated_items_have_an_unknown_status(self):
        doc = mk(SOURCE_GSTR2B, 2, GSTIN_A, "X", None, 1000)
        self.assertEqual(make_open_item("PENDING_2B", doc, SEP, False).deadline_status, "OK")

    def test_exposure_and_actions(self):
        opening = OpeningItems(pending_2b=[
            _tag(portal(7, GSTIN_A, "OLD", date(2025, 2, 10), 1000, igst=180,
                        origin_period="Mar-25"), OPENING_2B_SHEET),
            _tag(portal(8, GSTIN_B, "NEAR", date(2026, 3, 15), 1000, igst=90,
                        origin_period="Apr-26"), OPENING_2B_SHEET),
        ])
        work = build_workpaper([], [], SEP, opening)
        exposure = work.expiry_exposure()
        self.assertEqual((exposure["EXPIRED"].total, exposure["EXPIRING"].total), (180, 90))
        actions = work.action_items()
        self.assertIn("past its section 16(4) time limit", actions[0])
        self.assertTrue(any("within about 4 months" in a for a in actions))


class CarryForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_files_are_written_and_read_back_unchanged(self):
        two_b, book_rows, opening = wf._demo_records()
        work = build_workpaper(two_b, book_rows, SEP, opening)
        paths = write_carry_forward(work, self.temp.name)
        self.assertEqual([os.path.basename(p) for p in paths],
                         [wf.CARRY_BOOKS_FILE, wf.CARRY_2B_FILE])
        again = load_opening_items(paths[0], paths[1])
        self.assertEqual([r.invoice_no for r in again.books_deferred], ["E-5"])
        self.assertEqual([r.invoice_no for r in again.pending_2b], ["C/9"])
        self.assertEqual(again.pending_2b[0].igst, 2000)
        self.assertEqual(again.pending_2b[0].sheet, OPENING_2B_SHEET)
        self.assertEqual(origin_of(again.pending_2b[0]), SEP)
        self.assertEqual(origin_of(again.books_deferred[0]), SEP)

    def test_credit_notes_survive_the_round_trip_with_their_flag(self):
        book = books(2, GSTIN_A, "", None, -1000, igst=-180, voucher_no="DN/1",
                     booked=date(2026, 9, 4), document_type=DocumentType.CREDIT_NOTE)
        work = build_workpaper([], [book], SEP)
        paths = write_carry_forward(work, self.temp.name)
        again = load_opening_items(paths[0], "")
        record = again.books_deferred[0]
        self.assertIs(record.document_type, DocumentType.CREDIT_NOTE)
        self.assertEqual((record.taxable_value, record.igst), (-1000, -180))
        self.assertEqual(record.voucher_no, "DN/1")
        self.assertTrue(record.taken_in_3b)
        self.assertEqual(record.invoice_no, "")

    def test_a_two_month_cycle_clears_every_open_item(self):
        # August: X2 is in GSTR-2B but not booked; Y1 is booked but the supplier has not filed;
        # a purchase return is booked ahead of the supplier's credit note.
        aug = Period(2026, 8)
        august = build_workpaper(
            [portal(1, GSTIN_A, "X1", date(2026, 8, 3), 1000, igst=180),
             portal(2, GSTIN_A, "X2", date(2026, 8, 5), 2000, igst=360)],
            [books(2, GSTIN_A, "X1", date(2026, 8, 3), 1000, booked=date(2026, 8, 4), igst=180),
             books(3, GSTIN_B, "Y1", date(2026, 8, 9), 3000, booked=date(2026, 8, 10), igst=540),
             books(4, GSTIN_A, "", None, -500, booked=date(2026, 8, 12), igst=-90,
                   voucher_no="DN/1", document_type=DocumentType.CREDIT_NOTE)],
            aug)
        self.assertEqual(august.table4()["net"], Tax(180) + Tax(-90))
        self.assertEqual(dict(august.bridge())["difference"], ZERO)
        self.assertEqual(sorted(i.doc.invoice_no or i.doc.voucher_no
                                for i in august.closing_books_deferred), ["DN/1", "Y1"])
        self.assertEqual([i.doc.invoice_no for i in august.closing_pending_2b], ["X2"])
        paths = write_carry_forward(august, self.temp.name)

        # September: Y1 reaches GSTR-2B, so does the supplier's credit note (already reversed in
        # August), X2 is finally booked.
        opening = load_opening_items(paths[0], paths[1])
        september = build_workpaper(
            [portal(1, GSTIN_B, "Y1", date(2026, 8, 9), 3000, igst=540),
             portal(2, GSTIN_A, "CN-9", date(2026, 9, 2), -500, igst=-90,
                    document_type=DocumentType.CREDIT_NOTE),
             portal(3, GSTIN_A, "Z1", date(2026, 9, 6), 4000, igst=720)],
            [books(2, GSTIN_A, "X2", date(2026, 8, 5), 2000, booked=date(2026, 9, 3), igst=360),
             books(3, GSTIN_A, "Z1", date(2026, 9, 6), 4000, booked=date(2026, 9, 7), igst=720)],
            SEP, opening)
        self.assertEqual(treatments(september), {"Y1": CLAIM_PM, "CN-9": CN_ALREADY, "Z1": CLAIM})
        self.assertEqual(books_treatments(september), {"X2": RECLAIM_4D1})
        table = september.table4()
        self.assertEqual(table["a5"], Tax(540 + 720 + 360))
        self.assertEqual(table["d1"], Tax(360))
        self.assertEqual(table["net"], Tax(540 + 720 + 360))
        self.assertEqual(dict(september.bridge())["difference"], ZERO)
        self.assertEqual(september.closing_books_deferred, [])
        self.assertEqual(september.closing_pending_2b, [])
        statement = september.ecrrs()
        self.assertEqual((statement["opening"], statement["closing"]), (Tax(360), ZERO))
        self.assertEqual(statement["difference"], ZERO)

    def test_the_firms_own_tracker_headings_are_understood(self):
        path = os.path.join(self.temp.name, "tracker.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "ITC DEFERRED IN BOOKS - BILL-WISE DETAILS FY 26-27\n"
                "Date,Particulars,GSTIN/UIN,Vch Type,Vch No.,Doc No.,Doc date,Taxable,"
                "Integrated Tax,Central Tax,State Tax,GSTR 2B Period,Unique GSTR 2B No,Remarks,"
                "Def - Books Month (not in 2B),CN in Books Not in 2B But Taken in 3B\n"
                "23-07-2026,Some Supplier,27ABCDE1234F1Z5,Registered Purchase,101/26-27,"
                "012345678901234,23-07-2026,50,9,0,0,NOT SHOWN IN 2B,,,"
                "Def - Books July-26 (not in 2B),\n"
                "04-05-2026,Another,24AAAAA0000A1Z5,Debit Note,DN/001/26-27,,,-1250,-62.5,"
                "0,0,NOT SHOWN IN 2B,,,,CN Books taken in May 26-3B\n"
            )
        items = load_opening_items(books_deferred=path)
        invoice, debit = items.books_deferred
        self.assertEqual((invoice.invoice_no, invoice.igst), ("012345678901234", 9))
        self.assertEqual(origin_of(invoice), Period(2026, 7))
        self.assertIs(debit.document_type, DocumentType.CREDIT_NOTE)
        self.assertEqual((debit.voucher_no, debit.invoice_no), ("DN/001/26-27", ""))
        self.assertTrue(debit.taken_in_3b)
        self.assertFalse(invoice.taken_in_3b)

    def test_no_opening_files_means_empty_registers(self):
        items = load_opening_items()
        self.assertEqual((items.books_deferred, items.pending_2b, items.notes), ([], [], []))


def _books_tax(rng, igst, cgst, sgst):
    """What the books recorded: usually the same tax, sometimes a wrong rate or a wrong head."""
    roll = rng.random()
    if roll < 0.15:
        return round(igst * 0.4, 2), round(cgst * 0.4, 2), round(sgst * 0.4, 2)
    if roll < 0.25:
        total = round(igst + cgst + sgst, 2)
        return (0.0, round(total / 2, 2), round(total / 2, 2)) if igst else (total, 0.0, 0.0)
    return igst, cgst, sgst


def _random_world(rng):
    """A month of invented but internally consistent data, full of awkward cases."""
    suppliers = [f"{state:02d}AAAAA{number:04d}A1Z{number % 10}"
                 for number, state in enumerate((19, 27, 24, 7, 29, 33), start=1)]
    two_b, book_rows, opening_books, opening_pending = [], [], [], []
    row = 1
    for n in range(rng.randint(8, 30)):
        gstin = rng.choice(suppliers)
        number = f"INV/{n:03d}"
        when = date(2026, rng.choice((7, 8, 9)), rng.randint(1, 28))
        taxable = rng.choice((1000, 2500, 40000, 99999.5))
        rate = rng.choice((0.05, 0.12, 0.18))
        tax = round(taxable * rate, 2)
        igst, cgst, sgst = (tax, 0.0, 0.0) if rng.random() < 0.5 else (0.0, round(tax / 2, 2),
                                                                     round(tax / 2, 2))
        where = rng.random()
        row += 1
        if where < 0.35:                      # in both
            two_b.append(portal(row, gstin, number, when, taxable, igst=igst, cgst=cgst,
                                sgst=sgst, itc_available=rng.random() > 0.1))
            wobble = rng.choice((0, 0, 0, 1, -40))
            b_igst, b_cgst, b_sgst = _books_tax(rng, igst, cgst, sgst)
            book_rows.append(books(row, gstin, number if rng.random() > 0.2 else number.lower(),
                                   when, taxable + wobble, igst=b_igst, cgst=b_cgst, sgst=b_sgst,
                                   booked=date(2026, rng.choice((8, 9, 9, 9, 10)),
                                               rng.randint(1, 28))))
        elif where < 0.5:                     # in 2B only
            two_b.append(portal(row, gstin, number, when, taxable, igst=igst, cgst=cgst,
                                sgst=sgst))
        elif where < 0.7:                     # books only
            book_rows.append(books(row, gstin if rng.random() > 0.1 else "", number, when,
                                   taxable, igst=igst, cgst=cgst, sgst=sgst,
                                   booked=date(2026, rng.choice((8, 9, 9, 10)),
                                               rng.randint(1, 28))))
        elif where < 0.8:                     # deferred earlier, in 2B now
            booked = date(2026, 8, rng.randint(1, 28))
            opening_books.append(_tag(books(row, gstin, number, when, taxable, igst=igst,
                                            cgst=cgst, sgst=sgst, booked=booked),
                                      OPENING_BOOKS_SHEET))
            if rng.random() < 0.7:
                two_b.append(portal(row, gstin, number, when, taxable, igst=igst, cgst=cgst,
                                    sgst=sgst))
        elif where < 0.9:                     # pending from an earlier 2B, booked now or not
            opening_pending.append(_tag(portal(row, gstin, number, when, taxable, igst=igst,
                                               cgst=cgst, sgst=sgst, origin_period="Aug-26"),
                                        OPENING_2B_SHEET))
            if rng.random() < 0.6:
                b_igst, b_cgst, b_sgst = _books_tax(rng, igst, cgst, sgst)
                book_rows.append(books(row, gstin, number, when, taxable, igst=b_igst, cgst=b_cgst,
                                       sgst=b_sgst, booked=date(2026, 9, rng.randint(1, 28))))
        else:                                 # reverse charge on one side or both
            two_b.append(portal(row, gstin, number, when, taxable, igst=igst, rcm=True))
            if rng.random() < 0.5:
                book_rows.append(books(row, gstin, number, when, taxable, igst=igst, rcm=True))
    for n in range(rng.randint(0, 5)):        # credit notes and purchase returns
        gstin = rng.choice(suppliers)
        amount = rng.choice((500.0, 1250.0, 8000.0))
        tax = round(amount * 0.18, 2)
        row += 1
        kind = rng.random()
        if kind < 0.4:
            two_b.append(portal(row, gstin, f"CN/{n}", date(2026, 9, rng.randint(1, 28)),
                                -amount, igst=-tax, document_type=DocumentType.CREDIT_NOTE))
            book_rows.append(books(row, gstin, "", None, -amount, igst=-tax,
                                   voucher_no=f"DN/{n}", document_type=DocumentType.CREDIT_NOTE))
        elif kind < 0.6:
            two_b.append(portal(row, gstin, f"CN/{n}", date(2026, 9, rng.randint(1, 28)),
                                -amount, igst=-tax, document_type=DocumentType.CREDIT_NOTE))
        elif kind < 0.8:
            book_rows.append(books(row, gstin, "", None, -amount, igst=-tax,
                                   voucher_no=f"DN/{n}", document_type=DocumentType.CREDIT_NOTE))
        else:
            opening_books.append(_tag(books(
                row, gstin, "", None, -amount, igst=-tax, voucher_no=f"DN/{n}",
                booked=date(2026, 8, 20), document_type=DocumentType.CREDIT_NOTE,
                taken_in_3b=rng.random() < 0.5), OPENING_BOOKS_SHEET))
            two_b.append(portal(row, gstin, f"CN/{n}", date(2026, 9, 3), -amount, igst=-tax,
                                document_type=DocumentType.CREDIT_NOTE))
    return two_b, book_rows, OpeningItems(opening_books, opening_pending)


TAGS_2B_INVOICE = (CLAIM, CLAIM_PM, REVERSE_4B2, REVERSE_4B1, INELIGIBLE)
TAGS_2B_CREDIT = (CN_REVERSE, CN_ALREADY, CN_NOT_OURS)
TAGS_BOOKS_INVOICE = (DEFER_BOOKS, RECLAIM_4D1, NO_ITC, NOT_THIS_PERIOD)
TAGS_BOOKS_CREDIT = (CN_TAKEN, CN_HOLD, NO_ITC)


class IdentityPropertyTests(unittest.TestCase):
    """The reconciliations hold for any data and any tagging - not just for the demo."""

    def check_identities(self, work, manual):
        lines = dict(work.bridge(manual_4b2=manual))
        self.assertTrue(lines["difference"].is_zero(0.005), lines["difference"])
        table = work.table4(manual)
        self.assertTrue((table["a5"] - table["b1"] - table["b2"] - table["net"]).is_zero(0.005))
        claims = sum_tax(r.claim for r in work.rows_2b) + sum_tax(r.claim for r in work.rows_books)
        self.assertTrue((table["net"] + manual - claims).is_zero(0.005))
        # The pending register and the credit-reversal statement differ only by the pending
        # items matched to the books less the items tagged for reclaim.
        matched_pending = sum_tax(r.basis for r in work.rows_books
                                  if r.pending is not None and not r.credit_like)
        expected = (matched_pending - table["d1"]).rounded()
        self.assertTrue((work.ecrrs(manual)["difference"] - expected).is_zero(0.005))

    def test_identities_hold_for_random_months(self):
        for seed in range(150):
            rng = random.Random(seed)
            two_b, book_rows, opening = _random_world(rng)
            work = build_workpaper(
                two_b, book_rows, SEP, opening,
                cn_policy=rng.choice(("books", "2b")),
                reconciler=GSTR2BReconciler(taxable_tolerance=rng.choice((0.0, 100.0))))
            with self.subTest(seed=seed):
                self.check_identities(work, ZERO)
                self.assertTrue(work.ecrrs()["difference"].is_zero(0.005))

    def test_identities_hold_after_arbitrary_retagging_and_manual_inputs(self):
        for seed in range(150):
            rng = random.Random(1000 + seed)
            two_b, book_rows, opening = _random_world(rng)
            work = build_workpaper(two_b, book_rows, SEP, opening)
            for row in work.rows_2b:
                pool = TAGS_2B_CREDIT if row.credit_like else TAGS_2B_INVOICE
                row.treatment = rng.choice(pool)
            for row in work.rows_books:
                pool = TAGS_BOOKS_CREDIT if row.credit_like else TAGS_BOOKS_INVOICE
                row.treatment = rng.choice(pool)
            manual = Tax(rng.choice((0, 125.5)), rng.choice((0, 40)), 0, 0)
            with self.subTest(seed=seed):
                self.check_identities(work, manual)

    def test_every_document_lands_in_exactly_one_place(self):
        for seed in range(100):
            rng = random.Random(5000 + seed)
            two_b, book_rows, opening = _random_world(rng)
            work = build_workpaper(two_b, book_rows, SEP, opening)
            counts = work.counts()
            ordinary_2b = [r for r in two_b if not r.rcm]
            with self.subTest(seed=seed):
                self.assertEqual(len({(r.sheet, r.invoice_no or r.voucher_no, r.gstin_key,
                                       r.invoice_date) for r in ordinary_2b}),
                                 counts["gstr2b_documents"])
                seen = [id(r.doc) for r in work.rows_2b]
                self.assertEqual(len(seen), len(set(seen)))
                matched_books = {wf._source_key(r.book) for r in work.rows_2b if r.book}
                only_books = {wf._source_key(r.doc) for r in work.rows_books}
                self.assertFalse(matched_books & only_books)

    def test_pending_items_are_either_reclaimed_or_carried(self):
        for seed in range(100):
            rng = random.Random(9000 + seed)
            two_b, book_rows, opening = _random_world(rng)
            work = build_workpaper(two_b, book_rows, SEP, opening)
            pending_total = sum_tax(Tax.of(r) for r in opening.pending_2b)
            reclaimed = sum_tax(r.basis for r in work.rows_books if r.pending is not None)
            carried = sum_tax(i.tax for i in work.closing_pending_2b if i.carried)
            with self.subTest(seed=seed):
                self.assertTrue((pending_total - reclaimed - carried).is_zero(0.005))


class ActionItemTests(unittest.TestCase):
    def test_a_clean_month_needs_no_action(self):
        work = build_workpaper(
            [portal(1, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)],
            [books(2, GSTIN_A, "A-1", date(2026, 9, 3), 1000, igst=180)], SEP)
        self.assertEqual(len(work.action_items()), 1)
        self.assertIn("No action required", work.action_items()[0])

    def test_open_registers_produce_follow_ups(self):
        two_b, book_rows, opening = wf._demo_records()
        work = build_workpaper(two_b, book_rows, SEP, opening)
        text = " ".join(work.action_items())
        self.assertIn("Chase 1 supplier(s)", text)
        self.assertIn("Book the 1 GSTR-2B document(s) still pending", text)


def purchase_return(row, number, booked, amount, **kwargs):
    """A Tally debit note: its own voucher number, citing an original invoice."""
    return books(row, GSTIN_A, "INV-ORIG", date(2026, 7, 1), amount, booked=booked,
                 igst=amount * 0.18, voucher_no=number, document_type=DocumentType.CREDIT_NOTE,
                 **kwargs)


class CumulativeRegisterTests(unittest.TestCase):
    """The books file is the whole year's register and the opening registers are cut from it."""

    def test_an_earlier_purchase_return_is_not_reversed_a_second_time(self):
        earlier = purchase_return(1, "DN/1", date(2026, 7, 5), 1000.0)
        work = build_workpaper([], [earlier], SEP)
        self.assertEqual(work.rows_books, [])
        self.assertEqual(work.table4()["credit_books"], ZERO)
        self.assertEqual(work.closing_books_deferred, [])
        self.assertEqual(len(work.settled_earlier), 1)
        self.assertTrue(any("dealt with in the returns of their own months" in n
                            for n in work.notes))

    def test_the_assumption_is_an_action_item_only_when_no_register_was_given(self):
        earlier = purchase_return(1, "DN/1", date(2026, 7, 5), 1000.0)
        without = build_workpaper([], [earlier], SEP)
        self.assertTrue(any("no opening registers were given" in item
                            for item in without.action_items()))
        given = build_workpaper([], [earlier], SEP, OpeningItems(supplied=True))
        self.assertFalse(any("no opening registers were given" in item
                             for item in given.action_items()))
        self.assertEqual(len(given.settled_earlier), 1)

    def test_an_earlier_invoice_not_in_this_months_2b_is_not_a_new_deferral(self):
        earlier = books(1, GSTIN_A, "INV-1", date(2026, 7, 1), 1000.0, booked=date(2026, 7, 5),
                        igst=180.0)
        work = build_workpaper([], [earlier], SEP)
        self.assertEqual((work.rows_books, work.closing_books_deferred), ([], []))

    def test_an_earlier_entry_that_reclaims_a_pending_2b_document_still_does(self):
        pending = portal(1, GSTIN_A, "INV-1", date(2026, 7, 1), 1000.0, igst=180.0,
                         sheet=OPENING_2B_SHEET)
        earlier = books(1, GSTIN_A, "INV-1", date(2026, 7, 1), 1000.0, booked=date(2026, 8, 20),
                        igst=180.0)
        work = build_workpaper([], [earlier], SEP, OpeningItems(pending_2b=[pending]))
        self.assertEqual(books_treatments(work), {"INV-1": RECLAIM_4D1})
        self.assertEqual(work.settled_earlier, [])

    def test_a_voucher_the_register_tracks_stays_open(self):
        tracked = purchase_return(1, "DN/1", date(2026, 7, 5), 1000.0, taken_in_3b=True)
        opening = OpeningItems(books_deferred=[replace(tracked, sheet=OPENING_BOOKS_SHEET)],
                               supplied=True)
        work = build_workpaper([], [tracked], SEP, opening)
        self.assertEqual(len(work.closing_books_deferred), 1)
        self.assertEqual(work.settled_earlier, [])
        self.assertTrue(any("counted once" in note for note in work.notes))

    def test_a_voucher_in_both_the_file_and_the_register_pairs_once_from_the_register(self):
        file_copy = purchase_return(1, "DN/9", date(2026, 8, 5), 1000.0)
        register_copy = replace(file_copy, sheet=OPENING_BOOKS_SHEET, taken_in_3b=True)
        notes = [portal(row, GSTIN_A, f"CN-{row}", date(2026, 9, 3), 1000.0, igst=180.0,
                        document_type=DocumentType.CREDIT_NOTE) for row in (1, 2)]
        work = build_workpaper(notes, [file_copy], SEP,
                               OpeningItems(books_deferred=[register_copy], supplied=True))
        paired = [row for row in work.rows_2b if row.book is not None]
        self.assertEqual(len(paired), 1)        # one voucher, one credit note - not two
        self.assertEqual((paired[0].books_class, paired[0].treatment), (CLASS_OPENING, CN_ALREADY))
        unpaired = [row for row in work.rows_2b if row.book is None]
        self.assertEqual(unpaired[0].treatment, CN_REVERSE)

    def test_a_tracked_voucher_has_first_claim_over_an_equal_untracked_one(self):
        tracked = purchase_return(1, "DN/1", date(2026, 7, 1), 1000.0, taken_in_3b=True)
        other = purchase_return(2, "DN/2", date(2026, 9, 1), 1000.0)    # nearer the credit note
        note = portal(1, GSTIN_A, "CN-1", date(2026, 9, 3), 1000.0, igst=180.0,
                      document_type=DocumentType.CREDIT_NOTE)
        opening = OpeningItems(books_deferred=[replace(tracked, sheet=OPENING_BOOKS_SHEET)],
                               supplied=True)
        work = build_workpaper([note], [other], SEP, opening)
        row = work.rows_2b[0]
        self.assertEqual((row.books_class, row.treatment), (CLASS_OPENING, CN_ALREADY))


class TrackerSheetTests(unittest.TestCase):
    """The firm's own cumulative sheets are read in place: only what is still open comes in."""

    BOOKS_TRACKER = (
        "Date,Particulars,GSTIN/UIN,Vch Type,Vch No.,Doc No.,Doc date,Taxable,Integrated Tax,"
        "Central Tax,State Tax,Unique GSTR 2B No,Def - Books Month (not in 2B),Reclaimed Month,"
        "CN in Books Not in 2B But Taken in 3B\n"
        "01-07-2026,A,27ABCDE1234F1Z5,Registered Purchase,V1,D1,01-07-2026,100,18,0,0,,"
        "Def - Books July-26 (not in 2B),,\n"
        "02-07-2026,B,27ABCDE1234F1Z5,Registered Purchase,V2,D2,02-07-2026,100,18,0,0,"
        "235 Aug-26,Def - Books July-26 (not in 2B),,\n"
        "03-07-2026,C,27ABCDE1234F1Z5,Registered Purchase,V3,D3,03-07-2026,100,18,0,0,,"
        "Def - Books July-26 (not in 2B),Reclaimed in Aug-26,\n"
        "04-07-2026,D,27ABCDE1234F1Z5,Registered Purchase,V4,D4,04-07-2026,100,18,0,0,"
        "12 Jul-26,,,\n"
        "05-07-2026,E,27ABCDE1234F1Z5,Debit Note,DN1,,,-50,-9,0,0,,,,CN taken in 3B-Jul-26\n"
        "06-07-2026,F,27ABCDE1234F1Z5,Debit Note,DN2,,,-50,-9,0,0,240 Aug-26,,,"
        "CN taken in 3B-Jul-26\n"
    )
    TWOB_TRACKER = (
        "GSTIN of supplier,Trade/Legal name,Invoice number,Invoice type,Invoice Date,"
        "Taxable Value,Integrated Tax,Central Tax,State/UT Tax,DEFFERRED & INELIGIABLE  MONTH,"
        "RECLAIM MONTH\n"
        "27ABCDE1234F1Z5,A,P1,B2B,01-07-2026,100,18,0,0,DEFFERRED FOR JULY-26,\n"
        "27ABCDE1234F1Z5,B,P2,B2B,02-07-2026,100,18,0,0,DEFFERRED FOR JULY-26,RECLAIM AUG-26\n"
        "27ABCDE1234F1Z5,C,P3,B2B,03-07-2026,100,18,0,0,INELIGIBLE FOR JULY-26,\n"
        "27ABCDE1234F1Z5,D,P4,B2B,04-07-2026,100,18,0,0,,\n"
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write(self, name, text):
        path = os.path.join(self.temp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_only_the_open_rows_of_a_books_tracker_come_in(self):
        items = load_opening_items(books_deferred=self.write("books.csv", self.BOOKS_TRACKER))
        self.assertEqual([r.voucher_no for r in items.books_deferred], ["V1", "DN1"])
        self.assertTrue(items.supplied)
        self.assertTrue(any("4 closed row(s) of the tracker left out - 2 still open" in n
                            for n in items.notes))

    def test_only_the_deferred_rows_not_yet_reclaimed_come_in_from_a_2b_tracker(self):
        items = load_opening_items(pending_2b=self.write("2b.csv", self.TWOB_TRACKER))
        self.assertEqual([r.invoice_no for r in items.pending_2b], ["P1"])
        self.assertTrue(all(r.sheet == OPENING_2B_SHEET for r in items.pending_2b))

    def test_a_plain_list_of_open_items_is_taken_whole(self):
        plain = ("GSTIN of supplier,Invoice number,Invoice Date,Taxable Value,Integrated Tax\n"
                 "27ABCDE1234F1Z5,P1,01-07-2026,100,18\n27ABCDE1234F1Z5,P2,02-07-2026,100,18\n")
        items = load_opening_items(pending_2b=self.write("plain.csv", plain))
        self.assertEqual(len(items.pending_2b), 2)
        self.assertEqual(items.notes, [n for n in items.notes if "closed row" not in n])

    def test_a_register_given_empty_is_still_a_register(self):
        header = "GSTIN of supplier,Invoice number,Invoice Date,Taxable Value\n"
        items = load_opening_items(pending_2b=self.write("empty.csv", header))
        self.assertEqual((items.pending_2b, items.supplied), ([], True))
        self.assertFalse(load_opening_items().supplied)

    def test_unique_2b_numbers_are_recognised_as_the_firm_writes_them(self):
        for text in ("235 May-26", "77JUN-26", "431 SEPT-25", " 12 Jul-26 "):
            self.assertTrue(wf.is_2b_number(text), text)
        for text in ("", "NOT SHOWING YET", "CN IN BOOKS NOT IN GSTR 2B", "12", "May-26"):
            self.assertFalse(wf.is_2b_number(text), text)


if __name__ == "__main__":
    unittest.main()
