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
                         "check giup doanh thu cua cua hang")
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


class EverydayWordingTests(unittest.TestCase):
    """Wordings found with the train question variants (store_word, greeting, chat_typos)."""

    def test_greetings_and_thanks_around_a_question(self):
        expected = normalize_question("doanh thu tháng 3/2026 là bao nhiêu")
        for text in ("Alo doanh thu tháng 3/2026 là bao nhiêu cảm ơn",
                     "Hello shop doanh thu tháng 3/2026 là bao nhiêu thanks",
                     "Dạ cho em hỏi doanh thu tháng 3/2026 là bao nhiêu nha cảm ơn ạ",
                     "Chị ơi cho em hỏi doanh thu tháng 3/2026 là bao nhiêu"):
            with self.subTest(text=text):
                self.assertEqual(normalize_question(text), expected)

    def test_names_that_look_like_greetings_stay(self):
        self.assertEqual(normalize_question("Đá xay"), "da xay")
        self.assertEqual(normalize_question("Cháo gà"), "chao ga")
        self.assertEqual(normalize_question("Dạ cho mình hỏi doanh thu"), "doanh thu")
        self.assertEqual(normalize_question("Chào shop doanh thu"), "doanh thu")

    def test_revenue_of_the_shop_is_not_money_collected(self):
        self.assertIn("doanh thu cua cua hang duoc",
                      normalize_question("doanh thu của cửa hàng được bao nhiêu tiền"))

    def test_chat_spellings_in_context_only(self):
        self.assertEqual(normalize_question("dthu ngày nào cao nhứt trog thg 6 năm 2026"),
                         "doanh thu ngay nao cao nhat trong thang 6/2026")
        self.assertEqual(normalize_question("7 ngày gần nhứt bán bao nhiu dza"),
                         "7 ngay gan nhat ban bao nhiu")
        self.assertEqual(normalize_question("tháng 3 có tăng ko"), "thang 3 co tang khong")
        # "Nhựt" is a name, "ko" inside a name stays.
        self.assertEqual(normalize_question("khách Trần Minh Nhựt mua bao nhiêu"),
                         "khach tran minh nhut mua bao nhieu")

    def test_the_shop_named_by_the_owner(self):
        self.assertEqual(normalize_question("doanh thu bên mình trung bình một đơn tháng 3"),
                         normalize_question("doanh thu trung bình một đơn tháng 3"))
        self.assertEqual(normalize_question("số khách còn nợ quán"),
                         normalize_question("số khách còn nợ cửa hàng"))
        # With accents "quán" is the shop; "quần" (a product) and unaccented "quan" are left.
        self.assertEqual(normalize_question("tháng này quán bán được bao nhiêu"),
                         normalize_question("tháng này cửa hàng bán được bao nhiêu"))
        self.assertIn("quan ban", normalize_question("tháng này quần bán được bao nhiêu"))
        self.assertIn("quan ban", normalize_question("thang nay quan ban duoc bao nhieu"))
        for wording in ("doanh thu bên mình thuốc kê đơn tháng 3",
                        "doanh thu quán mình thuốc kê đơn tháng 3",
                        "doanh thu cửa hàng thuốc kê đơn tháng 3"):
            self.assertEqual(normalize_question(wording),
                             normalize_question("doanh thu thuốc kê đơn tháng 3"))
        self.assertIn("tong doanh thu", normalize_question("chiếm bao nhiêu % doanh thu cả quán"))
        # Never "doanh thu được" (read as money collected from debtors).
        self.assertNotIn("doanh thu duoc", normalize_question("doanh thu của quán được bao nhiêu"))

    def test_binh_quan_is_trung_binh_only_for_orders(self):
        self.assertEqual(normalize_question("giá trị bình quân một đơn"),
                         "gia tri trung binh mot don")
        self.assertEqual(normalize_question("bình quân mỗi đơn"), "trung binh moi don")
        self.assertIn("binh quan", normalize_question("giá nhập bình quân một thùng"))

    def test_same_measure_in_other_words(self):
        for wording, train in (
                ("số doanh thu ghi nhận hôm qua", "doanh thu hôm qua"),
                ("giá trị bình quân tính cho một đơn tháng 7/2026", "giá trị trung bình một đơn tháng 7/2026"),
                ("bình quân mỗi ngày bán ra lên hay xuống bao nhiêu %",
                 "trung bình mỗi ngày bán ra tăng hay giảm bao nhiêu %"),
                ("khách nào mua nhiều đơn nhứt", "khách nào mua nhiều đơn nhất"),
                ("Ca chiều tuần trước doanh thu tiệm mình được bao nhiêu?",
                 "Ca chiều tuần trước doanh thu của cửa hàng được bao nhiêu?"),
                ("chiếm bao nhiêu phần trăm doanh thu cả quán mình", "chiếm bao nhiêu phần trăm tổng doanh thu"),
                ("thuốc kê đơn chiếm bao nhiêu % doanh thu, trong Tháng 1/2025?",
                 "Tháng 1/2025 thuốc kê đơn chiếm bao nhiêu % doanh thu?"),
                ("thuốc kê đơn bán được bao nhiêu, trong tháng 1/2025 hả?",
                 "tháng 1/2025 thuốc kê đơn bán được bao nhiêu hả?"),
                ("khách còn thiếu tiền quán", "khách còn thiếu tiền cửa hàng"),
                ("khách chi nhiều tiền nhất cho tiệm", "khách chi nhiều tiền nhất cho cửa hàng"),
                ("quán còn bao nhiêu khách nợ", "cửa hàng còn bao nhiêu khách nợ"),
                ("nhân viên bán giỏi nhứt", "nhân viên bán giỏi nhất")):
            with self.subTest(wording=wording):
                self.assertEqual(normalize_question(wording), normalize_question(train))
        # "So doanh thu …" compares; only "số doanh thu ghi nhận" is the revenue itself.
        self.assertIn("so doanh thu", normalize_question("So doanh thu tháng này với tháng trước"))
        self.assertIn("binh quan", normalize_question("giá nhập bình quân một thùng tháng 3"))

    def test_particle_after_la_chi(self):
        self.assertEqual(normalize_question("top 5 hàng chạy nhất là chi nha"),
                         "top 5 hang chay nhat la gi")


