"""D01: README-backed debt ledger, source checks and reconciliation."""

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

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
        self.assertEqual(ledger.lineage["coverage"]["source"], "latest_record")

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
        self.assertEqual(caught.exception.code, "column_missing")

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


if __name__ == "__main__":
    unittest.main()
