"""Rule reading must survive rewordings of the same rulebook and stop on meaning changes.

Uses the synthetic test rulebook only; scripts/rulebook_robustness.py runs the same report
on a local TRAIN rulebook.
"""

import unittest

from test_s01_grammar import rulebook

from datamate.evaluation.rulebook_robustness import (
    CHANGING, PRESERVING, compare, enabled, perturb_rulebook,
)
from datamate.semantics.rules import canonical, read_rules
from datamate.semantics.text import fold

# Exact rows (debt definitions, answer formats) stay strict: an added sentence may add an
# exception, so they are deliberately not read once a note is appended.
STRICT_ROWS = {"boolean_answer", "calendar_month", "debt_at_date", "debt_by_customer",
               "debt_excluded", "debt_orders", "table_answer"}


class RulebookRobustnessTests(unittest.TestCase):
    def test_meaning_preserving_rewrites_keep_every_rule(self):
        report = compare(rulebook(), tuple(PRESERVING))
        for name in PRESERVING:
            with self.subTest(perturbation=name):
                self.assertEqual(report[name]["gained"], [])
                allowed = STRICT_ROWS if name == "notes" else set()
                self.assertLessEqual(set(report[name]["lost"]), allowed)

    def test_flipped_negation_disables_the_revenue_rule(self):
        report = compare(rulebook(), tuple(CHANGING))
        self.assertIn("revenue", report["negation_flip"]["lost"])
        self.assertIn("debt_orders", report["negation_flip"]["lost"])

    def test_extended_key_and_filler_words_still_read_core_rows(self):
        book = (rulebook()
                .replace("| Đơn tính vào doanh thu |", "| Đơn được tính vào doanh thu (quy ước) |")
                .replace("không cộng thuế VAT", "không hề cộng thuế VAT"))
        rules = read_rules(book)
        self.assertTrue(rules.revenue)
        self.assertIn("Đơn được tính vào doanh thu (quy ước)", rules.source_rows[0])

    def test_shortest_matching_key_is_chosen(self):
        extra = "| Tuần trước nữa | không dùng trong bộ này |"
        self.assertEqual(enabled(read_rules(rulebook(extra=extra))), enabled(read_rules(rulebook())))

    def test_canonical_only_drops_fillers_and_merges_spellings(self):
        self.assertEqual(canonical(fold("Không hề cộng thuế, tất cả các hoá đơn của khách hàng")),
                         "khong cong thue don khach")
        self.assertEqual(canonical(fold("không cộng")), "khong cong")
        self.assertNotEqual(canonical(fold("có cộng thuế")), canonical(fold("không cộng thuế")))

    def test_perturbations_change_the_text(self):
        for name in (*PRESERVING, *CHANGING):
            if name == "header":
                continue  # the synthetic rulebook has no table header row
            with self.subTest(perturbation=name):
                self.assertNotEqual(perturb_rulebook(name, rulebook()), rulebook())


if __name__ == "__main__":
    unittest.main()
