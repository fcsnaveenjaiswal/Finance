#!/usr/bin/env python3
"""
Finance Hack: the GSTR-2B vs Books ITC workpaper as an app - one HTML file.

`gstr2b_workpaper_app.html` is that app. Open it in Chrome or Edge, choose the GSTR-2B
download and the purchase register, press Build, and download the Excel workpaper. There is
nothing to install and no command to type.

The page runs the same Python engine as the command line (gstr2b_workpaper_hack and the
modules under it) inside the browser tab, using Pyodide. The files you choose are read in
the tab and never uploaded: the page carries a Content-Security-Policy that lets it contact
one host only, the CDN that serves the Python runtime, and its own code makes no network
request at all.

This module is both halves of that:

  * run_job / list_sheets / example_files - the functions the page calls inside the browser;
  * build_app - writes the HTML file from gstr2b_app_template.html with the engine's own
    source embedded, so the app can never drift from the tested code.

    python3 gstr2b_app_hack.py            # rebuild gstr2b_workpaper_app.html
    python3 gstr2b_app_hack.py --check    # fail if the committed file is out of date
"""

import argparse
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from typing import Callable, Dict, List, Optional

import gstr2b_workpaper_hack as wp
from gstr2b_itc_workflow_hack import Workpaper
from gstr2b_reconciliation_hack import xlsx_sheet_names

# The files the page embeds and runs. The page's own template is not one of them.
ENGINE_MODULES = (
    "xlsx_writer_hack",
    "gstr2b_reconciliation_hack",
    "gstr2b_itc_workflow_hack",
    "gstr2b_workpaper_hack",
    "gstr2b_app_hack",
)
TEMPLATE_FILE = "gstr2b_app_template.html"
APP_FILE = "gstr2b_workpaper_app.html"
PLACEHOLDER = "__APP_DATA__"
# The one host the page may contact: the CDN that serves the Python runtime. Pinned to an
# exact version so the engine does not change under a working app.
PYODIDE_BASE = "https://cdn.jsdelivr.net/npm/pyodide@0.27.7/"

Progress = Optional[Callable[[str], None]]


# ------------------------------------------------------------------ inside the browser


def list_sheets(path: str) -> List[str]:
    """The sheet names of an .xlsx workbook; empty for anything else or an unreadable file."""
    if not path.lower().endswith(".xlsx"):
        return []
    try:
        return xlsx_sheet_names(path)
    except (ValueError, OSError):
        return []


def _arguments(options: Dict[str, object], outdir: str) -> List[str]:
    """The command-line arguments that the choices made on the page amount to."""
    gstr2b = str(options.get("gstr2b") or "").strip()
    books = str(options.get("books") or "").strip()
    if not gstr2b:
        raise ValueError("Choose the GSTR-2B download first.")
    if not books:
        raise ValueError("Choose the purchase register first.")
    argv = ["--gstr2b", gstr2b, "--books", books, "--outdir", outdir, "--quiet"]
    for key, flag in (("opening_books", "--opening-books-deferred"),
                      ("opening_2b", "--opening-2b-pending"), ("period", "--period"),
                      ("client", "--client"), ("gstin", "--gstin"),
                      ("cn_policy", "--cn-policy")):
        value = str(options.get(key) or "").strip()
        if value:
            argv += [flag, value]
    for key, flag in (("date_tolerance", "--date-tolerance"),
                      ("value_tolerance", "--value-tolerance"),
                      ("tax_tolerance", "--tax-tolerance")):
        value = options.get(key)
        if value not in (None, ""):
            argv += [flag, str(value)]
    return argv


