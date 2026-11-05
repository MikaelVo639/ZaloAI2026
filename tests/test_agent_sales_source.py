import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

import datamate.agent.sales_source as source_module
from datamate.agent import compute
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.joined_sales import JoinedSalesError, RuleCapabilities


RULEBOOK = """# Synthetic current-split rules
| Luật | Mặc định |
|---|---|
| Đơn tính vào doanh thu | đơn không huỷ và không trả hàng; có trả thì bỏ cả đơn. Đơn đang giao vẫn tính |
| Doanh thu một đơn | tổng thành tiền các dòng hàng, đã trừ giảm giá; không trừ voucher, không cộng thuế VAT, không gồm phí giao hàng |
| Bán chạy | theo số lượng quy về đơn vị cơ bản |
| Mặt hàng | Phí phục vụ không phải mặt hàng, không xếp hạng. Vẫn tính vào doanh thu |
| Trạng thái đơn | mỗi đơn là hoàn thành, huỷ, trả hàng hoặc đang giao |
| Hai dòng của cùng một đơn giống nhau ở mọi ô | lỗi xuất file, tính một lần |
"""


class AgentSalesSourceTests(unittest.TestCase):
    ORDER_HEADER = ["Mã hóa đơn", "Thời gian", "Tổng tiền hàng", "Trạng thái"]
    DETAIL_HEADER = [
        "Mã hóa đơn", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Giảm giá",
        "Thành tiền", "Số lượng quy đổi", "Đơn vị tính", "STT",
    ]
    PRODUCT_HEADER = ["Mã hàng", "Tên hàng", "Nhóm hàng(3 Cấp)", "ĐVT"]

    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        self.data_root.mkdir()

    @staticmethod
    def _write_xlsx(path, header, rows):
        book = Workbook()
        sheet = book.active
        sheet.title = "Sheet1"
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        book.save(path)
        book.close()

    @staticmethod
    def _write_csv(path, header, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)

    def _write_xlsx_schema(self, business="arbitrary-store", *, amount="20,000",
                           include_products=True, duplicate_orders=False):
        target = self.data_root / business
        target.mkdir(parents=True, exist_ok=True)
        self._write_xlsx(
            target / "orders.xlsx", self.ORDER_HEADER,
            [["O1", "01/01/2026", "20,000", "completed"]],
        )
        if duplicate_orders:
            self._write_xlsx(
                target / "orders-copy.xlsx", self.ORDER_HEADER,
                [["O2", "01/01/2026", "1,000", "completed"]],
            )
        self._write_xlsx(
            target / "details.xlsx", self.DETAIL_HEADER,
            [["O1", "P1", "Nước", 2, "10,000", "0", amount, 48, "thùng", 1]],
        )
        if include_products:
            self._write_xlsx(
                target / "products.xlsx", self.PRODUCT_HEADER,
                [["P1", "Nước", "Đồ uống", "chai"]],
            )
        (target / "README.md").write_text(
            "\n".join((
                "- **Mã hóa đơn**: mã đơn",
                "- **Thành tiền**: thành tiền sau giảm",
                "- **STT**: số thứ tự dòng trong đơn",
                "- **Số lượng quy đổi**: số lượng đã đổi ra đơn vị cơ bản",
                "- **ĐVT**: đơn vị cơ bản",
            )),
            encoding="utf-8",
        )
        return build_catalog(self.data_root, business)

    def test_flat_schema_keeps_flat_adapter(self):
        target = self.data_root / "flat"
        self._write_csv(
            target / "sales.csv",
            ["order_id", "order_date", "status", "line_amount", "product_id",
             "product_name", "quantity_base"],
            [["F1", "2026-01-01", "completed", "10", "P1", "Flat", "2"]],
        )
        with patch.object(source_module, "load_joined_sales",
                          side_effect=AssertionError("joined adapter must not run")):
            sales, evidence, eligible = source_module.load_business_sales(
                build_catalog(self.data_root, "flat"), RULEBOOK,
            )
        self.assertEqual(len(sales), 1)
        self.assertEqual(evidence["adapter"], "flat_sales")
        self.assertIsNone(eligible)

    def test_xlsx_signature_calls_data02_factory_and_joined_adapter(self):
        catalog = self._write_xlsx_schema("name-with-no-special-routing")
        with patch.object(
            source_module, "mapping_for_xlsx_order_detail_product",
            wraps=source_module.mapping_for_xlsx_order_detail_product,
        ) as factory, patch.object(
            source_module, "load_joined_sales", wraps=source_module.load_joined_sales,
        ) as loader:
            sales, evidence, eligible = source_module.load_business_sales(catalog, RULEBOOK)

        self.assertEqual(factory.call_count, 1)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(loader.call_args.kwargs["mapping"].version,
                         "xlsx_order_detail_product.v1")
        rules = loader.call_args.kwargs["rule_capabilities"]
        self.assertIsInstance(rules, RuleCapabilities)
        self.assertTrue(rules.evidence)
        self.assertEqual(len(sales), 1)
        self.assertEqual(str(sales[0].amount), "20000")
        self.assertEqual(evidence["adapter"], "joined_sales")
        self.assertEqual(evidence["mapping"]["version"],
                         "xlsx_order_detail_product.v1")
        self.assertEqual(eligible, frozenset({0}))

    def test_partial_xlsx_schema_abstains_without_join_or_flat_load(self):
        catalog = self._write_xlsx_schema(include_products=False)
        with patch.object(source_module, "load_joined_sales") as joined, \
                patch.object(source_module, "load_sales") as flat:
            with self.assertRaises(JoinedSalesError) as caught:
                source_module.load_business_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "SCHEMA_MISMATCH")
        self.assertEqual(caught.exception.diagnostics["role"], "products")
        joined.assert_not_called()
        flat.assert_not_called()

    def test_ambiguous_xlsx_role_abstains_with_diagnostics(self):
        catalog = self._write_xlsx_schema(duplicate_orders=True)
        with patch.object(source_module, "load_joined_sales") as joined, \
                patch.object(source_module, "load_sales") as flat:
            with self.assertRaises(JoinedSalesError) as caught:
                source_module.load_business_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "MAPPING_AMBIGUOUS")
        self.assertEqual(caught.exception.diagnostics,
                         {"code": "MAPPING_AMBIGUOUS", "role": "orders", "match_count": 2})
        joined.assert_not_called()
        flat.assert_not_called()

    def test_parse_failure_is_cached_as_abstain_not_zero(self):
        catalog = self._write_xlsx_schema(amount="not-a-number")
        prepared = compute.prepare_sales({}, catalog, RULEBOOK)
        self.assertFalse(prepared["ok"])
        self.assertIn("NUMERIC_AMBIGUOUS", prepared["reason"])
        self.assertNotIn("answer", prepared)

    def test_flat_status_aliases_are_gated_by_current_rulebook(self):
        target = self.data_root / "statuses"
        self._write_csv(
            target / "sales.csv",
            ["order_id", "order_date", "status", "line_amount", "product_name"],
            [
                ["O1", "2026-01-01", "Hoàn thành", "10", "A"],
                ["O2", "2026-01-01", "Đã hủy", "20", "B"],
                ["O3", "2026-01-01", "Đã trả hàng", "30", "C"],
                ["O4", "2026-01-01", "Đang giao hàng", "40", "D"],
            ],
        )
        catalog = build_catalog(self.data_root, "statuses")
        sales, evidence, _ = source_module.load_business_sales(catalog, RULEBOOK)
        self.assertEqual([sale.status for sale in sales],
                         ["valid", "cancelled", "returned", "valid"])
        self.assertEqual(evidence["status_policy"]["version"],
                         "rulebook_status_aliases.v1")
        self.assertIn("Trạng thái đơn", evidence["status_policy"]["evidence"][0])

        changed = RULEBOOK.replace(
            "| Trạng thái đơn | mỗi đơn là hoàn thành, huỷ, trả hàng hoặc đang giao |\n",
            "",
        ).replace(
            "| Đơn tính vào doanh thu | đơn không huỷ và không trả hàng; có trả thì bỏ cả đơn. Đơn đang giao vẫn tính |\n",
            "| Đơn tính vào doanh thu | mọi trạng thái đều tính |\n",
        )
        with self.assertRaises(JoinedSalesError) as caught:
            source_module.load_business_sales(catalog, changed)
        self.assertEqual(caught.exception.code, "RULE_CAPABILITY_MISSING")

    def test_unreviewed_status_remains_an_ingestion_abstain(self):
        target = self.data_root / "unknown-status"
        self._write_csv(
            target / "sales.csv",
            ["order_id", "order_date", "status", "line_amount", "product_name"],
            [["O1", "2026-01-01", "Thành công", "10", "A"]],
        )
        prepared = compute.prepare_sales(
            {}, build_catalog(self.data_root, "unknown-status"), RULEBOOK,
        )
        self.assertFalse(prepared["ok"])
        self.assertIn("Unknown status 'Thành công'", prepared["reason"])
        self.assertNotIn("answer", prepared)


if __name__ == "__main__":
    unittest.main()
