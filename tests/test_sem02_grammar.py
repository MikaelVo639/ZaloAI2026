"""SEM-02: several months, customers, quantity in a base unit, year-to-date wording.

Rulebook rows are synthetic paraphrases written for tests, not organizer data.
"""
import unittest
from datetime import date
from decimal import Decimal

from datamate.agent import compute
from datamate.contracts import Catalog, Filter, Plan, Question, Threshold
from datamate.demo import RULEBOOK
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

ROWS = {
    "range": "| Từ X đến Y | Gồm cả X và Y |",
    "ytd": "| Từ đầu năm | Từ 01/01 đến hết hôm nay |",
    "threshold": "| Quá N, trên N, dưới N | Không gồm N. Từ N trở lên thì gồm N |",
    "orders": "| Số đơn | Bỏ đơn huỷ, trả. Khách đã mua là khách có ít nhất một đơn như vậy |",
    "customers": ("| Xác định khách | Có mã khách thì mỗi mã là một khách; khách lẻ không tính. "
                  "Không có mã thì theo tên ghi trên đơn, tên chỉ khác cách viết là một khách. "
                  "Khách mua nhiều tiền nhất xếp theo doanh thu |"),
    "unit": ("| Bán được bao nhiêu <đơn vị> | Cộng số lượng của mặt hàng có đơn vị cơ bản là "
             "<đơn vị>; mặt hàng có đơn vị cơ bản khác không tính |"),
    "date": '| date | "YYYY-MM-DD"; câu hỏi theo tháng: "YYYY-MM" |',
    "names": "| Tên gọi | Các tên cùng chỉ một thứ là cùng một thứ. File có mã thì theo mã |",
}
SENTENCES = {
    "list": "Câu hỏi nhiều số thì ghi list theo đúng thứ tự câu hỏi nêu.",
    "ties": "- Bằng nhau: xếp theo mã tăng dần (không có mã thì theo tên).",
}


def rulebook(*skip: str) -> str:
    table = "\n".join(row for key, row in ROWS.items() if key not in skip)
    text = "\n".join(line for key, line in SENTENCES.items() if key not in skip)
    return RULEBOOK.rstrip("\n") + "\n" + table + "\n\n" + text + "\n"


def plan(text, today="2026-07-15", book=None, names=()):
    rules = read_rules(rulebook() if book is None else book)
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules, names)


class MonthsGrammarTests(unittest.TestCase):
    def test_series_of_months_in_question_order(self):
        for text, first, last in (
                ("dthu 3 tháng liền nhau kết thúc ở tháng 2/2026 là bnhiu mỗi tháng, từ tháng cũ "
                 "đến tháng mới", (2025, 12), (2026, 2)),
                ("Doanh thu từng tháng của 3 tháng 11/2025, 12/2025, 1/2026 lần lượt là bao nhiêu?",
                 (2025, 11), (2026, 1)),
                ("Từ tháng 4/2025 đến tháng 6/2025 doanh thu mỗi tháng là bao nhiêu, ghi 3 số theo "
                 "thứ tự thời gian", (2025, 4), (2025, 6))):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.shape, len(result.periods)),
                                 ("revenue", "series", 3))
                self.assertEqual(result.periods[0][0], date(*first, 1))
                self.assertEqual(result.periods[-1][0], date(*last, 1))

    def test_best_month(self):
        for text in ("xét 3 tháng 1/2026, 2/2026 và 3/2026, doanh thu tháng nào lớn nhất",
                     "Từ tháng 1/2026 tới tháng 3/2026 tháng nào cửa hàng bán được nhiều tiền nhất?",
                     "trong các tháng 1/2026 2/2026 và 3/2026 cửa hàng có dthu cao nhất vào tháng nào"):
            with self.subTest(text=text):
                book = rulebook() + ("| Bán được bao nhiêu tiền | Là doanh thu, không phải tiền "
                                     "khách đã trả |\n")
                result = plan(text, book=book)
                self.assertEqual((result.shape, len(result.periods)), ("best_period", 3))

    def test_months_abstain_without_rule_or_when_unclear(self):
        text = "Doanh thu từng tháng của 3 tháng 11/2025, 12/2025, 1/2026 lần lượt là bao nhiêu?"
        self.assertIsNone(plan(text, book=rulebook("list")))
        self.assertIsNone(plan("xét 3 tháng 1/2026, 2/2026 và 3/2026, doanh thu tháng nào lớn nhất",
                               book=rulebook("date")))
        # "tăng liên tục" has no rule; the stated count must match; running months abstain.
        self.assertIsNone(plan("dthu 3 tháng 4/2026 5/2026 6/2026 có tăng liên tục không"))
        self.assertIsNone(plan("Doanh thu từng tháng của 2 tháng 11/2025, 12/2025, 1/2026 lần lượt"))
        self.assertIsNone(plan("doanh thu từng tháng của 3 tháng 5/2026, 6/2026, 7/2026 lần lượt",
                               today="2026-07-15"))


