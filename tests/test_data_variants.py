"""Data-layout variants keep the facts: the same sales load from the rewritten layout."""

import unittest
from decimal import Decimal

from datamate.evaluation.data_variants import (
    catalogue_stock_column,
    duplicate_orders,
    monthly_files,
    repeated_headers,
    summary_rows,
)
from datamate.ingestion.catalog import build_catalog, iter_rows
from datamate.ingestion.readme_sales import load_readme_sales
from test_ingestion_readme_sales import (
    FLAT_HEADER,
    FLAT_README,
    INVOICE_README,
    RULEBOOK,
    _Shop,
)


def _totals(sales):
    return sorted((s.order_id, s.day, s.status, s.amount) for s in sales)


class DataVariantTests(_Shop):
    def test_monthly_files_keep_every_sale(self):
        rows = [FLAT_HEADER,
                ["HD1", "30/12/2025", "Xong", "Cà phê", "2", "ly", "50,000", "2", "An"],
                ["", "", "", "Trà", "1", "ly", "20,000", "1", ""],  # continuation line
                ["HD2", "02/01/2026", "Xong", "Trà", "1", "ly", "20000", "1", ""],
                ["HD3", "03/02/2026", "khách hủy", "Cà phê", "1", "ly", "25k", "1", ""]]
        original = self.shop(FLAT_README, {"ban_hang.csv": rows})
        variant = self.data.parent / "variant"
        self.assertTrue(monthly_files(self.data / "shop", variant / "shop"))
        names = sorted(p.name for p in (variant / "shop").iterdir())
        self.assertEqual(names, ["README.md", "ban_hang_T12_2025.csv", "ban_hang_T1_2026.csv",
                                 "ban_hang_T2_2026.csv"])
        self.assertIn("`ban_hang_T<tháng>_<năm>.csv`",
                      (variant / "shop" / "README.md").read_text(encoding="utf-8"))
        self.assertTrue((self.data / "shop" / "ban_hang.csv").exists())  # source untouched
        before, _, _ = load_readme_sales(original, RULEBOOK)
        after, _, _ = load_readme_sales(build_catalog(variant, "shop"), RULEBOOK)
        self.assertEqual(_totals(after), _totals(before))
        # Every monthly file keeps the source header, an always-empty last column included.
        for path in (variant / "shop").glob("ban_hang_T*.csv"):
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(encoding="utf-8-sig").splitlines()[0].split(","),
                                 FLAT_HEADER)

    def test_duplicate_orders_count_once(self):
        header = ["Mã bill", "Ngày GD", "Status", "Tien hang"]
        rows = [header, *[[f"B{i}", "01/01/2026", "Hoàn thành", f"{i}000"]
                          for i in range(1, 60)]]
        original = self.shop(INVOICE_README, {"hoa_don.csv": rows})
        variant = self.data.parent / "variant"
        self.assertTrue(duplicate_orders(self.data / "shop", variant / "shop"))
        before, _, _ = load_readme_sales(original, RULEBOOK)
        after, evidence, _ = load_readme_sales(build_catalog(variant, "shop"), RULEBOOK)
        self.assertEqual(evidence["duplicates_removed"], 2)  # rows 25 and 50
        self.assertEqual(_totals(after), _totals(before))
        self.assertEqual(sum(s.amount for s in after), Decimal(1770000))

    def test_repeated_headers_keep_every_sale_and_leave_the_original_file_intact(self):
        rows = [FLAT_HEADER,
                ["HD1", "05/01/2026", "Xong", "Cà phê", "2", "ly", "50000", "2", "An"],
                ["", "", "", "Trà", "1", "ly", "20000", "1", ""]]
        original = self.shop(FLAT_README, {"ban_hang.csv": rows})
        source_path = self.data / "shop" / "ban_hang.csv"
        source_bytes = source_path.read_bytes()
        variant = self.data.parent / "variant"
        self.assertTrue(repeated_headers(self.data / "shop", variant / "shop"))
        changed = build_catalog(variant, "shop")
        self.assertEqual(list(iter_rows(changed.tables[0]))[1], dict(zip(FLAT_HEADER, FLAT_HEADER)))
        self.assertEqual(source_path.read_bytes(), source_bytes)
        before, _, _ = load_readme_sales(original, RULEBOOK)
        after, _, _ = load_readme_sales(changed, RULEBOOK)
        self.assertEqual(after, before)

    def test_catalogue_stock_column_keeps_the_documented_base_unit(self):
        readme = FLAT_README + """
## Cột trong `catalogue.csv`
- **Mã**: mã hàng
- **Hàng**: tên
- **ĐV**: đơn vị cơ bản
- **Loại**: nhóm hàng
"""
        files = {
            "ban_hang.csv": [FLAT_HEADER,
                             ["A1", "05/01/2026", "Xong", "Trà", 1, "hộp", 9000, 12, "An"]],
            "catalogue.csv": [["Mã", "Hàng", "ĐV", "Loại"], ["SKU01", "Trà", "gói", "Đồ uống"]],
        }
        original = self.shop(readme, files)
        variant = self.data.parent / "variant"
        self.assertTrue(catalogue_stock_column(self.data / "shop", variant / "shop"))
        before, _, _ = load_readme_sales(original, RULEBOOK)
        after, _, _ = load_readme_sales(build_catalog(variant, "shop"), RULEBOOK)
        self.assertEqual(after, before)
        self.assertEqual(after[0].base_unit, "goi")

        files["catalogue.csv"] = [[*files["catalogue.csv"][0], "Phiếu nhập"],
                                  [*files["catalogue.csv"][1], "N001"]]
        other = self.shop(readme + "- **Phiếu nhập**: mã phiếu nhập\n", files, business="receipt")
        sales, _, _ = load_readme_sales(other, RULEBOOK)
        self.assertIsNone(sales[0].base_unit)

    def test_summary_rows_are_not_sales(self):
        rows = [FLAT_HEADER,
                ["HD1", "05/01/2026", "Xong", "Cà phê", "2", "ly", "50,000", "2", "An"],
                ["", "", "", "Trà", "1", "ly", "20,000", "1", ""],
                ["HD2", "06/01/2026", "Xong", "Trà", "1", "ly", "20000", "1", ""]]
        original = self.shop(FLAT_README, {"ban_hang.csv": rows})
        variant = self.data.parent / "variant"
        self.assertTrue(summary_rows(self.data / "shop", variant / "shop"))
        text = (variant / "shop" / "ban_hang.csv").read_text(encoding="utf-8-sig")
        self.assertIn("Tổng 05/01", text)
        self.assertIn("Tổng 06/01", text)
        before, _, _ = load_readme_sales(original, RULEBOOK)
        after, evidence, _ = load_readme_sales(build_catalog(variant, "shop"), RULEBOOK)
        self.assertEqual(evidence["counts"]["summary_rows_skipped"], 2)
        self.assertEqual(_totals(after), _totals(before))


if __name__ == "__main__":
    unittest.main()
