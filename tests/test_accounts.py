"""Unit tests for accounts_hack."""

import unittest

from accounts_hack import Account, AccountsManager, Transaction


class CreateAccountTests(unittest.TestCase):
    def setUp(self):
        self.mgr = AccountsManager()

    def test_create_account_returns_account(self):
        acc = self.mgr.create_account("BANK001", "Main Bank", "Asset")
        self.assertIsInstance(acc, Account)
        self.assertEqual(acc.account_id, "BANK001")
        self.assertEqual(acc.balance, 0.0)

    def test_create_duplicate_account_raises(self):
        self.mgr.create_account("BANK001", "Main Bank", "Asset")
        with self.assertRaises(ValueError):
            self.mgr.create_account("BANK001", "Other", "Asset")

    def test_get_balance_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.get_account_balance("MISSING")


class RecordTransactionTests(unittest.TestCase):
    def setUp(self):
        self.mgr = AccountsManager()
        self.mgr.create_account("BANK001", "Main Bank", "Asset")
        self.mgr.create_account("CASH001", "Cash Box", "Asset")
        self.mgr.create_account("EQUITY001", "Owner's Equity", "Equity")
        self.mgr.create_account("LOAN001", "Bank Loan", "Liability")
        self.mgr.create_account("RENT001", "Rent Expense", "Expense")

    def test_amount_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.mgr.record_transaction("T", "BANK001", "CASH001", 0)
        with self.assertRaises(ValueError):
            self.mgr.record_transaction("T", "BANK001", "CASH001", -100)

    def test_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.record_transaction("T", "GHOST", "BANK001", 100)

    def test_equity_contribution_balances(self):
        # Owner contributes ₹10,000 of equity into the bank account.
        self.mgr.record_transaction("T1", "EQUITY001", "BANK001", 10000)
        self.assertEqual(self.mgr.get_account_balance("BANK001"), 10000)
        self.assertEqual(self.mgr.get_account_balance("EQUITY001"), 10000)
        self.assertTrue(self.mgr.reconcile_accounts()["balance_check"])

    def test_bank_to_cash_transfer_keeps_assets_constant(self):
        self.mgr.record_transaction("T1", "EQUITY001", "BANK001", 10000)
        self.mgr.record_transaction("T2", "BANK001", "CASH001", 2500)
        self.assertEqual(self.mgr.get_account_balance("BANK001"), 7500)
        self.assertEqual(self.mgr.get_account_balance("CASH001"), 2500)
        recon = self.mgr.reconcile_accounts()
        self.assertEqual(recon["total_assets"], 10000)
        self.assertTrue(recon["balance_check"])

    def test_loan_received_increases_liability_and_asset(self):
        self.mgr.record_transaction("T1", "LOAN001", "BANK001", 5000)
        self.assertEqual(self.mgr.get_account_balance("LOAN001"), 5000)
        self.assertEqual(self.mgr.get_account_balance("BANK001"), 5000)
        self.assertTrue(self.mgr.reconcile_accounts()["balance_check"])

    def test_expense_paid_from_cash(self):
        self.mgr.record_transaction("T1", "EQUITY001", "CASH001", 3000)
        self.mgr.record_transaction("T2", "CASH001", "RENT001", 1000)
        self.assertEqual(self.mgr.get_account_balance("CASH001"), 2000)
        self.assertEqual(self.mgr.get_account_balance("RENT001"), 1000)


class StatementAndReconcileTests(unittest.TestCase):
    def setUp(self):
        self.mgr = AccountsManager()
        self.mgr.create_account("BANK001", "Main Bank", "Asset")
        self.mgr.create_account("EQUITY001", "Owner's Equity", "Equity")
        self.mgr.record_transaction("T1", "EQUITY001", "BANK001", 10000)

    def test_statement_shape(self):
        stmt = self.mgr.get_account_statement("BANK001")
        self.assertEqual(stmt["balance"], 10000)
        self.assertEqual(stmt["transaction_count"], 1)
        self.assertEqual(len(stmt["transactions"]), 1)
        self.assertIsInstance(stmt["transactions"][0], Transaction)

    def test_statement_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.get_account_statement("MISSING")

    def test_reconcile_unbalanced_when_orphan_asset(self):
        # Manually inject an orphan asset to ensure the check catches drift.
        self.mgr.accounts["BANK001"].balance += 1
        self.assertFalse(self.mgr.reconcile_accounts()["balance_check"])


if __name__ == "__main__":
    unittest.main()
