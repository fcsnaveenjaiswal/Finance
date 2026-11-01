# CLAUDE.md

Guidance for AI assistants working in this repository.

## Project overview

`fcsnaveenjaiswal/finance` is an educational/demo collection of Indian
finance, tax, and litigation-support utilities written in pure Python 3
(standard library at runtime; `ruff`, `flake8`, and `pytest` are dev
tools). Each module at the repo root is independent and models one
domain. They fall into three groups.

**Core ledger / compute modules** (small, with unit tests):

- **Accounts** (`accounts_hack.py`) — double-entry accounts, transactions,
  statements, and a reconciliation check that enforces
  `assets == liabilities + equity`.
- **GST** (`gst_hack.py`) — Indian Goods and Services Tax: invoice
  generation (intra-state SGST+CGST vs. inter-state IGST) and monthly
  return summaries with input tax credit.
- **Payroll** (`payroll_hack.py`) — Indian payroll: employees, monthly
  payslip generation with HRA / special allowance / PF / professional tax
  / TDS, and monthly aggregates.
- **Income Tax** (`income_tax_hack.py`) — slab-based individual income tax
  for FY 2025-26: income heads, deductions, surcharge, and cess.

**Reconciliation tooling** (CLI + unit tests):

- **GSTR-2B Reconciliation** (`gstr2b_reconciliation_hack.py`) — offline,
  deterministic matching of GSTR-2B against the purchase register, with its
  own CSV/`.xlsx` readers, a text work-paper and a CSV audit register.
- **ITC Workflow** (`gstr2b_itc_workflow_hack.py`) — the monthly process on
  top of the matcher: treatments, GSTR-3B Table 4, the books-to-return bridge,
  the credit-reversal statement and the carry-forward registers. Pure logic.
- **ITC Workpaper** (`gstr2b_workpaper_hack.py`) — the Excel workpaper and its
  command-line tool.
- **XLSX Writer** (`xlsx_writer_hack.py`) — a standard-library `.xlsx` writer
  (styles, live formulas with cached values, validation, conditional colours).
- **ITC App** (`gstr2b_app_hack.py`, `gstr2b_app_template.html`,
  `gstr2b_workpaper_app.html`) — the workpaper as a one-file browser app: the page
  runs the Python engine in a Web Worker with Pyodide. The HTML is generated.

**Compliance / litigation modules** (larger, demo-only, document-heavy):

- **ICAI Guidance Notes** (`icai_guidance_notes.py`) — accounting treatment
  helpers for leases, ESOPs, real estate, derivatives, and uncertainty.
- **GST Financial Governance** (`gst_financial_governance.py`) — a 15-component
  enterprise GST control/reconciliation/compliance framework.
- **GST Case Drafting** (`gst_case_drafting.py`) — legal templates for GST
  notices (ASMT-10, DRC-01A, DRC-01), appeals, and audit notices.
- **Income Tax Litigation Repository** (`income_tax_litigation_repository.py`)
  — a structured tax-defence/case-management system (scrutiny, appeals,
  penalty, evidence, case law, drafting).

There is no framework and no web server. The one browser entry point is a
single HTML file (`gstr2b_workpaper_app.html`, see **ITC App** below). Only the two
reconciliation CLIs write files; everything else keeps its state in memory. The
compliance/litigation modules generate large formatted text documents via
multi-line string templates.

## Repository layout

