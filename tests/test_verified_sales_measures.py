"""Source, plan and execution regressions for catalogue ranking and phone sales."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from datamate.agent.compute import answer_plan
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion.readme_sales import load_readme_sales
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from test_group_share_wording import GROUP
from test_ingestion_readme_sales import _Shop
from test_ranking_threshold_wording import answer
from test_sem02_grammar import rulebook, sale


PHONE_RULES = rulebook() + """
| Trạng thái đơn | mỗi đơn có đúng một trong bốn trạng thái: hoàn thành, huỷ, trả hàng, đang giao |
| Sổ không có cột trạng thái | trạng thái đơn ghi ở cột Ghi chú. Ghi chú không nhắc trạng thái là đơn hoàn thành |
| Bán được bao nhiêu tiền / thu về bao nhiêu tiền | Là doanh thu, không phải tiền khách đã trả |
| Giá trị trung bình một đơn | Làm tròn về đồng, nửa đồng làm tròn lên |
| Thu máy cũ | Khách phải trả là tiền của đơn sau bớt và sau trừ máy cũ. Chỉ tính đơn không huỷ, không trả |
| Dòng máy, hãng | Máy khách mang tới đổi không phải máy bán |
"""
SIZE_RULES = rulebook() + GROUP + """
| Số sữa, size bỉm | Lấy theo cột Số / Size của danh mục. Hàng để trống cột Số / Size thì không xếp vào số, size nào |
"""
TODAY = date(2026, 5, 31)


class VerifiedSalesMeasureTests(_Shop):
    def test_brand_ranking_aggregates_base_quantities_in_requested_size(self):
        vocab = {"product_group": ["Sữa"], "product_brand": ["A", "B"],
                 "product_size": ["Số 1", "Số 3"]}
        question = Question("synthetic", "shop",
                            "Tháng 5/2026 sữa số 3 của hãng nào bán nhiều hộp nhất?", TODAY)
        plan = plan_question(question, read_rules(SIZE_RULES), vocabulary=vocab)
        self.assertEqual((plan.intent, plan.unit), ("top_product_brand", "hop"))

        def row(order, brand, quantity, *, size="Số 3", unit="hộp", status="valid"):
            return replace(sale(order, TODAY, 1, status, product=order, name=order,
                                quantity=quantity, unit=unit),
                           product_group="Sữa", product_brand=brand, product_size=size)

        sales = [row("O1", "A", 3), row("O2", "A", 3), row("O3", "B", 5),
                 row("O4", "B", 90, size="Số 1"), row("O5", "B", 90, unit="gói"),
                 row("O6", "B", 90, status="returned"), row("O7", "B", 90, size=None)]
        result = check_executed(answer(plan, sales, SIZE_RULES), plan)
        self.assertEqual((result.type, result.answer), ("string", "A"))
        self.assertEqual(answer(plan, sales + [row("O8", None, 12)], SIZE_RULES).type, "abstain")
        self.assertIsNone(plan_question(question, read_rules(rulebook() + GROUP), vocabulary=vocab))

    def test_phone_measures_join_product_kind_and_use_one_amount_due_per_order(self):
        readme = """# Sổ điện thoại
Sổ bán hàng, mỗi tháng một sheet.
- Đơn: các dòng cùng **STT** là một đơn.
- Tiền khách trả: **Khách trả** là số tiền khách trả cho cả đơn, đã bớt và đã trừ tiền thu máy cũ.
- Đơn khách trả lại: **Ghi chú** của đơn ghi khách trả lại.
- Sheet **Ghi tắt** cho tên đầy đủ và loại hàng.
"""
        header = ["Ngày", "STT", "Khách", "Hàng", "SL", "Giá", "Thành tiền", "Khách trả", "Ghi chú"]
        catalog = self.shop(readme, {"Sales.xlsx": {
            "Ghi tắt": [["Ghi tắt", "Tên hàng", "Loại"], ["A", "Máy A", "máy mới"],
                        ["B", "Máy B", "máy cũ"], ["P", "Ốp", "phụ kiện"]],
            "T5-2026": [
                [], header,
                ["01/05/2026", 1, "An", "A", 2, 500, 1000, 800, ""],
                ["01/05/2026", 1, "An", "P", 1, 99, 99, 800, ""],
                ["05/05/2026", 2, "Bình", "Máy B", 1, 200, 200, 101, ""],
                ["09/05/2026", 3, "Chi", "A", 1, 500, 500, 500, "khách trả lại"],
            ],
        }})
        rules = read_rules(PHONE_RULES)
        cache = {}
        for text, expected, denominator in (
            ("Tháng 5/2026 tiền khách phải trả bình quân mỗi đơn là bao nhiêu đồng?", 451, 2),
            ("Tháng 5/2026 không tính phụ kiện, bình quân mỗi máy điện thoại bán bao nhiêu tiền?",
             400, 3),
        ):
            with self.subTest(text=text):
                question = Question("synthetic", "shop", text, TODAY)
                plan = plan_question(question, rules)
                result = check_executed(answer_plan(cache, catalog, plan, PHONE_RULES), plan)
                self.assertEqual((result.type, result.answer), ("number", expected))
                self.assertEqual(result.evidence["denominator"], denominator)

        sales = cache["sales"][0]
        conflicting = replace(sales[0], order_amount_due=Decimal(801))
        due = plan_question(Question("synthetic", "shop", "Tháng 5/2026 tiền khách phải trả "
                                    "bình quân mỗi đơn bao nhiêu đồng?", TODAY), rules)
        self.assertEqual(answer(due, [conflicting, *sales[1:]], PHONE_RULES).type, "abstain")

    def test_combined_export_catalogue_requires_a_documented_sheet_and_unique_name(self):
        readme = ("Excel xuất từ phần mềm bán hàng. Dữ liệu trong \x60Sales.xlsx\x60 gồm sheet "
                  "\x60Lines\x60 và danh mục hàng hóa \x60Catalogue\x60.")
        header = ["Đơn số", "Ngày HĐ", "Trạng thái giao", "Hàng hóa", "S.L",
                  "Thành tiền (đ)", "SL (ĐV cơ bản)", "ĐV"]
        catalog = self.shop(readme, {"Sales.xlsx": {
            "Lines": [header, ["O1", "01/05/2026", "completed", "Món A", 2, 100, 2, "ly"],
                      ["O2", "02/05/2026", "completed", "Món B", 2, 100, 2, "ly"]],
            "Catalogue": [["Mã H.hóa", "Tên hàng hóa", "Nhóm hàng", "DVT"],
                          ["P1", "Món A", "Đồ uống", "ly"], ["P2", "Món B", "Nhóm B", "ly"],
                          ["P3", "Món B", "Nhóm C", "ly"]],
        }})
        sales, _, _ = load_readme_sales(catalog, PHONE_RULES)
        self.assertEqual([(row.product_group, row.base_unit) for row in sales],
                         [("Đồ uống", "ly"), (None, "ly")])
        sales, _, _ = load_readme_sales(replace(catalog, readme=readme.split(" và ")[0]), PHONE_RULES)
        self.assertTrue(all(row.product_group is None for row in sales))
