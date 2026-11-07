"""S01: grammar for the C01 intents revenue_increasing (boolean) and top_debt_customers (table).

Contract: docs/AGENT_02_CONTRACT.md §4-§5. Rulebook rows are synthetic paraphrases written for
tests, not organizer data. The executor is A01's work: until then every Plan below must be
refused by the current backend, so these questions keep abstaining.
"""
import unittest
from datetime import date

from datamate.agent import compute
from datamate.contracts import SNAPSHOT_INTENTS, Catalog, Question
from datamate.demo import RULEBOOK
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

ROWS = {
    "range": "| Từ X đến Y | Gồm cả X và Y |",
    "month": "| Tháng M/Y | Cả tháng, từ ngày 1 đến hết ngày cuối tháng |",
    "boolean": "| boolean | `true` / `false` |",
    "table": '| table | `{"columns": [...], "rows": [[...]]}`, ô số là JSON number |',
    "until": "| Đến hết ngày X | Tình trạng lúc hết ngày đó, tính mọi phát sinh từ đầu dữ liệu |",
    "debt_orders": "| Đơn được tính vào nợ | Đơn không huỷ; đơn trả hàng vẫn tính nếu còn nợ |",
    "debt_at": ("| Nợ một đơn tại ngày X | Phần chưa trả của tổng đơn, gồm phí giao, trừ các "
                "khoản trả đến hết ngày X |"),
    "debt_customer": ("| Nợ theo khách | Đơn khách lẻ vẫn vào tổng nợ nhưng không có dòng "
                      "trong bảng |"),
    "debt_excluded": "| Đơn COD, đơn bán qua sàn TMĐT | Không phải khách nợ, bỏ khỏi công nợ |",
    "customers": ("| Xác định khách | Có mã khách thì mỗi mã là một khách; khách lẻ không tính. "
                  "Không có mã thì theo tên ghi trên đơn, tên chỉ khác cách viết là một khách |"),
}
TIES = "- Bằng nhau: xếp theo mã tăng dần (không có mã thì theo tên)."


def rulebook(*skip: str, extra: str = "") -> str:
    table = "\n".join(row for key, row in ROWS.items() if key not in skip)
    ties = "" if "ties" in skip else TIES
    return RULEBOOK.rstrip("\n") + "\n" + table + "\n" + extra + "\n\n" + ties + "\n"


def plan(text, today="2026-07-15", book=None, names=()):
    rules = read_rules(rulebook() if book is None else book)
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules, names)


def months(result):
    return [(start.year, start.month) for start, _ in result.periods]


