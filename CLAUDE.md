# CLAUDE.md

Guidance for AI assistants working in this repository.

## Project overview

`fcsnaveenjaiswal/finance` is a small, educational/demo collection of
finance domain utilities written in pure Python 3 (standard library only).
Two independent modules at the repo root each model one domain:

- **Accounts** — double-entry style accounts, transactions, statements, and
  a global reconciliation check.
- **GST** — Indian Goods and Services Tax: invoice generation
  (intra-state SGST+CGST vs. inter-state IGST) and monthly return summaries
  with input tax credit.

There is no framework, no web/CLI entry point, no persistence layer
(all state is in-memory), and no third-party dependencies.

## Repository layout

```
.
├── accounts_hack.py   # Accounts/transactions/reconciliation
├── gst_hack.py        # GST invoices and returns
└── CLAUDE.md
```

Flat tree by design — no package, no `__init__.py`. Each module is
importable on its own and standalone.

## Running the code

Each module ships with a demo block that doubles as the only smoke test:

```bash
python3 accounts_hack.py   # prints "Bank Balance: 10000.0" and "Accounts balanced: False"
python3 gst_hack.py        # prints "Invoice Total: ₹7080.0" and "GST Payable: ₹0"
```

The `Accounts balanced: False` output is expected with the current demo —
see the note on `record_transaction` semantics below.

Run these after any change as a sanity check — there is no test suite,
linter config, or CI yet.

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
  - `reconcile_accounts()` → totals plus `balance_check`, which asserts
    `abs(assets - (liabilities + equity)) < 0.01`.

`account_type` is a free-form string but the codebase uses these five
values: `"Asset"`, `"Liability"`, `"Equity"`, `"Income"`, `"Expense"`.
Reconciliation only sums the first three.

**Important quirk:** `record_transaction` unconditionally does
`from.balance -= amount; to.balance += amount` regardless of account type.
That is *not* true double-entry semantics — in real accounting, equity,
liability, and income accounts increase (are credited) when value flows
out of them. As a result, the bundled demo (equity → bank, ₹10 000) ends
with `EQUITY001` at `-10 000`, `BANK001` at `+10 000`, and `balance_check`
returns `False`. Treat this as a known limitation: do not "fix" the demo
output by re-jiggering the transaction. If you want a true accounting
invariant, change `record_transaction` to credit/debit based on
`account_type`, and update both the demo and this note.

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
- `GSTReturn` (dataclass): monthly aggregation by GST rate. `add_outward_supply`,
  `add_inward_supply`, `add_itc` accumulate by rate; `calculate_gst_liability`
  applies `output_tax - input_credit` (floored at 0); `generate_return_summary`
  returns the full report.

Indian context: GSTIN strings (e.g. `27AAFCD1234A1Z5`) and the ₹ symbol
appear in the demo.

## Conventions

When extending this codebase, mirror these patterns:

- **Dataclasses for records, classes for behavior.** `@dataclass` for value
  types (`Account`, `Transaction`, `LineItem`, `Invoice`, `GSTReturn`);
  plain classes for services (`AccountsManager`).
- **Validation via `ValueError`.** Existing checks: duplicate IDs, unknown
  accounts, non-positive amounts. Raise the same exception type for new
  preconditions.
- **File naming.** New domains go in `<domain>_hack.py` at the repo root
  (e.g. `payroll_hack.py`). Do not introduce nested packages until there
  is a real reason.
- **Demo `__main__` block.** Every module ends with a runnable
  `if __name__ == "__main__":` block that exercises the happy path and
  prints something verifiable. Keep this when adding modules — it is the
  current smoke test.
- **Standard library only.** No third-party packages unless the user
  explicitly asks. `dataclasses`, `datetime`, `typing`, `enum` are the
  imports in use today.
- **Type hints.** Public methods are annotated. The existing style uses
  `Dict`, `List`, `Optional` from `typing` — match that style within a
  file rather than switching to PEP 585 builtins (`dict`, `list`) mid-file.
- **Float money.** The codebase stores amounts as `float`. Be aware of
  precision when adding logic that compares balances (the reconciliation
  check uses a `< 0.01` tolerance). Don't silently switch to `Decimal`
  without flagging it.

## What does NOT exist yet

Don't go looking for these — they aren't here:

- No `tests/` directory and no test framework configured.
- No `requirements.txt`, `pyproject.toml`, `setup.py`, or lockfile.
- No linter / formatter config (no `ruff`, `black`, `flake8`, `mypy`).
- No CI workflows (`.github/workflows/` is absent).
- No persistence — state lives in `AccountsManager.accounts` /
  `.transactions` and inside `Invoice` / `GSTReturn` instances. Nothing
  is written to disk.
- No CLI, no web server, no API layer.

If a task requires any of the above, add it deliberately rather than
assuming it exists.

## Git workflow

- Default branch: `main`.
- This session's working branch: `claude/claude-md-docs-0dAxp` — develop,
  commit, and push here.
- Push with `git push -u origin <branch-name>`.
- Do not open a pull request unless the user explicitly asks.
