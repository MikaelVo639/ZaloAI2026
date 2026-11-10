"""Regression cases for ranking and thresholds, using synthetic questions and sales."""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from test_group_share_wording import GROUP
from test_new_intents import ROWS as INTENT_RULES
from test_sem02_grammar import plan, rulebook, sale

from datamate.agent import compute
from datamate.contracts import Catalog, Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules


def answer(planned, sales, book=""):
    catalog = Catalog("synthetic", "", ())
    cache = {
        "sales_key": compute._catalog_key(catalog),
        "sales_rulebook": book,
        "sales": (sales, {"tables": [], "sales_rows": len(sales)}),
    }
    return compute.answer_plan(cache, catalog, planned, book)


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
            "Trong tháng 5/2026 bữa nào doanh thu cao nhất?",
            "Tháng 5/2026 ngày mô doanh thu lên cao nhất?",
            "Tháng 5/2026 ngày mà có doanh thu cao nhất là ngày nào?",
        ):
            with self.subTest(text=text):
                planned = plan(text)
                self.assertEqual(planned, canonical)
                result = answer(planned, sales)
                self.assertEqual((result.type, result.answer), ("date", "2026-05-04"))
        book = rulebook() + INTENT_RULES["money"] + "\n"
        dialect = plan("Tháng 5/2026 bữa mô bán được nhiều tiền nhứt?", book=book)
        self.assertIsNotNone(dialect)
        self.assertEqual(answer(dialect, sales).answer, "2026-05-04")
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
            "Tháng 5/2026 khách mô mua nhiều đơn nhứt rứa?",
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

    def test_day_dialect_and_threshold_preface_preserve_count_subject(self):
        day = plan("Tháng 5/2026 có mấy bữa doanh thu từ 750k trở lên?")
        order = plan("Tôi định đặt ngưỡng thưởng, tháng 5/2026 có mấy đơn doanh thu từ "
                     "750k trở lên?")
        self.assertEqual((day.threshold.subject, order.threshold.subject), ("day", "order"))
        self.assertEqual(day.threshold.value, order.threshold.value)
        self.assertIsNone(plan("Tháng 5/2026 có bao nhiêu bữa tối doanh thu từ 750k trở lên?"))
        self.assertIsNone(plan("Tôi định đặt ngưỡng riêng cho khách lẻ, tháng 5/2026 có mấy "
                               "đơn doanh thu từ 750k trở lên?"))

    def test_money_shorthand_does_not_drop_ambiguous_units_or_conditions(self):
        question = "Tháng 5/2026 có mấy đơn có doanh thu từ {} trở lên?"
        for amount in ("1,5k", "1, 5k", "-500k", "500kg", "3tr75", "11 cũ"):
            with self.subTest(amount=amount):
                self.assertIsNone(plan(question.format(amount)))
        self.assertIsNone(plan(question.format("500k"), book=rulebook("threshold")))
        self.assertIsNone(plan("Tháng 5/2026 có mấy đơn doanh thu từ 500k trở lên, riêng kênh sàn?"))

    def test_million_shorthand_in_explicit_thresholds(self):
        for text, amount in (("3tr7", 3_700_000), ("11 củ", 11_000_000)):
            with self.subTest(text=text):
                result = plan(f"Tháng 5/2026 có mấy đơn có doanh thu từ {text} trở lên?")
                self.assertIsNotNone(result)
                self.assertEqual((result.threshold.op, result.threshold.value),
                                 (">=", Decimal(amount)))

    def test_group_product_rankings_keep_the_metric_group_and_unit(self):
        today = date(2026, 7, 15)
        day = date(2026, 5, 5)
        sales = [
            replace(sale("A", day, 100, product="P1", name="Trà", quantity=10, unit="ly"),
                    product_group="Đồ uống"),
            replace(sale("B", day, 300, product="P2", name="Cà phê", quantity=5, unit="ly"),
                    product_group="Đồ uống"),
            replace(sale("C", day, 90, product="P3", name="Nước suối", quantity=20, unit="chai"),
                    product_group="Đồ uống"),
            replace(sale("D", day, 999, product="P4", name="Sữa", quantity=30, unit="ly"),
                    product_group="Bánh"),
            replace(sale("E", day, 999, "cancelled", product="P2", name="Cà phê",
                         quantity=30, unit="ly"), product_group="Đồ uống"),
        ]

        def scoped(text, book=None):
            rules = read_rules(rulebook() + GROUP if book is None else book)
            return plan_question(Question("synthetic", "shop", text, today), rules,
                                 vocabulary={"product_group": ["Đồ uống", "Bánh"]})

        for phrasing, rank_by, unit, expected in (
            ("mặt hàng nào bán chạy nhất", "", "", "Nước suối"),
            ("mặt hàng nào có doanh thu cao nhất", "revenue", "", "Cà phê"),
            ("mặt hàng nào bán được nhiều ly nhất", "", "ly", "Trà"),
        ):
            with self.subTest(phrasing=phrasing):
                text = f"Trong nhóm Đồ uống tháng 5/2026 {phrasing}?"
                result = scoped(text)
                self.assertIsNotNone(result)
                self.assertEqual((result.intent, result.rank_by, result.unit),
                                 ("top_products", rank_by, unit))
                self.assertEqual(result.filters[0].value, "Đồ uống")
                self.assertEqual(answer(result, sales).answer, expected)
                self.assertEqual(scoped(f"Tháng 5/2026 nhóm Đồ uống thì {phrasing}?"), result)
                self.assertIsNone(scoped(text, rulebook("ties") + GROUP))

        text = "Trong nhóm Đồ uống tháng 5/2026 mặt hàng nào bán được nhiều ly nhất?"
        result = scoped(text)
        self.assertEqual(answer(result, [replace(sales[0], base_unit=None)] + sales[1:]).type,
                         "abstain")
        self.assertEqual(answer(result, [replace(sales[0], product_group=None)] + sales[1:]).type,
                         "abstain")
        self.assertIsNone(scoped(text, rulebook("unit") + GROUP))

    def test_trade_in_wording_still_needs_a_supported_money_measure(self):
        self.assertIsNone(plan("Tháng 5/2026 bình quân mỗi máy khách thực trả bao nhiêu tiền "
                               "sau khi trừ tiền thu máy cũ?"))
