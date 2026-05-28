#!/usr/bin/env python3
"""
Finance Hack: Accounts Management System
Handles account creation, tracking, and reconciliation
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

# Account types whose balances increase on debit (and decrease on credit).
# Liability, Equity, and Income are the inverse.
_DEBIT_NORMAL = {"Asset", "Expense"}


@dataclass
class Account:
    """Represents a financial account"""
    account_id: str
    account_name: str
    account_type: str  # Asset, Liability, Equity, Income, Expense
    balance: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    description: str = ""


@dataclass
class Transaction:
    """Represents a financial transaction"""
    transaction_id: str
    from_account: str
    to_account: str
    amount: float
    date: datetime = field(default_factory=datetime.now)
    description: str = ""
    category: str = ""


class AccountsManager:
    """Manages accounts and transactions"""

    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        self.transactions: List[Transaction] = []

    def create_account(self, account_id: str, account_name: str, account_type: str,
                       description: str = "") -> Account:
        """Create a new account"""
        if account_id in self.accounts:
            raise ValueError(f"Account {account_id} already exists")

        account = Account(
            account_id=account_id,
            account_name=account_name,
            account_type=account_type,
            description=description
        )
        self.accounts[account_id] = account
        return account

    def get_account_balance(self, account_id: str) -> float:
        """Get account balance"""
        if account_id not in self.accounts:
            raise ValueError(f"Account {account_id} not found")
        return self.accounts[account_id].balance

    def record_transaction(self, transaction_id: str, from_account: str, to_account: str,
                          amount: float, description: str = "", category: str = "") -> Transaction:
        """Record a transaction between accounts.

        Treats the transaction as a journal entry: `from_account` is credited
        and `to_account` is debited. Whether that increases or decreases each
        account's balance depends on its type — Asset and Expense accounts have
        debit-normal balances; Liability, Equity, and Income accounts are
        credit-normal.
        """
        if from_account not in self.accounts or to_account not in self.accounts:
            raise ValueError("One or both accounts not found")

        if amount <= 0:
            raise ValueError("Amount must be positive")

        self._apply_credit(self.accounts[from_account], amount)
        self._apply_debit(self.accounts[to_account], amount)

        # Record transaction
        transaction = Transaction(
            transaction_id=transaction_id,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            description=description,
            category=category
        )
        self.transactions.append(transaction)
        return transaction

    @staticmethod
    def _apply_credit(account: Account, amount: float) -> None:
        if account.account_type in _DEBIT_NORMAL:
            account.balance -= amount
        else:
            account.balance += amount

    @staticmethod
    def _apply_debit(account: Account, amount: float) -> None:
        if account.account_type in _DEBIT_NORMAL:
            account.balance += amount
        else:
            account.balance -= amount

    def get_account_statement(self, account_id: str) -> Dict:
        """Get account statement"""
        if account_id not in self.accounts:
            raise ValueError(f"Account {account_id} not found")

        account = self.accounts[account_id]
        account_transactions = [t for t in self.transactions
                               if t.from_account == account_id or t.to_account == account_id]

        return {
            "account": account,
            "balance": account.balance,
            "transaction_count": len(account_transactions),
            "transactions": account_transactions
        }

    def reconcile_accounts(self) -> Dict:
        """Reconcile all accounts"""
        total_assets = sum(acc.balance for acc in self.accounts.values()
                          if acc.account_type == "Asset")
        total_liabilities = sum(acc.balance for acc in self.accounts.values()
                               if acc.account_type == "Liability")
        total_equity = sum(acc.balance for acc in self.accounts.values()
                          if acc.account_type == "Equity")

        return {
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "balance_check": abs(total_assets - (total_liabilities + total_equity)) < 0.01
        }


# Example usage
if __name__ == "__main__":
    manager = AccountsManager()

    # Create accounts
    manager.create_account("BANK001", "Main Bank Account", "Asset")
    manager.create_account("CASH001", "Cash Box", "Asset")
    manager.create_account("EQUITY001", "Owner's Equity", "Equity")

    # Record transactions
    manager.record_transaction("TXN001", "EQUITY001", "BANK001", 10000, "Initial deposit")

    # Get statement
    statement = manager.get_account_statement("BANK001")
    print(f"Bank Balance: {statement['balance']}")

    # Reconcile
    reconciliation = manager.reconcile_accounts()
    print(f"Accounts balanced: {reconciliation['balance_check']}")
