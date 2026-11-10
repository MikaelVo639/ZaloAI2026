"""Average purchase price of one product in a month (rulebook §5). Shops are synthetic."""
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
from datamate.ingestion.purchases import PurchaseSourceError, load_purchase_lines
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

NAMES = "| Tên gọi | Tên gọi khác nhau cùng chỉ một thứ là một mặt hàng; file có mã thì theo mã |"
RULE = ("| Giá nhập bình quân một mặt hàng trong tháng | bình quân **theo số lượng nhập**, "
        "**không** phải trung bình cộng các mức giá nhập; theo đơn vị nhập, các phiếu có ngày "
        "phiếu trong tháng, bỏ phiếu huỷ |")
BOOK = rulebook() + NAMES + "\n" + RULE + "\n"
CONVENTION = ("- **Nhập hàng**: **Giá nhập** là giá nhập một đơn vị, theo đơn vị nhập của dòng; "
              "**Thành tiền** là tiền nhập của cả dòng.\n")
BOOK_README = """# Shop

## Cột trong `so_nhap.csv`
- **Số phiếu**: mã phiếu nhập
- **Ngày nhập**: ngày nhập
- **Product**: tên hàng
- **ĐVT**: đơn vị nhập
- **SL nhập**: số lượng nhập
- **Giá nhập**: giá nhập một đơn vị (theo đơn vị nhập của dòng)
- **Thành tiền**: tiền nhập của cả dòng

## Quy ước
- Dữ liệu có đến hết 14/07/2026.
""" + CONVENTION
# Mean of the two June prices is 205000; weighted by quantity it is 6170000 / 30.
BOOK_ROWS = ("Số phiếu,Ngày nhập,Product,ĐVT,SL nhập,Giá nhập,Thành tiền\n"
             "PN1,02/06/2026,Bia Tiger lon,thùng,10,200000,2000000\n"
             "PN1,02/06/2026,Cát xây tô,xe 4m3,2,950000,1900000\n"
             "PN2,20/06/2026,Bia Tiger lon,thùng,20,208500,4170000\n"
             "PN3,03/07/2026,Bia Tiger lon,thùng,5,210000,1050000\n")
LIST_README = """# Shop

## Cột trong `phieu.csv`
- **Mã nhập hàng**: mã phiếu nhập
- **Thời gian**: ngày nhập
- **Cần trả NCC**: tổng tiền phiếu nhập
- **Trạng thái**: trạng thái phiếu nhập

## Cột trong `chi_tiet.csv`
- **Mã nhập hàng**: mã phiếu nhập
- **Mã hàng**: mã hàng
- **ĐVT**: đơn vị tính
- **Số lượng**: số lượng
- **Đơn giá**: giá nhập một đơn vị (theo đơn vị nhập của dòng)

## Quy ước
- Dữ liệu có đến hết 14/07/2026.
- **Nhập hàng**: **Đơn giá** là giá nhập một đơn vị, theo đơn vị nhập của dòng; **Cần trả NCC** là tổng tiền của cả phiếu nhập.
"""
RECEIPTS = ("Mã nhập hàng,Thời gian,Cần trả NCC,Trạng thái\n"
            "PN1,02/06/2026,2000000,Đã nhập hàng\nPN2,05/06/2026,900000,Đã hủy\n")
LINES = "Mã nhập hàng,Mã hàng,ĐVT,Số lượng,Đơn giá\nPN1,SP1,thùng,10,200000\nPN2,SP1,thùng,3,300000\n"


def plan(text, today="2026-07-14"):
    names = ["Bia Tiger lon", "Cát xây tô", "SP1"]
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), read_rules(BOOK),
                         names)


class PurchasePriceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def catalog(self, readme, files):
        shop = Path(self.directory.name) / "b"
        shop.mkdir(exist_ok=True)
        (shop / "README.md").write_text(readme, encoding="utf-8")
        for name, rows in files.items():
            (shop / name).write_text(rows, encoding="utf-8")
        return build_catalog(Path(self.directory.name), "b")

    def test_lines_from_a_book_and_from_a_receipt_list(self):
        book = load_purchase_lines(self.catalog(BOOK_README, {"so_nhap.csv": BOOK_ROWS}))
        self.assertEqual([(line.product_name, line.unit, line.quantity, line.unit_price)
                          for line in book.lines][:2],
                         [("Bia Tiger lon", "thùng", Decimal(10), Decimal(200000)),
                          ("Cát xây tô", "xe 4m3", Decimal(2), Decimal(950000))])
        joined = load_purchase_lines(self.catalog(LIST_README, {"phieu.csv": RECEIPTS,
                                                                "chi_tiet.csv": LINES}))
        self.assertEqual([(line.product_id, line.day, line.cancelled) for line in joined.lines],
                         [("SP1", date(2026, 6, 2), False), ("SP1", date(2026, 6, 5), True)])
        with self.assertRaises(PurchaseSourceError):  # line of a receipt the list lacks
            load_purchase_lines(self.catalog(LIST_README, {
                "phieu.csv": RECEIPTS, "chi_tiet.csv": LINES + "PN9,SP1,thùng,1,1\n"}))
        with self.assertRaises(PurchaseSourceError):  # no README price convention
            load_purchase_lines(self.catalog(BOOK_README.replace(CONVENTION, ""),
                                             {"so_nhap.csv": BOOK_ROWS}))

    def test_purchase_minus_sales_converts_units_and_excludes_invalid_orders(self):
        from test_ingestion_readme_sales import FLAT_README

        rules = (BOOK
                 + "| Trạng thái đơn | mỗi đơn có đúng một trong bốn trạng thái: "
                   "hoàn thành, huỷ, trả hàng, đang giao |\n"
                 + "| Nhập trừ bán | phần bán là số lượng của đơn tính vào doanh thu, "
                   "quy về cùng đơn vị với phần nhập |\n")
        readme = BOOK_README + FLAT_README + (
            "- **Quy đổi đơn vị**:\n  - Bia Tiger lon: 1 thùng = 10 lon\n")
        purchases = ("Số phiếu,Ngày nhập,Product,ĐVT,SL nhập,Giá nhập,Thành tiền\n"
                     "P,03/06/2026,Bia Tiger lon,thùng,1,200000,200000\n"
                     "OLD,31/05/2026,Bia Tiger lon,lon,5,1,5\n")
        sales = ("Số HĐ,Ngày tháng,Kết quả,Nội dung,SL,ĐVT,Thành tiền,Quy ra lẻ,Đối tác\n"
                 "A,04/06/2026,hoàn thành,Bia Tiger lon,4,lon,500,4,Khách lẻ\n"
                 "B,05/06/2026,huỷ,Bia Tiger lon,9,lon,500,9,Khách lẻ\n"
                 "C,06/06/2026,trả hàng,Bia Tiger lon,8,lon,500,8,Khách lẻ\n")
        query = Question("q", "b", "Chênh lệch nhập trừ bán Bia Tiger lon T6/2026 là bao nhiêu lon?",
                         date(2026, 7, 14))
        parsed = plan_question(query, read_rules(rules), ["Bia Tiger lon"])
        self.assertIsNotNone(parsed)
        files = {"so_nhap.csv": purchases, "ban_hang.csv": sales}
        candidate = check_executed(compute.answer_plan({}, self.catalog(readme, files),
                                                       parsed, rules), parsed)
        self.assertEqual((candidate.type, candidate.answer), ("number", 6), candidate.reason)
        without_factor = self.catalog(readme.replace("  - Bia Tiger lon: 1 thùng = 10 lon\n", ""),
                                      files)
        self.assertEqual(compute.answer_plan({}, without_factor, parsed, rules).type, "abstain")

    def test_wordings(self):
        for text, unit in (
            ("Giá nhập bình quân một thùng Bia Tiger lon trong tháng 6 năm 2026 là bao nhiêu?",
             "thung"),
            ("Tháng 6/2026 giá nhập bình quân một thùng Bia Tiger lon là bao nhiêu vậy?", "thung"),
            ("Trong tháng 6/2026, Bia Tiger lon nhập vào giá bình quân một thùng là bao nhiêu?",
             "thung"),
            ("thang 6/2026 cua hang nhap bia tiger lon binh quan bn tien mot thung z", "thung"),
            ("gia nhap binh quan mot xe 4m3 cat xay to trong thang 6 nam 2026 la bn", "xe 4m3"),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.unit, result.start, result.end),
                                 ("avg_purchase_price", unit, date(2026, 6, 1),
                                  date(2026, 6, 30)))
        for text in ("Giá nhập bình quân một thùng hàng tháng 6/2026 là bao nhiêu?",
                     "Giá nhập bình quân một thùng Bia Tiger lon từ nhà cung cấp Cty An "
                     "tháng 6/2026 là bao nhiêu?",
                     "Giá nhập bình quân một thùng Bia Tiger lon tháng 8/2026 là bao nhiêu?"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))

    def test_weighted_by_quantity_in_the_purchase_unit(self):
        catalog = self.catalog(BOOK_README, {"so_nhap.csv": BOOK_ROWS})
        for text, expected in (
            ("Tháng 6/2026 giá nhập bình quân một thùng Bia Tiger lon là bao nhiêu?", 205667),
            ("Giá nhập bình quân một xe 4m3 Cát xây tô trong tháng 6 năm 2026 là bao nhiêu?",
             950000),
            ("Tháng 6/2026 giá nhập bình quân một lon Bia Tiger lon là bao nhiêu?", None),
            ("Tháng 5/2026 giá nhập bình quân một thùng Bia Tiger lon là bao nhiêu?", None),
            ("Tháng 7/2026 giá nhập bình quân một thùng Bia Tiger lon là bao nhiêu?", None),
        ):
            with self.subTest(text=text):
                result = plan(text)
                if result is None:  # the month is not over: no Plan at all
                    self.assertIn("7/2026", text)
                    continue
                candidate = compute.answer_plan({}, catalog, result, BOOK)
                if expected is None:  # other unit, before the records
                    self.assertEqual(candidate.type, "abstain")
                    continue
                self.assertEqual(candidate.answer, expected)
                self.assertIs(check_executed(candidate, result), candidate)

    def test_cancelled_receipts_are_dropped(self):
        lines = load_purchase_lines(self.catalog(LIST_README, {"phieu.csv": RECEIPTS,
                                                               "chi_tiet.csv": LINES})).lines
        selected = compute.purchase_lines_of(lines, "Bia Tiger lon", {"SP1"})
        self.assertEqual(compute.average_purchase_price(
            selected, date(2026, 6, 1), date(2026, 6, 30)), Decimal(200000))
        self.assertEqual(compute.purchase_lines_of(lines, "Bia Tiger lon", set()), [])


if __name__ == "__main__":
    unittest.main()
