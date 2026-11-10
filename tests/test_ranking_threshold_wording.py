"""Regression cases for ranking and thresholds, using synthetic questions and sales."""

import unittest
from datetime import date
from decimal import Decimal

from test_sem02_grammar import plan, rulebook, sale

from datamate.agent import compute
from datamate.contracts import Catalog


def answer(planned, sales):
    catalog = Catalog("synthetic", "", ())
    cache = {
        "sales_key": compute._catalog_key(catalog),
        "sales_rulebook": "",
        "sales": (sales, {"tables": [], "sales_rows": len(sales)}),
    }
    return compute.answer_plan(cache, catalog, planned)


class RankingThresholdWordingTests(unittest.TestCase):
    def test_best_day_keeps_the_period_and_existing_tie_behavior(self):
        canonical = plan("Ngày nào doanh thu cao nhất tháng 5/2026?")
        self.assertIsNotNone(canonical)
        sales = [
            sale("A", date(2026, 5, 3), 400),
            sale("B", date(2026, 5, 4), 500),
            sale("C", date(2026, 6, 1), 900),
            sale("D", date(2026, 5, 2), 800, "cancelled"),
        ]
        for text in (
            "Tháng 5/2026 đạt doanh thu cao nhất vào ngày nào?",
            "Hôm nào có doanh thu cao nhứt trong tháng 5/2026?",
            "Trong tháng 5/2026 ngày mô doanh thu cao nhất?",
        ):
            with self.subTest(text=text):
                planned = plan(text)
                self.assertEqual(planned, canonical)
                result = answer(planned, sales)
                self.assertEqual((result.type, result.answer), ("date", "2026-05-04"))
        tied = sales + [sale("E", date(2026, 5, 3), 100)]
        self.assertEqual(answer(canonical, tied).type, "abstain")
        self.assertIsNone(plan("Hôm nào doanh thu thấp nhất tháng 5/2026?"))
        self.assertIsNone(plan("Ngày mô doanh thu cao nhất tháng 5/2026, riêng nhóm sỉ?"))

    def test_explicit_product_count_and_answer_wording(self):
        expected = plan("Top 2 mặt hàng bán chạy nhất tháng 5/2026 là gì?")
        self.assertIsNotNone(expected)
        for text in (
            "Tháng 5/2026 top hai món bán chạy nhứt là những món nào?",
            "2 món bán nhiều nhất trong tháng 5/2026 là món nào?",
        ):
            with self.subTest(text=text):
                self.assertEqual(plan(text), expected)
        sales = [
            sale("A", date(2026, 5, 1), 100, product="P1", name="Trà", quantity=10),
            sale("B", date(2026, 5, 2), 200, product="P2", name="Cà phê", quantity=5),
            sale("C", date(2026, 5, 3), 300, product="P3", name="Nước", quantity=2),
        ]
        result = answer(expected, sales)
        self.assertEqual((result.type, result.answer), ("list", ["Trà", "Cà phê"]))
        self.assertIsNone(plan("Top hai món có doanh thu cao nhất tháng 5/2026?"))
        self.assertIsNone(plan("Top hai món bán chạy nhất tháng 5/2026, chỉ nhóm Trà?"))
        self.assertIsNone(plan("Top 500 món bán chạy nhất tháng 5/2026?"))

    def test_customer_phrasing_preserves_the_ranking_measure(self):
        by_orders = plan("Khách nào mua nhiều đơn nhất tháng 5/2026?")
        self.assertIsNotNone(by_orders)
        for text in (
            "Tháng 5/2026 ai mua nhiều đơn hàng nhất?",
            "Tháng 5/2026 khách mô mua nhiều đơn nhứt?",
            "Tháng 5/2026 khách nào có nhiều đơn nhất?",
            "Tháng 5/2026 ai là khách mua nhiều lần nhất, đếm theo số đơn?",
        ):
            with self.subTest(text=text):
                self.assertEqual(plan(text), by_orders)
        by_revenue = plan("Tháng 5/2026 khách nào mua nhiều tiền nhất?")
        self.assertEqual((by_orders.rank_by, by_revenue.rank_by), ("order_count", "revenue"))
        day = date(2026, 5, 5)
        sales = [
            sale("A", day, 10, customer_id="K1", customer_name="An"),
            sale("B", day, 10, customer_id="K1", customer_name="An"),
            sale("C", day, 10, customer_id="K2", customer_name="Bình"),
            sale("C", day, 50, product="P2", customer_id="K2", customer_name="Bình"),
        ]
        self.assertEqual(answer(by_orders, sales).answer, "An")
        self.assertEqual(answer(by_revenue, sales).answer, "Bình")
        self.assertIsNone(plan("Tháng 5/2026 ai mua nhiều lần nhất?"))
        self.assertIsNone(plan("Tháng 5/2026 ai mua nhiều đơn nhất, chỉ tính khách sỉ?"))
        self.assertIsNone(plan("Tháng 5/2026 ai mua nhiều đơn nhất?", book=rulebook("customers")))

    def test_threshold_spelling_preserves_amount_and_subject(self):
        day = date(2026, 5, 5)
        sales = [sale("A", day, 400_000), sale("B", day, 500_000), sale("C", day, 600_000)]
        for word, subject, count in (("đơn", "order", 2), ("ngày", "day", 1)):
            question = f"Tháng 5/2026 có bao nhiêu {word} có doanh thu từ {{}}?"
            expected = plan(question.format("500.000 đồng trở lên"))
            self.assertIsNotNone(expected)
            for amount in ("500k trở lên", "500.000 đồng trở lne"):
                with self.subTest(subject=subject, amount=amount):
                    planned = plan(question.format(amount))
                    self.assertEqual(planned, expected)
                    self.assertEqual((planned.threshold.subject, planned.threshold.op,
                                      planned.threshold.value), (subject, ">=", Decimal(500_000)))
                    self.assertEqual(answer(planned, sales).answer, count)

    def test_threshold_uses_the_date_as_a_period_not_a_count_subject(self):
        result = plan("Ngày 12/05/2026 có bao nhiêu đơn có doanh thu từ 2500k trở lên?")
        self.assertIsNotNone(result)
        self.assertEqual((result.start, result.end), (date(2026, 5, 12), date(2026, 5, 12)))
        self.assertEqual((result.threshold.subject, result.threshold.value),
                         ("order", Decimal(2_500_000)))

    def test_money_shorthand_does_not_drop_ambiguous_units_or_conditions(self):
        question = "Tháng 5/2026 có mấy đơn có doanh thu từ {} trở lên?"
        for amount in ("1,5k", "1, 5k", "-500k", "500kg", "3tr7"):
            with self.subTest(amount=amount):
                self.assertIsNone(plan(question.format(amount)))
        self.assertIsNone(plan(question.format("500k"), book=rulebook("threshold")))
        self.assertIsNone(plan("Tháng 5/2026 có mấy đơn doanh thu từ 500k trở lên, riêng kênh sàn?"))

    def test_trade_in_wording_still_needs_a_supported_money_measure(self):
        self.assertIsNone(plan("Tháng 5/2026 bình quân mỗi máy khách thực trả bao nhiêu tiền "
                               "sau khi trừ tiền thu máy cũ?"))