class CustomerGrammarTests(unittest.TestCase):
    def test_top_customers(self):
        cases = {
            "5 khách hàng có tổng tiền mua cao nhất tháng 1/2025 là ai": (5, "revenue"),
            "top 3 khach hang mua nhiu tien nhat thang 1/2026 la ai": (3, "revenue"),
            "Khách nào mua nhiều đơn hàng nhất tháng 10/2025?": (1, "order_count"),
            "ai là khách hàng chi tiêu nhiều nhất trong tháng 2/2025": (1, "revenue"),
            "liệt kê 3 khách chi tiêu nhiều nhất tuần rồi": (3, "revenue"),
        }
        for text, (k, by) in cases.items():
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.top_k, result.rank_by),
                                 ("top_customers", k, by))

    def test_customer_ranking_in_everyday_questions(self):
        for text, count, rank in (
            ("Tháng 4/2025 ai mua hàng nhiều tiền nhất vậy?", 1, "revenue"),
            ("Tháng 6/2025 khách nào mua nhiều tiền nhất?", 1, "revenue"),
            ("Tháng 8/2026 khách nào mua nhiều đơn nhất?", 1, "order_count"),
            ("Tháng 1/2026 ai là khách mua nhiều lần nhất, tính theo số đơn?", 1, "order_count"),
            ("Cho hỏi ai là khách có số đơn mua nhiều nhất tháng 1/2026?", 1, "order_count"),
            ("Liệt kê 5 khách chi tiêu nhiều nhất tháng 1/2026.", 5, "revenue"),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result)
                self.assertEqual((result.intent, result.top_k, result.rank_by),
                                 ("top_customers", count, rank))

    def test_customer_percentage_keeps_the_denominator(self):
        for text, minimum in (
            ("Trong số khách có mua tháng 1/2026, tỉ lệ khách mua ít nhất 4 đơn là bao nhiêu %?", 4),
            ("Bao nhiêu % khách trong tháng 1/2026 mua từ 6 đơn trở lên?", 6),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertIsNotNone(result)
                self.assertEqual((result.intent, result.comparison), ("count_where", "share"))
                self.assertEqual(result.threshold.value, minimum)

    def test_new_customer_phrasings_preserve_scope_and_required_rules(self):
        for text in (
            "Tháng 1/2026 ai mua hàng nhiều tiền nhất, chỉ tính khách sỉ?",
            "Ai là khách mua nhiều lần nhất tháng 1/2026?",
            "Ai mua hàng nhiều tiền nhất tháng 1/2026 và tháng 2/2026?",
            "Khách mua ít nhất 3 đơn chiếm bao nhiêu % doanh thu tháng 1/2026?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))
        self.assertIsNone(plan("Khách nào mua nhiều tiền nhất tháng 1/2026 size M?"))
        count = plan("Tháng 8/2025 có nhiêu khách mua từ 12 đơn trở leen?")
        self.assertEqual(count.threshold.value, 12)
        self.assertIsNone(plan("Tháng 1/2026 ai mua hàng nhiều tiền nhất?",
                               book=rulebook("customers")))

    def test_customers_with_at_least_n_orders(self):
        count = plan("tháng 1/2026 có mấy khách mua từ 5 đơn trở lên")
        self.assertEqual((count.intent, count.comparison), ("count_where", ""))
        self.assertEqual(count.threshold, Threshold("customer", "order_count", ">=", Decimal(5),
                                                    ROWS["threshold"]))
        share = plan("Trong số khách đã mua hàng tháng 5/2026 bao nhiêu phần trăm mua từ 4 đơn trở lên?")
        self.assertEqual((share.intent, share.comparison, share.threshold.value),
                         ("count_where", "share", 4))
        self.assertEqual(plan("trong tháng 12/2025 số khách mua ít nhất 3 đơn là bao nhiêu")
                         .threshold.value, 3)

    def test_customer_questions_need_their_rules(self):
        self.assertIsNone(plan("top 3 khách mua nhiều tiền nhất tháng 1/2026", book=rulebook("customers")))
        self.assertIsNone(plan("top 3 khách mua nhiều tiền nhất tháng 1/2026", book=rulebook("ties")))
        self.assertIsNone(plan("tháng 1/2026 có mấy khách mua từ 5 đơn trở lên",
                               book=rulebook("threshold")))
        # A share of something other than "the customers who bought" is not defined here.
        self.assertIsNone(plan("khách mua từ 3 đơn trở lên chiếm bao nhiêu phần trăm doanh thu tháng 1/2026"))


class ThresholdGrammarTests(unittest.TestCase):
    def test_orders_days_and_products_over_a_threshold(self):
        cases = {
            "Tháng 12/2025 có mấy đơn có dthu từ 170.000 đồng trở lên?": ("order", 170000),
            "Đếm số ngày trong tháng 5/2026 mà doanh thu cả ngày từ 5.500.000 đồng trở lên":
                ("day", 5500000),
            "Tháng 6/2025 có mấy ngày doanh thu đạt 9.800.000 đồng trở lên?": ("day", 9800000),
            "tháng 3/2026 có mấy mặt hàng được mua ở 84 đơn khác nhau trở lên":
                ("product", 84),
        }
        for text, (subject, value) in cases.items():
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.threshold.subject, result.threshold.value),
                                 ("count_where", subject, value))
        self.assertIsNone(plan("Tháng 12/2025 có mấy đơn có dthu từ 170.000 đồng trở lên?",
                               book=rulebook("threshold")))
        # "bán được từ X đồng" needs the rule that "bán được ... tiền" is revenue.
        self.assertIsNone(plan("có mấy ngày trong tháng 3/2026 cửa hàng bán được từ 5.000.000 "
                               "đồng trở lên"))


