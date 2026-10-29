import unittest
from datetime import date
from decimal import Decimal

from datamate.contracts import Question
from datamate.demo import RULEBOOK
from datamate.ingestion.sales import parse_date, parse_number
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules


class SemanticsTests(unittest.TestCase):
    def plan(self, text, today="2026-03-04"):
        return plan_question(Question("x", "b", text, date.fromisoformat(today)), read_rules(RULEBOOK))

    def test_previous_week_uses_monday_to_sunday(self):
        plan = self.plan("Doanh thu tuần rồi bao nhiêu?")
        self.assertEqual((plan.start, plan.end), (date(2026, 2, 23), date(2026, 3, 1)))

    def test_month_and_recent_days_include_as_of_date(self):
        month = self.plan("doanh thu tháng này bao nhiêu")
        recent = self.plan("doanh thu 7 ngày gần nhất bao nhiêu")
        self.assertEqual((month.start, month.end), (date(2026, 3, 1), date(2026, 3, 4)))
        self.assertEqual((recent.end - recent.start).days, 6)
        today = self.plan("Tổng doanh thu trong ngày hôm nay là bao nhiêu?")
        self.assertEqual((today.start, today.end), (date(2026, 3, 4), date(2026, 3, 4)))

    def test_explicit_month_is_capped_at_as_of_date(self):
        plan = self.plan("Doanh thu tháng 3/2026 bao nhiêu")
        self.assertEqual(plan.end, date(2026, 3, 4))

    def test_unknown_filters_comparisons_and_invalid_dates_are_not_ignored(self):
        for text in ("Doanh thu tiền mặt tháng này bao nhiêu?",
                     "Doanh thu nhóm cà phê tháng này bao nhiêu?",
                     "Doanh thu tháng này so với tháng trước tăng bao nhiêu?",
                     "Doanh thu ngày 31/02/2026 bao nhiêu?",
                     "Doanh thu 0 ngày gần nhất bao nhiêu?", "Doanh thu bao nhiêu?"):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))

    def test_vietnamese_dates_and_sheet_year(self):
        self.assertEqual(parse_date("1 thg 2 2026"), date(2026, 2, 1))
        self.assertEqual(parse_date("02/03", "Sales 2025"), date(2025, 3, 2))
        self.assertEqual(parse_date("2026-03-04 07:30:00"), date(2026, 3, 4))
        with self.assertRaises(ValueError):
            parse_date("02/03")

    def test_native_numbers_do_not_receive_locale_conversion(self):
        self.assertEqual(parse_number(1.234), Decimal("1.234"))
        self.assertEqual(parse_number("1.234.567"), Decimal("1234567"))
        self.assertEqual(parse_number("1.234,50"), Decimal("1234.50"))
        self.assertEqual(parse_number("1,234.50"), Decimal("1234.50"))
        self.assertEqual(parse_number("25.000đ"), Decimal("25000"))
        for value in (True, "NaN", "Infinity", "", None):
            with self.assertRaises(ValueError):
                parse_number(value)
