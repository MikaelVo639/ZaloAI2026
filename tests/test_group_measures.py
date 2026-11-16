"""Bản 22: a product group with order counts and units, group share wordings, and more
price-increase wordings. Questions, groups and products are written for these tests.
"""
import unittest
from datetime import date
from decimal import Decimal

import test_group_share_wording as group_words
import test_sem02_grammar as sem02
from test_price_effect import plan as price_plan

from datamate.agent.compute import _answer_group
from datamate.contracts import Question
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

BOOK = group_words.rulebook() + group_words.GROUP + group_words.SHARE + sem02.ROWS["unit"] + "\n"


def plan(text):
    return plan_question(Question("x", "b", text, date(2026, 7, 10)), read_rules(BOOK), (),
                         group_words.VOCABULARY)


class GroupPlanTests(unittest.TestCase):
    def test_orders_holding_the_group(self):
        for text in ("tháng 3/2026 có bao nhiêu đơn mua hàng nhóm bánh",
                     "số đơn có mua hàng nhóm bánh trong tháng 3/2026 là bao nhiêu",
                     "tháng 3/2026 bao nhiêu đơn có ít nhất 1 mặt hàng nhóm bánh"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.intent, result.comparison, result.filters[0].value),
                                 ("order_count", "", "bánh"))

    def test_units_of_the_group(self):
        for text, unit in (("tháng 3/2026 nhóm nước ép bán được bao nhiêu ly", "ly"),
                           ("trong tháng 3/2026 các mặt hàng nhóm gỏi bán ra tổng cộng mấy đĩa", "dia"),
                           ("tổng số cái nhóm bánh bán được trong tháng 3/2026", "cai")):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.intent, result.unit), ("quantity", unit))

    def test_share_wordings(self):
        for text in ("tháng 3/2026 nhóm bánh đóng góp mấy phần trăm doanh thu",
                     "nhóm gỏi tháng 3/2026 mang về bao nhiêu % tổng doanh thu cửa hàng",
                     "tháng 3/2026 nhóm gỏi đóng góp bao nhiêu % trên tổng doanh thu"):
            with self.subTest(text=text):
                self.assertEqual(plan(text).comparison, "share")
        self.assertEqual(plan("tháng 3/2026 nhóm gỏi mang về bao nhiêu doanh thu").comparison, "")

    def test_other_group_measures_still_abstain(self):
        for text in ("tháng 3/2026 nhóm bánh chiếm bao nhiêu phần trăm số đơn",
                     "tháng 3/2026 nhóm bánh có mặt hàng nào bán được nhiều cái nhất"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))


class GroupComputeTests(unittest.TestCase):
    def sales(self, juice_unit="ly"):
        day = date(2026, 3, 4)
        return [
            Sale("A", day, "valid", Decimal(30), "J1", "Ép cam", Decimal(2), base_unit="ly",
                 product_group="nước ép"),
            Sale("A", day, "valid", Decimal(20), "B1", "Bánh mì", Decimal(1), base_unit="cái",
                 product_group="bánh"),
            Sale("B", day, "valid", Decimal(15), "J2", "Ép táo", Decimal(3), base_unit=juice_unit,
                 product_group="nước ép"),
            Sale("C", day, "cancelled", Decimal(15), "J2", "Ép táo", Decimal(9), base_unit="ly",
                 product_group="nước ép"),
            Sale("D", day, "valid", Decimal(20), "B1", "Bánh mì", Decimal(4), base_unit="cái",
                 product_group="bánh"),
        ]

    def test_order_count_and_units(self):
        orders = plan("tháng 3/2026 có bao nhiêu đơn mua hàng nhóm nước ép")
        self.assertEqual(_answer_group(orders, self.sales(), {"inputs": {}}).answer, 2)
        units = plan("tháng 3/2026 nhóm nước ép bán được bao nhiêu ly")
        self.assertEqual(_answer_group(units, self.sales(), {"inputs": {}}).answer, 5)

    def test_group_sold_in_several_units_abstains(self):
        units = plan("tháng 3/2026 nhóm nước ép bán được bao nhiêu ly")
        result = _answer_group(units, self.sales(juice_unit="chai"), {"inputs": {}})
        self.assertEqual(result.type, "abstain")


class PriceIncreaseWordingTests(unittest.TestCase):
    def test_more_wordings(self):
        window = (date(2026, 6, 10), date(2026, 6, 16), date(2026, 6, 3))
        for text in (
                "Prospan siro ho chai 100ml lên 95k từ 10/6/2026, số chai bán 7 ngày từ hôm đó so "
                "với 7 ngày trước tăng hay giảm bao nhiêu phần trăm",
                "Từ khi Prospan siro ho chai 100ml tăng giá lên 95.000đ/chai, số chai bán trong 7 ngày "
                "kể từ 10/6/2026 so với 7 ngày liền trước nhiều hơn hay ít hơn bao nhiêu phần trăm",
                "Prospan siro ho chai 100ml lên giá 95.000đ ngày 10/6/2026, lượng chai bán trong 7 "
                "ngày kể từ 10/6/2026 thay đổi bao nhiêu phần trăm so với 7 ngày liền trước"):
            with self.subTest(text=text):
                result = price_plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.start, result.end, result.compare_start, result.unit),
                                 (*window, "chai"))
        result = price_plan("Cơm trắng tăng giá từ 10k lên 12k ngày 10/6/2026, số phần bán ra trong "
                            "7 ngày kể từ ngày đó cao hay thấp hơn 7 ngày liền trước bao nhiêu phần trăm")
        self.assertEqual((result.start, result.unit), (date(2026, 6, 10), "phan"))
        # A lower new price is not an increase.
        self.assertIsNone(price_plan("Cơm trắng đổi giá từ 12k lên 10k ngày 10/6/2026, số phần bán "
                                     "ra trong 7 ngày kể từ ngày đó so với 7 ngày trước đó thay đổi "
                                     "bao nhiêu phần trăm"))


if __name__ == "__main__":
    unittest.main()
