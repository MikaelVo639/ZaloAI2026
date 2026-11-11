"""Rankings whose measure a rulebook row defines: loyal, big-spending and fickle customers,
the most popular product, the busiest day and the peak hour.

Rulebook rows and sales below are synthetic, written for tests.
"""
import unittest
from datetime import date
from decimal import Decimal

from test_new_intents import ROWS as INTENT_ROWS
from test_sale_context_fields import _Shop
from test_sem02_grammar import rulebook

from datamate.agent.baseline import BaselineAgent
from datamate.agent.compute import (
    customer_rulebook_ranking,
    daily_orders,
    hourly_orders,
    product_order_ranking,
    unsupported,
)
from datamate.contracts import BusinessContext, Plan, Question, Split, Threshold
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

RANKING_ROWS = {
    "loyal": "| Khách trung thành nhất | khách có **nhiều ngày có mua nhất** trong kỳ |",
    "big": ("| Khách sộp nhất | khách có **giá trị trung bình một đơn** cao nhất trong kỳ, "
            "chỉ xét khách có **từ 3 đơn trở lên** trong kỳ |"),
    "fickle": "| Khách hay đổi ý nhất | khách có **nhiều đơn huỷ nhất** trong kỳ |",
    "popular": "| Mặt hàng được ưa chuộng nhất | mặt hàng có mặt trong **nhiều đơn nhất** trong kỳ |",
    "busiest": "| Ngày đắt khách nhất | ngày có **nhiều đơn nhất** trong kỳ |",
    "peak": ("| Giờ cao điểm | khung **một giờ tròn** theo giờ tạo đơn (từ h giờ đến trước h + 1 "
             "giờ) có nhiều đơn nhất trong kỳ. Đáp án là **giờ bắt đầu khung**, type `number` |"),
}


def book(*skip):
    rows = [row for key, row in RANKING_ROWS.items() if key not in skip]
    return rulebook() + "\n".join([INTENT_ROWS["cancelled"], *rows]) + "\n"


def plan(text, text_book=None):
    rules = read_rules(book() if text_book is None else text_book)
    return plan_question(Question("x", "b", text, date(2026, 4, 10)), rules)


def sale(order, day, customer=None, status="valid", amount=100, product="P1", name=None,
         minute=None):
    return Sale(order, date(2026, 3, day), status, Decimal(amount), product, f"Hàng {product}",
                Decimal(1), customer_id=customer, customer_name=name or customer, minute=minute)


class RulebookRankingGrammarTests(unittest.TestCase):
    def test_rows_enable_each_ranking(self):
        rules = read_rules(book())
        self.assertEqual((rules.loyal_customer, rules.big_spender_orders, rules.fickle_customer,
                          rules.popular_product, rules.busiest_day, rules.peak_hour),
                         (True, 3, True, True, True, True))
        other = book("big") + RANKING_ROWS["big"].replace("từ 3 đơn", "nhiều đơn") + "\n"
        self.assertEqual(read_rules(other).big_spender_orders, 0)

    def test_questions_plan_the_rulebook_measure(self):
        for text, intent, rank_by in (
            ("Tháng 3/2026, trong các khách hàng, ai trung thành nhất?", "top_customers",
             "purchase_days"),
            ("Khách sộp nhất tháng 3/2026 là ai?", "top_customers", "average_order"),
            ("Trong tháng 3/2026, ai là khách hay đổi ý nhất của cửa hàng?", "top_customers",
             "cancelled_orders"),
            ("Xem giúp mình tháng 3/2026, hàng nào được khách ưa chuộng nhất?", "top_products",
             "order_count"),
            ("Tháng 3/2026 ngày nào cửa hàng đắt khách nhất?", "best_day", "order_count"),
            ("Trong tháng 3/2026, giờ cao điểm trong ngày của cửa hàng là mấy giờ?", "peak_hour",
             ""),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.rank_by, result.top_k),
                                 (intent, rank_by, 1))
                self.assertEqual(unsupported(result), "")
        big = plan("Khách sộp nhất tháng 3/2026 là ai?")
        self.assertEqual(big.threshold, Threshold("customer", "order_count", ">=", Decimal(3),
                                                  RANKING_ROWS["big"]))

    def test_without_the_row_or_a_period_abstain(self):
        for text, row in (("Tháng 3/2026 ai trung thành nhất?", "loyal"),
                          ("Khách sộp nhất tháng 3/2026 là ai?", "big"),
                          ("Tháng 3/2026 khách nào hay đổi ý nhất?", "fickle"),
                          ("Tháng 3/2026 hàng nào được khách ưa chuộng nhất?", "popular"),
                          ("Tháng 3/2026 ngày nào đắt khách nhất?", "busiest"),
                          ("Tháng 3/2026 giờ cao điểm là mấy giờ?", "peak")):
            with self.subTest(text=text):
                self.assertIsNotNone(plan(text))
                self.assertIsNone(plan(text, book(row)))
        self.assertIsNone(plan("Tiệm mình thường đông khách nhất vào mấy giờ?"))
        # A measure the rulebook does not define is not guessed.
        self.assertIsNone(plan("Tháng 3/2026 khách nào thân thiện nhất?"))

    def test_unsupported_plan_fields(self):
        window = (date(2026, 3, 1), date(2026, 3, 31))
        self.assertTrue(unsupported(Plan("top_customers", *window, rank_by="average_order")))
        self.assertTrue(unsupported(Plan("best_day", *window, top_k=2, rank_by="order_count")))
        self.assertTrue(unsupported(Plan("peak_hour", *window, rank_by="order_count")))


