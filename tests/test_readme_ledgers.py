"""Owner-kept ledgers: column meanings with explanations, states written in notes, short
product names, conventions that name the order column, total and note rows."""

import sqlite3
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from datamate.agent.baseline import BaselineAgent
from datamate.agent.compute import answer_plan
from datamate.contracts import BusinessContext, Filter, Plan, Question, Split
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    convention_columns,
    load_readme_sales,
    meaning_field,
    note_state,
    parse_day,
    sqlite_staff_codes,
    staff_codes,
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
# A register that names no price adjustment and no ticket state anywhere.
PLAIN_TICKETS = """## File
- `register.csv` (csv)

## Cột trong `register.csv`
- **Ngày**: ngày làm
- **Người làm**: người làm phiếu
- **Tiền**: tổng tiền cả phiếu
- **Ghi chú**: ghi chú

## Quy ước
- Mỗi dòng là một phiếu sửa.
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
        for written in ("Thứ 2, 03/08", "T2 3/8", "CN - 3/8", "3/8 (T2)", "ngày 3", "Ngày 03",
                        "3/8/26", "03-08-26", "thứ hai 3/8"):
            with self.subTest(written=written):
                self.assertEqual(parse_day(written, "T8-2026"), date(2026, 8, 3))
        for wrong in ("3/8/25", "T9 3/8", "tháng 3"):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                parse_day(wrong, "T8-2026")


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
        sales, _, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual({s.status for s in sales if s.order_id == "0"}, {"state_ambiguous"})

    def test_only_sales_answers_affected_by_an_ambiguous_order_are_withheld(self):
        readme = LEDGER.replace("huỷ hoặc trả lại", "huỷ hoặc đang giao")
        rows = ledger(("",))
        rows[1][-1] = "Chưa giao"
        rows[2][2] = "TX"
        catalog = self.shop(readme, {"so.csv": rows, "tat.csv": SHORT})
        early, later = date(2026, 1, 1), date(2026, 1, 2)
        cache = {}
        for plan, want in (
            (Plan("revenue", early, early), None),
            (Plan("order_count", early, early), None),
            (Plan("cancel_count", early, early), None),
            (Plan("revenue", later, later), 40000),
            (Plan("revenue", early, later, filters=(Filter("product", "Bánh mì"),)), 10000),
            (Plan("revenue", early, later, filters=(Filter("product", "Trà xanh"),)), None),
        ):
            with self.subTest(plan=plan):
                candidate = answer_plan(cache, catalog, plan, RULEBOOK)
                self.assertEqual(candidate.type, "number" if want is not None else "abstain")
                self.assertEqual(candidate.answer, want)
                if want is None:
                    self.assertTrue(candidate.evidence["state_ambiguous"])

    def test_service_ledger_ignores_extra_descriptions_and_reads_every_month(self):
        readme = TICKETS.replace("register.csv", "register.xlsx").replace("(csv)", "(xlsx)")
        readme = readme.replace("## Quy ước", "- **Hẹn**: ngày giao dự kiến\n"
                               "- **Việc**: mô tả tự do\n\n## Quy ước")
        def records(day, amount):
            return [[], ["Sổ thử", "tiêu đề"], ["Ngày", "Giá", "Hẹn", "Việc"],
                    [day, amount, "gọi sau", "kiểm tra"]]
        sheets = {"T01-2026": records("10/01/2026", 40000),
                  "T02-2026": records("11/02/2026", 33000)}
        catalog = self.shop(readme, {"register.xlsx": sheets})
        sales, _, _ = load_readme_sales(catalog, GROSS_RULEBOOK)
        self.assertEqual(sum(s.amount for s in sales), 73000)
        sheets["T02-2026"][2][1] = "Cột tiền lạ"
        catalog = self.shop(readme, {"register.xlsx": sheets})
        with self.assertRaisesRegex(ReadmeSalesError, "HEADER_NOT_FOUND"):
            load_readme_sales(catalog, GROSS_RULEBOOK)

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

    def test_plain_ticket_register_reads_totals_and_withholds_counts_of_free_tickets(self):
        rows = [["Ngày", "Người làm", "Tiền", "Ghi chú"], ["10/01/2026", "Ba", 40000, ""],
                ["10/01/2026", "Tư", 60000, ""], ["11/01/2026", "Ba", 50000, ""]]
        day = date(2026, 1, 10)
        catalog = self.shop(PLAIN_TICKETS, {"register.csv": rows})
        sales, source, _ = load_readme_sales(catalog, GROSS_RULEBOOK)
        self.assertEqual(source["grain"], "service_tickets")
        self.assertEqual({s.staff_id for s in sales}, {"Ba", "Tư"})
        for intent, expected in (("revenue", 100000), ("order_count", 2)):
            answer = answer_plan({}, catalog, Plan(intent, day, day), GROSS_RULEBOOK)
            self.assertEqual((answer.type, answer.answer), ("number", expected), answer.reason)
        # A zero ticket with a note (a free return visit) may or may not be a ticket. A note
        # written across the date column without an amount is not a ticket.
        free = rows + [["10/01/2026", "Tư", 0, "Quay lại làm lại"],
                       ["Lưu ý: tháng này nghỉ Tết", "", "", ""]]
        catalog = self.shop(PLAIN_TICKETS, {"register.csv": free})
        cache = {}
        revenue = answer_plan(cache, catalog, Plan("revenue", day, day), GROSS_RULEBOOK)
        self.assertEqual((revenue.type, revenue.answer), ("number", 100000), revenue.reason)
        count = answer_plan(cache, catalog, Plan("order_count", day, day), GROSS_RULEBOOK)
        self.assertEqual(count.type, "abstain")
        self.assertIn("ambiguous status", count.reason)
        # Rankings by revenue are not changed by a zero ticket either.
        month = (date(2026, 1, 1), date(2026, 1, 31))
        best = answer_plan(cache, catalog, Plan("best_day", *month), GROSS_RULEBOOK)
        self.assertEqual((best.type, best.answer), ("date", "2026-01-10"), best.reason)
        top = answer_plan(cache, catalog, Plan("top_staff", *month), GROSS_RULEBOOK)
        self.assertEqual((top.type, top.answer), ("string", "Ba"), top.reason)

    def test_worker_spellings_join_the_worker_list_for_commission_and_rankings(self):
        readme = (PLAIN_TICKETS.replace("- `register.csv` (csv)\n",
                                        "- `register.csv` (csv)\n- `tho.csv` (csv)\n")
                  .replace("- **Người làm**: người làm phiếu", "- **Thợ**: thợ làm phiếu, ghi tên gọi")
                  .replace("## Quy ước", "## Cột trong `tho.csv`\n- **Họ tên**: họ tên thợ\n"
                           "- **Hoa hồng (%)**: phần trăm hoa hồng của thợ, tính trên tổng Tiền "
                           "các phiếu thợ làm trong tháng\n\n## Quy ước"))
        rows = [["Ngày", "Thợ", "Tiền", "Ghi chú"], ["03/01/2026", "a Bình", 300000, ""],
                ["04/01/2026", "bình", 200000, ""], ["05/01/2026", "Chú Ba", 400000, ""],
                ["05/01/2026", "Ba", 0, "Bảo hành phiếu trước"]]
        workers = [["Họ tên", "Hoa hồng (%)"], ["Trần Văn Bình", 10], ["Lê Thị Ba", "15%"]]
        catalog = self.shop(readme, {"register.csv": rows, "tho.csv": workers})
        split = Split(self.data, (), GROSS_RULEBOOK, self.data / "calendar.csv")
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        context = BusinessContext(split, catalog)
        day = date(2026, 1, 31)
        for text, expected in (
                ("Hoa hồng của thợ Bình tháng 1/2026 là bao nhiêu?", ("number", 50000)),
                ("Tháng 1/2026 thợ Lê Thị Ba được bao nhiêu tiền hoa hồng?", ("number", 60000)),
                ("Thợ nào được hoa hồng nhiều nhất tháng 1/2026?", ("string", "Lê Thị Ba")),
                ("Thợ nào làm được nhiều tiền nhất tháng 1/2026?", ("string", "Trần Văn Bình")),
                ("Tháng 1/2026 thợ Bình làm được bao nhiêu tiền?", ("number", 500000)),
                ("Doanh thu sửa xe tháng 1/2026 là bao nhiêu?", ("number", 900000)),
                ("Tháng 1/2026 có bao nhiêu lượt xe vào sửa?", ("abstain", None))):
            with self.subTest(text=text):
                answer = agent.answer(Question("q", "shop", text, day), context)
                self.assertEqual((answer.type, answer.answer), expected, answer.reason)
        invalid = answer_plan({}, catalog, Plan("staff_commission", day, day), GROSS_RULEBOOK)
        self.assertEqual(invalid.type, "abstain")
        outside = date(2025, 12, 31)
        uncovered = answer_plan(
            {}, catalog, Plan("staff_commission", outside, outside,
                              filters=(Filter("staff", "Trần Văn Bình", ""),)), GROSS_RULEBOOK,
        )
        self.assertEqual(uncovered.type, "abstain")
        # A spelling that names no listed worker leaves every worker question unanswered.
        catalog = self.shop(readme, {"register.csv": rows + [["06/01/2026", "Tư", 100000, ""]],
                                     "tho.csv": workers}, business="other")
        answer = agent.answer(Question("q", "other", "Hoa hồng của thợ Bình tháng 1/2026 là "
                                       "bao nhiêu?", day), BusinessContext(split, catalog))
        self.assertEqual(answer.type, "abstain")

    def test_plain_ticket_register_fails_closed_when_the_readme_names_more(self):
        rows = [["Ngày", "Người làm", "Tiền", "Ghi chú"], ["10/01/2026", "Ba", 40000, ""]]
        for doc, error in (
                (PLAIN_TICKETS + "- Khách quen được giảm giá 10%.\n", "AMOUNT_UNVERIFIED"),
                (PLAIN_TICKETS + "- Phiếu khách huỷ vẫn ghi.\n", "MISSING_VALUE"),
                (PLAIN_TICKETS.replace("tổng tiền cả phiếu", "tiền công"), "NO_SALES_LIST|AMOUNT"),
                (PLAIN_TICKETS.replace("ngày làm", "ngày làm, mỗi dòng một phiếu")
                 .replace("## Cột trong `register.csv`", "## Cột trong `register.csv`\n"
                          "- **Trạng thái**: trạng thái phiếu"), "MISSING_VALUE|UNKNOWN|STATUS")):
            catalog = self.shop(doc, {"register.csv": rows})
            with self.subTest(doc=doc[-60:]), self.assertRaisesRegex(ReadmeSalesError, error):
                load_readme_sales(catalog, GROSS_RULEBOOK)

    def test_catalogue_without_codes_gives_groups_by_name_and_gaps_withhold_group_answers(self):
        readme = LEDGER.replace("- `tat.csv` (csv)\n", "- `tat.csv` (csv)\n- `hang.csv` (csv)\n")
        readme = readme.replace("## Quy ước", "## Cột trong `hang.csv`\n- **Tên hàng**: tên hàng\n"
                                "- **Nhóm**: nhóm hàng\n\n## Quy ước")
        groups = [["Tên hàng", "Nhóm"], ["Trà xanh", "Đồ uống"], ["Bánh mì", "Đồ ăn"]]
        short = [row[:2] for row in SHORT]  # the short-name list gives no group here
        catalog = self.shop(readme, {"so.csv": ledger(notes=("",)), "tat.csv": short,
                                     "hang.csv": groups})
        sales, _, _ = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual({s.product_group for s in sales}, {"Đồ uống", "Đồ ăn"})
        month = (date(2026, 1, 1), date(2026, 1, 31))
        plan = Plan("revenue", *month, filters=(Filter("product_group", "Đồ ăn", ""),))
        answer = answer_plan({}, catalog, plan, RULEBOOK)
        self.assertEqual((answer.type, answer.answer), ("number", 24 * 10000), answer.reason)
        # A sold product missing from the catalogue: its group is unknown, so no group total.
        catalog = self.shop(readme, {"so.csv": ledger(notes=("",)), "tat.csv": short,
                                     "hang.csv": groups[:2]}, business="gap")
        answer = answer_plan({}, catalog, plan, RULEBOOK)
        self.assertEqual(answer.type, "abstain")
        self.assertIn("group is unknown", answer.reason)

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

    def test_documented_staff_catalogue_rejects_duplicates_and_entity_collisions(self):
        readme = LEDGER + ("\n## Cột trong `team.csv`\n"
                          "- **Số sổ**: mã nhân viên\n- **Thẻ**: tên nhân viên\n")
        rows = [["Số sổ", "Thẻ"], ["A", "Vũ Bích"], ["B", "Trà xanh"],
                ["C", "Hà Yến"], ["D", "Ha Yen"], ["E", "An Vân"], ["E", "An Lan"]]
        catalog = self.shop(readme, {"team.csv": rows, "so.csv": ledger(), "tat.csv": SHORT})
        self.assertEqual(staff_codes(catalog, reserved_names=["Trà xanh"]), {"vu bich": "A"})

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

    def test_header_catalogue_supplies_groups_without_replacing_sales_identity(self):
        readme = ("## File\n- `so_ban_hang.csv` (csv)\n"
                  "## Quy ước\n- Dữ liệu có đến hết 31/01/2026.\n"
                  "- **Đơn**: các dòng cùng **Số** là một đơn.\n"
                  "- **Đơn trả lại**: **Note** của đơn ghi khách trả lại.\n")
        files = {"so_ban_hang.csv": ledger(notes=("",), name="Trà xanh")}
        base, _, eligible = load_readme_sales(self.shop(readme, files), RULEBOOK)
        groups = [["Mã hàng", "Tên hàng", "Nhóm hàng"], ["SP01", "Trà xanh", "Đồ uống"],
                  ["SP02", "Bánh mì", "Đồ ăn"]]
        catalog = self.shop(readme, {**files, "danh_muc.csv": groups}, business="with_groups")
        sales, evidence, enriched_eligible = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual({s.product_group for s in sales}, {"Đồ uống", "Đồ ăn"})
        self.assertEqual([replace(s, product_group=None) for s in sales], base)
        self.assertEqual(enriched_eligible, eligible)
        self.assertEqual(evidence["product_groups_from_headers"]["matched_rows"], len(base))
        period = date(2026, 1, 1), date(2026, 1, 31)
        plan = Plan("revenue", *period, filters=(Filter("product_group", "Đồ ăn", ""),))
        answer = answer_plan({}, catalog, plan, RULEBOOK)
        self.assertEqual((answer.type, answer.answer), ("number", 24 * 10000), answer.reason)

        duplicate = [*groups, ["SP01", "Trà sữa", "Đồ uống"]]
        catalog = self.shop(readme, {**files, "danh_muc.csv": duplicate}, business="duplicate_code")
        sales, evidence, enriched_eligible = load_readme_sales(catalog, RULEBOOK)
        self.assertEqual(sales, base)
        self.assertEqual(enriched_eligible, eligible)
        self.assertNotIn("product_groups_from_headers", evidence)


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