```
.
├── accounts_hack.py                    # Accounts/transactions/reconciliation
├── gst_hack.py                         # GST invoices and returns
├── payroll_hack.py                     # Employees, payslips, monthly payroll
├── income_tax_hack.py                  # Slab-based income tax (FY 2025-26)
├── icai_guidance_notes.py              # ICAI accounting guidance helpers
├── gst_financial_governance.py         # 15-component GST governance framework
├── gst_case_drafting.py                # GST legal notice/appeal templates
├── income_tax_litigation_repository.py # Tax-defence case management
├── gstr2b_reconciliation_hack.py       # GSTR-2B vs books matching engine + quick CLI
├── gstr2b_itc_workflow_hack.py         # Monthly ITC process (engine, no spreadsheet code)
├── gstr2b_workpaper_hack.py            # Monthly ITC workpaper in Excel + CLI
├── xlsx_writer_hack.py                 # Stdlib-only .xlsx writer
├── gstr2b_app_hack.py                  # What the browser app runs + its HTML generator
├── gstr2b_app_template.html            # The app's screen (hand-written)
├── gstr2b_workpaper_app.html           # GENERATED one-file app - never edit by hand
├── tests/
│   ├── test_accounts.py
│   ├── test_gst.py
│   ├── test_payroll.py
│   ├── test_gstr2b_reconciliation.py
│   ├── test_gstr2b_itc_workflow.py
│   ├── test_gstr2b_workpaper.py
│   ├── test_gstr2b_app.py
│   └── test_xlsx_writer.py
├── pyproject.toml                      # project metadata + ruff config
├── README.md
├── .github/workflows/
│   ├── ci.yml                          # unittest + demos + ruff
│   └── python-app.yml                  # flake8 + pytest
└── CLAUDE.md
```

Flat tree by design — no package, no `__init__.py` (in modules or in
`tests/`). Each domain module is importable on its own. Tests import the
modules directly because they are run from the repo root. The four core
modules and the four reconciliation-tooling modules have unit tests; the
compliance/litigation modules are covered only by their demo blocks.

## Running the code

Every module has a demo `__main__` block that doubles as a smoke check.
The core ones print verifiable values:

```bash
python3 accounts_hack.py   # "Bank Balance: 10000.0" and "Accounts balanced: True"
python3 gst_hack.py        # "Invoice Total: ₹7080.0" and "GST Payable: ₹0"
python3 payroll_hack.py    # "EMP001 Net Pay: ₹71300.0" and a monthly total
```

`gstr2b_reconciliation_hack.py` and `gstr2b_workpaper_hack.py` have a
command-line interface. With no arguments each runs its demo (smoke checks in
`ci.yml`); with arguments they reconcile real files and never touch the
network:

```bash
python3 gstr2b_reconciliation_hack.py                      # demo
python3 gstr2b_reconciliation_hack.py \
    --gstr2b GSTR2B_062025.xlsx --books purchase_register.csv \
    --outdir reco_june                                     # one-off text/CSV match

python3 gstr2b_workpaper_hack.py                           # demo (writes to the temp dir)
python3 gstr2b_workpaper_hack.py \
    --gstr2b GSTR2B_19AAAAA0000A1Z5_092026.xlsx \
    --books purchase_register.xlsx --client "Alpha" \
    --opening-dir aug26 --outdir sep26                     # monthly Excel workpaper
```

`xlsx_writer_hack.py` and `gstr2b_itc_workflow_hack.py` also have demo blocks.

`python3 gstr2b_app_hack.py` rebuilds `gstr2b_workpaper_app.html`; after changing
any engine module or `gstr2b_app_template.html` run it and commit the result
(`--check` is a CI step, and `tests/test_gstr2b_app.py` fails on a stale file).

The compliance/litigation modules (`gst_financial_governance.py`,
`gst_case_drafting.py`, `icai_guidance_notes.py`,
`income_tax_litigation_repository.py`, `income_tax_hack.py`) print long
formatted reports; running them should exit 0.

Run the unit tests with either runner from the repo root:

```bash
python3 -m unittest discover -s tests -v   # stdlib runner (ci.yml)
pytest                                     # pytest (python-app.yml)
```

Lint with ruff (install with `pip install ruff` if not present):

```bash
ruff check .
```

Two CI workflows run on every push and PR:
- `.github/workflows/ci.yml` — unit tests, the core and reconciliation demo
  scripts, and `ruff check .`
- `.github/workflows/python-app.yml` — `flake8` (syntax/undefined-name
  gate) and `pytest`

Both must be green before merging.

## Module guide

### `accounts_hack.py`

- `Account` (dataclass): `account_id`, `account_name`, `account_type`,
  `balance`, `created_at`, `description`.
- `Transaction` (dataclass): `transaction_id`, `from_account`, `to_account`,
  `amount`, `date`, `description`, `category`.
