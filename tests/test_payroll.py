"""Unit tests for payroll_hack."""

import unittest

from payroll_hack import Employee, PayrollManager, Payslip


class EmployeeTests(unittest.TestCase):
    def setUp(self):
        self.mgr = PayrollManager()

    def test_add_employee_returns_employee(self):
        emp = self.mgr.add_employee("EMP001", "Naveen", "Engineer", 50000, "ABCDE1234F")
        self.assertIsInstance(emp, Employee)
        self.assertEqual(emp.monthly_basic, 50000)

    def test_duplicate_employee_raises(self):
        self.mgr.add_employee("EMP001", "Naveen", "Engineer", 50000)
        with self.assertRaises(ValueError):
            self.mgr.add_employee("EMP001", "Other", "Manager", 60000)

    def test_non_positive_basic_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.add_employee("EMP002", "Bad", "Engineer", 0)
        with self.assertRaises(ValueError):
            self.mgr.add_employee("EMP003", "Bad", "Engineer", -100)


class PayslipTests(unittest.TestCase):
    def setUp(self):
        self.mgr = PayrollManager()
        self.mgr.add_employee("EMP001", "Naveen", "Engineer", 50000)

    def test_payslip_components(self):
        slip = self.mgr.generate_payslip("PS1", "EMP001", "2026-05", tds=2500)
        self.assertEqual(slip.basic, 50000)
        self.assertEqual(slip.hra, 20000)              # 40% of basic
        self.assertEqual(slip.special_allowance, 10000) # 20% of basic
        self.assertEqual(slip.pf_deduction, 6000)      # 12% of basic
        self.assertEqual(slip.professional_tax, 200)

    def test_net_pay_arithmetic(self):
        slip = self.mgr.generate_payslip("PS1", "EMP001", "2026-05", tds=2500)
        self.assertEqual(slip.gross_earnings, 80000)
        self.assertEqual(slip.total_deductions, 8700)  # 6000 + 200 + 2500
        self.assertEqual(slip.net_pay, 71300)

    def test_unknown_employee_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.generate_payslip("PS1", "MISSING", "2026-05")

    def test_negative_tds_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.generate_payslip("PS1", "EMP001", "2026-05", tds=-1)

    def test_get_payslips_for_employee(self):
        self.mgr.generate_payslip("PS1", "EMP001", "2026-04")
        self.mgr.generate_payslip("PS2", "EMP001", "2026-05")
        slips = self.mgr.get_payslips_for_employee("EMP001")
        self.assertEqual(len(slips), 2)
        self.assertTrue(all(isinstance(s, Payslip) for s in slips))


class MonthlyPayrollTests(unittest.TestCase):
    def test_monthly_aggregate(self):
        mgr = PayrollManager()
        mgr.add_employee("EMP001", "Naveen", "Engineer", 50000)
        mgr.add_employee("EMP002", "Asha", "PM", 80000)
        mgr.generate_payslip("PS1", "EMP001", "2026-05", tds=2500)
        mgr.generate_payslip("PS2", "EMP002", "2026-05", tds=6000)
        mgr.generate_payslip("PS3", "EMP001", "2026-04", tds=2500)  # different month

        summary = mgr.calculate_monthly_payroll("2026-05")
        self.assertEqual(summary["employee_count"], 2)
        self.assertEqual(summary["total_gross"], 80000 + 128000)
        self.assertEqual(summary["total_tds"], 8500)
        self.assertEqual(summary["total_net_pay"], 71300 + 112200)


if __name__ == "__main__":
    unittest.main()
