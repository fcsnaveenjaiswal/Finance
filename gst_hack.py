#!/usr/bin/env python3
"""
Finance Hack: GST (Goods and Services Tax) Management System
Handles invoice generation, tax calculation, and return filing
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List


class GSTType(Enum):
    """Types of GST"""
    INTRA_STATE = "INTRA_STATE"  # SGST + CGST
    INTER_STATE = "INTER_STATE"  # IGST


class HSNCode(Enum):
    """HSN Codes and their GST rates"""
    FOOD = ("1001-1009", 0.00)      # 0% - Food grains
    CLOTHING = ("6204-6215", 0.05)  # 5% - Apparel
    ELECTRONICS = ("8471-8517", 0.12)  # 12% - Electronics
    FURNITURE = ("9401-9406", 0.18)  # 18% - Furniture
    SERVICES = ("9990", 0.18)       # 18% - General Services


@dataclass
class LineItem:
    """Line item in an invoice"""
    description: str
    hsn_code: str
    quantity: float
    unit_price: float
    gst_rate: float = 0.18

    def calculate_amount(self) -> float:
        return self.quantity * self.unit_price

    def calculate_gst(self) -> float:
        return self.calculate_amount() * self.gst_rate

    def calculate_total(self) -> float:
        return self.calculate_amount() + self.calculate_gst()


@dataclass
class Invoice:
    """GST Invoice"""
    invoice_number: str
    date: datetime = field(default_factory=datetime.now)
    supplier_name: str = ""
    supplier_gstin: str = ""
    customer_name: str = ""
    customer_gstin: str = ""
    gst_type: GSTType = GSTType.INTRA_STATE
    items: List[LineItem] = field(default_factory=list)

    def add_item(self, item: LineItem) -> None:
        """Add line item to invoice"""
        self.items.append(item)

    def calculate_subtotal(self) -> float:
        """Calculate subtotal before tax"""
        return sum(item.calculate_amount() for item in self.items)

    def calculate_total_gst(self) -> float:
        """Calculate total GST"""
        return sum(item.calculate_gst() for item in self.items)

    def calculate_invoice_total(self) -> float:
        """Calculate total invoice amount"""
        return self.calculate_subtotal() + self.calculate_total_gst()

    def generate_invoice(self) -> Dict:
        """Generate formatted invoice"""
        subtotal = self.calculate_subtotal()
        total_gst = self.calculate_total_gst()
        invoice_total = self.calculate_invoice_total()

        # For intra-state: SGST = CGST = total_gst / 2
        if self.gst_type == GSTType.INTRA_STATE:
            sgst = total_gst / 2
            cgst = total_gst / 2
            igst = 0
        else:  # Inter-state
            sgst = 0
            cgst = 0
            igst = total_gst

        return {
            "invoice_number": self.invoice_number,
            "date": self.date.strftime("%Y-%m-%d"),
            "supplier": {
                "name": self.supplier_name,
                "gstin": self.supplier_gstin
            },
            "customer": {
                "name": self.customer_name,
                "gstin": self.customer_gstin
            },
            "items": [
                {
                    "description": item.description,
                    "hsn": item.hsn_code,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.calculate_amount(),
                    "gst_rate": item.gst_rate,
                    "gst_amount": item.calculate_gst(),
                    "total": item.calculate_total()
                }
                for item in self.items
            ],
            "subtotal": subtotal,
            "sgst": sgst,
            "cgst": cgst,
            "igst": igst,
            "total_gst": total_gst,
            "invoice_total": invoice_total
        }


@dataclass
class GSTReturn:
    """GST Return filing"""
    return_month: str  # Format: YYYY-MM
    supply_type: str   # B2B, B2C, etc.

    outward_supplies: Dict[str, float] = field(default_factory=dict)  # {gst_rate: amount}
    inward_supplies: Dict[str, float] = field(default_factory=dict)   # {gst_rate: amount}
    input_tax_credits: Dict[str, float] = field(default_factory=dict) # {gst_rate: amount}

    def add_outward_supply(self, gst_rate: float, amount: float) -> None:
        """Add outward supply"""
        if gst_rate not in self.outward_supplies:
            self.outward_supplies[gst_rate] = 0
        self.outward_supplies[gst_rate] += amount

    def add_inward_supply(self, gst_rate: float, amount: float) -> None:
        """Add inward (purchase) supply"""
        if gst_rate not in self.inward_supplies:
            self.inward_supplies[gst_rate] = 0
        self.inward_supplies[gst_rate] += amount

    def add_itc(self, gst_rate: float, amount: float) -> None:
        """Add input tax credit"""
        if gst_rate not in self.input_tax_credits:
            self.input_tax_credits[gst_rate] = 0
        self.input_tax_credits[gst_rate] += amount

    def calculate_gst_liability(self) -> Dict:
        """Calculate GST liability"""
        liability = {}
        for rate in set(list(self.outward_supplies.keys()) + list(self.input_tax_credits.keys())):
            output_tax = (self.outward_supplies.get(rate, 0) * rate)
            input_credit = self.input_tax_credits.get(rate, 0)
            net_liability = output_tax - input_credit
            liability[rate] = {
                "output_tax": output_tax,
                "input_credit": input_credit,
                "net_liability": max(0, net_liability)
            }
        return liability

    def generate_return_summary(self) -> Dict:
        """Generate GST return summary"""
        liability = self.calculate_gst_liability()
        total_gst_payable = sum(entry["net_liability"] for entry in liability.values())

        return {
            "return_month": self.return_month,
            "supply_type": self.supply_type,
            "outward_supplies": self.outward_supplies,
            "inward_supplies": self.inward_supplies,
            "gst_liability": liability,
            "total_gst_payable": total_gst_payable
        }


# Example usage
if __name__ == "__main__":
    # Create invoice
    invoice = Invoice(
        invoice_number="INV-2025-001",
        supplier_name="ABC Enterprises",
        supplier_gstin="27AAFCD1234A1Z5",
        customer_name="XYZ Retail",
        customer_gstin="09ABCDS1111A1Z5",
        gst_type=GSTType.INTRA_STATE
    )

    # Add items
    invoice.add_item(LineItem("Furniture", "9402", 5, 1000, 0.18))
    invoice.add_item(LineItem("Office Supplies", "9990", 10, 100, 0.18))

    # Generate invoice
    inv_data = invoice.generate_invoice()
    print(f"Invoice Total: ₹{inv_data['invoice_total']}")

    # Create GST return
    gst_return = GSTReturn("2025-05", "B2B")
    gst_return.add_outward_supply(0.18, 10000)
    gst_return.add_itc(0.18, 2000)

    # Generate return summary
    summary = gst_return.generate_return_summary()
    print(f"GST Payable: ₹{summary['total_gst_payable']}")
