#!/usr/bin/env python3
"""
Finance Hack: GSTR-2B vs Books (purchase register) reconciliation - fully offline.

Nothing leaves the machine. The module imports only the Python standard
library, opens no sockets and calls no service: the two files are read from
disk, matched locally and the report is written back to disk. The matching is
deterministic, so the same two inputs always produce the same output, which is
what makes the result usable as an audit work-paper.

Inputs
    GSTR-2B : the B2B sheet of the GSTR-2B download from the GST portal
              (.xlsx) or any CSV with comparable columns.
    Books   : the purchase register exported from the accounting package
              (Tally Prime, Busy, Zoho, SAP, ...) as CSV or .xlsx.

Both files are read with flexible header detection and column aliasing, so the
exports do not have to be reshaped by hand first. Multi-line documents are
summed before matching - GSTR-2B splits an invoice rate-wise and a purchase
register splits it across expense ledgers, so the two sides are only
comparable document by document.

Matching runs per supplier GSTIN in four ordered passes, strongest first:

    1. exact invoice number
    2. normalised invoice number        INV/001 == INV-1 == inv 001
    3. trailing number of the invoice   guarded by date + value tolerance
    4. taxable value + invoice date     invoice number unusable

Every pass is one-to-one: a document already matched is never reused, and
candidates are ranked by (date gap, value gap, tax gap, source row) so the
result never depends on dict ordering or on how the file happened to be sorted.

Outcomes
    MATCHED                 - agrees within tolerance
    MATCHED_WITH_DIFFERENCE - same document, value / tax / head / date differs
    ONLY_IN_GSTR2B          - supplier reported it, books have not booked it
    ONLY_IN_BOOKS           - booked, supplier has not reported it

ITC is reported on the conservative section 16(2)(aa) basis: for a matched
document only the tax actually reflected in GSTR-2B is treated as claimable,
and anything booked over and above that is reported as ITC at risk. Credit
notes are normalised to negative amounts on both sides, so they reduce credit.

Educational/demo module - review the figures before relying on them for a
filing.
"""

import argparse
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Indicative review tolerances. Dates drift by a day or two between the
# supplier's books and ours; small value gaps are usually rounding.
DEFAULT_DATE_TOLERANCE_DAYS = 3
DEFAULT_TAXABLE_TOLERANCE = 100.0
DEFAULT_TAX_TOLERANCE = 10.0

SOURCE_GSTR2B = "GSTR2B"
SOURCE_BOOKS = "BOOKS"

# Difference flags recorded against a matched pair.
FLAG_TAXABLE_VALUE_DIFF = "TAXABLE_VALUE_DIFF"
FLAG_TAX_AMOUNT_DIFF = "TAX_AMOUNT_DIFF"
FLAG_TAX_HEAD_DIFF = "TAX_HEAD_DIFF"
FLAG_INVOICE_DATE_DIFF = "INVOICE_DATE_DIFF"
FLAG_INVOICE_NO_FORMAT_DIFF = "INVOICE_NO_FORMAT_DIFF"
FLAG_INVOICE_NO_MISMATCH = "INVOICE_NO_MISMATCH"
FLAG_DOCUMENT_TYPE_DIFF = "DOCUMENT_TYPE_DIFF"
FLAG_ITC_NOT_AVAILABLE = "ITC_NOT_AVAILABLE"
FLAG_MISSING_DATE = "MISSING_DATE"
FLAG_RATE_DIFFERENCE = "RATE_DIFFERENCE"
FLAG_NO_DOC_REFERENCE = "NO_DOC_REFERENCE"

# Flags that make a matched pair a difference rather than a clean match. A
# pure invoice-number format difference is informational: the amounts, the
# date and the tax heads all agree, so the credit is not in question.
DIFFERENCE_FLAGS = frozenset({
    FLAG_TAXABLE_VALUE_DIFF,
    FLAG_TAX_AMOUNT_DIFF,
    FLAG_TAX_HEAD_DIFF,
    FLAG_INVOICE_DATE_DIFF,
    FLAG_INVOICE_NO_MISMATCH,
    FLAG_DOCUMENT_TYPE_DIFF,
    FLAG_ITC_NOT_AVAILABLE,
    FLAG_RATE_DIFFERENCE,
    FLAG_NO_DOC_REFERENCE,
})

_EXCEL_EPOCH = date(1899, 12, 30)
_EXCEL_MAX_SERIAL = 2958465  # 9999-12-31
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class ReconStatus(Enum):
    """Reconciliation outcome for one document or matched pair."""
    MATCHED = "MATCHED"
    MATCHED_WITH_DIFFERENCE = "MATCHED_WITH_DIFFERENCE"
    ONLY_IN_GSTR2B = "ONLY_IN_GSTR2B"
    ONLY_IN_BOOKS = "ONLY_IN_BOOKS"


class MatchBasis(Enum):
    """How a pair was matched - kept for the audit trail."""
    EXACT_INVOICE_NO = "EXACT_INVOICE_NO"
    NORMALISED_INVOICE_NO = "NORMALISED_INVOICE_NO"
    INVOICE_TRAILING_NUMBER = "INVOICE_TRAILING_NUMBER"
    TAXABLE_VALUE_AND_DATE = "TAXABLE_VALUE_AND_DATE"
    SUPPLIER_VALUE_ONLY = "SUPPLIER_VALUE_ONLY"


class DocumentType(Enum):
    """Document class of a purchase record."""
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"


_NON_ALNUM = re.compile(r"[^0-9A-Z]+")
_GSTIN_SHAPE = re.compile(r"^[0-9]{2}[0-9A-Z]{13}$")
_LEADING_ZEROS = re.compile(r"0*(\d+)")
_TRAILING_DIGITS = re.compile(r"(\d+)(?!.*\d)")
_FIRST_DIGIT = re.compile(r"[0-9]")
_NUMBER_BODY = re.compile(r"[0-9,.]*")
_HEADER_NOISE = re.compile(r"[^a-z0-9]+")

_BLANK_AMOUNTS = {"-", "--", "NA", "N/A", "NIL", "NONE", "."}

_DATE_FORMATS = (
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
    "%d-%m-%y", "%d/%m/%y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d-%b-%Y", "%d/%b/%Y", "%d %b %Y",
    "%d-%b-%y", "%d %b %y",
    "%d-%B-%Y", "%d %B %Y",
)

_CREDIT_NOTE_HINTS = ("CREDIT", "CRN", "C/N", "CR NOTE")
_DEBIT_NOTE_HINTS = ("DEBIT", "DRN", "D/N", "DR NOTE")
# On the books side a "Debit Note" (Tally) or "Credit Note"/"Vendor Credit"
# (Zoho and others) in a purchase register is a purchase return. Bare words such
# as "Credit" are deliberately not enough: "Credit Purchase" is a purchase.
_BOOKS_RETURN = re.compile(
    r"(DEBIT|CREDIT)[\s_]*NOTE|(DR|CR)\.?\s*NOTE|PURCHASE[\s_]*RETURN|\bRETURNS?\b|\b(DN|CN)\b"
)
_YES = {"Y", "YES", "TRUE", "1", "T", "RCM", "REVERSE CHARGE"}
_SCIENTIFIC = re.compile(r"^\d+(\.\d+)?[eE][+-]?\d+$")
_ITC_NOT_AVAILABLE = {"N", "NO", "FALSE", "0", "NOT AVAILABLE", "INELIGIBLE", "NOT ELIGIBLE"}


def normalise_gstin(value: str) -> str:
    """Upper-case a GSTIN and drop separators and whitespace."""
    return _NON_ALNUM.sub("", (value or "").upper())


def _as_credit(value: float) -> float:
    """Sign an amount negative, keeping zero as a plain zero (not -0.0)."""
    return -abs(value) if value else 0.0


def looks_like_gstin(value: str) -> bool:
    """
    True when the text has the shape of a GSTIN/UIN: fifteen alphanumerics
    behind a two-digit state code. Deliberately tolerant - it is used to tell
    a supplier row from a 'Total' row, not to validate the check digit.
    """
    return bool(_GSTIN_SHAPE.match(normalise_gstin(value)))


def normalise_invoice_no(value: str) -> str:
    """
    Collapse an invoice number into a comparison key.

    Case and punctuation are dropped and the leading zeros inside every run of
    digits are removed, so 'INV/001', 'inv-001' and 'INV 1' share one key.
    This is what catches the classic 'INV/001 in Tally vs INV-1 on the portal'
    break without any guessing.
    """
    cleaned = _NON_ALNUM.sub("", (value or "").upper())
    return _LEADING_ZEROS.sub(lambda match: match.group(1), cleaned)


# A financial-year stamp inside an invoice number: '25-26', '2025-26', '2025/2026'.
_FY_STAMP = re.compile(r"(?<![0-9])(20\d{2}|\d{2})\s*[-/]\s*(20\d{2}|\d{2})(?![0-9])")


def _without_fy_stamps(value: str) -> str:
    """Blank out the financial-year stamps (two consecutive years) in an invoice number."""
    def blank(match: "re.Match[str]") -> str:
        first, second = int(match.group(1)[-2:]), int(match.group(2)[-2:])
        return " " if second == (first + 1) % 100 and 14 <= first <= 45 else match.group(0)
    return _FY_STAMP.sub(blank, value)


def invoice_trailing_number(value: str) -> str:
    """
    Return the last run of digits in an invoice number, zeros stripped, leaving out any
    financial-year stamp: 'ABC/25-26/54' and '0054/25-26' both end in 54, not in 26.
    """
    text = value or ""
    match = _TRAILING_DIGITS.search(_without_fy_stamps(text)) or _TRAILING_DIGITS.search(text)
    return str(int(match.group(1))) if match else ""


def parse_amount(value) -> float:
    """
    Read a money cell as a float.

    Handles thousands separators (Indian or Western grouping), a currency
    prefix such as 'Rs.' or the rupee sign, blank cells and the accounting
    '(1,234.00)' negative form. The sign of a credit note comes from its
    document type, not from a 'Cr'/'Dr' suffix, which is ignored. A cell that
    cannot be read is treated as zero rather than aborting a 5,000-row file;
    such rows are counted in the data-quality notes.
    """
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.upper() in _BLANK_AMOUNTS:
        return 0.0
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    compact = text.replace(" ", "")
    first_digit = _FIRST_DIGIT.search(compact)
    if first_digit is None:
        return 0.0
    start = first_digit.start()
    if start > 0 and compact[start - 1] == "." and not compact[max(0, start - 2)].isalpha():
        # A bare '.50', but not the full stop in a 'Rs.50' prefix.
        start -= 1
    cleaned = _NUMBER_BODY.match(compact[start:]).group(0).replace(",", "")
    try:
        amount = float(cleaned)
    except ValueError:
        return 0.0
    return -amount if negative else amount


