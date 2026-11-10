import unittest
from datetime import date

from test_ingestion_readme_sales import REPORT_HEADER, REPORT_README, RULEBOOK, _Shop
from test_semantics_periods import rulebook

from datamate.agent.compute import answer_plan
from datamate.contracts import Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules


PRICE_RULE = (
    "| Giá bán X ngày D | đơn giá ghi trên dòng bán X của các đơn đặt ngày D, "
    "cho một đơn vị cơ bản, trước chiết khấu dòng; tính cả dòng của đơn huỷ, đơn trả |\n"
)


class DailySalePriceTests(_Shop):
    def plan(self, text, rulebook=RULEBOOK + PRICE_RULE):
        return plan_question(Question("example", "shop", text, date(2026, 2, 1)),
                             read_rules(rulebook), ["Bánh quy mè"])

    def test_wordings_require_the_rule_and_an_exact_day(self):
        for text in (
            "Ngày 05/01/2026 cửa hàng bán Bánh quy mè với đơn giá bao nhiêu một gói?",
            "Giá bán một gói Bánh quy mè vào ngày 05/01/2026 là bao nhiêu ạ?",
            "Ngày 05/01/2026 cửa hàng bán Bánh quy mè giá bao nhiêu một gói?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text, RULEBOOK))
                plan = self.plan(text)
                self.assertEqual((plan.intent, plan.start, plan.end, plan.unit),
                                 ("daily_sale_price", date(2026, 1, 5), date(2026, 1, 5), "goi"))
        self.assertIsNone(self.plan("Giá bán một gói Bánh quy mè tháng 1/2026 là bao nhiêu?"))
        self.assertIsNone(self.plan("Giá bán một gói Bánh quy mè ngày 05/03/2026 là bao nhiêu?"))

    def test_reads_line_price_and_includes_cancelled_and_returned_orders(self):
        header = [*REPORT_HEADER, "Đơn giá ghi"]
        readme = REPORT_README.replace("## Cột trong `QuyDoi.xlsx`",
                                       "- **Đơn giá ghi**: đơn giá\n\n## Cột trong `QuyDoi.xlsx`")
        sold = ["O1", "05/01/2026", "Đã huỷ", "Bánh quy mè", 1, 200, "thùng", 2400]
        returned = ["O2", "05/01/2026", "Đã hoàn hàng", "Bánh quy mè", 1, 50, "gói", 100]
        conversions = {"Sheet1": [["Tên sản phẩm", "Đơn vị quy đổi", "Tỉ lệ quy đổi",
                                   "Đơn vị cơ bản"], ["Bánh quy mè", "thùng", 24, "gói"]]}
        for case, last_price, expected in (("consistent", 100, 100), ("ambiguous", 99, None),
                                            ("missing", None, None)):
            with self.subTest(case=case):
                sources = {"DonHang.xlsx": {"01.2026": [header, sold, [*returned[:-1], last_price]]},
                           "QuyDoi.xlsx": conversions}
                catalog = self.shop(readme, sources, business=case)
                plan = self.plan("Giá bán một gói Bánh quy mè ngày 05/01/2026 là bao nhiêu?")
                candidate = answer_plan({}, catalog, plan, RULEBOOK + PRICE_RULE)
                self.assertEqual(candidate.answer, expected)
                self.assertEqual(candidate.type, "number" if expected is not None else "abstain")
                wrong_unit = self.plan("Giá bán một thùng Bánh quy mè ngày 05/01/2026 là bao nhiêu?")
                self.assertEqual(answer_plan({}, catalog, wrong_unit, RULEBOOK + PRICE_RULE).type,
                                 "abstain")

    def test_explicit_top_k_after_a_month(self):
        plan = self.plan("Tháng 12/2025 top 5 hàng chạy nhất là chi?", rulebook())
        self.assertEqual((plan.intent, plan.top_k, plan.start, plan.end),
                         ("top_products", 5, date(2025, 12, 1), date(2025, 12, 31)))
