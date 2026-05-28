#!/usr/bin/env python3
"""
GST Case Drafting Module - Professional Legal Templates
Comprehensive GST case management for:
- GST ASMT-10 (Discrepancy Notices)
- GST DRC-01A (Intimation of Liability)
- GST DRC-01 (Show Cause Notices)
- GST Appeals (Appellate Authority & GSTAT)
- GST Audit Notices (ADT-01)
- GST Assessment Cases
- Related Party Audits & Compliance

Expert drafting as per GST Rules, 2017 and Procedural Guidelines
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional


class NoticeType(Enum):
    """GST Notice Types"""
    ASMT_10 = "ASMT-10: Discrepancy Notice"
    DRC_01A = "DRC-01A: Intimation of Liability"
    DRC_01 = "DRC-01: Show Cause Notice"
    ADT_01 = "ADT-01: Audit Notice"
    GST_APPEAL = "GST Appeal: Appellate Authority"
    GSTAT_APPEAL = "GSTAT: GST Appellate Tribunal Appeal"


class CaseStatus(Enum):
    """Case Status Tracking"""
    NOTICE_RECEIVED = "Notice Received"
    REPLY_FILED = "Reply Filed"
    PERSONAL_HEARING = "Personal Hearing Scheduled"
    ORDER_ISSUED = "Order Issued"
    APPEAL_FILED = "Appeal Filed"
    APPEAL_PENDING = "Appeal Pending"
    APPEAL_DISPOSED = "Appeal Disposed"
    COMPLIANCE_ONGOING = "Compliance Ongoing"


@dataclass
class TaxpayerProfile:
    """Taxpayer Information"""
    gstin: str
    taxpayer_name: str
    legal_status: str  # Individual, Partnership, Company, LLP, Proprietorship
    pan: str
    registered_address: str
    contact_number: str
    email: str
    authorized_representative: Optional[str] = None
    legal_counsel: Optional[str] = None
    ca_details: Optional[str] = None


@dataclass
class GSTNoticeASMT10:
    """GST ASMT-10: Notice for Discrepancies in Return"""
    notice_number: str
    notice_date: datetime
    taxpayer: TaxpayerProfile
    tax_period: str  # Format: MM/YYYY
    discrepancies: List[Dict]  # List of identified discrepancies
    reply_due_date: datetime  # Typically 15 days

    def generate_notice_draft(self) -> str:
        """Generate professional ASMT-10 notice draft"""

        discrepancy_details = "\n".join([
            f"  {i+1}. {disc['description']}\n"
            f"     Amount: ₹{disc['amount']}\n"
            f"     GST Liability Impact: ₹{disc['gst_impact']}\n"
            f"     Reason: {disc['reason']}"
            for i, disc in enumerate(self.discrepancies)
        ])

        draft = f"""
═══════════════════════════════════════════════════════════════════════════════
                           GOVERNMENT OF INDIA
                    GOODS & SERVICES TAX DEPARTMENT
                    
                    NOTICE UNDER SECTION 61, GST ACT, 2017
                    (FORM ASMT-10: DISCREPANCY NOTICE)
═══════════════════════════════════════════════════════════════════════════════

TO:
{self.taxpayer.taxpayer_name}
GSTIN: {self.taxpayer.gstin}
PAN: {self.taxpayer.pan}
Legal Status: {self.taxpayer.legal_status}
Address: {self.taxpayer.registered_address}
Contact: {self.taxpayer.contact_number}
Email: {self.taxpayer.email}

═══════════════════════════════════════════════════════════════════════════════

NOTICE NUMBER: {self.notice_number}
ISSUED DATE: {self.notice_date.strftime('%d-%m-%Y')}
TAX PERIOD: {self.tax_period}
REPLY DUE DATE: {self.reply_due_date.strftime('%d-%m-%Y')} (Within 15 days)

═══════════════════════════════════════════════════════════════════════════════

SUBJECT: NOTICE FOR DISCREPANCIES IDENTIFIED IN GST RETURNS - TAX PERIOD {self.tax_period}

Dear {self.taxpayer.taxpayer_name.split()[0]},

