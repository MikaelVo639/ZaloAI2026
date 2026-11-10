"""General Vietnamese wording, with synthetic questions and catalogue names."""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from test_compare_filters import ROWS as COMPARISON_ROWS
from test_new_intents import ROWS as ORDER_ROWS
from test_sem02_grammar import ROWS as THRESHOLD_ROWS
from test_semantics_periods import rulebook

from datamate.agent.baseline import BaselineAgent
from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question


class EverydayWordingTests(unittest.TestCase):
    def normalize(self, text, names=()):
        return normalize_question(text, product_names=names, as_of_year=2026)

    def plan(self, text, names=()):
        rules = read_rules(rulebook(cancelled=ORDER_ROWS["cancelled"],
                                    change=COMPARISON_ROWS["change"],
                                    threshold=THRESHOLD_ROWS["threshold"]))
        return plan_question(Question("synthetic", "shop", text, date(2026, 9, 18)),
                             rules, names)

    def test_money_spelling_keeps_the_threshold(self):
        for short, long in (
            ("650.000đ", "650 000 đồng"),
            ("14 triệu", "14 000 000 đồng"),
            ("14 triệu đồng", "14 000 000 đồng"),
        ):
            with self.subTest(short=short):
                text = "Tháng 8/2026 có bao nhiêu đơn doanh thu từ {} trở lên?"
                expected = self.plan(text.format(long))
                self.assertIsNotNone(expected)
                self.assertEqual(self.plan(text.format(short)), expected)
        for text in ("Sơn 3D", "Kem 5d", "14,5 triệu", "14, 5 triệu", "-14 triệu",
                     "từ -650.000đ trở lên", "14 triệu 500 nghìn"):
            with self.subTest(text=text):
                self.assertEqual(self.normalize(text), normalize_question(text))

    def test_store_subject_keeps_clothing_and_catalogue_names(self):
        expected = self.plan("Hôm qua cửa hàng bán được bao nhiêu đơn?")
        self.assertIsNotNone(expected)
        self.assertEqual(self.plan("Hôm qua quán bán được bao nhiêu đơn?"), expected)
        self.assertEqual(self.plan("hom qua quan ban duoc bao nhieu don", ["Nước mía"]), expected)
        self.assertIsNone(self.plan("Hôm qua quần bán được bao nhiêu đơn?"))
        self.assertIsNone(self.plan("hom qua quan ban duoc bao nhieu don", ["Quần jean nam"]))
        self.assertEqual(self.normalize("Của quán"), "cua cua hang")

    def test_agent_loads_catalogue_before_rewriting_store_subject(self):
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        context = SimpleNamespace(cache={}, split=SimpleNamespace(rulebook=rulebook()))
        question = Question("synthetic", "shop", "Hôm qua quán bán được bao nhiêu đơn?",
                            date(2026, 9, 18))
        with patch.object(agent, "_product_names", return_value=["Quần jean nam"]) as names:
            with patch.object(agent, "_vocabulary", return_value={}):
                self.assertIsNone(agent._plan(question, context))
        names.assert_called_once_with(context)

    def test_colloquial_order_count(self):
        expected = self.plan("Hôm qua bán được bao nhiêu đơn?")
        self.assertIsNotNone(expected)
        for text in ("Hôm qua chốt được bao nhiêu đơn?", "Hôm qua lên được mấy đơn?",
                     "Hqua tổng bao nhiêu đh?"):
            self.assertEqual(self.plan(text), expected)
        for text in ("Giá lên được bao nhiêu", "Tổng cộng", "Tổng số", "Tổng doanh thu"):
            self.assertEqual(self.normalize(text), normalize_question(text))

    def test_comparisons_preserve_reference_month(self):
        text = "Doanh thu tháng 8/2026 {} thay đổi bao nhiêu %?"
        expected = self.plan(text.format("so với tháng liền trước"))
        self.assertIsNotNone(expected)
        self.assertEqual(self.plan(text.format("so tháng liền trước")), expected)
        expected = self.plan(text.format("so với cùng tháng năm trước"))
        self.assertIsNotNone(expected)
        self.assertEqual(self.plan(text.format("so với tháng 8 năm ngoái")), expected)
        self.assertIsNone(self.plan(text.format("so với tháng 7 năm ngoái")))
        historical = "Doanh thu tháng 8/2025 so với tháng 8 năm ngoái thay đổi bao nhiêu %?"
        self.assertIsNone(self.plan(historical))
        for text in ("Số tháng có doanh thu trên 1 tỷ", "Có bao nhiêu số ca",
                     "Top 3 số tháng có doanh thu cao nhất"):
            self.assertEqual(self.normalize(text), normalize_question(text))

    def test_period_words_keep_dates_and_shifts(self):
        for canonical, variant in (
            ("Trong quý 2/2025 cửa hàng bán được bao nhiêu đơn?",
             "Chong quý 2/2025 cửa hàng bán được bao nhiêu đơn?"),
            ("Trong quý 2/2025 cửa hàng bán được bao nhiêu đơn?",
             "Trog quý 2/2025 cửa hàng bán được bao nhiêu đơn?"),
            ("Từ đầu năm đến hôm nay doanh thu là bao nhiêu?",
             "Từ đầu năm tới chừ doanh thu là bao nhiêu?"),
            ("Từ đầu năm đến hôm nay doanh thu là bao nhiêu?",
             "Từ đầu năm tới giờ doanh thu là bao nhiêu?"),
            ("Quý 2/2025 cửa hàng bán được bao nhiêu tiền?",
             "Cả quý 2/2025 cửa hàng bán được bao nhiêu tiền?"),
            ("Tháng 5/2025 có bao nhiêu đơn bị hủy?",
             "Hồi T5/2025 có bao nhiêu đơn bị hủy?"),
        ):
            with self.subTest(variant=variant):
                expected = self.plan(canonical)
                self.assertIsNotNone(expected)
                self.assertEqual(self.plan(variant), expected)
        for text in ("Nồi chống dính", "Tới giờ đóng cửa", "Tới giờ 5",
                     "Ca ngày tháng 3 doanh thu bao nhiêu", "Ca ngày hôm qua"):
            self.assertEqual(self.normalize(text), normalize_question(text))

    def test_revenue_wording_preserves_superlatives_and_product_names(self):
        expected = self.plan("Doanh thu hôm qua là bao nhiêu?")
        self.assertIsNotNone(expected)
        self.assertEqual(self.plan("Hôm qua tiệm đạt doanh thu bao nhiêu?"), expected)
        self.assertEqual(self.plan("Hôm qua doanh thu đạt bao nhiêu?"), expected)
        for text in ("Ngày nào bán đắt nhất", "Thu về", "Đạt doanh thu"):
            names = [text] if text == "Đạt doanh thu" else []
            self.assertEqual(self.normalize(text, names), normalize_question(text))

    def test_closing_particles_preserve_units_and_filters(self):
        for canonical, variant in (
            ("Hôm qua doanh thu bao nhiêu?", "Hôm qua doanh thu bao nhiêu thế?"),
            ("Hôm qua doanh thu bao nhiêu?", "Hôm qua doanh thu bao nhiêu hè?"),
            ("Hôm qua có mấy đơn?", "Hôm qua có mấy đơn rứa?"),
            ("Hôm qua có mấy đơn?", "Hôm qua có mấy đơn rồi ta?"),
        ):
            with self.subTest(variant=variant):
                expected = self.plan(canonical)
                self.assertIsNotNone(expected)
                self.assertEqual(self.plan(variant), expected)
        for text in ("Là gì thế", "Bao nhiêu % thế"):
            self.assertEqual(self.normalize(text), normalize_question(text.removesuffix(" thế")))
        for text in ("Doanh thu đơn trả bằng thẻ", "Bán được bao nhiêu thẻ",
                     "Doanh thu mùa hè", "Doanh thu nước rửa", "Bán được bao nhiêu tạ",
                     "Bán đc mấy gói", "Bán bao nhiêu thế"):
            self.assertEqual(self.normalize(text), normalize_question(text))
