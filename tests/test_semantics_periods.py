"""Regression for SEM-01: question wording, rule-backed periods and rule changes between splits.

Rulebook rows below are synthetic paraphrases written for tests, not organizer data.
"""
import unittest
from datetime import date

from datamate.contracts import Question
from datamate.demo import RULEBOOK
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question

ROWS = {
    "money": "| Bán được bao nhiêu tiền, thu về bao nhiêu tiền | Là doanh thu, không phải tiền khách đã trả |",
    "range": "| Từ X đến Y | Gồm cả X và Y |",
    "ytd": "| Từ đầu năm | Từ 01/01 của năm nay đến hết hôm nay |",
    "implicit": ("| Tháng M, quý Q không ghi năm | Của năm nay nếu không sau hôm nay; sau hôm nay "
                 "thì của năm trước. Quý theo dương lịch, mỗi quý 3 tháng tính từ tháng 1 |"),
}
GOODS = "không gồm phí giao hàng. Tiền hàng, tiền bán cùng nghĩa với doanh thu |"


def rulebook(*skip: str, **replace: str) -> str:
    rows = {**ROWS, **replace}
    base = RULEBOOK.replace("không gồm phí giao hàng |", GOODS)
    return base + "\n".join(row for key, row in rows.items() if key not in skip) + "\n"


