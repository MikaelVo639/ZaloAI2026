"""Bản 23: everyday wordings of thresholds in millions, running periods, customer and
day rankings, units over every product, average order value and shift comparisons.
Questions are written for these tests; rulebook rows are the synthetic ones of the
imported test modules.
"""
import unittest
from datetime import date
from decimal import Decimal

import test_sem02_grammar as sem02
import test_shift as shift
from test_sem02_grammar import plan

from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question

AVERAGE = "| Giá trị trung bình một đơn | làm tròn về đồng, nửa đồng làm tròn lên |\n"


class MillionThresholdTests(unittest.TestCase):
    def test_days_and_orders_in_millions(self):
        for text, subject, value in (
                ("tháng 4/2026 có mấy bữa cửa hàng bán được từ 12 củ trở lên", "day", 12_000_000),
                ("tháng 4/2026 bn ngày bán được từ 30tr trở lên", "day", 30_000_000),
                ("tháng 4/2026 có bao nhiêu ngày bán được từ 8 triệu trở lên", "day", 8_000_000),
                ("tháng 4/2026 có bao nhiêu bữa doanh thu của cửa hàng đạt từ 12 củ trở lên", "day",
                 12_000_000),
                ("tháng 4/2026 cửa hàng bn đơn doanh thu đạt từ 2tr5 trở lên", "order", 2_500_000)):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.intent, result.threshold.subject, result.threshold.value),
                                 ("count_where", subject, Decimal(value)))

    def test_old_goods_are_not_millions(self):
        self.assertIn("may cu", normalize_question("tháng 4/2026 bán được 3 máy cũ"))


class EverydayWordingTests(unittest.TestCase):
    def test_running_periods_and_totals(self):
        for text, intent, start in (
                ("từ đầu năm nay tới giờ shop lên được bao nhiêu đơn", "order_count",
                 date(2026, 1, 1)),
                ("đầu năm đến hôm nay tổng số đơn hàng bnhiu", "order_count", date(2026, 1, 1)),
                ("tuần này tính tới hôm nay doanh thu là bao nhiêu", "revenue", date(2026, 7, 13)),
                ("hôm qua có doanh thu tổng bnhiu", "revenue", date(2026, 7, 14)),
                ("hôm nay tổng bnhiu đơn hàng", "order_count", date(2026, 7, 15))):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.intent, result.start), (intent, start))

    def test_rankings(self):
        for text, intent in (
                ("tháng 4/2026 khách nào mua nhiều lần nhất nhỉ đếm theo số đơn ấy", "top_customers"),
                ("tháng 4/2026 khách nào có nhiều đơn nhất", "top_customers"),
                ("trong tháng 4/2026 khách nào tổng tiền mua hàng cao nhất", "top_customers"),
                ("tháng 4/2026 ngày nào bán đắt nhất tính theo doanh thu", "best_day"),
                ("tháng 4/2026 bữa nào cửa hàng bán được nhiều tiền nhất", "best_day"),
                ("tháng 4/2026 mặt hàng nào bán chạy nhất thế", "top_products")):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual(result.intent, intent)
        self.assertEqual(plan("tháng 4/2026 khách nào mua nhiều đơn nhất").rank_by, "order_count")
        # "nhiều lần nhất" alone does not say whether orders or money are counted.
        self.assertIsNone(plan("tháng 4/2026 khách nào mua nhiều lần nhất"))

    def test_units_over_every_product(self):
        for text, unit in (("tuần trước bán được tổng mấy hộp tính hết các mặt hàng bán theo hộp", "hop"),
                           ("tất cả mặt hàng tính theo gói tuần trước bán được mấy gói", "goi")):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.unit), ("quantity", unit))

    def test_average_order_value(self):
        book = sem02.rulebook() + AVERAGE
        for text in ("tháng 4/2026 trung bình 1 đơn được bao nhiêu doanh thu",
                     "tháng 4/2026 khách mua trung bình một đơn hết bnhiu tiền",
                     "tháng 4/2026 trung bình mỗi đơn hàng mang lại bao nhiêu doanh thu"):
            with self.subTest(text=text):
                self.assertEqual(plan(text, book=book).intent, "average_order")

    def test_shift_against_shift(self):
        for text in ("tháng 3/2026 số đơn ca chiều cao hay thấp hơn ca sáng bao nhiêu phần trăm",
                     "số đơn ca chiều tháng 3/2026 nhiều hay ít hơn ca sáng mấy phần trăm"):
            with self.subTest(text=text):
                result = plan_question(Question("x", "b", text, date(2026, 4, 10)),
                                       read_rules(shift.book()))
                self.assertEqual((result.intent, result.comparison),
                                 ("order_count", "shift_change_percent"))


if __name__ == "__main__":
    unittest.main()
