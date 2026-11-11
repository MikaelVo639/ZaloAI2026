"""Typing slips and word order in everyday questions.

Questions, catalogue names and rulebook rows below are synthetic, written for tests.
"""
import unittest
from datetime import date

from test_new_intents import ROWS as ORDER_ROWS
from test_sale_context_fields import _Shop
from test_sem02_grammar import rulebook
from test_semantics_periods import GOODS
from test_semantics_periods import ROWS as PERIOD_ROWS
from test_staff import BEST

from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Question, Split
from datamate.demo import COLUMNS
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question

BOOK = (rulebook().replace("không gồm phí giao hàng |", GOODS) + "\n".join((
    PERIOD_ROWS["money"], PERIOD_ROWS["implicit"], ORDER_ROWS["cancelled"],
    ORDER_ROWS["average"], BEST)) + "\n")


def plan(text, names=(), today=date(2026, 4, 10)):
    return plan_question(Question("x", "b", text, today), read_rules(BOOK), names)


def normalize(text, names=()):
    return normalize_question(text, product_names=names, as_of_year=2026)


class TypingSlipTests(unittest.TestCase):
    def assertSamePlan(self, canonical, variant, names=()):
        expected = plan(canonical, names)
        self.assertIsNotNone(expected, canonical)
        self.assertEqual(plan(variant, names), expected, variant)

    def test_slips_in_grammar_words(self):
        for canonical, variant in (
            ("Tháng 3/2026 ngày nào cửa hàng bán nhiều tiền nhất?",
             "Tháng 3/26 ngày mô quán bná nhiều tiền nhất?"),
            ("Ngày nào tháng 3/2026 cửa hàng bán nhiều tiền nhất?",
             "Ngày nào th 3/26 quán bánnhiều tiền nhất z?"),
            ("Từ đầu năm đến hôm nay bán được bao nhiêu tiền?",
             "Tu dau nam den hom nay bna duoc bnhiu tien roi?"),
            ("Tháng 3/2026 ai là khách mua nhiều đơn nhất?",
             "Th3/2026 ai là khách mua nhiềuđơn nhất quán z?"),
            ("Bình quân 1 đơn tháng 3/2026 bao nhiêu tiền?", "Bìn quân 1 đơn th3/2026 bao nhiêu tiền?"),
            ("Tuần trước trung bình 1 đơn giá trị bao nhiêu?",
             "Tuần trước trung bìh 1 đơn giá trị bao nhiêu?"),
            ("30 ngày gần nhất doanh thu được bao nhiêu?", "30 ngay gan nhat doanhthu duoc bao nhieu?"),
            ("Tuần trước có mấy đơn tổng cộng?", "tuần trc dduowcj mí đơn tổg cộg zị ha"),
        ):
            with self.subTest(variant=variant):
                self.assertSamePlan(canonical, variant)

    def test_slip_words_in_catalogue_names_stay(self):
        # A catalogue name written with the slip keeps its spelling, and the question abstains.
        for name, text in (("Bánh BNA", "Tháng 3/2026 bna được bao nhiêu tiền?"),
                           ("Hạt bin quan", "Bìn quân 1 đơn th3/2026 bao nhiêu tiền?")):
            with self.subTest(name=name):
                self.assertEqual(normalize(text, [name]), normalize_question(text))

    def test_marked_misspellings_of_money_staff_and_count(self):
        for canonical, variant in (
            ("Tuần trước bán được bao nhiêu tiền?", "Tuần trước bán được bao nhiêu tền ha?"),
            ("Tháng 3/2026 cửa hàng bán được bao nhiêu tiền?",
             "Tháng 3/2026 tiệm bán được bao nhiêu tiềng?"),
            ("Tháng 3/2026 nhân viên bán giỏi nhất là ai?", "Th3/2026 nhân diên nào bán giỏi nhất?"),
            ("Tháng 3/2026 khách nào mua nhiều đơn nhất?",
             "Tháng 3/2026 khách nào mua nhiều lần nhất nhỉ, đếm theo xố đơn ấy?"),
        ):
            with self.subTest(variant=variant):
                self.assertSamePlan(canonical, variant)
        # Only the misspelled forms: hours, names, power and "most times" keep their meaning.
        for text in ("Tháng 3/2026 bán được bao nhiêu tiếng?", "Tên khách mua nhiều nhất",
                     "Nhân viên điện nào", "Tháng 3/2026 khách nào mua nhiều lần nhất?"):
            with self.subTest(text=text):
                self.assertEqual(normalize(text), normalize_question(text))
        self.assertNotEqual(plan("Tháng 3/2026 bán được bao nhiêu tiếng?"),
                            plan("Tháng 3/2026 bán được bao nhiêu tiền?"))
        self.assertIsNone(plan("Tháng 3/2026 khách nào mua nhiều lần nhất?"))

    def test_marked_slip_can_overlap_a_longer_catalogue_name(self):
        question = "Tháng 3/2026 doanh thu Bột Tiền Báng 500g?"
        names = ("Bột Tiền Báng 500g", "Bột Tiền Bán 500g")
        self.assertEqual(plan(question, names).filters[0].value, names[0])

    def test_compound_particle_after_a_name_can_still_be_removed(self):
        names = ("Bột Nha E", "Bột Ngũ Cốc")
        self.assertSamePlan("Tháng 3/2026 doanh thu Bột Nha E?",
                            "Tháng 3/2026 doanh thu Bột Nha E nha e nha?", names)
        self.assertEqual(plan("Tháng 3/2026 doanh thu SP Nha E?", ("SP Nha E",))
                         .filters[0].value, "SP Nha E")
        self.assertSamePlan("Tháng 3/2026 doanh thu Bột Ngũ Cốc?",
                            "Tháng 3/2026 doanh thu Bột Ngũ Cốc nhe e?", names)

    def test_spoken_and_short_months(self):
        self.assertSamePlan("Tháng 3 cửa hàng có bao nhiêu đơn bị huỷ?",
                            "Tháng ba quán có tất cả bao nhiêu đơn bị huỷ thế?")
        self.assertSamePlan("Tháng 3 trung bình mỗi đơn được bao nhiêu doanh thu?",
                            "Th3 trung bình mỗi đơn được bao nhiêu doanh thu?")
        # "tháng sau" is next month and "tháng năm" may be "month and year": not rewritten.
        for text in ("Tháng sau", "Tháng năm 2026", "Tháng 3/2026"):
            self.assertEqual(normalize(text), normalize_question(text))

    def test_word_order_and_order_spelling(self):
        for canonical, variant in (
            ("Tháng 11/2025 cửa hàng bán được tổng cộng bao nhiêu tiền?",
             "Tháng 11/25 tiệm bán tổng cộng đươcj bnhiu tiền?"),
            ("Cửa hàng tháng 7/2025 bán được tổng cộng bao nhiêu tiền?",
             "Tiem ban th7/2025 duoc tong cong bao nhieu tien nhi?"),
            ("Tuần trước mặt hàng nào bán chạy nhất?", "Tuan truoc ban chay nhat la mat hang nao?"),
            ("Quý 1/2026 cửa hàng bán được bao nhiêu đơn?",
             "Quý 1 năm 2026 tiệm chốt được bao nhiêu o đờ hả?"),
            ("Giá trị bình quân 1 đơn tuần trước được bao nhiêu?",
             "Gia tri binh quan 1 don tuan truoc duoc bao nhieu the?"),
        ):
            with self.subTest(variant=variant):
                self.assertSamePlan(canonical, variant)
        # An unaccented closing "the" may be the unit "thẻ" outside an average-order question.
        self.assertEqual(normalize("Tuan truoc ban duoc bao nhieu the"),
                         normalize_question("Tuan truoc ban duoc bao nhieu the"))


