"""Regression for SEM-01 step 3: period comparisons, product filter, top-k wording.

Rulebook rows are synthetic paraphrases written for tests, not organizer data.
"""
import unittest
from datetime import date
from decimal import Decimal

from datamate.agent import compute
from datamate.contracts import Catalog, Filter, Plan, Question
from datamate.demo import RULEBOOK
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

ROWS = {
    "change": "| Phần trăm thay đổi, tăng trưởng | So với kỳ trước; giảm thì ra số âm |",
    "names": "| Tên gọi, nhãn | Các tên cùng chỉ một thứ là cùng một thứ. File có mã thì theo mã |",
    "money": "| Bán được bao nhiêu tiền, thu về bao nhiêu tiền | Là doanh thu, không phải tiền khách đã trả |",
}
SAME_DAYS = ("| Tháng này | Từ ngày 1 đến hết hôm nay. So với tháng trước thì lấy cùng số ngày "
             "của tháng trước |")
NAMES = ("Nước khoáng LaVie 19L", "Nước khoáng LaVie", "Gas Petrolimex 12kg", "Trà", "SP0042")


GOODS = "không gồm phí giao hàng. Tiền hàng, tiền bán cùng nghĩa với doanh thu |"


def rulebook(*skip: str, same_days: bool = True) -> str:
    base = RULEBOOK.replace("không gồm phí giao hàng |", GOODS)
    if same_days:
        base = base.replace("| Tháng này | Từ ngày 1 đến hết hôm nay |", SAME_DAYS)
    return base + "\n".join(row for key, row in ROWS.items() if key not in skip) + "\n"


