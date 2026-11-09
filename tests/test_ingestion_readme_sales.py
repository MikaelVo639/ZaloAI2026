"""DATA-03 (10/11): sales adapter driven by the column meanings in the business README."""

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from datamate.agent import sales_source
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    _note_state,
    _resolve_status,
    _rules,
    load_readme_sales,
    meaning_field,
    parse_amount,
    parse_day,
    parse_quantity,
    parse_sections,
)
from datamate.ingestion.status import StatusError

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
RULEBOOK_WITH_NOTES = RULEBOOK + """
| Sổ không có cột trạng thái | trạng thái đơn ghi ở cột Ghi chú. Ghi chú không nhắc trạng thái là đơn hoàn thành |
"""

FLAT_README = """# Dữ liệu shop

## File
- `ban_hang.csv` (csv)

## Cột trong `ban_hang.csv`
- **Số HĐ**: mã đơn
- **Ngày tháng**: ngày bán
- **Kết quả**: trạng thái đơn
- **Nội dung**: tên hàng
- **SL**: số lượng
- **ĐVT**: đơn vị tính
- **Thành tiền**: thành tiền sau giảm
- **Quy ra lẻ**: số lượng đã đổi ra đơn vị cơ bản
- **Đối tác**: tên khách (Khách lẻ nếu không lưu)

## Quy ước
- Dữ liệu có đến hết 31/01/2026.
"""
FLAT_HEADER = ["Số HĐ", "Ngày tháng", "Kết quả", "Nội dung", "SL", "ĐVT", "Thành tiền",
               "Quy ra lẻ", "Đối tác"]


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


class ParsingTests(unittest.TestCase):
    def test_amounts_as_shops_write_them(self):
        cases = {
            "45,000.0000": "45000", "55.000đ": "55000", "25k": "25000", "1tr440": "1440000",
            "2tr5": "2500000", "2,5k": "2500", "1.500.000": "1500000", "52000": "52000",
            "-45,000": "-45000", "1.250.000,50": "1250000.50", "120.000 VNĐ": "120000",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), Decimal(expected))
        self.assertEqual(parse_amount(45000), Decimal(45000))
        for bad in ("", None, "abc", "1,2,3.4.5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_amount(bad)

    def test_quantities_with_units(self):
        cases = {"3 chai": 3, "1 bao 25kg": 1, "2 xe 4m3": 2, "1,5 kg": Decimal("1.5"),
                 " 10 ": 10, "-2": -2}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_quantity(raw), Decimal(str(expected)))
        with self.assertRaises(ValueError):
            parse_quantity("chai")

    def test_dates(self):
        self.assertEqual(parse_day(45689), date(2025, 2, 1))  # Excel serial
        self.assertEqual(parse_day("45689"), date(2025, 2, 1))
        self.assertEqual(parse_day("01/02/2025"), date(2025, 2, 1))
        self.assertEqual(parse_day("1 thg 2 2025"), date(2025, 2, 1))
        self.assertEqual(parse_day("1/2", "Tháng 02-2025"), date(2025, 2, 1))
        self.assertEqual(parse_day("1/2", "T02_2025"), date(2025, 2, 1))  # year after "_"
        with self.assertRaises(ValueError):
            parse_day("1/2", "Sheet1")  # no year anywhere: not guessed

    def test_meanings(self):
        self.assertEqual(meaning_field("tên khách (Khách lẻ nếu không lưu)"), "customer_name")
        self.assertEqual(meaning_field("đơn vị lớn (thùng, hộp, tấn...)"), "big_unit")
        self.assertEqual(meaning_field("hệ số: số đơn vị cơ bản trong 1 đơn vị lớn"), "factor")
        self.assertEqual(meaning_field("thành tiền sau giảm"), "amount")
        self.assertEqual(meaning_field("tồn kho (đơn vị cơ bản)"), "_other")
        self.assertIsNone(meaning_field("biển số xe"))
        sections = parse_sections(FLAT_README)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].fields["amount"], "Thành tiền")


