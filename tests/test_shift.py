"""Rulebook shifts (ca sáng / ca chiều): order time, rule windows, grammar and execution.

Rulebook rows and sales below are synthetic, written for tests.
"""
import unittest
from datetime import date, datetime, time
from decimal import Decimal

from test_semantics_periods import rulebook

from datamate.agent.compute import _answer_shift, answer_plan, select_shift
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion.sales import Sale, parse_minute
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

SHIFT_ROW = ("| Ca sáng, ca chiều | theo giờ tạo đơn: ca sáng 06:00–13:59, ca chiều 14:00–21:59. "
             "So ca chiều với ca sáng: mốc là **ca sáng** |")


def book(row=SHIFT_ROW):
    return rulebook() + row + "\n"


def sale(order, minute, amount, status="valid", day=date(2026, 3, 2)):
    return Sale(order, day, status, Decimal(amount), "P1", "Trà", Decimal(1), minute=minute)


class ParseMinuteTests(unittest.TestCase):
    def test_clock_values(self):
        self.assertEqual(parse_minute(datetime(2026, 3, 2, 13, 59, 59)), 13 * 60 + 59)
        self.assertEqual(parse_minute(time(14, 0)), 840)
        self.assertEqual(parse_minute("01/03/2026 14:05:09"), 845)
        self.assertEqual(parse_minute("6:03"), 363)
        # A spreadsheet time is a fraction of a day; 14:00 must not round down to 13:59.
        self.assertEqual(parse_minute(14 / 24), 840)

    def test_no_clock_time(self):
        for value in (None, "", "01/03/2026", "25:10", True, 3, Decimal("1.5")):
            with self.subTest(value=value):
                self.assertIsNone(parse_minute(value))


class ShiftRuleTests(unittest.TestCase):
    def test_windows_and_base_come_from_the_split(self):
        rules = read_rules(book())
        self.assertEqual(rules.shifts, (("sang", 360, 839), ("chieu", 840, 1319)))
        self.assertEqual(rules.shift_base, "sang")
        other = read_rules(book(SHIFT_ROW.replace("13:59", "12:59").replace("14:00", "13:00")))
        self.assertEqual(other.shifts, (("sang", 360, 779), ("chieu", 780, 1319)))

    def test_missing_overlapping_or_other_basis_disables_shifts(self):
        for row in ("", SHIFT_ROW.replace("14:00", "13:00"),
                    SHIFT_ROW.replace("theo giờ tạo đơn", "theo giờ thanh toán"),
                    SHIFT_ROW.replace(", ca chiều 14:00–21:59", "")):
            with self.subTest(row=row):
                self.assertEqual(read_rules(book(row)).shifts, ())


class ShiftPlanTests(unittest.TestCase):
    def plan(self, text, row=SHIFT_ROW):
        return plan_question(Question("x", "b", text, date(2026, 4, 10)), read_rules(book(row)))

    def test_one_shift(self):
        for text, intent, name in (
            ("Tháng 3/2026 riêng ca chiều bán được tổng cộng bao nhiêu tiền?", "revenue", "chieu"),
            ("Ca sáng tháng 3/2026 doanh thu được bao nhiêu?", "revenue", "sang"),
            ("Tổng tiền bán hàng của ca sáng tháng 3/2026 là bao nhiêu?", "revenue", "sang"),
            ("Đếm số đơn bán trong ca sáng tháng 3/2026", "order_count", "sang"),
            ("Tuần trước ca chiều có bao nhiêu đơn?", "order_count", "chieu"),
        ):
            with self.subTest(text=text):
                plan = self.plan(text)
                self.assertEqual((plan.intent, plan.comparison), (intent, ""))
                self.assertEqual([(f.field, f.value) for f in plan.filters], [("shift", name)])

    def test_afternoon_against_the_base_shift(self):
        for text, intent in (
            ("Tuần trước số đơn ca chiều chênh ca sáng bao nhiêu phần trăm?", "order_count"),
            ("Tháng 3/2026 doanh thu ca chiều hơn hay kém ca sáng bao nhiêu %?", "revenue"),
            ("Tháng 3/2026 doanh thu ca chiều so ca sáng chênh bao nhiêu %?", "revenue"),
            ("Tháng 3/2026 ca chiều so với ca sáng thì số đơn chênh bao nhiêu %?", "order_count"),
            ("So với ca sáng, doanh thu ca chiều tháng 3/2026 chênh bao nhiêu %?", "revenue"),
        ):
            with self.subTest(text=text):
                plan = self.plan(text)
                self.assertEqual((plan.intent, plan.comparison),
                                 (intent, "shift_change_percent"))
                self.assertEqual(plan.filters[0].value, "chieu")

    def test_unsupported_shift_questions_abstain(self):
        for text in (
            "Tháng 3/2026 ca sáng so với ca chiều doanh thu chênh bao nhiêu %?",  # other base
            "Tháng 3/2026 ca sáng so ca chiều doanh thu chênh bao nhiêu %?",
            "Tháng 3/2026 ca chiều chiếm bao nhiêu % doanh thu?",  # a share, not defined
            "Tháng 3/2026 doanh thu ca chiều chênh ca sáng bao nhiêu tiền?",  # not a percent
            "Mặt hàng nào bán chạy nhất ca chiều tháng 3/2026?",  # shift only for revenue/orders
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))
        self.assertIsNone(self.plan("Ca chiều tháng 3/2026 doanh thu bao nhiêu?", row=""))


