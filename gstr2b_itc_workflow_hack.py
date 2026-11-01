#!/usr/bin/env python3
"""
Finance Hack: the monthly GSTR-2B vs Books ITC workflow (engine).

A one-off match of GSTR-2B against the purchase register is only half of what a
GST practice does every month. The other half is carrying ITC across months, and
this module models that process as a chartered-accountancy firm runs it:

  * GSTR-3B Table 4(A)(5) is kept tied to GSTR-2B. Credit that is in GSTR-2B but
    not yet in the books is *reversed* under 4(B)(2), tracked bill by bill as
    "2B pending", and *reclaimed* under 4(D)(1) in the month it is booked.
  * Credit that is in the books but not yet in GSTR-2B is *deferred*: not
    claimed, tracked bill by bill as "books deferred", and claimed in the month
    the supplier's return puts it into GSTR-2B (section 16(2)(aa)).
  * Both registers are carried forward from month to month. The electronic
    credit reversal and re-claimed statement (opening + 4(B)(2) - 4(D)(1) =
    closing) must agree with the pending register, and the ITC booked must bridge
    to the ITC claimed with a difference of nil.
  * Every GSTR-2B line carries a *treatment* - claim, defer, ineligible, reverse -
    chosen by the tool and changeable by the accountant.

The module is pure logic with no spreadsheet code: `build_workpaper()` takes the
two sides plus the opening registers and returns a `Workpaper` that knows its
Table 4 figures, its bridge and its closing registers. The Excel layout lives in
gstr2b_workpaper_hack.py.

Educational/demo module - the treatments are defaults to be reviewed, and the
statutory points are summarised, not advised on.
"""

import csv
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gstr2b_reconciliation_hack import (
    FLAG_GSTIN_MISMATCH,
    SOURCE_BOOKS,
    SOURCE_GSTR2B,
    DocumentType,
    GSTR2BReconciler,
    InvoiceRecord,
    MatchedPair,
    ReconStatus,
    aggregate_records,
    find_duplicate_documents,
    load_records,
    normalise_invoice_no,
    split_reverse_charge,
)

# ------------------------------------------------------------------ treatments
# GSTR-2B documents that carry ITC (invoices, supplier debit notes)
CLAIM = "CLAIM"                          # in 2B and booked this month -> claim
CLAIM_PM = "CLAIM_PM_BOOKED"             # in 2B, booked in an earlier month -> claim now
REVERSE_4B2 = "REVERSE_4B2"              # in 2B, not booked -> reverse, reclaim when booked
REVERSE_4B1 = "REVERSE_4B1"              # permanently reversed (17(5), rules 42/43)
INELIGIBLE = "INELIGIBLE"                # 2B says ITC not available
# GSTR-2B credit notes
CN_REVERSE = "CN_REVERSE"                # reduce ITC in this return
CN_ALREADY = "CN_ALREADY_TAKEN"          # reversed earlier from the books
CN_NOT_OURS = "CN_NOT_OURS"              # not our bill: reject in IMS, no reversal
# books-only documents
DEFER_BOOKS = "DEFER_BOOKS"              # booked, not in 2B -> hold, claim when it appears
RECLAIM_4D1 = "RECLAIM_4D1"              # books now match an earlier pending 2B document
CN_TAKEN = "CN_TAKEN_BOOKS"              # purchase return reversed from the books
CN_HOLD = "CN_HOLD"                      # purchase return not reversed yet
NO_ITC = "NO_ITC"                        # no GSTIN / blocked: no credit at all
NOT_THIS_PERIOD = "NOT_THIS_PERIOD"      # booked after the period end

TREATMENTS_2B = (CLAIM, CLAIM_PM, REVERSE_4B2, REVERSE_4B1, INELIGIBLE,
                 CN_REVERSE, CN_ALREADY, CN_NOT_OURS)
TREATMENTS_BOOKS = (DEFER_BOOKS, RECLAIM_4D1, CN_TAKEN, CN_HOLD, NO_ITC, NOT_THIS_PERIOD)

CLAIMED_2B = frozenset({CLAIM, CLAIM_PM, CN_REVERSE})
IN_4A5_INVOICE = frozenset({CLAIM, CLAIM_PM, REVERSE_4B2, REVERSE_4B1})
CLAIMED_BOOKS = frozenset({RECLAIM_4D1, CN_TAKEN})

TREATMENT_HELP: Dict[str, str] = {
    CLAIM: "In GSTR-2B and booked this month: claim in 4(A)(5).",
    CLAIM_PM: "In GSTR-2B, booked in an earlier month (was deferred): claim now.",
    REVERSE_4B2: "In GSTR-2B but not booked: shown in 4(A)(5), reversed in 4(B)(2), "
                 "reclaimed under 4(D)(1) when booked.",
    REVERSE_4B1: "Permanently reversed: 17(5) or rules 42/43 - 4(B)(1).",
    INELIGIBLE: "GSTR-2B marks ITC not available: kept out of 4(A)(5), shown in 4(D)(2).",
    CN_REVERSE: "Credit note: reverse the ITC in this return.",
    CN_ALREADY: "Credit note already reversed from the books in an earlier return.",
    CN_NOT_OURS: "Credit note that is not ours: reject it in IMS; nothing is reversed.",
    DEFER_BOOKS: "Booked but not in GSTR-2B: not claimed; claim when it appears.",
    RECLAIM_4D1: "Books now carry a document that was pending from an earlier GSTR-2B: "
                 "reclaim under 4(D)(1).",
    CN_TAKEN: "Purchase return reversed in this return from the books.",
    CN_HOLD: "Purchase return booked but not reversed yet.",
    NO_ITC: "No supplier GSTIN or blocked credit: no ITC.",
    NOT_THIS_PERIOD: "Booked after the end of this period: belongs to a later return.",
}

# Books classes - when the books entry was made relative to the return period
CLASS_CM = "CM"                # booked in the return month
CLASS_PM = "PM"                # booked in an earlier month, not in the deferred register
CLASS_OPENING = "OPENING"      # carried in from the deferred register
CLASS_FUTURE = "FUTURE"        # booked after the return month
CLASS_NONE = "NONE"            # no books entry

CN_POLICIES = ("books", "2b")

OPENING_BOOKS_SHEET = "Opening: books deferred"
OPENING_2B_SHEET = "Opening: 2B pending"

CARRY_BOOKS_FILE = "carry_forward_books_deferred.csv"
CARRY_2B_FILE = "carry_forward_2b_pending.csv"