class ComparisonGrammarTests(unittest.TestCase):
    def plan(self, text, today="2026-07-15", book=None, names=()):
        rules = read_rules(rulebook() if book is None else book)
        return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules, names)

    def windows(self, text, today="2026-07-15", book=None):
        plan = self.plan(text, today, book)
        self.assertIsNotNone(plan, text)
        self.assertEqual((plan.intent, plan.comparison), ("revenue", "change_percent"))
        return (plan.start, plan.end), (plan.compare_start, plan.compare_end)

    def test_previous_month_for_a_full_month(self):
        expected = ((date(2026, 3, 1), date(2026, 3, 31)), (date(2026, 2, 1), date(2026, 2, 28)))
        for text in ("So với tháng liền trước, doanh thu tháng 3/2026 thay đổi bao nhiêu %?",
                     "dthu thang 3/2026 tang hay giam bnhiu phan tram so vs thang truoc do",
                     "dthu tháng 3/2026 hơn hay kém th trước bn %",
                     "thang 3/2026 dthu thay doi bao nhieu % so vs thang lien trc z"):
            with self.subTest(text=text):
                self.assertEqual(self.windows(text), expected)

    def test_month_to_date_uses_the_same_number_of_days(self):
        current, previous = self.windows(
            "Doanh thu từ đầu tháng này đến hết hôm nay hơn hay kém cùng kỳ tháng trước mấy %?")
        self.assertEqual((current, previous),
                         ((date(2026, 7, 1), date(2026, 7, 15)), (date(2026, 6, 1), date(2026, 6, 15))))
        # The 31st compares with the end of a 28-day February.
        _, previous = self.windows("doanh thu tháng này tăng hay giảm bao nhiêu % so với tháng trước",
                                   today="2026-03-31")
        self.assertEqual(previous, (date(2026, 2, 1), date(2026, 2, 28)))

    def test_same_period_last_year_and_an_explicit_month(self):
        self.assertEqual(self.windows("doanh thu tháng 5/2026 so với cùng kỳ năm ngoái thay đổi bao nhiêu %")[1],
                         (date(2025, 5, 1), date(2025, 5, 31)))
        self.assertEqual(self.windows("dthu tháng 5 năm 2026 tăng trưởng bao nhiêu % so với tháng 5 năm 2025?")[1],
                         (date(2025, 5, 1), date(2025, 5, 31)))
        self.assertEqual(self.windows("so cung ky nam truoc doanh thu thang 5/2026 tawng/giamr may %")[1],
                         (date(2025, 5, 1), date(2025, 5, 31)))

    def test_comparisons_need_their_rule_rows(self):
        text = "So với tháng liền trước, doanh thu tháng 3/2026 thay đổi bao nhiêu %?"
        self.assertIsNone(self.plan(text, book=rulebook("change")))
        mtd = "doanh thu tháng này tăng hay giảm bao nhiêu % so với tháng trước"
        self.assertIsNotNone(self.plan(mtd))
        self.assertIsNone(self.plan(mtd, book=rulebook(same_days=False)))

    def test_reference_order_and_comparison_wording(self):
        expected = ((date(2026, 3, 1), date(2026, 3, 31)), (date(2025, 3, 1), date(2025, 3, 31)))
        for text in (
            "So với T3/2025 thì T3/2026 doanh thu tăng trưởng bao nhiêu %?",
            "Doanh thu T3/2026 so T3/2025 cao hay thấp hơn bao nhiêu %?",
            "Doanh thu T3/2026 đem so với tháng 3 năm ngoái thì thay đổi bao nhiêu %?",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.windows(text), expected)
        self.assertEqual(
            self.windows("Tháng này tới hôm nay dthu chênh bao nhiêu % so với cùng kỳ tháng trước, "
                         "tăng hay giảm?", today="2026-06-30"),
            ((date(2026, 6, 1), date(2026, 6, 30)), (date(2026, 5, 1), date(2026, 5, 30))),
        )
        self.assertIsNone(self.plan("Doanh thu T3/2026 so T3/2025 cao hay thấp hơn bao nhiêu tiền?"))

    def test_unsupported_comparisons_still_abstain(self):
        for text in ("doanh thu tháng 3/2026 chênh lệch bao nhiêu tiền so với tháng trước",
                     "doanh thu tháng 7/2026 so với tháng trước tăng bao nhiêu %",  # running month
                     "số đơn tháng 3/2026 tăng hay giảm bao nhiêu % so với tháng trước",
                     "doanh thu tuần này so với tuần trước tăng hay giảm bao nhiêu %",
                     "doanh thu dịp Tết hơn hay kém 17 ngày trước Tết bao nhiêu %",
                     "doanh thu tháng 3/2026 so với tháng 3/2026 thay đổi bao nhiêu %"):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))


