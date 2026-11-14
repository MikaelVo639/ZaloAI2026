"""Sales line lists described from their headers when the README has no column section."""

import unittest
from decimal import Decimal

from datamate.ingestion.header_schema import (
    header_columns,
    header_field,
    looks_like_sales_lines,
    sales_list_name,
    table_role,
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


ORDERS = ["Mã hóa đơn", "Thời gian", "Trạng thái", "Mã KH", "Tổng tiền hàng", "Giảm giá"]
LINES = ["Mã hóa đơn", "STT", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Giảm giá",
         "Thành tiền"]
PRODUCTS = ["Mã hàng", "Tên hàng", "ĐVT", "Nhóm hàng(3 Cấp)", "Giá bán"]


class TableRoleTests(unittest.TestCase):
    def test_database_style_and_export_words(self):
        for header, field in (("SLQuyDoi", "quantity_base"), ("MaHoaDon", "order_id"),
                              ("TinhTrang", "status"), ("GiaDonVi", "unit_price"),
                              ("TriGia", "amount"), ("TenGoi", "product_name"), ("ĐVT", "unit"),
                              ("Slg", "quantity"), ("Giá niêm yết", "unit_price"),
                              ("Khuyến mãi", "discount"), ("IDCuaHang", None), ("HSD", None)):
            with self.subTest(header=header):
                self.assertEqual(header_field(header), field)

    def test_roles_of_a_joined_export(self):
        role, columns = table_role(ORDERS)
        self.assertEqual(role, "orders")
        self.assertEqual(set(columns), {"Mã hóa đơn", "Thời gian", "Trạng thái", "Mã KH"})
        role, columns = table_role(LINES)
        self.assertEqual((role, columns["Thành tiền"]), ("lines", "thành tiền sau giảm"))
        role, columns = table_role(PRODUCTS)
        self.assertEqual(role, "products")
        self.assertEqual(columns, {"Mã hàng": "mã hàng", "Tên hàng": "tên",
                                   "ĐVT": "đơn vị cơ bản", "Nhóm hàng(3 Cấp)": "nhóm hàng"})
        role, columns = table_role(["Mã khách hàng", "Tên khách hàng", "Nhóm khách hàng",
                                    "Loại hình khách"])
        self.assertEqual(columns["Nhóm khách hàng"], "nhóm khách")

    def test_no_role_for_purchases_stock_or_ambiguous_tables(self):
        self.assertIsNone(table_role(["Mã phiếu nhập", "Mã hàng", "Số lượng", "Đơn giá",
                                      "Thành tiền", "Mã hóa đơn"]))
        self.assertIsNone(table_role(["Mã hóa đơn", "Ngày", "Trạng thái", "Mã NCC"]))
        self.assertIsNone(table_role(["Mã hàng", "Tên hàng", "Tồn cuối kỳ", "ĐVT"]))
        self.assertIsNone(table_role([*LINES, "Tổng tiền"]))  # two amount columns
        self.assertIsNone(table_role(["Mã khách hàng", "Tên khách hàng", "Loại hình khách"]))
        _, columns = table_role(["Số HĐ", "Ngày", "Trạng thái", "Số khách"])
        self.assertNotIn("Số khách", columns)  # a head count, not a customer code


def joined_files(orphan=False):
    orders = [ORDERS]
    lines = [LINES]
    for i in range(24):
        orders.append([f"HD{i}", f"{1 + i % 28:02d}/01/2026", "Hoàn thành", "", "30000", "0"])
        lines.append([f"HD{i}", "1", "SP1", "Trà", "2", "15000", "0", "30000"])
    if orphan:
        lines.append(["HD99", "1", "SP1", "Trà", "2", "15000", "0", "30000"])
    receipts = [["Mã nhập hàng", "Thời gian", "Trạng thái", "Mã NCC"],
                ["N1", "02/01/2026", "Đã nhập", "NCC1"]]
    return {"hoa_don.csv": orders, "chi_tiet.csv": lines, "phieu.csv": receipts,
            "san_pham.csv": [PRODUCTS, ["SP1", "Trà xanh", "chai", "Đồ uống", "15000"]]}


class JoinedHeaderSectionTests(_Shop):
    README = README.replace("- `ban_hang.csv` (csv)\n- `nhap_hang.csv` (csv)",
                            "- `hoa_don.csv` (csv)\n- `chi_tiet.csv` (csv)\n"
                            "- `phieu.csv` (csv)\n- `san_pham.csv` (csv)")

    def test_orders_and_lines_are_joined_from_their_headers(self):
        sales, evidence, _ = load_readme_sales(self.shop(self.README, joined_files()), RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales), Decimal(24 * 30000))
        self.assertEqual(evidence["tables"], ["chi_tiet.csv::chi_tiet", "hoa_don.csv::hoa_don",
                                              "san_pham.csv::san_pham"])
        self.assertEqual({s.product_name for s in sales}, {"Trà"})
        self.assertNotIn("product_identity", evidence)

    def test_a_line_without_its_order_abstains(self):
        catalog = self.shop(self.README, joined_files(orphan=True))
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "ORPHAN_FK")

    def test_lines_join_an_order_list_the_readme_documents(self):
        readme = self.README.replace("## Quy ước", "## Cột trong `hoa_don.csv`\n"
                                     "- **Mã hóa đơn**: mã đơn\n- **Thời gian**: ngày bán\n"
                                     "- **Trạng thái**: trạng thái đơn\n\n## Quy ước")
        sales, evidence, _ = load_readme_sales(self.shop(readme, joined_files()), RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales), Decimal(24 * 30000))
        self.assertIn("hoa_don.csv::hoa_don", evidence["tables"])

    def test_two_order_list_shapes_abstain(self):
        files = joined_files()
        files["hoa_don_cu.csv"] = [ORDERS[:3], ["HD0", "01/01/2026", "Hoàn thành"]]
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(self.README, files), RULEBOOK)
        self.assertEqual(caught.exception.code, "README_COLUMNS_MISSING")


if __name__ == "__main__":
    unittest.main()
