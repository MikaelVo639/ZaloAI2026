"""Unanswerable only when the shop records no trace of what the question needs.

Synthetic shops and rule wording written for these tests, not organizer data.
"""

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from test_s01_grammar import rulebook

from datamate.agent import compute
from datamate.agent.absence import missing_concept
from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Plan, Question, Split
from datamate.ingestion import build_catalog
from datamate.ingestion.sales import Sale
from datamate.semantics.attributes import asked_attribute

ABSENCE = ("- Không có dữ liệu khác với bằng 0: kỳ nằm **hoàn toàn** ngoài khoảng ngày có dữ "
           "liệu của shop, hoặc shop không ghi loại thông tin câu hỏi cần, thì không trả lời "
           "được, không phải 0.")
SALES_README = """# Dữ liệu shop

## Cột trong `ban_hang.csv`
- **Ngày**: ngày bán
- **Mã đơn**: mã đơn
- **Tên hàng**: tên hàng
- **Thành tiền**: thành tiền sau giảm
"""
SALES = "Ngày,Mã đơn,Tên hàng,Thành tiền\n2026-01-02,D1,Bia lon,10000\n"


class AbsenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def shop(self, readme=SALES_README, business="b", **files):
        root = Path(self.directory.name)
        shop = root / business
        shop.mkdir(exist_ok=True)
        (shop / "README.md").write_text(readme, encoding="utf-8")
        (shop / "ban_hang.csv").write_text(SALES, encoding="utf-8")
        for name, text in files.items():
            (shop / name).write_text(text, encoding="utf-8")
        return build_catalog(root, business)

    def test_sales_only_shop_records_no_stock_purchase_cost_or_expiry(self):
        catalog = self.shop()
        for intent, concept in (("stock", "stock"), ("purchase_total", "purchase"),
                                ("avg_purchase_price", "purchase_price"), ("gross_profit", "cost"),
                                ("expiring_products", "expiry")):
            with self.subTest(intent=intent):
                self.assertEqual(missing_concept(catalog, intent), concept)
        self.assertEqual(missing_concept(catalog, "revenue"), "")

    def test_any_trace_keeps_the_abstain(self):
        cases = {
            "README line": dict(readme=SALES_README + "- **Tồn kho**: cuối ngày\n"),
            "file name": {"TonKho.csv": "Tên hàng\nBia lon\n"},
            "filled column": {"hang.csv": "Tên hàng,SL tồn\nBia lon,4\n"},
        }
        for index, (name, files) in enumerate(cases.items()):
            with self.subTest(trace=name):
                catalog = self.shop(business=f"b{index}", **files)
                self.assertEqual(missing_concept(catalog, "stock"), "")

    def test_empty_columns_and_reorder_thresholds_are_not_stock(self):
        catalog = self.shop(**{"hang.csv": "Tên hàng,SL tồn,Tồn lớn nhất\nBia lon,,50\n"})
        self.assertEqual(missing_concept(catalog, "stock"), "stock")

    def test_purchase_table_with_money_records_cost(self):
        catalog = self.shop(**{"phieu_nhap.csv": "Số phiếu,Thành tiền\nP1,50000\n"})
        self.assertEqual(missing_concept(catalog, "avg_purchase_price"), "")
        catalog = self.shop(**{"phieu_nhap.csv": "Số phiếu,Số lượng\nP1,5\n"})
        self.assertEqual(missing_concept(catalog, "gross_profit"), "cost")

    def test_catalogue_cost_is_not_a_purchase_price(self):
        catalog = self.shop(**{"hang.csv": "Tên hàng,Giá vốn\nBia lon,8000\n"})
        self.assertEqual(missing_concept(catalog, "gross_profit"), "")
        self.assertEqual(missing_concept(catalog, "avg_purchase_price"), "purchase_price")
        catalog = self.shop(business="lots", **{"lo.csv": "Mã phiếu nhập,SL nhập\nP1,5\n"})
        self.assertEqual(missing_concept(catalog, "avg_purchase_price"), "purchase_price")
        self.assertEqual(missing_concept(catalog, "net_purchase"), "")

    def test_answer_plan_needs_the_rulebook_absence_row(self):
        catalog = self.shop()
        plan = Plan("stock", date(2026, 1, 31), date(2026, 1, 31))
        self.assertEqual(compute.answer_plan({}, catalog, plan, "").type, "abstain")
        candidate = compute.answer_plan({}, catalog, plan, ABSENCE)
        self.assertEqual(candidate.type, "unanswerable")
        self.assertEqual(candidate.evidence["coverage_source"]["absent_concept"], "stock")


def sales(first, days):
    return [Sale(f"D{i}", first + timedelta(days=i), "valid", Decimal(1), None, "x", None)
            for i in days]