class ProductFilterGrammarTests(unittest.TestCase):
    def plan(self, text, names=NAMES, book=None):
        rules = read_rules(rulebook() if book is None else book)
        return plan_question(Question("x", "b", text, date(2026, 7, 15)), rules, names)

    def test_one_named_product_becomes_a_filter(self):
        cases = {
            "Doanh thu của Nước khoáng Lavie 19L trong tháng 5 năm 2026 là bao nhiêu nhỉ?":
                "Nước khoáng LaVie 19L",  # the longest name wins over "Nước khoáng LaVie"
            "tiền bán gas petrolimex 12kg tháng 5/2026 là bn": "Gas Petrolimex 12kg",
            "tháng 5/2026 mặt hàng gas petrolimex 12kg thu về bn tiền": "Gas Petrolimex 12kg",
            "doanh thu sp0042 tháng 5/2026": "SP0042",
        }
        for text, product in cases.items():
            with self.subTest(text=text):
                plan = self.plan(text)
                self.assertIsNotNone(plan, text)
                self.assertEqual((plan.intent, plan.start), ("revenue", date(2026, 5, 1)))
                self.assertEqual(plan.filters, (Filter("product", product, ROWS["names"]),))

    def test_no_names_ambiguity_or_missing_rule_abstain(self):
        text = "Doanh thu của Nước khoáng Lavie 19L trong tháng 5/2026 là bao nhiêu?"
        self.assertIsNone(self.plan(text, names=()))
        self.assertIsNone(self.plan(text, book=rulebook("names")))
        self.assertIsNone(self.plan("doanh thu nước khoáng lavie 19l và gas petrolimex 12kg tháng 5/2026"))
        self.assertIsNone(self.plan("doanh thu trà tháng 5/2026"))  # one-word names are ignored
        self.assertIsNone(self.plan("tháng 5/2026 bán được bao nhiêu bình nước khoáng lavie 19l"))
        self.assertIsNone(self.plan("doanh thu nhóm nước khoáng lavie 19l tháng 5/2026"))

    def test_top_k_wording(self):
        for text, k in (("Cho mình 3 mặt hàng bán chạy nhất trong tháng 5/2026.", 3),
                        ("Giúp mình liệt kê 5 mặt hàng bán chạy nhất tháng 5/2026.", 5),
                        ("Top 3 sản phẩm bán nhiều nhất tháng 5 năm 2026 là những gì?", 3),
                        ("Hàng nào bán chạy nhất tháng 5/2026?", 1),
                        ("tuan roi hang nao chay nhat", 1),
                        ("tháng 5/2026 top 5 hàng bán chạy nhất là j", 5),
                        ("coi gium top 5 san pham ban nhiu nhat thang 5 nam 2026 la nhung gi", 5)):
            with self.subTest(text=text):
                plan = self.plan(text, names=())
                self.assertIsNotNone(plan, text)
                self.assertEqual((plan.intent, plan.top_k), ("top_products", k))

    def test_hot_needs_the_ranking_row_to_name_it(self):
        text = "tháng 5/2026 top 5 hàng hot nhất là gì"
        hot = "**Bán nhiều nhất**, **hot nhất** cùng nghĩa với **bán chạy nhất**"
        book = rulebook().replace("| Bán chạy | Theo số lượng quy về đơn vị cơ bản |",
                                  f"| Bán chạy | Theo số lượng quy về đơn vị cơ bản. {hot} |")
        self.assertIsNone(self.plan(text, names=()))  # the demo ranking row has no synonyms
        self.assertEqual(self.plan(text, names=(), book=book).top_k, 5)
        plan = self.plan("mat hang hot nhat thang 5 nam 2026 la mat hang nao", names=(), book=book)
        self.assertEqual((plan.intent, plan.top_k), ("top_products", 1))

    def test_best_day_with_telex_day_word(self):
        plan = self.plan("ngayf nào trong tháng 5/2026 dthu cao nhaats", names=())
        self.assertEqual((plan.intent, plan.start), ("best_day", date(2026, 5, 1)))


def sale(order, day, status, amount, product="P1", name="Nước khoáng LaVie 19L", month=5):
    return Sale(order, date(2026, month, day), status, Decimal(amount), product, name, Decimal(1))


SALES = [
    sale("A1", 2, "valid", 100000), sale("A1", 2, "valid", 50000, "P2", "Gas Petrolimex 12kg"),
    sale("A2", 9, "cancelled", 900000),
    sale("B1", 3, "valid", 80000, month=4), sale("B2", 4, "valid", 40000, "P2", "Gas Petrolimex 12kg", 4),
]