def parse_date(value) -> Optional[date]:
    """
    Read a date cell.

    Day-first is assumed ('01-06-2025' is 1 June 2025), which is how the GST
    portal and Indian accounting packages export dates. Bare Excel date
    serials - what a raw .xlsx read returns for a formatted date cell - are
    converted from the 1900 epoch. Returns None when nothing parses.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        text = text.replace("T", " ").split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        serial = float(text.replace(",", ""))
    except ValueError:
        return None
    if 1 <= serial <= _EXCEL_MAX_SERIAL:
        return _EXCEL_EPOCH + timedelta(days=int(serial))
    return None


def parse_document_type(value: str, books: bool = False) -> DocumentType:
    """
    Classify a document-type cell; blank reads as a tax invoice.

    The portal's wording is taken at face value: a Credit Note reduces ITC, a
    Debit Note raises it. On the books side any purchase-return voucher reduces
    ITC, whatever it is called, so it is classed as a credit note - that is the
    supplier-side document it will be matched against in GSTR-2B.
    """
    text = (value or "").strip().upper()
    if not text:
        return DocumentType.INVOICE
    if books:
        return DocumentType.CREDIT_NOTE if _BOOKS_RETURN.search(text) else DocumentType.INVOICE
    if text == "C" or any(hint in text for hint in _CREDIT_NOTE_HINTS):
        return DocumentType.CREDIT_NOTE
    if text == "D" or any(hint in text for hint in _DEBIT_NOTE_HINTS):
        return DocumentType.DEBIT_NOTE
    return DocumentType.INVOICE


def parse_yes_no(value) -> bool:
    """Read a Yes/No style cell (reverse charge flags); blank is No."""
    return str(value or "").strip().upper() in _YES


def _flag_text(value: str) -> bool:
    """
    A free-text flag column: any note counts as Yes ('CN Books taken in May-26 3B'),
    except an explicit No or an empty cell.
    """
    return bool(value) and value.strip().upper() not in ("NO", "N", "FALSE", "0", "-")


def clean_doc_number(value) -> str:
    """
    Tidy a document-number cell read from a spreadsheet.

    Excel hands back numeric invoice numbers as floats ('385.0') or, for long
    ones, in scientific notation ('7.4069601131831E+13'). Both are turned back
    into the plain digits the portal shows. Text numbers are left untouched
    apart from surrounding whitespace.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    if _SCIENTIFIC.match(text):
        try:
            number = Decimal(text)
        except InvalidOperation:
            return text
        if number == number.to_integral_value():
            return str(int(number))
    return text


def parse_itc_available(value: str) -> bool:
    """Read the GSTR-2B 'ITC Availability' cell; blank reads as available."""
    return (value or "").strip().upper() not in _ITC_NOT_AVAILABLE


@dataclass
class InvoiceRecord:
    """One purchase document, from GSTR-2B or from the books."""
    source: str
    row_id: int
    sheet: str = ""
    gstin: str = ""
    supplier_name: str = ""
    invoice_no: str = ""
    invoice_date: Optional[date] = None
    taxable_value: float = 0.0
    igst: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    cess: float = 0.0
    document_type: DocumentType = DocumentType.INVOICE
    itc_available: bool = True
    line_count: int = 1
    source_rows: List[int] = field(default_factory=list)
    booking_date: Optional[date] = None
    voucher_no: str = ""
    voucher_type: str = ""
    rcm: bool = False
    reason: str = ""
    serial: int = 0
    ref: str = ""
    gstr1_period: str = ""
    gstr1_filing_date: Optional[date] = None
    period_ref: str = ""
    origin_period: str = ""
    taken_in_3b: bool = False
    reclaimed: str = ""
    remarks: str = ""
    voucher_nos: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.source_rows:
            self.source_rows = [self.row_id]
        if self.voucher_no and not self.voucher_nos:
            self.voucher_nos = [self.voucher_no]
        if self.document_type is DocumentType.CREDIT_NOTE:
            # A credit note reduces credit, so it is held as negative amounts
            # whichever sign the export used. Idempotent, so re-wrapping an
            # already-negative record changes nothing.
            self.taxable_value = _as_credit(self.taxable_value)
            self.igst = _as_credit(self.igst)
            self.cgst = _as_credit(self.cgst)
            self.sgst = _as_credit(self.sgst)
            self.cess = _as_credit(self.cess)

    @property
    def total_tax(self) -> float:
        return round(self.igst + self.cgst + self.sgst + self.cess, 2)

    @property
    def document_value(self) -> float:
        return round(self.taxable_value + self.total_tax, 2)

    @property
    def gstin_key(self) -> str:
        return normalise_gstin(self.gstin)

    @property
    def exact_key(self) -> str:
        return (self.invoice_no or "").strip().upper()

    @property
    def invoice_key(self) -> str:
        return normalise_invoice_no(self.invoice_no)

    @property
    def trailing_number(self) -> str:
        return invoice_trailing_number(self.invoice_no)

    @property
    def identity_key(self) -> str:
        """
        What makes this row the same document as another row.

        The supplier's document number when there is one. A document with no
        number (a Tally debit note carries only its own voucher number) falls
        back to that voucher number, and failing that to its row, so that
        several number-less documents from one supplier are never merged. A
        purchase return in the books is its voucher number even when it cites an
        invoice: several returns against one invoice share the cited number and
        are still separate documents.
        """
        if self.voucher_no and self.source == SOURCE_BOOKS and self.sign_class == "C":
            return "V:" + self.voucher_no.strip().upper()
        if self.invoice_key:
            return self.invoice_key
        if self.voucher_no:
            return "V:" + self.voucher_no.strip().upper()
        return f"R:{self.sheet}:{self.row_id}"

    @property
    def sign_class(self) -> str:
        """'C' for an ITC-reducing document, 'P' for an ITC-bearing one."""
        credit = self.document_type is DocumentType.CREDIT_NOTE
        return "C" if credit or self.taxable_value < 0 or self.total_tax < 0 else "P"

    @property
    def label(self) -> str:
        """Short identifier used in report lines."""
        when = self.invoice_date.isoformat() if self.invoice_date else "date?"
        return f"{self.gstin_key or 'GSTIN?'} {self.invoice_no or 'inv?'} {when}"

    def sort_key(self) -> Tuple[str, str, str, str, int]:
        """Stable ordering for reports: supplier, then date, then number."""
        when = self.invoice_date.isoformat() if self.invoice_date else "9999-12-31"
        return (self.gstin_key, when, self.exact_key, self.sheet, self.row_id)


def _merge_into(existing: InvoiceRecord, record: InvoiceRecord) -> None:
    """Fold another line of the same document into `existing`."""
    existing.taxable_value = round(existing.taxable_value + record.taxable_value, 2)
    existing.igst = round(existing.igst + record.igst, 2)
    existing.cgst = round(existing.cgst + record.cgst, 2)
    existing.sgst = round(existing.sgst + record.sgst, 2)
    existing.cess = round(existing.cess + record.cess, 2)
    existing.line_count += record.line_count
    existing.source_rows.extend(record.source_rows)
    existing.itc_available = existing.itc_available and record.itc_available
    existing.rcm = existing.rcm or record.rcm
    existing.taken_in_3b = existing.taken_in_3b or record.taken_in_3b
    for number in record.voucher_nos:
        if number not in existing.voucher_nos:
            existing.voucher_nos.append(number)
    for name in ("supplier_name", "voucher_no", "voucher_type", "reason", "ref", "gstr1_period",
                 "gstr1_filing_date", "period_ref", "origin_period", "reclaimed", "remarks",
                 "invoice_date"):
        if not getattr(existing, name):
            setattr(existing, name, getattr(record, name))
    if record.booking_date and (not existing.booking_date
                                or record.booking_date < existing.booking_date):
        existing.booking_date = record.booking_date
    if record.serial and (not existing.serial or record.serial < existing.serial):
        existing.serial = record.serial