- `AccountsManager`:
  - `create_account(account_id, account_name, account_type, description="")`
  - `get_account_balance(account_id)`
  - `record_transaction(transaction_id, from_account, to_account, amount, description="", category="")`
  - `get_account_statement(account_id)` → `{account, balance, transaction_count, transactions}`
  - `reconcile_accounts()` → totals plus `balance_check`
    (`abs(assets - (liabilities + equity)) < 0.01`).

`account_type` is a free-form string but the codebase uses these five
values: `"Asset"`, `"Liability"`, `"Equity"`, `"Income"`, `"Expense"`.
Reconciliation only sums Asset / Liability / Equity.

`record_transaction` treats the call as a journal entry: `from_account` is
**credited** and `to_account` is **debited**. The effect on each balance
depends on the account's "normal side":

| Account type      | Normal side | Debit (`to`)   | Credit (`from`) |
|-------------------|-------------|----------------|-----------------|
| Asset, Expense    | Debit       | balance + amt  | balance − amt   |
| Liability, Equity, Income | Credit | balance − amt | balance + amt   |

So an owner contributing ₹10 000 (`record_transaction("T", "EQUITY001",
"BANK001", 10000)`) leaves both `EQUITY001` and `BANK001` at +10 000, and
`balance_check` returns `True`. Any change to `record_transaction` must
preserve this invariant — tests in `tests/test_accounts.py` cover it.

### `gst_hack.py`

- `GSTType` enum: `INTRA_STATE` (splits tax into SGST + CGST, each half of
  total GST) and `INTER_STATE` (becomes IGST).
- `HSNCode` enum: maps HSN-range strings to GST rates —
  `FOOD` 0%, `CLOTHING` 5%, `ELECTRONICS` 12%, `FURNITURE` 18%,
  `SERVICES` 18%.
- `LineItem` (dataclass): `description`, `hsn_code`, `quantity`, `unit_price`,
  `gst_rate` (defaults to 0.18); helpers `calculate_amount`, `calculate_gst`,
  `calculate_total`.
- `Invoice` (dataclass): supplier/customer name + GSTIN, `gst_type`, list of
  `LineItem`. Use `add_item()`, then `generate_invoice()` for the full dict
  output.
- `GSTReturn` (dataclass): monthly aggregation by GST rate.
  `add_outward_supply`, `add_inward_supply`, `add_itc` accumulate by rate;
  `calculate_gst_liability` applies `output_tax - input_credit` (floored at
  0); `generate_return_summary` returns the full report.

Indian context: GSTIN strings (e.g. `27AAFCD1234A1Z5`) and the ₹ symbol
appear in the demo.

### `payroll_hack.py`

- `Employee` (dataclass): `employee_id`, `name`, `designation`,
  `monthly_basic`, `pan`, `joining_date`.
- `Payslip` (dataclass): per-month `basic`, `hra`, `special_allowance`,
  `pf_deduction`, `professional_tax`, `tds`; properties `gross_earnings`,
  `total_deductions`, `net_pay`.
- `PayrollManager` (class) with indicative Indian payroll constants:
  - `HRA_RATE = 0.40` (40% of basic)
  - `SPECIAL_ALLOWANCE_RATE = 0.20`
  - `PF_RATE = 0.12` (employee contribution)
  - `PROFESSIONAL_TAX = 200.0` (flat monthly)
  - Methods: `add_employee`, `generate_payslip`, `get_payslips_for_employee`,
    `calculate_monthly_payroll(period_month)` which aggregates gross / PF /
    TDS / net pay across all payslips for the given `YYYY-MM`.

These rates are illustrative defaults — do not "correct" them to a slab-
based regime without a corresponding test update.

### `income_tax_hack.py`

Slab-based individual income tax for FY 2025-26. `DeductionSection` and
`IncomeSource` enums; `TaxSlab` / `Income` / `Deduction` / `TaxPayer`
dataclasses; `IncomeTaxRates` holds the slab tables. Computes taxable
income after deductions, then tax + surcharge + cess.

### `icai_guidance_notes.py`

Accounting-treatment helpers keyed to ICAI Guidance Notes. Dataclasses
per topic (`Lease`, `LeaveAccrual`, `EmployeeShareOption`,
`RealEstateTransaction`, `DerivativeContract`, `IncomeUncertainty`) plus
`GuidanceNotesComplianceChecker`. Methods return dicts describing the
prescribed accounting entry/treatment.

