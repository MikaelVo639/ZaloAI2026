"""Rule specs: rulebook rows -> structured ranking descriptions, grounded in the row's words.

Every row here is a synthetic paraphrase written for these tests, not organizer text.
"""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from test_s01_grammar import rulebook

from datamate.agent.rule_rank import RankError, rank, sales_groups, supplier_groups
from datamate.contracts import Question
from datamate.ingestion.purchases import Receipt
from datamate.ingestion.sales import Sale
from datamate.semantics.rule_rank_plan import plan_rule_rank
from datamate.semantics.rule_spec import enabled_specs, ground, normalize_rulebook, parse_row
from datamate.semantics.rules import read_rules


def spec_of(name, definition):
    line = f"| {name} | {definition} |"
    return parse_row(name, definition, line)


# Synthetic rows: (name, definition) -> expected (đối tượng, chỉ tiêu, chọn, điều kiện).
EXPECTED = {
    ("Khách chịu chi nhất", "giá trị trung bình một đơn cao nhất, chỉ xét khách có từ 4 đơn"):
        ("khach", "gia_tri_tb_don", "lon_nhat", {"so_don_toi_thieu": 4}),
    ("Khách quen nhất", "khách có số ngày có mua nhiều nhất trong kỳ"):
        ("khach", "so_ngay_co_mua", "lon_nhat", {}),
    ("Khách hay huỷ nhất", "khách có số đơn huỷ nhiều nhất"):
        ("khach", "so_don_huy", "lon_nhat", {}),
    ("Món được gọi nhiều nhất", "mặt hàng có mặt trong nhiều đơn nhất, bỏ đơn huỷ và trả"):
        ("mat_hang", "so_don", "lon_nhat", {}),
    ("Nhân viên siêng nhất", "nhân viên có số ngày có đặt đơn nhiều nhất"):
        ("nhan_vien", "so_ngay_co_mua", "lon_nhat", {}),
    ("Ngày bán đắt nhất", "ngày có doanh thu cao nhất trong kỳ"):
        ("ngay", "doanh_thu", "lon_nhat", {}),
    ("Nhà cung cấp lớn nhất", "nhà cung cấp có nhiều phiếu nhập nhất, bỏ phiếu huỷ"):
        ("nha_cung_cap", "so_phieu_nhap", "lon_nhat", {}),
    ("Nhân viên bán giỏi nhất", "theo **doanh thu** (§1) của nhân viên trong kỳ, không theo "
                                "số đơn"):
        ("nhan_vien", "doanh_thu", "lon_nhat", {}),
    # The subject comes from the definition; the name only arbitrates.
    ("Ngày đông khách nhất", "ngày có nhiều đơn nhất"): ("ngay", "so_don", "lon_nhat", {}),
    ("Ngày bán ít nhất", "ngày có ít đơn nhất, kể cả ngày không có đơn"):
        ("ngay", "so_don", "nho_nhat", {"tinh_ngay_khong_don": True}),
    ("Thợ siêng nhất", "người có nhiều ngày làm việc nhất"):
        ("nhan_vien", "so_ngay_co_mua", "lon_nhat", {}),
    ("Khách đều đặn nhất", "khách có nhiều ngày mua khác nhau nhất"):
        ("khach", "so_ngay_co_mua", "lon_nhat", {}),
    ("Khách hay huỷ đơn nhất", "khách có nhiều đơn huỷ nhất"):
        ("khach", "so_don_huy", "lon_nhat", {}),
    # Meaning words in the name ("đổi") need no field; only the definition is checked.
    ("Khách hay đổi món nhất", "khách có nhiều đơn huỷ nhất"):
        ("khach", "so_don_huy", "lon_nhat", {}),
}