def summarise(work: Workpaper) -> Dict[str, object]:
    """The headline figures of a month, as plain data for the page to lay out."""
    def line(label: str, tax) -> Dict[str, object]:
        return {"label": label, "values": [round(v, 2) for v in tax.as_list()],
                "total": round(tax.total, 2)}

    table, bridge, statement = work.table4(), dict(work.bridge()), work.ecrrs()
    return {
        "client": work.client,
        "gstin": work.gstin,
        "period": work.period.long_label,
        "fy": work.period.fy_label,
        "table4": [line("4(A)(5) All other ITC", table["a5"]),
                   line("4(B)(1) Reversed", table["b1"]),
                   line("4(B)(2) Reclaimable", table["b2"]),
                   line("4(C) Net ITC", table["net"]),
                   line("4(D)(1) Reclaimed", table["d1"]),
                   line("4(D)(2) Ineligible", table["d2"])],
        "bridge": [line("ITC per books", bridge["books"]),
                   line("ITC claimed", bridge["claimed"]),
                   line("Difference", bridge["difference"])],
        "statement": [line("Opening", statement["opening"]),
                      line("Add 4(B)(2)", statement["added"]),
                      line("Less 4(D)(1)", statement["reclaimed"]),
                      line("Closing", statement["closing"]),
                      line("Per pending register", statement["register"])],
        "checks": [
            {"label": "The ITC booked bridges to the ITC claimed",
             "ok": bridge["difference"].is_zero(0.5)},
            {"label": "The credit-reversal statement agrees with the pending register",
             "ok": statement["difference"].is_zero(0.5)},
        ],
        "counts": work.counts(),
        "expiry": {key: round(value.total, 2) for key, value in work.expiry_exposure().items()},
        "actions": work.action_items(),
        "notes": list(work.notes) + list(work.warnings),
    }


