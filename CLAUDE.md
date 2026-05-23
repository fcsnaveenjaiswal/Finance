# CLAUDE.md

Guidance for AI assistants working in this repository.

## Project overview

`fcsnaveenjaiswal/finance` is a small, educational/demo collection of
finance domain utilities written in pure Python 3 (standard library at
runtime; `ruff` is the only dev tool). Three independent modules at the
repo root each model one domain:

- **Accounts** — double-entry accounts, transactions, statements, and a
  global reconciliation check that enforces `assets == liabilities + equity`.
- **GST** — Indian Goods and Services Tax: invoice generation
  (intra-state SGST+CGST vs. inter-state IGST) and monthly return summaries
  with input tax credit.
- **Payroll** — Indian payroll: employees, monthly payslip generation
  with HRA / special allowance / PF / professional tax / TDS, and monthly
  aggregates.

There is no framework, no web/CLI entry point, and no persistence layer
(all state is in-memory).

## Repository layout

```
.
├── accounts_hack.py        # Accounts/transactions/reconciliation
├── gst_hack.py             # GST invoices and returns
├── payroll_hack.py         # Employees, payslips, monthly payroll
├── tests/
│   ├── test_accounts.py
│   ├── test_gst.py
│   └── test_payroll.py
├── pyproject.toml          # project metadata + ruff config
├── .github/workflows/ci.yml
└── CLAUDE.md
```

Flat tree by design — no package, no `__init__.py` (in modules or in
`tests/`). Each domain module is importable on its own. Tests import the
modules directly because they are run from the repo root.

## Running the code

Each module has a demo `__main__` block that doubles as a smoke check:

```bash
python3 accounts_hack.py   # "Bank Balance: 10000.0" and "Accounts balanced: True"
python3 gst_hack.py        # "Invoice Total: ₹7080.0" and "GST Payable: ₹0"
python3 payroll_hack.py    # "EMP001 Net Pay: ₹71300.0" and a monthly total
```

Run the unit tests with the stdlib `unittest` runner from the repo root:

```bash
python3 -m unittest discover -s tests -v
```

Lint with ruff (install with `pip install ruff` if not present):

```bash
ruff check .
```

CI runs all three of the above on every push and PR — see
`.github/workflows/ci.yml`.

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

## Conventions

When extending this codebase, mirror these patterns:

- **Dataclasses for records, classes for behavior.** `@dataclass` for value
  types (`Account`, `Transaction`, `LineItem`, `Invoice`, `GSTReturn`,
  `Employee`, `Payslip`); plain classes for services (`AccountsManager`,
  `PayrollManager`).
- **Validation via `ValueError`.** Existing checks: duplicate IDs, unknown
  accounts/employees, non-positive amounts/basic, negative TDS. Raise the
  same exception type for new preconditions.
- **File naming.** New domains go in `<domain>_hack.py` at the repo root.
  Do not introduce nested packages.
- **Demo `__main__` block.** Every module ends with a runnable
  `if __name__ == "__main__":` block that exercises the happy path and
  prints something verifiable. Keep this when adding modules — it is run
  by CI as a smoke check.
- **Unit tests for new modules.** Add `tests/test_<domain>.py` using
  stdlib `unittest`. Tests import the module directly
  (`from payroll_hack import ...`) and run via
  `python3 -m unittest discover -s tests`.
- **Standard library only at runtime.** No third-party runtime packages
  unless the user explicitly asks. `dataclasses`, `datetime`, `typing`,
  `enum` are the imports in use today. `ruff` is dev-only.
- **Type hints.** Public methods are annotated. The existing style uses
  `Dict`, `List`, `Optional` from `typing` — match that style within a
  file rather than switching to PEP 585 builtins (`dict`, `list`) mid-file.
  Ruff is configured to leave this alone (`UP006`, `UP035` ignored).
- **Float money.** The codebase stores amounts as `float`. Be aware of
  precision when adding logic that compares balances (the reconciliation
  check uses a `< 0.01` tolerance). Don't silently switch to `Decimal`
  without flagging it.
- **Line length.** 100 chars (enforced by ruff).

## What does NOT exist yet

Don't go looking for these — they aren't here:

- No `requirements.txt`, `setup.py`, or lockfile (only `pyproject.toml`
  with ruff config and project metadata; no installable runtime deps).
- No formatter beyond ruff (no `black`); no type checker (`mypy`,
  `pyright`).
- No persistence — state lives in `AccountsManager.accounts` /
  `.transactions`, in `Invoice` / `GSTReturn` instances, and in
  `PayrollManager.employees` / `.payslips`. Nothing is written to disk.
- No CLI, no web server, no API layer.

If a task requires any of the above, add it deliberately rather than
assuming it exists.

## Git workflow

- Default branch: `main`.
- Push feature work to a branch named `claude/<topic>-<suffix>` and open a
  PR against `main`.
- Push with `git push -u origin <branch-name>`.
- CI must be green (unit tests, demo scripts, ruff) before merging.