class ParseRowTests(unittest.TestCase):
    def test_synthetic_rows_match_the_hand_written_specs(self):
        for (name, definition), expected in EXPECTED.items():
            with self.subTest(row=name):
                result = spec_of(name, definition)
                self.assertIsNotNone(result.spec, result.reasons)
                spec = result.spec
                self.assertEqual((spec.doi_tuong, spec.chi_tieu, spec.chon, spec.dieu_kien),
                                 expected)
                self.assertEqual(spec.nguon, f"| {name} | {definition} |")

    def test_rows_that_are_not_named_rankings_are_ignored(self):
        for name in ("Tháng này", "N ngày gần nhất", "Doanh thu một đơn", "Đơn bị huỷ"):
            with self.subTest(row=name):
                self.assertIsNone(spec_of(name, "định nghĩa bất kỳ"))

    def test_unknown_measure_or_number_turns_the_rule_off(self):
        for name, definition in (
                ("Hàng lời nhất", "theo tổng lãi gộp của mặt hàng"),
                ("Khách tốt nhất", "khách có doanh thu cao nhất trong 30 ngày"),
                ("Ngày bán ít nhất", "ngày có ít đơn nhất")):  # empty days not stated
            with self.subTest(row=name):
                result = spec_of(name, definition)
                self.assertIsNone(result.spec)
                self.assertTrue(result.reasons)

    def test_every_meaning_word_must_be_used(self):
        for name, definition, word in (
                ("Khách hay trả hàng nhất", "khách có nhiều đơn trả nhất", "tra"),
                ("Khách mua lãi nhất", "khách có doanh thu cao nhất tính theo lãi", "lai"),
                ("Mặt hàng hot nhất", "mặt hàng có nhiều đơn nhất, kể cả hàng bảo hành",
                 "bao hanh"),
                ("Ngày đông nhất", "ngày có nhiều lượt khách nhất", "luot")):
            with self.subTest(row=name):
                result = spec_of(name, definition)
                self.assertIsNone(result.spec)
                self.assertTrue(any(word in reason for reason in result.reasons), result.reasons)

    def test_name_and_definition_must_agree_on_the_subject(self):
        for name, definition in (("Mặt hàng đắt khách nhất", "ngày có nhiều đơn nhất"),
                                 ("Người mua nhiều nhất", "người có doanh thu cao nhất"),
                                 ("Khách, ngày đông nhất", "có nhiều đơn nhất")):
            with self.subTest(row=name):
                self.assertIsNone(spec_of(name, definition).spec)

    def test_rows_read_by_read_rules_are_left_to_it(self):
        row = ("| Nhân viên bán giỏi nhất | theo **doanh thu** (§1) của nhân viên trong kỳ, "
               "không theo số đơn |")
        self.assertTrue(read_rules(rulebook(extra=row)).best_staff)
        self.assertEqual(normalize_rulebook(rulebook(extra=row)), [])

    def test_the_condition_is_not_read_as_the_choice(self):
        result = spec_of("Khách ruột nhất", "doanh thu cao nhất, chỉ xét khách mua ít nhất 2 đơn")
        self.assertEqual((result.spec.chon, result.spec.dieu_kien),
                         ("lon_nhat", {"so_don_toi_thieu": 2}))

    def test_same_meaning_phrases_become_question_names(self):
        result = spec_of("Khách chịu chi nhất",
                         "giá trị trung bình một đơn cao nhất. **Khách vip nhất** cùng nghĩa với "
                         "khách chịu chi nhất")
        self.assertIn("khach vip nhat", result.spec.cach_goi)
        self.assertIn("khach chiu chi nhat", result.spec.cach_goi)


class GroundTests(unittest.TestCase):
    def setUp(self):
        self.spec = spec_of(*next(iter(EXPECTED))).spec

    def test_a_valid_spec_has_no_problem(self):
        self.assertEqual(ground(self.spec), [])

    def test_values_without_support_in_the_row_are_rejected(self):
        for change, needle in (({"dieu_kien": {"so_don_toi_thieu": 5}}, "so_don_toi_thieu"),
                               ({"chi_tieu": "so_ngay_co_mua"}, "so_ngay_co_mua"),
                               ({"doi_tuong": "nha_cung_cap"}, "nha_cung_cap"),
                               ({"chon": "khac"}, "outside the vocabulary")):
            with self.subTest(change=change):
                problems = ground(replace(self.spec, **change))
                self.assertTrue(any(needle in p for p in problems), problems)


def sale(order, day, customer, amount, status="valid", product="P1", staff="NV1"):
    return Sale(order, date(2026, 3, day), status, Decimal(amount), product, product,
                Decimal(1), customer_id=None, customer_name=customer, staff_id=staff)


SALES = [
    sale("A1", 2, "Lan", 100), sale("A2", 3, "Lan", 100), sale("A3", 4, "Lan", 100),
    sale("A4", 5, "Lan", 100),                       # Lan: 4 orders, average 100
    sale("B1", 2, "Minh", 500),                      # Minh: 1 order, average 500
    sale("C1", 6, "Hoa", 300), sale("C2", 6, "Hoa", 300), sale("C3", 7, "Hoa", 300),
    sale("C4", 8, "Hoa", 300, "cancelled"),          # Hoa: 3 valid orders, average 300
]


