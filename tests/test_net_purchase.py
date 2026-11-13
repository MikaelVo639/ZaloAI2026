"""Purchases minus sales of one product (rulebook §5). Names and numbers are invented."""
import unittest
from datetime import date
from decimal import Decimal

from test_semantics_periods import rulebook

from datamate.agent import compute
from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

NAMES = "| Tên gọi | Tên gọi khác nhau cùng chỉ một thứ là một mặt hàng; file có mã thì theo mã |"
RULE = ("| Nhập trừ bán, chênh nhập và bán | phần **bán** là số lượng của đơn tính vào doanh "
        "thu (§1), quy về cùng đơn vị với phần nhập |")
BOOK = rulebook() + NAMES + "\n" + RULE + "\n"


def plan(text, book=BOOK, today="2026-07-14"):
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), read_rules(book),
                         ["Trà sen hộp 20 gói", "Bánh quy mè"])


class NetPurchaseTests(unittest.TestCase):
    def test_wordings(self):
        for text, unit in (
            ("Lấy số nhập trừ số bán của Trà sen hộp 20 gói trong tháng 6/2026 thì được bao"
             " nhiêu gói?", "goi"),
            ("lay so nhap tru so ban cua banh quy me trong thang 6/2026 thi dc bnhiu cai", "cai"),
            ("Tháng 6/2026, số nhập trừ số bán của Bánh quy mè là bao nhiêu cái?", "cai"),
            ("Chênh lệch nhập trừ bán của Trà sen hộp 20 gói trong tháng 6 năm 2026 là bao"
             " nhiêu gói?", "goi"),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.unit, result.start, result.end),
                                 ("net_purchase", unit, date(2026, 6, 1), date(2026, 6, 30)))
        for text in ("Số nhập trừ số bán tháng 6/2026 là bao nhiêu gói?",       # no product
                     "Số nhập trừ số bán của Trà sen hộp 20 gói tháng 6/2026 là bao nhiêu tiền?",
                     "Số nhập trừ số bán của Bánh quy mè tháng 9/2026 là bao nhiêu cái?"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))
        self.assertIsNone(plan("Số nhập trừ số bán của Bánh quy mè tháng 6/2026 là bao nhiêu cái?",
                               book=rulebook() + NAMES + "\n"))

    def test_purchases_are_converted_to_the_base_unit(self):
        # 3 boxes of 20 and 5 loose packs bought, 61 packs sold: 4 packs left over.
        bought = [(Decimal(3), Decimal(20)), (Decimal(5), Decimal(1))]
        self.assertEqual(compute.net_purchase(bought, Decimal(61), None), Decimal(4))
        self.assertEqual(compute.net_purchase(bought, Decimal(70), None), Decimal(-5))
        # In the purchase unit: 3 boxes minus 50 packs sold (2.5 boxes).
        self.assertEqual(compute.net_purchase([(Decimal(3), Decimal(20))], Decimal(50),
                                              Decimal(20)), Decimal("0.5"))


if __name__ == "__main__":
    unittest.main()