class OpeningTests(unittest.TestCase):
    def test_small_talk_before_the_question_is_cut(self):
        from datamate.semantics.text import opening_cuts
        question = ("Mình đang gom số liệu để hoàn tất báo cáo nội bộ. Nhờ tra giúp câu này: "
                    "hôm qua doanh thu bao nhiêu?")
        self.assertEqual(opening_cuts(question)[-1], "hôm qua doanh thu bao nhiêu?")
        self.assertEqual(opening_cuts("minh dang lam bao cao. cho hoi: hom qua dthu bao nhieu")[-1],
                         "hom qua dthu bao nhieu")

    def test_an_opening_with_a_condition_or_number_is_kept(self):
        from datamate.semantics.text import opening_cuts
        for question in ("Chỉ tính đơn online. Hôm qua doanh thu bao nhiêu?",
                         "So doanh thu tháng này: mỗi bên bao nhiêu?",
                         "Từ ngày 16/03/2025 giá tăng. So 7 ngày kể từ đó với 7 ngày trước",
                         "Khách hàng thân thiết thôi nhé. Hôm qua doanh thu bao nhiêu?",
                         "Riêng Bánh quy bơ nha. Hôm qua bán được bao nhiêu?"):
            with self.subTest(question=question):
                self.assertEqual(opening_cuts(question), [])
        self.assertEqual(opening_cuts("Xem giúp Bánh quy bơ nha. Hôm qua bán được mấy hộp?",
                                      ("banh quy bo",)), [])


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