class RevenueIncreasingTests(unittest.TestCase):
    def test_wordings_give_one_month_per_period_in_time_order(self):
        for text in (
                "Doanh thu 3 tháng 2/2026, 3/2026, 4/2026 có tăng liên tục không?",
                "dthu có tăng liên tục qua 3 tháng 2/2026 3/2026 4/2026 khoong",
                "Từ tháng 2/2026 qua tháng 3/2026 rồi tháng 4/2026 doanh thu có tăng liên tục ko",
                "Từ tháng 2/2026 đến tháng 4/2026 doanh thu tháng sau có luôn cao hơn tháng trước "
                "không?",
                "Xu hướng doanh thu 3 tháng 2/2026, 3/2026, 4/2026: có phải tháng 3/2026 cao hơn "
                "tháng 2/2026 và tháng 4/2026 cao hơn tháng 3/2026 nhỉ?"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual(result.intent, "revenue_increasing")
                self.assertEqual(months(result), [(2026, 2), (2026, 3), (2026, 4)])
                self.assertEqual((result.start, result.end), (date(2026, 2, 1), date(2026, 4, 30)))
                self.assertEqual((result.filters, result.comparison, result.shape),
                                 ((), "", ""))
                self.assertIn(ROWS["boolean"], result.evidence)
                self.assertIn(ROWS["month"], result.evidence)

    def test_year_change_and_leap_year(self):
        result = plan("doanh thu 3 tháng 12/2025 1/2026 2/2026 có tăng liên tục không")
        self.assertEqual(months(result), [(2025, 12), (2026, 1), (2026, 2)])
        leap = plan("doanh thu 3 tháng 1/2024 2/2024 3/2024 có tăng liên tục không")
        self.assertEqual(leap.periods[1], (date(2024, 2, 1), date(2024, 2, 29)))

    def test_last_month_must_be_finished_on_the_as_of_date(self):
        text = "doanh thu 3 tháng 4/2026 5/2026 6/2026 có tăng liên tục không"
        self.assertIsNotNone(plan(text, today="2026-06-30"))
        self.assertIsNone(plan(text, today="2026-06-29"))  # June still running
        self.assertIsNone(plan(text, today="2026-05-20"))  # June in the future

    def test_unclear_or_unsupported_wordings_abstain(self):
        for text in (
                "doanh thu 3 tháng 3/2026 2/2026 4/2026 có tăng liên tục không",   # not in order
                "doanh thu 3 tháng 2/2026 2/2026 4/2026 có tăng liên tục không",   # duplicate
                "doanh thu 2 tháng 2/2026 3/2026 4/2026 có tăng liên tục không",   # count differs
                "doanh thu tháng 4/2026 có tăng liên tục không",                   # one month
                "doanh thu có tăng liên tục không",                                # no month
                "doanh thu 3 tháng 2/2026 3/2026 4/2026 có giảm liên tục không",
                "số đơn 3 tháng 2/2026 3/2026 4/2026 có tăng liên tục không",
                "doanh thu 3 tháng 2/2026 3/2026 4/2026 có tăng liên tục ít nhất 10% không",
                "doanh thu tiền mặt 3 tháng 2/2026 3/2026 4/2026 có tăng liên tục không",
                "Xu hướng doanh thu 3 tháng 2/2026, 3/2026, 4/2026: có phải tháng 3/2026 cao hơn "
                "tháng 2/2026 và tháng 4/2026 cao hơn tháng 2/2026"):           # wrong pair
            with self.subTest(text=text):
                self.assertIsNone(plan(text))

    def test_a_product_filter_is_not_dropped(self):
        text = "doanh thu Trà sữa trân châu 3 tháng 2/2026 3/2026 4/2026 có tăng liên tục không"
        self.assertIsNone(plan(text, names=("Trà sữa trân châu",)))

    def test_rules_of_the_split_gate_the_intent(self):
        text = "doanh thu 3 tháng 2/2026 3/2026 4/2026 có tăng liên tục không"
        self.assertIsNone(plan(text, book=rulebook("boolean")))
        self.assertIsNone(plan(text, book=rulebook("month")))
        defined = rulebook(extra="| Tăng liên tục | Tháng sau không thấp hơn tháng trước |")
        self.assertIsNone(plan(text, book=defined))  # another meaning: do not guess
        span = "Từ tháng 2/2026 đến tháng 4/2026 doanh thu tháng sau có luôn cao hơn tháng trước"
        self.assertIsNone(plan(span, book=rulebook("range")))


class TopDebtCustomersTests(unittest.TestCase):
    def test_wordings_give_k_and_the_snapshot_day(self):
        for text, k in (
                ("Tính đến hết ngày 14/07/2026, 3 khách nào đang nợ nhiều nhất, mỗi người còn nợ "
                 "bao nhiêu?", 3),
                ("xem giúp top 5 khách có công nợ lớn nhất đến hết ngày 14/07/2026 và số nợ của "
                 "từng nguwowif", 5),
                ("Cho bảng 3 khách còn nowj nhiều nhất tính đến hết ngày 14/07/2026, kèm số tiền "
                 "còn nợ", 3),
                ("liệt kê 2 khách nợ nhiều nhất tính đến hết ngày 14/07/2026 kèm số tiền còn nợ", 2)):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual(result.intent, "top_debt_customers")
                self.assertIn(result.intent, SNAPSHOT_INTENTS)
                self.assertEqual((result.start, result.end, result.top_k),
                                 (date(2026, 7, 14), date(2026, 7, 14), k))
                for key in ("table", "until", "debt_orders", "debt_at", "debt_customer",
                            "debt_excluded", "customers"):
                    self.assertIn(ROWS[key], result.evidence)
                self.assertIn(TIES, result.evidence)

    def test_today_and_boundaries(self):
        result = plan("3 khách nào đang nợ nhiều nhất tính đến hôm nay, mỗi người còn nợ bao "
                      "nhiêu", today="2026-07-15")
        self.assertEqual((result.start, result.end), (date(2026, 7, 15), date(2026, 7, 15)))
        same_day = "3 khách nợ nhiều nhất đến hết ngày 15/07/2026 kèm số tiền còn nợ"
        self.assertIsNotNone(plan(same_day, today="2026-07-15"))
        self.assertIsNone(plan(same_day, today="2026-07-14"))  # the day has not ended yet
        self.assertIsNone(plan("3 khách nợ nhiều nhất đến hết ngày 31/02/2026 kèm số tiền còn nợ"))

    def test_unclear_or_unsupported_wordings_abstain(self):
        for text in (
                "3 khách nào đang nợ nhiều nhất tính đến hết ngày 14/07/2026",   # no amounts asked
                "3 khách nào đang nợ nhiều nhất, mỗi người còn nợ bao nhiêu",     # no snapshot day
                "khách nào nợ nhiều nhất tính đến hết ngày 14/07/2026",           # no k, not a table
                "0 khách nợ nhiều nhất đến hết ngày 14/07/2026 kèm số tiền còn nợ",
                "60 khách nợ nhiều nhất đến hết ngày 14/07/2026 kèm số tiền còn nợ",
                "3 khách ở chi nhánh 2 nợ nhiều nhất đến hết ngày 14/07/2026 kèm số tiền còn nợ",
                "3 khách nợ nhiều nhất đến hết ngày 14/07/2026 kèm số tiền còn nợ và hạn trả"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))

    def test_other_debt_questions_are_not_tables(self):
        # C01 §7 intents are not in S01 yet: these keep abstaining instead of becoming a table.
        for text in ("Tổng công nợ đến hết ngày 14/07/2026 là bao nhiêu?",
                     "Có bao nhiêu khách còn nợ tính đến hết ngày 14/07/2026?"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))

    def test_rules_of_the_split_gate_the_intent(self):
        text = "3 khách nợ nhiều nhất đến hết ngày 14/07/2026 kèm số tiền còn nợ"
        for missing in ("table", "until", "debt_orders", "debt_at", "debt_customer",
                        "debt_excluded", "customers", "ties"):
            with self.subTest(missing=missing):
                self.assertIsNone(plan(text, book=rulebook(missing)))


class CurrentBackendTests(unittest.TestCase):
    def test_new_plans_abstain_until_the_executor_exists(self):
        catalog = Catalog("b", "", ())
        for text in ("doanh thu 3 tháng 2/2026 3/2026 4/2026 có tăng liên tục không",
                     "3 khách nợ nhiều nhất đến hết ngày 14/07/2026 kèm số tiền còn nợ"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertTrue(compute.unsupported(result))
                self.assertEqual(compute.answer_plan({}, catalog, result).type, "abstain")

    def test_sem02_month_series_is_unchanged(self):
        sentence = "Câu hỏi nhiều số thì ghi list theo đúng thứ tự câu hỏi nêu."
        book = rulebook(extra="") + sentence + "\n"
        result = plan("Doanh thu từng tháng của 3 tháng 11/2025, 12/2025, 1/2026 lần lượt là bao "
                      "nhiêu?", book=book)
        self.assertEqual((result.intent, result.shape, len(result.periods)),
                         ("revenue", "series", 3))


if __name__ == "__main__":
    unittest.main()
