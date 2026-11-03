"""Regression for SEM-01 PR2: average order value, cancelled orders, cancel rate, best day.

Rulebook rows are synthetic paraphrases written for tests, not organizer data.
"""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Catalog, Plan, Question, Split
from datamate.demo import RULEBOOK
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question

ROWS = {
    "money": "| Bán được bao nhiêu tiền, thu về bao nhiêu tiền | Là doanh thu, không phải tiền khách đã trả |",
    "average": "| Giá trị trung bình một đơn | Làm tròn về đồng, nửa đồng làm tròn lên |",
    "cancelled": "| Đơn bị huỷ | Đơn có trạng thái huỷ. Đơn trả hàng không phải đơn huỷ |",
    "rate": ("| Tỉ lệ đơn huỷ trong kỳ | Phần trăm đơn huỷ trong mọi đơn đặt trong kỳ, "
             "mọi trạng thái. Làm tròn 2 chữ số thập phân |"),
}


def rulebook(*skip: str) -> str:
    return RULEBOOK + "\n".join(row for key, row in ROWS.items() if key not in skip) + "\n"


class IntentGrammarTests(unittest.TestCase):
    def plan(self, text, today="2026-07-01", book=None):
        rules = read_rules(rulebook() if book is None else book)
        return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules)

    def assertIntent(self, intent, texts, window=(date(2026, 5, 1), date(2026, 5, 31))):
        for text in texts:
            with self.subTest(text=text):
                plan = self.plan(text)
                self.assertIsNotNone(plan, text)
                self.assertEqual((plan.intent, plan.start, plan.end), (intent, *window))

    def test_percent_sign_is_kept_as_words(self):
        self.assertEqual(normalize_question("bao nhiêu % đơn bị huỷ"),
                         "bao nhieu phan tram don bi huy")
        self.assertEqual(normalize_question("tỉ lệ đơn bij huỷ, ddax huỷ"), "ti le don bi huy da huy")

    def test_average_order_value(self):
        self.assertIntent("average_order", (
            "Giá trị trung bình một đơn tháng 5/2026 là bao nhiêu?",
            "dthu trung bình trên một đơn tháng 5/2026 là bao nhiêu",
            "trung bình mỗi đơn tháng 5/2026 mang về bao nhiêu doanh thu",
            "tháng 5/2026 doanh thu bình quân mỗi đơn hàng là nhiêu",
        ))

    def test_cancelled_orders_count_and_rate(self):
        self.assertIntent("cancel_count", (
            "Đếm số đơn đã huỷ trong tháng 5/2026",
            "tháng 5/2026 bị huỷ mấy đơn",
            "Trong tháng 5/2026 cửa hàng có mấy đơn ở trạng thái huỷ?",
            "tháng 5/2026 có bnhiu bill bị huỷ",
        ))
        self.assertIntent("cancel_rate", (
            "Tỉ lệ huỷ đơn tháng 5/2026 là bao nhiêu?",
            "tháng 5/2026 tỉ lệ đơn bị huỷ là bao nhiêu phần trăm",
            "tháng 5/2026 cửa hàng có bao nhiêu % đơn bị huỷ",
            "tháng 5/2026 tỉ lệ đơn bij huỷ là bn %",
        ))

    def test_best_day(self):
        self.assertIntent("best_day", (
            "Tháng 5/2026 ngày nào cửa hàng bán được nhiều tiền nhất?",
            "doanh thu ngày nào cao nhất trong tháng 5/2026",
            "trong tháng 5/2026 xét theo dthu ngày nào bán đắt nhất",
            "ngày nào trong tháng 5/2026 doanh thu cao nhất",
        ))

    def test_each_intent_requires_its_rule_row(self):
        cases = {
            "average": "Giá trị trung bình một đơn tháng 5/2026 là bao nhiêu?",
            "cancelled": "tháng 5/2026 bị huỷ mấy đơn",
            "rate": "Tỉ lệ huỷ đơn tháng 5/2026 là bao nhiêu?",
        }
        for row, text in cases.items():
            with self.subTest(row=row):
                self.assertIsNotNone(self.plan(text))
                self.assertIsNone(self.plan(text, book=rulebook(row)))
        self.assertIn(ROWS["rate"], self.plan(cases["rate"]).evidence)

    def test_conditions_and_other_measures_still_abstain(self):
        for text in ("tháng 5/2026 có bnhiu đơn bị huỷ vì giao trễ",
                     "tháng 5/2026 có bao nhiêu đơn bị huỷ, chiếm bao nhiêu %",
                     "ngày nào trong tháng 5/2026 doanh thu thấp nhất",
                     "ngày nào trong tháng 5/2026 có nhiều đơn nhất",
                     "trung bình mỗi đơn tháng 5/2026 có mấy món",
                     "tháng 5/2026 giá nhập bình quân một hộp sữa là bao nhiêu",
                     "tỉ lệ huỷ đơn của khách lẻ tháng 5/2026",
                     "doanh thu tháng 5/2026 là bao nhiêu %"):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))