### `gst_financial_governance.py`

A 15-component enterprise GST control framework. Each component is its own
dataclass/class (e.g. `CentralizedFinancialRepository`,
`ITCRepositoryManagement`, `GSTReconciliationRepository`,
`VendorComplianceMonitoring`, `AuditReadinessFramework`). Methods return
status/reconciliation dicts; the demo prints them with `json.dumps`.

### `gst_case_drafting.py`

Legal-document templates for GST litigation. `NoticeType` / `CaseStatus`
enums; `TaxpayerProfile`; notice dataclasses (`GSTNoticeASMT10`,
`GSTNoticeDRC01A`, `GSTNoticeDRC01`); `GSTAppealDrafter` and
`GSTCaseDraftingModule`. The notice/appeal generators return long
formatted document strings built from multi-line templates.

### `income_tax_litigation_repository.py`

A structured income-tax defence/case-management system. Many repository
dataclasses (statutory, ITR, TDS, scrutiny, rectification, appeals at
CIT(A)/ITAT, penalty, evidence, case law) plus `DraftingTemplate` and
`StrategicDashboard`. Like the GST drafting module, it produces formatted
legal-draft text via templates.

### `gstr2b_reconciliation_hack.py`

The matching engine, the file readers and a quick text/CSV CLI. Standard
library only — no upload, no network call, and the same two inputs always
produce the same output.

- Readers: `read_csv_rows` (delimiter + encoding sniffing) and
  `read_xlsx_sheets` (a small `zipfile` + `ElementTree` reader; formatted date
  cells arrive as Excel serials and `parse_date` converts them back). Rows are
  padded so list position + 1 is the true Excel row number. Every part is read
  through `_safe_xml`, which refuses a DTD/entity declaration, non-UTF-8 parts
  and parts over `MAX_XLSX_PART_BYTES` (a workbook can arrive by e-mail; no
  `defusedxml`, because the tool is standard-library only). `.xls` is rejected
  with a message. `read_sheets("file.xlsx#Sheet name")` selects one sheet.