class CatalogueIdentityTests(_Shop):
    def test_product_names_are_preserved_on_the_agents_first_pass(self):
        names = ("Tiền Báng", "Tiền Bán", "Nha E", "Bánh Nhe E")
        catalog = self.shop("", {"sales.csv": [
            COLUMNS, *[(f"O{i}", "2026-03-01", "completed", f"P{i}", name, 1, amount)
                       for i, (name, amount) in enumerate(zip(names, (100, 200, 300, 400)))],
        ]})
        split = Split(self.data.parent, (), BOOK, self.data.parent / "calendar.csv")
        context = BusinessContext(split, catalog)
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        for text, expected in (
            ("Doanh thu Tiền Báng tháng 3/2026?", 100),
            ("Tháng 3/2026 doanh thu Tiền Bán?", 200),
            ("Tháng 3/2026 doanh thu Nha E?", 300),
            ("Tháng 3/2026 doanh thu Bánh Nhe E nha?", 400),
            ("Tháng 3/2026 bán được bao nhiêu tền?", 1000),
        ):
            with self.subTest(text=text):
                result = agent.answer(Question("q", "shop", text, date(2026, 4, 10)), context)
                self.assertEqual((result.type, result.answer), ("number", expected), result.reason)


if __name__ == "__main__":
    unittest.main()
