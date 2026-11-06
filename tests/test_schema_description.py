"""DATA-03 (10/11): answer-free schema description for direction B prompts."""

import csv
import tempfile
import unittest
from pathlib import Path

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.schema_description import describe_schema, schema_text


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


REPORT_README = """# Dữ liệu shop

File Excel xuất từ phần mềm bán hàng.

## File
- `DonHang.xlsx` (xlsx)
- `QuyDoi.xlsx` (xlsx)

## Cột trong `DonHang.xlsx`
- **Mã đơn hàng**: mã đơn
- **Ngày đặt hàng**: ngày bán
- **Trạng thái đơn hàng**: trạng thái đơn
- **Tên sản phẩm**: tên hàng
- **Số lượng sản phẩm**: số lượng
- **Tổng tiền**: thành tiền sau giảm
- **Đơn vị tính**: đơn vị tính

## Cột trong `QuyDoi.xlsx`
- **Tên sản phẩm**: tên hàng (file chỉ có mặt hàng có đơn vị lớn)
- **Đơn vị quy đổi**: đơn vị lớn (thùng, hộp, tấn...)
- **Tỉ lệ quy đổi**: hệ số: số đơn vị cơ bản trong 1 đơn vị lớn
- **Đơn vị cơ bản**: đơn vị cơ bản

## Quy ước
- Dữ liệu có đến hết 28/02/2026.
- **Quy đổi đơn vị**: số lượng ghi theo đơn vị của dòng, hệ số quy đổi ở `QuyDoi.xlsx`.
"""
REPORT_HEADER = ["Mã đơn hàng", "Ngày đặt hàng", "Trạng thái đơn hàng", "Tên sản phẩm",
                 "Số lượng sản phẩm", "Tổng tiền", "Đơn vị tính"]


class SchemaDescriptionTests(_Shop):
    def test_description_has_structure_and_samples_but_no_aggregates(self):
        orders = {sheet: [["Trạng thái đơn hàng", "Đặt hàng (Đã duyệt)"], [], REPORT_HEADER,
                          ["O1", "05/01/2026", "Xong", "Bia", "1 thùng", "240,000", "thùng"]]
                  for sheet in ("01.2026", "02.2026")}
        conversions = {"Sheet1": [["Tên sản phẩm", "Đơn vị quy đổi", "Tỉ lệ quy đổi",
                                   "Đơn vị cơ bản"], ["Bia", "thùng", 24, "lon"]]}
        catalog = self.shop(REPORT_README, {"DonHang.xlsx": orders, "QuyDoi.xlsx": conversions})
        description = describe_schema(catalog)
        lines = next(t for t in description["tables"] if t["role"] == "sales_lines")
        self.assertEqual(lines["sheets"], ["01.2026", "02.2026"])  # one entry per list
        self.assertEqual(lines["header"], "below title rows")
        amount = next(c for c in lines["columns"] if c["name"] == "Tổng tiền")
        self.assertEqual((amount["type"], amount["field"], amount["unit"]),
                         ("money", "amount", "VND"))
        self.assertEqual(amount["samples"], ["240,000"])
        text = schema_text(description)
        self.assertIn("role=sales_lines", text)
        self.assertIn("Dữ liệu có đến hết 28/02/2026.", text)
        self.assertNotIn("480", text)  # nothing is summed across rows or sheets
        self.assertLessEqual(len(schema_text(description, max_chars=300)), 400)


if __name__ == "__main__":
    unittest.main()