- `map_columns` / `detect_header_block` map headers by alias, **best alias wins
  whatever the column order**: `Doc No.` / `Supplier Invoice No` beat `Vch No.`,
  `Doc date` beats the voucher `Date` (which becomes `booking_date`), a bare
  `Invoice` beside `Invoice No` is Tally's supplier invoice date, and `Original
  ...` columns of B2BA/CDNRA sheets are ignored so the revised document is read.
  **The portal stacks its headings** (`Invoice Details` over `Invoice number /
  type / Date`, `Tax Amount` over the four taxes, `Original Details` / `Revised
  Details` over an amendment): `combine_header_rows` folds one to three rows
  into one label per column, a stacked block only counts when supplier GSTINs
  sit beneath it (the portal's `Read me` sheet is running text), and `declared`
  columns (`Amount declared by taxpayer for ITC reduction`) are never the
  document's tax. `load_records(path, source, side="portal"|"books")` reads
  every sheet carrying the required columns; on the books side a Tally `Debit
  Note` / purchase return is an ITC-reducing document (`CREDIT_NOTE`). Workbook
  escapes such as `_x000D_` are decoded.
- `InvoiceRecord` (dataclass) normalises its own keys (`gstin_key`,
  `invoice_key` — `INV/001` == `INV-1` and leading zeros go — `trailing_number`,
  which ignores a financial-year stamp so `ABC/25-26/54` and `0054/25-26` both
  end in 54, `identity_key`, `sign_class`). Credit notes are signed negative in
  `__post_init__` whichever way the export wrote them. A row with neither a
  GSTIN-shaped supplier nor a document number, or one repeating the header
  text, is not a document (`Total` rows, section breaks).
- `aggregate_records` sums multi-line documents per sheet (GSTR-2B splits an
  invoice rate-wise; a register splits it across ledgers). Number-less
  documents are identified by voucher number, never merged; so is a purchase
  return in the books, which cites the *original* invoice's number (several
  returns against one invoice are normal, not duplicates). The same invoice
  under two vouchers is a duplicate warning.
- `GSTR2BReconciler.reconcile` runs five ordered passes — exact invoice no,
  normalised, trailing number (date+value guard), taxable value + date, and
  supplier + value only (for number-less documents and for purchase returns,
  which cite the original invoice, never for an invoice with a number of its
  own) — assigning candidates **best-first** (date gap, value gap, tax gap,
  sheet, row; in the value-only pass the value fit comes first, the date means
  nothing) so nothing depends on file order. `book_priority` lets a caller give
  some books entries first claim. Invoices and credit notes never pair.
  Tolerances default to 3 days / ₹100 / ₹10 and raise `ValueError` if negative.
- Flags: value, tax, head, rate (`RATE_DIFFERENCE`), date, `NO_DOC_REFERENCE`
  (weak match), ITC not available. A missing date alone is informational.
- `split_reverse_charge` takes reverse-charge rows out of the match and counts
  them in a note rather than dropping them silently.
- `ReconciliationResult` gives `summary()`, `itc_position()`,
  `action_items()`, `generate_report()` and `write_csv_reports()`.

Amounts in text reports are written as `Rs.` rather than `₹` so they print on
a Windows console without an encoding error.

### `gstr2b_itc_workflow_hack.py`

The monthly process, as a chartered-accountancy practice runs it. Pure logic
(no spreadsheet code). `build_workpaper(gstr2b, books, period, opening, ...)`
matches the month, tags every document, and rolls the registers forward.

- **The model.** 4(A)(5) stays tied to GSTR-2B; a 2B line not yet booked is
  reversed under 4(B)(2) and *pends*, reclaimed under 4(D)(1) when booked; a
  books entry not in 2B is *deferred*, claimed when it reaches 2B. Both
  registers carry forward (`OpeningItems`, `write_carry_forward`).
- **A register kept for the whole year** is a normal books input. A books entry
  dated before the return month that is not in this GSTR-2B, reclaims nothing
  and is not in the opening register is taken as dealt with in its own month's
  return (`Workpaper.settled_earlier`, a note, and an action item when no
  opening register was given) — otherwise every earlier purchase return would
  be reversed again. A voucher in both the books file and the opening register
  counts once, from the register, which also has first claim on GSTR-2B
  documents. `load_opening_items` reads a firm's own cumulative tracker in place
  and brings in only what is still open (`_still_open`: no reclaim month, no
  `<serial> <Mon-yy>` GSTR-2B number against it, and — books side — deferred or
  taken in 3B; a deferral marked `INELIGIBLE` never returns); a plain list of
  open items such as the carry-forward CSVs is taken whole.
- **Treatments** are the single source of truth: `Row2B.claim` and
  `RowBooks.claim` depend only on the tag, which is what the spreadsheet
  formulas read too. 2B lines: `CLAIM`, `CLAIM_PM_BOOKED`, `REVERSE_4B2`,
  `REVERSE_4B1`, `INELIGIBLE`, `CN_REVERSE`, `CN_ALREADY_TAKEN`,
  `CN_NOT_OURS`. Books-only lines: `DEFER_BOOKS`, `RECLAIM_4D1`,
  `CN_TAKEN_BOOKS`, `CN_HOLD`, `NO_ITC`, `NOT_THIS_PERIOD`.
- `Workpaper.table4()`, `.bridge()` and `.ecrrs()` compute Table 4, the bridge
  from books ITC to ITC claimed (**the difference is nil for any data and any
  tagging** — `tests/test_gstr2b_itc_workflow.py` proves it with randomized
  worlds and random retagging) and the credit-reversal roll-forward.
- `Period` / `parse_period` read months from the labels used in practice
  (`Deffered In MAY-25`, `235 May-26`, `092026`). `itc_time_limit` is the
  section 16(4) limit (30 November after the financial year); open items carry
  `OK` / `EXPIRING` / `EXPIRED` against the return's due date.
- `cn_policy`: `books` reverses a purchase return in the month booked (the
  default), `2b` only once the supplier's credit note is in GSTR-2B.
- `reconcile()` returns its own copies of the documents: find them again by
  `_source_key` (sheet + first row), never by `id()`.

### `gstr2b_workpaper_hack.py`

The Excel workpaper and the command-line tool for the monthly process.
`build_workbook(work)` assembles the sheets (Summary, 2B Detail, Books Only,
Differences, Reclaimed 4(D)(1), Carry-forward, Party-wise, Notes);
`write_workpaper` also writes the two carry-forward CSVs and `summary.txt`.

- Every figure on the Summary is a live formula (`SUMIFS` over the two detail
  sheets keyed on the TREATMENT column) written with a cached value, so a
  viewer that does not recalculate still shows numbers and Excel recalculates
  on opening. Column positions are looked up by key (`SPEC_2B`, `SPEC_BOOKS`,
  `Layout`), never hard-coded.
- House style: navy/orange banners, peach headings, blue-on-pale-blue inputs
  and tags, black formulas, yellow for review. The checks block sits at the top
  of the Summary and is colour-coded `OK` / `CHECK` / `enter`.
- The workbook is deterministic: identical inputs give identical bytes (the
  run time goes in only if the CLI is not given `--no-timestamp`).
- The CLI reads the month and GSTIN from the portal's file name
  (`GSTR2B_<GSTIN>_<MMYYYY>.xlsx` or `<MMYYYY>_<GSTIN>_GSTR2B_<DDMMYYYY>.xlsx`),
  failing that from the `Read me` sheet inside the download (financial year,
  tax period, GSTIN, legal name — `read_portal_details`), or `--period`; it
  notes it when the file's own header disagrees with the month or GSTIN being
  run. A register or reclaim list with nothing in it keeps one blank row so its
  totals never sum themselves (a circular reference). Opening registers come from
  `--opening-dir`, or `--opening-books-deferred` / `--opening-2b-pending`,
  which accept `file#Sheet` so existing tracker sheets can be read in place.