class FlatReadmeTests(_Shop):
    def test_zip_filename_encoding_preserves_sources_and_schema_meanings(self):
        from datamate.ingestion.schema_description import describe_schema

        documented = "Bảng bán hàng.csv"
        extracted = documented.encode("utf-8").decode("cp437")
        catalog = self.shop(FLAT_README.replace("ban_hang.csv", documented),
                            {extracted: self.rows()})
        sales, evidence, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales if s.status == "valid"), Decimal(100000))
        self.assertTrue(catalog.tables[0].path.exists())
        self.assertEqual(catalog.tables[0].path.name, extracted)
        self.assertIn(extracted, evidence["tables"][0])
        description = describe_schema(catalog)
        self.assertEqual(description["tables"][0]["documented_by"], "readme")
        self.assertEqual(description["tables"][0]["columns"][0]["field"], "order_id")

    def test_equivalent_filenames_do_not_silently_union_sources(self):
        documented = "Bảng bán hàng.csv"
        extracted = documented.encode("utf-8").decode("cp437")
        catalog = self.shop(FLAT_README.replace("ban_hang.csv", documented),
                            {documented: self.rows(), extracted: self.rows()})
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "AMBIGUOUS_SOURCE")

    def rows(self, *extra):
        return [FLAT_HEADER,
                ["HD1", "05/01/2026", "Xong", "Cà phê", "2 ly", "ly", "50,000", "2", "An"],
                ["HD1", "05/01/2026", "Xong", "Bạc xỉu", "1", "ly", "30.000đ", "1", "An"],
                ["HD1", "05/01/2026", "Xong", "Bạc xỉu", "1", "ly", "30.000đ", "1", "An"],
                ["HD2", "06/01/2026", "khách hủy", "Cà phê", "1", "ly", "25k", "1", ""],
                ["", "", "", "Tổng ngày 06/01", "", "", "105,000", "", ""],
                ["HD3", "07/01/2026", "Đang giao", "Trà", "1", "ly", "20000", "1", ""],
                *extra]

    def test_renamed_columns_statuses_duplicates_and_total_rows(self):
        catalog = self.shop(FLAT_README, {"ban_hang.csv": self.rows()})
        sales, evidence, eligible = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(evidence["counts"]["duplicates_removed"], 1)
        self.assertEqual(evidence["counts"]["summary_rows_skipped"], 1)
        self.assertEqual(evidence["status_counts"], {"cancelled": 1, "valid": 2})
        valid = [s for s in sales if s.status == "valid"]
        self.assertEqual(sum(s.amount for s in valid), Decimal(100000))
        self.assertEqual(len({s.order_id for s in valid}), 2)
        self.assertEqual(sales[0].day, date(2026, 1, 5))
        self.assertEqual(sales[0].product_id, "Cà phê")  # no codes: the name identifies
        self.assertEqual(eligible, frozenset(range(len(sales))))

    def test_unknown_status_raises(self):
        rows = self.rows(["HD4", "08/01/2026", "Lưu tạm", "Trà", "1", "ly", "20000", "1", ""])
        catalog = self.shop(FLAT_README, {"ban_hang.csv": rows})
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "UNKNOWN_STATUS")

    def test_missing_status_rule_raises(self):
        catalog = self.shop(FLAT_README, {"ban_hang.csv": self.rows()})
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK.replace("| Trạng thái đơn |", "| Khác |"))
        self.assertEqual(caught.exception.code, "RULE_CAPABILITY_MISSING")

    def test_conflicting_order_dates_raise(self):
        rows = self.rows(["HD3", "09/01/2026", "Đang giao", "Trà", "1", "ly", "20000", "1", ""])
        catalog = self.shop(FLAT_README, {"ban_hang.csv": rows})
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "CONFLICTING_ORDER")

    def test_negative_lines_return_the_whole_order_when_readme_says_so(self):
        readme = FLAT_README.replace(
            "## Quy ước\n", "## Quy ước\n- **Trả hàng**: hàng trả ghi số lượng âm, cùng mã đơn "
            "với đơn đã bán: đơn có dòng số lượng âm là đơn có trả hàng.\n")
        rows = self.rows(["HD1", "10/01/2026", "Xong", "Bạc xỉu", "-1", "ly", "-30.000đ", "-1", ""])
        sales, evidence, _ = load_readme_sales(self.shop(readme, {"ban_hang.csv": rows}),
                                               RULEBOOK)
        self.assertEqual({s.status for s in sales if s.order_id == "HD1"}, {"returned"})
        self.assertEqual({s.day for s in sales if s.order_id == "HD1"}, {date(2026, 1, 5)})
        self.assertEqual(evidence["status_counts"]["returned"], 1)


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


