"""DATA-03 (10/11): column meanings for POS / sales-software exports whose README lists no columns."""

import csv
import tempfile
import unittest
from pathlib import Path

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.readme_sales import ReadmeSalesError, load_readme_sales

RULEBOOK = """# Synthetic split rules
| Luật | Mặc định |
|---|---|
| Đơn tính vào doanh thu | đơn không huỷ và không trả hàng. Đơn có trả hàng, kể cả chỉ trả một phần, là đơn trả hàng: bỏ cả đơn. Đơn đang giao vẫn tính |
| Đơn bị huỷ | đơn có trạng thái huỷ. Đơn trả hàng không phải đơn huỷ |
| Doanh thu một đơn | tổng thành tiền các dòng hàng, đã trừ giảm giá |
| Bán chạy | theo số lượng quy về đơn vị cơ bản |
| Mặt hàng | Phí phục vụ không phải mặt hàng: không xếp hạng, không đếm. Vẫn tính vào doanh thu |
| Ô để trống ở các dòng sau của cùng một nhóm dòng | giống ô đó ở dòng đầu nhóm |
| Trạng thái đơn | mỗi đơn có đúng một trong bốn trạng thái: hoàn thành, huỷ, trả hàng, đang giao |
| Hai dòng của cùng một đơn giống nhau ở mọi ô | lỗi xuất file, tính một lần |
"""

class _Shop(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"

    def shop(self, readme, files, business="shop"):
        root = self.data / business
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        for name, rows in files.items():
            path = root / name
            if name.endswith(".csv"):
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    csv.writer(stream).writerows(rows)
            else:
                from openpyxl import Workbook

                book = Workbook()
                book.remove(book.active)
                for sheet, sheet_rows in rows.items():
                    worksheet = book.create_sheet(sheet)
                    for row in sheet_rows:
                        worksheet.append(row)
                book.save(path)
        return build_catalog(self.data, business)


class ExportProfileTests(_Shop):
    def pos_files(self):
        return {
            "DanhSachHoaDon.xlsx": {"Sheet1": [
                ["DANH SÁCH HÓA ĐƠN"], [],
                ["Mã hóa đơn", "Thời gian", "Trạng thái", "Tổng tiền hàng", "Khách hàng"],
                ["HD1", "02/01/2026", "Hoàn thành", "45,000", "Khách lẻ"]]},
            "DanhSachHoaDonChiTiet.xlsx": {"Sheet1": [
                ["Mã hóa đơn", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Thành tiền",
                 "Số lượng quy đổi"],
                ["HD1", "SP1", "Trà", 1, "45,000", "45,000", 1]]},
        }

    def test_pos_profile_only_for_a_documented_pos_export(self):
        readme = "# Dữ liệu shop\n\nFile máy POS xuất ra.\n\n## Quy ước\n- Dữ liệu có đến hết 31/01/2026.\n"
        sales, evidence, _ = load_readme_sales(self.shop(readme, self.pos_files()), RULEBOOK)
        self.assertEqual(len(sales), 1)
        self.assertEqual(evidence["column_meanings"], ["pos_export_kiotviet.v1"])
        self.assertEqual(evidence["amount_reconciliation"]["order_goods_vs_line_sum"]["differs"], 0)
        plain = "# Dữ liệu shop\n\nSổ tự ghi.\n"
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(plain, self.pos_files(), business="other"), RULEBOOK)
        self.assertEqual(caught.exception.code, "README_COLUMNS_MISSING")


class SqliteProfileTests(_Shop):
    def test_sqlite_pos_profile_joins_and_drops_rows_identical_in_every_cell(self):
        import sqlite3

        root = self.data / "pos_shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text("# Dữ liệu\n\nFile máy POS xuất ra.\n", encoding="utf-8")
        connection = sqlite3.connect(root / "pos.sqlite")
        connection.executescript("""
            CREATE TABLE don_hang (MaHoaDon TEXT, NgayLap TEXT, TinhTrang TEXT, TongTienHang INT);
            CREATE TABLE chi_tiet_don (MaHoaDon TEXT, SoThuTu INT, MaHangHoa TEXT, SL INT,
                                       SLQuyDoi INT, TriGia INT, GiaVonLucBan INT);
            CREATE TABLE san_pham (MaHangHoa TEXT, TenGoi TEXT, DonViCoSo TEXT, DanhMuc TEXT);
            INSERT INTO don_hang VALUES ('O1', '01/01/2025', 'completed', 30),
                                        ('O2', '1 thg 1 2025', 'returned', 10);
            INSERT INTO chi_tiet_don VALUES ('O1', 1, 'P1', 1, 1, 10, 5), ('O1', 1, 'P1', 1, 1, 10, 5),
                                            ('O1', 2, 'P1', 2, 2, 20, 5), ('O2', 1, 'P1', 1, 1, 10, 6);
            INSERT INTO san_pham VALUES ('P1', 'Trà', 'chai', 'Đồ uống');
        """)
        connection.commit()
        connection.close()
        sales, evidence, _ = load_readme_sales(build_catalog(self.data, "pos_shop"), RULEBOOK)
        self.assertEqual(evidence["column_meanings"], ["pos_export_sqlite.v1"])
        self.assertEqual(evidence["counts"]["duplicates_removed"], 1)
        self.assertEqual(evidence["amount_reconciliation"]["order_goods_vs_line_sum"],
                         {"compared": 2, "differs": 0, "unparsed": 0})
        self.assertEqual([(s.order_id, s.status, s.product_name) for s in sales],
                         [("O1", "valid", "Trà"), ("O1", "valid", "Trà"),
                          ("O2", "returned", "Trà")])


if __name__ == "__main__":
    unittest.main()