def aggregate_records(records: Sequence[InvoiceRecord]) -> List[InvoiceRecord]:
    """
    Sum multi-line documents into one record each.

    Both sides routinely carry several rows per document: GSTR-2B splits an
    invoice rate-wise, and a purchase register splits it across expense
    ledgers (brokerage, consultancy, freight ...). Rows sharing supplier
    GSTIN, document identity, document type and date are added together so
    that the comparison is document to document, not line to line. Rows from
    different sheets are never merged: the same number on the B2B and the B2BA
    sheet, or in two monthly sheets of a register, is a duplicate to flag, not
    one document to double. First-seen order is preserved.
    """
    merged: Dict[Tuple[str, str, str, str, str], InvoiceRecord] = {}
    order: List[Tuple[str, str, str, str, str]] = []
    for record in records:
        key = (
            record.sheet,
            record.gstin_key,
            record.identity_key,
            record.document_type.value,
            record.invoice_date.isoformat() if record.invoice_date else "",
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = replace(
                record,
                source_rows=list(record.source_rows),
                voucher_nos=list(record.voucher_nos),
            )
            order.append(key)
            continue
        _merge_into(existing, record)
    return [merged[key] for key in order]


def find_duplicate_documents(records: Sequence[InvoiceRecord]) -> List[str]:
    """
    Flag likely double entries in one file.

    Two cases: the same supplier + invoice number appearing as more than one
    document (two dates for the same bill), and one document booked under
    more than one voucher. Returned sorted, so the warning list is stable. A purchase
    return in the books is left out of both: it cites the original invoice's number,
    and several returns against one invoice are normal.
    """
    groups: Dict[Tuple[str, str], List[InvoiceRecord]] = {}
    records = [r for r in records if not (r.source == SOURCE_BOOKS and r.sign_class == "C")]
    for record in records:
        if not record.gstin_key or not record.invoice_key:
            continue
        groups.setdefault((record.gstin_key, record.invoice_key), []).append(record)
    warnings = []
    for group in groups.values():
        if len(group) >= 2:
            rows = ", ".join(str(row) for record in group for row in record.source_rows)
            warnings.append(
                f"{group[0].source}: {group[0].gstin_key} invoice {group[0].invoice_no} "
                f"appears {len(group)} times (rows {rows})"
            )
    for record in records:
        if record.gstin_key and record.invoice_key and len(record.voucher_nos) > 1:
            warnings.append(
                f"{record.source}: {record.gstin_key} invoice {record.invoice_no} is booked "
                f"under {len(record.voucher_nos)} vouchers ({', '.join(record.voucher_nos)}) "
                f"- check for a double entry"
            )
    return sorted(warnings)


@dataclass
class MatchedPair:
    """A GSTR-2B document matched to a book document, with the differences."""
    gstr2b: InvoiceRecord
    books: InvoiceRecord
    basis: MatchBasis
    status: ReconStatus
    flags: List[str] = field(default_factory=list)
    taxable_diff: float = 0.0
    tax_diff: float = 0.0
    igst_diff: float = 0.0
    cgst_diff: float = 0.0
    sgst_diff: float = 0.0
    cess_diff: float = 0.0
    date_diff_days: Optional[int] = None
    itc_claimable: float = 0.0
    itc_at_risk: float = 0.0

    @property
    def flag_text(self) -> str:
        return ",".join(self.flags) if self.flags else "-"


class GSTR2BReconciler:
    """
    Deterministic GSTR-2B vs books matching engine.

    Tolerances are review thresholds, not law: a gap inside the tolerance is
    reported as a clean match, a gap outside it becomes a difference to
    investigate.
    """

    _PASSES: Tuple[Tuple[MatchBasis, bool], ...] = (
        (MatchBasis.EXACT_INVOICE_NO, False),
        (MatchBasis.NORMALISED_INVOICE_NO, False),
        (MatchBasis.INVOICE_TRAILING_NUMBER, True),
        (MatchBasis.TAXABLE_VALUE_AND_DATE, True),
        (MatchBasis.SUPPLIER_VALUE_ONLY, True),
    )

    def __init__(
        self,
        date_tolerance_days: int = DEFAULT_DATE_TOLERANCE_DAYS,
        taxable_tolerance: float = DEFAULT_TAXABLE_TOLERANCE,
        tax_tolerance: float = DEFAULT_TAX_TOLERANCE,
        book_priority: Optional[Callable[[InvoiceRecord], int]] = None,
    ) -> None:
        if date_tolerance_days < 0:
            raise ValueError("date_tolerance_days cannot be negative")
        if taxable_tolerance < 0:
            raise ValueError("taxable_tolerance cannot be negative")
        if tax_tolerance < 0:
            raise ValueError("tax_tolerance cannot be negative")
        self.date_tolerance_days = int(date_tolerance_days)
        self.taxable_tolerance = float(taxable_tolerance)
        self.tax_tolerance = float(tax_tolerance)
        # Optional: a books document with a lower number is assigned first within each
        # pass, so a caller can give some entries (a register of open items) first claim.
        self.book_priority = book_priority

    def reconcile(
        self,
        gstr2b_records: Sequence[InvoiceRecord],
        book_records: Sequence[InvoiceRecord],
    ) -> "ReconciliationResult":
        """Match both sides and return the classified result."""
        portal = aggregate_records(gstr2b_records)
        books = aggregate_records(book_records)
        used_portal: set = set()
        used_books: set = set()
        pairs: List[MatchedPair] = []

        for basis, strict in self._PASSES:
            index: Dict[Tuple[str, str, str], List[int]] = {}
            for position, record in enumerate(portal):
                if position in used_portal:
                    continue
                key = self._match_key(record, basis)
                if key is not None:
                    index.setdefault(key, []).append(position)
            # Collect every admissible (portal, book) candidate for this pass and
            # assign them best-first, so a book entry never takes a poorer match
            # merely because it comes earlier in the file.
            candidates: List[Tuple[Tuple, int, int]] = []
            for book_position, book in enumerate(books):
                if book_position in used_books:
                    continue
                key = self._match_key(book, basis)
                if key is None:
                    continue
                for position in index.get(key, []):
                    if strict and not self._admissible(portal[position], book, basis):
                        continue
                    priority = self.book_priority(book) if self.book_priority else 0
                    rank = ((priority,) + self._rank(portal[position], book, basis)
                            + (book.sheet, book.row_id))
                    candidates.append((rank, position, book_position))
            candidates.sort(key=lambda item: item[0])
            for _rank, position, book_position in candidates:
                if position in used_portal or book_position in used_books:
                    continue
                used_portal.add(position)
                used_books.add(book_position)
                pairs.append(self._build_pair(portal[position], books[book_position], basis))

        only_portal = [r for p, r in enumerate(portal) if p not in used_portal]
        only_books = [r for p, r in enumerate(books) if p not in used_books]
        pairs.sort(key=lambda pair: pair.gstr2b.sort_key())
        only_portal.sort(key=lambda record: record.sort_key())
        only_books.sort(key=lambda record: record.sort_key())
        return ReconciliationResult(
            gstr2b_documents=portal,
            book_documents=books,
            matched=pairs,
            only_in_gstr2b=only_portal,
            only_in_books=only_books,
            date_tolerance_days=self.date_tolerance_days,
            taxable_tolerance=self.taxable_tolerance,
            tax_tolerance=self.tax_tolerance,
        )

    def _match_key(
        self,
        record: InvoiceRecord,
        basis: MatchBasis,
    ) -> Optional[Tuple[str, str, str]]:
        """
        Bucket key for one pass, or None when the record cannot take part.

        The ITC sign class is part of the key, so an invoice and a credit note
        that happen to share a number are never paired.
        """
        if not record.gstin_key:
            return None
        if basis is MatchBasis.EXACT_INVOICE_NO:
            part = record.exact_key
        elif basis is MatchBasis.NORMALISED_INVOICE_NO:
            part = record.invoice_key
        elif basis is MatchBasis.INVOICE_TRAILING_NUMBER:
            part = record.trailing_number
        else:
            # Value passes bucket by supplier only; _admissible applies the
            # tolerances, so the value tolerance still governs the match.
            part = ""
        if basis in (MatchBasis.EXACT_INVOICE_NO, MatchBasis.NORMALISED_INVOICE_NO,
                     MatchBasis.INVOICE_TRAILING_NUMBER) and not part:
            return None
        return (record.gstin_key, record.sign_class, part)

    def _date_gap(self, left: InvoiceRecord, right: InvoiceRecord) -> Optional[int]:
        if left.invoice_date is None or right.invoice_date is None:
            return None
        return abs((left.invoice_date - right.invoice_date).days)

    def _value_close(self, portal: InvoiceRecord, book: InvoiceRecord) -> bool:
        difference = abs(book.taxable_value - portal.taxable_value)
        return difference <= self.taxable_tolerance + 1e-9

    def _admissible(self, portal: InvoiceRecord, book: InvoiceRecord, basis: MatchBasis) -> bool:
        """Guard for the weaker passes."""
        if basis is MatchBasis.SUPPLIER_VALUE_ONLY:
            # Only for documents that carry no supplier document number (a Tally
            # debit note) and for purchase returns, which usually cite the original
            # invoice rather than the supplier's credit-note number; and then on
            # value alone: the voucher date says little about when the supplier
            # issued its credit note, and the date a return cites is the original
            # invoice's. An invoice with a number of its own is never paired this way.
            tax_gap = abs(book.total_tax - portal.total_tax)
            return (
                (not book.invoice_key or not portal.invoice_key or portal.sign_class == "C")
                and self._value_close(portal, book)
                and tax_gap <= self.tax_tolerance + 1e-9
            )
        gap = self._date_gap(portal, book)
        if gap is None or gap > self.date_tolerance_days:
            return False
        return self._value_close(portal, book)

    def _rank(
        self,
        portal: InvoiceRecord,
        book: InvoiceRecord,
        basis: Optional[MatchBasis] = None,
    ) -> Tuple[float, float, float, str, int]:
        """
        Candidate ranking - closest date, then value, then tax, then row.

        On value alone (the weak pass) the date says nothing - a return cites the
        original invoice's - so there the closest value, then tax, comes first and the
        date only breaks a tie.
        """
        gap = self._date_gap(portal, book)
        date_rank = 10 ** 6 if gap is None else gap
        taxable_rank = round(abs(book.taxable_value - portal.taxable_value), 2)
        tax_rank = round(abs(book.total_tax - portal.total_tax), 2)
        if basis is MatchBasis.SUPPLIER_VALUE_ONLY:
            return (taxable_rank, tax_rank, date_rank, portal.sheet, portal.row_id)
        return (date_rank, taxable_rank, tax_rank, portal.sheet, portal.row_id)

    def _is_rate_difference(
        self,
        portal: InvoiceRecord,
        book: InvoiceRecord,
        taxable_diff: float,
    ) -> bool:
        """
        Same taxable value, different tax: the two sides applied different rates.

        The implied rates must differ by more than half a percentage point, so
        a stray rounding difference is not mistaken for a rate change.
        """
        if abs(taxable_diff) > self.taxable_tolerance + 1e-9:
            return False
        if not portal.taxable_value or not book.taxable_value:
            return False
        portal_rate = portal.total_tax / portal.taxable_value * 100
        book_rate = book.total_tax / book.taxable_value * 100
        return abs(portal_rate - book_rate) > 0.5

    def _build_pair(
        self,
        portal: InvoiceRecord,
        book: InvoiceRecord,
        basis: MatchBasis,
    ) -> MatchedPair:
        taxable_diff = round(book.taxable_value - portal.taxable_value, 2)
        igst_diff = round(book.igst - portal.igst, 2)
        cgst_diff = round(book.cgst - portal.cgst, 2)
        sgst_diff = round(book.sgst - portal.sgst, 2)
        cess_diff = round(book.cess - portal.cess, 2)
        tax_diff = round(book.total_tax - portal.total_tax, 2)
        gap = None if basis is MatchBasis.SUPPLIER_VALUE_ONLY else self._date_gap(portal, book)

        flags: List[str] = []
        if abs(taxable_diff) > self.taxable_tolerance + 1e-9:
            flags.append(FLAG_TAXABLE_VALUE_DIFF)
        if abs(tax_diff) > self.tax_tolerance + 1e-9:
            flags.append(FLAG_TAX_AMOUNT_DIFF)
            if self._is_rate_difference(portal, book, taxable_diff):
                flags.append(FLAG_RATE_DIFFERENCE)
        else:
            head_shift = max(abs(igst_diff), abs(cgst_diff), abs(sgst_diff), abs(cess_diff))
            if head_shift > self.tax_tolerance + 1e-9:
                # Total tax agrees but the heads do not: IGST booked against a
                # CGST+SGST invoice, or the other way round.
                flags.append(FLAG_TAX_HEAD_DIFF)
        weak = basis is MatchBasis.SUPPLIER_VALUE_ONLY
        if weak:
            flags.append(FLAG_NO_DOC_REFERENCE)
        elif gap is None:
            flags.append(FLAG_MISSING_DATE)
        elif gap > self.date_tolerance_days:
            flags.append(FLAG_INVOICE_DATE_DIFF)
        if basis is MatchBasis.TAXABLE_VALUE_AND_DATE:
            flags.append(FLAG_INVOICE_NO_MISMATCH)
        elif basis not in (MatchBasis.EXACT_INVOICE_NO, MatchBasis.SUPPLIER_VALUE_ONLY):
            flags.append(FLAG_INVOICE_NO_FORMAT_DIFF)
        if portal.document_type is not book.document_type:
            flags.append(FLAG_DOCUMENT_TYPE_DIFF)
        if not portal.itc_available:
            flags.append(FLAG_ITC_NOT_AVAILABLE)

        status = (
            ReconStatus.MATCHED_WITH_DIFFERENCE
            if any(flag in DIFFERENCE_FLAGS for flag in flags)
            else ReconStatus.MATCHED
        )
        # Section 16(2)(aa): credit is available only to the extent the
        # supplier has reported it, so a matched document is claimable at the
        # lower of the two figures and the excess in books is at risk.
        claimable = 0.0 if not portal.itc_available else min(book.total_tax, portal.total_tax)
        return MatchedPair(
            gstr2b=portal,
            books=book,
            basis=basis,
            status=status,
            flags=flags,
            taxable_diff=taxable_diff,
            tax_diff=tax_diff,
            igst_diff=igst_diff,
            cgst_diff=cgst_diff,
            sgst_diff=sgst_diff,
            cess_diff=cess_diff,
            date_diff_days=gap,
            itc_claimable=round(claimable, 2),
            itc_at_risk=round(max(0.0, book.total_tax - claimable), 2),
        )


REGISTER_COLUMNS: Tuple[str, ...] = (
    "status", "match_basis", "flags", "gstin", "supplier_name",
    "gstr2b_invoice_no", "gstr2b_invoice_date", "gstr2b_taxable_value",
    "gstr2b_igst", "gstr2b_cgst", "gstr2b_sgst", "gstr2b_cess", "gstr2b_total_tax",
    "gstr2b_itc_available", "gstr2b_rows",
    "books_invoice_no", "books_invoice_date", "books_taxable_value",
    "books_igst", "books_cgst", "books_sgst", "books_cess", "books_total_tax",
    "books_rows",
    "taxable_value_diff", "total_tax_diff", "date_diff_days",
    "itc_claimable", "itc_at_risk",
)

REGISTER_FILENAME = "gstr2b_vs_books_register.csv"
SUMMARY_FILENAME = "gstr2b_vs_books_summary.csv"


def _money(value: float) -> str:
    """Format an amount for the text report."""
    return f"{value:,.2f}"


def _iso(value: Optional[date]) -> str:
    return value.isoformat() if value else ""


def _rows_text(record: InvoiceRecord) -> str:
    rows = "|".join(str(row) for row in record.source_rows)
    return f"{record.sheet}!{rows}" if record.sheet else rows


@dataclass
class ReconciliationResult:
    """The classified outcome of one reconciliation run."""
    gstr2b_documents: List[InvoiceRecord]
    book_documents: List[InvoiceRecord]
    matched: List[MatchedPair]
    only_in_gstr2b: List[InvoiceRecord]
    only_in_books: List[InvoiceRecord]
    date_tolerance_days: int = DEFAULT_DATE_TOLERANCE_DAYS
    taxable_tolerance: float = DEFAULT_TAXABLE_TOLERANCE
    tax_tolerance: float = DEFAULT_TAX_TOLERANCE
    warnings: List[str] = field(default_factory=list)

    def clean_matches(self) -> List[MatchedPair]:
        """Pairs that agree within every tolerance."""
        return [pair for pair in self.matched if pair.status is ReconStatus.MATCHED]

    def differences(self) -> List[MatchedPair]:
        """Pairs that are the same document but do not agree."""
        return [
            pair for pair in self.matched
            if pair.status is ReconStatus.MATCHED_WITH_DIFFERENCE
        ]

    def _totals(self, records: Sequence[InvoiceRecord]) -> Dict[str, float]:
        return {
            "documents": len(records),
            "lines": sum(record.line_count for record in records),
            "taxable_value": round(sum(record.taxable_value for record in records), 2),
            "igst": round(sum(record.igst for record in records), 2),
            "cgst": round(sum(record.cgst for record in records), 2),
            "sgst": round(sum(record.sgst for record in records), 2),
            "cess": round(sum(record.cess for record in records), 2),
            "total_tax": round(sum(record.total_tax for record in records), 2),
        }

    def itc_position(self) -> Dict[str, float]:
        """
        Input tax credit position on the conservative basis: credit only to
        the extent the supplier has reported it in GSTR-2B.
        """
        portal = self.gstr2b_documents
        eligible = round(sum(r.total_tax for r in portal if r.itc_available), 2)
        ineligible = round(sum(r.total_tax for r in portal if not r.itc_available), 2)
        claimable = round(sum(pair.itc_claimable for pair in self.matched), 2)
        excess = round(sum(pair.itc_at_risk for pair in self.matched), 2)
        books_only = round(sum(record.total_tax for record in self.only_in_books), 2)
        unbooked = round(
            sum(record.total_tax for record in self.only_in_gstr2b if record.itc_available), 2
        )
        return {
            "as_per_gstr2b": round(sum(record.total_tax for record in portal), 2),
            "eligible_in_gstr2b": eligible,
            "ineligible_in_gstr2b": ineligible,
            "as_per_books": round(sum(r.total_tax for r in self.book_documents), 2),
            "claimable_on_matched": claimable,
            "in_gstr2b_not_in_books": unbooked,
            "excess_claimed_on_matched": excess,
            "booked_but_not_in_gstr2b": books_only,
            "at_risk": round(excess + books_only, 2),
        }

    def summary(self) -> Dict[str, object]:
        """Machine-readable summary of the run."""
        portal_totals = self._totals(self.gstr2b_documents)
        book_totals = self._totals(self.book_documents)
        basis_counts = {basis.value: 0 for basis in MatchBasis}
        flag_counts: Dict[str, int] = {}
        for pair in self.matched:
            basis_counts[pair.basis.value] += 1
            for flag in pair.flags:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
        return {
            "tolerances": {
                "date_days": self.date_tolerance_days,
                "taxable_value": self.taxable_tolerance,
                "tax": self.tax_tolerance,
            },
            "gstr2b": portal_totals,
            "books": book_totals,
            "counts": {
                "matched": len(self.clean_matches()),
                "matched_with_difference": len(self.differences()),
                "only_in_gstr2b": len(self.only_in_gstr2b),
                "only_in_books": len(self.only_in_books),
            },
            "match_basis": basis_counts,
            "flags": dict(sorted(flag_counts.items())),
            "differences": {
                "taxable_value": round(
                    book_totals["taxable_value"] - portal_totals["taxable_value"], 2
                ),
                "total_tax": round(book_totals["total_tax"] - portal_totals["total_tax"], 2),
            },
            "itc": self.itc_position(),
            "warnings": list(self.warnings),
        }

    def action_items(self) -> List[str]:
        """Plain-language follow-ups derived from the result."""
        itc = self.itc_position()
        items: List[str] = []
        if self.only_in_books:
            suppliers = len({record.gstin_key for record in self.only_in_books})
            items.append(
                f"Chase {suppliers} supplier(s) for {len(self.only_in_books)} document(s) not in "
                f"GSTR-2B - ITC Rs.{_money(itc['booked_but_not_in_gstr2b'])} is not available "
                f"until they report it."
            )
        if self.only_in_gstr2b:
            items.append(
                f"Book {len(self.only_in_gstr2b)} document(s) that appear in GSTR-2B but not in "
                f"the books - ITC Rs.{_money(itc['in_gstr2b_not_in_books'])} is sitting unclaimed."
            )
        value_diffs = [p for p in self.differences() if FLAG_TAXABLE_VALUE_DIFF in p.flags]
        if value_diffs:
            items.append(
                f"Investigate {len(value_diffs)} taxable-value difference(s) beyond the "
                f"Rs.{_money(self.taxable_tolerance)} tolerance."
            )
        head_diffs = [p for p in self.differences() if FLAG_TAX_HEAD_DIFF in p.flags]
        if head_diffs:
            items.append(
                f"Correct the tax head on {len(head_diffs)} document(s): total tax agrees but "
                f"IGST vs CGST/SGST does not (place-of-supply issue)."
            )
        ineligible = [p for p in self.differences() if FLAG_ITC_NOT_AVAILABLE in p.flags]
        if ineligible:
            items.append(
                f"Reverse ITC on {len(ineligible)} document(s) that GSTR-2B marks as ITC "
                f"not available."
            )
        date_diffs = [p for p in self.differences() if FLAG_INVOICE_DATE_DIFF in p.flags]
        if date_diffs:
            items.append(
                f"Confirm the invoice date on {len(date_diffs)} document(s) - the gap exceeds "
                f"{self.date_tolerance_days} day(s), which can move the credit to another period."
            )
        review = [
            p for p in self.matched
            if p.basis in (MatchBasis.INVOICE_TRAILING_NUMBER, MatchBasis.TAXABLE_VALUE_AND_DATE)
        ]
        if review:
            items.append(
                f"Eyeball {len(review)} weak match(es) made on the trailing number or on "
                f"value+date rather than on the invoice number itself."
            )
        if not items:
            items.append("No action required - both sides agree within the tolerances.")
        return items

    def to_rows(self) -> List[Dict[str, str]]:
        """
        Flatten the result into one row per document or matched pair.

        This is the audit register: filtering the `status` column reproduces
        every table in the text report.
        """
        rows: List[Dict[str, str]] = []
        for pair in self.matched:
            portal, book = pair.gstr2b, pair.books
            rows.append({
                "status": pair.status.value,
                "match_basis": pair.basis.value,
                "flags": pair.flag_text,
                "gstin": portal.gstin_key,
                "supplier_name": portal.supplier_name or book.supplier_name,
                "gstr2b_invoice_no": portal.invoice_no,
                "gstr2b_invoice_date": _iso(portal.invoice_date),
                "gstr2b_taxable_value": f"{portal.taxable_value:.2f}",
                "gstr2b_igst": f"{portal.igst:.2f}",
                "gstr2b_cgst": f"{portal.cgst:.2f}",
                "gstr2b_sgst": f"{portal.sgst:.2f}",
                "gstr2b_cess": f"{portal.cess:.2f}",
                "gstr2b_total_tax": f"{portal.total_tax:.2f}",
                "gstr2b_itc_available": "Yes" if portal.itc_available else "No",
                "gstr2b_rows": _rows_text(portal),
                "books_invoice_no": book.invoice_no,
                "books_invoice_date": _iso(book.invoice_date),
                "books_taxable_value": f"{book.taxable_value:.2f}",
                "books_igst": f"{book.igst:.2f}",
                "books_cgst": f"{book.cgst:.2f}",
                "books_sgst": f"{book.sgst:.2f}",
                "books_cess": f"{book.cess:.2f}",
                "books_total_tax": f"{book.total_tax:.2f}",
                "books_rows": _rows_text(book),
                "taxable_value_diff": f"{pair.taxable_diff:.2f}",
                "total_tax_diff": f"{pair.tax_diff:.2f}",
                "date_diff_days": "" if pair.date_diff_days is None else str(pair.date_diff_days),
                "itc_claimable": f"{pair.itc_claimable:.2f}",
                "itc_at_risk": f"{pair.itc_at_risk:.2f}",
            })
        for record in self.only_in_gstr2b:
            claimable = record.total_tax if record.itc_available else 0.0
            rows.append({
                "status": ReconStatus.ONLY_IN_GSTR2B.value,
                "match_basis": "",
                "flags": "" if record.itc_available else FLAG_ITC_NOT_AVAILABLE,
                "gstin": record.gstin_key,
                "supplier_name": record.supplier_name,
                "gstr2b_invoice_no": record.invoice_no,
                "gstr2b_invoice_date": _iso(record.invoice_date),
                "gstr2b_taxable_value": f"{record.taxable_value:.2f}",
                "gstr2b_igst": f"{record.igst:.2f}",
                "gstr2b_cgst": f"{record.cgst:.2f}",
                "gstr2b_sgst": f"{record.sgst:.2f}",
                "gstr2b_cess": f"{record.cess:.2f}",
                "gstr2b_total_tax": f"{record.total_tax:.2f}",
                "gstr2b_itc_available": "Yes" if record.itc_available else "No",
                "gstr2b_rows": _rows_text(record),
                "taxable_value_diff": "",
                "total_tax_diff": "",
                "date_diff_days": "",
                "itc_claimable": f"{claimable:.2f}",
                "itc_at_risk": "0.00",
            })
        for record in self.only_in_books:
            rows.append({
                "status": ReconStatus.ONLY_IN_BOOKS.value,
                "match_basis": "",
                "flags": "",
                "gstin": record.gstin_key,
                "supplier_name": record.supplier_name,
                "books_invoice_no": record.invoice_no,
                "books_invoice_date": _iso(record.invoice_date),
                "books_taxable_value": f"{record.taxable_value:.2f}",
                "books_igst": f"{record.igst:.2f}",
                "books_cgst": f"{record.cgst:.2f}",
                "books_sgst": f"{record.sgst:.2f}",
                "books_cess": f"{record.cess:.2f}",
                "books_total_tax": f"{record.total_tax:.2f}",
                "books_rows": _rows_text(record),
                "taxable_value_diff": "",
                "total_tax_diff": "",
                "date_diff_days": "",
                "itc_claimable": "0.00",
                "itc_at_risk": f"{record.total_tax:.2f}",
            })
        return [{column: row.get(column, "") for column in REGISTER_COLUMNS} for row in rows]

    def generate_report(
        self,
        title: str = "GSTR-2B vs BOOKS RECONCILIATION",
        generated_at: Optional[str] = None,
        max_rows: int = 50,
    ) -> str:
        """
        Render the reconciliation as a plain-text work-paper.

        Amounts are written as 'Rs.' rather than the rupee sign so the report
        prints on a Windows console without an encoding error.
        """
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        summary = self.summary()
        itc = self.itc_position()
        portal, books = summary["gstr2b"], summary["books"]
        counts = summary["counts"]
        bar = "=" * 78
        thin = "-" * 78
        lines = [bar, title, bar]
        if generated_at:
            lines.append(f"Generated   : {generated_at}")
        lines.append(
            f"Tolerances  : date +/- {self.date_tolerance_days} day(s) | "
            f"taxable +/- Rs.{_money(self.taxable_tolerance)} | "
            f"tax +/- Rs.{_money(self.tax_tolerance)}"
        )

        lines += ["", "1. DOCUMENTS READ", thin]
        lines.append(
            f"   GSTR-2B : {portal['documents']:>5} document(s) from {portal['lines']:>5} row(s)"
        )
        lines.append(
            f"   Books   : {books['documents']:>5} document(s) from {books['lines']:>5} row(s)"
        )

        lines += ["", "2. MATCH SUMMARY", thin]
        lines.append(f"   Matched (within tolerance)      : {counts['matched']:>5}")
        lines.append(f"   Matched with difference         : {counts['matched_with_difference']:>5}")
        lines.append(f"   In GSTR-2B only (not booked)    : {counts['only_in_gstr2b']:>5}")
        lines.append(f"   In books only (not in GSTR-2B)  : {counts['only_in_books']:>5}")
        for basis, count in summary["match_basis"].items():
            if count:
                lines.append(f"   matched on {basis:<28}: {count:>5}")

        lines += ["", "3. VALUE RECONCILIATION", thin]
        lines.append(f"   {'':<22}{'Taxable value':>18}{'Total tax':>18}")
        lines.append(
            f"   {'As per GSTR-2B':<22}{_money(portal['taxable_value']):>18}"
            f"{_money(portal['total_tax']):>18}"
        )
        lines.append(
            f"   {'As per books':<22}{_money(books['taxable_value']):>18}"
            f"{_money(books['total_tax']):>18}"
        )
        lines.append(
            f"   {'Difference (bk - 2B)':<22}"
            f"{_money(summary['differences']['taxable_value']):>18}"
            f"{_money(summary['differences']['total_tax']):>18}"
        )

        lines += ["", "4. INPUT TAX CREDIT POSITION", thin]
        for label, key in (
            ("ITC as per GSTR-2B", "as_per_gstr2b"),
            ("of which marked not available", "ineligible_in_gstr2b"),
            ("ITC as per books", "as_per_books"),
            ("Claimable on matched documents", "claimable_on_matched"),
            ("In GSTR-2B but not booked", "in_gstr2b_not_in_books"),
            ("Excess claimed on matched docs", "excess_claimed_on_matched"),
            ("Booked but not in GSTR-2B", "booked_but_not_in_gstr2b"),
            ("TOTAL ITC AT RISK", "at_risk"),
        ):
            lines.append(f"   {label:<32}: Rs.{_money(itc[key]):>16}")

        differences = self.differences()
        lines += ["", f"5. MATCHED WITH DIFFERENCE ({len(differences)})", thin]
        if not differences:
            lines.append("   None.")
        for pair in differences[:max_rows]:
            lines.append(f"   {pair.gstr2b.label}  [{pair.flag_text}]")
            gap = "n/a" if pair.date_diff_days is None else f"{pair.date_diff_days} day(s)"
            lines.append(
                f"      books - 2B: taxable Rs.{_money(pair.taxable_diff)} | "
                f"tax Rs.{_money(pair.tax_diff)} | date gap {gap}"
            )
            if pair.books.exact_key != pair.gstr2b.exact_key:
                lines.append(f"      books invoice no: {pair.books.invoice_no}")
        if len(differences) > max_rows:
            lines.append(f"   ... and {len(differences) - max_rows} more - see {REGISTER_FILENAME}")

        lines += ["", f"6. IN GSTR-2B, NOT IN BOOKS ({len(self.only_in_gstr2b)})", thin]
        if not self.only_in_gstr2b:
            lines.append("   None.")
        for record in self.only_in_gstr2b[:max_rows]:
            note = "" if record.itc_available else "  [ITC not available]"
            lines.append(
                f"   {record.label}  taxable Rs.{_money(record.taxable_value)} | "
                f"tax Rs.{_money(record.total_tax)}{note}"
            )
            if record.supplier_name:
                lines.append(f"      {record.supplier_name}")
        if len(self.only_in_gstr2b) > max_rows:
            extra = len(self.only_in_gstr2b) - max_rows
            lines.append(f"   ... and {extra} more - see {REGISTER_FILENAME}")

        lines += ["", f"7. IN BOOKS, NOT IN GSTR-2B ({len(self.only_in_books)})", thin]
        if not self.only_in_books:
            lines.append("   None.")
        for record in self.only_in_books[:max_rows]:
            lines.append(
                f"   {record.label}  taxable Rs.{_money(record.taxable_value)} | "
                f"ITC at risk Rs.{_money(record.total_tax)}"
            )
            if record.supplier_name:
                lines.append(f"      {record.supplier_name}")
        if len(self.only_in_books) > max_rows:
            extra = len(self.only_in_books) - max_rows
            lines.append(f"   ... and {extra} more - see {REGISTER_FILENAME}")

        lines += ["", "8. READ NOTES & DATA QUALITY", thin]
        if not self.warnings:
            lines.append("   Nothing to report.")
        for warning in self.warnings[:max_rows]:
            lines.append(f"   - {warning}")
        if len(self.warnings) > max_rows:
            lines.append(f"   ... and {len(self.warnings) - max_rows} more")

        lines += ["", "9. ACTION ITEMS", thin]
        for index, item in enumerate(self.action_items(), start=1):
            lines.append(f"   {index}. {item}")
        lines += ["", bar]
        return "\n".join(lines)

    def write_csv_reports(self, output_dir: str) -> List[str]:
        """
        Write the audit register and the summary as CSV files.

        Returns the paths written, register first. Nothing else is touched in
        the directory, which is created if it does not exist.
        """
        os.makedirs(output_dir, exist_ok=True)
        register_path = os.path.join(output_dir, REGISTER_FILENAME)
        with open(register_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(REGISTER_COLUMNS))
            writer.writeheader()
            writer.writerows(self.to_rows())
        summary_path = os.path.join(output_dir, SUMMARY_FILENAME)
        with open(summary_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["item", "value"])
            for key, value in _flatten_summary(self.summary()):
                writer.writerow([key, value])
        return [register_path, summary_path]


def _flatten_summary(summary: Dict[str, object], prefix: str = "") -> List[Tuple[str, str]]:
    """Flatten the nested summary dict into key/value pairs for CSV."""
    flat: List[Tuple[str, str]] = []
    for key, value in summary.items():
        label = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.extend(_flatten_summary(value, f"{label}."))
        elif isinstance(value, list):
            for index, item in enumerate(value, start=1):
                flat.append((f"{label}.{index}", str(item)))
        else:
            flat.append((label, str(value)))
    return flat


# Column aliases, matched as substrings of the normalised header text, best alias
# first. Within a field the header carrying the best-ranked alias wins whatever
# its position in the sheet, so a "Supplier Invoice No" beats a "Voucher No" even
# when the voucher number is the left-hand column. A header is used by one field
# only; fields are filled in the order listed.
COLUMN_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("gstin", (
        "gstin of supplier", "supplier gstin", "party gstin", "gstin uin", "gstin no", "gst no",
        "gstin",
    )),
    ("voucher_no", (
        "voucher number", "voucher no", "vch number", "vch no", "entry no",
    )),
    ("invoice_no", (
        "supplier invoice number", "supplier invoice no", "supplier inv no", "party invoice no",
        "party bill no", "invoice number", "invoice no", "inv no", "document number",
        "document no", "doc number", "doc no", "note number", "note no", "bill number",
        "bill no", "reference number", "reference no", "ref no",
    )),
    ("invoice_date", (
        "supplier invoice date", "invoice date", "inv date", "document date", "doc date",
        "note date", "bill date", "reference date", "ref date",
    )),
    ("booking_date", ("voucher date", "vch date", "booking date", "posting date", "entry date")),
    ("taxable_value", (
        "taxable value", "taxable amount", "assessable value", "net value", "basic amount",
        "taxable",
    )),
    ("igst", ("integrated tax", "igst")),
    ("cgst", ("central tax", "cgst")),
    ("sgst", ("state ut tax", "state tax", "ut tax", "sgst", "utgst")),
    ("cess", ("cess",)),
    ("itc_available", (
        "itc availability", "itc available", "itc eligibility", "eligibility for itc",
        "itc eligible",
    )),
    ("document_type", ("note type", "invoice type", "document type", "doc type")),
    ("voucher_type", ("voucher type", "vch type", "vtype")),
    ("rcm", ("supply attract reverse charge", "reverse charge", "rcm")),
    ("reason", ("reason",)),
    ("gstr1_period", ("gstr 1 iff gstr 5 period", "gstr 1 period", "filing period")),
    ("gstr1_filing_date", ("gstr 1 iff gstr 5 filing date", "gstr 1 filing date", "filing date")),
    ("ref", ("unique gstr 2b no", "unique gstr 2b period", "unique gstr 2b")),
    ("period_ref", ("gstr 2b period", "gstr2b period", "2b period")),
    ("origin_period", (
        "origin period", "def books month", "deferred in", "deffered in", "deferred month",
        "deffered month", "defferred ineligiable month", "deferred ineligible month",
    )),
    ("taken_in_3b", ("taken in gstr 3b", "taken in 3b")),
    ("reclaimed", ("reclaimed month", "reclaim month", "reclaimed in")),
    ("serial", ("sl no", "sr no", "serial no", "serial")),
    ("supplier_name", (
        "trade legal name", "legal name", "trade name", "supplier name", "party name",
        "vendor name", "particulars", "supplier", "vendor", "party",
    )),
    ("remarks", ("remarks", "remark")),
)

