"""Regression cases for documented staff, product, channel and credit calculations."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from test_group_share_wording import GROUP
from test_ingestion_credit_terms import README as CREDIT_COLUMNS
from test_ingestion_readme_sales import _Shop
from test_verified_sales_measures import PHONE_RULES, SIZE_RULES

from datamate.agent.compute import answer_plan, credit_due_count, product_names, vocabulary
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion.credit_terms import CreditTerm
from datamate.ingestion.sales import Sale
from datamate.ingestion.staff import load_repair_staff
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

TODAY = date(2026, 5, 31)
CREDIT_RULES = PHONE_RULES + """
| Đến hết ngày X, tính đến hôm nay | hỏi số còn lại: tình trạng lúc hết ngày đó, tính mọi phát sinh từ đầu dữ liệu đến hết ngày đó |
| Đơn được tính vào nợ | đơn không huỷ; đơn trả hàng vẫn tính nếu còn nợ |
| Tháng M/Y | cả tháng, từ ngày 1 đến hết ngày cuối tháng |
"""


def ask(text, book, vocab=None):
    return plan_question(Question("fixture", "shop", text, TODAY), read_rules(book),
                         vocabulary=vocab)


class StaffCoverageTests(_Shop):
    def test_unique_seller_name_and_mechanic_name_collision(self):
        text = "Tháng 5/2026 doanh thu các đơn nhân viên Đỗ Minh An bán là bao nhiêu?"
        vocab = {"staff_terms": ["seller|do minh an|NV010"]}
        book = PHONE_RULES
        self.assertEqual(ask(text, book, vocab).filters[0].value, "NV010")
        self.assertIsNone(ask(text, book))
        catalog = self.shop(
            """## Cột trong `repairs.csv`
- **Thợ**: thợ làm phiếu, ghi tên gọi
## Cột trong `roster.csv`
- **Họ tên**: họ tên thợ
""", {"roster.csv": [["Họ tên"], ["Đỗ Minh An"], ["Lê Tuấn An"], ["Mai Thị Hoa"]]})
        directory = load_repair_staff(catalog)
        self.assertEqual(directory.codes_by_unique_name["a hoa"], "Mai Thị Hoa")
        self.assertNotIn("an", directory.codes_by_unique_name)
        self.assertNotIn("a an", directory.codes_by_unique_name)


class AssignedMeasureTests(_Shop):
    def predict(self, catalog, text, book):
        cache = {}
        plan = plan_question(Question("fixture", "shop", text, TODAY), read_rules(book),
                             product_names(cache, catalog, book), vocabulary(cache, catalog, book))
        self.assertIsNotNone(plan, text)
        return check_executed(answer_plan(cache, catalog, plan, book), plan)

    def test_catalogue_conversion_size_and_distinct_group_orders(self):
        catalog = self.shop("""## Cột trong `sales.csv`
- **Mã**: mã đơn
- **Ngày**: ngày bán
- **Trạng thái**: trạng thái đơn
- **Hàng**: tên hàng
- **SL**: số lượng
- **ĐV**: đơn vị tính
- **Tiền**: thành tiền sau giảm
## Cột trong `products.csv`
- **Tên**: tên hàng
- **Nhóm**: nhóm hàng
- **Size**: số / size
- **ĐV**: đơn vị cơ bản
- **Quy đổi**: quy đổi đơn vị lớn ra ĐV
""", {
            "sales.csv": [
                ["Mã", "Ngày", "Trạng thái", "Hàng", "SL", "ĐV", "Tiền"],
                ["A", "01/05/2026", "Hoàn thành", "Bỉm A", 1, "thùng", 120],
                ["A", "01/05/2026", "Hoàn thành", "Bỉm B", 3, "bịch", 30],
                ["B", "02/05/2026", "Hoàn thành", "Bỉm C", 5, "bịch", 50],
                ["C", "02/05/2026", "Huỷ", "Bỉm B", 90, "bịch", 900],
            ],
            "products.csv": [
                ["Tên", "Nhóm", "Size", "ĐV", "Quy đổi"],
                ["Bỉm A", "Bỉm tã", "Size M", "bịch", "1 thùng = 12 bịch"],
                ["Bỉm B", "Bỉm tã", "Size M", "bịch", ""],
                ["Bỉm C", "Bỉm tã", "Size L", "bịch", ""],
            ],
        })
        self.assertEqual(self.predict(catalog, "Tháng 5/2026 bỉm size M bán được mấy bịch "
                                     "tất cả các hãng?", SIZE_RULES + PHONE_RULES).answer, 15)
        self.assertEqual(self.predict(catalog, "Tháng 5/2026 có mấy đơn mua hàng nhóm bỉm tã?",
                                     PHONE_RULES + GROUP).answer, 2)

    def test_phone_model_and_share_exclude_accessories_and_returns(self):
        readme = """## Cột trong `Sales.xlsx`