class ComparisonAndFilterAgentTests(unittest.TestCase):
    def answer(self, plan, sales=SALES, readme=""):
        catalog = Catalog("b", readme, ())
        cache = {"sales_key": compute._catalog_key(catalog),
                 "sales": (sales, {"tables": ["fixture"], "raw_rows": len(sales),
                                   "sales_rows": len(sales), "duplicates_removed": 0})}
        return compute.answer_plan(cache, catalog, plan)

    def test_change_percent_uses_valid_revenue_of_both_windows(self):
        plan = Plan("revenue", date(2026, 5, 1), date(2026, 5, 31), compare_start=date(2026, 4, 1),
                    compare_end=date(2026, 4, 30), comparison="change_percent")
        result = self.answer(plan)  # 150000 vs 120000
        self.assertEqual((result.type, result.answer), ("percent", 25.0))
        self.assertTrue(result.evidence["code"]["call"].startswith("change_percent(revenue_total("))
        self.assertEqual(compute.change_percent(Decimal(2), Decimal(3)), -33.33)

    def test_reference_first_keeps_the_sign_and_denominator(self):
        sales = [sale("A", 2, "valid", 2500000), sale("B", 2, "valid", 2000000, month=4)]
        for subject, reference, percent in ((5, 4, 25.0), (4, 5, -20.0)):
            with self.subTest(subject=subject):
                text = f"So với T{reference}/2026 thì T{subject}/2026 doanh thu cao hay thấp hơn bao nhiêu %?"
                question = Question("x", "b", text, date(2026, 7, 15))
                result = self.answer(plan_question(question, read_rules(rulebook())), sales)
                self.assertEqual((result.type, result.answer), ("percent", percent))

    def test_zero_or_missing_comparison_period(self):
        plan = Plan("revenue", date(2026, 5, 1), date(2026, 5, 31), compare_start=date(2026, 4, 1),
                    compare_end=date(2026, 4, 30), comparison="change_percent")
        self.assertEqual(self.answer(plan, SALES[:3]).type, "abstain")  # no April sales
        zero_base = SALES[:3] + [sale("Z", 2, "valid", 0, month=4)]
        self.assertEqual(self.answer(plan, zero_base).type, "abstain")
        covered = "Khoảng dữ liệu: 2026-05-01 đến 2026-05-31"
        self.assertEqual(self.answer(plan, SALES[:3], covered).type, "unanswerable")

    def test_product_filter_for_revenue_and_orders(self):
        product = (Filter("product", "nước khoáng lavie 19l"),)
        revenue = self.answer(Plan("revenue", date(2026, 5, 1), date(2026, 5, 31), filters=product))
        orders = self.answer(Plan("order_count", date(2026, 5, 1), date(2026, 5, 31), filters=product))
        self.assertEqual((revenue.answer, orders.answer), (100000, 1))
        # The name finds one code; every row of that code counts (rulebook "Tên gọi").
        self.assertIn("select_product_code(sales, 'P1')", revenue.evidence["code"]["call"])
        unknown = (Filter("product", "Bia Tiger"),)
        self.assertEqual(self.answer(Plan("revenue", date(2026, 5, 1), date(2026, 5, 31),
                                          filters=unknown)).type, "abstain")

    def test_plan_fields_the_backend_cannot_apply_abstain(self):
        product = (Filter("product", "P1"),)
        for plan in (Plan("best_day", date(2026, 5, 1), date(2026, 5, 31), filters=product),
                     Plan("revenue", date(2026, 5, 1), date(2026, 5, 31),
                          filters=(Filter("payment_method", "tiền mặt"),)),
                     Plan("order_count", date(2026, 5, 1), date(2026, 5, 31),
                          compare_start=date(2026, 4, 1), compare_end=date(2026, 4, 30),
                          comparison="change_percent"),
                     Plan("revenue", date(2026, 5, 1), date(2026, 5, 31), comparison="difference",
                          compare_start=date(2026, 4, 1), compare_end=date(2026, 4, 30))):
            with self.subTest(plan=plan):
                self.assertEqual(self.answer(plan).type, "abstain")

    def test_product_names_task_lists_names_and_codes(self):
        catalog = Catalog("b", "", ())
        cache = {"sales_key": compute._catalog_key(catalog),
                 "sales": (SALES, {"tables": ["t"], "raw_rows": 5, "sales_rows": 5,
                                   "duplicates_removed": 0})}
        self.assertEqual(compute.product_names(cache, catalog),
                         ["Gas Petrolimex 12kg", "Nước khoáng LaVie 19L", "P1", "P2"])


if __name__ == "__main__":
    unittest.main()
