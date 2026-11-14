"""Owner-kept ledgers: column meanings with explanations, states written in notes, short
product names, conventions that name the order column, total and note rows."""

import sqlite3
import unittest
from datetime import date
from decimal import Decimal

from datamate.agent.baseline import BaselineAgent
from datamate.agent.compute import answer_plan
from datamate.contracts import BusinessContext, Plan, Question, Split
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    convention_columns,
    load_readme_sales,
    meaning_field,
    note_state,
    parse_day,
    sqlite_staff_codes,
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
GROSS_RULEBOOK = RULEBOOK.replace(
    "tổng thành tiền các dòng hàng, đã trừ giảm giá",
    "tổng thành tiền các dòng hàng đã giảm từng dòng; không trừ giảm giá trên cả đơn; "
    "không gồm phí giao hàng và không cộng thuế VAT")
TICKETS = """## File
- `register.csv` (csv)

## Cột trong `register.csv`
- **Ngày**: ngày bán
- **Giá**: tổng tiền phiếu; trước giảm giá cả đơn, không gồm phí giao, không gồm thuế

## Quy ước
- Mỗi dòng là một phiếu dịch vụ. Chỉ ghi phiếu đã hoàn thành.
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

    def test_undocumented_note_states_do_not_mask_a_documented_state(self):
        readme = LEDGER.replace("huỷ hoặc trả lại", "huỷ hoặc đang giao")
        rows = ledger(("Khách huỷ; đang làm thủ tục hoàn trả",
                       "Hỏi có được trả lại không", "Khách không trả lại", "Đang giao"), count=24)
        catalog = self.shop(readme, {"so.csv": rows, "tat.csv": SHORT})
        sales, _, _ = load_readme_sales(catalog, RULEBOOK)
        values = {s.order_id: s.status for s in sales}
        self.assertEqual([values[str(i)] for i in range(4)],
                         ["cancelled", "valid", "valid", "valid"])
        rows[2][-1] = "Đang giao"  # order 0 has notes naming two documented states
        catalog = self.shop(readme, {"so.csv": rows, "tat.csv": SHORT})
        with self.assertRaisesRegex(ReadmeSalesError, "STATUS_AMBIGUOUS"):
            load_readme_sales(catalog, RULEBOOK)

    def test_documented_adjustments_are_neither_revenue_nor_products(self):
        readme = LEDGER.replace("ở file tat.csv)", "ở file tat.csv); không phải mặt hàng: "
                                "`Quà tri ân` là giảm giá của cả đơn; "
                                "`Cước bưu tá` là phí giao hàng; "
                                "`Điểm thưởng` dùng để giảm giá cả đơn")
        rows = ledger(("",))
        rows[3:3] = [["", "0", "Quà tri ân", 1, 95000, -1000, ""],
                     ["", "0", "Cước bưu tá", 1, 65000, 5000, ""],
                     ["", "0", "Điểm thưởng", "", "", -1500, ""]]
        rows.append(["25/01/2026", "only-fee", "Cước bưu tá", "", "", 5000, ""])
        catalog = self.shop(readme, {"so.csv": rows, "tat.csv": SHORT})
        sales, evidence, eligible = load_readme_sales(catalog, GROSS_RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales), Decimal(24 * 40000))
        self.assertEqual(len({s.order_id for s in sales}), 25)
        self.assertEqual({sales[i].product_name for i in eligible}, {"Trà xanh", "Bánh mì"})
        self.assertEqual(evidence["counts"]["order_adjustment_rows_excluded"], 4)
        with self.assertRaisesRegex(ReadmeSalesError, "RULE_CAPABILITY_MISSING"):
            load_readme_sales(catalog, RULEBOOK)
        with self.assertRaisesRegex(ReadmeSalesError, "RULE_CAPABILITY_MISSING"):
            load_readme_sales(catalog, "\n".join(line for line in GROSS_RULEBOOK.splitlines()
                                                 if not line.startswith("| Mặt hàng")))

    def test_unquoted_adjustment_list_matches_whole_labels(self):
        readme = LEDGER.replace(", có thể ghi tắt (bảng tra ở file tat.csv)",
                                "; không phải hàng (giảm giá cả đơn, dùng điểm, phí giao hàng)")
        rows = ledger(("",), name="Trà xanh")
        rows[2:2] = [["", "0", label, 1, 300, 300, ""] for label in
                     ("Giảm giá cả đơn", "Dùng điểm", "Phí giao hàng", "Dùng điểm xếp hình")]
        catalog = self.shop(readme, {"so.csv": rows, "tat.csv": SHORT})
        sales, evidence, eligible = load_readme_sales(catalog, GROSS_RULEBOOK)
        self.assertEqual(evidence["counts"]["order_adjustment_rows_excluded"], 3)
        self.assertEqual(sum(s.amount for s in sales), Decimal(24 * 40000 + 300))
        self.assertIn("Dùng điểm xếp hình", {sales[i].product_name for i in eligible})

    def test_ticket_ledger_answers_totals_without_product_facts(self):
        rows = [["Ngày", "Giá"], ["10/01/2026", 40000], ["10/01/2026", 40000],
                ["11/01/2026", 50000], ["Tổng", 130000]]
        catalog = self.shop(TICKETS, {"register.csv": rows})
        day = date(2026, 1, 10)
        sales, source, eligible = load_readme_sales(catalog, GROSS_RULEBOOK)
        self.assertEqual(source["grain"], "service_tickets")
        self.assertFalse(eligible)
        self.assertEqual(len({s.order_id for s in sales}), 3)
        cache = {}
        for intent, expected in (("revenue", 80000), ("order_count", 2)):
            answer = answer_plan(cache, catalog, Plan(intent, day, day), GROSS_RULEBOOK)
            self.assertEqual((answer.type, answer.answer), ("number", expected), answer.reason)
        result = answer_plan(cache, catalog, Plan("top_products", day, day, top_k=1),
                             GROSS_RULEBOOK)
        self.assertEqual(result.type, "abstain")
        self.assertIn("Service tickets", result.reason)
        split = Split(self.data, (), GROSS_RULEBOOK, self.data / "calendar.csv")
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        question = Question("local", "shop", "Ngày 10/01/2026 có bao nhiêu phiếu dịch vụ?", day)
        count = agent.answer(question, BusinessContext(split, catalog))
        self.assertEqual((count.type, count.answer), ("number", 2), count.reason)

    def test_ambiguous_ticket_amount_or_status_fails_closed(self):
        rows = [["Ngày", "Giá"], ["10/01/2026", 40000]]
        for doc, error in ((TICKETS.replace("không gồm thuế", "đã tính thuế"), "AMOUNT_UNVERIFIED"),
                           (TICKETS.replace(" Chỉ ghi phiếu đã hoàn thành.", ""), "MISSING_VALUE"),
                           (TICKETS.replace("Mỗi dòng là một phiếu dịch vụ", "Sổ thanh toán"),
                            "NO_SALES_LIST")):
            catalog = self.shop(doc, {"register.csv": rows})
            with self.subTest(error=error), self.assertRaisesRegex(ReadmeSalesError, error):
                load_readme_sales(catalog, GROSS_RULEBOOK)

    def test_staff_name_must_map_uniquely_in_documented_pos_table(self):
        from datamate.semantics.planner import plan_question
        from datamate.semantics.rules import read_rules

        self.shop("Máy POS; `register.sqlite`: `nhan_vien`.", {})
        with sqlite3.connect(self.data / "shop" / "register.sqlite") as connection:
            connection.executescript("""
                CREATE TABLE orders (MaHoaDon, NgayLap, TinhTrang, TongTienHang, MaNhanVien);
                CREATE TABLE nhan_vien (MaNhanVien, TenGoi);
                INSERT INTO nhan_vien VALUES ('X42', 'Vũ Nhật Thu'), ('Z1', 'Tú Mai'), ('Z2', 'Tú Mai');
            """)
        catalog = build_catalog(self.data, "shop")
        self.assertEqual(sqlite_staff_codes(catalog), {"vu nhat thu": "X42"})
        staff = {"staff_names": ["vu nhat thu"], "staff_codes": ["X42"]}
        day = date(2026, 1, 31)
        for name, code in (("VŨ Nhật Thu", "X42"), ("Tu Mai", None)):
            question = Question("local", "shop", f"Doanh thu nhân viên {name} tháng 1/2026?", day)
            plan = plan_question(question, read_rules(GROSS_RULEBOOK), vocabulary=staff)
            self.assertEqual(plan.filters[0].value if plan else None, code)

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


class NameSpellingTests(_Shop):
    def test_spellings_of_one_name_are_one_product(self):
        readme = LEDGER.replace("hàng bán, có thể ghi tắt (bảng tra ở file tat.csv)",
                                "tên hàng").replace(
            "ghi chú của đơn; đơn huỷ hoặc trả lại được ghi ở đây", "trạng thái đơn")
        rows = [HEADER]
        for i, name in enumerate(["Trà xanh", "TRÀ XANH", "Trà  xanh", "Trà xanh", "Tra xanh",
                                  "Bánh mì", "Bánh mì"] * 4):
            rows.append([f"{1 + i % 28:02d}/01/2026", str(i), name, "2", "15000", "30000",
                         "Hoàn thành"])
        sales, evidence, _ = load_readme_sales(self.shop(readme, {"so.csv": rows}), RULEBOOK)
        self.assertEqual({s.product_id for s in sales}, {"Trà xanh", "Bánh mì"})
        self.assertEqual(evidence["name_spellings_merged"], 3)


if __name__ == "__main__":
    unittest.main()