class ConversionListTests(_Shop):
    def test_conversions_listed_in_the_readme(self):
        readme = FLAT_README.replace("- **Quy ra lẻ**: số lượng đã đổi ra đơn vị cơ bản\n", "")
        readme += ("- **Quy đổi đơn vị**: số lượng ghi theo đơn vị của dòng:\n"
                   "  - Bia Tiger 330ml: 1 thùng = 24 lon\n")
        header = [c for c in FLAT_HEADER if c != "Quy ra lẻ"]
        rows = [header,
                ["HD1", "05/01/2026", "Xong", "Bia Tiger 330ml", "2 thùng", "thùng", "600,000", ""],
                ["HD1", "05/01/2026", "Xong", "Bia Tiger 330ml", "3", "lon", "45,000", ""],
                ["HD2", "05/01/2026", "Xong", "Muối", "1", "gói", "6,000", ""]]
        sales, evidence, _ = load_readme_sales(self.shop(readme, {"ban_hang.csv": rows}), RULEBOOK)
        self.assertEqual([s.quantity_base for s in sales], [48, 3, 1])
        self.assertEqual(evidence["quantity_base_source"],
                         {"conversion": 1, "base_unit": 1, "no_larger_unit": 1})


class ReportReadmeTests(_Shop):
    def test_documented_month_year_filename_template_unions_only_matching_files(self):
        readme = REPORT_README.replace("`DonHang.xlsx`", "`BanHang_T<tháng>_<năm>.xlsx`")
        header = REPORT_HEADER
        files = {
            "BanHang_T1_2026.xlsx": {"DonHang_ChiTiet": [header, header,
                ["O1", "05/01/2026", "Xong", "Bia", 1, "10,000", "lon"]]},
            "BanHang_T2_2026.xlsx": {"DonHang_ChiTiet": [header,
                ["O2", "05/02/2026", "Xong", "Muối", 1, "6,000", "gói"]]},
            "QuyDoi.xlsx": {"Sheet1": [["Tên sản phẩm", "Đơn vị quy đổi",
                                           "Tỉ lệ quy đổi", "Đơn vị cơ bản"]]},
        }
        sales, evidence, _ = load_readme_sales(self.shop(readme, files), RULEBOOK)
        self.assertEqual({sale.order_id for sale in sales}, {"O1", "O2"})
        self.assertEqual(len(evidence["sources"]["lines"]), 1)
        self.assertEqual(len(evidence["tables"]), 3)
        self.assertEqual(evidence["counts"]["repeated_headers_skipped"], 1)

    def test_title_rows_monthly_sheets_blank_cells_and_conversions(self):
        orders = {
            "01.2026": [["Trạng thái đơn hàng", "Đặt hàng (Đã duyệt)"], [], REPORT_HEADER,
                        ["O1", "05/01/2026", "Đã hoàn thành", "Bia", "1 thùng", "240,000", "thùng"],
                        [None, None, None, "Bia", 3, "30,000", "lon"],
                        [None, None, None, "Muối", "2 gói", "12,000", "gói"]],
            "02.2026": [["Trạng thái đơn hàng", "Đặt hàng (Đã duyệt)"], [], REPORT_HEADER,
                        ["O2", 46055, "Đã hoàn hàng", "Bia", 2, "20,000", "lon"]],
        }
        conversions = {"Sheet1": [["Tên sản phẩm", "Đơn vị quy đổi", "Tỉ lệ quy đổi",
                                   "Đơn vị cơ bản"], ["Bia", "thùng", 24, "lon"]]}
        catalog = self.shop(REPORT_README, {"DonHang.xlsx": orders, "QuyDoi.xlsx": conversions})
        sales, evidence, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(evidence["counts"]["order_cells_inherited"], 2)
        self.assertEqual([s.order_id for s in sales], ["O1", "O1", "O1", "O2"])
        self.assertEqual([s.quantity_base for s in sales], [24, 3, 2, 2])
        self.assertEqual(evidence["quantity_base_source"],
                         {"conversion": 1, "base_unit": 2, "no_larger_unit": 1})
        self.assertEqual(sales[3].day, date(2026, 2, 2))
        self.assertEqual(sales[3].status, "returned")

    def test_blank_order_cells_need_the_rule(self):
        orders = {"01.2026": [REPORT_HEADER,
                              ["O1", "05/01/2026", "Xong", "Bia", 1, "10,000", "lon"],
                              [None, None, None, "Muối", 1, "6,000", "gói"]]}
        conversions = {"Sheet1": [["Tên sản phẩm", "Đơn vị quy đổi", "Tỉ lệ quy đổi",
                                   "Đơn vị cơ bản"]]}
        catalog = self.shop(REPORT_README, {"DonHang.xlsx": orders, "QuyDoi.xlsx": conversions})
        rules = RULEBOOK.replace("| Ô để trống ở các dòng sau của cùng một nhóm dòng |",
                                 "| Ô trống |")
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, rules)
        self.assertEqual(caught.exception.code, "MISSING_KEY")


