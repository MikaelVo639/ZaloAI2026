"""Cups of size M / L: items whose name ends with "(M)" / "(L)" (rulebook "Size M, size L").

Rulebook rows and sales below are synthetic, written for tests.
"""
import unittest
from datetime import date

from test_semantics_periods import rulebook

from datamate.agent.compute import select_size
from datamate.contracts import Question
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

SIZE = ("| Size M, size L | món có tên kết thúc bằng `(M)` hoặc `(L)`, **không phân biệt hoa "
        "thường** |")
UNIT = ("| Bán được bao nhiêu <đơn vị> | cộng số lượng quy về đơn vị cơ bản của các mặt hàng "
        "có đơn vị cơ bản là <đơn vị>; mặt hàng có đơn vị cơ bản khác không tính |")


def plan(text, extra=SIZE + "\n" + UNIT):
    rules = read_rules(rulebook() + extra + "\n")
    return plan_question(Question("x", "b", text, date(2026, 4, 10)), rules)


class SizeGrammarTests(unittest.TestCase):
    def test_train_wordings(self):
        for text, size in (
            ("Tháng 3/2026 quán bán được bao nhiêu ly size M?", "M"),
            ("Tháng 3/2026 quán bán ra tổng cộng mấy ly size L cộng tất cả các món?", "L"),
            ("Trong tháng 3/2026 số ly size m bán được là bao nhiêu, tính mọi loại đồ uống?", "M"),
            ("Quán bán được bao nhiêu ly size l trong tháng 3/2026 cộng mỗi món?", "L"),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.unit), ("quantity", "ly"))
                self.assertEqual([(f.field, f.value) for f in result.filters], [("size", size)])

    def test_needs_the_rule_and_one_size(self):
        self.assertIsNone(plan("Tháng 3/2026 quán bán được bao nhiêu ly size M?", extra=UNIT))
        self.assertIsNone(plan("Tháng 3/2026 bán được bao nhiêu ly size M và size L?"))
        self.assertIsNone(plan("Doanh thu size M tháng 3/2026 là bao nhiêu?"))

    def test_select_size_by_name_suffix(self):
        sales = [Sale(o, date(2026, 3, 1), "valid", 0, "P", name, 1)
                 for o, name in (("A", "Trà đào (M)"), ("B", "Trà đào (l)"), ("C", "Size M cake"),
                                 ("D", "Cà phê muối (M) "))]
        self.assertEqual([s.order_id for s in select_size(sales, "M")], ["A", "D"])
        self.assertEqual([s.order_id for s in select_size(sales, "L")], ["B"])


if __name__ == "__main__":
    unittest.main()
