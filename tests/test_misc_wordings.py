"""Đợt 3 gói 11: "≥ N" as the rulebook's "từ N trở lên", products in at least N orders.

Rulebook rows come from tests/test_sem02_grammar.py (synthetic paraphrases).
"""
import unittest
from decimal import Decimal

from test_sem02_grammar import plan, rulebook

from datamate.semantics.text import normalize_question


class AtLeastSymbolTests(unittest.TestCase):
    def test_symbol_becomes_tu_n_tro_len_with_its_unit(self):
        for raw, folded in (
                ("số đơn có doanh thu ≥ 580.000 đồng", "so don co doanh thu tu 580 000 dong tro len"),
                ("khách đã mua ≥ 4 đơn trong tháng", "khach da mua tu 4 don tro len trong thang"),
                ("có mặt ở ≥ 92 đơn hàng khác nhau", "co mat o tu 92 don hang khac nhau tro len"),
                ("co mat o >= 112 don khac nhau", "co mat o tu 112 don khac nhau tro len")):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_question(raw), folded)

    def test_thresholds_with_the_symbol(self):
        cases = {
            "Trong tháng 1 năm 2026, số đơn có doanh thu ≥ 580.000 đồng là bao nhiêu?":
                ("order", "revenue", 580000),
            "trong thang 3 nam 2026, so ngay co dthu ≥ 53.000.000 dong la bn":
                ("day", "revenue", 53000000),
            "Đếm số mặt hàng có mặt ở ≥ 92 đơn hàng khác nhau trong tháng 2/2026.":
                ("product", "order_count", 92),
            "đếm số khách đã mua ≥ 4 đơn trong tháng 4/2026": ("customer", "order_count", 4),
        }
        for text, (subject, measure, value) in cases.items():
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual(result.intent, "count_where")
                self.assertEqual((result.threshold.subject, result.threshold.measure,
                                  result.threshold.op, result.threshold.value),
                                 (subject, measure, ">=", Decimal(value)))

    def test_other_symbols_and_missing_rule_abstain(self):
        for text in ("số đơn có doanh thu ≤ 580.000 đồng tháng 1/2026",
                     "số đơn có doanh thu > 580.000 đồng tháng 1/2026",
                     "số đơn có doanh thu < 580.000 đồng tháng 1/2026"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))
        self.assertIsNone(plan("số đơn có doanh thu ≥ 580.000 đồng tháng 1/2026",
                               book=rulebook("threshold")))


class ProductsInOrdersTests(unittest.TestCase):
    def test_products_in_at_least_n_orders(self):
        for text, value in (
                ("check gium thang 3/2026 co bao nhieu mat hang xuat hien trong it nhat 123 don", 123),
                ("trong tháng 11/2025, bao nhiêu sp có mặt trong ít nhất 145 bill", 145),
                ("thang 2/2026 co bao nhieu mat hang xuat hien trong it nhat 107 don hang", 107),
                ("Trong tháng 7 năm 2026, số sản phẩm xuất hiện ở từ 140 đơn hàng khác nhau trở "
                 "lên là bao nhiêu?", 140),
                ("dem so mat hang co mat o tu 112 don khac nhau tro len trong thang 10/2025", 112)):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.threshold.subject,
                                  result.threshold.measure, result.threshold.value),
                                 ("count_where", "product", "order_count", Decimal(value)))

    def test_unclear_product_thresholds_abstain(self):
        for text in ("có bao nhiêu mặt hàng xuất hiện trong ít nhất 12 khách tháng 3/2026",
                     "có bao nhiêu mặt hàng xuất hiện trong nhiều nhất 12 đơn tháng 3/2026",
                     "xuat hien trong it nhat 12 don thang 3/2026"):  # no subject
            with self.subTest(text=text):
                self.assertIsNone(plan(text))


if __name__ == "__main__":
    unittest.main()