### `gstr2b_app_hack.py` and the app

`gstr2b_workpaper_app.html` is the workpaper as an app for people who do not use a
terminal: open it, choose the GSTR-2B download and the purchase register, press
Build, save the Excel file. It is **generated** by `build_app()` from
`gstr2b_app_template.html` plus the source of the five engine modules
(`ENGINE_MODULES`, embedded as JSON), so it can never drift from the tested code.

- `run_job(options, progress)` is what the page calls inside the browser: it builds
  the CLI arguments from the page's choices, calls `gstr2b_workpaper_hack.run`, and
  returns `{"ok": True, "summary", "text", "files"}` or `{"ok": False, "error"}`.
  It **never raises** (a problem with the files must reach the screen in words);
  `list_sheets` feeds the sheet drop-downs; `example_files()` is the invented month
  behind "Try it with example data".
- The page runs Pyodide (pinned, `PYODIDE_BASE`) in a Web Worker so it stays
  responsive. **Privacy is enforced, not promised:** the template's
  Content-Security-Policy allows `connect-src` to `cdn.jsdelivr.net` only, and tests
  fail if the template ever contains `fetch(`, `XMLHttpRequest`, `sendBeacon`,
  `WebSocket`, a form, an image or any other host. Everything shown that came from a
  file is inserted as text (`h()` helper), never as HTML.
- An in-chat Artifact page cannot host it: that sandbox blocks the runtime's
  downloads and file saves. It is a file the user opens.
- Checked in a real browser (headless Chromium) on the example and on real files:
  the workbook was identical cell for cell to the command line's, only the CDN host
  was contacted, a `fetch` to any other host was blocked by the policy, and a second
  open worked with the network off (the browser cache holds the runtime).

### `xlsx_writer_hack.py`

A small writer: `Workbook`, `Sheet`, `Style`, `Dxf`. `Sheet.write` /
`Sheet.formula(…, cached=…)`, merges, `freeze_panes`, `autofilter`,
`validation_list`, `conditional_cell` / `conditional_expression`, `link`,
`print_titles`, `landscape`. Colours and borders are validated when written.
Output is deterministic (fixed zip timestamps). It writes workbooks only.

