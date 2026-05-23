"""Unit tests for gst_hack."""

import unittest

from gst_hack import GSTReturn, GSTType, Invoice, LineItem


class LineItemTests(unittest.TestCase):
    def test_calculations(self):
        item = LineItem("Furniture", "9402", quantity=5, unit_price=1000, gst_rate=0.18)
        self.assertEqual(item.calculate_amount(), 5000)
        self.assertEqual(item.calculate_gst(), 900)
        self.assertEqual(item.calculate_total(), 5900)


class InvoiceTests(unittest.TestCase):
    def _invoice(self, gst_type):
        inv = Invoice(
            invoice_number="INV-001",
            supplier_name="ABC", supplier_gstin="27AAFCD1234A1Z5",
            customer_name="XYZ", customer_gstin="09ABCDS1111A1Z5",
            gst_type=gst_type,
        )
        inv.add_item(LineItem("Furniture", "9402", 5, 1000, 0.18))
        inv.add_item(LineItem("Office Supplies", "9990", 10, 100, 0.18))
        return inv

    def test_intra_state_splits_sgst_cgst(self):
        inv = self._invoice(GSTType.INTRA_STATE).generate_invoice()
        self.assertEqual(inv["subtotal"], 6000)
        self.assertEqual(inv["total_gst"], 1080)
        self.assertEqual(inv["sgst"], 540)
        self.assertEqual(inv["cgst"], 540)
        self.assertEqual(inv["igst"], 0)
        self.assertEqual(inv["invoice_total"], 7080)

    def test_inter_state_uses_igst(self):
        inv = self._invoice(GSTType.INTER_STATE).generate_invoice()
        self.assertEqual(inv["sgst"], 0)
        self.assertEqual(inv["cgst"], 0)
        self.assertEqual(inv["igst"], 1080)
        self.assertEqual(inv["invoice_total"], 7080)

    def test_empty_invoice_totals_zero(self):
        inv = Invoice(invoice_number="EMPTY").generate_invoice()
        self.assertEqual(inv["subtotal"], 0)
        self.assertEqual(inv["invoice_total"], 0)


class GSTReturnTests(unittest.TestCase):
    def test_outward_supplies_accumulate_by_rate(self):
        r = GSTReturn("2026-05", "B2B")
        r.add_outward_supply(0.18, 10000)
        r.add_outward_supply(0.18, 5000)
        r.add_outward_supply(0.12, 4000)
        self.assertEqual(r.outward_supplies[0.18], 15000)
        self.assertEqual(r.outward_supplies[0.12], 4000)

    def test_itc_offsets_output_tax(self):
        r = GSTReturn("2026-05", "B2B")
        r.add_outward_supply(0.18, 10000)  # output tax = 1800
        r.add_itc(0.18, 500)
        liability = r.calculate_gst_liability()[0.18]
        self.assertEqual(liability["output_tax"], 1800)
        self.assertEqual(liability["input_credit"], 500)
        self.assertEqual(liability["net_liability"], 1300)

    def test_itc_excess_floors_at_zero(self):
        r = GSTReturn("2026-05", "B2B")
        r.add_outward_supply(0.18, 10000)
        r.add_itc(0.18, 5000)  # ITC exceeds output tax
        self.assertEqual(r.calculate_gst_liability()[0.18]["net_liability"], 0)

    def test_summary_total_payable(self):
        r = GSTReturn("2026-05", "B2B")
        r.add_outward_supply(0.18, 10000)
        r.add_itc(0.18, 2000)
        summary = r.generate_return_summary()
        self.assertEqual(summary["return_month"], "2026-05")
        self.assertEqual(summary["total_gst_payable"], 0)


if __name__ == "__main__":
    unittest.main()
