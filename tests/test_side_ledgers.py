"""Side books of a phone shop: phones taken in and instalment plans. Shops are synthetic."""
import unittest
from datetime import date

from test_ingestion_readme_sales import RULEBOOK, _Shop
from test_group_share_wording import GROUP
from test_semantics_periods import rulebook

from datamate.agent.compute import answer_plan
from datamate.contracts import Plan, Question
from datamate.ingestion.side_ledgers import SideLedgerError, instalments, trade_ins
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

README = """# Dữ liệu shop

Sổ thu máy cũ ghi máy khách đưa, giá thu (**Giá thu**). Sổ góp: mỗi đơn trả góp có dòng đầu
ghi ngày mua, **Giá** là tiền cả đơn, **Trả trước** là tiền trả lúc mua, còn lại chia đều
**Số kỳ** kỳ. Mỗi lần khách góp ghi **Ngày góp**, **Đã góp**: lần đầu ở dòng đầu đơn, mỗi lần
sau một dòng có **STT**.

## File
- `thu.csv` (csv)
- `gop.csv` (csv)

## Quy ước
- Dữ liệu có đến hết 31/03/2026.
"""
TRADE = [["Ngày", "Máy thu", "Giá thu"], ["03/02/2026", "Máy A", "2.500.000"],
         ["20/02/2026", "Máy B", 1500000], ["02/03/2026", "Máy C", 900000]]
PLANS = [["Ngày", "STT", "Khách", "Giá", "Trả trước", "Số kỳ", "Ngày góp", "Đã góp"],
         ["05/01/2026", "1", "An", 12000000, 3000000, 6, "", ""],
         ["", "1", "", "", "", "", "05/02/2026", 1500000],
         ["", "1", "", "", "", "", "06/03/2026", 1500000],
         ["10/02/2026", "2", "Bình", 6000000, 2000000, 4, "10/02/2026", 1000000],
         ["", "2", "", "", "", "", "11/03/2026", 1000000]]


def plan(text, today=date(2026, 3, 31)):
    return plan_question(Question("x", "b", text, today), read_rules(RULEBOOK))


class SideLedgerTests(_Shop):
    def test_trade_ins_and_instalments_from_the_described_books(self):
        catalog = self.shop(README, {"thu.csv": TRADE, "gop.csv": PLANS})
        self.assertEqual(len(trade_ins(catalog)), 3)
        self.assertEqual([p.number for p in instalments(catalog)], ["1", "2"])
        for text, intent, expected in (
                ("Tổng tiền thu máy cũ tháng 2/2026 là bao nhiêu?", "trade_in_total", 4000000),
                ("Tháng 2/2026 cửa hàng thu bao nhiêu máy cũ?", "trade_in_count", 2),
                ("Tháng 2/2026 có bao nhiêu đơn trả góp?", "instalment_count", 1),
                ("Tháng 3/2026 khách góp được bao nhiêu tiền?", "instalment_collected", 2500000),
                ("Đến hết ngày 28/02/2026 tổng tiền góp khách còn phải trả là bao nhiêu?",
                 "instalment_outstanding", 9000000 - 1500000 + 4000000 - 1000000)):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual(result.intent, intent)
                answer = answer_plan({}, catalog, result, RULEBOOK)
                self.assertEqual((answer.type, answer.answer), ("number", expected), answer.reason)

    def test_revenue_of_a_group_is_not_a_trade_in(self):
        self.assertNotIn(getattr(plan("Doanh thu máy cũ tháng 2/2026 là bao nhiêu?"), "intent",
                                 None), {"trade_in_total", "trade_in_count"})

    def test_books_that_do_not_read_cleanly_fail_closed(self):
        for name, files in (
                ("no README words", {"README": README.replace("Giá thu", "Giá").replace("giá thu", "giá")}),
                ("payment before plan", {"gop.csv": [PLANS[0], PLANS[2], *PLANS[1:]]}),
                ("payment without day", {"gop.csv": PLANS[:2] + [["", "1", "", "", "", "", "",
                                                                   1500000]]}),
                ("payment exceeds balance", {"gop.csv": [
                    *PLANS, ["", "2", "", "", "", "", "24/03/2026", 3000000]]}),
                ("negative trade-in", {"thu.csv": TRADE + [["04/03/2026", "Máy D", -1000]]}),
                ("unreadable price", {"thu.csv": TRADE + [["04/03/2026", "Máy D", "?"]]})):
            with self.subTest(name=name):
                readme = files.pop("README", README)
                catalog = self.shop(readme, {"thu.csv": TRADE, "gop.csv": PLANS, **files},
                                    business=name.replace(" ", "_"))
                loader = instalments if "payment" in name else trade_ins
                with self.assertRaises(SideLedgerError):
                    loader(catalog)


class BareGroupNameTests(unittest.TestCase):
    def test_a_group_name_of_two_words_reads_without_nhom_but_not_after_thu(self):
        vocabulary = {"product_group": ["Máy cũ", "Phụ kiện", "Sữa"]}
        rules = read_rules(rulebook() + GROUP)

        def planned(text):
            return plan_question(Question("x", "b", text, date(2026, 3, 31)), rules, (),
                                 vocabulary)
        for text in ("Doanh thu máy cũ tháng 2/2026 là bao nhiêu?",
                     "Doanh thu loại phụ kiện tháng 2/2026 là bao nhiêu?",
                     "Doanh thu loại sữa tháng 2/2026 là bao nhiêu?"):
            with self.subTest(text=text):
                result = planned(text)
                self.assertEqual((result.intent, result.filters[0].field),
                                 ("revenue", "product_group"))
        # One word without "nhóm"/"loại" is not read as a group; nor a trade-in of the group.
        self.assertIsNone(planned("Doanh thu sữa tháng 2/2026 là bao nhiêu?"))
        trade = planned("Tiền thu máy cũ tháng 2/2026 là bao nhiêu?")
        self.assertTrue(trade is None or trade.intent == "trade_in_total")

    def test_existing_product_filter_has_priority_over_a_bare_group_name(self):
        product = "Tủ trưng bày phụ kiện"
        names = "| Tên gọi | Các tên cùng chỉ một thứ. File có mã thì theo mã |\n"
        rules = read_rules(rulebook() + GROUP + names)
        question = Question("x", "b", f"Doanh thu mặt hàng {product} tháng 2/2026 là bao nhiêu?",
                            date(2026, 3, 31))
        result = plan_question(question, rules, (product,), {"product_group": ["Phụ kiện"]})
        self.assertEqual([(f.field, f.value) for f in result.filters], [("product", product)])


if __name__ == "__main__":
    unittest.main()
