"""Purchase money in a period and the top supplier (rulebook §5). Shops are synthetic."""
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from test_semantics_periods import rulebook

from datamate.agent import compute
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion import build_catalog
from datamate.ingestion.purchases import PurchaseSourceError, load_purchases
from datamate.ingestion.readme_sales import parse_amount
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

RULE = ("| Tiền nhập hàng trong kỳ | tổng tiền các phiếu nhập có **ngày phiếu** trong kỳ, bỏ phiếu "
        "huỷ. Nhà cung cấp nhập nhiều nhất = nhà cung cấp có tiền nhập cao nhất |")
BOOK = rulebook() + RULE + "\n"
BOOK_README = """# Shop

## Cột trong `so_nhap.csv`
- **Số phiếu**: mã phiếu nhập
- **Ngày nhập**: ngày nhập
- **Nhà cung cấp**: nhà cung cấp
- **Thành tiền**: tiền nhập của cả dòng

## Quy ước
- Dữ liệu có đến hết 14/07/2026.
"""
BOOK_ROWS = ("Số phiếu,Ngày nhập,Nhà cung cấp,Thành tiền\n"
             "PN1,02/06/2026,Cty An,\"19,782tr\"\nPN1,02/06/2026,Cty An,5tr\n"
             "PN2,10/06/2026,Cty Bình,1200k\nPN3,05/07/2026,Cty Bình,30tr\n")
LIST_README = """# Shop

## Cột trong `phieu.csv`
- **Mã**: mã phiếu nhập
- **Ngày**: ngày nhập
- **Mã NCC**: mã nhà cung cấp
- **Tổng**: tổng tiền phiếu nhập
- **TT**: trạng thái phiếu nhập

## Quy ước
- Dữ liệu có đến hết 14/07/2026.
"""
LIST_ROWS = ("Mã,Ngày,Mã NCC,Tổng,TT\nPN1,02/06/2026,NCC1,1000000,Đã nhập hàng\n"
             "PN2,03/06/2026,NCC2,500000,Đã hủy\n")


