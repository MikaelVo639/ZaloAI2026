"""Đợt 3 gói 8: product-group share wordings typed with Telex leftovers ("nhoms", "chieems").

Rulebook rows and group names are synthetic, written for tests.
"""
import unittest
from datetime import date

from test_semantics_periods import rulebook

from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question

GROUP = ("| Nhóm hàng | theo cột nhóm hàng, đúng tên nhóm câu hỏi nêu; không gộp nhóm khác tên dù "
         "tên gần nhau |\n")
SHARE = ("| Mặt hàng | Phí phục vụ không phải mặt hàng. Vẫn tính vào doanh thu, kể cả mẫu số tỉ "
         "trọng nhóm hàng |\n")
VOCABULARY = {"product_group": ["bánh", "gỏi", "nước ép", "mắt mũi họng"]}


def plan(text, book=None):
    rules = read_rules(rulebook() + GROUP + SHARE if book is None else book)
    return plan_question(Question("x", "b", text, date(2026, 7, 10)), rules, (), VOCABULARY)


class TelexGroupWordingTests(unittest.TestCase):
    def test_telex_keys_on_grammar_words(self):
        self.assertEqual(normalize_question("nhoms gỏi chieems bn % toongr dthu"),
                         "nhom goi chiem bn phan tram tong dthu")
        # A name that only contains the word is untouched.
        self.assertEqual(normalize_question("Thước thủy nhôm"), "thuoc thuy nhom")

    def test_share_and_revenue_with_telex(self):
        for text, group, comparison in (
                ("check giùm dthu nhoms hàng bánh chiếm bnhiu % tổng doanh thu thangs 1 năm 2026",
                 "bánh", "share"),
                ("coi giùm dthu nhóm hàng mắt mũi họng chieems bn % toongr revenue tháng 1/2026",
                 "mắt mũi họng", "share"),
                ("trong tháng 1/2026, nhoms gỏi đóng góp bao nhieeu % dthu cả cửa hàng", "gỏi",
                 "share"),
                ("cho hỏi nhoms nước ép tháng 1/2026 bán dc bao nhiêu tiền", "nước ép", "")):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.comparison), ("revenue", comparison))
                self.assertEqual([(f.field, f.value) for f in result.filters],
                                 [("product_group", group)])
                self.assertEqual((result.start, result.end), (date(2026, 1, 1), date(2026, 1, 31)))

    def test_rules_and_unknown_groups_still_abstain(self):
        self.assertIsNone(plan("nhoms gỏi chiếm bao nhiêu % doanh thu tháng 1/2026",
                               book=rulebook() + GROUP))
        self.assertIsNone(plan("nhoms lẩu chiếm bao nhiêu % doanh thu tháng 1/2026"))


if __name__ == "__main__":
    unittest.main()