# Credit more than this many days from the filing date of its time limit is "expiring"
EXPIRY_WARNING_DAYS = 120

KIND_BOOKS_DEFERRED = "BOOKS_DEFERRED"
KIND_PENDING_2B = "PENDING_2B"

# ----------------------------------------------------------------- tax vectors


@dataclass(frozen=True)
class Tax:
    """The four tax heads as one value, so figures add and subtract as a unit."""
    igst: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    cess: float = 0.0

    def __add__(self, other: "Tax") -> "Tax":
        return Tax(self.igst + other.igst, self.cgst + other.cgst,
                   self.sgst + other.sgst, self.cess + other.cess)

    def __sub__(self, other: "Tax") -> "Tax":
        return Tax(self.igst - other.igst, self.cgst - other.cgst,
                   self.sgst - other.sgst, self.cess - other.cess)

    def __neg__(self) -> "Tax":
        return Tax(-self.igst, -self.cgst, -self.sgst, -self.cess)

    @property
    def total(self) -> float:
        return round(self.igst + self.cgst + self.sgst + self.cess, 2)

    def rounded(self) -> "Tax":
        return Tax(round(self.igst, 2) + 0.0, round(self.cgst, 2) + 0.0,
                   round(self.sgst, 2) + 0.0, round(self.cess, 2) + 0.0)

    def as_list(self) -> List[float]:
        return [self.igst, self.cgst, self.sgst, self.cess]

    def is_zero(self, tolerance: float = 0.005) -> bool:
        return all(abs(value) <= tolerance for value in self.as_list())

    @classmethod
    def of(cls, record: InvoiceRecord) -> "Tax":
        return cls(record.igst, record.cgst, record.sgst, record.cess)


ZERO = Tax()


def sum_tax(items: Iterable[Tax]) -> Tax:
    """Add a collection of Tax values, rounded to the paisa."""
    total = ZERO
    for item in items:
        total = total + item
    return total.rounded()


# ---------------------------------------------------------------------- periods
_MONTH_WORDS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
_PERIOD_NAME = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s\-'/,]*(\d{4}|\d{2})\b",
    re.I,
)
_PERIOD_ISO = re.compile(r"\b(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?\b")
_PERIOD_MY = re.compile(r"\b(\d{1,2})[-/](\d{4})\b")
_PERIOD_COMPACT = re.compile(r"\b(\d{2})(\d{4})\b")


@dataclass(frozen=True, order=True)
class Period:
    """A return month."""
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"month must be 1-12, got {self.month}")
        if not 2000 <= self.year <= 2100:
            raise ValueError(f"year {self.year} is out of range")

    @property
    def index(self) -> int:
        return self.year * 12 + self.month - 1

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        following = self.first_day.replace(day=28) + timedelta(days=4)
        return following.replace(day=1) - timedelta(days=1)

    @property
    def label(self) -> str:
        return f"{_MONTH_NAMES[self.month - 1][:3]}-{self.year % 100:02d}"

    @property
    def long_label(self) -> str:
        return f"{_MONTH_NAMES[self.month - 1]} {self.year}"

    @property
    def fy_start(self) -> date:
        return date(self.year if self.month >= 4 else self.year - 1, 4, 1)

    @property
    def fy_label(self) -> str:
        start = self.fy_start.year
        return f"{start}-{(start + 1) % 100:02d}"

    @property
    def due_date(self) -> date:
        """The 20th of the following month - when the GSTR-3B for this month falls due."""
        following = self.last_day + timedelta(days=1)
        return following.replace(day=20)

    def months_since(self, earlier: "Period") -> int:
        return self.index - earlier.index


