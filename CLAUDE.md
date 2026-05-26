# CLAUDE.md

Guidance for AI assistants working in this repository.

## Project overview

`fcsnaveenjaiswal/finance` is an educational/demo collection of Indian
finance, tax, and litigation-support utilities written in pure Python 3
(standard library at runtime; `ruff`, `flake8`, and `pytest` are dev
tools). Each module at the repo root is independent and models one
domain. They fall into two groups.

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

There is no framework, no web/CLI entry point, and no persistence layer
(all state is in-memory). The compliance/litigation modules generate large
formatted text documents via multi-line string templates.

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
├── tests/
│   ├── test_accounts.py
│   ├── test_gst.py
│   └── test_payroll.py
├── pyproject.toml                      # project metadata + ruff config
├── README.md
├── .github/workflows/
│   ├── ci.yml                          # unittest + demos + ruff
│   └── python-app.yml                  # flake8 + pytest
└── CLAUDE.md
```

Flat tree by design — no package, no `__init__.py` (in modules or in
`tests/`). Each domain module is importable on its own. Tests import the
modules directly because they are run from the repo root. Only the four
core modules currently have unit tests; the compliance/litigation modules
are covered only by their demo blocks.

## Running the code

Every module has a demo `__main__` block that doubles as a smoke check.
The core ones print verifiable values:

```bash
python3 accounts_hack.py   # "Bank Balance: 10000.0" and "Accounts balanced: True"
python3 gst_hack.py        # "Invoice Total: ₹7080.0" and "GST Payable: ₹0"
python3 payroll_hack.py    # "EMP001 Net Pay: ₹71300.0" and a monthly total
```

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
- `.github/workflows/ci.yml` — unit tests, the three core demo scripts,
  and `ruff check .`
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
  dataclass instances). Nothing is written to disk.
- No CLI, no web server, no API layer.
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
