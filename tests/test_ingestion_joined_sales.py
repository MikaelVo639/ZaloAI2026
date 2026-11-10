import csv
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.joined_sales import (
    JoinedSalesError,
    MappingSpec,
    NumericPolicy,
    RuleCapabilities,
    SourceMapping,
    load_joined_sales,
    mapping_for_xlsx_order_detail_product,
)


def source_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JoinedSalesTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        self.business = self.data_root / "synthetic_shop"
        self.business.mkdir(parents=True)
        self.mapping = MappingSpec(
            version="synthetic.v1",
            orders=SourceMapping(
                source="orders.csv",
                fields={
                    "order_id": "oid", "date": "day", "status": "state",
                    "total_due": "header_total", "payment_method": "payment",
                },
                key=("order_id",), grain="one row per order",
                evidence=("fixture:orders",),
            ),
            details=SourceMapping(
                source="details.csv",
                fields={
                    "order_id": "order_ref", "line_no": "line", "product_id": "sku",
                    "amount": "line_amount", "quantity": "quantity",
                    "quantity_base": "quantity_base", "unit": "unit",
                    "unit_price": "unit_price", "discount": "discount",
                },
                key=("order_id", "line_no"), grain="one row per order line",
                evidence=("fixture:details",),
            ),
            products=SourceMapping(
                source="products.csv",
                fields={
                    "product_id": "sku", "product_name": "name",
                    "base_unit": "base_unit", "category": "category",
                },
                key=("product_id",), grain="one row per product",
                evidence=("fixture:products",),
            ),
            numeric_policy=NumericPolicy(
                decimal_separator=",", thousands_separator=".",
                amount_suffix_multipliers={"tr": Decimal("1000000")},
                currency_suffixes=("đ",),
            ),
            evidence=("fixture mapping reviewed",),
        )
        self.rules = RuleCapabilities(
            version="rules.synthetic.a",
            status_mapping={
                "completed": "valid", "cancelled": "cancelled",
                "returned": "returned", "shipping": "valid",
            },
            whole_order_returns=True,
            quantity_base_authoritative=True,
            line_amount_after_discount=True,
            deduplicate_identical_detail_rows=False,
            service_fee_names=("Service fee",),
            service_fee_counts_as_revenue=True,
            service_fee_ranking_eligible=False,
            evidence=("fixture rulebook sections 1 and 3",),
        )

    def _write_csv(self, name, header, rows):
        path = self.business / name
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def _write_sources(self, *, orders=None, details=None, products=None):
        orders = orders if orders is not None else [
            ["O001", "2026-01-01", "completed", "25", "cash"],
        ]
        details = details if details is not None else [
            ["O001", "01", "P001", "20", "2", "48", "case", "10", "0"],
        ]
        products = products if products is not None else [
            ["P001", "Cola", "can", "Drink"],
        ]
        return (
            self._write_csv(
                "orders.csv", ["oid", "day", "state", "header_total", "payment"], orders
            ),
            self._write_csv(
                "details.csv",
                ["order_ref", "line", "sku", "line_amount", "quantity",
                 "quantity_base", "unit", "unit_price", "discount"],
                details,
            ),
            self._write_csv(
                "products.csv", ["sku", "name", "base_unit", "category"], products
            ),
        )

    def _load(self, mapping=None, rules=None):
        catalog = build_catalog(self.data_root, "synthetic_shop")
        return load_joined_sales(
            catalog, mapping=mapping or self.mapping, rule_capabilities=rules or self.rules
        )

    def test_documented_customer_context_preserves_order_and_line_grain(self):
        self._write_sources(details=[
            ["O001", "01", "P001", "20", "2", "48", "case", "10", "0"],
            ["O001", "02", "P001", "10", "1", "24", "case", "10", "0"],
            ["O002", "01", "P001", "20", "2", "48", "case", "10", "0"],
            ["O003", "01", "P001", "10", "1", "24", "case", "10", "0"],
        ])
        self._write_csv("orders.csv",
                        ["oid", "day", "state", "header_total", "payment", "buyer", "label"],
                        [["O001", "2026-01-01", "completed", "30", "cash", "C01", "Lan"],
                         ["O002", "2026-01-01", "completed", "20", "cash", "C02", "Lan"],
                         ["O003", "2026-01-01", "completed", "10", "cash", "", "Khách lẻ"]])
        readme = self.business / "README.md"
        readme.write_text("## Cột trong `orders.csv`\n"
                          "- **oid**: mã đơn\n- **buyer**: mã khách (trống nếu khách lẻ)\n"
                          "- **label**: tên khách\n\n"
                          "## Cột trong `customers.csv`\n"
                          "- **ref**: mã khách\n- **segment**: nhóm khách\n", encoding="utf-8")
        self._write_csv("customers.csv", ["ref", "segment"], [["C01", "Sỉ"]])
        sales, _ = self._load()
        self.assertEqual([(s.customer_id, s.customer_name, s.customer_group) for s in sales],
                         [("C01", "Lan", "Sỉ"), ("C01", "Lan", "Sỉ"),
                          ("C02", "Lan", None), (None, None, None)])

        self._write_csv("customers.csv", ["ref", "segment"], [["C01", "Sỉ"], ["C01", "Lẻ"]])
        sales, evidence = self._load()
        self.assertEqual([s.customer_group for s in sales], [None] * 4)
        self.assertIn("DUPLICATE_PARENT_KEY", {item["code"] for item in evidence["diagnostics"]})

    def test_valid_join_statuses_suffix_quantity_base_service_fee_and_hashes(self):
        paths = self._write_sources(
            orders=[
                ["O001", "2026-01-01", "completed", "999", "cash"],
                ["O002", "2026-01-02", "cancelled", "30", "cash"],
                ["O003", "2026-01-03", "returned", "1.500.000", "transfer"],
                ["O004", "2026-01-04", "shipping", "40", "cod"],
            ],
            details=[
                ["O001", "01", "P001", "20", "2", "48", "case", "10", "0"],
                ["O001", "02", "FEE", "5đ", "1", "1", "fee", "5", "0"],
                ["O002", "01", "P001", "30", "3", "72", "case", "10", "0"],
                ["O003", "01", "P002", "1,5tr", "1", "1", "item", "1.500.000", "0"],
                ["O004", "01", "P002", "40", "4", "4", "item", "10", "0"],
            ],
            products=[
                ["P001", "Cola", "can", "Drink"],
                ["P002", "Meal", "item", "Food"],
                ["FEE", "Service fee", "fee", "Fee"],
            ],
        )
        before = {path.name: source_hash(path) for path in paths}

        sales, evidence = self._load()

        self.assertEqual({path.name: source_hash(path) for path in paths}, before)
        self.assertEqual(len(sales), 5)
        self.assertEqual([sale.order_id for sale in sales],
                         ["O001", "O001", "O002", "O003", "O004"])
        self.assertEqual([sale.status for sale in sales],
                         ["valid", "valid", "cancelled", "returned", "valid"])
        self.assertEqual([sale.amount for sale in sales],
                         [Decimal("20"), Decimal("5"), Decimal("30"),
                          Decimal("1500000.0"), Decimal("40")])
        self.assertEqual(sales[0].quantity_base, Decimal("48"))
        self.assertEqual(sales[0].unit_price_base, Decimal(10) / 24)
        self.assertEqual((sales[0].base_unit, sales[0].product_group), ("can", "Drink"))
        self.assertEqual(sales[0].product_id, "P001")
        self.assertEqual(evidence["counts"]["detail_rows_raw"], 5)
        self.assertEqual(evidence["counts"]["sales_rows"], 5)
        self.assertEqual(evidence["join"]["join_expansion"], 0)
        self.assertEqual(evidence["parsing"]["amount_parsed"], 5)
        self.assertEqual(evidence["parsing"]["amount_rejected"], 0)
        self.assertEqual(evidence["amount_reconciliation"]["matches"], 5)
        self.assertEqual(evidence["amount_reconciliation"]["mismatches"], 0)
        self.assertEqual(evidence["amount_reconciliation"][
            "header_total_differs_from_line_sum"], 1)
        self.assertTrue(evidence["amount_reconciliation"][
            "header_total_is_not_line_revenue"])
        self.assertEqual(evidence["service_fee"], {
            "rows": 1, "counts_as_revenue": True, "ranking_eligible": False
        })
        fee_lineage = evidence["lineage"][1]
        self.assertTrue(fee_lineage["is_service_fee"])
        self.assertTrue(fee_lineage["counts_as_revenue"])
        self.assertFalse(fee_lineage["ranking_eligible"])
        self.assertEqual(fee_lineage["detail"]["source"], "details.csv")
        self.assertNotIn(str(self.root) if hasattr(self, "root") else "never", json.dumps(evidence))
        json.dumps(evidence, ensure_ascii=False, allow_nan=False)

    def test_duplicate_parents_are_rejected_without_join_expansion(self):
        for parent, orders, products in (
            ("orders", [
                ["O001", "2026-01-01", "completed", "20", "cash"],
                ["O001", "2026-01-01", "completed", "20", "cash"],
            ], None),
            ("products", None, [
                ["P001", "Cola", "can", "Drink"],
                ["P001", "Other", "can", "Drink"],
            ]),
        ):
            with self.subTest(parent=parent):
                for path in self.business.glob("*.csv"):
                    path.unlink()
                self._write_sources(orders=orders, products=products)
                with self.assertRaises(JoinedSalesError) as caught:
                    self._load()
                self.assertEqual(caught.exception.code, "DUPLICATE_PARENT_KEY")
                self.assertEqual(caught.exception.diagnostics["duplicate_count"], 1)

    def test_orphan_missing_key_and_duplicate_compound_line_key_fail(self):
        cases = (
            ("orphan_order", [["MISSING", "01", "P001", "10", "1", "1", "can", "10", "0"]],
             "ORPHAN_FK"),
            ("orphan_product", [["O001", "01", "MISSING", "10", "1", "1", "can", "10", "0"]],
             "ORPHAN_FK"),
            ("missing_key", [["O001", "", "P001", "10", "1", "1", "can", "10", "0"]],
             "MISSING_KEY"),
            ("duplicate_line", [
                ["O001", "01", "P001", "10", "1", "1", "can", "10", "0"],
                ["O001", "01", "P001", "11", "1", "1", "can", "11", "0"],
            ], "DUPLICATE_DETAIL_KEY"),
        )
        for name, details, code in cases:
            with self.subTest(name=name):
                for path in self.business.glob("*.csv"):
                    path.unlink()
                self._write_sources(details=details)
                with self.assertRaises(JoinedSalesError) as caught:
                    self._load()
                self.assertEqual(caught.exception.code, code)

    def test_status_capabilities_are_split_scoped_and_not_cached(self):
        self._write_sources(
            orders=[["O001", "2026-01-01", "shipping", "20", "cod"]]
        )
        sales, _ = self._load()
        self.assertEqual(sales[0].status, "valid")

        other_rules = replace(
            self.rules,
            version="rules.synthetic.b",
            status_mapping={"completed": "valid"},
            evidence=("different split rulebook",),
        )
        with self.assertRaises(JoinedSalesError) as caught:
            self._load(rules=other_rules)
        self.assertEqual(caught.exception.code, "UNKNOWN_STATUS")

        self._write_sources(
            orders=[["O001", "2026-01-01", "returned", "20", "cash"]]
        )
        no_whole_return = replace(
            self.rules, version="rules.synthetic.c", whole_order_returns=False,
            evidence=("split without whole-order return capability",),
        )
        with self.assertRaises(JoinedSalesError) as caught:
            self._load(rules=no_whole_return)
        self.assertEqual(caught.exception.code, "RULE_CAPABILITY_MISSING")

    def test_amount_suffix_is_source_scoped_and_unsupported_suffix_rejects(self):
        self._write_sources(details=[
            ["O001", "01", "P001", "1,89tr", "1", "1", "can", "1.890.000", "0"],
        ], orders=[["O001", "2026-01-01", "completed", "1.890.000", "cash"]])
        sales, evidence = self._load()
        self.assertEqual(sales[0].amount, Decimal("1890000.00"))
        self.assertEqual(evidence["amount_reconciliation"]["matches"], 1)

        self._write_sources(
            details=[
                ["O001", "01", "P001", "1,89tr", "1", "1", "can",
                 "1.890.000", "0"],
            ],
            orders=[["O001", "2026-01-01", "completed", "1.890.000", "cash"]],
        )
        mixed_locale_mapping = replace(
            self.mapping,
            numeric_policy=NumericPolicy(
                decimal_separator=",", thousands_separator=".",
                amount_suffix_multipliers={"tr": Decimal("1000000")},
                amount_decimal_separator=".",
                amount_thousands_separator=",",
                amount_suffix_decimal_separator=",",
                amount_suffix_thousands_separator=".",
            ),
            evidence=("source declares dot decimals and comma-decimal tr suffix",),
        )
        mixed_sales, mixed_evidence = self._load(mapping=mixed_locale_mapping)
        self.assertEqual(mixed_sales[0].amount, Decimal("1890000.00"))
        self.assertEqual(mixed_evidence["amount_reconciliation"]["matches"], 1)

        no_suffix_mapping = replace(
            self.mapping,
            numeric_policy=NumericPolicy(decimal_separator=",", thousands_separator="."),
            evidence=("same schema without suffix policy",),
        )
        with self.assertRaises(JoinedSalesError) as caught:
            self._load(mapping=no_suffix_mapping)
        self.assertEqual(caught.exception.code, "NUMERIC_AMBIGUOUS")
        self.assertEqual(caught.exception.diagnostics["amount_rejected"], 1)

        self._write_sources(details=[
            ["O001", "01", "P001", "12k", "1", "1", "can", "12", "0"],
        ])
        with self.assertRaises(JoinedSalesError) as caught:
            self._load()
        self.assertEqual(caught.exception.code, "NUMERIC_AMBIGUOUS")
        self.assertEqual(caught.exception.diagnostics["amount_rejected"], 1)

        self._write_sources(details=[
            ["O001", "01", "P001", "NaN", "1", "1", "can", "12", "0"],
        ])
        with self.assertRaises(JoinedSalesError) as caught:
            self._load()
        self.assertEqual(caught.exception.code, "NUMERIC_AMBIGUOUS")
        self.assertEqual(caught.exception.diagnostics["amount_rejected"], 1)

    def test_formula_mismatch_preserves_source_amount_and_header_total_is_separate(self):
        self._write_sources(
            orders=[["O001", "2026-01-01", "completed", "999", "cash"]],
            details=[["O001", "01", "P001", "21", "2", "48", "case", "10", "0"]],
        )
        sales, evidence = self._load()
        self.assertEqual(sales[0].amount, Decimal("21"))
        self.assertEqual(sales[0].quantity_base, Decimal("48"))
        self.assertEqual(evidence["amount_reconciliation"]["matches"], 0)
        self.assertEqual(evidence["amount_reconciliation"]["mismatches"], 1)
        self.assertEqual(evidence["amount_reconciliation"][
            "header_total_differs_from_line_sum"], 1)
        self.assertEqual(evidence["diagnostics"][0]["code"],
                         "AMOUNT_RECONCILIATION_MISMATCH")

    def test_exact_detail_dedup_requires_explicit_rule_capability(self):
        duplicate = ["O001", "01", "P001", "20", "2", "48", "case", "10", "0"]
        self._write_sources(details=[duplicate, duplicate])
        with self.assertRaises(JoinedSalesError) as caught:
            self._load()
        self.assertEqual(caught.exception.code, "DUPLICATE_DETAIL_KEY")

        allowed = replace(
            self.rules, deduplicate_identical_detail_rows=True,
            evidence=("split rule explicitly deduplicates identical order rows",),
        )
        sales, evidence = self._load(rules=allowed)
        self.assertEqual(len(sales), 1)
        self.assertEqual(evidence["counts"]["duplicates_removed"], 1)