REQUIRED_COLUMNS: Tuple[str, ...] = ("gstin", "invoice_no", "taxable_value")
OPTIONAL_COLUMNS: Tuple[str, ...] = ("invoice_date", "igst", "cgst", "sgst")

# A header carrying any of these words is never mapped. They are the columns that
# look like the ones wanted but are not: 'IRN date', 'Rate(%)', 'Applicable % of
# Tax Rate', the 'Original ...' columns of an amended (B2BA/CDNRA) sheet, where the
# revised document is the one that counts, and the credit-note sheets' 'Amount
# declared by taxpayer for ITC reduction', which repeats the four tax headings.
_IGNORED_HEADER_TOKENS = frozenset({"irn", "rate", "checksum", "original", "declared"})

# A header that is only the word "date" is the voucher date in a books register
# and the invoice date when it is the only date there is.
_BARE_DATE_HEADERS = frozenset({"date", "dated"})


def _normalise_header(text: str) -> str:
    """Lower-case a header and reduce punctuation to single spaces."""
    return _HEADER_NOISE.sub(" ", str(text or "").lower()).strip()


def map_columns(headers: Sequence[str]) -> Dict[str, int]:
    """Map a header row to field -> column index."""
    normalised = [_normalise_header(raw) for raw in headers]
    usable = [
        index for index, header in enumerate(normalised)
        if header and not _IGNORED_HEADER_TOKENS & set(header.split())
    ]
    mapping: Dict[str, int] = {}
    used: set = set()
    for fieldname, aliases in COLUMN_ALIASES:
        best: Optional[Tuple[int, int]] = None
        for index in usable:
            if index in used:
                continue
            for rank, alias in enumerate(aliases):
                if alias in normalised[index]:
                    if best is None or (rank, index) < best:
                        best = (rank, index)
                    break
        if best is not None:
            mapping[fieldname] = best[1]
            used.add(best[1])
    if "invoice_date" not in mapping and "invoice_no" in mapping:
        # Tally's voucher register prints the supplier's invoice date under a header
        # that is only 'Invoice', right beside 'Invoice No'. Without this the voucher date
        # (about a week later) would stand in for it and the date check would reject
        # every pair.
        after = mapping["invoice_no"] + 1
        if after in usable and after not in used and normalised[after] == "invoice":
            mapping["invoice_date"] = after
            used.add(after)
    bare = [index for index in usable if index not in used
            and normalised[index] in _BARE_DATE_HEADERS]
    if bare:
        if "invoice_date" not in mapping:
            # The only date there is stands in for both dates.
            mapping["invoice_date"] = bare[0]
            mapping.setdefault("booking_date", bare[0])
        else:
            mapping.setdefault("booking_date", bare[0])
    if "invoice_no" not in mapping and "voucher_no" in mapping:
        # No supplier-document column: the voucher number is the best there is.
        mapping["invoice_no"] = mapping["voucher_no"]
    return mapping


