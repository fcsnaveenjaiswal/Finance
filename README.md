# 💰 Finance Repository - Comprehensive Finance Management System

**ICAI Accounting Standards | Indian Tax Laws | GST Compliance | Professional Legal Drafting**

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Repository Structure](#repository-structure)
3. [Features](#features)
4. [Installation & Usage](#installation--usage)
5. [Module Documentation](#module-documentation)
6. [Compliance Standards](#compliance-standards)
7. [Examples & Use Cases](#examples--use-cases)
8. [License & Support](#license--support)

---

## 🎯 Overview

This comprehensive Finance Repository provides **end-to-end financial management solutions** for Indian businesses, combining:

✅ **ICAI Accounting Standards** (AS 1-29 Compliant)  
✅ **Income Tax Compliance** (Income Tax Act, 1961)  
✅ **GST Management & Compliance** (GST Act, 2017)  
✅ **Professional Legal Case Drafting** (Tax disputes & appeals)  
✅ **ICAI Guidance Notes** (All 10 Guidance Notes implemented)  

---

## 📁 Repository Structure

```
Finance/
├── accounts_hack.py                 # ICAI AS 1-29: Accounting Standards
├── income_tax_hack.py               # Income Tax Calculation (FY 2025-26)
├── gst_hack.py                      # GST Management & Returns
├── icai_guidance_notes.py           # All ICAI Guidance Notes (GN1-GN10)
├── gst_case_drafting.py             # GST Case Drafting & Legal Templates
├── gstr2b_reconciliation_hack.py    # GSTR-2B vs Books matching engine + quick text/CSV CLI
├── gstr2b_itc_workflow_hack.py      # Monthly ITC process: Table 4, bridge, carry-forward
├── gstr2b_workpaper_hack.py         # Monthly ITC workpaper in Excel (offline CLI)
├── xlsx_writer_hack.py              # Stdlib-only .xlsx writer used by the workpaper
└── README.md                        # This file
```

---

## ✨ Features

### 1. **Accounts Management (ICAI AS Compliant)**
- ✅ Fixed Asset Management with 4 Depreciation Methods
  - Straight Line Method (SLM)
  - Written Down Value (WDV)
  - Sum of Years Digits (SYD)
  - Units of Production (UOP)
- ✅ Investment Accounting per AS 13
- ✅ Inventory Valuation per AS 2
- ✅ Provision Recognition per AS 29
- ✅ Balance Sheet Generation
- ✅ Income Statement/P&L Generation
- ✅ Complete Financial Statements Package

### 2. **Income Tax Management**
- ✅ FY 2025-26 Tax Slabs
  - Individuals (< 60 years)
  - Senior Citizens (60-80 years)
  - Super Senior Citizens (80+ years)
- ✅ Multiple Income Sources
  - Salary
  - Business/Profession
  - Capital Gains
  - House Property
  - Other Sources
- ✅ All Tax Deductions
  - Section 80C (₹1.5L)
  - Section 80D (₹25K)
  - Section 80E (Education Loan Interest)
  - Section 80G (Charitable Donations)
  - Section 80TTA (₹10K)
  - Section 80U (₹75K)
  - Standard Deduction (₹50K)
- ✅ Surcharge Calculation
- ✅ Health & Education Cess (4%)
- ✅ ITR Summary Generation

### 3. **GST Management**
- ✅ Invoice Generation with Line Items
- ✅ SGST, CGST, IGST Calculation
- ✅ HSN Codes & GST Rates
- ✅ Input Tax Credit (ITC) Tracking
- ✅ GST Returns (B2B, B2C, etc.)
- ✅ GST Liability Calculation
- ✅ GST Compliance Reporting

### 4. **ICAI Guidance Notes Implementation**
- ✅ GN1: Lease Accounting (Finance & Operating)
- ✅ GN2: Real Estate Transactions
- ✅ GN3: Derivative Contracts
- ✅ GN4: Employee Share-Based Payments
- ✅ GN5: Leave Encashment & Sick Leave
- ✅ GN6: Reports for Special Purposes
- ✅ GN7: Tax Uncertainty Accounting
- ✅ GN8: Cryptocurrency & Virtual Assets
- ✅ GN9: Merger/Amalgamation
- ✅ GN10: Scrap & Waste Accounting

### 5. **GST Case Drafting & Legal Templates**
- ✅ **ASMT-10**: Discrepancy Notices
  - Professional notice generation
  - Expert reply templates
  - Supporting document guidelines
- ✅ **DRC-01A**: Intimation of Liability
  - Pre-demand notices
  - Payment options & appeals
  - Compliance options
- ✅ **DRC-01**: Show Cause Notices
  - Formal assessment notices
  - Detailed grounds of demand
  - Personal hearing procedures
- ✅ **GST Appeals**
  - Appeal to Appellate Authority
  - GSTAT (GST Appellate Tribunal) Appeals
  - Comprehensive legal submissions
- ✅ **Audit Notices (ADT-01)**
  - Audit scheduling
  - Document requirements
  - Compliance checklist

---

## 🔧 Installation & Usage

### Prerequisites
```bash
Python 3.8+
pip install python-dateutil
```

### Installation
```bash
git clone https://github.com/fcsnaveenjaiswal/Finance.git
cd Finance
```

### Quick Start Examples

#### 1. Create Financial Statements (ICAI Compliant)
```python
from accounts_hack import FinancialStatementGenerator, Account, FixedAsset, AccountType, DepreciationMethod
from datetime import datetime, timedelta

# Create generator
fs_gen = FinancialStatementGenerator()

# Add accounts
fs_gen.add_account(Account("CASH001", "Cash", AccountType.ASSET, 100000))
fs_gen.add_account(Account("BANK001", "Bank", AccountType.ASSET, 500000))
fs_gen.add_account(Account("CAPITAL001", "Capital", AccountType.EQUITY, 400000))

# Add fixed asset with depreciation
equipment = FixedAsset(
    "ASSET001",
    "Machinery",
    datetime.now() - timedelta(days=365),
    1000000,
    depreciation_method=DepreciationMethod.STRAIGHT_LINE,
    useful_life_years=5
)
fs_gen.add_fixed_asset(equipment)

# Generate statements
statements = fs_gen.generate_financial_statements_package()
print("Balance Sheet:", statements["balance_sheet"])
print("Income Statement:", statements["income_statement"])
```

#### 2. Calculate Income Tax (FY 2025-26)
```python
from income_tax_hack import TaxPayer, Income, Deduction, IncomeSource, DeductionSection

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
    Deduction(DeductionSection.STANDARD_DEDUCTION, 50000, True)
)

# Generate ITR
itr = taxpayer.generate_itr_summary()
print(f"Total Tax Liability: ₹{itr['tax_calculation']['total_tax_liability']}")
```

#### 3. Generate GST Invoice
```python
from gst_hack import Invoice, LineItem, GSTType

# Create invoice
invoice = Invoice(
    invoice_number="INV-2025-001",
    supplier_name="ABC Enterprises",
    customer_name="XYZ Retail",
    gst_type=GSTType.INTRA_STATE
)

# Add line items
invoice.add_item(LineItem("Furniture", "9402", 5, 1000, 0.18))

# Generate
inv_data = invoice.generate_invoice()
print(f"Invoice Total: ₹{inv_data['invoice_total']}")
```

#### 4. Handle GST ASMT-10 Notice
```python
from gst_case_drafting import GSTNoticeASMT10, TaxpayerProfile
from datetime import datetime, timedelta

# Create taxpayer profile
taxpayer = TaxpayerProfile(
    gstin="27AAFCD1234A1Z5",
    taxpayer_name="ABC Enterprises",
    legal_status="Company",
    pan="AAFCD1234A",
    registered_address="Pune",
    contact_number="+91-20-12345678",
    email="gst@abc.com"
)

# Create notice
notice = GSTNoticeASMT10(
    notice_number="ASMT-10/2024/05",
    notice_date=datetime.now(),
    taxpayer=taxpayer,
    tax_period="07/2024",
    discrepancies=[{"description": "CGST shortfall", "amount": 50000, "gst_impact": 50000, "reason": "Error"}],
    reply_due_date=datetime.now() + timedelta(days=15)
)

# Generate notice and reply
print(notice.generate_notice_draft())
print(notice.generate_expert_reply_template())
```

#### 5. Monthly GSTR-2B vs Books ITC workpaper (Excel, offline)

`gstr2b_workpaper_hack.py` runs the month the way a GST practice does, and
writes an Excel workpaper. It is a command-line tool, and nothing is uploaded
anywhere: the files are read from disk, matched on this machine, and the
workpaper is written back to disk. The same inputs always give the same file.

```bash
# See it work on built-in demo data
python3 gstr2b_workpaper_hack.py

# First month: the portal's GSTR-2B download and the purchase register
python3 gstr2b_workpaper_hack.py \
    --gstr2b GSTR2B_19AAAAA0000A1Z5_092026.xlsx \
    --books purchase_register.xlsx \
    --client "Alpha Traders" --outdir sep26

# Next month: carry the open items forward
python3 gstr2b_workpaper_hack.py \
    --gstr2b GSTR2B_19AAAAA0000A1Z5_102026.xlsx \
    --books purchase_register_oct.xlsx \
    --opening-dir sep26 --outdir oct26
```

The month and GSTIN are read from the portal's file name
(`GSTR2B_<GSTIN>_<MMYYYY>.xlsx` or `<MMYYYY>_<GSTIN>_GSTR2B_<DDMMYYYY>.xlsx`)
or, failing that, from the `Read me` sheet inside the download (it also supplies
the client's legal name); otherwise pass `--period 2026-09`. If the file's own
header names a different month or GSTIN than the one being run, the Notes sheet
says so.

**The process it follows.** GSTR-3B Table 4(A)(5) is kept tied to GSTR-2B.
A GSTR-2B line that is not yet in the books is *reversed* under 4(B)(2),
tracked bill by bill as "2B pending", and *reclaimed* under 4(D)(1) in the
month it is booked. A books entry that is not yet in GSTR-2B is *deferred*:
not claimed, tracked as "books deferred", and claimed when the supplier's
return puts it into GSTR-2B. Both registers roll forward every month. The
credit-reversal statement (opening + 4(B)(2) − 4(D)(1) = closing) must agree
with the pending register, and the ITC in the books must bridge to the ITC
claimed with a difference of nil.

**What you get in `--outdir`:**

| File | What it is |
|---|---|
| `GSTR2B_vs_Books_<Mon-YY>.xlsx` | the workpaper |
| `carry_forward_books_deferred.csv` | next month's opening: booked, not in 2B |
| `carry_forward_2b_pending.csv` | next month's opening: in 2B, not booked |
| `summary.txt` | the headline figures as text |

**The workbook.** `Summary` opens with a checks block (bridge closes,
credit-reversal statement agrees, every line tagged, tie-outs to the portal and
Tally), then GSTR-3B Table 4, the books-to-return bridge, the credit-reversal
statement and the action items. `2B Detail` lists every GSTR-2B document with
its books match and a **TREATMENT** drop-down (`CLAIM`, `CLAIM_PM_BOOKED`,
`REVERSE_4B2`, `REVERSE_4B1`, `INELIGIBLE`, `CN_REVERSE`, …): change a tag and
the Summary, the checks and the supplier-wise sheet recalculate. `Books Only`,
`Differences`, `Reclaimed 4(D)(1)`, `Carry-forward` (with ageing and the
section 16(4) time limit), `Party-wise` and `Notes` complete it. Blue on pale
blue is an input or tag you may change, black is a formula, yellow needs a
look.

**Starting from your existing trackers.** The opening registers can be your
own tracker sheets, read in place with `FILE#SHEET`. A tracker that keeps the
whole year (every row, with the closed ones marked by a reclaim month or a
`<serial> <Mon-yy>` GSTR-2B number) brings in only what is still open:

```bash
python3 gstr2b_workpaper_hack.py --gstr2b ... --books ... --period 2026-09 \
    --opening-books-deferred "workings.xlsx#03_Action ITC Book Def Billwise" \
    --opening-2b-pending "workings.xlsx#04_Action R2B pending Billwise"
```

| Option | Default | What it does |
|---|---|---|
| `--opening-dir DIR` | — | a previous run's folder (its two carry-forward CSVs) |
| `--cn-policy books\|2b` | `books` | reverse ITC on a purchase return when booked, or only once the supplier's credit note is in GSTR-2B |
| `--date-tolerance DAYS` | `3` | invoice-date gap treated as a match |
| `--value-tolerance RUPEES` | `100` | taxable-value gap treated as a match |
| `--tax-tolerance RUPEES` | `10` | tax-amount gap treated as a match |
| `--no-timestamp` | off | leave the run time out so identical inputs give identical bytes |

It copes with what actually breaks a monthly reconciliation: the portal's
banner rows, two- and three-row stacked headings, `Read me` sheet and
B2B/B2BA/credit-note sheets; Tally's `Doc No.` (supplier number) vs `Vch No.`
(internal number) and the supplier's invoice date printed under a bare `Invoice`;
purchase returns (debit notes) matched on supplier and value, since they cite
the original invoice rather than the supplier's credit-note number; invoice
numbers written differently on each side (`INV/001` vs `INV-1`, leading zeros,
`ABC/25-26/54` vs `0054/25-26`); an invoice split rate-wise or across expense
ledgers; `Total` rows; Excel date serials and `_x000D_` escapes; reverse-charge
rows (listed, not matched); double entries; and a register kept for the whole
year (earlier-dated entries are taken as dealt with in their own month's
return unless the opening register says they are still open).

Run against a firm's real files - a genuine GSTR-2B download and a seven-month
Tally GSTR-3B voucher register carrying the firm's own month-by-month tagging -
the tool, re-performing each month from scratch with opening registers taken from
those tags (and, for April, the purchase returns still open from March), agreed
with the firm's treatment of 98.6% of 2,450 GSTR-2B lines, and its net ITC for
the month was within 0.9% of what the firm's tags add up to. What is left is
judgement (a bill marked ineligible, a credit note rejected as not ours) and
books errors (a supplier booked under another party's GSTIN, an invoice date
typed a week off), which the tool leaves unpaired for review rather than
guesses at.

*Not covered:* ISD and import credit, the outward-supply reconciliation, the
reverse-charge workings, Rule 42/43. Treatments are defaults to review, and the
statutory points (sections 16(2)(aa), 16(4), Rule 37A) are summarised, not
advised on.

##### Quick one-off match (text and CSV)

For a single month with no carry-forward, `gstr2b_reconciliation_hack.py`
matches the two files and prints a text report:

```bash
python3 gstr2b_reconciliation_hack.py --gstr2b GSTR2B.xlsx --books register.xlsx \
    --outdir reco_june --report reco_june/report.txt
```

Every document lands in `MATCHED`, `MATCHED_WITH_DIFFERENCE`, `ONLY_IN_GSTR2B`
or `ONLY_IN_BOOKS`; `gstr2b_vs_books_register.csv` carries one row per document
and filtering its `status` column reproduces any table in the report.

---

## 📚 Module Documentation

### **Module 1: accounts_hack.py**

**Classes:**
- `FixedAsset`: Fixed asset with depreciation
- `Investment`: Investment accounting per AS 13
- `Inventory`: Inventory valuation per AS 2
- `Provision`: Provisions per AS 29
- `Account`: General accounting structure
- `FinancialStatementGenerator`: Complete FS generation

**Key Methods:**
- `calculate_depreciation_charge()`: Depreciation calculation
- `generate_balance_sheet()`: BS generation
- `generate_income_statement()`: P&L generation
- `generate_accounting_policies_disclosure()`: AS 1 compliance

---

### **Module 2: income_tax_hack.py**

**Classes:**
- `TaxPayer`: Taxpayer information
- `Income`: Income source details
- `Deduction`: Tax deductions
- `IncomeTaxRates`: Tax slab definitions

**Key Methods:**
- `calculate_total_income()`: Total income
- `calculate_taxable_income()`: Taxable income
- `calculate_income_tax()`: Tax per slabs
- `calculate_surcharge()`: Surcharge
- `calculate_cess()`: Health & Education Cess
- `generate_itr_summary()`: Complete ITR

---

### **Module 3: gst_hack.py**

**Classes:**
- `Invoice`: GST Invoice
- `LineItem`: Invoice line items
- `GSTReturn`: GST return filing
- `HSNCode`: HSN codes & rates

**Key Methods:**
- `generate_invoice()`: Invoice generation
- `calculate_gst_liability()`: GST liability
- `generate_return_summary()`: GST return summary

---

### **Module 4: gst_case_drafting.py**

**Classes:**
- `TaxpayerProfile`: Taxpayer details
- `GSTNoticeASMT10`: Discrepancy notices
- `GSTNoticeDRC01A`: Pre-demand intimations
- `GSTNoticeDRC01`: Show Cause Notices
- `GSTAppealDrafter`: Appeal generation
- `GSTCaseDraftingModule`: Case management

**Key Methods:**
- `generate_notice_draft()`: Notice generation
- `generate_expert_reply_template()`: Professional replies
- `generate_appellate_appeal_template()`: Appeal drafting
- `generate_compliance_checklist()`: Compliance tracking

---

### **Module 5: icai_guidance_notes.py**

**Classes:**
- `Lease`: Lease accounting (GN1)
- `LeaveAccrual`: Leave accrual (GN5)
- `EmployeeShareOption`: Share-based payments (GN4)
- `RealEstateTransaction`: Real estate (GN2)
- `DerivativeContract`: Derivatives (GN3)
- `IncomeUncertainty`: Tax uncertainty (GN7)
- `GuidanceNotesComplianceChecker`: Compliance validation

---

## 📜 Compliance Standards

### **1. ICAI Accounting Standards (AS 1-29)**
- ✅ AS 1: Disclosure of Accounting Policies
- ✅ AS 2: Valuation of Inventories
- ✅ AS 3: Cash Flow Statements
- ✅ AS 5: Prior Period Items
- ✅ AS 10: Property, Plant & Equipment
- ✅ AS 13: Investments
- ✅ AS 18: Related Party Disclosures
- ✅ AS 29: Provisions & Contingent Liabilities

### **2. Income Tax Act, 1961**
- ✅ Sections 80C, 80D, 80E, 80G, 80TTA, 80U
- ✅ Surcharge provisions
- ✅ Cess calculations
- ✅ Tax slabs (FY 2025-26)

### **3. GST Law (2017)**
- ✅ Invoice generation
- ✅ Tax calculation (SGST, CGST, IGST)
- ✅ Return filing
- ✅ ITC management
- ✅ Notice responses

### **4. ICAI Guidance Notes (GN1-GN10)**
- ✅ Leases, Real Estate, Derivatives
- ✅ Employee Benefits, Share-based Payments
- ✅ Tax Uncertainty, Amalgamations

---

## 💡 Examples & Use Cases

### **Use Case 1: Complete Financial Audit**
```python
# Generate comprehensive financial statements for annual audit
# Includes Balance Sheet, P&L, Notes to Accounts, Accounting Policies
# As per ICAI Accounting Standards (AS 1-29)
```

### **Use Case 2: Tax Planning**
```python
# Calculate optimal tax strategy
# Compare tax liability across different income combinations
# Identify maximum benefit from deductions
```

### **Use Case 3: GST Compliance**
```python
# Generate monthly GST returns
# Track ITC and GST liability
# Maintain compliance calendar
```

### **Use Case 4: Dispute Resolution**
```python
# Draft professional replies to GST notices
# Generate appeals with legal grounds
# Prepare for personal hearings
# Maintain case timeline and status
```

---

## 🎓 Best Practices

1. **Maintain detailed records** - Essential for all financial computations
2. **Regular reconciliation** - Verify accounts monthly
3. **Professional review** - Get CA/Lawyer review for notices
4. **Timely compliance** - File returns and respond to notices on time
5. **Documentation** - Keep all supporting documents organized

---

## 🤝 Contributing

Contributions are welcome! Please ensure:
- Code follows Python best practices
- Compliance with applicable laws
- Comprehensive documentation
- Test coverage

---

## 📞 Support & Contact

For queries or support:
- **Email:** support@fcsnaveenjaiswal.com
- **GitHub Issues:** [File an issue]
- **Professional Consultation:** [Contact CA/Lawyer]

---

## 📄 License

This repository is provided for educational and professional use. Please consult with qualified professionals before implementation.

---

## ⚠️ Disclaimer

This repository provides templates and frameworks for financial and legal matters. Users must:
1. Consult qualified professionals (CA, Lawyers) for specific cases
2. Verify all calculations with official guidelines
3. Ensure compliance with current laws and regulations
4. Adapt templates to specific jurisdictions and circumstances

**The developer assumes no liability for any financial or legal consequences arising from use of this repository.**

---

**Last Updated:** May 23, 2026  
**Version:** 1.0  
**Repository:** https://github.com/fcsnaveenjaiswal/Finance

---

## Quick Links

- [Accounts Module](accounts_hack.py)
- [Income Tax Module](income_tax_hack.py)
- [GST Module](gst_hack.py)
- [GST Case Drafting](gst_case_drafting.py)
- [ICAI Guidance Notes](icai_guidance_notes.py)

---

**🎉 Happy Finance Management!**
