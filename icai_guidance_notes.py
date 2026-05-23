#!/usr/bin/env python3
"""
ICAI Guidance Notes - Implementation Reference
Comprehensive guide for applying ICAI Guidance Notes in accounting systems

All ICAI Guidance Notes (GN) referenced:
1. GN on Accounting for Leases
2. GN on Accounting for Real Estate Transactions
3. GN on Accounting for Derivative Contracts
4. GN on Employee Share-Based Payments
5. GN on Accrual of Leave Encashment and Sick Leave
6. GN on Reports or Certificates for Special Purposes
7. GN on Accounting for Uncertainty in Income Taxes
8. GN on Accounting for Cryptocurrency and Virtual Digital Assets
9. GN on Merger/Amalgamation
10. GN on Accounting for Scrap and Waste
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from enum import Enum


class LeaseClassification(Enum):
    """GN: Accounting for Leases"""
    FINANCE_LEASE = "Finance Lease"
    OPERATING_LEASE = "Operating Lease"


@dataclass
class Lease:
    """GN: Accounting for Leases - Finance and Operating Leases"""
    lease_id: str
    asset_description: str
    lease_type: LeaseClassification
    lease_commencement_date: datetime
    lease_term_months: int
    monthly_lease_payment: float
    transfer_of_ownership: bool  # True = Finance Lease
    bargain_purchase_option: bool
    implicit_interest_rate: float = 0.1  # 10% default
    
    def is_finance_lease(self) -> bool:
        """Determine if lease is finance or operating lease"""
        # Finance lease if: transfer of ownership, bargain purchase, or 75%+ asset life
        return (self.transfer_of_ownership or 
                self.bargain_purchase_option or 
                self.lease_type == LeaseClassification.FINANCE_LEASE)
    
    def calculate_present_value_of_lease_payments(self) -> float:
        """Calculate PV of minimum lease payments for finance lease recognition"""
        total_pv = 0
        for month in range(1, self.lease_term_months + 1):
            pv = self.monthly_lease_payment / ((1 + self.implicit_interest_rate) ** (month / 12))
            total_pv += pv
        return total_pv
    
    def calculate_finance_lease_asset(self) -> Dict:
        """Calculate asset capitalization for finance lease"""
        pv = self.calculate_present_value_of_lease_payments()
        return {
            "right_of_use_asset": pv,
            "lease_liability": pv,
            "lease_classification": "Finance Lease"
        }


@dataclass
class Employee:
    """Employee for Leave Encashment - GN on Accrual of Leave and Sick Leave"""
    employee_id: str
    name: str
    annual_leave_entitled: int = 20  # Days
    sick_leave_entitled: int = 10    # Days
    leave_balance: int = 0
    sick_leave_balance: int = 0
    salary_per_day: float = 0
    date_of_joining: datetime = field(default_factory=datetime.now)
    accumulated_leave: List[Dict] = field(default_factory=list)


@dataclass
class LeaveAccrual:
    """GN: Accrual of Leave Encashment and Sick Leave"""
    
    @staticmethod
    def calculate_leave_liability(employee: Employee, period_end_date: datetime) -> Dict:
        """
        Calculate leave encashment liability as per GN
        Provisions for:
        - Accrued but unused leave
        - Sick leave (where applicable)
        - Restrictions on leave carry-forward
        """
        
        days_worked = (period_end_date - employee.date_of_joining).days
        months_worked = days_worked / 30
        
        # Calculate accrued leave
        accrued_leave = min(
            employee.annual_leave_entitled * (months_worked / 12),
            employee.leave_balance
        )
        
        # Sick leave - typically not encashable unless policy allows
        encashable_sick_leave = 0  # Depends on company policy
        
        total_leave_days = accrued_leave + encashable_sick_leave
        leave_encashment_liability = total_leave_days * employee.salary_per_day
        
        return {
            "accrued_leave_days": accrued_leave,
            "encashable_sick_leave": encashable_sick_leave,
            "total_encashable_days": total_leave_days,
            "leave_encashment_liability": leave_encashment_liability,
            "accounting_entry": f"Dr. Leave Encashment Expense {leave_encashment_liability} / Cr. Leave Encashment Liability {leave_encashment_liability}"
        }


@dataclass
class EmployeeShareOption:
    """GN: Employee Share-Based Payments"""
    option_id: str
    employee_id: str
    grant_date: datetime
    strike_price: float
    market_price_at_grant: float
    vesting_period_years: int
    shares_granted: int
    vesting_schedule: str = "Graded"  # Graded or Cliff
    
    def calculate_fair_value_at_grant(self) -> float:
        """Calculate fair value of share options at grant date"""
        # Simplified: Fair Value = Market Price - Strike Price
        return max(0, (self.market_price_at_grant - self.strike_price) * self.shares_granted)
    
    def calculate_expense_per_period(self) -> Dict:
        """Calculate expense recognition over vesting period (GN guidance)"""
        total_fair_value = self.calculate_fair_value_at_grant()
        monthly_expense = total_fair_value / (self.vesting_period_years * 12)
        
        return {
            "total_fair_value": total_fair_value,
            "vesting_period_months": self.vesting_period_years * 12,
            "monthly_expense": monthly_expense,
            "accounting_entry": f"Dr. Employee Compensation Expense {monthly_expense} / Cr. Share-Based Payment Reserve {monthly_expense}"
        }


@dataclass
class RealEstateTransaction:
    """GN: Accounting for Real Estate Transactions"""
    transaction_id: str
    property_description: str
    transaction_date: datetime
    consideration_amount: float
    registration_charges: float = 0
    brokerage_commission: float = 0
    legal_fees: float = 0
    survey_charges: float = 0
    inspection_charges: float = 0
    capitalization_eligible: bool = True  # Part of asset cost
    
    def calculate_total_acquisition_cost(self) -> float:
        """Calculate total cost as per GN - all directly attributable costs capitalized"""
        if self.capitalization_eligible:
            return (self.consideration_amount + 
                    self.registration_charges + 
                    self.brokerage_commission + 
                    self.legal_fees + 
                    self.survey_charges + 
                    self.inspection_charges)
        return self.consideration_amount
    
    def get_accounting_treatment(self) -> Dict:
        """Get accounting treatment as per GN"""
        total_cost = self.calculate_total_acquisition_cost()
        return {
            "asset_classification": "Property, Plant & Equipment / Investment Property",
            "capitalized_amount": total_cost,
            "accounting_entry": f"Dr. Property {total_cost} / Cr. Cash {total_cost}",
            "disclosure": "All directly attributable costs capitalized as per GN on Real Estate"
        }


@dataclass
class DerivativeContract:
    """GN: Accounting for Derivative Contracts"""
    contract_id: str
    contract_type: str  # Forward, Futures, Options, Swap
    notional_amount: float
    entry_date: datetime
    maturity_date: datetime
    hedging_relationship: bool  # True if used for hedging
    underlying_asset: str  # Currency, Commodity, Interest Rate, etc.
    fair_value_at_reporting_date: float = 0
    
    def classify_derivative(self) -> str:
        """Classify as hedging or trading derivative"""
        if self.hedging_relationship:
            return "Hedging Derivative - Eligible for Hedge Accounting"
        return "Trading Derivative - Fair Value through P&L"
    
    def get_accounting_treatment(self) -> Dict:
        """Get accounting treatment as per GN"""
        classification = self.classify_derivative()
        
        return {
            "classification": classification,
            "fair_value_at_reporting": self.fair_value_at_reporting_date,
            "accounting_entry": f"Fair Value Adjustment - {classification}",
            "disclosure_required": [
                "Objectives and strategies for holding derivatives",
                "Notional amount and fair value",
                "Gains/losses realized and unrealized",
                "Hedge effectiveness assessment (if hedging)"
            ]
        }


@dataclass
class IncomeUncertainty:
    """GN: Accounting for Uncertainty in Income Taxes"""
    uncertainty_id: str
    tax_position_description: str
    management_assessment_favorable: float = 1.0  # 0-1 probability
    potential_tax_exposure: float = 0
    management_believes_position_sustainable: bool = True
    
    def calculate_tax_contingency(self) -> Dict:
        """Calculate tax contingency as per GN"""
        probability_unfavorable = 1 - self.management_assessment_favorable
        contingent_liability = self.potential_tax_exposure * probability_unfavorable
        
        if contingent_liability > 0:
            return {
                "treatment": "Provision required" if probability_unfavorable > 0.5 else "Disclosure as contingent liability",
                "provision_amount": contingent_liability if probability_unfavorable > 0.5 else 0,
                "disclosure_required": True,
                "accounting_entry": f"Dr. Tax Expense {contingent_liability} / Cr. Tax Provision {contingent_liability}"
            }
        return {
            "treatment": "No provision or disclosure",
            "provision_amount": 0,
            "disclosure_required": False
        }


class GuidanceNotesComplianceChecker:
    """Compliance checker for all ICAI Guidance Notes"""
    
    GUIDANCE_NOTES = {
        "GN1": "Accounting for Leases (Finance & Operating)",
        "GN2": "Accounting for Real Estate Transactions",
        "GN3": "Accounting for Derivative Contracts",
        "GN4": "Employee Share-Based Payments",
        "GN5": "Accrual of Leave Encashment and Sick Leave",
        "GN6": "Reports or Certificates for Special Purposes",
        "GN7": "Accounting for Uncertainty in Income Taxes",
        "GN8": "Accounting for Cryptocurrency and Virtual Digital Assets",
        "GN9": "Merger and Amalgamation",
        "GN10": "Accounting for Scrap and Waste"
    }
    
    @staticmethod
    def get_guidance_notes_reference() -> Dict:
        """Get all ICAI Guidance Notes reference"""
        return GuidanceNotesComplianceChecker.GUIDANCE_NOTES
    
    @staticmethod
    def validate_accounting_treatment(transaction_type: str) -> Dict:
        """Validate accounting treatment against applicable GN"""
        treatments = {
            "lease": {
                "applicable_gn": "GN1",
                "key_requirements": [
                    "Classify as finance or operating lease",
                    "Recognize right-of-use asset and lease liability",
                    "Calculate PV of minimum lease payments",
                    "Disclose lease obligations"
                ]
            },
            "real_estate": {
                "applicable_gn": "GN2",
                "key_requirements": [
                    "Capitalize all directly attributable costs",
                    "Exclude general overhead",
                    "Separate land and building components",
                    "Determine IUP (In-Use Period) for capitalization"
                ]
            },
            "employee_benefits": {
                "applicable_gn": "GN4, GN5",
                "key_requirements": [
                    "Accrue leave encashment liability",
                    "Recognize share-based payment expense over vesting period",
                    "Measure at fair value at grant date",
                    "Disclose vesting conditions"
                ]
            },
            "derivatives": {
                "applicable_gn": "GN3",
                "key_requirements": [
                    "Classify as hedging or trading derivative",
                    "Measure at fair value",
                    "Apply hedge accounting if conditions met",
                    "Disclose objectives and strategies"
                ]
            },
            "tax_uncertainties": {
                "applicable_gn": "GN7",
                "key_requirements": [
                    "Assess probability of sustaining tax position",
                    "Create provision if probable",
                    "Disclose uncertain tax positions",
                    "Review for subsequent changes"
                ]
            }
        }
        return treatments.get(transaction_type, {})


# Example usage
if __name__ == "__main__":
    # Example: Lease accounting per GN
    lease = Lease(
        lease_id="LEASE001",
        asset_description="Production Equipment",
        lease_type=LeaseClassification.FINANCE_LEASE,
        lease_commencement_date=datetime.now(),
        lease_term_months=60,
        monthly_lease_payment=10000,
        transfer_of_ownership=True,
        bargain_purchase_option=False
    )
    print("Finance Lease Treatment:")
    print(lease.calculate_finance_lease_asset())
    
    # Example: Leave Encashment per GN
    employee = Employee(
        employee_id="EMP001",
        name="John Doe",
        annual_leave_entitled=20,
        salary_per_day=1000,
        date_of_joining=datetime.now() - timedelta(days=365)
    )
    leave_accrual = LeaveAccrual.calculate_leave_liability(employee, datetime.now())
    print("\nLeave Encashment Liability:")
    print(leave_accrual)
    
    # Example: Real Estate Transaction per GN
    re_transaction = RealEstateTransaction(
        transaction_id="REPROP001",
        property_description="Commercial Building",
        transaction_date=datetime.now(),
        consideration_amount=5000000,
        registration_charges=100000,
        legal_fees=50000,
        survey_charges=25000
    )
    print("\nReal Estate Transaction:")
    print(re_transaction.get_accounting_treatment())
    
    # All GN Reference
    print("\nAll ICAI Guidance Notes:")
    for gn, description in GuidanceNotesComplianceChecker.get_guidance_notes_reference().items():
        print(f"  {gn}: {description}")