NOTICE IS HEREBY GIVEN that upon scrutiny and examination of the GST returns 
filed by you for the tax period {self.tax_period}, certain discrepancies have 
been identified as detailed hereinunder:

IDENTIFIED DISCREPANCIES:
─────────────────────────────────────────────────────────────────────────────

{discrepancy_details}

─────────────────────────────────────────────────────────────────────────────

LEGAL BASIS:
These discrepancies have been identified in accordance with Section 61 of the 
Goods and Services Tax Act, 2017 read with the GST Rules, 2017.

YOUR RIGHTS AND OPTIONS:
─────────────────────────────────────────────────────────────────────────────

You are hereby called upon to take either of the following actions within 
{self.reply_due_date.strftime('%d-%m-%Y')} (15 days from receipt of this notice):

OPTION 1: ACCEPT AND PAY
If you accept the discrepancy, you may:
  a) Pay the demanded tax amount
  b) Pay interest @ 18% per annum from the due date of payment
  c) Accept such penalty as may be applicable

OPTION 2: FURNISH EXPLANATION
You may furnish a detailed written explanation addressing each discrepancy with 
supporting documentary evidence, including:
  - Copies of invoices (purchase/sales as applicable)
  - Bank statements and payment receipts
  - Delivery challans and goods receipt notes
  - Any other relevant supporting documents
  - Legal/technical clarifications on the disputed points

CONSEQUENCES OF NON-COMPLIANCE:
─────────────────────────────────────────────────────────────────────────────

Failure to respond or appear within the prescribed time period may result in:
  1. Issuance of a formal Show Cause Notice (DRC-01)
  2. Imposition of penalties under Section 63 of GST Act
  3. Recovery of disputed tax with interest
  4. Suspension of GST registration (in severe cases)

SUBMISSION PROCEDURE:
─────────────────────────────────────────────────────────────────────────────

