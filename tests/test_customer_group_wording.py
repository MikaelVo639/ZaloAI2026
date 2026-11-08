"""Customer-group revenue and share in train wordings; KiotViet customer list with groups.

Rulebook rows and shop names are synthetic, written for tests.
"""
import unittest
from datetime import date
from pathlib import Path

from test_semantics_periods import rulebook

from datamate.contracts import Question
from datamate.ingestion.readme_sales import POS_PROFILES, Section
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

GROUPS = ("| Doanh thu theo nhóm khách | theo cột nhóm khách của khách; đơn vãng lai không thuộc "
          "nhóm nào. Tỉ trọng chia cho doanh thu cả shop cùng kỳ |")
VOCABULARY = {"customer_group": ["lẻ", "sỉ", "thân thiết"]}


def plan(text):
    rules = read_rules(rulebook() + GROUPS + "\n")
    return plan_question(Question("x", "b", text, date(2026, 7, 10)), rules, (), VOCABULARY)


class CustomerGroupWordingTests(unittest.TestCase):
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