JOINED_README = """# Dữ liệu shop

## File
- `don.csv` (csv)
- `chi_tiet.csv` (csv)
- `san_pham.csv` (csv)
- `lo.csv` (csv)

## Cột trong `don.csv`
- **Mã CT**: mã đơn
- **Ngày**: ngày bán
- **Xử lý**: trạng thái đơn
- **Tiền hàng**: tổng tiền hàng của đơn (cộng thành tiền các dòng), trước giảm giá của đơn

## Cột trong `chi_tiet.csv`
- **Mã CT**: mã đơn
- **Dòng**: số thứ tự dòng trong đơn
- **Mã SP**: mã hàng
- **SL**: số lượng
- **Tiền**: thành tiền sau giảm
- **SL lẻ**: số lượng đã đổi ra đơn vị cơ bản

## Cột trong `san_pham.csv`
- **Mã SP**: mã hàng
- **Tên SP**: tên
- **ĐV**: đơn vị cơ bản
- **Nhóm**: nhóm hàng

## Cột trong `lo.csv`
- **Mã CT**: mã đơn
- **Lô**: số lô
- **SL trả**: số lượng khách trả lại (đơn vị cơ bản); lớn hơn 0: đơn đó có trả hàng
"""


class JoinedReadmeTests(_Shop):
    def files(self, orders=None, lines=None, lots=None):
        return {
            "don.csv": [["Mã CT", "Ngày", "Xử lý", "Tiền hàng"],
                        *(orders or [["C1", "2026-01-05", "completed", "70000"],
                                     ["C2", "2026-01-05", "completed", "10000"],
                                     ["C3", "2026-01-06", "cancelled", "5000"]])],
            "chi_tiet.csv": [["Mã CT", "Dòng", "Mã SP", "SL", "Tiền", "SL lẻ"],
                             *(lines or [["C1", "1", "SP1", "2", "40000", "2"],
                                         ["C1", "2", "FEE", "1", "30000", "1"],
                                         ["C2", "1", "SP1", "1", "10000", "1"],
                                         ["C3", "1", "SP1", "1", "5000", "1"]])],
            "san_pham.csv": [["Mã SP", "Tên SP", "ĐV", "Nhóm"], ["SP1", "Cà phê", "ly", "Đồ uống"],
                             ["FEE", "Phí phục vụ", "lần", "Khác"]],
            "lo.csv": [["Mã CT", "Lô", "SL trả"], *(lots or [["C2", "L1", "1"]])],
        }

    def test_join_by_code_totals_service_fee_and_lot_returns(self):
        sales, evidence, eligible = load_readme_sales(self.shop(JOINED_README, self.files()),
                                                      RULEBOOK)
        self.assertEqual(evidence["product_join"], {"by_code": 4})
        self.assertEqual(evidence["amount_reconciliation"]["order_goods_vs_line_sum"],
                         {"compared": 3, "differs": 0, "unparsed": 0})
        self.assertEqual({s.order_id: s.status for s in sales},
                         {"C1": "valid", "C2": "returned", "C3": "cancelled"})
        self.assertEqual(sales[0].product_name, "Cà phê")
        self.assertEqual(eligible, frozenset({0, 2, 3}))  # the service fee line is not ranked

    def test_orphan_lines_raise(self):
        lines = [["C1", "1", "SP1", "2", "40000", "2"], ["C9", "1", "SP1", "1", "10000", "1"]]
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(JOINED_README, self.files(lines=lines)), RULEBOOK)
        self.assertEqual(caught.exception.code, "ORPHAN_FK")

    def test_duplicate_order_keys_raise(self):
        orders = [["C1", "2026-01-05", "completed", "70000"],
                  ["C1", "2026-01-06", "completed", "70000"]]
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(JOINED_README, self.files(orders=orders)), RULEBOOK)
        self.assertEqual(caught.exception.code, "DUPLICATE_PARENT_KEY")

    def test_exact_duplicate_parent_and_dated_summary_rows_are_ignored(self):
        orders = [["C1", "2026-01-05", "completed", "70000"],
                  ["C1", "2026-01-05", "completed", "70000"],
                  ["Tổng 05/01", "", "", "70000"]]
        lines = [["C1", "1", "SP1", "2", "70000", "2"],
                 ["Tổng 05/01", "", "", "", "70000", ""]]
        sales, evidence, _ = load_readme_sales(
            self.shop(JOINED_README, self.files(orders=orders, lines=lines, lots=[])), RULEBOOK)
        self.assertEqual([sale.order_id for sale in sales], ["C1"])
        self.assertEqual(evidence["counts"]["duplicate_rows_dropped"], 1)
        self.assertEqual(evidence["counts"]["summary_rows"], 1)
        self.assertEqual(evidence["counts"]["summary_rows_skipped"], 1)

    def test_header_total_mismatch_is_reported(self):
        orders = [["C1", "2026-01-05", "completed", "99000"],
                  ["C2", "2026-01-05", "completed", "10000"],
                  ["C3", "2026-01-06", "cancelled", "5000"]]
        _, evidence, _ = load_readme_sales(self.shop(JOINED_README, self.files(orders=orders)),
                                           RULEBOOK)
        self.assertEqual(evidence["amount_reconciliation"]["order_goods_vs_line_sum"]["differs"], 1)

    def test_service_fee_without_rule_blocks_rankings(self):
        rules = RULEBOOK.replace("Phí phục vụ không phải mặt hàng: không xếp hạng", "khác")
        _, _, eligible = load_readme_sales(self.shop(JOINED_README, self.files()), rules)
        self.assertEqual(eligible, frozenset())