Your reply may be submitted through:
  • GST Portal (https://www.gst.gov.in) - Upload under Respondent's Reply
  • Email to: [Officer Email]
  • Physical submission at: [GST Office Address]

Please provide your response in the prescribed format with clear references to 
the issues raised and supporting documentation.

═══════════════════════════════════════════════════════════════════════════════

                              Issued by Authority of
                         Superintendent/Assistant Commissioner
                            Goods & Services Tax Department

                         [Department Seal/Stamp]
                              [Officer Signature]
                    [Date]: {self.notice_date.strftime('%d-%m-%Y')}

═══════════════════════════════════════════════════════════════════════════════
"""
        return draft

    def generate_expert_reply_template(self) -> str:
        """Generate expert-drafted reply template to ASMT-10"""

        reply_template = f"""
═══════════════════════════════════════════════════════════════════════════════
                    EXPERT REPLY TO ASMT-10 NOTICE
                         (Professional Legal Draft)
═══════════════════════════��═══════════════════════════════════════════════════

FROM:
{self.taxpayer.taxpayer_name}
GSTIN: {self.taxpayer.gstin}
PAN: {self.taxpayer.pan}

TO:
The Superintendent/Assistant Commissioner
Goods & Services Tax Department
[GST Office]

RE: REPLY TO ASMT-10 NOTICE DATED {self.notice_date.strftime('%d-%m-%Y')}
    NOTICE NO: {self.notice_number}
    TAX PERIOD: {self.tax_period}

═══════════════════════════════════════════════════════════════════════════════

RESPECTFUL SUBMISSION:

1. BACKGROUND & CONTEXT
───────────────────────────────────────────────────────────────────────────────
The undersigned acknowledges receipt of the above notice and wishes to present 
a detailed and substantiated reply to the discrepancies identified therein.

The assessee has been diligently complying with GST obligations and has 
maintained proper books of accounts in accordance with GST Rules, 2017.

2. DETAILED RESPONSE TO IDENTIFIED DISCREPANCIES
───────────────────────────────────────────────────────────────────────────────

"""

        for i, disc in enumerate(self.discrepancies, 1):
            reply_template += f"""
DISCREPANCY {i}: {disc['description']}
─────────────────────────────────────────────────────────────────────────────
Amount Involved: ₹{disc['amount']}
GST Impact: ₹{disc['gst_impact']}

ASSESSEE'S SUBMISSION:

[Provide detailed explanation with reference to:]
  • Relevant GST Rules/Circulars
  • Supporting documents (Invoice no., Date, Amount)
  • Legal position citing judicial precedents if applicable
  • Technical clarification addressing the officer's query

DOCUMENTARY EVIDENCE ENCLOSED:
  ☐ GST Returns (GSTR-1/GSTR-3B)
  ☐ Purchase/Sales Invoices
  ☐ Bank Statements
  ☐ Delivery Challans
  ☐ [Other Relevant Documents]

"""

        reply_template += f"""

3. LEGAL POSITION & APPLICABLE LAW
───────────────────────────────────────────────────────────────────────────────

The assessee's position is fully supported by:
  • Sections [_____] of the Goods & Services Tax Act, 2017
  • Rule [___] of GST Rules, 2017
  • CBIC Circular No. [___]
  • Judicial Precedents: [Case Citations]

4. CONCLUSION
───────────────────────────────────────────────────────────────────────────────

The assessee respectfully submits that the identified discrepancies are either:
  (a) Properly explained with supporting documentation, OR
  (b) Based on a misinterpretation of the GST Rules, OR
  (c) Not liable to GST as per applicable provisions

Therefore, the discrepancies do not warrant any demand or penalty against 
the assessee.

5. PRAYER
───────────────────────────────────────────────────────────────────────────────

It is hereby prayed that:
  1. The ASMT-10 notice be treated as explained;
  2. No further action/demand be issued against the assessee;
  3. The matter be dropped/closed; OR
  4. Alternatively, grant personal hearing to the assessee before final decision.

═══════════════════════════════════════════════════════════════════════════════

VERIFICATION:

I, {self.taxpayer.taxpayer_name}, hereby declare that the contents of this 
reply are true and correct to the best of my knowledge and belief.

Place: [City]
Date: {datetime.now().strftime('%d-%m-%Y')}

                                        {self.taxpayer.taxpayer_name}
                                        GSTIN: {self.taxpayer.gstin}
                                        PAN: {self.taxpayer.pan}

Signature: ___________________________

═══════════════════════════════════════════════════════════════════════════════
ANNEXURES ATTACHED:
1. ASMT-10 Notice (Original)
2. GST Returns & relevant GST Forms
3. Invoices (copies)
4. Bank Statements
5. Other supporting documents
═════════════════════════════════════════════════════════════════════════════════
"""
        return reply_template


@dataclass
class GSTNoticeDRC01A:
    """GST DRC-01A: Intimation of Liability (Pre-Show Cause Notice)"""
    notice_number: str
    notice_date: datetime
    taxpayer: TaxpayerProfile
    tax_period: str
    tax_amount: float
    interest_amount: float
    penalty_amount: float
    total_demand: float
    reply_due_date: datetime  # Typically 7 days
    grounds_of_demand: str

    def generate_notice_draft(self) -> str:
        """Generate professional DRC-01A notice"""

        draft = f"""
═══════════════════════════════════════════════════════════════════════════════
                           GOVERNMENT OF INDIA
                    GOODS & SERVICES TAX DEPARTMENT
                    
            INTIMATION OF LIABILITY BEFORE SHOW CAUSE NOTICE
                    (FORM DRC-01A: PRE-DEMAND NOTICE)
═══════════════════════════════════════════════════════════════════════════════

TO:
{self.taxpayer.taxpayer_name}
GSTIN: {self.taxpayer.gstin}
PAN: {self.taxpayer.pan}
Legal Status: {self.taxpayer.legal_status}
Address: {self.taxpayer.registered_address}
Contact: {self.taxpayer.contact_number}
Email: {self.taxpayer.email}

═══════════════════════════════════════════════════════════════════════════════

NOTICE NUMBER: {self.notice_number}
ISSUED DATE: {self.notice_date.strftime('%d-%m-%Y')}
TAX PERIOD: {self.tax_period}
REPLY DUE DATE: {self.reply_due_date.strftime('%d-%m-%Y')} (Within 7 days)

═══════════════════════════════════════════════════════════════════════════════

SUBJECT: INTIMATION OF LIABILITY u/s 73(5)/74(5) - GST ACT, 2017
         TAX PERIOD: {self.tax_period}

Dear {self.taxpayer.taxpayer_name.split()[0]},

NOTICE IS HEREBY GIVEN that upon examination of your GST returns and records 
for the tax period {self.tax_period}, a liability has been determined as per 
the provisions of Sections 73(5) and/or 74(5) of the Goods and Services Tax 
Act, 2017.

GROUNDS OF DEMAND:
─────────────────────────────────────────────────────────────────────────────

{self.grounds_of_demand}

CALCULATED LIABILITY:
─────────────────────────────────────────────────────────────────────────────

                                                          ₹
1. Tax Shortfall                                  {self.tax_amount:>15,.2f}
2. Interest @ 18% per annum from due date         {self.interest_amount:>15,.2f}
3. Penalty @ 10%-20% of tax shortfall             {self.penalty_amount:>15,.2f}
                                                  ─────────────────────
   TOTAL DEMAND                                   {self.total_demand:>15,.2f}
                                                  ═════════════════════

OPPORTUNITY TO PAY OR RESPOND:
─────────────────────────────────────────────────────────────────────────────

You are hereby given an opportunity to:

OPTION 1: VOLUNTARY PAYMENT (WITHIN 7 DAYS)
If you accept the demand, you may pay the total amount of ₹{self.total_demand:,.2f} 
by {self.reply_due_date.strftime('%d-%m-%Y')}.

Benefits of Voluntary Payment:
  ✓ No additional penalties
  ✓ Demonstrates good faith compliance
  ✓ May avoid formal Show Cause Notice proceedings
  ✓ Builds favorable compliance record

Payment Mode: [NEFT/RTGS/Cheque/GST Portal]

OPTION 2: SUBMIT EXPLANATION (WITHIN 7 DAYS)
If you dispute the demand, you must submit:
  a) Detailed written explanation addressing each point of demand
  b) Supporting documentary evidence
  c) Relevant provisions of law and judicial precedents
  d) Specific factual clarifications

