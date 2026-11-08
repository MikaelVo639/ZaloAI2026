"""Staff questions: revenue of one staff code, and the best seller by revenue.

Rulebook rows and sales below are synthetic, written for tests.
"""
import unittest
from datetime import date
from decimal import Decimal

from test_semantics_periods import rulebook

from datamate.agent.compute import _answer_staff, best_staff, select_staff
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

BEST = "| Nhân viên bán giỏi nhất | theo **doanh thu** của nhân viên trong kỳ, không theo số đơn |"


def plan(text, book=None):
    rules = read_rules(book if book is not None else rulebook() + BEST + "\n")
    return plan_question(Question("x", "b", text, date(2026, 4, 10)), rules)


def sale(order, staff, amount, status="valid"):
    return Sale(order, date(2026, 3, 5), status, Decimal(amount), "P1", "Trà", Decimal(1),
                staff_id=staff)


SALES = [sale("A", "NV001", 100), sale("A", "NV001", 50), sale("B", "NV002", 150),
         sale("C", "NV002", 30), sale("D", "NV003", 999, "cancelled")]


class StaffGrammarTests(unittest.TestCase):
    def test_revenue_of_one_staff_code(self):
        for text in (
            "Tháng 3/2026 nhân viên mã NV001 bán được bao nhiêu tiền?",
            "Cho hỏi dthu của nhân viên mã nv001 tháng 3/2026 là bao nhiêu",
            "Nhân viên mã NV001 tháng 3/2026 bán được tổng cộng bao nhiêu tiền?",
            "Tháng 3/2026 tiền hàng các đơn do nhân viên mã NV001 bán cộng lại là bao nhiêu?",
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual(result.intent, "revenue")
                self.assertEqual([(f.field, f.value) for f in result.filters], [("staff", "NV001")])

    def test_best_seller(self):
        for text in ("Nhân viên bán giỏi nhất tháng 3/2026 là ai?",
                     "Ai là người bán có doanh thu cao nhất tháng 3/2026?",
                     "Trong tháng 3/2026 nhân viên nào có doanh thu cao nhất?"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.filters), ("top_staff", ()))
        # "giỏi nhất" means revenue only when the split's rulebook says so.
        self.assertIsNone(plan("Nhân viên bán giỏi nhất tháng 3/2026 là ai?", book=rulebook()))
        self.assertEqual(plan("Ai là người bán có doanh thu cao nhất tháng 3/2026?",
                              book=rulebook()).intent, "top_staff")

    def test_other_staff_questions_abstain(self):
        for text in ("Nhân viên nào có tay nghề cao nhất tiệm?",
                     "Nhân viên Bùi Dung Lan tháng 3/2026 bán được bao nhiêu tiền?",
                     "Tháng 3/2026 nhân viên mã NV001 và NV002 bán được bao nhiêu tiền?",
                     "Tháng 3/2026 nhân viên mã NV001 bán chạy nhất mặt hàng nào?"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))


class StaffExecutionTests(unittest.TestCase):
    def run_plan(self, text, sales=SALES):
        result = plan(text)
        return result, _answer_staff(result, sales, {"inputs": {}})

    def test_revenue_orders_and_best_seller(self):
        self.assertEqual(len(select_staff(SALES, "nv001")), 2)
        self.assertEqual(best_staff([s for s in SALES if s.status == "valid"]),
                         ("NV002", Decimal(180)))
        result, candidate = self.run_plan("Tháng 3/2026 nhân viên mã NV001 bán được bao nhiêu tiền?")
        self.assertEqual(candidate.answer, 150)
        self.assertIs(check_executed(candidate, result), candidate)
        result, candidate = self.run_plan("Nhân viên bán giỏi nhất tháng 3/2026 là ai?")
        self.assertEqual(candidate.answer, "NV002")
        self.assertIs(check_executed(candidate, result), candidate)

    def test_ties_by_code_and_missing_seller(self):
        tied = [sale("A", "NV002", 100), sale("B", "NV001", 100)]
        self.assertEqual(best_staff(tied)[0], "NV001")
        _, candidate = self.run_plan("Nhân viên bán giỏi nhất tháng 3/2026 là ai?",
                                     SALES + [sale("E", None, 10)])
        self.assertEqual(candidate.type, "abstain")
        _, candidate = self.run_plan("Tháng 3/2026 nhân viên mã NV009 bán được bao nhiêu tiền?")
        self.assertEqual(candidate.type, "abstain")


if __name__ == "__main__":
    unittest.main()
