"""Đợt 3 gói 11: "tháng 10/2025 hàng nào chạy nhất" — the year is not the top-k count.

Rulebook rows come from tests/test_sem02_grammar.py (synthetic paraphrases).
"""
import unittest

from test_sem02_grammar import plan

from datamate.semantics.text import normalize_question


class BestSellerWordingTests(unittest.TestCase):
    def test_month_before_the_question_is_not_a_count(self):
        for text, k in (("thang 6/2026 hang nao chay nhat", 1),
                        ("Tháng 1/2026 mặt hàng nào bán chạy nhất?", 1),
                        ("tháng 12/2025 top 5 hàng chạy nhất là chi", 5),
                        ("Tháng 3/2026 top 3 hàng bán chạy nhất là gì?", 3)):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.top_k), ("top_products", k))
                self.assertEqual(result.start.day, 1)

    def test_central_la_chi_only_at_the_end(self):
        self.assertEqual(normalize_question("top 5 hàng chạy nhất là chi"),
                         "top 5 hang chay nhat la gi")
        self.assertIn("la chi nhanh", normalize_question("doanh thu la chi nhanh 2"))

    def test_counts_out_of_range_still_abstain(self):
        self.assertIsNone(plan("tháng 3/2026 top 500 hàng chạy nhất"))


if __name__ == "__main__":
    unittest.main()