CONSEQUENCES OF INACTION:
─────────────────────────────────────────────────────────────────────────────

Failure to pay or respond within 7 days will result in:
  1. Issuance of formal Show Cause Notice (DRC-01)
  2. Escalation to formal adjudication proceedings
  3. Imposition of additional penalties
  4. Recovery proceedings under Section 79 of GST Act
  5. Potential suspension of GST registration

═══════════════════════════════════════════════════════════════════════════════

                              Issued by Authority of
                         Superintendent/Assistant Commissioner
                            Goods & Services Tax Department

                         [Department Seal/Stamp]
                              [Officer Signature]
                    [Date]: {self.notice_date.strftime('%d-%m-%Y')}

═══════════════════════════════════════════════════════════════════════════════
"""
        return draft


@dataclass
class GSTNoticeDRC01:
    """GST DRC-01: Show Cause Notice (Formal Assessment)"""
    notice_number: str
    notice_date: datetime
    taxpayer: TaxpayerProfile
    tax_period: str
    proposed_demand: float
    proposed_penalty: float
    legal_grounds: List[str]
    reply_due_date: datetime  # Typically 30 days
    personal_hearing_date: Optional[datetime] = None

    def generate_notice_draft(self) -> str:
        """Generate professional DRC-01 Show Cause Notice"""

        grounds_text = "\n".join([
            f"  {i+1}. {ground}"
            for i, ground in enumerate(self.legal_grounds)
        ])

        draft = f"""
═══════════════════════════════════════════════════════════════════════════════
                           GOVERNMENT OF INDIA
                    GOODS & SERVICES TAX DEPARTMENT
                    
                    SHOW CAUSE NOTICE
                    (FORM DRC-01: FORMAL ASSESSMENT NOTICE)
                    u/s 73/74 OF THE GOODS & SERVICES TAX ACT, 2017
