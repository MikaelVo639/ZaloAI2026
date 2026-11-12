"""Sales line lists described from their headers when the README has no column section."""

import unittest
from decimal import Decimal

from datamate.ingestion.header_schema import (
    header_columns,
    header_field,
    looks_like_sales_lines,
    sales_list_name,
)
from datamate.ingestion.readme_sales import ReadmeSalesError, load_readme_sales
from test_ingestion_readme_sales import RULEBOOK, _Shop

README = """# Dữ liệu shop

## File
- `ban_hang.csv` (csv)
- `nhap_hang.csv` (csv)

## Quy ước
- Dữ liệu có đến hết 31/01/2026.
"""
HEADER = ["Số HĐ", "Ngày", "Tình trạng", "Mã hàng", "Tên hàng", "SL", "Đơn giá", "Thành tiền"]


def rows(*extra, count=24):
    lines = [HEADER]
    for i in range(count):
        lines.append([f"HD{i}", f"{1 + i % 28:02d}/01/2026", "Xong", "SP1", "Trà", "2",
                      "15,000", "30,000"])
    return [*lines, *extra]


class HeaderFieldTests(unittest.TestCase):
    def test_generic_ledger_words(self):
        for header, field in (("Mã hóa đơn", "order_id"), ("Đơn số", "order_id"),
                              ("Ngày HĐ", "date"), ("Trạng thái giao", "status"),
                              ("Hàng hóa", "product_name"), ("SL (ĐV cơ bản)", "quantity_base"),
                              ("S.L", "quantity"), ("Giá 1 đv", "unit_price"),
                              ("Thành tiền (đ)", "amount"), ("Giảm (đ)", "discount"),
                              ("Mã khách", "customer_id"), ("Giá vốn lúc bán", None)):
            with self.subTest(header=header):
                self.assertEqual(header_field(header), field)

    def test_two_columns_for_one_field_is_ambiguous(self):
        self.assertIsNone(header_columns([*HEADER, "Tổng tiền"]))
        self.assertIsNone(header_columns([h for h in HEADER if h != "Tình trạng"]))
        self.assertEqual(header_columns(HEADER)["Thành tiền"], "thành tiền sau giảm")

    def test_list_names_and_shapes(self):
        self.assertTrue(sales_list_name("BanHang_2025.xlsx", "Sheet1"))
        self.assertTrue(sales_list_name("DonHang_ChiTiet.xlsx", "T01"))
        for name in ("SoNhapHang.xlsx", "BaoCaoTonKho.xlsx", "BanHang_PhieuThu.xlsx",
                     "DonHang_Lo.xlsx", "DuLieu.xlsx"):
            self.assertFalse(sales_list_name(name, "Sheet1"), name)
        self.assertFalse(looks_like_sales_lines(["Mã hàng", "Tên hàng", "Nhóm hàng"]))


class HeaderSectionTests(_Shop):
    def test_a_ledger_without_column_docs_is_read_from_its_headers(self):
        purchases = [["Số phiếu", "Ngày", "Tên hàng", "SL", "Đơn giá", "Thành tiền"],
                     ["P1", "02/01/2026", "Trà", "10", "5,000", "50,000"]]
        catalog = self.shop(README, {"ban_hang.csv": rows(), "nhap_hang.csv": purchases})
        sales, evidence, eligible = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales), Decimal(24 * 30000))
        self.assertEqual(evidence["tables"], ["ban_hang.csv::ban_hang"])  # not the purchases
        self.assertEqual(len(eligible), 24)
        self.assertNotIn("product_identity", evidence)  # codes present

    def test_names_only_mark_product_identity_unverified(self):
        header = [h for h in HEADER if h != "Mã hàng"]
        lines = [header, *[[c for c, h in zip(row, HEADER) if h != "Mã hàng"]
                           for row in rows()[1:]]]
        _, evidence, _ = load_readme_sales(self.shop(README, {"ban_hang.csv": lines}), RULEBOOK)
        self.assertEqual(evidence["product_identity"], "names_unverified")

    def test_amounts_above_price_times_quantity_are_not_line_amounts(self):
        over = [[f"X{i}", "03/01/2026", "Xong", "SP1", "Trà", "2", "15,000", "45,000"]
                for i in range(5)]
        catalog = self.shop(README, {"ban_hang.csv": rows(*over, count=20)})
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "README_COLUMNS_MISSING")

    def test_one_undescribable_month_blocks_every_month(self):
        readme = README.replace("`ban_hang.csv` (csv)", "`ban_hang.xlsx` (xlsx)")
        other = [["Số HĐ", "Ngày", "Ghi chú TT", "Mã hàng", "Tên hàng", "SL", "Đơn giá",
                  "Thành tiền"],
                 *rows()[1:]]
        catalog = self.shop(readme, {"ban_hang.xlsx": {"T01": rows(), "T02": other}})
        with self.assertRaises(ReadmeSalesError):
            load_readme_sales(catalog, RULEBOOK)


if __name__ == "__main__":
    unittest.main()
