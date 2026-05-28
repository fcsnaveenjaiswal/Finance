#!/usr/bin/env python3
"""
Finance Hack: Payroll Management System
Handles employee records, salary calculation, and payslip generation
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass
class Employee:
    """Represents an employee on payroll"""
    employee_id: str
    name: str
    designation: str
    monthly_basic: float
    pan: str = ""
    joining_date: datetime = field(default_factory=datetime.now)


@dataclass
class Payslip:
    """Monthly payslip for an employee"""
    payslip_id: str
    employee_id: str
    period_month: str  # Format: YYYY-MM
    basic: float
    hra: float
    special_allowance: float
    pf_deduction: float
    professional_tax: float
    tds: float = 0.0

    @property
    def gross_earnings(self) -> float:
        return self.basic + self.hra + self.special_allowance

    @property
    def total_deductions(self) -> float:
        return self.pf_deduction + self.professional_tax + self.tds

    @property
    def net_pay(self) -> float:
        return self.gross_earnings - self.total_deductions


class PayrollManager:
    """Manages employees and payroll generation"""

    HRA_RATE = 0.40              # 40% of basic
    SPECIAL_ALLOWANCE_RATE = 0.20  # 20% of basic
    PF_RATE = 0.12               # 12% of basic (employee contribution)
    PROFESSIONAL_TAX = 200.0     # flat monthly, indicative

    def __init__(self):
        self.employees: Dict[str, Employee] = {}
        self.payslips: List[Payslip] = []

    def add_employee(self, employee_id: str, name: str, designation: str,
                     monthly_basic: float, pan: str = "") -> Employee:
        """Add a new employee"""
        if employee_id in self.employees:
            raise ValueError(f"Employee {employee_id} already exists")
        if monthly_basic <= 0:
            raise ValueError("Monthly basic must be positive")

        employee = Employee(
            employee_id=employee_id,
            name=name,
            designation=designation,
            monthly_basic=monthly_basic,
            pan=pan,
        )
        self.employees[employee_id] = employee
        return employee

    def generate_payslip(self, payslip_id: str, employee_id: str,
                         period_month: str, tds: float = 0.0) -> Payslip:
        """Generate a payslip for an employee for a given month"""
        if employee_id not in self.employees:
            raise ValueError(f"Employee {employee_id} not found")
        if tds < 0:
            raise ValueError("TDS cannot be negative")

        emp = self.employees[employee_id]
        basic = emp.monthly_basic

        payslip = Payslip(
            payslip_id=payslip_id,
            employee_id=employee_id,
            period_month=period_month,
            basic=basic,
            hra=basic * self.HRA_RATE,
            special_allowance=basic * self.SPECIAL_ALLOWANCE_RATE,
            pf_deduction=basic * self.PF_RATE,
            professional_tax=self.PROFESSIONAL_TAX,
            tds=tds,
        )
        self.payslips.append(payslip)
        return payslip

    def get_payslips_for_employee(self, employee_id: str) -> List[Payslip]:
        """Return all payslips for an employee"""
        if employee_id not in self.employees:
            raise ValueError(f"Employee {employee_id} not found")
        return [p for p in self.payslips if p.employee_id == employee_id]

    def calculate_monthly_payroll(self, period_month: str) -> Dict:
        """Aggregate payroll totals for a given month"""
        month_slips = [p for p in self.payslips if p.period_month == period_month]
        return {
            "period_month": period_month,
            "employee_count": len(month_slips),
            "total_gross": sum(p.gross_earnings for p in month_slips),
            "total_pf": sum(p.pf_deduction for p in month_slips),
            "total_tds": sum(p.tds for p in month_slips),
            "total_net_pay": sum(p.net_pay for p in month_slips),
            "payslips": month_slips,
        }


# Example usage
if __name__ == "__main__":
    payroll = PayrollManager()

    payroll.add_employee("EMP001", "Naveen Jaiswal", "Software Engineer",
                         monthly_basic=50000, pan="ABCDE1234F")
    payroll.add_employee("EMP002", "Asha Rao", "Product Manager",
                         monthly_basic=80000, pan="FGHIJ5678K")

    slip1 = payroll.generate_payslip("PS-2026-05-001", "EMP001", "2026-05", tds=2500)
    payroll.generate_payslip("PS-2026-05-002", "EMP002", "2026-05", tds=6000)

    print(f"EMP001 Gross: ₹{slip1.gross_earnings}")
    print(f"EMP001 Net Pay: ₹{slip1.net_pay}")

    summary = payroll.calculate_monthly_payroll("2026-05")
    print(f"Total Net Payroll (May 2026): ₹{summary['total_net_pay']}")
