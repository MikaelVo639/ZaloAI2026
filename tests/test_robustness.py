"""Robustness splits: rewrites keep numbers, dates and names; labels stay outside the split."""

import json
import tempfile
import unittest
from pathlib import Path

from datamate.evaluation.robustness import PERTURBATIONS, build_splits, perturb


class PerturbationTests(unittest.TestCase):
    def test_each_rewrite_keeps_the_numbers(self):
        question = "Doanh thu tháng 3/2025 của cửa hàng tăng hay giảm bao nhiêu % so với tháng trước?"
        for name in PERTURBATIONS:
            with self.subTest(name=name):
                rewritten = perturb(name, question, f"{name}:q1")
                self.assertIsNotNone(rewritten)
                self.assertNotEqual(rewritten, question)
                year = "/25" if name == "short_year" else "2025"
                for number in ("3", year):
                    self.assertIn(number, rewritten)

    def test_short_year_and_chat_style(self):
        self.assertRegex(perturb("short_year", "Doanh thu tháng 3/2025?", "k"),
                         r"^Doanh thu (th 3/25|3/25|T3/25)\?$")
        self.assertIsNone(perturb("short_year", "Doanh thu tháng 3/2019?", "k"))
        rewritten = perturb("chat_style", "Cho mình hỏi doanh thu của cửa hàng?", "k")
        self.assertRegex(rewritten, r"^(em ơi|ad ơi|shop ơi|ủa) cho mik hỏi doanh thu kua "
                                    r"cửa hàng rồi\?$")

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