class QuantityAndPeriodGrammarTests(unittest.TestCase):
    def test_quantity_in_a_unit(self):
        names = ("Sinh tố bơ (M)", "Thép cuộn phi 6")
        result = plan("Tháng 6/2025 bán ra bao nhiêu kg Thép cuộn phi 6 vậy?", names=names)
        self.assertEqual((result.intent, result.unit, result.filters[0].value),
                         ("quantity", "kg", "Thép cuộn phi 6"))
        self.assertEqual(plan("Tuần trước tổng cộng bán được bao nhiêu ly?").unit, "ly")
        self.assertEqual(plan("số ly Sinh tố bơ (M) bán được trong tháng 5/2026 là bao nhiêu",
                              names=names).unit, "ly")
        self.assertIsNone(plan("Tuần trước bán được bao nhiêu ly?", book=rulebook("unit")))
        # "bán được bao nhiêu đơn" stays an order count.
        self.assertEqual(plan("tháng 5/2026 bán được bao nhiêu đơn").intent, "order_count")

    def test_year_to_date_wordings(self):
        for text in ("Tính từ ngày 1/1 đến hết hôm nay, dthu năm nay là bao nhiêu?",
                     "năm nay tính từ đầu năm đến hết hôm nay dthu của cửa hàng là bao nhiêu",
                     "từ 1/1 năm nay đến hết hôm nay đã bán được bao nhiêu đơn"):
            with self.subTest(text=text):
                result = plan(text, today="2026-06-02")
                self.assertEqual((result.start, result.end), (date(2026, 1, 1), date(2026, 6, 2)))
        self.assertIsNone(plan("Tính từ ngày 1/1 đến hết hôm nay, dthu năm nay là bao nhiêu?",
                               book=rulebook("ytd")))