class XlsxOrderDetailProductTests(unittest.TestCase):
    ORDER_HEADER = ["Mã hóa đơn", "Thời gian", "Tổng tiền hàng", "Trạng thái"]
    DETAIL_HEADER = [
        "Mã hóa đơn", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Giảm giá",
        "Thành tiền", "Số lượng quy đổi", "Đơn vị tính", "STT",
    ]
    PRODUCT_HEADER = ["Mã hàng", "Tên hàng", "Nhóm hàng(3 Cấp)", "ĐVT"]

    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        self.rules = RuleCapabilities(
            version="rules.xlsx.synthetic.v1",
            status_mapping={
                "Hoàn thành": "valid", "Đã hủy": "cancelled",
                "Đã trả hàng": "returned", "Đang giao hàng": "valid",
            },
            whole_order_returns=True,
            quantity_base_authoritative=True,
            line_amount_after_discount=True,
            deduplicate_identical_detail_rows=False,
            service_fee_names=("Phí phục vụ",),
            service_fee_counts_as_revenue=True,
            service_fee_ranking_eligible=False,
            evidence=("synthetic current-split rulebook",),
        )

    @staticmethod
    def _write_xlsx(path, headers, rows):
        book = Workbook()
        sheet = book.active
        sheet.title = "Sheet1"
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        book.save(path)
        book.close()

    def _write_sources(self, business="xlsx_shop", *, orders=None, details=None,
                       products=None):
        target = self.data_root / business
        target.mkdir(parents=True)
        orders = orders if orders is not None else [
            ["O1", "01/01/2026", "25,000", "Hoàn thành"],
            ["O2", "02/01/2026", "10,000", "Đã hủy"],
            ["O3", "03/01/2026", "30,000", "Đã trả hàng"],
            ["O4", "04/01/2026", "40,000", "Đang giao hàng"],
        ]
        details = details if details is not None else [
            ["O1", "P1", "Nước", 2, "10,000", "0", "20,000", 48, "thùng", 1],
            ["O1", "P2", "Bánh", 1, "10,000", "5,000", "5,000", 1, "gói", 2],
            ["O2", "P1", "Nước", 1, "10,000", "0", "10,000", 24, "thùng", 1],
            ["O3", "P2", "Bánh", 3, "10,000", "0", "30,000", 3, "gói", 1],
            ["O4", "P2", "Bánh", 4, "10,000", "0", "40,000", 4, "gói", 1],
        ]
        products = products if products is not None else [
            ["P1", "Nước", "Đồ uống", "chai"],
            ["P2", "Bánh", "Đồ ăn", "gói"],
        ]
        self._write_xlsx(target / "orders-export.xlsx", self.ORDER_HEADER, orders)
        self._write_xlsx(target / "details-export.xlsx", self.DETAIL_HEADER, details)
        self._write_xlsx(target / "products-export.xlsx", self.PRODUCT_HEADER, products)
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
        return target

    def _load(self, business="xlsx_shop", rules=None):
        catalog = build_catalog(self.data_root, business)
        mapping = mapping_for_xlsx_order_detail_product(catalog)
        return load_joined_sales(
            catalog, mapping=mapping, rule_capabilities=rules or self.rules,
        )

    def test_valid_join_comma_thousands_dates_units_statuses_and_hashes(self):
        target = self._write_sources()
        before = {path.name: source_hash(path) for path in target.glob("*.xlsx")}

        sales, evidence = self._load()

        self.assertEqual({path.name: source_hash(path) for path in target.glob("*.xlsx")},
                         before)
        self.assertEqual(len(sales), 5)
        self.assertEqual([sale.amount for sale in sales], [
            Decimal("20000"), Decimal("5000"), Decimal("10000"),
            Decimal("30000"), Decimal("40000"),
        ])
        self.assertEqual([sale.quantity_base for sale in sales], [
            Decimal("48"), Decimal("1"), Decimal("24"), Decimal("3"), Decimal("4"),
        ])
        self.assertEqual([sale.status for sale in sales],
                         ["valid", "valid", "cancelled", "returned", "valid"])
        self.assertEqual(sales[0].day.isoformat(), "2026-01-01")
        self.assertEqual(evidence["mapping"]["version"],
                         "xlsx_order_detail_product.v1")
        self.assertEqual(evidence["mapping"]["numeric_policy"]
                         ["thousands_separator"], ",")
        self.assertTrue(all("::Sheet1" in item["source"]
                            for item in evidence["sources"]))
        self.assertEqual(evidence["join"]["join_expansion"], 0)
        self.assertEqual(evidence["parsing"]["amount_parsed"], 5)
        self.assertEqual(evidence["parsing"]["amount_rejected"], 0)
        self.assertEqual(evidence["amount_reconciliation"]["matches"], 5)
        self.assertEqual(evidence["amount_reconciliation"]
                         ["header_total_differs_from_line_sum"], 0)
        json.dumps(evidence, ensure_ascii=False, allow_nan=False)

    def test_new_schema_missing_duplicate_orphan_and_fanout_are_rejected(self):
        cases = (
            ("missing", None,
             [["O1", "P1", "Nước", 2, "10,000", "0", "20,000", 48,
               "thùng", None]], None, "MISSING_KEY"),
            ("duplicate_detail", None, [
                ["O1", "P1", "Nước", 2, "10,000", "0", "20,000", 48, "thùng", 1],
                ["O1", "P2", "Bánh", 1, "5,000", "0", "5,000", 1, "gói", 1],
            ], None, "DUPLICATE_DETAIL_KEY"),
            ("orphan", None,
             [["MISSING", "P1", "Nước", 2, "10,000", "0", "20,000", 48,
               "thùng", 1]], None, "ORPHAN_FK"),
            ("fanout", None,
             [["O1", "P1", "Nước", 2, "10,000", "0", "20,000", 48,
               "thùng", 1]], [
                   ["P1", "Nước", "Đồ uống", "chai"],
                   ["P1", "Nước khác", "Đồ uống", "chai"],
             ], "DUPLICATE_PARENT_KEY"),
        )
        for business, orders, details, products, code in cases:
            with self.subTest(business=business):
                self._write_sources(
                    business, orders=orders, details=details, products=products,
                )
                with self.assertRaises(JoinedSalesError) as caught:
                    self._load(business)
                self.assertEqual(caught.exception.code, code)

    def test_new_schema_unknown_status_and_incompatible_rules_are_rejected(self):
        self._write_sources(
            "unknown_status",
            orders=[["O1", "01/01/2026", "20,000", "Chờ xác nhận"]],
            details=[["O1", "P1", "Nước", 2, "10,000", "0", "20,000", 48,
                      "thùng", 1]],
        )
        with self.assertRaises(JoinedSalesError) as caught:
            self._load("unknown_status")
        self.assertEqual(caught.exception.code, "UNKNOWN_STATUS")

        self._write_sources(
            "return_without_rule",
            orders=[["O1", "01/01/2026", "20,000", "Đã trả hàng"]],
            details=[["O1", "P1", "Nước", 2, "10,000", "0", "20,000", 48,
                      "thùng", 1]],
        )
        incompatible = replace(
            self.rules, whole_order_returns=False,
            evidence=("current split does not define whole-order returns",),
        )
        with self.assertRaises(JoinedSalesError) as caught:
            self._load("return_without_rule", incompatible)
        self.assertEqual(caught.exception.code, "RULE_CAPABILITY_MISSING")

    def test_new_schema_mapping_requires_readme_evidence_and_is_unambiguous(self):
        target = self._write_sources("ambiguous")
        self._write_xlsx(
            target / "second-orders.xlsx", self.ORDER_HEADER,
            [["O9", "01/01/2026", "1,000", "Hoàn thành"]],
        )
        catalog = build_catalog(self.data_root, "ambiguous")
        with self.assertRaises(JoinedSalesError) as caught:
            mapping_for_xlsx_order_detail_product(catalog)
        self.assertEqual(caught.exception.code, "MAPPING_AMBIGUOUS")

        target = self._write_sources("missing_evidence")
        (target / "README.md").write_text("undocumented export", encoding="utf-8")
        catalog = build_catalog(self.data_root, "missing_evidence")
        with self.assertRaises(JoinedSalesError) as caught:
            mapping_for_xlsx_order_detail_product(catalog)
        self.assertEqual(caught.exception.code, "MAPPING_EVIDENCE_MISSING")


if __name__ == "__main__":
    unittest.main()
