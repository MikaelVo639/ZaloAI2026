"""Owner-kept ledgers: column meanings with explanations, states written in notes, short
product names, conventions that name the order column, total and note rows."""

import unittest
from datetime import date
from decimal import Decimal

from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    convention_columns,
    load_readme_sales,
    meaning_field,
    note_state,
    parse_day,
)
from test_ingestion_readme_sales import RULEBOOK, _Shop

LEDGER = """# Dữ liệu shop

## File
- `so.csv` (csv)
- `tat.csv` (csv)

## Cột trong `so.csv`
- **Ngày**: ngày bán; dòng sau của đơn để trống
- **Số**: số thứ tự đơn trong sổ
- **Món**: hàng bán, có thể ghi tắt (bảng tra ở file tat.csv)
- **SL**: số lượng
- **Giá**: đơn giá một cái
- **Tiền**: thành tiền của dòng
- **Note**: ghi chú của đơn; đơn huỷ hoặc trả lại được ghi ở đây

## Quy ước
- Dữ liệu có đến hết 31/01/2026.
"""
HEADER = ["Ngày", "Số", "Món", "SL", "Giá", "Tiền", "Note"]
SHORT = [["Viết tắt", "Tên hàng", "Loại"], ["TX", "Trà xanh", "Đồ uống"],
         ["BM", "Bánh mì", "Đồ ăn"]]


def ledger(notes=("", "Khách trả lại", "Khách huỷ"), count=24, name="TX"):
    rows = [HEADER]
    for i in range(count):
        note = notes[i % len(notes)]
        rows.append([f"{1 + i % 28:02d}/01/2026", str(i), name, "2", "15000", "30000", note])
        rows.append(["", str(i), "Bánh mì", "1", "10000", "10000", ""])
    return rows


class MeaningTests(unittest.TestCase):
    def test_meaning_heads(self):
        for meaning, field in (
                ("số thứ tự đơn (hoá đơn) trong sổ", "order_id"),
                ("số thứ tự dòng trong đơn", "line_no"),
                ("số hoá đơn, ghi ở mọi dòng", "order_id"),
                ("hàng bán, có thể ghi tắt", "product_name"),
                ("thành tiền của dòng; dòng phí ghi số âm", "amount"),
                ("kênh bán: tại quầy, giao tận nơi", "channel"),
                ("hãng", None), ("ghi chú", None),
                ("ghi chú của đơn; đơn đang giao ghi ở đây", "status_note"),
                ("giá nhập một đơn vị", None), ("tổng tiền đơn", None)):
            with self.subTest(meaning=meaning):
                self.assertEqual(meaning_field(meaning), field)

    def test_note_states(self):
        self.assertEqual(note_state("Khách trả lại máy"), "tra hang")
        self.assertEqual(note_state("Huỷ đơn"), "huy")
        self.assertEqual(note_state("Không giao được"), "huy")
        self.assertIsNone(note_state("Khách quen"))
        for note in ("Khách huỷ rồi trả lại", "Khách không trả"):
            with self.assertRaises(ReadmeSalesError):
                note_state(note)

    def test_conventions_naming_columns(self):
        readme = ("## Quy ước\n- **Đơn**: các dòng cùng **Số** là một đơn.\n"
                  "- **Đơn trả lại**: **Note** của đơn ghi khách trả lại.\n")
        found = convention_columns(readme)
        self.assertEqual(meaning_field(found["Số"]), "order_id")
        self.assertEqual(meaning_field(found["Note"]), "status_note")

    def test_dates(self):
        self.assertEqual(parse_day("7", "T7-2025"), date(2025, 7, 7))
        self.assertEqual(parse_day("05.03.2026"), date(2026, 3, 5))
        self.assertEqual(parse_day("5-3", "Thang 03 2026"), date(2026, 3, 5))
        with self.assertRaises(ValueError):
            parse_day("7", "Sheet1")


class LedgerTests(_Shop):
    def test_states_in_notes_and_short_names(self):
        catalog = self.shop(LEDGER, {"so.csv": ledger(), "tat.csv": SHORT})
        sales, evidence, _ = load_readme_sales(catalog, RULEBOOK)
        statuses = {s.order_id: s.status for s in sales}
        self.assertEqual([statuses[str(i)] for i in range(3)],
                         ["valid", "returned", "cancelled"])
        self.assertEqual({s.product_name for s in sales}, {"Trà xanh", "Bánh mì"})
        self.assertEqual({s.product_group for s in sales}, {"Đồ uống", "Đồ ăn"})
        self.assertNotIn("product_identity", evidence)

    def test_an_unknown_short_name_leaves_products_unverified(self):
        catalog = self.shop(LEDGER, {"so.csv": ledger(name="XX"), "tat.csv": SHORT})
        _, evidence, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(evidence["product_identity"], "names_unverified")

    def test_an_amount_that_does_not_check_out_abstains(self):
        rows = ledger()
        for row in rows[1:]:
            row[5] = "99000"
        catalog = self.shop(LEDGER, {"so.csv": rows, "tat.csv": SHORT})
        with self.assertRaises(ReadmeSalesError) as caught:
            load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(caught.exception.code, "AMOUNT_UNVERIFIED")

    def test_total_and_note_rows_in_the_date_column_are_skipped(self):
        rows = [*ledger(), ["Cộng tháng 1", "", "", "", "", "960000", ""],
                ["Sổ ghi tay, chép lại", "", "", "", "", "", ""]]
        catalog = self.shop(LEDGER, {"so.csv": rows, "tat.csv": SHORT})
        sales, _, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales), Decimal(24 * 40000))

    def test_conventions_describe_a_ledger_without_column_docs(self):
        readme = ("# Dữ liệu shop\n\n## File\n- `so_ban_hang.csv` (csv)\n- `tat.csv` (csv)\n\n"
                  "## Quy ước\n- Dữ liệu có đến hết 31/01/2026.\n"
                  "- **Đơn**: các dòng cùng **Số** là một đơn.\n"
                  "- **Đơn trả lại**: **Note** của đơn ghi khách trả lại.\n"
                  "- **Tên hàng**: sổ ghi tắt tên hàng, tra ở `tat.csv`.\n")
        rows = ledger(notes=("", "Khách trả lại"))
        catalog = self.shop(readme, {"so_ban_hang.csv": rows, "tat.csv": SHORT})
        sales, evidence, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual({s.order_id for s in sales if s.status == "returned"},
                         {str(i) for i in range(1, 24, 2)})
        self.assertEqual({s.product_name for s in sales}, {"Trà xanh", "Bánh mì"})
        self.assertEqual(evidence["order_ids"], "from_file")


if __name__ == "__main__":
    unittest.main()