def sale(order, day, amount, status="valid", product="P1", name="Trà", quantity=1,
         unit="ly", customer_id=None, customer_name=None):
    return Sale(order, day, status, Decimal(amount), product, name, Decimal(quantity),
                base_unit=unit, customer_id=customer_id, customer_name=customer_name)


class ComputeTests(unittest.TestCase):
    def answer(self, plan_, sales, readme=""):
        catalog = Catalog("b", readme, ())
        cache = {"sales_key": compute._catalog_key(catalog), "sales_rulebook": "",
                 "sales": (sales, {"tables": [], "sales_rows": len(sales)}),
                 "ranking_eligible": None}
        return compute.answer_plan(cache, catalog, plan_)

    def test_series_and_best_period(self):
        sales = [sale("A", date(2026, 1, 5), 100), sale("B", date(2026, 2, 5), 300),
                 sale("C", date(2026, 3, 5), 200), sale("D", date(2026, 3, 6), 999, "cancelled")]
        periods = ((date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 28)),
                   (date(2026, 3, 1), date(2026, 3, 31)))
        series = self.answer(Plan("revenue", date(2026, 1, 1), date(2026, 3, 31), periods=periods,
                                  shape="series"), sales)
        self.assertEqual((series.type, series.answer), ("list", [100, 300, 200]))
        best = self.answer(Plan("revenue", date(2026, 1, 1), date(2026, 3, 31), periods=periods,
                                shape="best_period"), sales)
        self.assertEqual((best.type, best.answer), ("date", "2026-02"))
        tie = sales + [sale("E", date(2026, 3, 7), 100)]
        self.assertEqual(self.answer(Plan("revenue", date(2026, 1, 1), date(2026, 3, 31),
                                          periods=periods, shape="best_period"), tie).type,
                         "abstain")

    def test_customers(self):
        day = date(2026, 1, 5)
        sales = [sale("A", day, 500, customer_id="K2", customer_name="Chị Lan"),
                 sale("B", day, 300, customer_id="K1", customer_name="Anh Hùng"),
                 sale("C", day, 200, customer_id="K1", customer_name="Anh Hùng"),
                 sale("D", day, 900),  # walk-in: not a customer
                 sale("E", day, 700, "returned", customer_id="K3", customer_name="Cô Ba")]
        window = (date(2026, 1, 1), date(2026, 1, 31))
        top = self.answer(Plan("top_customers", *window, top_k=2, rank_by="revenue"), sales)
        # 500 each: tie broken by code, K1 before K2.
        self.assertEqual((top.type, top.answer), ("list", ["Anh Hùng", "Chị Lan"]))
        most_orders = self.answer(Plan("top_customers", *window, rank_by="order_count"), sales)
        self.assertEqual((most_orders.type, most_orders.answer), ("string", "Anh Hùng"))
        rule = Threshold("customer", "order_count", ">=", Decimal(2))
        count = self.answer(Plan("count_where", *window, threshold=rule), sales)
        self.assertEqual((count.type, count.answer), ("number", 1))
        share = self.answer(Plan("count_where", *window, threshold=rule, comparison="share"),
                            sales)
        self.assertEqual((share.type, share.answer), ("percent", 50.0))
        no_customers = [sale("A", day, 100)]
        self.assertEqual(self.answer(Plan("top_customers", *window, rank_by="revenue"),
                                     no_customers).type, "abstain")

    def test_customers_by_name_when_the_shop_has_no_codes(self):
        day = date(2026, 1, 5)
        sales = [sale("A", day, 100, customer_name="Chị  Lan"), sale("B", day, 100, customer_name="chị lan"),
                 sale("C", day, 150, customer_name="Khách lẻ"), sale("D", day, 150, customer_name="Cô Ba")]
        top = self.answer(Plan("top_customers", date(2026, 1, 1), date(2026, 1, 31),
                               rank_by="revenue"), sales)
        self.assertEqual(top.answer, "Chị  Lan")  # 200 for the two spellings of one name

    def test_quantity_in_base_unit(self):
        day = date(2026, 1, 5)
        sales = [sale("A", day, 100, quantity=24, unit="lon", name="Bia", product="P1"),
                 sale("B", day, 100, quantity=2, unit="kg", name="Đường", product="P2"),
                 sale("C", day, 100, quantity=6, unit="lon", name="Bia", product="P1")]
        window = (date(2026, 1, 1), date(2026, 1, 31))
        total = self.answer(Plan("quantity", *window, unit="lon"), sales)
        self.assertEqual((total.type, total.answer), ("number", 30))
        one = self.answer(Plan("quantity", *window, unit="kg",
                               filters=(Filter("product", "Đường"),)), sales)
        self.assertEqual(one.answer, 2)
        self.assertEqual(self.answer(Plan("quantity", *window, unit="hop"), sales).type, "abstain")
        unknown = sales + [Sale("D", day, "valid", Decimal(1), "P3", "Muối", Decimal(1))]
        self.assertEqual(self.answer(Plan("quantity", *window, unit="lon"), unknown).type, "abstain")

    def test_unsupported_combinations_abstain(self):
        window = (date(2026, 1, 1), date(2026, 1, 31))
        self.assertTrue(compute.unsupported(Plan("count_where", *window)))
        self.assertTrue(compute.unsupported(Plan("revenue", *window, shape="series")))
        self.assertTrue(compute.unsupported(Plan("revenue", *window, comparison="share")))
        self.assertTrue(compute.unsupported(Plan("top_customers", *window)))
        self.assertTrue(compute.unsupported(Plan("revenue", *window, unit="ly")))
        self.assertTrue(compute.unsupported(Plan(
            "count_where", *window, threshold=Threshold("order", "revenue", ">", Decimal(1)))))
        self.assertTrue(compute.unsupported(Plan(
            "count_where", *window, threshold=Threshold("staff", "revenue", ">=", Decimal(1)))))
        self.assertTrue(compute.unsupported(Plan(
            "count_where", *window, threshold=Threshold("day", "revenue", ">=", Decimal(1)),
            comparison="share")))

    def test_threshold_counts(self):
        sales = [sale("A", date(2026, 1, 5), 60000), sale("A", date(2026, 1, 5), 40000, product="P2"),
                 sale("B", date(2026, 1, 5), 99999), sale("C", date(2026, 1, 6), 100000),
                 sale("D", date(2026, 1, 6), 500000, "cancelled")]
        window = (date(2026, 1, 1), date(2026, 1, 31))

        def count(subject, measure, value):
            rule = Threshold(subject, measure, ">=", Decimal(value))
            return self.answer(Plan("count_where", *window, threshold=rule), sales).answer

        self.assertEqual(count("order", "revenue", 100000), 2)  # A (100000) and C; B is below
        self.assertEqual(count("day", "revenue", 150000), 1)  # 5/1 = 199999; 6/1 = 100000
        self.assertEqual(count("product", "order_count", 3), 1)  # P1 in A, B, C


if __name__ == "__main__":
    unittest.main()