def _has_required(mapping: Dict[str, int]) -> List[str]:
    """Names of the required columns still missing from a mapping."""
    return [name for name in REQUIRED_COLUMNS if name not in mapping]


# The portal prints a group heading over its sub-headings: 'Invoice Details' over
# 'Invoice number / Invoice type / Invoice Date', 'Tax Amount' over the four tax
# columns, and on an amended document 'Original Details' / 'Revised Details' over
# whole blocks. A heading block is therefore up to this many rows tall.
MAX_HEADER_ROWS = 3


def _is_data_like(text: str) -> bool:
    """A cell that holds a value (a GSTIN, a date, a number) rather than a column heading."""
    return looks_like_gstin(text) or parse_date(text) is not None


def _is_heading_row(row: Sequence[str]) -> bool:
    """A row of column headings: two or more labels and nothing that looks like data."""
    cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
    return len(cells) >= 2 and not any(_is_data_like(text) for text in cells)


def _documents_below(
    rows: Sequence[Sequence[str]],
    last: int,
    mapping: Dict[str, int],
    limit: int = 400,
) -> bool:
    """
    True when the rows under a stacked heading hold supplier GSTINs, or hold nothing.

    Several rows of running text (the portal's 'Read me' sheet) can be folded into
    something that looks like a heading, so a stacked heading only counts when a
    table of documents sits beneath it. An amendment sheet with no amendments has
    nothing beneath its heading and is accepted: it reads as zero rows.
    """
    column = mapping.get("gstin")
    seen = False
    for row in rows[last + 1:last + 1 + limit]:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        seen = True
        if column is not None and column < len(row) and row[column] is not None \
                and looks_like_gstin(str(row[column])):
            return True
    return not seen