**Verifying spreadsheet output.** Unit tests parse the XML with the standard
library. Beyond that, the workbook was checked with tools that share no code
with the writer: `openpyxl` loads it, element order matches XlsxWriter's, and
LibreOffice recalculates every formula from scratch and agrees with the cached
values — `RecalculationTests` does the last of these when `soffice` is on the
path (it skips itself otherwise). Do the same after changing any formula.

**Editing the document-template modules:** the legal-draft strings embed
significant indentation and intentional blank/trailing whitespace that is
part of the rendered output. `ruff` is configured to skip line-length and
in-string whitespace rules on these files (see per-file-ignores in
`pyproject.toml`) — don't "clean up" that whitespace, as it changes the
generated documents.

## Conventions

When extending this codebase, mirror these patterns:

- **Dataclasses for records, classes for behavior.** `@dataclass` for value
  types (`Account`, `Transaction`, `LineItem`, `Invoice`, `GSTReturn`,
  `Employee`, `Payslip`, the income-tax and repository records); plain
  classes for services (`AccountsManager`, `PayrollManager`,
  `GuidanceNotesComplianceChecker`, the various framework components).
- **Validation via `ValueError`.** Existing checks: duplicate IDs, unknown
  accounts/employees, non-positive amounts/basic, negative TDS. Raise the
  same exception type for new preconditions.
- **File naming.** New core domains go in `<domain>_hack.py` at the repo
  root. Do not introduce nested packages.
- **Demo `__main__` block.** Every module ends with a runnable
  `if __name__ == "__main__":` block that exercises the happy path. For
  core modules it prints something verifiable; `ci.yml` runs the three
  core demos as a smoke check. Keep this block when adding modules.
- **Unit tests for new modules.** Add `tests/test_<domain>.py` using
  stdlib `unittest`. Tests import the module directly
  (`from payroll_hack import ...`) and run via
  `python3 -m unittest discover -s tests` (and under `pytest`).
- **Standard library only at runtime.** No third-party runtime packages
  unless the user explicitly asks. `dataclasses`, `datetime`, `typing`,
  `enum`, `json` are the imports in use today. `ruff` / `flake8` /
  `pytest` are dev-only.
- **Type hints.** Public methods are annotated. The existing style uses
  `Dict`, `List`, `Optional` from `typing` — match that style within a
  file rather than switching to PEP 585/604 builtins (`dict`, `list`,
  `X | None`) mid-file. Ruff is configured to leave this alone (`UP006`,
  `UP035`, `UP045` ignored).
- **Client data stays out of the repo.** The workpaper tools write client
  registers and workbooks into the working folder; `.gitignore` covers their
  usual names, but never commit real GSTINs, names or amounts — tests use
  invented data only.
- **Float money.** The codebase stores amounts as `float`. Be aware of
  precision when adding logic that compares balances (the reconciliation
  check uses a `< 0.01` tolerance). Don't silently switch to `Decimal`
  without flagging it.
- **Line length.** 100 chars (enforced by ruff), except the document-template
  modules listed in `pyproject.toml` per-file-ignores.

## What does NOT exist yet

Don't go looking for these — they aren't here:

- No `requirements.txt`, `setup.py`, or lockfile (only `pyproject.toml`
  with ruff config and project metadata; no installable runtime deps).
- No formatter (no `black`); no type checker (`mypy`, `pyright`).
- No persistence — all state is in-memory (manager `dict`s/`list`s,
  dataclass instances). The one exception is the reconciliation CLIs, which
  write their reports, the workpaper and the carry-forward CSVs.
- No web server and no API layer. `gstr2b_reconciliation_hack.py` and
  `gstr2b_workpaper_hack.py` have a CLI, and `gstr2b_workpaper_app.html` runs the
  workpaper in the browser with no server; every other module is import-only.
- No unit tests for the compliance/litigation modules yet — only the four
  core modules have `tests/`.

If a task requires any of the above, add it deliberately rather than
assuming it exists.

## Git workflow

- Default branch: `main`.
- Push feature work to a branch named `claude/<topic>-<suffix>` and open a
  PR against `main`.
- Push with `git push -u origin <branch-name>`.
- Both CI workflows must be green (`ci.yml`: unit tests + demos + ruff;
  `python-app.yml`: flake8 + pytest) before merging.