class PeriodTests(unittest.TestCase):
    def plan(self, text, today="2026-03-04", book=None):
        rules = read_rules(rulebook() if book is None else book)
        return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules)

    def window(self, text, today="2026-03-04", book=None):
        plan = self.plan(text, today, book)
        self.assertIsNotNone(plan, text)
        return plan.intent, plan.start, plan.end

    def test_telex_and_abbreviations_only_touch_grammar_words(self):
        # The closing particle "vậy" is dropped at the end of a question.
        self.assertEqual(normalize_question("toongr dthu hnay laf bnhiu vaayj"),
                         "tong dthu hom nay la bnhiu")
        self.assertEqual(normalize_question("bữa qua có bao nhiu ddown"), "hom qua co bao nhiu don")
        self.assertEqual(normalize_question("tinhs toonf kho"), "tinh ton kho")
        self.assertEqual(normalize_question("Tháng 8 năm 2025, quý 2 năm 2026"),
                         "thang 8/2025 quy 2/2026")
        # "lên", "thuốc"/"thuộc" and the closing "nhỉ" typed in Telex decode like any other
        # grammar word, so no wording needs its own typo pattern.
        self.assertEqual(normalize_question("đơn từ 2 món trở leen ở nhà thuoocs nhir"),
                         "don tu 2 mon tro len o nha thuoc")
        self.assertEqual(normalize_question("khách thuoocj nhóm sỉ"), "khach thuoc nhom si")
        # Names that merely look like Telex keystrokes stay as written.
        self.assertEqual(normalize_question("Bột giặt Max, van gas, Panadol Plus"),
                         "bot giat max van gas panadol plus")

    def test_everyday_wording_for_revenue_and_orders(self):
        for text in ("Tháng 5/2025 cửa hàng bán đc tổng cộng bao nhiêu tiền?",
                     "thang 5/2025 cua hang ban duoc total bnhiu tien",
                     "toongr dthu tháng 5 năm 2025 là bn",
                     "Tiền hàng tháng 5/2025 là bao nhiêu?"):
            with self.subTest(text=text):
                self.assertEqual(self.window(text, "2026-07-01"),
                                 ("revenue", date(2025, 5, 1), date(2025, 5, 31)))
        for text in ("Hôm qua cửa hàng bán được mấy đơn?", "hqua có tổng cộng bao nhiêu đơn?",
                     "bữa qua có tổng cộng bao nhiu ddown", "số bill hnay là bnhiu"):
            with self.subTest(text=text):
                intent, start, _ = self.window(text)
                self.assertEqual(intent, "order_count")
                self.assertIn(start, (date(2026, 3, 3), date(2026, 3, 4)))

    def test_quantity_wording_preserves_the_asked_unit(self):
        book = (rulebook() + "| Bán được bao nhiêu <đơn vị> | cộng số lượng quy về đơn vị "
                "cơ bản của các mặt hàng có đơn vị cơ bản là <đơn vị>, kể cả dòng bán theo "
                "đơn vị lớn; mặt hàng có đơn vị cơ bản khác không tính |\n")
        for text, unit in (
            ("Hôm qua bán ra được bao nhiêu phần?", "phan"),
            ("Cộng mọi mặt hàng tính theo lon, hôm qua bán ra được mấy lon?", "lon"),
        ):
            with self.subTest(text=text):
                result = self.plan(text, book=book)
                self.assertIsNotNone(result)
                self.assertEqual((result.intent, result.unit), ("quantity", unit))
        self.assertIsNone(self.plan("Cộng mọi mặt hàng tính theo ly, hôm qua bán ra mấy lon?",
                                    book=book))
        self.assertIsNone(self.plan("Hôm qua bán được bao nhiêu phần trăm?", book=book))

    def test_money_wording_requires_rule_of_current_split(self):
        text = "Tháng 5/2025 cửa hàng bán được bao nhiêu tiền?"
        self.assertIsNone(self.plan(text, "2026-07-01", rulebook("money")))
        self.assertIsNone(self.plan("Tiền hàng tháng 5/2025 là bao nhiêu?", "2026-07-01",
                                    rulebook().replace(GOODS, "không gồm phí giao hàng |")))

    def test_quarters_and_inclusive_ranges(self):
        self.assertEqual(self.window("Tổng doanh thu quý 4/2025 là bao nhiêu?")[1:],
                         (date(2025, 10, 1), date(2025, 12, 31)))
        self.assertEqual(self.window("trong quý 1 năm 2026 có tổng cộng bnhiu đơn")[1:],
                         (date(2026, 1, 1), date(2026, 3, 4)))  # capped at as-of date
        self.assertEqual(
            self.window("doanh thu từ 28/02/2026 đến hết 01/03/2026 là bao nhiêu")[1:],
            (date(2026, 2, 28), date(2026, 3, 1)))
        for text in ("doanh thu từ 02/03/2026 đến 01/03/2026", "doanh thu từ 30/02/2026 đến 01/03/2026",
                     "doanh thu quý 5/2025"):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))

    def test_year_and_month_to_date(self):
        self.assertEqual(self.window("Số đơn từ đầu năm tới hết hôm nay là bao nhiêu?")[1:],
                         (date(2026, 1, 1), date(2026, 3, 4)))
        self.assertEqual(self.window("doanh thu từ đầu tháng này đến hết hôm nay")[1:],
                         (date(2026, 3, 1), date(2026, 3, 4)))

    def test_explicit_current_cutoffs_and_rolling_windows(self):
        for text, start in (
            ("Năm nay tính đến hôm nay có tổng cộng bao nhiêu đơn?", date(2026, 1, 1)),
            ("Từ đầu năm nay đến bây giờ có bao nhiêu đơn?", date(2026, 1, 1)),
            ("Doanh thu từ mùng 1 tháng này đến hết hôm nay", date(2026, 3, 1)),
            ("Tuần này tính tới hôm nay doanh thu bao nhiêu?", date(2026, 3, 2)),
            ("6 ngày qua kể cả hôm nay doanh thu bao nhiêu?", date(2026, 2, 27)),
            ("Tính luôn hôm nay thì 6 ngày gần nhất có bao nhiêu đơn?", date(2026, 2, 27)),
            ("Riêng ngày hôm nay doanh thu bao nhiêu?", date(2026, 3, 4)),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.window(text)[1:], (start, date(2026, 3, 4)))
        self.assertIsNone(self.plan("Doanh thu 6 ngày qua, không tính hôm nay"))
        self.assertIsNone(self.plan("Từ mùng 2 tháng này đến hết hôm nay, doanh thu?"))
        self.assertIsNone(self.plan("Tuần này tính tới hôm nay còn bao nhiêu hàng?"))

    def test_month_or_quarter_without_year(self):
        today = "2026-03-04"
        self.assertEqual(self.window("doanh thu tháng 3", today)[1:], (date(2026, 3, 1), date(2026, 3, 4)))
        self.assertEqual(self.window("doanh thu tháng 5", today)[1:], (date(2025, 5, 1), date(2025, 5, 31)))
        self.assertEqual(self.window("doanh thu quý 1", today)[1:], (date(2026, 1, 1), date(2026, 3, 4)))
        self.assertEqual(self.window("doanh thu quý 2", today)[1:], (date(2025, 4, 1), date(2025, 6, 30)))
        self.assertIsNone(self.plan("doanh thu tháng 13", today))

    def test_date_boundaries(self):
        # Monday and Sunday as-of dates; leap February.
        self.assertEqual(self.window("doanh thu tuần rồi", "2026-03-02")[1:],
                         (date(2026, 2, 23), date(2026, 3, 1)))
        self.assertEqual(self.window("doanh thu tuần rồi", "2026-03-01")[1:],
                         (date(2026, 2, 16), date(2026, 2, 22)))
        self.assertEqual(self.window("doanh thu tuần này", "2026-03-02")[1:],
                         (date(2026, 3, 2), date(2026, 3, 2)))
        self.assertEqual(self.window("doanh thu tháng 2/2024", "2026-03-02")[1:],
                         (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(self.window("doanh thu tháng trước", "2024-03-31")[1:],
                         (date(2024, 2, 1), date(2024, 2, 29)))

    def test_rules_changed_between_splits_disable_wording(self):
        cases = {
            "doanh thu từ 01/02/2026 đến 10/02/2026": rulebook(range="| Từ X đến Y | Không gồm Y |"),
            "doanh thu từ đầu năm tới nay": rulebook("ytd"),
            "doanh thu quý 1/2026": rulebook("implicit"),
            "doanh thu tháng 5": rulebook(implicit="| Tháng M không ghi năm | Của năm gần nhất |"),
        }
        for text, book in cases.items():
            with self.subTest(text=text):
                self.assertIsNotNone(self.plan(text))
                self.assertIsNone(self.plan(text, book=book))

    def test_plan_cites_the_rule_rows_it_used(self):
        plan = self.plan("Quý 4 năm 2025 cửa hàng bán được tổng cộng bao nhiêu tiền?")
        self.assertIn(ROWS["implicit"], plan.evidence)
        self.assertIn(ROWS["money"], plan.evidence)
        self.assertNotIn(ROWS["range"], plan.evidence)

    def test_unsupported_conditions_still_abstain(self):
        for text in ("Tháng 5/2025 cửa hàng bán được bao nhiêu tiền mặt?",
                     "tháng này thu được bao nhiêu tiền khách trả nợ",
                     "Hôm qua có mấy đơn bị huỷ?",
                     "Doanh thu quý 2/2025 nhóm cà phê là bao nhiêu?",
                     "doanh thu từ đầu tháng này đến hết hôm nay so với cùng kỳ tháng trước",
                     "dthu 3 tháng liền nhau kết thúc ở tháng 6/2025",
                     "doanh thu tháng 3/2026 và tháng 4/2026",
                     "tháng 7/2026 có bao nhiêu đơn có dthu từ 55.000 đồng trở lên"):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text, "2026-07-01"))


if __name__ == "__main__":
    unittest.main()