class RulebookRankingComputeTests(unittest.TestCase):
    def test_loyal_counts_days_with_a_valid_order(self):
        rows = [sale("A1", 1, "KH2"), sale("A2", 1, "KH2"), sale("A3", 1, "KH2"),  # one day
                sale("B1", 1, "KH1"), sale("B2", 2, "KH1"),
                sale("C1", 3, "KH3", "cancelled"), sale("C2", 4, "KH3", "returned"),
                sale("C3", 5, "KH3")]
        ranking = customer_rulebook_ranking(rows, "purchase_days")
        self.assertEqual([(name, value) for _, name, value in ranking],
                         [("KH1", 2), ("KH2", 1), ("KH3", 1)])  # ties by code

    def test_big_spender_needs_the_minimum_orders(self):
        rows = [sale("A1", 1, "KH1", amount=900), sale("A2", 2, "KH1", amount=900),
                sale("B1", 1, "KH2", amount=300), sale("B2", 2, "KH2", amount=200),
                sale("B3", 3, "KH2", amount=100), sale("B4", 3, "KH2", amount=999,
                                                         status="cancelled")]
        ranking = customer_rulebook_ranking(rows, "average_order", 3)
        self.assertEqual([(name, value) for _, name, value in ranking], [("KH2", Decimal(200))])

    def test_fickle_counts_cancelled_orders_once(self):
        rows = [sale("A1", 1, "KH1", "cancelled"), sale("A1", 1, "KH1", "cancelled", product="P2"),
                sale("B1", 1, "KH2", "cancelled"), sale("B2", 2, "KH2", "cancelled"),
                sale("C1", 1, "KH3", "returned"), sale("C2", 1, "KH3")]
        ranking = customer_rulebook_ranking(rows, "cancelled_orders")
        self.assertEqual([(name, value) for _, name, value in ranking], [("KH2", 2), ("KH1", 1)])

    def test_display_name_is_the_most_written(self):
        rows = [sale("A1", 1, "KH1", name="Lan 0901234567"), sale("A2", 2, "KH1", name="Lan"),
                sale("A3", 3, "KH1", name="Lan")]
        self.assertEqual(customer_rulebook_ranking(rows, "purchase_days")[0][1], "Lan")

    def test_popular_product_counts_orders_not_quantity(self):
        rows = [sale("A1", 1, product="P2"), sale("A1", 1, product="P2"),
                sale("A2", 1, product="P1"), sale("A3", 1, product="P1"),
                sale("A4", 1, product="P3"), sale("A5", 1, product="P2")]
        self.assertEqual([(key, value) for key, _, value in product_order_ranking(rows)],
                         [("P1", 2), ("P2", 2), ("P3", 1)])  # ties by id

    def test_busiest_day_and_peak_hour(self):
        rows = [sale("A1", 1, minute=8 * 60 + 59), sale("A1", 1, minute=8 * 60 + 59),
                sale("A2", 1, minute=9 * 60), sale("A3", 2, minute=9 * 60 + 30)]
        self.assertEqual(daily_orders(rows), [(date(2026, 3, 1), 2), (date(2026, 3, 2), 1)])
        self.assertEqual(hourly_orders(rows), [(9, 2), (8, 1)])
        self.assertIsNone(hourly_orders(rows + [sale("A4", 2)]))  # an order without a time