INVOICE_README = """# Dữ liệu shop

## Cột trong `hoa_don.csv`
- **Mã bill**: mã đơn
- **Ngày GD**: ngày bán
- **Status**: trạng thái đơn
- **Tien hang**: tiền hàng của đơn: cộng thành tiền các mặt hàng, trước giảm giá của đơn, chưa gồm phí giao
"""


class InvoiceListTests(_Shop):
    def test_invoice_only_list(self):
        rows = [["Mã bill", "Ngày GD", "Status", "Tien hang"],
                ["B1", "01/01/2026", "Hoàn thành", "32,000"],
                ["", "", "", "32,000"],
                ["B2", "01/01/2026", "Hoàn trả", "10,000"]]
        sales, evidence, eligible = load_readme_sales(
            self.shop(INVOICE_README, {"hoa_don.csv": rows}), RULEBOOK)
        self.assertEqual([(s.order_id, s.status, s.amount, s.product_id) for s in sales],
                         [("B1", "valid", Decimal(32000), None),
                          ("B2", "returned", Decimal(10000), None)])
        self.assertEqual(evidence["summary_rows_skipped"], 1)
        self.assertEqual(eligible, frozenset())

    def test_exact_duplicate_invoice_is_counted_once_but_conflict_still_raises(self):
        duplicate = ["B1", "01/01/2026", "Hoàn thành", "32,000"]
        rows = [["Mã bill", "Ngày GD", "Status", "Tien hang"], duplicate, duplicate]
        sales, evidence, _ = load_readme_sales(
            self.shop(INVOICE_README, {"hoa_don.csv": rows}), RULEBOOK)
        self.assertEqual([sale.order_id for sale in sales], ["B1"])
        self.assertEqual(evidence["summary_rows_skipped"], 1)

        conflicting = rows + [["B1", "02/01/2026", "Hoàn thành", "32,000"]]
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(INVOICE_README, {"hoa_don.csv": conflicting},
                                        business="conflict"), RULEBOOK)
        self.assertEqual(caught.exception.code, "DUPLICATE_PARENT_KEY")


