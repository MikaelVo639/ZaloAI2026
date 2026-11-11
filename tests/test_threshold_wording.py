"""Everyday wording of thresholds, customer shares, cancel rates and customer ranking.

Questions and rulebook rows below are synthetic, written for tests.
"""
import unittest
from datetime import date

from test_new_intents import ROWS as INTENT_ROWS
from test_sem02_grammar import rulebook
from test_semantics_periods import GOODS
from test_semantics_periods import ROWS as PERIOD_ROWS

from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question

BOOK = (rulebook().replace("không gồm phí giao hàng |", GOODS) + "\n".join((
    PERIOD_ROWS["money"], INTENT_ROWS["cancelled"], INTENT_ROWS["rate"])) + "\n")


def plan(text, today=date(2026, 7, 22)):
    return plan_question(Question("x", "b", text, today), read_rules(BOOK))


class ThresholdWordingTests(unittest.TestCase):
    def assertSamePlan(self, canonical, *variants):
        expected = plan(canonical)
        self.assertIsNotNone(expected, canonical)
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(plan(variant), expected)

    def test_money_thresholds(self):
        self.assertSamePlan(
            "Tháng 8/2025 có mấy đơn có doanh thu từ 180.000 đồng trở lên?",
            "8/2025 đếm giùm mấy đh dthu từ 180.000đ trở lên",
            "Trong th8/2025, đơn có doanh thu từ 180.000đ trở lên đếm được mấy đơn vậy?",
            "Đếm giùm t8/2025 có mấy đơng doanh thu từ 180.000đ trở lên nha",
        )
        self.assertSamePlan(
            "Đếm số ngày trong tháng 4/2026 mà doanh thu cả ngày từ 40.000.000 đồng trở lên",
            "Thang 4/2026 dem giup so ngay dthu tu 40.000.000 dong tro len?",
        )
        self.assertSamePlan(
            "Tháng 4/2026 có bao nhiêu ngày cửa hàng bán được từ 40.000.000 đồng trở lên?",
            "thang 4/2026 bn ngay cua hang ban dc tu 40tr tro len z nha e",
        )
        # The subject asked twice must be the same thing.
        self.assertIsNone(plan("Trong th8/2025, đơn có doanh thu từ 180.000đ trở lên đếm được mấy ngày?"))
        # "40tr" is read as millions only in a lower threshold.
        self.assertEqual(normalize_question("Bán 40tr", product_names=(), as_of_year=2026),
                         normalize_question("Bán 40tr"))

    def test_products_in_at_least_n_orders(self):
        self.assertSamePlan(
            "Tháng 5/2026 có bao nhiêu mặt hàng xuất hiện ở 152 đơn khác nhau trở lên?",
            "Th5/2026 có bnhiu món góp mặt trong ít nhất 152 đơn?",
            "Tháng 5/2026 có mấy mặt hàng được gọi chong ít nhất 152 đơn?",
            "T5/2026 co may mat hang nam trong it nhat 152 don khac nhau?",
            "Th5/2026 có bao nhiêu mặt hàng có mặt trong từ 152 đơn khác nhau trở lên?",
        )

    def test_customer_counts_and_shares(self):
        self.assertSamePlan(
            "Tháng 1/2026 có mấy khách mua từ 2 đơn trở lên?",
            "T1/2026 khách mua từ 2 đơn trở lên là mấy người z?",
            "Khach mua tu 2 don tro len trong thang 1/2026, dem duoc bao nhieu nguoi?",
            "Mình muốn tặng quà khách quen, đếm giúp số khách mua từ 2 đơn trở lên trong th1/2026.",
        )
        self.assertSamePlan(
            "Trong số khách đã mua tháng 6/2025, bao nhiêu % mua từ 3 đơn trở lên?",
            "Trong khách mua th6/2025, mấy % mua từ 3 đh trở lên?",
            "Khách có mua th6/2025, mấy % mua từ 3 đơn trở lên?",
            "Khách mua từ 3 đh trở lên th6/2025 chiếm bnhiu % tất cả khách đã mua tháng đó?",
            "Th6/2025 khách có từ 3 đơn trở lên chiếm mấy % tổng số khách mua?",
            "T6/2025 bao nhieu % khach mua tu 3 don tro len, tinh tren so khach co mua?",
        )
        # A share needs the customers who bought as its denominator.
        self.assertIsNone(plan("Tháng 6/2025 khách mua từ 3 đơn trở lên chiếm mấy % doanh thu?"))

    def test_cancel_rate(self):
        self.assertSamePlan(
            "Tỉ lệ huỷ đơn tháng 7/2025 là bao nhiêu?",
            "Th7/2025 đh bị huỹ chiếm bnhiu %?",
            "T7/2025 don huy chiem bao nhieu phan tram?",
            "Tháng 7/2025 tiệm bị huỷ mấy % đơn rứa?",
            "Tháng 7/2025 đơn huỷ chiếm nhiêu % toàn bộ đơn z?",
            "Tỉ nệ huỷ đơn th7/2025 bnhiu %?",
        )
        self.assertIsNone(plan("Tháng 7/2025 đơn huỷ chiếm mấy % doanh thu?"))
        self.assertIsNone(plan("Tháng 7/2025 có mấy đơn bị huỷ, chiếm bao nhiêu %?"))

    def test_top_customer_by_total_spent(self):
        self.assertSamePlan(
            "Tháng 8/2025 khách nào mua nhiều tiền nhất?",
            "chị ơi mik mún bít xíu, trog th8/2025 khach nao tong tien mua hang cao nhut, thanks e nha",
        )

    def test_misspellings_only_where_they_are_not_words(self):
        for text in ("Đơn giá", "Tỉ lệ", "Nệm", "nha em"):
            self.assertEqual(normalize_question(text), normalize_question(text))
        self.assertEqual(normalize_question("Tỉ nệ"), "ti le")
        self.assertEqual(normalize_question("đơng"), "don")
        self.assertEqual(normalize_question("Vitamin E"), "vitamin e")


if __name__ == "__main__":
    unittest.main()