def parse_period(value) -> Optional[Period]:
    """
    Read a month out of whatever text a register carries.

    Understands '2026-09', '09-2026', '092026', 'Sep-26', 'Sept 2026', and the
    labels used in practice ('Deffered In MAY-25', 'Def - Books Sept-26 (not in
    2B)', '235 May-26'). A full date yields its month. Returns None when there is
    no month to find.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return Period(value.year, value.month)
    text = str(value).strip()
    named = _PERIOD_NAME.search(text)
    if named:
        year = int(named.group(2))
        year = year + 2000 if year < 100 else year
        return _safe_period(year, _MONTH_WORDS[named.group(1).lower()])
    iso = _PERIOD_ISO.search(text)
    if iso:
        return _safe_period(int(iso.group(1)), int(iso.group(2)))
    month_year = _PERIOD_MY.search(text)
    if month_year:
        return _safe_period(int(month_year.group(2)), int(month_year.group(1)))
    compact = _PERIOD_COMPACT.search(text)
    if compact:
        return _safe_period(int(compact.group(2)), int(compact.group(1)))
    return None


def _safe_period(year: int, month: int) -> Optional[Period]:
    try:
        return Period(year, month)
    except ValueError:
        return None


def require_period(text: str) -> Period:
    """parse_period, but a value that is not a month is an error."""
    period = parse_period(text)
    if period is None:
        raise ValueError(
            f"cannot read {text!r} as a month - use a form such as 2026-09, Sep-26 or 092026"
        )
    return period


def itc_time_limit(document_date: date) -> date:
    """
    The last date to claim ITC on a document dated `document_date`: 30 November
    following the end of the financial year the document belongs to (section 16(4),
    as amended). Filing the annual return earlier brings the limit forward.
    """
    year = document_date.year + 1 if document_date.month >= 4 else document_date.year
    return date(year, 11, 30)


def origin_of(record: InvoiceRecord) -> Optional[Period]:
    """The month an open item arose in, from whichever column of the register says so."""
    for text in (record.origin_period, record.period_ref, record.ref):
        period = parse_period(text)
        if period is not None:
            return period
    for when in (record.booking_date, record.invoice_date):
        if when is not None:
            return Period(when.year, when.month)
    return None


# ---------------------------------------------------------------- opening items


@dataclass
class OpeningItems:
    """The two registers carried in from the previous month."""
    books_deferred: List[InvoiceRecord] = field(default_factory=list)
    pending_2b: List[InvoiceRecord] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    supplied: bool = False       # an opening register was given (even an empty one)


def _tag_opening(records: Sequence[InvoiceRecord], sheet: str) -> List[InvoiceRecord]:
    for record in records:
        record.sheet = sheet
    return list(records)


_UNIQUE_2B_NUMBER = re.compile(r"^\s*\d+\s*[A-Za-z]{3,9}[-\s']*\d{2,4}\s*$")


def is_2b_number(text: str) -> bool:
    """A 'unique GSTR-2B number' as the firm writes it: serial and month, '235 May-26'."""
    return bool(_UNIQUE_2B_NUMBER.match(text or ""))


def _still_open(record: InvoiceRecord, books: bool) -> bool:
    """
    Whether a row of a tracker sheet is still open.

    A cumulative tracker keeps every row of the year and marks the closed ones: a
    GSTR-2B line carries the month it was reclaimed; a books entry carries the
    GSTR-2B number it was matched to, or its reclaim month. What is left is open
    only if it was deferred (or, on the books side, a purchase return already
    reversed and waiting for the supplier's credit note). Deferrals marked
    'ineligible' never come back.
    """
    if record.reclaimed.strip():
        return False
    if books:
        return not is_2b_number(record.ref) and bool(record.origin_period.strip()
                                                     or record.taken_in_3b)
    label = record.origin_period.strip().upper()
    return bool(label) and not label.startswith("INELIG")


def _open_rows(records: List[InvoiceRecord], books: bool) -> Tuple[List[InvoiceRecord], int]:
    """
    Keep the open rows of a tracker sheet; leave a plain list of open items alone.

    A sheet is a tracker when it records closure at all - a reclaim-month column or
    GSTR-2B numbers linking the books to the portal. The CSVs this tool writes carry
    neither, and every row of them is open.
    """
    tracker = any(r.reclaimed.strip() or is_2b_number(r.ref) for r in records)
    if not tracker:
        return records, 0
    kept = [r for r in records if _still_open(r, books)]
    return kept, len(records) - len(kept)


def load_opening_items(books_deferred: str = "", pending_2b: str = "") -> OpeningItems:
    """
    Read the opening registers. Each is a CSV or workbook (optionally 'file#Sheet')
    in the layout this tool writes at the end of a run, or the firm's own
    tracker sheets, read in place: the same header aliases apply as for the main
    files, and in a tracker that also holds the rows already closed only the open
    ones are brought forward.
    """
    items = OpeningItems(supplied=bool(books_deferred or pending_2b))
    for path, source, side, books in ((books_deferred, SOURCE_BOOKS, "books", True),
                                      (pending_2b, SOURCE_GSTR2B, "portal", False)):
        if not path:
            continue
        label = "Opening books deferred" if books else "Opening 2B pending"
        records, notes = load_records(path, source, side=side)
        records, closed = _open_rows(records, books)
        if closed:
            notes.append(f"{closed} closed row(s) of the tracker left out - {len(records)} "
                         f"still open")
        if books:
            items.books_deferred = _tag_opening(records, OPENING_BOOKS_SHEET)
        else:
            items.pending_2b = _tag_opening(records, OPENING_2B_SHEET)
        items.notes += [f"{label} - {note}" for note in notes]
    return items


# -------------------------------------------------------------------- the rows


def books_class(record: InvoiceRecord, period: Period, dated: bool = True) -> str:
    """When a books entry was made relative to the return month."""
    if record.sheet == OPENING_BOOKS_SHEET:
        return CLASS_OPENING
    when = record.booking_date
    if when is None or not dated:
        return CLASS_CM
    if when > period.last_day:
        return CLASS_FUTURE
    if when < period.first_day:
        return CLASS_PM
    return CLASS_CM


def _credit_like(record: InvoiceRecord) -> bool:
    return record.sign_class == "C"


@dataclass
class Row2B:
    """One GSTR-2B document, with its books match and its treatment."""
    ref: str
    doc: InvoiceRecord
    pair: Optional[MatchedPair]
    book: Optional[InvoiceRecord]
    books_class: str
    treatment: str
    notes: List[str] = field(default_factory=list)

    @property
    def credit_like(self) -> bool:
        return _credit_like(self.doc)

    @property
    def tax(self) -> Tax:
        return Tax.of(self.doc)

    @property
    def books_tax(self) -> Tax:
        return Tax.of(self.book) if self.book is not None else ZERO

    @property
    def claim(self) -> Tax:
        """What this line adds to the net ITC of the return. The tag alone decides: it is
        what the spreadsheet formulas read too, so the two always agree."""
        return self.tax if self.treatment in CLAIMED_2B else ZERO

    @property
    def difference(self) -> Tax:
        """Books minus GSTR-2B, where the two are matched."""
        return self.books_tax - self.tax if self.book is not None else ZERO


@dataclass
class RowBooks:
    """One books document that GSTR-2B does not carry."""
    ref: str
    doc: InvoiceRecord
    books_class: str
    treatment: str
    pending: Optional[InvoiceRecord] = None
    notes: List[str] = field(default_factory=list)

    @property
    def credit_like(self) -> bool:
        return _credit_like(self.doc)

    @property
    def tax(self) -> Tax:
        return Tax.of(self.doc)

    @property
    def basis(self) -> Tax:
        """The ITC this line would claim: the pending GSTR-2B document's if there is one."""
        return Tax.of(self.pending) if self.pending is not None else self.tax

    @property
    def claim(self) -> Tax:
        return self.basis if self.treatment in CLAIMED_BOOKS else ZERO

    @property
    def counts_as_booked(self) -> bool:
        """Whether it belongs in this month's books ITC."""
        return self.books_class == CLASS_CM and self.treatment != NO_ITC


@dataclass
class OpenItem:
    """One line of a closing register - what stays open for next month."""
    kind: str
    doc: InvoiceRecord
    origin: Optional[Period]
    age_months: int
    deadline: Optional[date]
    deadline_status: str
    carried: bool
    taken_in_3b: bool = False
    note: str = ""

    @property
    def tax(self) -> Tax:
        return Tax.of(self.doc)


def _deadline_status(deadline: Optional[date], period: Period) -> str:
    if deadline is None:
        return "UNKNOWN"
    due = period.due_date
    if due > deadline:
        return "EXPIRED"
    if (deadline - due).days <= EXPIRY_WARNING_DAYS:
        return "EXPIRING"
    return "OK"


def make_open_item(
    kind: str,
    doc: InvoiceRecord,
    period: Period,
    carried: bool,
    taken_in_3b: bool = False,
    note: str = "",
    origin: Optional[Period] = None,
) -> OpenItem:
    """An open register line. An item brought forward ages from the month its register
    names; a new one from `origin`, defaulting to the month being reconciled."""
    if origin is None:
        origin = origin_of(doc) if carried else period
    age = max(0, period.months_since(origin)) if origin else 0
    stamp = doc.invoice_date or doc.booking_date or (origin.first_day if origin else None)
    deadline = itc_time_limit(stamp) if stamp else None
    return OpenItem(kind, doc, origin, age, deadline, _deadline_status(deadline, period),
                    carried, taken_in_3b, note)


# --------------------------------------------------------------------- workpaper


@dataclass
class Workpaper:
    """The month's reconciliation: the rows, the closing registers and the figures."""
    period: Period
    client: str = ""
    gstin: str = ""
    cn_policy: str = "books"
    date_tolerance_days: int = 3
    taxable_tolerance: float = 100.0
    tax_tolerance: float = 10.0
    rows_2b: List[Row2B] = field(default_factory=list)
    rows_books: List[RowBooks] = field(default_factory=list)
    closing_books_deferred: List[OpenItem] = field(default_factory=list)
    closing_pending_2b: List[OpenItem] = field(default_factory=list)
    opening_books_deferred: List[InvoiceRecord] = field(default_factory=list)
    opening_pending_2b: List[InvoiceRecord] = field(default_factory=list)
    rcm_2b: List[InvoiceRecord] = field(default_factory=list)
    rcm_books: List[InvoiceRecord] = field(default_factory=list)
    settled_earlier: List[InvoiceRecord] = field(default_factory=list)
    opening_supplied: bool = False
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ------------------------------------------------------------- selections
    def reclaimed(self) -> List[RowBooks]:
        """Books lines that reclaim an earlier pending GSTR-2B document this month."""
        return [row for row in self.rows_books if row.treatment == RECLAIM_4D1]

    def differences(self) -> List[Row2B]:
        return [row for row in self.rows_2b
                if row.pair is not None and row.pair.status is ReconStatus.MATCHED_WITH_DIFFERENCE]

    def credit_notes_not_in_books(self) -> List[Row2B]:
        return [row for row in self.rows_2b if row.credit_like and row.book is None]

    # ---------------------------------------------------------------- Table 4
    def _sum(self, rows: Iterable, attr: str, treatments: Optional[Iterable[str]] = None,
             credit: Optional[bool] = None) -> Tax:
        total = ZERO
        for row in rows:
            if treatments is not None and row.treatment not in treatments:
                continue
            if credit is not None and row.credit_like != credit:
                continue
            total = total + getattr(row, attr)
        return total.rounded()

    def table4(self, manual_4b2: Tax = ZERO) -> Dict[str, Tax]:
        """GSTR-3B Table 4 as this workpaper computes it, one Tax per line."""
        invoices = self._sum(self.rows_2b, "tax", IN_4A5_INVOICE)
        credit_2b = self._sum(self.rows_2b, "tax", {CN_REVERSE})
        credit_books = self._sum(self.rows_books, "basis", {CN_TAKEN})
        reclaim = self._sum(self.rows_books, "basis", {RECLAIM_4D1})
        a5 = (invoices + credit_2b + credit_books + reclaim).rounded()
        b1 = self._sum(self.rows_2b, "tax", {REVERSE_4B1})
        b2 = (self._sum(self.rows_2b, "tax", {REVERSE_4B2}) + manual_4b2).rounded()
        net = (a5 - b1 - b2).rounded()
        d2 = self._sum(self.rows_2b, "tax", {INELIGIBLE})
        return {
            "invoices": invoices, "credit_2b": credit_2b, "credit_books": credit_books,
            "reclaim": reclaim, "a5": a5, "b1": b1, "b2": b2, "net": net, "d1": reclaim,
            "d2": d2,
        }

    def gstr2b_available(self) -> Tax:
        """ITC on the invoice-type GSTR-2B documents that carry credit - what the portal's
        own summary shows. A fact about the documents, independent of any tagging."""
        return sum_tax(r.tax for r in self.rows_2b
                       if not r.credit_like and r.doc.itc_available)

    def gstr2b_credit_notes(self) -> Tax:
        """ITC on the credit notes in GSTR-2B (negative)."""
        return sum_tax(r.tax for r in self.rows_2b if r.credit_like)

    # ----------------------------------------------------------------- bridge
    def books_itc(self) -> Tax:
        """Net ITC as per the books for this month: matched pairs plus books-only lines."""
        matched = sum_tax(r.books_tax for r in self.rows_2b if r.books_class == CLASS_CM)
        only = sum_tax(r.tax for r in self.rows_books if r.counts_as_booked)
        return (matched + only).rounded()

    def bridge(self, manual_4b2: Tax = ZERO, manual_1: Tax = ZERO,
               manual_2: Tax = ZERO) -> List[Tuple[str, Tax]]:
        """
        From the ITC booked to the ITC claimed, line by line. The last entry is the
        difference, which is nil unless a manual line is wrong or a row was retagged
        inconsistently.
        """
        cm = [r for r in self.rows_2b if r.books_class == CLASS_CM]
        elsewhere = [r for r in self.rows_2b if r.books_class != CLASS_CM]
        pm = [r for r in elsewhere if r.books_class in (CLASS_PM, CLASS_OPENING)]
        unbooked = [r for r in elsewhere if r.books_class not in (CLASS_PM, CLASS_OPENING)]
        in_cm = [r for r in self.rows_books if r.counts_as_booked]

        books = self.books_itc()
        lines: List[Tuple[str, Tax]] = [("books", books)]
        claimed_tags = {CLAIM, CLAIM_PM, CN_REVERSE}
        lines.append(("matched_difference", sum_tax(
            r.claim - r.books_tax for r in cm if r.treatment in claimed_tags)))
        lines.append(("matched_not_claimed", sum_tax(
            r.claim - r.books_tax for r in cm if r.treatment not in claimed_tags)))
        lines.append(("claimed_from_earlier_books", sum_tax(r.claim for r in pm)))
        lines.append(("claimed_without_books", sum_tax(r.claim for r in unbooked)))
        lines.append(("deferred", sum_tax(
            r.claim - r.tax for r in in_cm if r.treatment == DEFER_BOOKS)))
        lines.append(("reclaim_difference", sum_tax(
            r.claim - r.tax for r in in_cm if r.treatment == RECLAIM_4D1)))
        lines.append(("returns_held", sum_tax(
            r.claim - r.tax for r in in_cm if r.treatment == CN_HOLD)))
        lines.append(("returns_difference", sum_tax(
            r.claim - r.tax for r in in_cm if r.treatment == CN_TAKEN)))
        other_tags = {DEFER_BOOKS, RECLAIM_4D1, CN_HOLD, CN_TAKEN}
        lines.append(("books_only_other", sum_tax(
            r.claim - r.tax for r in in_cm if r.treatment not in other_tags)))
        lines.append(("books_only_outside", sum_tax(
            r.claim for r in self.rows_books if not r.counts_as_booked)))
        lines.append(("manual_reversal", -manual_4b2))
        lines.append(("manual_1", manual_1))
        lines.append(("manual_2", manual_2))
        reconciled = sum_tax(value for _name, value in lines)
        claimed = self.table4(manual_4b2)["net"]
        lines.append(("reconciled", reconciled))
        lines.append(("claimed", claimed))
        lines.append(("difference", (claimed - reconciled).rounded()))
        return lines

    # ------------------------------------------------------------------ ECRRS
    def ecrrs(self, manual: Tax = ZERO) -> Dict[str, Tax]:
        """
        The electronic credit reversal and re-claimed statement roll-forward, set against
        the bill-wise pending register.

        The register is the items brought forward and still open, plus whatever is
        currently tagged for reversal under 4(B)(2); both sides therefore follow the
        tags. They differ only if a pending item was matched to the books and not tagged
        for reclaim, or the other way round - which is exactly what the check is for.
        `manual` is any reversal (Rule 37A) kept outside the bill-wise register.
        """
        opening = sum_tax(Tax.of(r) for r in self.opening_pending_2b if not _credit_like(r))
        added = self._sum(self.rows_2b, "tax", {REVERSE_4B2})
        reclaimed = self.table4()["d1"]
        closing = (opening + added + manual - reclaimed).rounded()
        carried = sum_tax(item.tax for item in self.closing_pending_2b
                          if item.carried and not _credit_like(item.doc))
        register = (carried + added).rounded()
        return {"opening": opening, "added": (added + manual).rounded(), "reclaimed": reclaimed,
                "closing": closing, "register": register,
                "difference": (closing - register - manual).rounded()}

    # ----------------------------------------------------------------- counts
    def counts(self) -> Dict[str, int]:
        return {
            "gstr2b_documents": len(self.rows_2b),
            "matched": sum(1 for r in self.rows_2b if r.pair is not None),
            "matched_clean": sum(1 for r in self.rows_2b
                                 if r.pair is not None and r.pair.status is ReconStatus.MATCHED),
            "differences": len(self.differences()),
            "not_in_books": sum(1 for r in self.rows_2b if r.book is None),
            "books_only": len(self.rows_books),
            "reclaimed": len(self.reclaimed()),
            "closing_books_deferred": len(self.closing_books_deferred),
            "closing_pending_2b": len(self.closing_pending_2b),
        }

    def expiry_exposure(self) -> Dict[str, Tax]:
        """ITC in the closing registers that is time-barred or about to be."""
        result = {"EXPIRED": ZERO, "EXPIRING": ZERO}
        for item in self.closing_books_deferred + self.closing_pending_2b:
            if item.deadline_status in result and not _credit_like(item.doc):
                result[item.deadline_status] = result[item.deadline_status] + item.tax
        return {key: value.rounded() for key, value in result.items()}

    def action_items(self) -> List[str]:
        """Plain-language follow-ups, most valuable first."""
        items: List[str] = []
        counts = self.counts()
        deferred = sum_tax(i.tax for i in self.closing_books_deferred if not _credit_like(i.doc))
        pending = sum_tax(i.tax for i in self.closing_pending_2b if not _credit_like(i.doc))
        expiry = self.expiry_exposure()
        if expiry["EXPIRED"].total > 0:
            items.append(
                f"Rs.{expiry['EXPIRED'].total:,.2f} of ITC in the closing registers is past its "
                f"section 16(4) time limit - it can no longer be claimed; write it off or "
                f"reverse it."
            )
        if expiry["EXPIRING"].total > 0:
            items.append(
                f"Rs.{expiry['EXPIRING'].total:,.2f} of ITC reaches its section 16(4) time limit "
                f"within about {EXPIRY_WARNING_DAYS // 30} months - chase these first."
            )
        if counts["closing_books_deferred"]:
            suppliers = len({i.doc.gstin_key for i in self.closing_books_deferred})
            items.append(
                f"Chase {suppliers} supplier(s) for {counts['closing_books_deferred']} booked "
                f"document(s) not in GSTR-2B - Rs.{deferred.total:,.2f} of ITC is deferred until "
                f"they file."
            )
        if counts["closing_pending_2b"]:
            items.append(
                f"Book the {counts['closing_pending_2b']} GSTR-2B document(s) still pending - "
                f"Rs.{pending.total:,.2f} was reversed under 4(B)(2) and is reclaimed under "
                f"4(D)(1) once they are in the books."
            )
        if self.settled_earlier and not self.opening_supplied:
            items.append(
                f"{len(self.settled_earlier)} books entr"
                f"{'y' if len(self.settled_earlier) == 1 else 'ies'} dated before "
                f"{self.period.long_label} were taken as dealt with in earlier returns because no "
                f"opening registers were given - list any that is still unclaimed in the opening "
                f"books-deferred register."
            )
        late = [r for r in self.rows_2b if CLAIM_PM == r.treatment and r.books_class == CLASS_PM]
        if late:
            items.append(
                f"{len(late)} document(s) were booked in an earlier month but are not in the "
                f"opening deferred register - confirm they were not already claimed."
            )
        wrong = [r for r in self.rows_2b
                 if r.pair is not None and FLAG_GSTIN_MISMATCH in r.pair.flags]
        if wrong:
            items.append(
                f"{len(wrong)} document(s) are booked under a different supplier GSTIN than "
                f"GSTR-2B has them - correct the supplier in the books."
            )
        notes = self.credit_notes_not_in_books()
        if notes:
            items.append(
                f"{len(notes)} credit note(s) in GSTR-2B are not in the books - record the "
                f"purchase return, or reject them in IMS if they are not yours."
            )
        if counts["differences"]:
            items.append(
                f"Resolve {counts['differences']} matched document(s) that differ in value, "
                f"rate, tax head or date - see the Differences sheet."
            )
        ineligible = [r for r in self.rows_2b if r.treatment == INELIGIBLE]
        if ineligible:
            items.append(
                f"{len(ineligible)} GSTR-2B document(s) are marked ITC not available - "
                f"confirm none of it is claimed."
            )
        if self.rcm_2b or self.rcm_books:
            items.append(
                f"{len(self.rcm_2b) + len(self.rcm_books)} reverse-charge row(s) were kept out of "
                f"this match - reconcile them in the RCM workings."
            )
        if not items:
            items.append("No action required - the books and GSTR-2B agree and nothing is open.")
        return items


# ------------------------------------------------------------------ the builder


def _source_key(record: InvoiceRecord) -> Tuple[str, int]:
    """Where a document came from: its sheet and its first source row."""
    return (record.sheet, record.source_rows[0])


def _sheet_order(records: Sequence[InvoiceRecord]) -> Dict[str, int]:
    order: Dict[str, int] = {}
    for record in records:
        order.setdefault(record.sheet, len(order))
    return order


def _ref_2b(record: InvoiceRecord, first_sheet: str, period: Period) -> str:
    """The firm's 'unique GSTR-2B number': the line's serial and the period."""
    serial = record.serial or record.row_id
    prefix = "" if record.sheet in (first_sheet, "") else f"{record.sheet[:10]}-"
    return f"{prefix}{serial} {period.label}"


def _ref_books(record: InvoiceRecord) -> str:
    return record.voucher_no or record.invoice_no or f"row {record.row_id}"


def _voucher_identity(record: InvoiceRecord) -> Optional[Tuple[str, str, str, float, float]]:
    """
    What makes two books rows the same voucher: supplier, voucher (or supplier document)
    number, sign and amounts. None for a row with no number at all, which cannot be told
    apart from its neighbours.
    """
    number = normalise_invoice_no(record.voucher_no or record.invoice_no)
    if not number or not record.gstin_key:
        return None
    return (record.gstin_key, record.sign_class, number, round(record.taxable_value, 2),
            round(record.total_tax, 2))


def _without_carried_vouchers(
    file_books: Sequence[InvoiceRecord],
    carried: Sequence[InvoiceRecord],
) -> Tuple[List[InvoiceRecord], int]:
    """
    Drop from the books file the vouchers the opening register already holds.

    A register kept for the whole year contains its own deferred vouchers, and the
    opening register is cut from it. Left in twice, one voucher could pair with two
    different GSTR-2B documents - and the copy that knows it was already reversed
    (the register's) must be the one that counts.
    """
    held = {key for key in map(_voucher_identity, aggregate_records(carried)) if key}
    repeated = []
    for doc in aggregate_records(file_books):
        key = _voucher_identity(doc)
        if key is not None and key in held:
            repeated.append(doc)
    if not repeated:
        return list(file_books), 0
    drop = {(doc.sheet, row) for doc in repeated for row in doc.source_rows}
    return [b for b in file_books if (b.sheet, b.row_id) not in drop], len(repeated)


def build_workpaper(
    gstr2b_records: Sequence[InvoiceRecord],
    book_records: Sequence[InvoiceRecord],
    period: Period,
    opening: Optional[OpeningItems] = None,
    reconciler: Optional[GSTR2BReconciler] = None,
    cn_policy: str = "books",
    client: str = "",
    gstin: str = "",
    read_notes: Sequence[str] = (),
) -> Workpaper:
    """
    Run the month: match, tag every document, and roll the registers forward.

    `cn_policy` is 'books' (reverse ITC on a purchase return in the month it is
    booked, even before the supplier's credit note reaches GSTR-2B) or '2b'
    (only when it does).
    """
    if cn_policy not in CN_POLICIES:
        raise ValueError(f"cn_policy must be one of {CN_POLICIES}")
    opening = opening or OpeningItems()
    reconciler = reconciler or GSTR2BReconciler()
    if reconciler.book_priority is None:
        # A voucher the register already tracks as waiting for its credit note has first
        # claim on a GSTR-2B document; an equal-value voucher that is not tracked comes after.
        reconciler = GSTR2BReconciler(
            reconciler.date_tolerance_days, reconciler.taxable_tolerance,
            reconciler.tax_tolerance,
            book_priority=lambda book: 0 if book.sheet == OPENING_BOOKS_SHEET else 1)
    work = Workpaper(
        period=period, client=client, gstin=gstin, cn_policy=cn_policy,
        date_tolerance_days=reconciler.date_tolerance_days,
        taxable_tolerance=reconciler.taxable_tolerance,
        tax_tolerance=reconciler.tax_tolerance,
        opening_books_deferred=list(opening.books_deferred),
        opening_pending_2b=list(opening.pending_2b),
    )
    work.notes += list(read_notes) + list(opening.notes)

    portal, work.rcm_2b, rcm_notes_2b = split_reverse_charge(gstr2b_records, SOURCE_GSTR2B)
    file_books, work.rcm_books, rcm_notes_books = split_reverse_charge(book_records, SOURCE_BOOKS)
    work.notes += rcm_notes_2b + rcm_notes_books

    file_books, repeated = _without_carried_vouchers(file_books, opening.books_deferred)
    if repeated:
        work.notes.append(
            f"BOOKS: {repeated} voucher(s) in the books file are also in the opening "
            f"books-deferred register; each is counted once, from the register."
        )
    dated = any(record.booking_date for record in file_books)
    if file_books and not dated:
        work.notes.append(
            f"BOOKS: no voucher-date column was found, so every row is treated as booked in "
            f"{period.long_label}."
        )

    portal_docs = aggregate_records(portal)
    work.warnings += find_duplicate_documents(portal_docs)
    work.warnings += find_duplicate_documents(aggregate_records(file_books))

    # 1. GSTR-2B against this month's books plus the deferred register
    first = reconciler.reconcile(portal_docs, list(file_books) + list(opening.books_deferred))
    pair_by_portal = {id(pair.gstr2b): pair for pair in first.matched}
    sheets = _sheet_order(portal_docs)
    first_sheet = min(sheets, key=lambda name: sheets[name]) if sheets else ""

    for doc in first.gstr2b_documents:
        pair = pair_by_portal.get(id(doc))
        book = pair.books if pair else None
        cls = books_class(book, period, dated) if book is not None else CLASS_NONE
        treatment, notes = _treatment_2b(doc, book, cls)
        if pair is not None and pair.flags:
            notes.append("Match flags: " + pair.flag_text)
        if pair is not None and FLAG_GSTIN_MISMATCH in pair.flags:
            notes.append(f"The books carry this under GSTIN {book.gstin_key}, GSTR-2B under "
                         f"{doc.gstin_key}: matched on number, date and amounts - correct the "
                         f"supplier in the books.")
        work.rows_2b.append(Row2B(_ref_2b(doc, first_sheet, period), doc, pair, book, cls,
                                  treatment, notes))
    work.rows_2b.sort(key=lambda r: (sheets.get(r.doc.sheet, 0), r.doc.serial or r.doc.row_id,
                                     r.doc.row_id))

    # 2. books documents GSTR-2B does not carry: do they reclaim an earlier pending line?
    leftovers = list(first.only_in_books)
    eligible = [b for b in leftovers if b.sheet != OPENING_BOOKS_SHEET and b.gstin_key
                and books_class(b, period, dated) in (CLASS_CM, CLASS_PM)]
    second = reconciler.reconcile(list(opening.pending_2b), eligible)
    # reconcile() hands back its own copies of the documents, so they are found again
    # by where they came from rather than by object identity
    reclaim_of = {_source_key(pair.books): pair.gstr2b for pair in second.matched}

    for book in leftovers:
        if book.sheet == OPENING_BOOKS_SHEET:
            continue                              # still open: carried forward below
        cls = books_class(book, period, dated)
        pending = reclaim_of.get(_source_key(book))
        if cls == CLASS_PM and pending is None:
            # Booked in an earlier month, not in this month's GSTR-2B, reclaiming nothing and
            # not in the opening register: it was dealt with in its own month's return. (A
            # register kept for the whole year is full of these; counting them again would
            # reverse every earlier purchase return a second time.)
            work.settled_earlier.append(book)
            continue
        treatment, notes = _treatment_books(book, cls, pending, cn_policy)
        work.rows_books.append(RowBooks(_ref_books(book), book, cls, treatment, pending, notes))
    work.rows_books.sort(key=lambda r: (r.doc.gstin_key, r.doc.booking_date or date.min,
                                        r.doc.sheet, r.doc.row_id))
    work.opening_supplied = opening.supplied
    if work.settled_earlier:
        returns = sum(1 for b in work.settled_earlier if _credit_like(b))
        net = sum_tax(Tax.of(b) for b in work.settled_earlier).total
        count = len(work.settled_earlier)
        work.notes.append(
            f"BOOKS: {count} entr{'y' if count == 1 else 'ies'}"
            f" dated before {period.long_label} ({returns} of them purchase returns, net ITC "
            f"Rs.{net:,.2f}) are not in this GSTR-2B, reclaim nothing and are not in the opening "
            f"books-deferred register: they are taken as dealt with in the returns of their own "
            f"months and left out of this workpaper."
        )

    # 3. closing registers
    still_open_books = [b for b in leftovers if b.sheet == OPENING_BOOKS_SHEET]
    for book in still_open_books:
        work.closing_books_deferred.append(make_open_item(
            KIND_BOOKS_DEFERRED, book, period, True, book.taken_in_3b))
    for row in work.rows_books:
        if row.treatment in (DEFER_BOOKS, CN_HOLD) or (row.treatment == CN_TAKEN
                                                       and row.pending is None):
            booked = row.doc.booking_date
            booked_in = Period(booked.year, booked.month) if booked else period
            work.closing_books_deferred.append(make_open_item(
                KIND_BOOKS_DEFERRED, row.doc, period, False, row.treatment == CN_TAKEN,
                "; ".join(row.notes), origin=booked_in))
    for pending in second.only_in_gstr2b:
        work.closing_pending_2b.append(make_open_item(KIND_PENDING_2B, pending, period, True))
    for row in work.rows_2b:
        if row.treatment == REVERSE_4B2:
            work.closing_pending_2b.append(make_open_item(
                KIND_PENDING_2B, row.doc, period, False, note="; ".join(row.notes)))
    return work


def _treatment_2b(
    doc: InvoiceRecord,
    book: Optional[InvoiceRecord],
    cls: str,
) -> Tuple[str, List[str]]:
    """The default treatment of one GSTR-2B document, with any review notes."""
    notes: List[str] = []
    if _credit_like(doc):
        if book is not None and cls == CLASS_OPENING and book.taken_in_3b:
            return CN_ALREADY, ["Already reversed from the books in an earlier return."]
        if book is None:
            notes.append("Credit note not in the books: record the purchase return, or reject "
                         "it in IMS if it is not yours. Reversed here until then.")
        return CN_REVERSE, notes
    if not doc.itc_available:
        notes.append("GSTR-2B: ITC not available" + (f" - {doc.reason}" if doc.reason else ""))
        return INELIGIBLE, notes
    if book is None:
        notes.append("Not in the books: reverse under 4(B)(2); reclaim when booked.")
        return REVERSE_4B2, notes
    if cls == CLASS_CM:
        return CLAIM, notes
    if cls == CLASS_OPENING:
        notes.append("Was deferred in the books; claimed now that it is in GSTR-2B.")
        return CLAIM_PM, notes
    if cls == CLASS_PM:
        notes.append("Booked in an earlier month but not in the opening deferred register - "
                     "confirm it was not claimed then.")
        return CLAIM_PM, notes
    notes.append("Booked after the end of the period: reverse under 4(B)(2); reclaim next month.")
    return REVERSE_4B2, notes


def _treatment_books(
    book: InvoiceRecord,
    cls: str,
    pending: Optional[InvoiceRecord],
    cn_policy: str,
) -> Tuple[str, List[str]]:
    """The default treatment of one books document GSTR-2B does not carry."""
    notes: List[str] = []
    if not book.gstin_key:
        return NO_ITC, ["No supplier GSTIN: unregistered purchase, no ITC."]
    if cls == CLASS_FUTURE:
        return NOT_THIS_PERIOD, ["Booked after the end of the period."]
    if _credit_like(book):
        if pending is not None:
            notes.append("Matches a credit note pending in an earlier GSTR-2B: reversed now.")
            return CN_TAKEN, notes
        if cn_policy == "books":
            notes.append("Purchase return reversed from the books; the supplier's credit note "
                         "is not in GSTR-2B yet.")
            return CN_TAKEN, notes
        notes.append("Purchase return held: the supplier's credit note is not in GSTR-2B yet.")
        return CN_HOLD, notes
    if pending is not None:
        notes.append("Matches a document pending from an earlier GSTR-2B: reclaim under 4(D)(1).")
        if pending.gstin_key != book.gstin_key:
            notes.append(f"Booked under GSTIN {book.gstin_key}, GSTR-2B has it under "
                         f"{pending.gstin_key}: correct the supplier in the books.")
        return RECLAIM_4D1, notes
    if cls == CLASS_PM:
        notes.append("Booked in an earlier month and still not in GSTR-2B; not in the opening "
                     "deferred register.")
    else:
        notes.append("Booked but not in GSTR-2B: deferred.")
    return DEFER_BOOKS, notes


# ------------------------------------------------------------- carry-forward CSV

CARRY_COLUMNS: Tuple[str, ...] = (
    "GSTIN", "Supplier Name", "Invoice No", "Invoice Date", "Booking Date", "Voucher No",
    "Document Type", "Taxable Value", "IGST", "CGST", "SGST", "Cess", "Origin Period",
    "Taken In 3B", "Age (months)", "ITC Time Limit", "Time Limit Status", "Remarks",
)


def _carry_row(item: OpenItem) -> List[str]:
    doc = item.doc
    kind = {DocumentType.CREDIT_NOTE: "Credit Note", DocumentType.DEBIT_NOTE: "Debit Note"}
    return [
        doc.gstin_key, doc.supplier_name, doc.invoice_no,
        doc.invoice_date.isoformat() if doc.invoice_date else "",
        doc.booking_date.isoformat() if doc.booking_date else "",
        doc.voucher_no, kind.get(doc.document_type, "Invoice"),
        f"{doc.taxable_value:.2f}", f"{doc.igst:.2f}", f"{doc.cgst:.2f}", f"{doc.sgst:.2f}",
        f"{doc.cess:.2f}", item.origin.label if item.origin else "",
        "Yes" if item.taken_in_3b else "", str(item.age_months),
        item.deadline.isoformat() if item.deadline else "", item.deadline_status, item.note,
    ]


def write_carry_forward(work: Workpaper, outdir: str) -> List[str]:
    """
    Write the two closing registers as CSV - next month's opening files. They carry
    the same headings the readers recognise, so they can be handed back unchanged.
    """
    os.makedirs(outdir, exist_ok=True)
    written = []
    for name, items in ((CARRY_BOOKS_FILE, work.closing_books_deferred),
                        (CARRY_2B_FILE, work.closing_pending_2b)):
        path = os.path.join(outdir, name)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CARRY_COLUMNS)
            for item in sorted(items, key=lambda i: (i.doc.gstin_key, i.doc.invoice_no,
                                                     i.doc.voucher_no)):
                writer.writerow(_carry_row(item))
        written.append(path)
    return written


