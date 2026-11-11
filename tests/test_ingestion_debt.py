"""D01: README-backed debt ledger, source checks and reconciliation."""

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.debt import DebtSourceError, load_debt_ledger

RULEBOOK = """# Synthetic split rules
| Luật | Mặc định |
|---|---|
| Đơn được tính vào nợ | ngày đặt không sau mốc, không huỷ, không COD hoặc sàn |
| Nợ một đơn tại ngày X | tổng phải thu trừ các khoản trả có ngày không sau X |
| Đơn COD, đơn bán qua sàn TMĐT | không phải khách nợ |
"""

README = """# Dữ liệu shop

## File
- `orders.csv`
- `payments.csv`
- `customers.csv`

## Cột trong `orders.csv`
- **Mã**: mã đơn
- **Ngày**: ngày đặt
- **Trạng thái**: trạng thái đơn
- **Tổng thu**: số khách phải trả cho cả đơn
- **Thanh toán**: hình thức thanh toán
- **Kênh**: kênh bán
- **Mã khách**: mã khách
- **Tên khách**: tên khách
- **Còn nợ**: số tiền của đơn chưa thu, tính đến hết ngày cuối dữ liệu

## Cột trong `payments.csv`
- **Mã đơn**: mã đơn
- **Ngày thu**: thời điểm thu
- **Tiền thu**: số tiền trả lần đó

## Cột trong `customers.csv`
- **Mã KH**: mã khách
- **Khách hàng**: tên khách

## Quy ước
- **Công nợ**: mỗi lần khách trả tiền cho một đơn là một dòng, **Tiền thu** là số tiền trả lần đó; **Tổng thu** là số khách phải trả cho cả đơn; **Còn nợ** là số tiền của đơn chưa thu, tính đến hết ngày cuối dữ liệu.
- Dữ liệu có đến hết 31/01/2026.
"""


class _Shop(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"

    def catalog(self, *, readme=README, orders=None, payments=None, customers=None,
                extra=None):
        root = self.data / "shop"
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        files = {
            "orders.csv": orders or [
                ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
                 "Mã khách", "Tên khách", "Còn nợ"],
                ["D1", "01/01/2026", "Xong", "100000.5", "Ghi nợ", "Store",
                 "K1", "Tên trên đơn", "25000.5"],
                ["D2", "02/01/2026", "Trả hàng", "50000", "chuyển khoản", "phone",
                 "K2", "Bình", "50000"],
                ["D3", "03/01/2026", "Hủy", "90000", "cash", "zalo_oa",
                 "K3", "Cường", ""],
                ["D4", "04/01/2026", "Đang giao", "70000", "COD", "Shopee",
                 "", "Khách lẻ", ""],
            ],
            "payments.csv": payments or [
                ["Mã đơn", "Ngày thu", "Tiền thu"],
                ["D1", "01/01/2026", "25000"],
                ["D1", "15/01/2026", "50000"],
            ],
        }
        if customers is not False:
            files["customers.csv"] = customers if customers is not None else [
                ["Mã KH", "Khách hàng"], ["K1", "An"], ["K2", "Bình"],
            ]
        files.update(extra or {})
        for name, rows in files.items():
            with (root / name).open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)
        return build_catalog(self.data, "shop")