def combine_header_rows(block: Sequence[Sequence[str]]) -> List[str]:
    """
    Fold stacked heading rows into one label per column.

    A group heading sits in the first column it covers (the rest is a merged cell
    that reads as empty), so it is carried to the right across the columns that
    have a sub-heading below them. A column's label is its headings read top to
    bottom: 'Invoice Details Invoice number', 'Tax Amount Central Tax(Rs)'. A
    single row comes back unchanged.
    """
    width = max((len(row) for row in block), default=0)
    cells = [
        [str(row[column]).strip() if column < len(row) and row[column] is not None else ""
         for column in range(width)]
        for row in block
    ]
    labels: List[List[str]] = [[] for _ in range(width)]
    for depth, row in enumerate(cells):
        below = cells[depth + 1:]
        carried = ""
        for column in range(width):
            text = row[column]
            if text:
                carried = text
            elif not (carried and any(lower[column] for lower in below)):
                carried = ""
                continue
            if not labels[column] or labels[column][-1] != carried:
                labels[column].append(carried)
    return [" ".join(parts) for parts in labels]


def detect_header_block(
    rows: Sequence[Sequence[str]],
    max_scan: int = 25,
) -> Tuple[int, int, Dict[str, int], List[str]]:
    """
    Find the heading rows of a sheet that still has its title/preamble rows.

    The GSTR-2B download carries several banner rows above the headings, and the
    headings themselves are stacked (see MAX_HEADER_ROWS), so every run of one to
    three rows is scored as a candidate and the best wins; on a tie the shorter
    and earlier block does, so a plain one-row header is read exactly as before.
    Returns (first row, last row, field -> column, leaf label per column) with
    zero-based row indexes; the data starts below the last row. Raises ValueError
    when no block carries the columns needed to reconcile.
    """
    best: Optional[Tuple[int, int, Dict[str, int], List[str]]] = None
    best_score = 0
    for first in range(min(max_scan, len(rows))):
        for height in range(1, MAX_HEADER_ROWS + 1):
            last = first + height - 1
            if last >= len(rows):
                break
            block = rows[first:last + 1]
            if height > 1 and not all(_is_heading_row(row) for row in block):
                break                       # a taller block would only swallow data rows
            mapping = map_columns(combine_header_rows(block))
            score = len(mapping) + 3 * sum(1 for name in REQUIRED_COLUMNS if name in mapping)
            if height > 1 and score > best_score and not _documents_below(rows, last, mapping):
                continue
            if score > best_score:
                width = max(len(row) for row in block)
                leaf = [
                    next((str(row[column]).strip() for row in reversed(block)
                          if column < len(row) and row[column] is not None
                          and str(row[column]).strip()), "")
                    for column in range(width)
                ]
                best, best_score = (first, last, mapping, leaf), score
    missing = _has_required(best[2]) if best else list(REQUIRED_COLUMNS)
    if best is None or missing:
        found = ", ".join(sorted(best[2])) if best else "none"
        raise ValueError(
            f"no usable header row found (missing: {', '.join(missing) or 'everything'}; "
            f"recognised: {found or 'none'})"
        )
    return best


def detect_header_row(
    rows: Sequence[Sequence[str]],
    max_scan: int = 25,
) -> Tuple[int, Dict[str, int]]:
    """
    Find the last heading row of a sheet and the field -> column mapping.

    The rows below it are the data. See detect_header_block, which also reports
    where a stacked heading starts. Raises ValueError when no row carries the
    columns needed to reconcile.
    """
    _first, last, mapping, _leaf = detect_header_block(rows, max_scan)
    return last, mapping


