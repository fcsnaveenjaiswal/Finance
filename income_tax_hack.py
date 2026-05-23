#!/usr/bin/env python3
"""
Income Tax Hack - Indian Income Tax Calculation
Comprehensive tax calculation system for FY 2025-26

Per Income Tax Act:
- Tax slabs for individuals
- Deductions under various sections
- Surcharge and Cess calculations
- ITR (Income Tax Return) generation
"""

from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass, field
from enum import Enum


class DeductionSection(Enum):
    """Income Tax Deduction Sections"""
    SECTION_80C = "80C"      # Investments (Max 1.5 Lakh)
    SECTION_80D = "80D"      # Health Insurance (Max 25,000)
    SECTION_80E = "80E"      # Interest on Education Loan (No limit)
    SECTION_80G = "80G"      # Charitable Donations (50% or 100%)
    SECTION_80TTA = "80TTA"  # Savings Account Interest (Max 10,000)
    SECTION_80U = "80U"      # Disability (Max 75,000)
    STANDARD_DEDUCTION = "Standard Deduction"  # 50,000


class IncomeSource(Enum):
    """Sources of Income"""
    SALARY = "Salary"
    BUSINESS = "Business/Profession"
    CAPITAL_GAINS = "Capital Gains"
    HOUSE_PROPERTY = "House Property"
    OTHER_SOURCES = "Other Sources"


@dataclass
class TaxSlab:
    """Tax Slab Information"""
    min_income: float
    max_income: float
    rate: float  # Tax rate as decimal


class IncomeTaxRates:
    """Income Tax Rates for FY 2025-26 (India)"""
    
    # Tax Slabs for Individuals (below 60 years)
    TAX_SLABS_INDIVIDUAL = [
        TaxSlab(0, 250000, 0.00),
        TaxSlab(250000, 500000, 0.05),
        TaxSlab(500000, 1000000, 0.20),
        TaxSlab(1000000, float('inf'), 0.30)
    ]
    
    # Senior Citizens (60-80 years)
    TAX_SLABS_SENIOR = [
        TaxSlab(0, 500000, 0.00),
        TaxSlab(500000, 1000000, 0.20),
        TaxSlab(1000000, float('inf'), 0.30)
    ]
    
    # Super Senior Citizens (80+ years)
    TAX_SLABS_SUPER_SENIOR = [
        TaxSlab(0, 500000, 0.00),
        TaxSlab(500000, 1000000, 0.20),
        TaxSlab(1000000, float('inf'), 0.30)
    ]
    
    # Surcharge rates
    SURCHARGE_INDIVIDUAL = {
        (5000000, 10000000): 0.10,      # 10%
        (10000000, 20000000): 0.15,     # 15%
        (20000000, 50000000): 0.25,     # 25%
        (50000000, float('inf')): 0.37  # 37%
    }
    
    # Health & Education Cess
    CESS_RATE = 0.04  # 4%


@dataclass
class Income:
    """Income Source Details"""
    source: IncomeSource
    gross_income: float
    deductions_allowed: float = 0  # For business
    
    def get_net_income(self) -> float:
        """Get net income after deductions"""
        if self.source == IncomeSource.BUSINESS:
            return max(0, self.gross_income - self.deductions_allowed)
        return self.gross_income


@dataclass
class Deduction:
    """Tax Deduction Details"""
    section: DeductionSection
    amount: float
    applicable: bool = True
    section_limit: float = float('inf')
    
    def get_allowed_deduction(self) -> float:
        """Get deduction limited by section cap"""
        if not self.applicable:
            return 0
        return min(self.amount, self.section_limit)


