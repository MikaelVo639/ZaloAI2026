"""Customer-group revenue and share in train wordings; KiotViet customer list with groups.

Rulebook rows and shop names are synthetic, written for tests.
"""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from test_semantics_periods import rulebook

from datamate.agent.compute import _answer_group
from datamate.contracts import Question
from datamate.ingestion.readme_sales import POS_PROFILES, Section
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

GROUPS = ("| Doanh thu theo nhóm khách | theo cột nhóm khách của khách; đơn vãng lai không thuộc "
          "nhóm nào. Tỉ trọng chia cho doanh thu cả shop cùng kỳ |")
VOCABULARY = {"customer_group": ["lẻ", "sỉ", "thân thiết"]}


def plan(text, extra_rules=""):
    rules = read_rules(rulebook() + GROUPS + "\n" + extra_rules)
    return plan_question(Question("x", "b", text, date(2026, 7, 10)), rules, (), VOCABULARY)


class CustomerGroupWordingTests(unittest.TestCase):
    def test_average_uses_distinct_valid_orders_in_customer_group(self):
        average_rule = "| Giá trị trung bình một đơn | làm tròn về đồng, nửa đồng làm tròn lên |\n"
        questions = (
            "Tháng 1/2026, trung bình mỗi đơn của khách nhóm thân thiết là bao nhiêu tiền hàng?",
            "Khách nhóm thân thiết tháng 1/2026 mua trung bình bao nhiêu tiền hàng mỗi đơn?",
        )
        day = date(2026, 1, 5)
        sales = [
            Sale("A", day, "valid", Decimal(100), None, None, None, customer_group="thân thiết"),
            Sale("A", day, "valid", Decimal(50), None, None, None, customer_group="thân thiết"),
            Sale("B", day, "valid", Decimal(51), None, None, None, customer_group="thân thiết"),
            Sale("C", day, "returned", Decimal(99), None, None, None, customer_group="thân thiết"),
            Sale("D", day, "valid", Decimal(400), None, None, None),
        ]
        for question in questions:
            with self.subTest(question=question):
                self.assertIsNone(plan(question))
                result = plan(question, average_rule)
                self.assertIsNotNone(result)
                self.assertEqual((result.intent, result.filters[0].field),
                                 ("average_order", "customer_group"))
                self.assertEqual(_answer_group(result, sales, {"inputs": {}}).answer, 101)

    def test_share_wordings(self):
        for text in (
            "Tỉ trọng doanh thu của khách nhóm thân thiết trong tổng doanh thu tháng 1/2026 là "
            "bao nhiêu phần trăm?",
            "Trong tổng doanh thu tháng 1/2026, phần của khách nhóm thân thiết là mấy phần trăm?",
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.comparison), ("revenue", "share"))
                self.assertEqual([(f.field, f.value) for f in result.filters],
                                 [("customer_group", "thân thiết")])
                self.assertEqual((result.start, result.end), (date(2026, 1, 1), date(2026, 1, 31)))

    def test_revenue_wordings(self):
        for text in (
            "Tháng 12/2025 các khách thuộc nhóm sỉ mua tổng cộng bao nhiêu tiền hàng?",
            "Tính doanh thu các đơn do khách nhóm sỉ mua tháng 12/2025",
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.comparison), ("revenue", ""))
                self.assertEqual(result.filters[0].value, "sỉ")

    def test_other_conditions_still_abstain(self):
        for text in (
            "Tháng 12/2025 các khách thuộc nhóm sỉ mua bao nhiêu đơn hàng online?",
            "Tỉ trọng doanh thu của khách nhóm vip trong tổng doanh thu tháng 1/2026?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))


class KiotVietCustomerListTests(unittest.TestCase):
    def test_sales_export_uses_customer_tier_as_group(self):
        lists = dict(POS_PROFILES)["sales_export_banhang.v1"]
        meanings = next(meanings for required, meanings in lists if "Hạng" in required)
        self.assertEqual(Section("customers.xlsx", None, meanings).fields,
                         {"customer_id": "Customer ID", "customer_name": "ten_kh",
                          "customer_group": "Hạng"})

    def test_profile_reads_the_customer_group(self):
        lists = dict(POS_PROFILES)["pos_export_kiotviet.v1"]
        required, meanings = next(
            (required, meanings) for required, meanings in lists if "Nhóm khách hàng" in required)
        section = Section(Path("DanhSachKhachHang.xlsx").name, None, meanings)
        self.assertEqual(section.fields, {"customer_id": "Mã khách hàng",
                                          "customer_name": "Tên khách hàng",
                                          "customer_group": "Nhóm khách hàng"})
        self.assertEqual(required, {"Mã khách hàng", "Tên khách hàng", "Nhóm khách hàng"})


if __name__ == "__main__":
    unittest.main()