def _zip_outputs(written: List[str], outdir: str, label: str) -> str:
    """Put the written files into one zip, so everything can be saved in a single click."""
    path = os.path.join(outdir, f"GSTR2B_workpaper_{label}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(written):
            archive.write(item, os.path.basename(item))
    return path


def run_job(options: Dict[str, object], progress: Progress = None) -> Dict[str, object]:
    """
    Build the workpaper for the choices made on the page.

    Never raises: a problem with the files comes back as {"ok": False, "error": ...} in plain
    words so the page can show it. On success the result carries the figures for the screen,
    the same figures as text, and the files written (name and path), the last of them a zip of
    all the others.
    """
    outdir = str(options.get("outdir") or os.path.join(tempfile.gettempdir(), "gstr2b_app_out"))
    try:
        shutil.rmtree(outdir, ignore_errors=True)          # nothing from an earlier run lingers
        args = wp.build_arg_parser().parse_args(_arguments(options, outdir))
        work, written = wp.run(args, progress)
        archive = _zip_outputs(written, outdir, work.period.label)
        files = [{"name": os.path.basename(path), "path": path} for path in written + [archive]]
        return {"ok": True, "summary": summarise(work), "text": wp.text_summary(work),
                "files": files}
    except SystemExit:
        return {"ok": False, "error": "The options on the page could not be read - check the "
                                      "month and the tolerances."}
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    except OSError as error:
        return {"ok": False, "error": f"A file could not be read or written ({error})."}
    except Exception as error:  # noqa: BLE001 - the page must always get an answer
        return {"ok": False, "unexpected": True,
                "error": f"Something unexpected went wrong ({type(error).__name__}: {error})."}


# A small invented month the page can load, so the app can be tried without any real file.
_EXAMPLE_NAMES = {
    "19AAAAA1111A1Z1": "Alpha Textiles", "27BBBBB2222B1Z2": "Bravo Traders",
    "24CCCCC3333C1Z3": "Chandra Fabrics", "07DDDDD4444D1Z4": "Delhi Dyers",
    "36EEEEE5555E1Z5": "Eastern Looms", "29FFFFF6666F1Z6": "Fine Threads",
}


def _csv_text(header: List[str], rows: List[List[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def example_files() -> Dict[str, str]:
    """
    Four small CSV files for an invented September 2026: a GSTR-2B download, a purchase
    register and the two open-item registers carried in from August. Keyed by file name.
    """
    from gstr2b_itc_workflow_hack import (
        CARRY_COLUMNS,
        KIND_BOOKS_DEFERRED,
        KIND_PENDING_2B,
        Period,
        _carry_row,
        _demo_records,
        make_open_item,
    )
    two_b, books, opening = _demo_records()

    def day(value) -> str:
        return value.strftime("%d/%m/%Y") if value else ""

    def credit(record) -> bool:
        return record.sign_class == "C"

    portal_rows = [[
        r.gstin, _EXAMPLE_NAMES.get(r.gstin, ""), r.invoice_no,
        "Credit Note" if credit(r) else "B2B", day(r.invoice_date),
        f"{abs(r.taxable_value):.2f}", f"{abs(r.igst):.2f}", f"{abs(r.cgst):.2f}",
        f"{abs(r.sgst):.2f}", "0.00", "Yes"] for r in two_b]
    books_rows = [[
        day(r.booking_date), _EXAMPLE_NAMES.get(r.gstin, ""), r.gstin,
        "Debit Note" if credit(r) else "Registered Purchase", r.voucher_no or f"PV/{r.row_id}",
        r.invoice_no, day(r.invoice_date), f"{r.taxable_value:.2f}", f"{r.igst:.2f}",
        f"{r.cgst:.2f}", f"{r.sgst:.2f}"] for r in books]
    august = Period(2026, 8)

    def carry(kind, records) -> str:
        return _csv_text(list(CARRY_COLUMNS), [
            _carry_row(make_open_item(kind, doc, august, True, doc.taken_in_3b))
            for doc in records])

    return {
        "GSTR2B_19AAAAA0000A1Z5_092026.csv": _csv_text(
            ["GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice type",
             "Invoice Date", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax",
             "Cess", "ITC Availability"], portal_rows),
        "purchase_register_sep26.csv": _csv_text(
            ["Date", "Particulars", "GSTIN/UIN", "Vch Type", "Vch No.", "Doc No.", "Doc date",
             "Taxable", "Integrated Tax", "Central Tax", "State Tax"], books_rows),
        "carry_forward_books_deferred.csv": carry(KIND_BOOKS_DEFERRED, opening.books_deferred),
        "carry_forward_2b_pending.csv": carry(KIND_PENDING_2B, opening.pending_2b),
    }


# ------------------------------------------------------------------------- the builder


def _root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def build_app(root: str = "") -> str:
    """The text of the app: the page template with the engine's own source embedded in it."""
    root = root or _root()
    with open(os.path.join(root, TEMPLATE_FILE), encoding="utf-8") as handle:
        page = handle.read()
    if page.count(PLACEHOLDER) != 1:
        raise ValueError(f"{TEMPLATE_FILE} must contain {PLACEHOLDER} exactly once")
    sources = {}
    for module in ENGINE_MODULES:
        with open(os.path.join(root, module + ".py"), encoding="utf-8") as handle:
            sources[module + ".py"] = handle.read()
    data = json.dumps({"sources": sources, "example": example_files(), "pyodide": PYODIDE_BASE},
                      ensure_ascii=True, sort_keys=True)
    # ASCII only, and never a literal "<": a "</script>" inside the data could not end the page
    return page.replace(PLACEHOLDER, data.replace("<", "\\u003c"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gstr2b_app_hack.py",
        description="Write (or check) the one-file GSTR-2B vs Books app.")
    parser.add_argument("--out", metavar="FILE", help=f"where to write it (default: {APP_FILE})")
    parser.add_argument("--check", action="store_true",
                        help="do not write; fail if the existing file is out of date")
    args = parser.parse_args(argv)
    target = args.out or os.path.join(_root(), APP_FILE)
    page = build_app()
    if args.check:
        try:
            with open(target, encoding="utf-8", newline="") as handle:
                current = handle.read()
        except OSError:
            current = None
        if current != page:
            print(f"{target} is out of date - run: python3 gstr2b_app_hack.py", file=sys.stderr)
            return 1
        print(f"{target} is up to date")
        return 0
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    print(f"written: {target} ({len(page) / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