def read_csv_rows(path: str) -> List[List[str]]:
    """Read a delimited text file, sniffing the delimiter and the encoding."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, newline="", encoding=encoding) as handle:
                sample = handle.read(16384)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                return [list(row) for row in csv.reader(handle, dialect)]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode {path} as text (try saving it as UTF-8 CSV)")


# An .xlsx can arrive by e-mail from anyone. Its XML parts never legitimately carry a
# DTD or entity declaration, which is how "billion laughs" style files blow up a
# parser, so any part that does is refused outright. `defusedxml` would do this but
# the tool is standard-library only. Parts are also capped in decompressed size.
MAX_XLSX_PART_BYTES = 256 * 1024 * 1024


def _safe_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    """Parse one part of an .xlsx, refusing DTDs, entities and oversized parts."""
    size = archive.getinfo(name).file_size
    if size > MAX_XLSX_PART_BYTES:
        raise ValueError(
            f"{name} is {size:,} bytes uncompressed, too large to be a real workbook part"
        )
    data = archive.read(name)
    if b"\x00" in data[:64] or data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError(f"{name} is not UTF-8 XML, which a workbook part always is")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError(
            f"{name} declares a DTD or entity, which a real workbook never does - "
            "refusing to open it"
        )
    return ET.fromstring(data)


def _xlsx_column_index(ref: str) -> int:
    """'C7' -> 2. Returns 0 when the reference has no column letters."""
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - 64)
    return max(0, index - 1)


_OOXML_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _unescape_ooxml(text: str) -> str:
    """
    Undo the '_xHHHH_' escapes a workbook uses for characters XML cannot carry.

    Tally and Excel write a line break inside a cell as '_x000D_' (or '_x000A_'), and a
    literal underscore that would read as an escape as '_x005F_'. Left alone they sit
    inside a document number and stop it matching.
    """
    if "_x" not in text:
        return text
    return _OOXML_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _safe_xml(archive, "xl/sharedStrings.xml")
    return [
        _unescape_ooxml("".join(node.text or "" for node in item.iter(f"{_XLSX_NS}t")))
        for item in root.findall(f"{_XLSX_NS}si")
    ]


def _xlsx_sheet_paths(archive: zipfile.ZipFile) -> List[Tuple[str, str]]:
    """[(sheet name, path inside the archive)] in workbook order."""
    names = archive.namelist()
    relationships: Dict[str, str] = {}
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    doc_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    if "xl/_rels/workbook.xml.rels" in names:
        rels_root = _safe_xml(archive, "xl/_rels/workbook.xml.rels")
        for relationship in rels_root.findall(f"{rel_ns}Relationship"):
            relationships[relationship.get("Id") or ""] = relationship.get("Target") or ""
    sheets: List[Tuple[str, str]] = []
    if "xl/workbook.xml" in names:
        root = _safe_xml(archive, "xl/workbook.xml")
        for sheet in root.iter(f"{_XLSX_NS}sheet"):
            target = relationships.get(sheet.get(f"{doc_ns}id") or "", "").lstrip("/")
            if not target:
                continue
            path = target if target.startswith("xl/") else f"xl/{target}"
            if path in names:
                sheets.append((sheet.get("name") or path, path))
    if not sheets:
        sheets = [
            (os.path.basename(name), name)
            for name in sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
        ]
    return sheets


def read_xlsx_sheets(path: str) -> List[Tuple[str, List[List[str]]]]:
    """
    Read an .xlsx workbook into rows of strings, sheet by sheet.

    A deliberately small reader built on zipfile + ElementTree so the tool
    stays standard-library only and works on an air-gapped machine. Cell
    formatting is ignored, so a formatted date cell arrives as its Excel
    serial number - parse_date() converts those back.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            shared = _xlsx_shared_strings(archive)
            sheets = _xlsx_sheet_paths(archive)
            if not sheets:
                raise ValueError(f"{path} contains no worksheets")
            return [
                (name, _xlsx_rows(archive, sheet_path, shared))
                for name, sheet_path in sheets
            ]
    except zipfile.BadZipFile as error:
        raise ValueError(f"{path} is not a readable .xlsx workbook ({error})") from error


def _xlsx_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared: Sequence[str],
) -> List[List[str]]:
    root = _safe_xml(archive, sheet_path)
    rows: List[List[str]] = []
    for row in root.iter(f"{_XLSX_NS}row"):
        # A sheet stores only the rows that hold something. Pad the gaps with
        # empty rows so that list position + 1 is always the true Excel row
        # number - the audit register quotes those numbers.
        number = int(row.get("r") or len(rows) + 1)
        while len(rows) < number - 1:
            rows.append([])
        cells: Dict[int, str] = {}
        for cell in row.findall(f"{_XLSX_NS}c"):
            column = _xlsx_column_index(cell.get("r") or "")
            kind = cell.get("t")
            if kind == "inlineStr":
                inline = cell.find(f"{_XLSX_NS}is")
                cells[column] = _unescape_ooxml(
                    "".join(node.text or "" for node in inline.iter(f"{_XLSX_NS}t"))
                    if inline is not None else ""
                )
                continue
            value = cell.find(f"{_XLSX_NS}v")
            text = value.text or "" if value is not None else ""
            if kind == "s" and text:
                position = int(text)
                text = shared[position] if 0 <= position < len(shared) else ""
            cells[column] = text
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(index, "") for index in range(width)])
    return rows


def split_file_spec(spec: str) -> Tuple[str, str]:
    """
    Split 'file.xlsx#Sheet name' into ('file.xlsx', 'Sheet name').

    A path that exists as given is never split, so a file name containing '#'
    still works. With no sheet named the second part is empty.
    """
    if os.path.exists(spec) or "#" not in spec:
        return spec, ""
    path, sheet = spec.rsplit("#", 1)
    return (path, sheet) if os.path.exists(path) else (spec, "")


def read_sheets(spec: str) -> List[Tuple[str, List[List[str]]]]:
    """
    Read a CSV or .xlsx file as [(sheet name, rows)].

    `spec` may carry a sheet selector ('register.xlsx#03_Action ITC Book Def
    Billwise') to read just that sheet of a workbook that holds several tables.
    """
    path, wanted = split_file_spec(spec)
    if not os.path.exists(path):
        raise ValueError(f"file not found: {path}")
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        sheets = read_xlsx_sheets(path)
        if not wanted:
            return sheets
        chosen = [item for item in sheets if item[0].strip().lower() == wanted.strip().lower()]
        if not chosen:
            names = ", ".join(repr(name) for name, _rows in sheets)
            raise ValueError(f"sheet {wanted!r} not found in {path} (sheets: {names})")
        return chosen
    if lower.endswith((".csv", ".tsv", ".txt")):
        # A flat file has no sheet name: records keep plain row numbers.
        return [("", read_csv_rows(path))]
    raise ValueError(
        f"unsupported file type: {path} - save it as .csv or .xlsx "
        "(the old binary .xls format is not readable without extra packages)"
    )


def record_from_row(
    row: Sequence[str],
    mapping: Dict[str, int],
    source: str,
    row_id: int,
    sheet: str = "",
    books: bool = False,
    headers: Optional[Sequence[str]] = None,
) -> Optional[InvoiceRecord]:
    """
    Build one record from a data row, or None when the row is not a document.

    A row with neither a GSTIN-shaped supplier nor a document number is a banner,
    a sub-total or the 'Grand Total' line that exports end with, and a row that
    just repeats the header text is a section break inside a long register.
    Counting either would inflate every total. Credit notes are signed negative
    by InvoiceRecord itself, so it does not matter whether the export wrote them
    as positives or as negatives.
    """
    def cell(name: str) -> str:
        index = mapping.get(name)
        if index is None or index >= len(row):
            return ""
        return str(row[index] if row[index] is not None else "").strip()

    gstin = normalise_gstin(cell("gstin"))
    invoice_no = clean_doc_number(cell("invoice_no"))
    if headers is not None:
        for name in ("gstin", "invoice_no"):
            index = mapping.get(name)
            label = _normalise_header(headers[index]) if index is not None else ""
            if label and _normalise_header(cell(name)) == label:
                return None
    taxable = parse_amount(cell("taxable_value"))
    igst = parse_amount(cell("igst"))
    cgst = parse_amount(cell("cgst"))
    sgst = parse_amount(cell("sgst"))
    cess = parse_amount(cell("cess"))
    if not looks_like_gstin(gstin) and not invoice_no:
        # Nothing to match on: a banner, a sub-total or the 'Grand Total' row
        # that exports end with. Those carry amounts but no document identity,
        # and counting them would inflate every total in the report.
        return None
    voucher_type = cell("voucher_type")
    document_type = parse_document_type(cell("document_type") or voucher_type, books=books)
    shares_column = mapping.get("voucher_no") == mapping.get("invoice_no")
    voucher_no = "" if shares_column else cell("voucher_no")
    rcm = parse_yes_no(cell("rcm")) or (
        books and bool(re.search(r"\bRCM\b|REVERSE\s*CHARGE", voucher_type.upper()))
    )
    return InvoiceRecord(
        source=source,
        row_id=row_id,
        sheet=sheet,
        gstin=gstin,
        supplier_name=cell("supplier_name"),
        invoice_no=invoice_no,
        invoice_date=parse_date(cell("invoice_date")),
        taxable_value=round(taxable, 2),
        igst=round(igst, 2),
        cgst=round(cgst, 2),
        sgst=round(sgst, 2),
        cess=round(cess, 2),
        document_type=document_type,
        itc_available=parse_itc_available(cell("itc_available")) if "itc_available" in mapping
        else True,
        booking_date=parse_date(cell("booking_date")),
        voucher_no=voucher_no,
        voucher_type=voucher_type,
        rcm=rcm,
        reason=cell("reason"),
        ref=cell("ref"),
        gstr1_period=cell("gstr1_period"),
        gstr1_filing_date=parse_date(cell("gstr1_filing_date")),
        period_ref=cell("period_ref"),
        origin_period=cell("origin_period"),
        taken_in_3b=_flag_text(cell("taken_in_3b")),
        reclaimed=cell("reclaimed"),
        remarks=cell("remarks"),
    )


def load_records(
    path: str,
    source: str,
    max_header_scan: int = 25,
    side: str = "portal",
) -> Tuple[List[InvoiceRecord], List[str]]:
    """
    Load one side of the reconciliation from a CSV or .xlsx file.

    Every sheet carrying the required columns is read and the rows are
    concatenated in workbook order, so the GSTR-2B download can be passed
    exactly as the portal produced it: the 'Read me' banner sheet is skipped
    while B2B, B2BA and the credit/debit-note sheets are all picked up. Each
    record remembers the sheet and row it came from. `side` is 'portal' for a
    GSTR-2B style file and 'books' for an accounting export, where a debit note
    is a purchase return. Returns (records, notes).
    """
    if side not in ("portal", "books"):
        raise ValueError("side must be 'portal' or 'books'")
    books = side == "books"
    file_path, _sheet = split_file_spec(path)
    sheets = read_sheets(path)
    records: List[InvoiceRecord] = []
    read_notes: List[str] = []
    header_only: List[str] = []
    skipped_sheets: List[str] = []
    absent_columns: List[str] = []
    voucher_fallback = False
    blank = missing_gstin = missing_date = zero_value = 0
    for sheet_name, rows in sheets:
        try:
            first_header, header_index, mapping, headers = detect_header_block(
                rows, max_header_scan
            )
        except ValueError:
            skipped_sheets.append(sheet_name or "single table")
            continue
        kept = 0
        for row_id, row in enumerate(rows[header_index + 1:], start=header_index + 2):
            record = record_from_row(
                row, mapping, source, row_id, sheet=sheet_name, books=books, headers=headers
            )
            if record is None:
                blank += 1
                continue
            kept += 1
            record.serial = kept
            if not record.gstin_key:
                missing_gstin += 1
            if record.invoice_date is None:
                missing_date += 1
            if record.taxable_value == 0 and record.total_tax == 0:
                zero_value += 1
            records.append(record)
        label = sheet_name or "single table"
        where = (f"row {header_index + 1}" if first_header == header_index
                 else f"rows {first_header + 1}-{header_index + 1}")
        entry = f"{label} ({kept} row(s), header on {where})"
        if kept == 0:
            header_only.append(entry)       # the portal's empty amendment sheets
            continue
        read_notes.append(entry)
        if "voucher_no" in mapping and mapping.get("invoice_no") == mapping["voucher_no"]:
            voucher_fallback = True
        for name in OPTIONAL_COLUMNS:
            if name not in mapping:
                absent_columns.append(f"{label}.{name}" if sheet_name else name)
    if not read_notes and header_only:
        read_notes, header_only = header_only, []      # headings only: say so, do not refuse
    if not read_notes:
        raise ValueError(
            f"no usable header row found in {path} (sheets tried: "
            f"{', '.join(skipped_sheets) or 'none'}) - the file needs supplier GSTIN, "
            "invoice number and taxable value columns"
        )
    notes = [f"{source}: read {os.path.basename(file_path)} - {'; '.join(read_notes)}"]
    if header_only:
        notes[0] += f"; {len(header_only)} more sheet(s) with headings and no rows"
    if skipped_sheets:
        notes.append(
            f"{source}: skipped sheet(s) without reconcilable columns - "
            f"{', '.join(skipped_sheets)}"
        )
    if voucher_fallback:
        notes.append(
            f"{source}: no supplier document-number column found, so the voucher number is "
            f"used as the invoice number - matching on it will only work if it is the "
            f"supplier's number"
        )
    if absent_columns:
        notes.append(
            f"{source}: column(s) not present, treated as empty - {', '.join(absent_columns)}"
        )
    if missing_gstin:
        notes.append(
            f"{source}: {missing_gstin} row(s) carry no supplier GSTIN and cannot be matched"
        )
    if missing_date:
        notes.append(f"{source}: {missing_date} row(s) carry no readable invoice date")
    if zero_value:
        notes.append(f"{source}: {zero_value} row(s) have no taxable value and no tax")
    if blank:
        notes.append(f"{source}: {blank} blank, total or repeated-header row(s) skipped")
    return records, notes