class DebtLedgerTests(_Shop):
    def test_error_code_api_matches_the_shared_contract(self):
        error = DebtSourceError("absent")
        self.assertEqual((error.code, str(error)), ("absent", "absent"))

    def test_valid_ledger_preserves_events_labels_lineage_and_reconciliation(self):
        ledger = load_debt_ledger(self.catalog(), RULEBOOK)
        self.assertEqual((ledger.first_day, ledger.last_day),
                         (date(2026, 1, 1), date(2026, 1, 31)))
        self.assertTrue(ledger.uses_customer_codes)
        self.assertEqual((len(ledger.orders), len(ledger.payments)), (4, 2))
        first = ledger.orders[0]
        self.assertEqual((first.total, first.customer_name, first.on_credit, first.excluded),
                         (Decimal("100000.5"), "An", True, ""))
        self.assertEqual(first.source_customer_name, "Tên trên đơn")
        self.assertEqual(ledger.orders[1].state, "tra hang")
        self.assertEqual(ledger.orders[2].state, "huy")
        self.assertEqual(ledger.orders[3].excluded, "cod")
        self.assertIsNone(ledger.orders[3].customer_name)  # explicit walk-in label
        self.assertEqual(sum(p.amount for p in ledger.payments), Decimal("75000"))
        checks = ledger.lineage["checks"]
        self.assertEqual(checks["closing_debt_checked"], 2)
        self.assertEqual(checks["orphan_payments"], 0)
        self.assertIn("orders.csv", ledger.lineage["sources"]["orders"])
        self.assertEqual(ledger.lineage["customer_identity"], {
            "uses_customer_codes": True,
            "basis": [{"source": "orders.csv", "column": "Mã khách"}],
        })
        self.assertTrue(first.source.endswith("#1"))

    def test_latest_order_or_payment_is_fallback_coverage(self):
        readme = README.replace("- Dữ liệu có đến hết 31/01/2026.\n", "")
        ledger = load_debt_ledger(self.catalog(readme=readme), RULEBOOK)
        self.assertEqual(ledger.last_day, date(2026, 1, 15))
        self.assertEqual(ledger.lineage["coverage"]["source"], "observed")

    def test_summaries_are_skipped_but_incomplete_orders_are_rejected(self):
        header = ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
                  "Mã khách", "Tên khách", "Còn nợ"]
        order = ["D1", "01/01/2026", "Xong", "100000", "Ghi nợ", "Store", "K1", "An", "25000"]
        daily_total = ["Tổng 01/01", "", "", "100000", "", "", "", "", "25000"]
        monthly_total = ["Cộng tháng 01/2026", *daily_total[1:]]
        ledger = load_debt_ledger(
            self.catalog(orders=[header, order, daily_total, monthly_total]), RULEBOOK)
        self.assertEqual([order.order_id for order in ledger.orders], ["D1"])
        self.assertEqual(ledger.lineage["checks"]["summary_rows_skipped"], 2)
        self.assertEqual(ledger.lineage["checks"]["closing_debt_checked"], 1)

        for invalid in (
            ["D2", *daily_total[1:]],
            [*daily_total[:6], "K2", *daily_total[7:]],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DebtSourceError) as caught:
                    load_debt_ledger(self.catalog(orders=[header, order, invalid]), RULEBOOK)
                self.assertEqual(caught.exception.code, "column_missing")
                self.assertIn("order_day", caught.exception.diagnostics["fields"])

    def test_duplicate_order_and_orphan_payment_are_rejected(self):
        duplicate = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "10", "cash", "store", "", "", "10"],
            ["D1", "02/01/2026", "Xong", "10", "cash", "store", "", "", "10"],
        ]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(orders=duplicate), RULEBOOK)
        self.assertEqual(caught.exception.code, "key")

        orphan = [["Mã đơn", "Ngày thu", "Tiền thu"], ["missing", "01/01/2026", "1"]]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(payments=orphan), RULEBOOK)
        self.assertEqual(caught.exception.code, "orphan")

        missing_key = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["", "01/01/2026", "Xong", "10", "cash", "store", "", "", "10"],
        ]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(orders=missing_key), RULEBOOK)
        self.assertEqual(caught.exception.code, "key")

    def test_unknown_labels_bad_amount_and_bad_coverage_are_rejected(self):
        orders = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "10", "crypto", "store", "", "", "10"],
        ]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(orders=orders), RULEBOOK)
        self.assertEqual(caught.exception.code, "label")

        negative = [["Mã đơn", "Ngày thu", "Tiền thu"], ["D1", "01/01/2026", "-1"]]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(payments=negative), RULEBOOK)
        self.assertEqual(caught.exception.code, "amount")

        early = README.replace("31/01/2026", "05/01/2026")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=early), RULEBOOK)
        self.assertEqual(caught.exception.code, "reconcile")

    def test_marketplace_prefix_is_a_reviewed_closed_label(self):
        orders = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "10", "cash", "Sàn Shopee", "K1", "An", ""],
        ]
        ledger = load_debt_ledger(self.catalog(orders=orders, payments=[
            ["Mã đơn", "Ngày thu", "Tiền thu"],
        ]), RULEBOOK)
        self.assertEqual(ledger.orders[0].excluded, "marketplace")

    def test_customer_mode_comes_from_schema_not_observed_codes(self):
        all_walk_in = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "10", "cash", "store", "", "Tên có sẵn", "10"],
        ]
        no_payments = [["Mã đơn", "Ngày thu", "Tiền thu"]]
        coded_shop = load_debt_ledger(
            self.catalog(orders=all_walk_in, payments=no_payments), RULEBOOK)
        self.assertTrue(coded_shop.uses_customer_codes)
        self.assertIsNone(coded_shop.orders[0].customer_id)
        self.assertEqual(coded_shop.orders[0].customer_name, "Tên có sẵn")

        no_code_readme = README.replace("- `customers.csv`\n", "").replace(
            "- **Mã khách**: mã khách\n", "").replace(
            "\n## Cột trong `customers.csv`\n- **Mã KH**: mã khách\n"
            "- **Khách hàng**: tên khách\n", "")
        no_code_orders = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "10", "cash", "store", "Chị Lan", "10"],
        ]
        named_shop = load_debt_ledger(self.catalog(
            readme=no_code_readme, orders=no_code_orders, payments=no_payments,
            customers=False), RULEBOOK)
        self.assertFalse(named_shop.uses_customer_codes)
        self.assertIsNone(named_shop.orders[0].customer_id)
        self.assertEqual(named_shop.orders[0].customer_name, "Chị Lan")

    def test_ambiguous_customer_mode_is_a_typed_source_error(self):
        readme = README.replace("- `customers.csv`\n", "").replace(
            "- **Mã khách**: mã khách\n", "").replace(
            "- **Tên khách**: tên khách\n", "").replace(
            "\n## Cột trong `customers.csv`\n- **Mã KH**: mã khách\n"
            "- **Khách hàng**: tên khách\n", "")
        orders = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "10", "cash", "store", "10"],
        ]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=readme, orders=orders, customers=False),
                             RULEBOOK)
        self.assertEqual(caught.exception.code, "customer_mode")

    def test_reconciliation_mismatch_and_missing_value_are_rejected(self):
        orders = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "100", "Ghi nợ", "Store", "K1", "An", "99"],
        ]
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(orders=orders, payments=[
                ["Mã đơn", "Ngày thu", "Tiền thu"], ["D1", "01/01/2026", "50"],
            ]), RULEBOOK)
        self.assertEqual(caught.exception.code, "reconcile")

        orders[1][-1] = ""
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(orders=orders), RULEBOOK)
        self.assertEqual(caught.exception.code, "reconcile")

    def test_missing_rules_or_partial_source_abstains_not_absent(self):
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(), "")
        self.assertEqual(caught.exception.code, "rules_missing")

        partial = README.replace("; **Tổng thu** là số khách phải trả cho cả đơn", "")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=partial), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")

    def test_qualified_table_column_references_are_supported(self):
        readme = README.replace(
            "**Tiền thu** là số tiền trả lần đó; **Tổng thu** là số khách phải trả cho cả đơn",
            "`payments.Tiền thu` là số tiền trả lần đó; `orders.Tổng thu` là số khách "
            "phải trả cho cả đơn",
        )
        ledger = load_debt_ledger(self.catalog(readme=readme), RULEBOOK)
        self.assertEqual((len(ledger.orders), len(ledger.payments)), (4, 2))

        bad = readme.replace("orders.Tổng thu", "orders.Không tồn tại")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=bad), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")

    def test_absent_requires_a_complete_readme_without_debt_hints(self):
        readme = """# Shop
## File
- `orders.csv`
## Cột trong `orders.csv`
- **Mã**: mã đơn
- **Ngày**: ngày bán
"""
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        with (root / "orders.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([["Mã", "Ngày"], ["D1", "01/01/2026"]])
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "absent")

        (root / "undocumented.csv").write_text("x\n1\n", encoding="utf-8")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")

    def test_absent_requires_every_file_and_sheet_to_have_a_reviewed_mapping(self):
        root = self.data / "shop"
        root.mkdir(parents=True)
        listed_only = """# Shop
## File
- `orders.csv`
- `payments.csv`
- `customers.csv`
"""
        (root / "README.md").write_text(listed_only, encoding="utf-8")
        for name, rows in {
            "orders.csv": [["Mã", "Ngày"], ["D1", "01/01/2026"]],
            "payments.csv": [["Mã đơn", "Ngày thu", "Tiền thu"],
                             ["D1", "02/01/2026", "50"]],
            "customers.csv": [["Mã KH", "Tên"], ["K1", "An"]],
        }.items():
            with (root / name).open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")
        self.assertIn("payments.csv::payments", caught.exception.diagnostics["sources"])

        for name in ("orders.csv", "payments.csv", "customers.csv"):
            (root / name).unlink()
        from openpyxl import Workbook

        book = Workbook()
        orders = book.active
        orders.title = "DonHang"
        orders.append(["Mã", "Ngày"])
        orders.append(["D1", "01/01/2026"])
        payments = book.create_sheet("ThuTien")
        payments.append(["Mã", "Ngày", "Tiền thu"])
        payments.append(["D1", "02/01/2026", "50"])
        book.save(root / "BaoCao.xlsx")
        book.close()
        partial_sheet = """# Shop
## File
- `BaoCao.xlsx`
## Cột trong `BaoCao.xlsx`, sheet `DonHang`
- **Mã**: mã đơn
- **Ngày**: ngày bán
"""
        (root / "README.md").write_text(partial_sheet, encoding="utf-8")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")
        self.assertIn("BaoCao.xlsx::ThuTien", caught.exception.diagnostics["sources"])

        # A workbook-wide mapping matches both sheets' common columns but misses
        # the amount on ThuTien.
        (root / "README.md").write_text(
            partial_sheet.replace(', sheet `DonHang`', ''), encoding="utf-8")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")
        self.assertEqual(caught.exception.diagnostics["sources"], ["BaoCao.xlsx::ThuTien"])

    def test_absence_allows_reviewed_product_metadata_but_not_unknown_columns(self):
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text("""# Shop
## File
- `products.csv`
## Cột trong `products.csv`
- **Mã**: mã hàng
- **Tên**: tên
- **Nhóm**: nhóm hàng
""", encoding="utf-8")
        path = root / "products.csv"
        path.write_text("Mã,Tên,Nhóm,Bảo hành\nP1,Trà,Đồ uống,12\n", encoding="utf-8")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "absent")

        path.write_text("Mã,Tên,Nhóm,Tiền thu\nP1,Trà,Đồ uống,12\n", encoding="utf-8")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "column_missing")
        self.assertEqual(caught.exception.diagnostics["sources"], ["products.csv::products"])

    def test_incomplete_second_payment_file_is_not_silently_skipped(self):
        readme = README.replace("- `customers.csv`\n", "- `customers.csv`\n- `payments_2.csv`\n")
        readme += """
## Cột trong `payments_2.csv`
- **Mã đơn**: mã đơn
- **Tiền thu**: số tiền trả lần đó
"""
        extra = {"payments_2.csv": [
            ["Mã đơn", "Tiền thu"], ["D1", "10000"],
        ]}
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=readme, extra=extra), RULEBOOK)
        self.assertEqual(caught.exception.code, "partial")

    def test_payment_role_is_retained_from_inventory_or_mapped_file_family(self):
        for filename, description in (
            ("ThuBoSung.csv", " (bảng khoản trả tiếp theo)"),
            ("payments_2.csv", ""),
        ):
            with self.subTest(filename=filename):
                readme = README.replace(
                    "- `payments.csv`\n",
                    f"- `payments.csv`\n- `{filename}`{description}\n",
                )
                catalog = self.catalog(
                    readme=readme, extra={filename: [["Mã đơn"], ["D1"]]})
                try:
                    with self.assertRaises(DebtSourceError) as caught:
                        load_debt_ledger(catalog, RULEBOOK)
                finally:
                    (self.data / "shop" / filename).unlink()
                self.assertEqual(caught.exception.code, "partial")
                source, = caught.exception.diagnostics["sources"]
                self.assertEqual(source["source"], f"{filename}::{Path(filename).stem}")
                self.assertEqual(source["role"], "payments")
                self.assertEqual(source["missing_fields"], ["payment_amount", "payment_day"])

    def test_a_status_column_named_xu_ly_is_not_a_currency(self):
        readme = README.replace("**Trạng thái**", "**Xử lý**")
        self.catalog(readme=readme)
        with (self.data / "shop" / "orders.csv").open(encoding="utf-8-sig", newline="") as stream:
            orders = list(csv.reader(stream))
        orders[0][2] = "Xử lý"
        ledger = load_debt_ledger(self.catalog(readme=readme, orders=orders), RULEBOOK)
        self.assertEqual(ledger.orders[0].total, Decimal("100000.5"))

    def test_sqlite_profile_uses_qualified_readme_amount_columns(self):
        shop = self.data / "shop"
        shop.mkdir(parents=True)
        readme = ("File máy POS xuất ra.\n\n## File\n- `pos.sqlite`\n\n## Quy ước\n"
                  "- **Công nợ**: mỗi lần khách trả tiền cho một đơn là một dòng, "
                  "`thanh_toan.SoTienThu` là số tiền trả lần đó; "
                  "`don_hang.TongThanhToan` là số khách phải trả cho cả đơn.\n"
                  "- Dữ liệu có đến hết 31/01/2026.\n")
        (shop / "README.md").write_text(readme, encoding="utf-8")
        with closing(sqlite3.connect(shop / "pos.sqlite")) as database:
            database.executescript("""
                CREATE TABLE don_hang (MaHoaDon, NgayLap, TinhTrang, TongThanhToan,
                                       PhuongThucTT, KenhBanHang, MaKhachHang);
                INSERT INTO don_hang VALUES ('D1', '2026-01-01', 'completed', 100,
                                             'debt', 'store', 'K1');
                CREATE TABLE thanh_toan (MaHoaDon, NgayGioThu, SoTienThu);
                INSERT INTO thanh_toan VALUES ('D1', '2026-01-03', 25);
                CREATE TABLE khach_hang (MaKhachHang, TenGoi, LoaiKhachHang);
                INSERT INTO khach_hang VALUES ('K1', 'Khách A', 'thân thiết');
            """)
        ledger = load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual((ledger.orders[0].customer_name, ledger.orders[0].total,
                          ledger.payments[0].amount), ("Khách A", Decimal(100), Decimal(25)))
        (shop / "README.md").write_text(readme.replace("thanh_toan.SoTienThu", "table.SoTienThu"),
                                       encoding="utf-8")
        with self.assertRaises(DebtSourceError):
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)

    def test_money_units_are_converted_or_rejected_explicitly(self):
        thousand = README.replace(
            "- Dữ liệu có đến hết 31/01/2026.",
            "- Dữ liệu có đến hết 31/01/2026.\n"
            "- Tất cả các cột tiền có đơn vị nghìn đồng.",
        )
        ledger = load_debt_ledger(self.catalog(readme=thousand), RULEBOOK)
        self.assertEqual(ledger.orders[0].total, Decimal("100000500"))
        self.assertEqual(ledger.lineage["money"]["multiplier_to_vnd"], "1000")

        unsupported = thousand.replace("nghìn đồng", "triệu đồng")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=unsupported), RULEBOOK)
        self.assertEqual(caught.exception.code, "amount")

        foreign = README.replace(
            "- Dữ liệu có đến hết 31/01/2026.",
            "- Dữ liệu có đến hết 31/01/2026.\n- Đơn vị tiền: EUR.",
        )
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=foreign), RULEBOOK)
        self.assertEqual(caught.exception.code, "amount")

        short_unit = README + "\n- **Đơn vị**: USD\n"
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=short_unit), RULEBOOK)
        self.assertEqual(caught.exception.code, "amount")

        for declaration in ("VND", "cái"):
            with self.subTest(declaration=declaration):
                plain = README + f"\n- **Đơn vị**: {declaration}\n"
                ledger = load_debt_ledger(self.catalog(readme=plain), RULEBOOK)
                self.assertEqual(ledger.lineage["money"]["unit"], "vnd")

        mixed = README.replace(
            "- Dữ liệu có đến hết 31/01/2026.",
            "- Dữ liệu có đến hết 31/01/2026.\n"
            "- `orders.csv` dùng nghìn đồng; `payments.csv` dùng VND.",
        )
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=mixed), RULEBOOK)
        self.assertEqual(caught.exception.code, "amount")

        thousand_vnd = thousand.replace("nghìn đồng", "nghìn VND")
        ledger = load_debt_ledger(self.catalog(readme=thousand_vnd), RULEBOOK)
        self.assertEqual(ledger.orders[0].total, Decimal("100000500"))

        ordinary_words = README.replace(
            "- Dữ liệu có đến hết 31/01/2026.",
            "- Dữ liệu có đến hết 31/01/2026.\n"
            "- **Xử lý**: trạng thái đơn.\n"
            "- **Dòng hàng**: thành tiền theo số lượng đã đổi ra đơn vị cơ bản.",
        )
        ledger = load_debt_ledger(self.catalog(readme=ordinary_words), RULEBOOK)
        self.assertEqual(ledger.lineage["money"]["unit"], "vnd")

        unknown = README.replace(
            "- Dữ liệu có đến hết 31/01/2026.",
            "- Dữ liệu có đến hết 31/01/2026.\n"
            "- Tất cả các cột tiền có đơn vị JPY.",
        )
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(self.catalog(readme=unknown), RULEBOOK)
        self.assertEqual(caught.exception.code, "amount")

        unsupported_scales = (
            "Đơn vị tiền: 1.000 VND.",
            "Đơn vị tiền: 1000 đồng.",
            "Đơn vị tiền: x1000 VND.",
            "Đơn vị tiền: 1,000 VND.",
            "Đơn vị tiền: 2.000 VND.",
            "Đơn vị tiền: x2000 VND.",
            "Đơn vị tiền: VND hoặc SGD.",
            "Đơn vị tiền: VND/kg.",
            "Các cột tiền tính bằng nghìn.",
            "Số tiền ghi theo ngàn (k).",
            "Đơn vị tiền của cột Tổng thu: nghìn VND.",
        )
        for declaration in unsupported_scales:
            with self.subTest(declaration=declaration):
                readme = README.replace(
                    "- Dữ liệu có đến hết 31/01/2026.",
                    f"- Dữ liệu có đến hết 31/01/2026.\n- {declaration}",
                )
                with self.assertRaises(DebtSourceError) as caught:
                    load_debt_ledger(self.catalog(readme=readme), RULEBOOK)
                self.assertEqual(caught.exception.code, "amount")

        for expression, multiplier in (
            ("VND", "1"), ("đồng", "1"), ("nghìn VND", "1000"), ("ngàn VND", "1000"),
            ("nghìn đồng", "1000"), ("ngàn đồng", "1000"),
        ):
            with self.subTest(expression=expression):
                declaration = f"- Đơn vị tiền: **{expression}**."
                ledger = load_debt_ledger(
                    self.catalog(readme=f"{README}\n{declaration}\n"), RULEBOOK)
                self.assertEqual(ledger.lineage["money"]["multiplier_to_vnd"], multiplier)
                self.assertEqual(ledger.lineage["money"]["evidence"], [declaration])

    def test_coded_customer_keeps_real_code_even_when_display_name_is_walk_in(self):
        customers = [["Mã KH", "Khách hàng"], ["K1", "Khách lẻ"], ["K2", "Bình"]]
        ledger = load_debt_ledger(self.catalog(customers=customers), RULEBOOK)
        self.assertEqual((ledger.orders[0].customer_id, ledger.orders[0].customer_name),
                         ("K1", "Khách lẻ"))

    def test_zero_payment_is_kept_and_overpayment_reconciles_to_zero_debt(self):
        orders = [
            ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
             "Mã khách", "Tên khách", "Còn nợ"],
            ["D1", "01/01/2026", "Xong", "100", "Ghi nợ", "Store", "K1", "An", "0"],
        ]
        payments = [
            ["Mã đơn", "Ngày thu", "Tiền thu"],
            ["D1", "01/01/2026", "0"],
            ["D1", "02/01/2026", "120"],
        ]
        ledger = load_debt_ledger(self.catalog(orders=orders, payments=payments), RULEBOOK)
        self.assertEqual([p.amount for p in ledger.payments], [Decimal(0), Decimal(120)])
        self.assertEqual(ledger.lineage["checks"]["zero_payments"], 1)

    def test_deadline_only_source_is_absent_but_paid_amount_hint_is_partial(self):
        deadline = """# Shop
## File
- `orders.csv`
## Cột trong `orders.csv`
- **Mã**: mã đơn
- **Hạn nợ**: hạn thanh toán
"""
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text(deadline, encoding="utf-8")
        with (root / "orders.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([["Mã", "Hạn nợ"], ["D1", "30"]])
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "absent")

        partial = deadline.replace("hạn thanh toán", "số tiền khách đã trả cho đơn")
        (root / "README.md").write_text(partial, encoding="utf-8")
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "partial")

    def test_reviewed_pos_profile_resolves_orders_payments_and_customers(self):
        readme = """# Shop
File máy POS xuất ra.
## File
- `orders.csv`
- `SoQuy_1.csv`
- `customers.csv`
## Quy ước
- **Công nợ**: mỗi lần khách trả tiền cho một đơn là một dòng, **Giá trị** là số tiền trả lần đó; **Tổng sau giảm giá** là số khách phải trả cho cả đơn.
- Dữ liệu có đến hết 31/01/2026.
"""
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        files = {
            "orders.csv": [
                ["Mã hóa đơn", "Thời gian", "Trạng thái", "Tổng sau giảm giá",
                 "Phương thức thanh toán", "Kênh bán", "Mã KH", "Khách hàng",
                 "Khách đã trả"],
                ["D1", "01/01/2026", "Xong", "100", "Ghi nợ", "Store", "K1",
                 "Tên trên đơn", "50"],
            ],
            "SoQuy_1.csv": [
                ["Mã phiếu", "Thời gian", "Mã hóa đơn", "Phương thức thanh toán",
                 "Giá trị"],
                ["P1", "02/01/2026", "D1", "Tiền mặt", "50"],
            ],
            "customers.csv": [
                ["Mã khách hàng", "Tên khách hàng"], ["K1", "An"],
            ],
        }
        for name, rows in files.items():
            with (root / name).open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)
        ledger = load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual((len(ledger.orders), len(ledger.payments)), (1, 1))
        self.assertEqual(ledger.orders[0].customer_name, "An")
        self.assertEqual(ledger.lineage["columns"]["orders"][0]["origin"],
                         "debt_pos_kiotviet.v1")

        listed = readme.replace("- `customers.csv`", "- `customers.csv`\n- `SoQuy_2.csv`")
        (root / "README.md").write_text(listed, encoding="utf-8")
        incomplete_sources = (
            (["Mã hóa đơn", "Giá trị"], ["D1", "20"]),
            (["Mã hóa đơn", "Thời gian"], ["D1", "03/01/2026"]),
            (["Thời gian", "Giá trị"], ["03/01/2026", "20"]),
            (["Mã phiếu", "Mã hóa đơn"], ["P2", "D1"]),
            (["Mã hóa đơn"], ["D1"]),
            (["Giá trị"], ["20"]),
        )
        for header, row in incomplete_sources:
            with self.subTest(missing=set(("Mã hóa đơn", "Thời gian", "Giá trị"))
                              - set(header)):
                with (root / "SoQuy_2.csv").open(
                        "w", encoding="utf-8-sig", newline="") as stream:
                    csv.writer(stream).writerows([header, row])
                with self.assertRaises(DebtSourceError) as caught:
                    load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
                self.assertEqual(caught.exception.code, "partial")

        files["orders.csv"][1][-1] = "70"
        with (root / "orders.csv").open(
                "w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(files["orders.csv"])
        with (root / "SoQuy_2.csv").open(
                "w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([["Mã hóa đơn", "Thời gian", "Giá trị"]])
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "reconcile")

        with (root / "SoQuy_2.csv").open(
                "w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([
                ["Mã hóa đơn", "Thời gian", "Giá trị"],
                ["D1", "03/01/2026", "20"],
            ])
        complete = load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual((len(complete.orders), len(complete.payments)), (1, 2))
        self.assertEqual(sum(payment.amount for payment in complete.payments), Decimal(70))
        self.assertEqual(complete.lineage["checks"]["paid_to_date_checked"], 1)

    def test_reviewed_sqlite_pos_profile_uses_qualified_debt_references(self):
        readme = """# Shop
File máy POS xuất ra.
## File
- `pos.sqlite` (sqlite)
## Quy ước
- Dữ liệu có đến hết 31/01/2026.
- **Công nợ**: mỗi lần khách trả tiền cho một đơn là một dòng, `thanh_toan.SoTienThu` là số tiền trả lần đó; `don_hang.TongThanhToan` là số khách phải trả cho cả đơn.
"""
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        database = root / "pos.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "CREATE TABLE don_hang (MaHoaDon TEXT, NgayLap TEXT, TinhTrang TEXT, "
                "TongThanhToan REAL, PhuongThucTT TEXT, KenhBanHang TEXT, "
                "MaKhachHang TEXT, KhachDaTra REAL, ConPhaiThu REAL)"
            )
            connection.execute(
                "INSERT INTO don_hang VALUES "
                "('D1', '01/01/2026', 'Xong', 100, 'debt', 'store', 'K1', 50, 50)"
            )
            connection.execute(
                "CREATE TABLE thanh_toan "
                "(MaHoaDon TEXT, NgayGioThu TEXT, SoTienThu REAL)"
            )
            connection.execute(
                "INSERT INTO thanh_toan VALUES ('D1', '02/01/2026', 50)"
            )
            connection.execute(
                "CREATE TABLE khach_hang (MaKhachHang TEXT, TenGoi TEXT)"
            )
            connection.execute("INSERT INTO khach_hang VALUES ('K1', 'An')")
            connection.commit()

        ledger = load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual((len(ledger.orders), len(ledger.payments)), (1, 1))
        self.assertEqual(ledger.orders[0].customer_name, "An")
        self.assertEqual(ledger.lineage["columns"]["orders"][0]["origin"],
                         "debt_pos_sqlite.v1")

        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DROP TABLE thanh_toan")
            connection.execute(
                "CREATE TABLE thanh_toan (MaHoaDon TEXT, SoTienThu REAL)"
            )
            connection.commit()
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "partial")

    def test_pos_payment_family_is_grouped_by_filename_across_workbooks(self):
        from openpyxl import Workbook

        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text("""# Shop
File máy POS xuất ra.
## File
- `orders.csv`
- `SoQuy_1.xlsx`
- `SoQuy_2.xlsx`
## Quy ước
- **Công nợ**: **Giá trị** là số tiền trả lần đó; **Tổng sau giảm giá** là số khách phải trả cho cả đơn.
""", encoding="utf-8")
        (root / "orders.csv").write_text(
            "Mã hóa đơn,Thời gian,Trạng thái,Tổng sau giảm giá,Phương thức thanh toán,"
            "Kênh bán,Mã KH\nD1,01/01/2026,Xong,100,Ghi nợ,Store,K1\n",
            encoding="utf-8",
        )
        header = ["Mã hóa đơn", "Thời gian", "Giá trị"]

        def write_receipts(name, sheet_name, rows):
            book = Workbook()
            sheet = book.active
            sheet.title = sheet_name
            for row in rows:
                sheet.append(row)
            book.save(root / name)
            book.close()

        write_receipts("SoQuy_1.xlsx", "PhieuThu", [header, ["D1", "02/01/2026", 50]])
        write_receipts("SoQuy_2.xlsx", "TiepTheo", [[header[0]], ["D1"]])
        with self.assertRaises(DebtSourceError) as caught:
            load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(caught.exception.code, "partial")
        source, = caught.exception.diagnostics["sources"]
        self.assertEqual(source["source"], "SoQuy_2.xlsx::TiepTheo")
        self.assertEqual(source["missing_fields"], ["payment_amount", "payment_day"])

        write_receipts("SoQuy_2.xlsx", "TiepTheo", [header, ["D1", "03/01/2026", 20]])
        ledger = load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
        self.assertEqual(len(ledger.orders), 1)
        self.assertEqual([payment.amount for payment in ledger.payments],
                         [Decimal(50), Decimal(20)])

    def combined_catalog(self, *, conflicting=False, missing_partition=False,
                         documented_grain=True):
        root = self.data / "shop"
        root.mkdir(parents=True, exist_ok=True)
        readme = """# Shop
File xuất từ phần mềm bán hàng.
Mỗi dòng là một dòng hàng, thông tin hóa đơn lặp lại trên các dòng cùng đơn.
## File
- `Export_2025.xlsx`
- `Export_2026.xlsx`
- `Receipts.xlsx`
## Quy ước
- **Công nợ**: **Tiền thu** là số tiền trả lần đó; **Tổng thanh toán** là số khách phải trả cho cả đơn.
- Dữ liệu có đến hết 31/01/2026.
"""
        if not documented_grain:
            readme = readme.replace("thông tin hóa đơn lặp lại trên các dòng cùng đơn.", "")
        (root / "README.md").write_text(readme, encoding="utf-8")
        order_header = ["Đơn số", "Ngày HĐ", "Trạng thái giao", "Tổng thanh toán", "Nguồn",
                        "Hình thức thanh toán", "Mã khách", "Khách hàng (tên)", "Công nợ",
                        "Da tra", "Dòng", "Thành tiền (đ)"]
        first = ["D1", "31/12/2025", "Xong", 150, "store", "Ghi nợ", "K1", "An", 50, 100]
        second_line = first.copy()
        if conflicting:
            second_line[3] = 175
        second_order = ["D2", "02/01/2026", "Khách trả", 90, "phone", "Ghi nợ",
                        "K2", "Bình", 90, 0]
        files = [
            ("Export_2025.xlsx", "Orders",
             [order_header, [*first, 1, 20], [*second_line, 2, 100]]),
            ("Receipts.xlsx", "Payments", [
                ["Ma don", "Ngày TT", "Tiền thu", "Mã thanh toán"],
                ["D1", "31/12/2025", 50, "P1"],
                ["D1", "10/01/2026", 25, "P2"],
                ["D1", "10/01/2026", 25, "P3"],
            ]),
        ]
        if not missing_partition:
            files.append(("Export_2026.xlsx", "Orders", [order_header, [*second_order, 1, 80]]))
        for filename, sheet, rows in files:
            book = Workbook()
            book.active.title = sheet
            for row in rows:
                book.active.append(row)
            book.save(root / filename)
            book.close()
        return build_catalog(self.data, "shop")

    def test_combined_workbooks_count_one_total_per_order_and_keep_payment_events(self):
        ledger = load_debt_ledger(self.combined_catalog(), RULEBOOK)
        self.assertEqual([(order.order_id, order.total, order.state) for order in ledger.orders],
                         [("D1", Decimal(150), "hoan thanh"), ("D2", Decimal(90), "tra hang")])
        self.assertEqual([payment.amount for payment in ledger.payments], [50, 25, 25])
        self.assertEqual(ledger.lineage["checks"]["repeated_order_rows"], 1)
        self.assertEqual(ledger.lineage["checks"]["paid_to_date_checked"], 2)
        self.assertEqual(ledger.lineage["checks"]["closing_debt_checked"], 2)

    def test_combined_profile_requires_consistent_totals_grain_and_all_partitions(self):
        for options, code in (
            ({"missing_partition": True}, "partial"),
            ({"conflicting": True}, "key"),
            ({"documented_grain": False}, "partial"),
        ):
            with self.subTest(options=options):
                with self.assertRaises(DebtSourceError) as caught:
                    load_debt_ledger(self.combined_catalog(**options), RULEBOOK)
                self.assertEqual(caught.exception.code, code)

    def test_debt_convention_without_column_mapping_is_partial_not_absent(self):
        template = """# Shop
## File
- `orders.csv`
- `payments.csv`
## Quy ước
{convention}
"""
        root = self.data / "shop"
        root.mkdir(parents=True)
        for name, rows in {
            "orders.csv": [["Order", "Total"], ["D1", "100"]],
            "payments.csv": [["Order", "Paid"], ["D1", "50"]],
        }.items():
            with (root / name).open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)

        conventions = (
            "- **Công nợ**: **Paid** là số tiền trả lần đó; **Total** là số khách phải "
            "trả cho cả đơn.",
            "- Công nợ: Paid là số tiền trả lần đó; Total là số khách phải trả cho cả đơn.",
            "### Công nợ\nPaid là số tiền trả lần đó. Total là số khách phải trả cho cả đơn.",
        )
        for convention in conventions:
            (root / "README.md").write_text(
                template.format(convention=convention), encoding="utf-8")
            with self.assertRaises(DebtSourceError) as caught:
                load_debt_ledger(build_catalog(self.data, "shop"), RULEBOOK)
            self.assertEqual(caught.exception.code, "partial")


if __name__ == "__main__":
    unittest.main()