class HandwrittenLedgerTests(_Shop):
    def test_monthly_sheet_order_codes_remain_global_for_later_return_rows(self):
        readme = """# Sổ bán hàng
Sổ bán hàng ghi tay chuyển sang Excel, mỗi tháng một sheet.
## Cột trong `SoBanHang.xlsx`
- **Mã**: mã đơn
- **Ngày**: ngày bán
- **Trạng thái**: trạng thái đơn
- **Hàng**: tên hàng
- **SL**: số lượng
- **Tiền**: thành tiền sau giảm
## Quy ước
- **Trả hàng**: hàng trả ghi số lượng âm, cùng mã đơn với đơn đã bán: đơn có dòng số lượng âm là đơn có trả hàng.
"""
        header = ["Mã", "Ngày", "Trạng thái", "Hàng", "SL", "Tiền"]
        rows = {"T1-2026": [header, ["HD1", "01/01/2026", "Xong", "A", 1, 100]],
                "T2-2026": [header, ["HD1", "01/02/2026", "trả lại", "A", -1, -100]]}
        sales, evidence, _ = load_readme_sales(
            self.shop(readme, {"SoBanHang.xlsx": rows}), RULEBOOK)
        self.assertEqual({sale.order_id for sale in sales}, {"HD1"})
        self.assertEqual({sale.status for sale in sales}, {"returned"})
        self.assertEqual(evidence["counts"]["orders_in_lines"], 1)

    def test_combined_sales_software_export_is_recognised_by_documented_shape(self):
        readme = """# Dữ liệu
File Excel xuất từ phần mềm bán hàng; hóa đơn và chi tiết hóa đơn gộp chung sheet BanHang.
## File
- `BanHang_TongHop_2026.xlsx` (xlsx)
"""
        header = ["Đơn số", "Ngày HĐ", "Trạng thái giao", "Hàng hóa", "S.L", "ĐV",
                  "Giá 1 đv", "Thành tiền (đ)", "SL (ĐV cơ bản)", "Khách hàng (tên)",
                  "Mã khách", "Nguồn", "Mã NV bán", "Giờ tạo đơn", "Hình thức thanh toán"]
        rows = {"BanHang": [header,
                             ["HD1", "01/01/2026", "Khách trả", "Máy A", 1, "cái",
                              100, 100, 1, "An", "K1", "Quầy", "NV1", "09:00", "cash"]]}
        sales, evidence, _ = load_readme_sales(
            self.shop(readme, {"BanHang_TongHop_2026.xlsx": rows}), RULEBOOK)
        self.assertEqual([(sale.order_id, sale.status) for sale in sales],
                         [("HD1", "returned")])
        self.assertIn("sales_export_combined.v1", evidence["column_meanings"])

    def test_phone_monthly_ledger_uses_conventions_and_scopes_stt_by_sheet(self):
        readme = """# Sổ điện thoại
Sổ bán hàng, mỗi tháng một sheet.

## File
- `SoBanHang.xlsx` (xlsx)

## Quy ước
- Đơn: các dòng cùng **STT** là một đơn; ghi chú lặp ở mọi dòng.
- Tiền khách trả: **Khách trả** là số tiền khách trả cho cả đơn.
- Đơn khách trả lại: **Ghi chú** của đơn ghi khách trả lại.
"""
        header = ["Ngày", "STT", "Khách", "Hàng", "SL", "Giá", "Thành tiền", "Ghi chú"]
        sheets = {
            "T1-2026": [[], header,
                         ["01/01/2026", 1, "An", "Máy A", 1, 100, 100, ""],
                         ["01/01/2026", 1, "An", "Ốp", 1, 10, 10, ""],
                         ["Tổng cộng", None, None, None, None, None, 110, None]],
            "T2-2026": [[], header,
                         ["01/02/2026", 1, "Bình", "Máy B", 1, 200, 200,
                          "khách trả lại"]],
        }
        sales, evidence, _ = load_readme_sales(
            self.shop(readme, {"SoBanHang.xlsx": sheets}), RULEBOOK_WITH_NOTES)
        self.assertEqual({sale.order_id for sale in sales}, {"T1-2026::1", "T2-2026::1"})
        self.assertEqual(evidence["status_counts"], {"returned": 1, "valid": 1})
        self.assertEqual(evidence["counts"]["summary_rows_skipped"], 1)

    def test_phone_profile_rejects_an_incomplete_header(self):
        readme = """# Sổ điện thoại
## Quy ước
- Đơn: các dòng cùng **STT** là một đơn.
- Tiền khách trả: **Khách trả** là số tiền khách trả cho cả đơn.
- Đơn khách trả lại: **Ghi chú** của đơn ghi khách trả lại.
"""
        rows = {"T1-2026": [[], ["Ngày", "STT", "Khách", "Hàng", "SL", "Giá", "Ghi chú"]]}
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(self.shop(readme, {"SoBanHang.xlsx": rows}),
                              RULEBOOK_WITH_NOTES)
        self.assertEqual(caught.exception.code, "README_COLUMNS_MISSING")

    def test_repair_rows_are_revenue_records_but_not_rankable_products(self):
        readme = """# Sổ sửa xe
Sổ sửa xe, mỗi tháng một sheet; mỗi dòng một phiếu, không có mã phiếu.
## Cột trong `SoSuaXe.xlsx`
- **Ngày**: ngày sửa
- **Làm gì**: các việc đã làm cho xe
- **Thợ**: thợ làm phiếu
- **Tiền**: tổng tiền cả phiếu
- **Ghi chú**: ghi chú
"""
        rows = {"T7-2025": [[], ["Ngày", "Làm gì", "Thợ", "Tiền", "Ghi chú"],
                              ["1 thg 7 2025", "thay nhớt + bugi", "Đức", 150000,
                               "kiểm tra lại tuần sau"]]}
        sales, _, eligible = load_readme_sales(
            self.shop(readme, {"SoSuaXe.xlsx": rows}), RULEBOOK_WITH_NOTES)
        self.assertEqual([(sale.status, sale.amount, sale.staff_id) for sale in sales],
                         [("valid", Decimal(150000), "Đức")])
        self.assertEqual(eligible, frozenset())

    def test_merged_header_skips_documented_non_product_adjustments(self):
        readme = """# Sổ mẹ và bé
`BanHang_<năm>.xlsx`: mỗi tháng một sheet, header gộp 2 dòng. Dòng không phải hàng có Voucher, Dùng điểm và Phí ship.
## Cột trong `BanHang_2026.xlsx`
- **ĐG**: đơn giá
- **TT**: thành tiền của dòng
- **SL**: số lượng
- **ĐVT**: đơn vị tính
- **Tên hàng**: tên hàng
- **HĐ**: số hóa đơn
- **Ngày**: ngày bán
- **Khách**: tên khách
- **Kênh**: kênh bán
- **Ghi chú**: ghi chú của đơn
"""
        rows = {"T01-2026": [
            ["Tiền", None, "Hàng hoá", None, None, "Hoá đơn", None, None, None, None],
            ["ĐG", "TT", "SL", "ĐVT", "Tên hàng", "HĐ", "Ngày", "Khách", "Kênh", "Ghi chú"],
            [100000, 100000, 1, "cái", "Hàng A", "HD1", "01/01/2026", "An", "Tại tiệm", ""],
            [None, -10000, None, None, "Voucher", "HD1", None, None, None, None],
            [None, 20000, None, None, "Phí ship", "HD1", None, None, None, None],
        ]}
        sales, evidence, _ = load_readme_sales(
            self.shop(readme, {"BanHang_2026.xlsx": rows}), RULEBOOK_WITH_NOTES)
        self.assertEqual([sale.amount for sale in sales], [Decimal(100000)])
        self.assertEqual(evidence["counts"]["non_product_adjustments_skipped"], 2)