@dataclass
class TaxPayer:
    """Tax Payer Information"""
    pan: str
    name: str
    age: int  # Age as on 31st March
    incomes: List[Income] = field(default_factory=list)
    deductions: List[Deduction] = field(default_factory=list)
    financial_year: str = "2025-26"
    
    def get_applicable_tax_slabs(self) -> List[TaxSlab]:
        """Get applicable tax slabs based on age"""
        if self.age >= 80:
            return IncomeTaxRates.TAX_SLABS_SUPER_SENIOR
        elif self.age >= 60:
            return IncomeTaxRates.TAX_SLABS_SENIOR
        else:
            return IncomeTaxRates.TAX_SLABS_INDIVIDUAL
    
    def calculate_total_income(self) -> float:
        """Calculate total income from all sources"""
        return sum(income.get_net_income() for income in self.incomes)
    
    def calculate_total_deductions(self) -> Dict[str, float]:
        """Calculate total deductions"""
        deduction_details = {}
        total_deductions = 0
        
        for deduction in self.deductions:
            allowed = deduction.get_allowed_deduction()
            deduction_details[deduction.section.value] = allowed
            total_deductions += allowed
        
        return {
            "breakdown": deduction_details,
            "total": total_deductions
        }
    
    def calculate_taxable_income(self) -> float:
        """Calculate taxable income (Total Income - Deductions)"""
        total_income = self.calculate_total_income()
        deductions = self.calculate_total_deductions()
        return max(0, total_income - deductions["total"])
    
    def calculate_income_tax(self) -> float:
        """Calculate income tax as per tax slabs"""
        taxable_income = self.calculate_taxable_income()
        slabs = self.get_applicable_tax_slabs()
        
        tax = 0
        for slab in slabs:
            if taxable_income > slab.min_income:
                taxable_in_slab = min(taxable_income, slab.max_income) - slab.min_income
                tax += taxable_in_slab * slab.rate
        
        return tax
    
    def calculate_surcharge(self) -> float:
        """Calculate surcharge based on total income"""
        total_income = self.calculate_total_income()
        income_tax = self.calculate_income_tax()
        
        surcharge = 0
        for (min_amt, max_amt), rate in IncomeTaxRates.SURCHARGE_INDIVIDUAL.items():
            if min_amt <= total_income < max_amt:
                surcharge = income_tax * rate
                break
        
        return surcharge
    
    def calculate_cess(self) -> float:
        """Calculate Health and Education Cess"""
        income_tax = self.calculate_income_tax()
        surcharge = self.calculate_surcharge()
        return (income_tax + surcharge) * IncomeTaxRates.CESS_RATE
    
    def calculate_total_tax_liability(self) -> Dict:
        """Calculate total tax liability"""
        income_tax = self.calculate_income_tax()
        surcharge = self.calculate_surcharge()
        cess = self.calculate_cess()
        total_tax = income_tax + surcharge + cess
        
        return {
            "income_tax": income_tax,
            "surcharge": surcharge,
            "cess": cess,
            "total_tax_liability": total_tax
        }
    
    def generate_itr_summary(self) -> Dict:
        """Generate ITR (Income Tax Return) Summary"""
        total_income = self.calculate_total_income()
        deductions = self.calculate_total_deductions()
        taxable_income = self.calculate_taxable_income()
        tax_liability = self.calculate_total_tax_liability()
        
        return {
            "pan": self.pan,
            "name": self.name,
            "financial_year": self.financial_year,
            "age": self.age,
            "income_sources": [
                {
                    "source": income.source.value,
                    "gross": income.gross_income,
                    "net": income.get_net_income()
                }
                for income in self.incomes
            ],
            "total_income": total_income,
            "deductions": deductions,
            "taxable_income": taxable_income,
            "tax_calculation": tax_liability,
            "filing_status": "Mandatory" if total_income > 500000 else "Optional"
        }


# Example usage
if __name__ == "__main__":
    # Create taxpayer
    taxpayer = TaxPayer(
        pan="ABCDE1234F",
        name="John Doe",
        age=35
    )
    
    # Add incomes
    taxpayer.incomes.append(Income(IncomeSource.SALARY, 800000))
    taxpayer.incomes.append(Income(IncomeSource.OTHER_SOURCES, 50000))
    
    # Add deductions
    taxpayer.deductions.append(
        Deduction(DeductionSection.SECTION_80C, 150000, True, 150000)
    )
    taxpayer.deductions.append(
        Deduction(DeductionSection.SECTION_80D, 25000, True, 25000)
    )
    taxpayer.deductions.append(
        Deduction(DeductionSection.STANDARD_DEDUCTION, 50000, True)
    )
    
    # Generate ITR
    itr = taxpayer.generate_itr_summary()
    
    print("Income Tax Return Summary:")
    print(f"PAN: {itr['pan']}")
    print(f"Total Income: ₹{itr['total_income']}")
    print(f"Taxable Income: ₹{itr['taxable_income']}")
    print(f"Income Tax: ₹{itr['tax_calculation']['income_tax']}")
    print(f"Surcharge: ₹{itr['tax_calculation']['surcharge']}")
    print(f"Cess: ₹{itr['tax_calculation']['cess']}")
    print(f"Total Tax Liability: ₹{itr['tax_calculation']['total_tax_liability']}")