═══════════════════════════════════════════════════════════════════════════════

TO:
{self.taxpayer.taxpayer_name}
GSTIN: {self.taxpayer.gstin}
PAN: {self.taxpayer.pan}
Legal Status: {self.taxpayer.legal_status}
Address: {self.taxpayer.registered_address}
Contact: {self.taxpayer.contact_number}
Email: {self.taxpayer.email}

═══════════════════════════════════════════════════════════════════════════════

NOTICE NUMBER: {self.notice_number}
ISSUED DATE: {self.notice_date.strftime('%d-%m-%Y')}
TAX PERIOD: {self.tax_period}
REPLY DUE DATE: {self.reply_due_date.strftime('%d-%m-%Y')} (Within 30 days)
PERSONAL HEARING: {self.personal_hearing_date.strftime('%d-%m-%Y at %H:%M') if self.personal_hearing_date else 'To be scheduled'}

═══════════════════════════════════════════════════════════════════════════════

SUBJECT: SHOW CAUSE NOTICE FOR TAX DEMAND - TAX PERIOD {self.tax_period}
         u/s 73(1)/74(1) of Goods & Services Tax Act, 2017

Dear {self.taxpayer.taxpayer_name.split()[0]},

NOTICE IS HEREBY GIVEN that a Show Cause Notice is issued requiring you to 
show cause as to why the demand specified hereinunder should not be recovered 
from you and why penalties should not be imposed.

LEGAL BASIS FOR DEMAND:
─────────────────────────────────────────────────────────────────────────────

Section 73 of GST Act, 2017:
"Where any tax has not been paid or paid wrongly by any registered person, the 
proper officer may, within a period of three years from the date when payment 
of such tax was due or within three years from the date of filing of return, 
whichever is later, ask the registered person to show cause..."

Section 74 of GST Act, 2017:
"Penalty for non-compliance with the requirement of furnishing documents, 
information or evidence. Any person who contravenes... may be liable to penalty."

GROUNDS OF DEMAND:
─────────────────────────────────────────────────────────────────────────────

{grounds_text}

PROPOSED DEMAND COMPUTATION:
─────────────────────────────────────────────────────────────────────────────

                                                          ₹
1. Short-paid/Wrongly claimed GST                {self.proposed_demand:>15,.2f}
2. Interest @ 18% per annum                      [To be calculated]
3. Proposed Penalty                              {self.proposed_penalty:>15,.2f}
                                                  ─────────────────────
   TOTAL PROPOSED DEMAND                         [Will be specified]
                                                  ═════════════════════

YOUR RIGHTS & OBLIGATIONS:
─────────────────────────────────────────────────────────────────────────────

WITHIN 30 DAYS of receipt of this notice, you MUST:

1. FILE WRITTEN REPLY:
   ✓ Detailed point-by-point reply to each ground
   ✓ Supporting documentary evidence
   ✓ Relevant GST law/rules and judicial precedents
   ✓ Factual and legal rebuttals

2. APPEAR FOR PERSONAL HEARING:
   ✓ A personal hearing shall be granted
   ✓ You may represent through authorized person/CA/Lawyer
   ✓ Date to be intimated separately
   ✓ Mandatory appearance for clarifications

DETAILED SUBMISSION REQUIREMENTS:
─────────────────────────────────────────────────────────────────────────────

Your reply MUST include:

a) FACTUAL CLARIFICATIONS:
   • Explanation of business transactions
   • Circumstances and commercial rationale
   • Changes in business practices, if any
   • Force majeure or exceptional circumstances

b) DOCUMENTARY EVIDENCE:
   ☐ GST Returns (GSTR-1, GSTR-2, GSTR-3B)
   ☐ Purchase & Sales Invoices
   ☐ Delivery Challans
   ☐ Bank Statements & Payment Receipts
   ☐ Bills of Lading/Transports
   ☐ Warehouse Records
   ☐ Any other relevant document

