"""Bản 24: the product sold most within a group (in units or by money), stock at cost,
the supplier bought from most and the cancelled share in everyday words. Questions,
groups and products are written for these tests.
"""
import unittest
from datetime import date
from decimal import Decimal

import test_business_measures as business
import test_group_measures as groups
import test_new_intents as intents
import test_purchases as purchases

from datamate.agent.compute import _answer_group, unsupported
from datamate.contracts import Question
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules


class GroupTopPlanTests(unittest.TestCase):
    def test_in_units_and_by_money(self):
        for text, rank_by in (
                ("tháng 3/2026 nhóm nước ép có mặt hàng nào bán được nhiều ly nhất", "quantity:ly"),
                ("tháng 3/2026 trong nhóm bánh món nào bán được nhiều tiền nhất", "revenue"),
                ("tháng 3/2026 nhóm gỏi thì sản phẩm nào thu về nhiều tiền nhất", "revenue"),
                ("trong nhóm bánh mặt hàng nào có doanh thu cao nhất tháng 3/2026", "revenue"),
                ("mặt hàng có doanh thu cao nhất của nhóm gỏi tháng 3/2026 là mặt hàng nào", "revenue")):
            with self.subTest(text=text):
                result = groups.plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.intent, result.rank_by, result.top_k), ("top_products", rank_by, 1))
                self.assertEqual(unsupported(result), "")

    def test_rank_by_stays_closed(self):
        result = groups.plan("tháng 3/2026 trong nhóm bánh món nào bán được nhiều tiền nhất")
        from dataclasses import replace
        self.assertNotEqual(unsupported(replace(result, rank_by="margin")), "")
        self.assertNotEqual(unsupported(replace(result, filters=())), "")


class GroupTopComputeTests(unittest.TestCase):
    def sales(self, second_unit="ly", second_amount=40):
        day = date(2026, 3, 4)
        return [
            Sale("A", day, "valid", Decimal(30), "J1", "Ép cam", Decimal(3), base_unit="ly",
                 product_group="nước ép"),
            Sale("B", day, "valid", Decimal(second_amount), "J2", "Ép táo", Decimal(2),
                 base_unit=second_unit, product_group="nước ép"),
            Sale("C", day, "cancelled", Decimal(90), "J2", "Ép táo", Decimal(9), base_unit=second_unit,
                 product_group="nước ép"),
            Sale("D", day, "valid", Decimal(99), "B1", "Bánh mì", Decimal(9), base_unit="cái",
                 product_group="bánh"),
        ]

    def answer(self, text, sales):
        return _answer_group(groups.plan(text), sales, {"inputs": {}})

    def test_units_and_money(self):
        units = "tháng 3/2026 nhóm nước ép có mặt hàng nào bán được nhiều ly nhất"
        money = "tháng 3/2026 trong nhóm nước ép món nào bán được nhiều tiền nhất"
        self.assertEqual(self.answer(units, self.sales()).answer, "Ép cam")
        self.assertEqual(self.answer(money, self.sales()).answer, "Ép táo")

    def test_mixed_units_and_money_ties_abstain(self):
        units = "tháng 3/2026 nhóm nước ép có mặt hàng nào bán được nhiều ly nhất"
        money = "tháng 3/2026 trong nhóm nước ép món nào bán được nhiều tiền nhất"
        self.assertEqual(self.answer(units, self.sales(second_unit="chai")).type, "abstain")
        self.assertEqual(self.answer(money, self.sales(second_amount=30)).type, "abstain")


class EverydayMeasureTests(unittest.TestCase):
    def test_stock_at_cost(self):
        for text in ("cuối ngày 30/04/2026 hàng còn trong kho tính theo giá vốn đang bao nhiêu tiền",
                     "hết ngày 30/04/2026 hàng tồn trong kho của cửa hàng tính giá vốn là bnhiu tiền"):
            with self.subTest(text=text):
                result = business.plan(text)
                self.assertEqual((result.intent, result.start), ("stock_value", date(2026, 4, 30)))
        # "giá trị hàng còn trong kho tính theo giá vốn" keeps its own reading.
        from datamate.semantics.text import normalize_question
        self.assertIn("gia tri hang con trong kho tinh theo gia von", normalize_question(
            "Cuối ngày 30/04/2026, tổng giá trị hàng còn trong kho tính theo giá vốn là bao nhiêu tiền?"))

    def test_supplier_and_cancelled_share(self):
        for text in ("Tháng 6/2026 cửa hàng nhập của nhà cung cấp nào nhiều tiền nhất",
                     "tháng 6/2026 nhập tiền hàng của nhà cung cấp nào cao nhất"):
            with self.subTest(text=text):
                self.assertEqual(purchases.plan(text).intent, "top_supplier")
        rules = read_rules(intents.rulebook())
        for text in ("tháng 3/2026 đơn huỷ chiếm bnhiu phần trăm tất cả các đơn",
                     "tháng 3/2026 đơn hủy chiếm mấy phần trăm tổng số đơn hàng"):
            with self.subTest(text=text):
                result = plan_question(Question("x", "b", text, date(2026, 4, 10)), rules)
                self.assertEqual(result.intent, "cancel_rate")


if __name__ == "__main__":
    unittest.main()