class RankTests(unittest.TestCase):
    def test_minimum_orders_filters_before_ranking(self):
        spec = spec_of(*next(iter(EXPECTED))).spec  # trung bình một đơn, từ 4 đơn
        names, evidence = rank(spec, sales_groups(spec, SALES, date(2026, 3, 1),
                                                  date(2026, 3, 31)), 1, True)
        self.assertEqual(names, ["Lan"])
        loose = replace(spec, dieu_kien={})
        names, _ = rank(loose, sales_groups(loose, SALES, date(2026, 3, 1),
                                            date(2026, 3, 31)), 1, True)
        self.assertEqual(names, ["Minh"])

    def test_cancelled_orders_and_days(self):
        spec = spec_of("Khách hay huỷ nhất", "khách có số đơn huỷ nhiều nhất").spec
        names, _ = rank(spec, sales_groups(spec, SALES, date(2026, 3, 1), date(2026, 3, 31)),
                        1, True)
        self.assertEqual(names, ["Hoa"])
        spec = spec_of("Khách quen nhất", "khách có số ngày có mua nhiều nhất trong kỳ").spec
        names, _ = rank(spec, sales_groups(spec, SALES, date(2026, 3, 1), date(2026, 3, 31)),
                        2, True)
        self.assertEqual(names, ["Lan", "Hoa"])

    def test_tie_without_tie_rule_abstains(self):
        spec = spec_of("Khách quen nhất", "khách có số ngày có mua nhiều nhất trong kỳ").spec
        tied = [sale("X", 2, "An", 1), sale("Y", 2, "Bao", 1)]
        groups = sales_groups(spec, tied, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(rank(spec, groups, 1, True)[0], ["An"])
        with self.assertRaises(RankError):
            rank(spec, groups, 1, False)

    def test_supplier_receipts(self):
        spec = spec_of("Nhà cung cấp lớn nhất", "nhà cung cấp có nhiều phiếu nhập nhất, "
                       "bỏ phiếu huỷ").spec
        receipts = (Receipt("P1", date(2026, 3, 1), Decimal(1), "NCC A", False),
                    Receipt("P2", date(2026, 3, 2), Decimal(1), "NCC B", False),
                    Receipt("P3", date(2026, 3, 3), Decimal(1), "NCC B", True),
                    Receipt("P4", date(2026, 3, 4), Decimal(1), "NCC A", False))
        names, _ = rank(spec, supplier_groups(spec, receipts, date(2026, 3, 1),
                                              date(2026, 3, 31)), 1, True)
        self.assertEqual(names, ["NCC A"])


class PlanTests(unittest.TestCase):
    ROW = "| Khách chịu chi nhất | giá trị trung bình một đơn cao nhất, chỉ xét khách có từ 4 đơn |"

    def plan(self, text, extra=ROW):
        book = rulebook(extra=extra)
        return plan_rule_rank(Question("q", "b", text, date(2026, 7, 15)), read_rules(book),
                              enabled_specs(book))

    def test_a_question_naming_one_rule_and_one_period(self):
        plan = self.plan("Tháng 3/2026 khách chịu chi nhất là ai vậy?")
        self.assertEqual((plan.intent, plan.start, plan.end, plan.top_k),
                         ("rule_rank", date(2026, 3, 1), date(2026, 3, 31), 1))
        self.assertIn(self.ROW, plan.evidence)
        self.assertEqual(self.plan("top 3 khach chiu chi nhat thang 3/2026").top_k, 3)

    def test_extra_conditions_or_unknown_rules_stay_unplanned(self):
        for text in ("Tháng 3/2026 khách chịu chi nhất mua món nào?",
                     "Tháng 3/2026 khách chịu chi nhất ở chi nhánh 2 là ai?",
                     "Khách chịu chi nhất là ai?",                        # no period
                     "Tháng 3/2026 khách sành điệu nhất là ai?"):         # no such rule
            with self.subTest(text=text):
                self.assertIsNone(self.plan(text))
        self.assertIsNone(self.plan("Tháng 3/2026 khách chịu chi nhất là ai?", extra=""))

    def test_report_lists_enabled_and_disabled_rules(self):
        rows = normalize_rulebook(rulebook(extra=self.ROW + "\n| Hàng lời nhất | theo lãi gộp |"))
        self.assertEqual([r.spec is not None for r in rows], [True, False])


if __name__ == "__main__":
    unittest.main()


class ModelSpecTests(unittest.TestCase):
    """The model path with a fake generator: agreement and grounding decide."""

    ROW = "| Khách mua đều nhất | khách ghé quán vào nhiều ngày nhất |"

    def run_model(self, outputs):
        from datamate.agent.rule_spec_model import model_specs
        replies = iter(outputs)
        return model_specs(rulebook(extra=self.ROW), lambda messages: next(replies))

    def answer(self, **values):
        import json
        data = {"ten": "khach_mua_deu_nhat", "cach_goi": ["khach mua deu nhat"],
                "doi_tuong": "khach", "chi_tieu": "so_ngay_co_mua", "chon": "lon_nhat",
                "don_hop_le": "luat_chung", "dieu_kien": {}, "mau_so": "",
                "kieu_dap_an": "string", "nguon": "ignored"}
        return json.dumps({**data, **values}, ensure_ascii=False)

    def test_names_come_from_the_row_not_from_the_model(self):
        # The real model wrote the name with accents and an empty ``ten``; an invented name
        # must never become a trigger either.
        for ten, names in (("", ["khách mua đều nhất"]), ("x", ["khach vip"])):
            results, audit = self.run_model([self.answer(ten=ten, cach_goi=names)] * 2)
            self.assertEqual(audit[0]["decision"], "on")
            self.assertEqual(results[0].spec.ten, "khach_mua_deu_nhat")
            self.assertEqual(results[0].spec.cach_goi, ("khach mua deu nhat",))

    def test_deterministic_reader_leaves_the_row_off(self):
        rows = normalize_rulebook(rulebook(extra=self.ROW))
        self.assertIsNone(rows[0].spec)

    def test_two_identical_grounded_outputs_turn_the_rule_on(self):
        results, audit = self.run_model([self.answer(), self.answer()])
        self.assertEqual(results[0].spec.chi_tieu, "so_ngay_co_mua")
        self.assertEqual(results[0].spec.nguon, self.ROW)
        self.assertEqual(audit[0]["decision"], "on")

    def test_disagreement_or_ungrounded_values_keep_it_off(self):
        for outputs in ([self.answer(), self.answer(chon="nho_nhat")],
                        [self.answer(dieu_kien={"so_don_toi_thieu": 3})] * 2,
                        [self.answer(chi_tieu="doanh_thu")] * 2,
                        ["KHONG_RO", "KHONG_RO"], [None, None]):
            with self.subTest(outputs=outputs):
                results, audit = self.run_model(outputs)
                self.assertIsNone(results[0].spec)
                self.assertTrue(audit[0]["decision"].startswith("off"))


class AgentRouteTests(unittest.TestCase):
    """Question -> rule plan -> worker -> executor -> validation, on a synthetic shop."""

    ROWS = ("| Ngày bán đắt nhất | ngày có doanh thu cao nhất trong kỳ |\n"
            "| Món được gọi nhiều nhất | mặt hàng có mặt trong nhiều đơn nhất, bỏ đơn huỷ và trả |")

    def setUp(self):
        import csv
        import shutil
        import tempfile
        from pathlib import Path

        from datamate.demo import COLUMNS
        from datamate.ingestion.catalog import build_catalog

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "shop").mkdir()
        (root / "shop" / "README.md").write_text("", encoding="utf-8")
        with (root / "shop" / "sales.csv").open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([
                COLUMNS,
                ("O1", "2026-03-02", "completed", "P1", "Trà", 1, "100"),
                ("O1", "2026-03-02", "completed", "P2", "Bánh", 1, "50"),
                ("O2", "2026-03-03", "completed", "P1", "Trà", 1, "100"),
                ("O3", "2026-03-04", "completed", "P2", "Bánh", 5, "500"),
                ("O4", "2026-03-04", "cancelled", "P2", "Bánh", 1, "50"),
            ])
        self.catalog = build_catalog(root, "shop")
        self.root = root

    def answer(self, text, extra=None):
        from datamate.agent.baseline import BaselineAgent
        from datamate.contracts import BusinessContext, Split

        agent = BaselineAgent()
        self.addCleanup(agent.close)
        book = rulebook(extra=self.ROWS if extra is None else extra)
        split = Split(self.root, (), book, self.root / "calendar.csv")
        question = Question("q", "shop", text, date(2026, 3, 31))
        return agent.answer(question, BusinessContext(split, self.catalog))

    def test_named_rules_answer_through_the_agent(self):
        day = self.answer("Tháng 3/2026 ngày bán đắt nhất là ngày nào?")
        self.assertEqual((day.type, day.answer), ("date", "2026-03-04"), day.reason)
        dish = self.answer("Tháng 3/2026 món được gọi nhiều nhất là món gì?")
        self.assertEqual((dish.type, dish.answer), ("string", "Trà"), dish.reason)

    def test_without_the_row_the_question_abstains(self):
        self.assertEqual(self.answer("Tháng 3/2026 ngày bán đắt nhất là ngày nào?",
                                     extra="").type, "abstain")