c) LEGAL SUBMISSIONS:
   • Relevant provisions of GST Act/Rules
   • CBIC Circulars and Notifications
   • Judicial Precedents (Supreme Court/High Court/Tribunal)
   • Case law supporting your position
   • Legal interpretation of disputed provisions

CONSEQUENCES OF NON-COMPLIANCE:
─────────────────────────────────────────────────────────────────────────────

Failure to comply with this notice may result in:

1. COERCIVE MEASURES:
   ✗ Demand order passed ex-parte without hearing
   ✗ Recovery proceedings under Section 79
   ✗ Attachment of bank accounts
   ✗ Seizure of goods and assets

2. PENAL CONSEQUENCES:
   ✗ Penalty u/s 63(1): 10% to 20% of tax
   ✗ Additional penalty for delayed payment
   ✗ Penalty u/s 122 for obstruction

3. COMPLIANCE CONSEQUENCES:
   ✗ Suspension of GST registration
   ✗ Blacklisting for future contracts
   ✗ Adverse compliance record
   ✗ Criminal prosecution for serious violations

═══════════════════════════════════════════════════════════════════════════════

SUBMISSION PROCEDURE:
─────────────────────────────────────────────────────────────────────────────

Your reply may be submitted through:

1. GST Portal: https://www.gst.gov.in
   • Upload in "Respondent's Reply" section
   • Attach supporting documents as PDF