class ShiftExecutionTests(unittest.TestCase):
    SALES = [
        sale("A", 13 * 60 + 59, 100), sale("A", 13 * 60 + 59, 50),   # morning, 150
        sale("B", 14 * 60, 300),                                     # afternoon, 300
        sale("C", 15 * 60, 60), sale("D", 16 * 60, 999, "cancelled"),
    ]

    def run_plan(self, text, sales=None):
        plan = plan_question(Question("x", "b", text, date(2026, 4, 10)), read_rules(book()))
        candidate = _answer_shift(plan, sales or self.SALES, book(), {"inputs": {}})
        return plan, candidate

    def test_revenue_and_orders_of_one_shift(self):
        _, candidate = self.run_plan("Ca chiều tháng 3/2026 doanh thu được bao nhiêu?")
        self.assertEqual(candidate.answer, 360)
        _, candidate = self.run_plan("Tháng 3/2026 ca sáng có bao nhiêu đơn?")
        self.assertEqual(candidate.answer, 1)
        self.assertEqual(len(select_shift(self.SALES, 840, 1319)), 3)

    def test_change_against_the_morning(self):
        plan, candidate = self.run_plan(
            "Tháng 3/2026 doanh thu ca chiều so ca sáng chênh bao nhiêu phần trăm?")
        self.assertEqual(candidate.answer, 140.0)  # (360 - 150) / 150
        self.assertIs(check_executed(candidate, plan), candidate)
        plan, candidate = self.run_plan(
            "Tháng 3/2026 số đơn ca chiều chênh ca sáng bao nhiêu phần trăm?")
        self.assertEqual(candidate.answer, 100.0)  # 2 orders against 1
        self.assertIs(check_executed(candidate, plan), candidate)

    def test_missing_time_or_empty_base_abstains(self):
        _, candidate = self.run_plan("Ca chiều tháng 3/2026 doanh thu được bao nhiêu?",
                                     self.SALES + [sale("E", None, 10)])
        self.assertEqual(candidate.type, "abstain")
        _, candidate = self.run_plan(
            "Tháng 3/2026 doanh thu ca chiều chênh ca sáng bao nhiêu phần trăm?",
            [sale("B", 900, 300)])
        self.assertEqual(candidate.type, "abstain")

    def test_backend_requires_a_shift_filter_for_the_shift_comparison(self):
        plan = plan_question(Question("x", "b", "Doanh thu tháng 3/2026 là bao nhiêu?",
                                      date(2026, 4, 10)), read_rules(book()))
        from dataclasses import replace
        broken = replace(plan, comparison="shift_change_percent")
        self.assertEqual(answer_plan({}, None, broken, book()).type, "abstain")


if __name__ == "__main__":
    unittest.main()