class DenseStartTests(unittest.TestCase):
    def test_daily_seller_starts_its_data_at_the_first_sale(self):
        start = compute._dense_sales_start(sales(date(2025, 3, 1), range(0, 7)))
        self.assertEqual(start[0], date(2025, 3, 1))
        self.assertEqual(start[1]["selling_days_in_first_week"], 7)

    def test_sparse_seller_does_not_establish_a_start(self):
        self.assertIsNone(compute._dense_sales_start(sales(date(2025, 3, 1), (0, 3, 6, 9))))

    def test_outside_rule_is_read_from_the_rulebook(self):
        self.assertTrue(compute._outside_rule(ABSENCE))
        self.assertEqual(compute._outside_rule("- Kỳ không có đơn thì trả 0."), "")


if __name__ == "__main__":
    unittest.main()


class AskedAttributeTests(unittest.TestCase):
    def test_questions_about_unrecorded_kinds_of_information(self):
        cases = {
            "Tháng 5/2026 khách nữ mua bao nhiêu tiền?": "customer_gender",
            "có mấy khách ở đường Lý Thường Kiệt": "customer_address",
            "nhân viên nào phục vụ tận tâm nhất": "staff_quality",
            "tháng 4/2026 có mấy đơn bị huỷ vì hết hàng": "order_reason",
            "siro ho trẻ em uống mỗi lần bao nhiêu ml": "dosage",
            "Doanh thu các đơn trả bằng ví điện tử tháng 6/2026 là bao nhiêu?": "payment_method",
            "Tháng này doanh thu các đơn bán qua điện thoại chiếm bao nhiêu %?": "sales_channel",
            "Doanh thu từ khách nhóm thân thiết tháng này là bao nhiêu?": "customer_group",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(asked_attribute(text), expected)

    def test_planner_questions_are_not_attribute_questions(self):
        # Payment, channel and customer-group questions are planned when the shop records
        # those labels; only an unplanned one is checked against the data for any trace.
        for text in ("khách Lan tháng 8/2025 nợ thêm bao nhiêu, trả được bao nhiêu đó",
                     "Tháng 3/2026 có bao nhiêu đơn bị huỷ?",
                     "Năm 2025 doanh thu là bao nhiêu?",
                     "Đường cát trắng tháng này bán được bao nhiêu kg?"):
            with self.subTest(text=text):
                self.assertEqual(asked_attribute(text), "")

    def test_attribute_traces_in_the_data(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        shop = root / "b"
        shop.mkdir()
        (shop / "README.md").write_text(SALES_README, encoding="utf-8")
        (shop / "ban_hang.csv").write_text(SALES, encoding="utf-8")
        catalog = build_catalog(root, "b")
        for attribute in ("customer_gender", "customer_address", "staff_quality",
                          "order_reason", "dosage"):
            with self.subTest(attribute=attribute):
                self.assertFalse(compute.attribute_recorded({}, catalog, attribute))
        (shop / "khach.csv").write_text("Tên khách,Địa chỉ\nLan,12 Lê Lợi\n", encoding="utf-8")
        catalog = build_catalog(root, "b")
        self.assertTrue(compute.attribute_recorded({}, catalog, "customer_address"))

    def test_order_labels_are_absent_only_when_every_table_is_described(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        shop = root / "b"
        shop.mkdir()
        (shop / "README.md").write_text(SALES_README, encoding="utf-8")
        (shop / "ban_hang.csv").write_text(SALES, encoding="utf-8")
        for label in ("sales_channel", "payment_method", "customer_group"):
            with self.subTest(label=label):
                self.assertFalse(compute.attribute_recorded({}, build_catalog(root, "b"), label))
        # An undescribed list may hold the label under any header ("Loại bán", "Đối tượng").
        (shop / "don.csv").write_text("Số CT,Loại bán,Đối tượng\nD1,web,A\n", encoding="utf-8")
        for label in ("sales_channel", "payment_method", "customer_group"):
            with self.subTest(label=label, undescribed=True):
                self.assertTrue(compute.attribute_recorded({}, build_catalog(root, "b"), label))


class AgentRouteTests(unittest.TestCase):
    """Through BaselineAgent and its worker, as in a real run."""

    def test_unplanned_attribute_question_is_unanswerable_only_with_the_absence_row(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "b").mkdir()
        (root / "b" / "README.md").write_text(SALES_README, encoding="utf-8")
        (root / "b" / "ban_hang.csv").write_text(SALES, encoding="utf-8")
        catalog = build_catalog(root, "b")
        question = Question("q", "b", "Tháng 1/2026 khách nữ mua bao nhiêu tiền?",
                            date(2026, 1, 31))
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        for book, expected in ((rulebook(), "abstain"),
                               (rulebook() + ABSENCE + "\n", "unanswerable")):
            with self.subTest(expected=expected):
                split = Split(root, (), book, root / "calendar.csv")
                result = agent.answer(question, BusinessContext(split, catalog))
                self.assertEqual(result.type, expected, result.reason)