def plan(text):
    return plan_question(Question("x", "b", text, date(2026, 7, 14)), read_rules(BOOK))


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def catalog(self, readme, name, rows):
        shop = Path(self.directory.name) / "b"
        shop.mkdir(exist_ok=True)
        (shop / "README.md").write_text(readme, encoding="utf-8")
        (shop / name).write_text(rows, encoding="utf-8")
        return build_catalog(Path(self.directory.name), "b")

    def test_shorthand_millions_with_a_decimal_comma(self):
        self.assertEqual(parse_amount("19,782tr"), Decimal("19782000"))
        self.assertEqual(parse_amount("16,56tr"), Decimal("16560000"))
        self.assertEqual(parse_amount("9tr960"), Decimal("9960000"))
        self.assertEqual(parse_amount("1.250k"), Decimal("1250000"))

    def test_book_lines_and_receipt_list(self):
        book = load_purchases(self.catalog(BOOK_README, "so_nhap.csv", BOOK_ROWS))
        self.assertTrue(book.supplier_names)
        self.assertEqual({r.receipt_id: r.total for r in book.receipts},
                         {"PN1": Decimal("24782000"), "PN2": Decimal("1200000"),
                          "PN3": Decimal("30000000")})
        receipts = load_purchases(self.catalog(LIST_README, "phieu.csv", LIST_ROWS))
        self.assertFalse(receipts.supplier_names)
        self.assertEqual([r.cancelled for r in receipts.receipts], [False, True])
        with self.assertRaises(PurchaseSourceError):
            load_purchases(self.catalog(LIST_README, "phieu.csv",
                                        LIST_ROWS.replace("Đã hủy", "Phiếu tạm")))

    def test_repeated_totals_and_supplier_identity(self):
        readme = LIST_README.replace("- **Mã NCC**", "- **Tên NCC**: nhà cung cấp\n"
                                     "- **STT**: số thứ tự dòng trong phiếu nhập\n- **Mã NCC**")
        rows = ("Mã,Ngày,Mã NCC,Tên NCC,STT,Tổng,TT\n"
                "PN1,02/06/2026,A,Cty An,0,2000000,Đã nhập hàng\n"
                "PN1,02/06/2026,A,Cty An,2,2000000,Đã nhập hàng\n"
                "PN1,02/06/2026,A,Cty An,3,2000000,Đã nhập hàng\n"
                "PN2,03/06/2026,B,Cty Bình,1,3000000,Đã nhập hàng\n")
        catalog = self.catalog(readme, "phieu.csv", rows)
        total = compute.answer_plan({}, catalog, plan("Tổng tiền nhập hàng tháng 6/2026?"))
        self.assertEqual(total.answer, 5_000_000)
        top = plan("Tháng 6/2026 tiền nhập hàng từ nhà cung cấp nào cao nhất?")
        self.assertEqual(compute.answer_plan({}, catalog, top).answer, "Cty Bình")

        # A second supplier uses the same display name as A. Grouping by name would
        # incorrectly rank their combined value above B.
        rows += "PN3,04/06/2026,C,Cty An,1,1500000,Đã nhập hàng\n"
        rows += "PN4,05/06/2026,A,Cty An,1,100000,Đã nhập hàng\n"
        catalog = self.catalog(readme, "phieu.csv", rows)
        self.assertEqual(compute.answer_plan({}, catalog, top).answer, "Cty Bình")
        book = BOOK + "| Nhà cung cấp chính | nhà cung cấp có nhiều phiếu nhập nhất, bỏ phiếu huỷ |\n"
        main = plan_question(Question("q", "b", "Tháng 6/2026 nhà cung cấp chính là ai?",
                                      date(2026, 7, 14)), read_rules(book))
        self.assertEqual(compute.answer_plan({}, catalog, main).answer, "Cty An")
        conflict = self.catalog(readme, "phieu.csv", rows.replace(",2,2000000,", ",2,1000000,"))
        self.assertEqual(compute.answer_plan({}, conflict, top).type, "abstain")
        missing_code = self.catalog(readme, "phieu.csv", rows.replace(",C,Cty An,", ",,Cty An,"))
        self.assertEqual(compute.answer_plan({}, missing_code, main).type, "abstain")
        missing_line = self.catalog(readme, "phieu.csv", rows.replace(",0,2000000,", ",,2000000,"))
        self.assertEqual(compute.answer_plan({}, missing_line, top).type, "abstain")

    def test_wordings_and_answers(self):
        for text, intent in (
            ("Tổng tiền nhập hàng tháng 6/2026 là bao nhiêu?", "purchase_total"),
            ("Tháng 6/2026 cửa hàng bỏ ra bao nhiêu tiền nhập hàng?", "purchase_total"),
            ("Tháng 6/2026 nhập hàng hết bao nhiêu tiền?", "purchase_total"),
            ("Tháng 6/2026 tổng giá trị các phiếu nhập hàng là bao nhiêu?", "purchase_total"),
            ("Tháng 6/2026 tiền nhập hàng từ nhà cung cấp nào cao nhất?", "top_supplier"),
        ):
            with self.subTest(text=text):
                self.assertEqual(plan(text).intent, intent)
        self.assertIsNone(plan("Tổng tiền nhập hàng từ Cty An tháng 6/2026 là bao nhiêu?"))
        catalog = self.catalog(BOOK_README, "so_nhap.csv", BOOK_ROWS)
        for text, expected in (("Tổng tiền nhập hàng tháng 6/2026 là bao nhiêu?", 25982000),
                               ("Tháng 6/2026 tiền nhập hàng từ nhà cung cấp nào cao nhất?",
                                "Cty An")):
            with self.subTest(text=text):
                result = plan(text)
                candidate = compute.answer_plan({}, catalog, result, BOOK)
                self.assertEqual(candidate.answer, expected)
                self.assertIs(check_executed(candidate, result), candidate)

    def test_supplier_codes_cannot_name_the_supplier(self):
        catalog = self.catalog(LIST_README, "phieu.csv", LIST_ROWS)
        result = plan("Tháng 6/2026 tiền nhập hàng từ nhà cung cấp nào cao nhất?")
        self.assertEqual(compute.answer_plan({}, catalog, result, BOOK).type, "abstain")
        result = plan("Tổng tiền nhập hàng tháng 6/2026 là bao nhiêu?")
        self.assertEqual(compute.answer_plan({}, catalog, result, BOOK).answer, 1000000)


if __name__ == "__main__":
    unittest.main()