# -------------------------------------------------------------------------- demo


def _demo_records() -> Tuple[List[InvoiceRecord], List[InvoiceRecord], OpeningItems]:
    gstin_a, gstin_b, gstin_c, gstin_d, gstin_f = (
        "19AAAAA1111A1Z1", "27BBBBB2222B1Z2", "24CCCCC3333C1Z3", "07DDDDD4444D1Z4",
        "29FFFFF6666F1Z6")

    def doc(source, row, gstin, number, when, taxable, igst=0.0, cgst=0.0, sgst=0.0, **kw):
        return InvoiceRecord(source=source, row_id=row, serial=row, gstin=gstin,
                             invoice_no=number, invoice_date=when, taxable_value=taxable,
                             igst=igst, cgst=cgst, sgst=sgst, **kw)

    two_b = [
        doc(SOURCE_GSTR2B, 1, gstin_a, "A/101", date(2026, 9, 3), 100000, cgst=2500, sgst=2500),
        doc(SOURCE_GSTR2B, 2, gstin_b, "B-77", date(2026, 9, 5), 50000, igst=2500),
        doc(SOURCE_GSTR2B, 3, gstin_c, "C/9", date(2026, 9, 9), 40000, igst=2000),
        doc(SOURCE_GSTR2B, 4, gstin_d, "D-12", date(2026, 8, 28), 20000, igst=1000),
        doc(SOURCE_GSTR2B, 5, gstin_a, "CN-4", date(2026, 9, 12), 10000, cgst=250, sgst=250,
            document_type=DocumentType.CREDIT_NOTE),
    ]
    books = [
        # booked and in GSTR-2B: claim
        doc(SOURCE_BOOKS, 2, gstin_a, "A/101", date(2026, 9, 3), 100000, cgst=2500, sgst=2500,
            booking_date=date(2026, 9, 4)),
        doc(SOURCE_BOOKS, 3, gstin_b, "B-77", date(2026, 9, 5), 50000, igst=2500,
            booking_date=date(2026, 9, 6)),
        # booked, supplier has not filed: defer
        doc(SOURCE_BOOKS, 4, "36EEEEE5555E1Z5", "E-5", date(2026, 9, 20), 8000, igst=400,
            booking_date=date(2026, 9, 21)),
        # the pending GSTR-2B line from August is booked now: reclaim
        doc(SOURCE_BOOKS, 5, gstin_f, "F-3", date(2026, 8, 10), 30000, igst=1500,
            booking_date=date(2026, 9, 18)),
        # a purchase return with no supplier reference: matched to the credit note on value
        doc(SOURCE_BOOKS, 6, gstin_a, "", None, 10000, cgst=250, sgst=250, voucher_no="DN/01",
            document_type=DocumentType.CREDIT_NOTE, booking_date=date(2026, 9, 14)),
    ]
    opening = OpeningItems(
        # booked in August, deferred then; the supplier's invoice is in September's GSTR-2B
        books_deferred=_tag_opening([
            doc(SOURCE_BOOKS, 2, gstin_d, "D-12", date(2026, 8, 28), 20000, igst=1000,
                booking_date=date(2026, 8, 30)),
        ], OPENING_BOOKS_SHEET),
        pending_2b=_tag_opening([
            doc(SOURCE_GSTR2B, 7, gstin_f, "F-3", date(2026, 8, 10), 30000, igst=1500,
                origin_period="Aug-26"),
        ], OPENING_2B_SHEET),
    )
    return two_b, books, opening


def _run_demo() -> None:
    two_b, books, opening = _demo_records()
    work = build_workpaper(two_b, books, Period(2026, 9), opening, client="Demo Traders")
    table = work.table4()
    bridge = dict(work.bridge())
    print(f"Period {work.period.label} - F.Y. {work.period.fy_label}")
    for key, label in (("a5", "4(A)(5) all other ITC"), ("b2", "4(B)(2) reversed"),
                       ("d1", "4(D)(1) reclaimed"), ("net", "4(C) net ITC")):
        print(f"  {label:<26}{table[key].total:>12,.2f}")
    print(f"  ITC per books              {bridge['books'].total:>12,.2f}")
    print(f"  Bridge difference          {bridge['difference'].total:>12,.2f}")
    print(f"  Closing: {len(work.closing_books_deferred)} books deferred, "
          f"{len(work.closing_pending_2b)} 2B pending")


if __name__ == "__main__":
    _run_demo()
