"""DATA-03 / SEM-02: optional context on Sale (base unit, groups, customer) from README lists."""

import csv
import tempfile
import unittest
from pathlib import Path

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.readme_sales import load_readme_sales

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


README = """# Dữ liệu shop

## Cột trong `don.csv`
- **Mã CT**: mã đơn
- **Ngày**: ngày bán
- **Xử lý**: trạng thái đơn
- **KH**: mã khách (trống nếu khách lẻ)

## Cột trong `chi_tiet.csv`
- **Mã CT**: mã đơn
- **Mã SP**: mã hàng
- **SL**: số lượng
- **ĐVT**: đơn vị tính
- **Tiền**: thành tiền sau giảm

## Cột trong `san_pham.csv`
- **Mã SP**: mã hàng
- **Tên SP**: tên
- **ĐV**: đơn vị cơ bản
- **Nhóm**: nhóm hàng

## Cột trong `khach.csv`
- **Mã**: mã khách
- **Tên**: tên khách
- **Loại**: nhóm khách
"""


class SaleContextTests(_Shop):
    def files(self, customers=(("K1", "Chị Lan", "thân thiết"),)):
        return {
            "don.csv": [["Mã CT", "Ngày", "Xử lý", "KH"], ["C1", "2026-01-05", "completed", "K1"],
                        ["C2", "2026-01-05", "completed", ""]],
            "chi_tiet.csv": [["Mã CT", "Mã SP", "SL", "ĐVT", "Tiền"], ["C1", "P1", "2", "ly", "40000"],
                             ["C2", "P2", "1", "kg", "10000"]],
            "san_pham.csv": [["Mã SP", "Tên SP", "ĐV", "Nhóm"], ["P1", "Trà", "ly", "Đồ uống"],
                             ["P2", "Đường", "kg", "Gia vị"]],
            "khach.csv": [["Mã", "Tên", "Loại"], *customers],
        }

    def test_groups_base_units_and_customer_list(self):
        sales, evidence, _ = load_readme_sales(self.shop(README, self.files()), RULEBOOK)
        first, second = sales
        self.assertEqual((first.base_unit, first.product_group), ("ly", "Đồ uống"))
        self.assertEqual((first.customer_id, first.customer_name, first.customer_group),
                         ("K1", "Chị Lan", "thân thiết"))
        self.assertEqual((second.customer_id, second.customer_group), (None, None))  # walk-in
        self.assertEqual(evidence["base_units"], {"resolved": 2, "conflicting": 0})
        self.assertIn("customers", evidence["sources"])

    def test_missing_customer_list_does_not_block_sales(self):
        files = self.files()
        del files["khach.csv"]
        sales, evidence, _ = load_readme_sales(self.shop(README, files), RULEBOOK)
        self.assertEqual((len(sales), sales[0].customer_group), (2, None))
        self.assertNotIn("customers", evidence["sources"])

    def test_duplicate_customer_codes_raise(self):
        from datamate.ingestion.readme_sales import ReadmeSalesError

        files = self.files(customers=(("K1", "Chị Lan", "sỉ"), ("K1", "Lan", "lẻ")))
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(README, files), RULEBOOK)
        self.assertEqual(caught.exception.code, "DUPLICATE_PARENT_KEY")


if __name__ == "__main__":
    unittest.main()
