"""Credit due dates come only from a complete README-described source."""

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.credit_terms import CreditTermsError, load_credit_terms

README = """# Shop
## Cột trong `credit.csv`
- **Đơn**: mã đơn hàng
- **Kỳ hạn**: số ngày được nợ
- **Ngày phải trả**: hạn thanh toán
"""


class CreditTermsTests(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"

    def catalog(self, rows, readme=README, business="shop"):
        root = self.data / business
        root.mkdir(parents=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        with (root / "credit.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(rows)
        return build_catalog(self.data, business)

    def test_complete_source_preserves_due_date_term_and_lineage(self):
        records, evidence = load_credit_terms(self.catalog([
            ["Đơn", "Kỳ hạn", "Ngày phải trả"],
            ["O2", 30, "12/02/2026"], ["O1", "", "01/02/2026"],
        ]))
        self.assertEqual([(row.order_id, row.due_date, row.term_days) for row in records], [
            ("O1", date(2026, 2, 1), None), ("O2", date(2026, 2, 12), 30),
        ])
        self.assertEqual(evidence["records"], 2)
        self.assertEqual(evidence["sources"], ["credit.csv"])

    def test_incomplete_duplicate_or_invalid_source_is_rejected(self):
        incomplete = README.replace("- **Ngày phải trả**: hạn thanh toán\n", "")
        with self.assertRaises(CreditTermsError):
            load_credit_terms(self.catalog([["Đơn", "Kỳ hạn"], ["O1", 30]], incomplete))

        with self.assertRaises(CreditTermsError):
            load_credit_terms(self.catalog([
                ["Đơn", "Kỳ hạn", "Ngày phải trả"],
                ["O1", 30, "01/02/2026"], ["O1", 30, "01/02/2026"],
            ], business="duplicate"))

        with self.assertRaises(CreditTermsError):
            load_credit_terms(self.catalog([
                ["Đơn", "Kỳ hạn", "Ngày phải trả"], ["O1", -1, "01/02/2026"],
            ], business="invalid"))


if __name__ == "__main__":
    unittest.main()
