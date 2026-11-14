"""Robustness splits: rewrites keep numbers, dates and names; labels stay outside the split."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from datamate.evaluation.robustness import (
    MODEL_PERTURBATIONS, PERTURBATIONS, build_splits, longest_shared_run, perturb,
    strip_diacritics,
)
from datamate.semantics.text import fold


class PerturbationTests(unittest.TestCase):
    def test_each_rewrite_keeps_the_numbers(self):
        question = "Doanh thu tháng 3/2025 của cửa hàng tăng hay giảm bao nhiêu % so với tháng trước?"
        for name in PERTURBATIONS:
            if name == "clause_order":
                continue  # multiple periods are deliberately ineligible
            with self.subTest(name=name):
                rewritten = perturb(name, question, f"{name}:q1")
                self.assertIsNotNone(rewritten)
                self.assertNotEqual(rewritten, question)
                year = "/25" if name == "short_year" else "2025"
                for number in ("3", year):
                    self.assertIn(number, rewritten)

    def test_model_variants_keep_period_amount_and_exact_product_name(self):
        question = "Tháng 4/2026 doanh thu của Trà đồi Sương 12 gói là bao nhiêu?"
        for name in MODEL_PERTURBATIONS:
            with self.subTest(name=name):
                changed = perturb(name, question, "synthetic")
                self.assertIsNotNone(changed)
                self.assertIn("Trà đồi Sương 12 gói", changed)
                self.assertRegex(changed, r"[Tt]háng 4/2026")
                self.assertCountEqual(re.findall(r"\d+", question), re.findall(r"\d+", changed))
                self.assertEqual(changed, perturb(name, question, "synthetic"))

    def test_clause_order_leaves_multiple_periods_alone(self):
        comparison = "Tháng 4/2026 doanh thu tăng bao nhiêu so với tháng 3/2026?"
        sequence = "Tháng 4/2026 và tháng 5/2026 doanh thu lần lượt là bao nhiêu?"
        for question in (comparison, sequence):
            self.assertIsNone(perturb("clause_order", question, "synthetic"))
        self.assertEqual(perturb("clause_order", "Doanh thu trong tháng 4/2026?", "synthetic"),
                         "tháng 4/2026, Doanh thu?")

    def test_metric_wording_keeps_denominator_scope_and_ranking_direction(self):
        for original, expected in (
            ("Trong tháng 2/2026 có mấy đơn hàng trả bằng thẻ?",
             "Trong tháng 2/2026 ghi nhận mấy đơn hàng trả bằng thẻ?"),
            ("Giá trị trung bình mỗi đơn trong quý IV/2025 là bao nhiêu?",
             "giá trị bình quân tính trên mỗi đơn trong quý IV/2025 là bao nhiêu?"),
            ("3 mặt hàng bán chạy nhất tháng 2/2026 là gì?",
             "3 mặt hàng có số lượng bán ra cao nhất tháng 2/2026 là gì?"),
            ("Tỷ lệ đơn huỷ tuần trước là bao nhiêu?",
             "phần trăm đơn bị hủy tuần trước là bao nhiêu?"),
        ):
            with self.subTest(original=original):
                self.assertEqual(perturb("metric_wording", original, "synthetic"), expected)

    def test_short_year_and_chat_style(self):
        self.assertRegex(perturb("short_year", "Doanh thu tháng 3/2025?", "k"),
                         r"^Doanh thu (th 3/25|3/25|T3/25)\?$")
        self.assertIsNone(perturb("short_year", "Doanh thu tháng 3/2019?", "k"))
        rewritten = perturb("chat_style", "Cho mình hỏi doanh thu của cửa hàng?", "k")
        self.assertRegex(rewritten, r"^(em ơi|ad ơi|shop ơi|ủa) cho mik hỏi doanh thu kua "
                                    r"cửa hàng rồi\?$")

    def test_store_word_greeting_and_chat_typos(self):
        self.assertRegex(perturb("store_word", "Tháng này cửa hàng bán được bao nhiêu?", "k"),
                         r"^Tháng này (quán mình|tiệm mình|bên mình|shop mình|quán|tiệm) bán")
        self.assertRegex(perturb("store_word", "Doanh thu tháng 3/2026?", "k"),
                         r"^Doanh thu \S+( \S+)? tháng 3/2026\?$")
        rewritten = perturb("greeting", "Doanh thu tháng 3/2026?", "k")
        self.assertRegex(rewritten, r"^(Alo|Cho em hỏi|Chị ơi cho em hỏi|Hello shop|Dạ cho mình "
                                    r"hỏi) doanh thu tháng 3/2026.*\?$")
        typos = perturb("chat_typos", "Doanh thu tháng 3/2026 cao nhất là bao nhiêu?", "k")
        self.assertNotEqual(typos, "Doanh thu tháng 3/2026 cao nhất là bao nhiêu?")
        self.assertIn("3/2026", typos)
        self.assertIsNone(perturb("chat_typos", "Top 3 khách?", "k"))
        # "3 tháng 4/2026, 5/2026": "tháng" counts the months and stays.
        self.assertIsNone(perturb("month_abbreviation", "3 tháng 4/2026, 5/2026", "k"))

    def test_month_abbreviation_styles(self):
        rewritten = perturb("month_abbreviation", "Doanh thu tháng 8 năm 2025?", "k")
        self.assertRegex(rewritten, r"^Doanh thu (T8/2025|th8/2025|thg 8/2025|8-2025)\?$")
        self.assertIsNone(perturb("month_abbreviation", "Doanh thu tuần này?", "k"))

    def test_particle_goes_before_the_question_mark(self):
        rewritten = perturb("particle", "Doanh thu hôm nay là bao nhiêu?", "k")
        self.assertRegex(rewritten, r"^Doanh thu hôm nay là bao nhiêu \S+\?$")

    def test_transposition_swaps_letters_inside_a_grammar_word(self):
        self.assertEqual(perturb("transposition", "bán chạy nhất", "k"), "ban chay nhta")
        self.assertIsNone(perturb("transposition", "Có mấy đơn?", "k"))

    def test_not_applicable_is_none(self):
        self.assertIsNone(perturb("unaccented", "doanh thu thang nay", "k"))
        self.assertIsNone(perturb("synonym", "Có bao nhiêu đơn?", "k"))


class ReportSuiteTests(unittest.TestCase):
    """Held-out model benchmark wordings restructure the question instead of quoting it."""

    def assertRestructured(self, original, changed, *, keep=()):
        self.assertIsNotNone(changed)
        self.assertNotIn("«", changed)
        self.assertCountEqual(re.findall(r"\d+", original), re.findall(r"\d+", changed))
        for text in keep:
            self.assertIn(text, changed)
        run = longest_shared_run(original, changed)
        self.assertFalse(run >= 8 and 4 * run >= 3 * len(fold(original).split()))

    def test_yes_no_wording_asks_whether_then_for_the_figure(self):
        original = "Tháng 4/2026 doanh thu của Trà đồi Sương 12 gói là bao nhiêu?"
        for key in ("a", "b", "c"):
            changed = perturb("yes_no_wording", original, key)
            self.assertRestructured(original, changed, keep=("Trà đồi Sương 12 gói",))
            self.assertTrue(changed.startswith("Doanh thu của Trà đồi Sương 12 gói tháng 4/2026 có"))
            self.assertRegex(changed, r"(?:không|được)\b.*bao nhiêu")
        self.assertEqual(
            perturb("yes_no_wording", "Xem giúp quý 3/2025 cửa hàng có bao nhiêu đơn hàng?", "a"),
            "Quý 3/2025, cửa hàng có đơn hàng nào không? Nếu có thì tổng cộng bao nhiêu?")

    def test_yes_no_wording_keeps_the_unit_and_the_spelling(self):
        changed = perturb("yes_no_wording", "thang 4/2025 nhap hang het bao nhiu tien", "k")
        self.assertRestructured("thang 4/2025 nhap hang het bao nhiu tien", changed)
        self.assertTrue(changed.endswith("bao nhiu tien?"))
        self.assertEqual(changed, strip_diacritics(changed))

    def test_yes_no_wording_skips_a_verb_left_as_subject_and_shares(self):
        for question in ("Tháng 5/2025 bị huỷ mấy đơn vậy?",
                         "Tháng 7/2026 cửa hàng có bao nhiêu % đơn bị huỷ?",
                         "Doanh thu tháng 4/2026 tăng bao nhiêu so với tháng 3/2026?"):
            with self.subTest(question=question):
                self.assertIsNone(perturb("yes_no_wording", question, "k"))

    def test_condition_last_moves_the_whole_filter_phrase(self):
        cases = {
            "Tháng 1/2026 nhân viên mã NV003 bán được bao nhiêu tiền?":
                ("Tháng 1/2026 bán được bao nhiêu tiền,", "nhân viên mã NV003"),
            "Tháng 5/2026 có bao nhiêu đơn bị huỷ?": ("Tháng 5/2026 có bao nhiêu đơn,", "hủy"),
            "Doanh thu từ khách nhóm thân thiết tháng 4/2026 là bao nhiêu?":
                ("Doanh thu tháng 4/2026 là bao nhiêu,", "khách nhóm thân thiết"),
            "Tuần trước riêng ca chiều bán được tổng cộng bao nhiêu tiền?":
                ("Tuần trước bán được tổng cộng bao nhiêu tiền,", "ca chiều"),
        }
        for original, (main, condition) in cases.items():
            with self.subTest(original=original):
                changed = perturb("condition_last", original, original)
                self.assertRestructured(original, changed, keep=(condition,))
                self.assertTrue(changed.startswith(main), changed)
                self.assertGreater(changed.index(condition), len(main))

    def test_condition_last_leaves_shares_and_compound_filters(self):
        for question in (
                "Trong tổng doanh thu tháng 5/2026, phần của khách nhóm thân thiết là mấy phần trăm?",
                "thang 10/2025 cua hang co bn % don bi huy",
                "Tháng 4/2026, doanh thu các đơn trả bằng COD và các đơn trả bằng ví điện tử "
                "mỗi loại bao nhiêu ạ?"):
            with self.subTest(question=question):
                self.assertIsNone(perturb("condition_last", question, "k"))

    def test_indirect_wording_moves_the_period_to_the_end(self):
        original = "Doanh thu của Tôn lạnh Hoa Sen 0.4mm trong tháng 7 năm 2025 là bao nhiêu?"
        changed = perturb("indirect_wording", original, "k")
        self.assertRestructured(original, changed, keep=("Tôn lạnh Hoa Sen 0.4mm",))
        self.assertTrue(changed.endswith(", tính cho tháng 7 năm 2025?"))
        self.assertNotIn("doanh thu", changed.lower())


class BuildSplitsTests(unittest.TestCase):
    def test_split_holds_only_changed_questions_and_no_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            train = Path(directory) / "train"
            (train / "data" / "b1").mkdir(parents=True)
            (train / "data" / "b1" / "README.md").write_text("x", encoding="utf-8")
            (train / "rulebook.md").write_text("r", encoding="utf-8")
            (train / "calendar.csv").write_text("c", encoding="utf-8")
            questions = [
                {"id": "q1", "business_id": "b1", "question": "Doanh thu tháng 3/2025?",
                 "as_of_date": "2026-01-01"},
                {"id": "q2", "business_id": "b1", "question": "Có bao nhiêu đơn?",
                 "as_of_date": "2026-01-01"},
            ]
            (train / "questions.jsonl").write_text(
                "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions),
                encoding="utf-8")
            (train / "answers.jsonl").write_text(
                '{"id": "q1", "type": "number", "answer": 1}\n'
                '{"id": "q2", "type": "number", "answer": 2}\n', encoding="utf-8")
            output = Path(directory) / "out"
            report = build_splits(train, output, ("month_abbreviation",))
            self.assertEqual(report, {"month_abbreviation": {"questions": 1}})
            split = output / "month_abbreviation"
            self.assertFalse((split / "answers.jsonl").exists())
            self.assertTrue((split / "data" / "b1" / "README.md").is_file())
            rewritten = json.loads((split / "questions.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(rewritten["id"], "q1")
            labels = (output / "month_abbreviation.answers.jsonl").read_text(encoding="utf-8")
            self.assertEqual(json.loads(labels)["id"], "q1")
            with self.assertRaises(ValueError):
                build_splits(train, output, ("month_abbreviation",))


if __name__ == "__main__":
    unittest.main()