def sale(order, day, status, amount):
    return Sale(order, date(2026, 5, day), status, Decimal(amount), "P1", "Cà phê", Decimal(1))


SALES = [
    sale("O1", 1, "valid", 10000), sale("O1", 1, "valid", 5001),
    sale("O2", 1, "cancelled", 90000),
    sale("O3", 2, "returned", 70000),
    sale("O4", 3, "cancelled", 1000),
    sale("O5", 3, "valid", 10000),
]


class IntentAgentTests(unittest.TestCase):
    def answer(self, text, sales=SALES, readme=""):
        split = Split(Path("."), (), rulebook(), Path("calendar.csv"))
        context = BusinessContext(split, Catalog("b", readme, ()))
        context.cache["sales"] = (sales, {"tables": ["fixture"]})
        return BaselineAgent().answer(Question("x", "b", text, date(2026, 7, 1)), context)

    def test_cancel_count_excludes_returns_and_rate_counts_every_status(self):
        count = self.answer("tháng 5/2026 bị huỷ mấy đơn")
        rate = self.answer("Tỉ lệ huỷ đơn tháng 5/2026 là bao nhiêu?")
        self.assertEqual((count.type, count.answer), ("number", 2))
        self.assertEqual((rate.type, rate.answer), ("percent", 40.0))  # 2 of O1..O5
        third = self.answer("Tỉ lệ huỷ đơn tháng 5/2026 là bao nhiêu?", SALES[:3] + SALES[5:])
        self.assertEqual(third.answer, 33.33)

    def test_average_order_uses_valid_orders_and_rounds_half_up(self):
        result = self.answer("Giá trị trung bình một đơn tháng 5/2026 là bao nhiêu?")
        self.assertEqual((result.type, result.answer), ("number", 12501))  # 25001 / 2

    def test_best_day_and_tie(self):
        result = self.answer("doanh thu ngày nào cao nhất trong tháng 5/2026")
        self.assertEqual((result.type, result.answer), ("date", "2026-05-01"))
        tie = [sale("O1", 1, "valid", 10000), sale("O2", 2, "valid", 10000)]
        self.assertEqual(self.answer("doanh thu ngày nào cao nhất trong tháng 5/2026", tie).type,
                         "abstain")

    def test_empty_period_does_not_divide_by_zero(self):
        only_cancelled = [sale("O2", 1, "cancelled", 90000)]
        result = self.answer("Giá trị trung bình một đơn tháng 5/2026 là bao nhiêu?", only_cancelled)
        self.assertEqual(result.type, "abstain")

    def test_unknown_plan_intent_abstains_instead_of_ranking(self):
        plan = Plan("mystery", date(2026, 5, 1), date(2026, 5, 31))
        with mock.patch("datamate.agent.baseline.plan_question", return_value=plan):
            result = self.answer("bất kỳ")
        self.assertEqual(result.type, "abstain")
        self.assertIn("mystery", result.reason)


if __name__ == "__main__":
    unittest.main()