def split_reverse_charge(
    records: Sequence[InvoiceRecord],
    source: str,
) -> Tuple[List[InvoiceRecord], List[InvoiceRecord], List[str]]:
    """
    Separate reverse-charge rows from the rest.

    Credit on a reverse-charge supply comes through GSTR-3B Table 4(A)(3) once the
    tax has been paid in cash, not through the ordinary GSTR-2B match, so those
    rows are taken out of the matching. They are returned, and counted in a note,
    rather than dropped silently. Returns (ordinary, reverse_charge, notes).
    """
    ordinary = [record for record in records if not record.rcm]
    reverse = [record for record in records if record.rcm]
    notes = []
    if reverse:
        tax = round(sum(record.total_tax for record in reverse), 2)
        notes.append(
            f"{source}: {len(reverse)} reverse-charge row(s) with tax Rs.{tax:,.2f} kept out "
            f"of the match - their credit follows GSTR-3B Table 4(A)(3)"
        )
    return ordinary, reverse, notes


def reconcile_files(
    gstr2b_path: str,
    books_path: str,
    date_tolerance_days: int = DEFAULT_DATE_TOLERANCE_DAYS,
    taxable_tolerance: float = DEFAULT_TAXABLE_TOLERANCE,
    tax_tolerance: float = DEFAULT_TAX_TOLERANCE,
) -> ReconciliationResult:
    """Load both files, reconcile them and attach the read notes."""
    portal_records, portal_notes = load_records(gstr2b_path, SOURCE_GSTR2B, side="portal")
    book_records, book_notes = load_records(books_path, SOURCE_BOOKS, side="books")
    portal_records, _portal_rcm, portal_rcm_notes = split_reverse_charge(
        portal_records, SOURCE_GSTR2B
    )
    book_records, _book_rcm, book_rcm_notes = split_reverse_charge(book_records, SOURCE_BOOKS)
    reconciler = GSTR2BReconciler(
        date_tolerance_days=date_tolerance_days,
        taxable_tolerance=taxable_tolerance,
        tax_tolerance=tax_tolerance,
    )
    result = reconciler.reconcile(portal_records, book_records)
    result.warnings = (
        portal_notes
        + book_notes
        + portal_rcm_notes
        + book_rcm_notes
        + find_duplicate_documents(result.gstr2b_documents)
        + find_duplicate_documents(result.book_documents)
    )
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    """Command-line interface for the offline reconciliation."""
    parser = argparse.ArgumentParser(
        prog="gstr2b_reconciliation_hack.py",
        description="Reconcile GSTR-2B against the purchase register, entirely offline.",
        epilog=(
            "Nothing is uploaded anywhere: both files are read from disk, matched locally and "
            "the report is written back to disk. Run with no arguments for a built-in demo."
        ),
    )
    parser.add_argument(
        "--gstr2b", "-g", required=True, metavar="FILE",
        help="GSTR-2B download from the GST portal (.xlsx) or an equivalent CSV",
    )
    parser.add_argument(
        "--books", "-b", required=True, metavar="FILE",
        help="purchase register exported from the accounting package (.csv or .xlsx)",
    )
    parser.add_argument(
        "--outdir", "-o", metavar="DIR",
        help=f"write {REGISTER_FILENAME} and {SUMMARY_FILENAME} into this directory",
    )
    parser.add_argument(
        "--report", "-r", metavar="FILE",
        help="also write the text report to this file",
    )
    parser.add_argument(
        "--date-tolerance", type=int, default=DEFAULT_DATE_TOLERANCE_DAYS, metavar="DAYS",
        help=f"invoice date tolerance in days (default {DEFAULT_DATE_TOLERANCE_DAYS})",
    )
    parser.add_argument(
        "--value-tolerance", type=float, default=DEFAULT_TAXABLE_TOLERANCE, metavar="RUPEES",
        help=f"taxable value tolerance (default {DEFAULT_TAXABLE_TOLERANCE:.0f})",
    )
    parser.add_argument(
        "--tax-tolerance", type=float, default=DEFAULT_TAX_TOLERANCE, metavar="RUPEES",
        help=f"tax amount tolerance (default {DEFAULT_TAX_TOLERANCE:.0f})",
    )
    parser.add_argument(
        "--max-rows", type=int, default=50, metavar="N",
        help="longest list printed per section; full detail always goes to the CSV register",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="do not print the report to stdout",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the reconciliation from the command line. Returns an exit code."""
    args = build_arg_parser().parse_args(argv)
    try:
        result = reconcile_files(
            args.gstr2b,
            args.books,
            date_tolerance_days=args.date_tolerance,
            taxable_tolerance=args.value_tolerance,
            tax_tolerance=args.tax_tolerance,
        )
        report = result.generate_report(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            max_rows=args.max_rows,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: could not read a file ({error})", file=sys.stderr)
        return 2
    if not args.quiet:
        print(report)
    written: List[str] = []
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
        written.append(args.report)
    if args.outdir:
        written.extend(result.write_csv_reports(args.outdir))
    for path in written:
        print(f"written: {path}")
    return 0


def _demo_record(
    source: str,
    row_id: int,
    gstin: str,
    supplier: str,
    invoice_no: str,
    day: int,
    taxable: float,
    igst: float = 0.0,
    cgst: float = 0.0,
    sgst: float = 0.0,
    document_type: DocumentType = DocumentType.INVOICE,
    itc_available: bool = True,
) -> InvoiceRecord:
    return InvoiceRecord(
        source=source,
        row_id=row_id,
        gstin=gstin,
        supplier_name=supplier,
        invoice_no=invoice_no,
        invoice_date=date(2025, 6, day),
        taxable_value=taxable,
        igst=igst,
        cgst=cgst,
        sgst=sgst,
        document_type=document_type,
        itc_available=itc_available,
    )


def demo_data() -> Tuple[List[InvoiceRecord], List[InvoiceRecord]]:
    """
    A small June 2025 book of purchases for a Maharashtra (state 27) entity,
    carrying one of each break a monthly reconciliation throws up.
    """
    portal = [
        _demo_record(SOURCE_GSTR2B, 7, "27AAFCD1234A1Z5", "Dharam Traders", "INV-001",
                     1, 100000, cgst=9000, sgst=9000),
        _demo_record(SOURCE_GSTR2B, 8, "27AAFCD1234A1Z5", "Dharam Traders", "CRN-1",
                     8, 5000, cgst=450, sgst=450, document_type=DocumentType.CREDIT_NOTE),
        _demo_record(SOURCE_GSTR2B, 9, "29XYZAB5678C1Z2", "Sunrise Electricals", "2526/118",
                     5, 50000, igst=9000),
        _demo_record(SOURCE_GSTR2B, 10, "24LMNOP4321Q1Z8", "Gokul Packaging", "B-77",
                     12, 25000, igst=4500),
        _demo_record(SOURCE_GSTR2B, 11, "07PQRSD9999E1Z9", "Capital Infra LLP", "SEZ-9",
                     18, 80000, igst=14400, itc_available=False),
        _demo_record(SOURCE_GSTR2B, 12, "33ABCDE1234F1Z5", "Chennai Logistics", "LOG/0441",
                     20, 30000, igst=5400),
    ]
    books = [
        # One invoice booked across two expense ledgers - aggregated before matching.
        _demo_record(SOURCE_BOOKS, 2, "27AAFCD1234A1Z5", "Dharam Traders", "INV/001",
                     1, 60000, cgst=5400, sgst=5400),
        _demo_record(SOURCE_BOOKS, 3, "27AAFCD1234A1Z5", "Dharam Traders", "INV/001",
                     1, 40000, cgst=3600, sgst=3600),
        _demo_record(SOURCE_BOOKS, 4, "27AAFCD1234A1Z5", "Dharam Traders", "CRN/1",
                     8, 5000, cgst=450, sgst=450, document_type=DocumentType.CREDIT_NOTE),
        # Short-booked by Rs.1,000 of taxable value and Rs.180 of tax.
        _demo_record(SOURCE_BOOKS, 5, "29XYZAB5678C1Z2", "Sunrise Electricals", "2526-118",
                     5, 49000, igst=8820),
        # Booked two days before the supplier's invoice date - inside tolerance.
        _demo_record(SOURCE_BOOKS, 6, "24LMNOP4321Q1Z8", "Gokul Packaging", "B-77",
                     10, 25000, igst=4500),
        # GSTR-2B marks this one as ITC not available.
        _demo_record(SOURCE_BOOKS, 7, "07PQRSD9999E1Z9", "Capital Infra LLP", "SEZ-9",
                     18, 80000, igst=14400),
        # Supplier has not reported this at all.
        _demo_record(SOURCE_BOOKS, 8, "36ZZZZZ1111Z1Z1", "Hyderabad Hardware", "PUR-55",
                     25, 40000, igst=7200),
    ]
    return portal, books


def _run_demo() -> None:
    portal, books = demo_data()
    result = GSTR2BReconciler().reconcile(portal, books)
    print(result.generate_report(title="GSTR-2B vs BOOKS RECONCILIATION (DEMO DATA)"))
    counts = result.summary()["counts"]
    itc = result.itc_position()
    print(
        f"Documents: GSTR-2B {len(result.gstr2b_documents)} | Books {len(result.book_documents)}"
    )
    print(
        f"Matched: {counts['matched']} | With difference: {counts['matched_with_difference']} | "
        f"GSTR-2B only: {counts['only_in_gstr2b']} | Books only: {counts['only_in_books']}"
    )
    print(f"ITC claimable: Rs.{_money(itc['claimable_on_matched'])}")
    print(f"ITC at risk: Rs.{_money(itc['at_risk'])}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    _run_demo()
