"""Bản 21: more wordings of revenue comparisons, running periods, month lists and customer
shares. Questions are written for these tests; rulebook rows come from the synthetic
paraphrases of tests/test_sem02_grammar.py and tests/test_compare_filters.py.
"""
import unittest
from datetime import date
from decimal import Decimal

import test_compare_filters as compare
from test_sem02_grammar import plan

from datamate.contracts import Question
from datamate.semantics.planner import _carry_year, plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question


def compared(text, today="2026-06-10"):
    rules = read_rules(compare.rulebook())
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules)


class ComparisonWordingTests(unittest.TestCase):
    def windows(self, text, today="2026-06-10"):
        result = compared(text, today)
        self.assertIsNotNone(result, text)
        self.assertEqual((result.intent, result.comparison), ("revenue", "change_percent"))
        return (result.start, result.end), (result.compare_start, result.compare_end)

    def test_month_to_date_against_the_same_days_of_last_month(self):
        expected = ((date(2026, 6, 1), date(2026, 6, 10)), (date(2026, 5, 1), date(2026, 5, 10)))
        for text in ("Doanh thu tháng này thấp hay cao hơn mấy % so với cùng số ngày đầu tháng trước?",
                     "so voi cung so ngay cua thang roi, dthu thang nay tang hay giam bn phan tram",
                     "Doanh thu tháng này tính tới hôm nay so với cùng kỳ tháng trước lên xuống bao nhiêu %",
                     "doanh thu từ ngày 1 tháng này đến hôm nay so với cùng kỳ tháng trước thay đổi mấy %"):
            with self.subTest(text=text):
                self.assertEqual(self.windows(text), expected)

    def test_full_months(self):
        march = (date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(self.windows("Doanh thu tháng 3/2026 so tháng trước ít hay nhiều hơn bao nhiêu %?"),
                         (march, (date(2026, 2, 1), date(2026, 2, 28))))
        self.assertEqual(self.windows("dthu tháng 3/2026 tăng giảm mấy % so với cùng kỳ năm"),
                         (march, (date(2025, 3, 1), date(2025, 3, 31))))
        self.assertEqual(self.windows("doanh thu tháng 3/2026 so với tháng 3/2025 năm trước thì thay đổi mấy %"),
                         (march, (date(2025, 3, 1), date(2025, 3, 31))))

    def test_other_wordings_still_abstain(self):
        for text in ("doanh thu tháng 3/2026 nhiều hay ít hơn bao nhiêu tiền so với tháng trước",
                     "tháng 3/2026 bán nhiều hay ít"):
            with self.subTest(text=text):
                self.assertIsNone(compared(text))


class RunningPeriodTests(unittest.TestCase):
    def window(self, text):
        result = plan(text)
        self.assertIsNotNone(result, text)
        return result.intent, result.start, result.end

    def test_year_to_date(self):
        for text in ("Từ đầu năm tới giờ shop có tổng cộng mấy đơn?",
                     "tu dau nam nay den het hom nay doanh thu bao nhieu",
                     "Năm nay tính đến hôm nay doanh thu được bao nhiêu?"):
            with self.subTest(text=text):
                self.assertEqual(self.window(text)[1:], (date(2026, 1, 1), date(2026, 7, 15)))

    def test_last_days_including_today(self):
        for text in ("5 ngày qua tính cả hôm nay doanh thu bao nhiêu",
                     "tính luôn hôm nay thì 5 ngày gần nhất có bao nhiêu đơn"):
            with self.subTest(text=text):
                self.assertEqual(self.window(text)[1:], (date(2026, 7, 11), date(2026, 7, 15)))
        self.assertIsNone(plan("doanh thu 5 ngày qua"))  # whether today counts is not said


class MonthListTests(unittest.TestCase):
    def months(self, text):
        result = plan(text)
        self.assertIsNotNone(result, text)
        return result.shape, [(start.year, start.month) for start, _ in result.periods]

    def test_year_written_once(self):
        self.assertEqual(_carry_year("thang 11 thang 12 thang 1/2026"),
                         "thang 11/2025 thang 12/2025 thang 1/2026")
        self.assertEqual(_carry_year("cac thang 3 4 5 nam 2026"),
                         "thang 3/2026 thang 4/2026 thang 5/2026")
        self.assertEqual(_carry_year("ngay 2 thang 5/2026"), "ngay 2 thang 5/2026")
        expected = ("series", [(2026, 3), (2026, 4), (2026, 5)])
        for text in ("dthu th3 th4 tháng 5/2026 lần lượt bao nhiêu",
                     "doanh thu tháng 3 4 5/2026 mỗi tháng được bao nhiêu",
                     "doanh thu lần lượt các tháng 3 4 5 năm 2026",
                     "doanh thu tháng 3/2026 tháng 4/2026 tháng 5/2026 ghi theo đúng thứ tự giúp mình",
                     "liệt kê giúp doanh thu 3 tháng liền từ tháng 3/2026 tới tháng 5/2026 theo thứ tự thời gian",
                     "doanh thu 3 tháng liên tiếp tính đến tháng 5/2026 từng tháng bao nhiêu xếp từ tháng cũ "
                     "tới tháng mới nhất"):
            with self.subTest(text=text):
                self.assertEqual(self.months(text), expected)
        self.assertEqual(self.months("th3 th4 tháng 5/2026 tháng nào bán được nhiều tiền nhất"),
                         ("best_period", [(2026, 3), (2026, 4), (2026, 5)]))
        self.assertEqual(self.months("dthu th11 th12 tháng 1/2026 lần lượt"),
                         ("series", [(2025, 11), (2025, 12), (2026, 1)]))


class CustomerShareTests(unittest.TestCase):
    def test_denominator_wordings(self):
        for text in ("khách mua từ 4 đơn trở lên tháng 5/2026 chiếm mấy phần trăm tất cả khách đã mua tháng đó",
                     "tháng 5/2026 khách có từ 4 đơn trở lên chiếm bao nhiêu % tổng số khách mua",
                     "khách có mua trong tháng 5/2026 bao nhiêu phần trăm mua ít nhất 4 đơn",
                     "tháng 5/2026 bao nhiêu % khách mua từ 4 đơn trở lên tính trên số khách có mua",
                     "trong tất cả các khách có mua tháng 5/2026, khách mua ít nhất 4 đơn chiếm mấy %"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result, text)
                self.assertEqual((result.intent, result.comparison, result.threshold.value),
                                 ("count_where", "share", Decimal(4)))
        result = plan("tháng 5/2026 có bao nhiêu khách mua từ 4 đơn trở lên")
        self.assertEqual((result.comparison, result.threshold.value), ("", Decimal(4)))


class ClosingParticleTests(unittest.TestCase):
    def test_accented_regional_particles_close_the_question(self):
        self.assertEqual(normalize_question("doanh thu tháng 5/2026 bao nhiêu rứa?"),
                         "doanh thu thang 5/2026 bao nhieu")
        self.assertEqual(normalize_question("doanh thu tháng 5/2026 được nhiêu hè"),
                         "doanh thu thang 5/2026 duoc nhieu")
        # Unaccented "rua" may be "rửa" (a wash): it stays.
        self.assertTrue(normalize_question("tháng 5/2026 bao nhiêu phiếu rua").endswith("rua"))


if __name__ == "__main__":
    unittest.main()
