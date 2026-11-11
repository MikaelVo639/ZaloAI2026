"""Rule reading must survive rewordings of the same rulebook and stop on meaning changes.

Uses the synthetic test rulebook only; scripts/rulebook_robustness.py runs the same report
on a local TRAIN rulebook.
"""

import unittest

from test_s01_grammar import rulebook

from datamate.evaluation.rulebook_robustness import (
    CHANGING,
    PRESERVING,
    compare,
    enabled,
    perturb_rulebook,
    source_rules,
)
from datamate.semantics.rules import canonical, read_rules
from datamate.semantics.text import fold

# Exact rows (debt definitions, answer formats) stay strict: an added sentence may add an
# exception, so they are deliberately not read once a note is appended.
STRICT_ROWS = {"boolean_answer", "calendar_month", "debt_at_date", "debt_by_customer",
               "debt_excluded", "debt_orders", "table_answer"}

# Synthetic paraphrases of the rows the sales loaders read themselves (not organizer data).
LOADER_ROWS = "\n".join((
    "| Mục | Quy ước |",
    "|---|---|",
    "| Trạng thái đơn | Mỗi đơn có đúng một trạng thái: hoàn thành, huỷ, trả hàng, đang giao |",
    "| Đơn tính vào doanh thu | Đơn không huỷ và không trả hàng; trả một phần cũng bỏ cả đơn. "
    "Đơn đang giao vẫn tính |",
    "| Doanh thu một đơn | Cộng thành tiền các dòng hàng, đã trừ giảm giá từng dòng |",
    "| Bán chạy | Theo số lượng quy về đơn vị cơ bản |",
    "| Mặt hàng | Phí phục vụ không xếp hạng bán chạy nhưng vẫn tính vào doanh thu |",
    "| Hai dòng của cùng một đơn giống nhau ở mọi ô | Chỉ tính một lần |",
    "| Ô để trống ở các dòng sau của cùng một nhóm dòng | Giống ô đó ở dòng đầu nhóm |",
))
LOADERS = {"source:order_states", "source:joined_sales", "source:readme_sales",
           "source:readme_blank_cells_inherit", "source:readme_duplicate_rows_once",
           "source:readme_whole_order_returns", "source:readme_service_fee"}


class RulebookRobustnessTests(unittest.TestCase):
    def test_loader_rows_survive_rewording_and_stop_on_negation(self):
        self.assertLessEqual(LOADERS, source_rules(LOADER_ROWS))
        for name in ("fillers", "key_wording", "synonyms", "notes", "row_order"):
            with self.subTest(perturbation=name):
                self.assertLessEqual(LOADERS, source_rules(perturb_rulebook(name, LOADER_ROWS)))
        flipped = source_rules(perturb_rulebook("negation_flip", LOADER_ROWS))
        self.assertNotIn("source:order_states", flipped)
        self.assertNotIn("source:joined_sales", flipped)

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
        self.assertEqual(enabled(read_rules(rulebook(extra=extra))),
                         enabled(read_rules(rulebook())))

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