class CustomerIdentityTests(_Shop):
    def context(self, business, *, coded, earlier_code=None):
        readme = """\
## Cột trong `sales.csv`
- **CT**: mã đơn
- **Ngày**: ngày bán
- **Xử lý**: trạng thái đơn
- **Mã SP**: mã hàng
- **Tên SP**: tên hàng
- **SL**: số lượng đã đổi ra đơn vị cơ bản
- **Tiền**: thành tiền sau giảm
- **Tên KH**: tên khách
"""
        header = ["CT", "Ngày", "Xử lý", "Mã SP", "Tên SP", "SL", "Tiền", "Tên KH"]
        rows = [
            ["A", "2026-02-28", "completed", "P1", "Bánh mì", 1, 100, "Lan"],
            ["B", "2026-03-01", "completed", "P1", "Bánh mì", 1, 100, "Bình"],
            ["C", "2026-03-02", "completed", "P1", "Bánh mì", 1, 100, "Bình"],
            ["D", "2026-03-02", "completed", "P1", "Bánh mì", 1, 100, "Bình"],
            ["E", "2026-03-03", "cancelled", "P1", "Bánh mì", 1, 100, "Bình"],
        ]
        if coded:
            readme += "- **Mã KH**: mã khách (trống nếu khách lẻ)\n"
            header.append("Mã KH")
            for row, code in zip(rows, (earlier_code, None, None, None, None)):
                row.append(code)
        catalog = self.shop(readme, {"sales.csv": [header, *rows]}, business=business)
        rules = book() + (
            "| Trạng thái đơn | mỗi đơn có đúng một trong bốn trạng thái: "
            "hoàn thành, huỷ, trả hàng, đang giao |\n"
        )
        split = Split(self.data.parent, (), rules, self.data.parent / "calendar.csv")
        return BusinessContext(split, catalog)

    def test_identity_mode_survives_date_and_status_filters(self):
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        for business, coded, earlier_code in (
            ("code-outside-period", True, "KH1"),
            ("code-column-all-blank", True, None),
            ("name-only", False, None),
        ):
            context = self.context(business, coded=coded, earlier_code=earlier_code)
            for text in (
                "Tháng 3/2026 ai là khách trung thành nhất?",
                "Tháng 3/2026 khách sộp nhất là ai?",
                "Tháng 3/2026 ai là khách hay đổi ý nhất?",
                "Tháng 3/2026 khách nào mua nhiều tiền nhất?",
            ):
                with self.subTest(business=business, text=text):
                    question = Question("q", business, text, date(2026, 4, 10))
                    result = agent.answer(question, context)
                    expected = ("abstain", None) if coded else ("string", "Bình")
                    self.assertEqual((result.type, result.answer), expected, result.reason)
                    self.assertIs(result.evidence["inputs"]["uses_customer_codes"], coded)
            question = Question("q", business,
                                "Tháng 3/2026 có bao nhiêu khách mua từ 2 đơn trở lên?",
                                date(2026, 4, 10))
            result = agent.answer(question, context)
            self.assertEqual((result.type, result.answer), ("number", 0 if coded else 1),
                             result.reason)


if __name__ == "__main__":
    unittest.main()