class NoteStatusTests(unittest.TestCase):
    """Rulebook "Sổ không có cột trạng thái": the order state is written in the note."""

    def test_note_states(self):
        for note, state in (
            ("", "hoan thanh"), (None, "hoan thanh"), ("khách quen", "hoan thanh"),
            ("cancel", "huy"), ("Huỷ", "huy"), ("huy", "huy"), ("khách hủy đơn", "huy"),
            ("đang giao", "dang giao"), ("chưa giao", "dang giao"),
            ("khách trả lại 1 cái", "tra hang"),
            # The given name Huy is not "huỷ"; delivering to someone is not "đang giao".
            ("giao cho anh Huy", "hoan thanh"), ("Huy lấy giùm", "hoan thanh"),
        ):
            with self.subTest(note=note):
                self.assertEqual(_note_state(note), state)

    def test_note_naming_two_states_is_not_guessed(self):
        with self.assertRaises(StatusError):
            _note_state("huỷ, khách trả lại hàng")

    def test_notes_need_the_rulebook_row(self):
        self.assertEqual(_resolve_status("chưa giao", _rules(RULEBOOK_WITH_NOTES), from_note=True),
                         _resolve_status("đang giao", _rules(RULEBOOK), from_note=False))
        with self.assertRaises(StatusError):
            _resolve_status("chưa giao", _rules(RULEBOOK), from_note=True)


class AdapterSelectionTests(_Shop):
    def test_readme_adapter_is_used_when_flat_headers_are_unknown(self):
        catalog = self.shop(FLAT_README, {"ban_hang.csv": FlatReadmeTests.rows(None)})
        sales, evidence, eligible = sales_source.load_business_sales(catalog, RULEBOOK)
        self.assertEqual(evidence["adapter"], "readme_sales")
        self.assertIn("flat_adapter_error", evidence)
        self.assertTrue(sales and eligible)

    def test_failure_of_every_adapter_raises(self):
        catalog = self.shop("# Dữ liệu\n", {"x.csv": [["a", "b"], ["1", "2"]]})
        with self.assertRaises(ValueError) as caught:
            sales_source.load_business_sales(catalog, RULEBOOK)
        self.assertIn("README adapter", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