- **Ngày**: ngày bán
- **STT**: số thứ tự đơn
- **Khách**: tên khách
- **Hàng**: hàng bán
- **IMEI**: mã máy bán
- **SL**: số lượng
- **Giá**: đơn giá
- **Thành tiền**: thành tiền của dòng
- **Ghi chú**: ghi chú của đơn
- Sheet **Ghi tắt** cho tên đầy đủ và loại hàng.
"""
        catalog = self.shop(readme, {"Sales.xlsx": {
            "Ghi tắt": [["Ghi tắt", "Tên hàng", "Loại"],
                        ["A", "Điện thoại Ánh Dương 128GB", "máy mới"],
                        ["A2", "Điện thoại Ánh Dương 256GB", "máy mới"],
                        ["AP", "Điện thoại Ánh Dương Plus 128GB", "máy cũ"], ["P", "Ốp A", "phụ kiện"]],
            "Ledger": [
                ["Ngày", "STT", "Khách", "Hàng", "IMEI", "SL", "Giá", "Thành tiền", "Ghi chú"],
                ["01/05/2026", 1, "An", "A", "1", 1, 100, 100, ""],
                ["02/05/2026", 2, "An", "A2", "2", 1, 200, 200, ""],
                ["03/05/2026", 3, "An", "AP", "3", 1, 100, 100, ""],
                ["04/05/2026", 4, "An", "P", "", 1, 600, 600, ""],
                ["05/05/2026", 5, "An", "A", "5", 1, 100, 100, "khách trả lại"],
            ],
        }})
        self.assertEqual(self.predict(catalog, "Tháng này bán được bao nhiêu chiếc Điện thoại Ánh Dương?",
                                     PHONE_RULES).answer, 2)
        self.assertEqual(self.predict(catalog, "Tháng này máy mới chiếm mấy % tiền bán máy?",
                                     PHONE_RULES).answer, 75)

    def test_recorded_due_date_and_observation_are_independent(self):
        plan = ask("Tính đến hôm nay có mấy đơn bán chịu ghi ngày phải trả sau hôm nay?",
                   CREDIT_RULES)
        self.assertEqual((plan.start, plan.observed_on), (date(2026, 6, 1), TODAY))
        self.assertIsNone(ask("Đến hôm nay còn bao nhiêu đơn bán chịu chưa trả có ngày phải "
                              "trả sau hôm nay?", CREDIT_RULES))
        catalog = self.shop(CREDIT_COLUMNS + """- Hạn thanh toán: chỉ đơn bán nợ có hạn.
## Cột trong `sales.csv`
- **Mã**: mã đơn
- **Ngày**: ngày bán
- **Trạng thái**: trạng thái đơn
- **Hàng**: tên hàng
- **SL**: số lượng
- **Tiền**: thành tiền sau giảm
""", {
            "credit.csv": [["Đơn", "Kỳ hạn", "Ngày phải trả"], ["O1", 2, "01/06/2026"],
                           ["O2", 1, "31/05/2026"], ["O3", 1, "02/06/2026"]],
            "sales.csv": [["Mã", "Ngày", "Trạng thái", "Hàng", "SL", "Tiền"],
                          ["O1", "30/05/2026", "Hoàn thành", "Hàng A", 1, 100],
                          ["O1", "30/05/2026", "Hoàn thành", "Hàng B", 1, 100],
                          ["O2", "30/05/2026", "Hoàn thành", "Hàng A", 1, 100],
                          ["O3", "01/06/2026", "Hoàn thành", "Hàng A", 1, 100]],
        })
        result = check_executed(answer_plan({}, catalog, plan, CREDIT_RULES), plan)
        self.assertEqual((result.type, result.answer), ("number", 1))
        term = CreditTerm("O1", date(2026, 6, 1), 2, "credit.csv")
        sale = Sale("O1", date(2026, 5, 30), "valid", Decimal(1), "A", "Hàng A", Decimal(1))
        with self.assertRaises(ValueError):
            credit_due_count((replace(term, order_id="missing"),), [sale], TODAY,
                             plan.start, plan.end)
