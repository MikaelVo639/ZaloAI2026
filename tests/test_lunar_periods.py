import tempfile
import unittest
from datetime import date
from pathlib import Path

from datamate.contracts import Question
from datamate.semantics.lunar_periods import plan_tet
from datamate.semantics.rules import Rules


class LunarPeriodTests(unittest.TestCase):
    def test_comparison_uses_the_split_calendar_and_inclusive_length(self):
        rulebook = ("| Tết năm Y | Từ 23 tháng Chạp đến hết mùng 10 tháng Giêng. "
                    "Trước Tết lấy cùng số ngày ngay trước 23 tháng Chạp. |")
        rules = Rules(revenue=True, month_to_date=False, monday_week=False,
                      quantity_ranking=False, source_rows=(), change_percent=True)
        with tempfile.TemporaryDirectory() as directory:
            calendar = Path(directory) / "calendar.csv"
            calendar.write_text("date,lunar_year,lunar_month,lunar_day,lunar_leap\n"
                                "2028-01-15,2027,12,23,false\n"
                                "2028-01-31,2028,1,10,false\n", encoding="utf-8")

            def resolve(days, book=rulebook):
                question = Question("q", "b", "Dịp Tết âm lịch 2028 có doanh thu hơn hay kém "
                                    f"{days} ngày trước Tết bao nhiêu %?", date(2028, 3, 1))
                return plan_tet(question, rules, book, calendar)

            plan = resolve(17)
            self.assertEqual((plan.start, plan.end), (date(2028, 1, 15), date(2028, 1, 31)))
            self.assertEqual((plan.compare_start, plan.compare_end),
                             (date(2027, 12, 29), date(2028, 1, 14)))
            self.assertIsNone(resolve(16))
            self.assertIsNone(resolve(17, ""))
            calendar.unlink()
            self.assertIsNone(resolve(17))