2. Email: [Officer's Email]
   • Subject: "Reply to DRC-01 Notice No. [_____]"
   • Attach all supporting documents

3. Physical Submission:
   • [GST Office Address]
   • In person or through authorized representative
   • Get acknowledgment receipt

═══════════════════════════════════════════════════════════════════════════════

                              Issued by Authority of
                         Assistant Commissioner / Superintendent
                            Goods & Services Tax Department

                         [Department Seal/Stamp]
                              [Officer Signature]
                    [Date]: {self.notice_date.strftime('%d-%m-%Y')}

═════════════════════════════════════════════════════════════════════════════════
"""
        return draft


class GSTAppealDrafter:
    """Expert GST Appeal Drafting"""

    @staticmethod
    def generate_appellate_appeal_template(case_details: Dict) -> str:
        """Generate expert-drafted GST Appeal to Appellate Authority"""

        appeal_draft = f"""
═══════════════════════════════════════════════════════════════════════════════
                    GOODS & SERVICES TAX APPELLATE AUTHORITY
                         (FORM GST APL-01)
                     APPEAL AGAINST ASSESSMENT ORDER
═══════════════════════════════════════════════════════════════════════════════

FILED BY:
{case_details['taxpayer_name']}
GSTIN: {case_details['gstin']}
PAN: {case_details['pan']}
Address: {case_details['address']}

AGAINST:
Order No.: {case_details['order_number']}
Order Date: {case_details['order_date']}
Adjudicating Officer: {case_details['officer_name']}
Department: {case_details['department']}

═══════════════════════════════════════════════════════════════════════════════

APPEAL PREFERRED BY:
[Taxpayer/Authorized Representative/CA/Lawyer]

NATURE OF CASE: {case_details['case_type']}
TAX PERIOD: {case_details['tax_period']}
AMOUNT IN DISPUTE: ₹{case_details['disputed_amount']:,.2f}

═══════════════════════════════════════════════════════════════════════════════

I. STATEMENT OF FACTS:
───────────────────────────────────────────────────────────────────────────────

1. The appellant is a registered dealer under GST with GSTIN {case_details['gstin']}
   engaged in the business of [____________________].

2. During the relevant tax period {case_details['tax_period']}, the appellant:
   [Detailed factual narrative of business transactions, practices, and compliance]

3. On [Date], a Show Cause Notice (DRC-01) No. [_____] was issued proposing a 
   demand of ₹{case_details['proposed_demand']:,.2f}.

4. The appellant filed a detailed reply and appeared for personal hearing.

5. Subsequently, an Assessment Order was passed on {case_details['order_date']} 
   by {case_details['officer_name']} demanding ₹{case_details['demand_amount']:,.2f} 
   with penalty of ₹{case_details['penalty_amount']:,.2f}.

6. The appellant is aggrieved with the said order and hereby prefers this appeal.

═══════════════════════════════════════════════════════════════════════════════

II. GROUNDS OF APPEAL:
───────────────────────────────────────────────────────────────────────────────

GROUND 1: [Detailed ground with legal reasoning and case law]
───────────────────────────────────────────────────────────────────────────────

The Adjudicating Officer has misinterpreted the provisions of [Section _____] 
of GST Act, 2017. The impugned order is based on an incorrect application of law 
as follows:

[Detailed legal argument with references to:]
• GST Act provisions
• GST Rules
• CBIC Circulars
• Judicial precedents
• Technical clarifications

This ground is supported by:
• Case 1: [Citation] - supporting principle
• Case 2: [Citation] - supporting principle
• CBIC Circular: [Reference] - clarifying the law

GROUND 2: [Factual error or misapprehension of facts]
───────────────────────────────────────────────────────────────────────────────

The Adjudicating Officer has misapprehended the facts of the case. The findings 
are not supported by evidence as:

• The officer has disregarded documentary evidence
• The facts admitted by the officer contradict the conclusion
• The reasoning is based on incomplete or incorrect information

GROUND 3: [Procedural irregularity]
───────────────────────────────────────────────��───────────────────────────────

The assessment proceedings were vitiated by procedural irregularities:
• Denial of fair opportunity to present case
• Violation of principles of natural justice
• Non-consideration of submitted documents
• Inadequate notice period

═══════════════════════════════════════════════════════════════════════════════

III. DETAILED SUBMISSIONS:
───────────────────────────────────────────────────────────────────────────────

[Comprehensive legal and factual submissions addressing each ground with:]
• Detailed factual narrative
• Legal principles and precedents
• Point-by-point refutation of officer's findings
• Supporting documentary evidence references

═══════════════════════════════════════════════════════════════════════════════

IV. RELIEF SOUGHT:
───────────────────────────────────────────────────────────────────────────────

It is respectfully prayed that this Appellate Authority may be pleased to:

1. ALLOW this appeal and set aside the impugned order dated {case_details['order_date']};

2. DIRECT the Adjudicating Officer to pass a fresh order deleting the entire 
   demand of ₹{case_details['demand_amount']:,.2f} and penalty of 
   ₹{case_details['penalty_amount']:,.2f};

3. ALTERNATIVELY, partially allow the appeal and reduce the demand to 
   ₹[____] and penalty to ₹[____];

4. HOLD that the appellant is entitled to the relief claimed;

5. PASS such other orders as this Authority may deem fit in the facts and 
   circumstances of the case.

═══════════════════════════════════════════════════════════════════════════════

V. ANNEXURES:
───────────────────────────────────────────────────────────────────────────────

☐ Photocopy of the impugned order
☐ Copy of Show Cause Notice (DRC-01)
☐ Copy of reply to Show Cause Notice
☐ Relevant GST Returns (GSTR-1, GSTR-3B)
☐ Supporting invoices and documents
☐ Bank statements
☐ Correspondence with tax department
☐ Relevant case laws and judicial precedents
☐ Legal memorandum

═══════════════════════════════════════════════════════════════════════════════

VERIFICATION:

I, the undersigned, hereby certify that the facts and grounds stated in this 
appeal are true and correct to the best of my knowledge and belief.

Place: [City]
Date: {datetime.now().strftime('%d-%m-%Y')}

Signature: ___________________________
Name: {case_details['appellant_name']}
GSTIN: {case_details['gstin']}
PAN: {case_details['pan']}

═══════════════════════════════════════════════════════════════════════════════

CERTIFICATION BY AUTHORIZED REPRESENTATIVE:

This is to certify that I am duly authorized to represent the appellant in this 
matter. I have perused the appeal and the annexed documents. The contents are 
true and correct to the best of my knowledge.

Professional Details:
Name: [_________________]
Designation: [CA/Lawyer/Tax Consultant]
License/Registration No.: [____________]
Address: [_________________]
Contact: [_________________]

Signature: ___________________________
Date: {datetime.now().strftime('%d-%m-%Y')}

═════════════════════════════════════════════════════════════════════════════════
"""
        return appeal_draft


class GSTCaseDraftingModule:
    """Complete GST Case Management & Drafting System"""

    def __init__(self):
        self.cases: Dict[str, Dict] = {}
        self.notices_issued: List[str] = []

    def create_case_file(self, gstin: str, case_id: str, case_type: NoticeType) -> Dict:
        """Create new GST case file"""
        case_file = {
            "gstin": gstin,
            "case_id": case_id,
            "case_type": case_type,
            "created_date": datetime.now(),
            "status": CaseStatus.NOTICE_RECEIVED,
            "documents": [],
            "hearing_dates": [],
            "communications": []
        }
        self.cases[case_id] = case_file
        return case_file

    def track_case_status(self, case_id: str) -> Dict:
        """Track case status and timeline"""
        if case_id not in self.cases:
            return {"error": "Case not found"}

        case = self.cases[case_id]
        return {
            "case_id": case_id,
            "gstin": case["gstin"],
            "case_type": case["case_type"].value,
            "status": case["status"].value,
            "created_date": case["created_date"].isoformat(),
            "timeline": case["communications"]
        }

    def generate_compliance_checklist(self) -> Dict:
        """Generate compliance checklist for GST cases"""
        return {
            "pre_notice_stage": [
                "☐ Maintain complete GST records and documentation",
                "☐ File GST returns on time (GSTR-1, GSTR-3B, GSTR-9)",
                "☐ Reconcile returns with accounts",
                "☐ Implement robust invoice management system",
                "☐ Maintain bank statements and payment proofs"
            ],
            "notice_received_stage": [
                "☐ Acknowledge receipt of notice",
                "☐ Consult with GST expert/CA/Lawyer immediately",
                "☐ Compile all supporting documents",
                "☐ Identify issue and gather relevant invoices",
                "☐ Prepare detailed factual narrative",
                "☐ Research applicable law and precedents"
            ],
            "reply_preparation": [
                "☐ Draft professional and detailed reply",
                "☐ Include point-by-point response to discrepancies",
                "☐ Cite relevant provisions and case law",
                "☐ Attach all supporting documents",
                "☐ Get review from legal counsel",
                "☐ File within prescribed timeline"
            ],
            "hearing_preparation": [
                "☐ Prepare presentation materials",
                "☐ Organize document files",
                "☐ Brief authorized representative",
                "☐ Prepare for officer's likely questions",
                "☐ Have supporting documents readily accessible",
                "☐ Arrange for expert to attend hearing"
            ],
            "appeal_stage": [
                "☐ Analyze order and identify errors",
                "☐ Prepare comprehensive legal memorandum",
                "☐ File appeal within prescribed timeline",
                "☐ Include certified copy of order",
                "☐ Submit required fee with appeal",
                "☐ Track appeal status regularly"
            ]
        }


# Example usage
if __name__ == "__main__":
    # Create taxpayer profile
    taxpayer = TaxpayerProfile(
        gstin="27AAFCD1234A1Z5",
        taxpayer_name="ABC Enterprises Private Limited",
        legal_status="Company",
        pan="AAFCD1234A",
        registered_address="Plot No. 123, Industrial Area, Pune - 411001",
        contact_number="+91-20-12345678",
        email="gst@abcenterprises.com",
        legal_counsel="M/s. XYZ Legal Associates"
    )

    # Create ASMT-10 notice
    discrepancies = [
        {
            "description": "Short remittance of CGST for July 2024",
            "amount": 50000,
            "gst_impact": 50000,
            "reason": "Error in return calculation"
        }
    ]

    notice = GSTNoticeASMT10(
        notice_number="ASMT-10/2024/05",
        notice_date=datetime.now(),
        taxpayer=taxpayer,
        tax_period="07/2024",
        discrepancies=discrepancies,
        reply_due_date=datetime.now() + timedelta(days=15)
    )

    print(notice.generate_notice_draft())
    print("\n" + "="*80 + "\n")
    print(notice.generate_expert_reply_template())
