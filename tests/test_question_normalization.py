"""Question wording that means the same as train wording: abbreviations, typos, particles.

Each rule is a general Vietnamese typing convention, checked on rewritten TRAIN questions
(scripts/robustness_eval.py); none comes from public or private questions.
"""
import unittest
from datetime import date

from test_semantics_periods import rulebook

from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question


class NormalizeTests(unittest.TestCase):
    def test_month_and_quarter_abbreviations(self):
        for text in ("T3/2025", "t3/2025", "th3/2025", "th 3/2025", "thg 3/2025", "3-2025",
                     "tháng 3-2025", "3/2025"):
            with self.subTest(text=text):
                self.assertEqual(normalize_question(f"doanh thu {text}"),
                                 "doanh thu thang 3/2025")
        self.assertEqual(normalize_question("Q2/2026 doanh thu"), "quy 2/2026 doanh thu")
        self.assertEqual(normalize_question("doanh thu ngày 15-03-2026"),
                         "doanh thu ngay 15/03/2026")

    def test_two_digit_years(self):
        for text in ("th 3/25", "T3/25", "tháng 3/25", "3/25"):
            with self.subTest(text=text):
                self.assertEqual(normalize_question(f"doanh thu {text}"),
                                 "doanh thu thang 3/2025")
        self.assertEqual(normalize_question("từ 12/25 đến 2/26"),
                         "tu thang 12/2025 den thang 2/2026")
        # Day/month and dates are not years: "1/3", "15/3", "15/3/2026".
        for text in ("ngày 1/3", "15/3", "ngày 15/3/2026", "13/25"):
            with self.subTest(text=text):
                self.assertNotIn("thang", normalize_question(text))

    def test_chat_spellings_and_calls(self):
        self.assertEqual(normalize_question("em ơi check jum dthu kua shop mik"),
                         "check giup dthu cua cua hang")
        for word in ("giùm", "dùm", "jum", "giúp"):
            with self.subTest(word=word):
                self.assertEqual(normalize_question(f"xem {word} doanh thu"),
                                 "xem giup doanh thu")
        self.assertEqual(normalize_question("ad oi hom qua co bnhiu dh roi"),
                         "hom qua co bnhiu don hang")
        self.assertEqual(normalize_question("ủa tuần rồi"), "tuan truoc")
        self.assertEqual(normalize_question("hôm nay tiệm bán được bao nhiêu"),
                         "hom nay cua hang ban duoc bao nhieu")
        # "bán" at the start is not a call; names ending in "đỏ", "lươn" keep their word.
        self.assertEqual(normalize_question("bán được bao nhiêu"), "ban duoc bao nhieu")
        self.assertEqual(normalize_question("doanh thu áo đỏ"), "doanh thu ao do")
        self.assertEqual(normalize_question("doanh thu lẩu lươn"), "doanh thu lau luon")

    def test_bare_month_rewrite_keeps_dates_quarters_and_lists(self):
        self.assertEqual(normalize_question("ngày 15/3/2026"), "ngay 15/3/2026")
        self.assertEqual(normalize_question("doanh thu 15/3/2026"), "doanh thu 15/3/2026")
        self.assertEqual(normalize_question("quý 3/2025"), "quy 3/2025")
        self.assertEqual(normalize_question("tháng 1, 2 và 3/2026"), "thang 1 2 va 3/2026")
        # "T3" alone may be Tuesday ("thứ 3"): only a month when the year is written.
        self.assertEqual(normalize_question("doanh thu T3"), "doanh thu t3")

    def test_transposed_letters_in_long_grammar_words(self):
        self.assertEqual(normalize_question("donah thu cao nhta bao nheiu thnag"),
                         "doanh thu cao nhat bao nhieu thang")
        # Short words and names are left alone; "tuna" is not turned into "tuần".
        self.assertEqual(normalize_question("cá tuna, bút bi"), "ca tuna but bi")

    def test_closing_particles_only_at_the_end(self):
        for particle in ("vậy", "z", "ạ", "hả", "nhỉ", "nha", "dậy", "nè"):
            with self.subTest(particle=particle):
                self.assertEqual(normalize_question(f"doanh thu hôm nay {particle}?"),
                                 "doanh thu hom nay")
        self.assertEqual(normalize_question("dây sạc bán bao nhiêu"),
                         "day sac ban bao nhieu")

    def test_dialect_and_synonyms(self):
        self.assertEqual(normalize_question("năm ni"), "nam nay")
        self.assertEqual(normalize_question("bữa ni"), "hom nay")
        self.assertEqual(normalize_question("tháng rồi"), "thang truoc")
        self.assertEqual(normalize_question("tháng vừa rồi"), "thang truoc")
        self.assertEqual(normalize_question("lên hay xuống"), "tang hay giam")
        self.assertEqual(normalize_question("giảm hay tăng"), "tang hay giam")
        self.assertEqual(normalize_question("đem về"), "mang ve")
        self.assertEqual(normalize_question("của quán"), "cua cua hang")
        self.assertEqual(normalize_question("tiệm mình"), "cua hang")
        # A bare "quần" stays: it may be a product, not the shop.
        self.assertEqual(normalize_question("doanh thu quần"), "doanh thu quan")


class PlanTests(unittest.TestCase):
    def plan(self, text, today="2026-03-04"):
        rules = read_rules(rulebook())
        return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules)

    def test_rewrites_give_the_same_plan_as_the_train_wording(self):
        pairs = (
            ("Doanh thu tháng 1/2026 là bao nhiêu?", "dthu T1/2026 bao nheiu z?"),
            ("Doanh thu tháng 1/2026 là bao nhiêu?", "1-2026 tong doanh thu duoc bao nhieu?"),
            ("Doanh thu của cửa hàng hôm qua là bao nhiêu?", "Doanh thu bữa qua của quán là bao nhiêu dậy?"),
            ("Doanh thu tháng trước là bao nhiêu?", "Doanh thu tháng rồi là bao nhiêu nhỉ?"),
        )
        for train, rewritten in pairs:
            with self.subTest(rewritten=rewritten):
                expected = self.plan(train)
                self.assertIsNotNone(expected)
                self.assertEqual(self.plan(rewritten), expected)

    def test_conditions_are_still_not_dropped(self):
        # Words that may carry a condition still make the question abstain.
        for text in ("Doanh thu quần tháng 1/2026?", "Doanh thu phía bắc tháng 1/2026?"):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))


if __name__ == "__main__":
    unittest.main()
