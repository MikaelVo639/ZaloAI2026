import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from datamate.contracts import Catalog, Table
from datamate.ingestion.joined_sales import (
    MappingSpec,
    NumericPolicy,
    SourceMapping,
)
from datamate.ingestion.schema_descriptor import describe_catalog


class SchemaDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    @staticmethod
    def _mapping():
        return MappingSpec(
            version="synthetic.odp.v1",
            orders=SourceMapping(
                "orders.xlsx::*",
                {"order_id": "order", "date": "day", "status": "status",
                 "total_due": "total"},
                ("order_id",), "one row per order",
            ),
            details=SourceMapping(
                "details.xlsx::Lines",
                {"order_id": "order", "line_no": "line", "product_id": "product",
                 "amount": "amount", "quantity_base": "quantity_base"},
                ("order_id", "line_no"), "one row per line",
            ),
            products=SourceMapping(
                "products.xlsx::Products",
                {"product_id": "product", "product_name": "name",
                 "base_unit": "unit"},
                ("product_id",), "one row per product",
            ),
            numeric_policy=NumericPolicy(),
            evidence=("synthetic evidence",),
        )

    def _catalog(self, root):
        return Catalog("must-not-appear", "README secret", (
            Table(
                root / "orders.xlsx", "Jan", ("order", "day", "status", "total"),
                ({"order": "CUSTOMER-SECRET-123", "day": "2026-01-01",
                  "status": "completed", "total": Decimal("999999")},),
            ),
            Table(
                root / "details.xlsx", "Lines",
                ("order", "line", "product", "amount", "quantity_base"),
                ({"order": "CUSTOMER-SECRET-123", "line": 1, "product": "P1",
                  "amount": Decimal("999999"), "quantity_base": 2},),
            ),
            Table(
                root / "products.xlsx", "Products", ("product", "name", "unit"),
                ({"product": "P1", "name": "PRIVATE PRODUCT", "unit": "piece"},),
            ),
        ))

    def test_descriptor_is_json_safe_bounded_redacted_and_path_stable(self):
        root_a = Path(self.temporary.name) / "checkout-a" / "private" / "business"
        root_b = Path(self.temporary.name) / "elsewhere" / "business"
        first = describe_catalog(
            self._catalog(root_a), mapping=self._mapping(),
            diagnostics=("z limitation", "a limitation"), max_sample_values=1,
        )
        second = describe_catalog(
            self._catalog(root_b), mapping=self._mapping(),
            diagnostics=("a limitation", "z limitation"), max_sample_values=1,
        )
        self.assertEqual(first, second)
        encoded = json.dumps(first, ensure_ascii=False, allow_nan=False)
        self.assertNotIn(str(root_a), encoded)
        self.assertNotIn("must-not-appear", encoded)
        self.assertNotIn("CUSTOMER-SECRET-123", encoded)
        self.assertNotIn("PRIVATE PRODUCT", encoded)
        self.assertNotIn("999999", encoded)
        self.assertEqual(first["schema_version"], "schema_descriptor.v1")
        self.assertEqual(first["mapping_version"], "synthetic.odp.v1")
        self.assertEqual(len(first["joins"]), 2)
        self.assertEqual(first["tables"][0]["roles"][0]["role"], "details")
        for table in first["tables"]:
            for column in table["columns"]:
                self.assertLessEqual(len(column["sample_shapes"]), 1)

    def test_descriptor_without_mapping_declares_unknowns_and_checks_limit(self):
        descriptor = describe_catalog(self._catalog(Path(self.temporary.name)))
        self.assertIsNone(descriptor["mapping_version"])
        self.assertFalse(descriptor["joins"])
        self.assertIn("grain, keys and joins are undeclared", descriptor["limitations"])
        with self.assertRaises(ValueError):
            describe_catalog(self._catalog(Path(self.temporary.name)), max_sample_values=6)


if __name__ == "__main__":
    unittest.main()
