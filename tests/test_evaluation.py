import unittest

from datamate.evaluation.scorer import evaluate, matches
from datamate.evaluation.validation import validate_record, validate_submission


def record(kind, answer=None, **extra):
    return {"id": "q", "type": kind, "answer": answer, **extra}


class EvaluationTests(unittest.TestCase):
    def test_pa_formula_including_missing_and_unanswerable(self):
        refs = [{"id": "a", "type": "number", "answer": 1},
                {"id": "b", "type": "number", "answer": 2},
                {"id": "c", "type": "unanswerable", "answer": None},
                {"id": "d", "type": "number", "answer": 4}]
        predictions = [{"id": "a", "type": "number", "answer": 1},
                       {"id": "b", "type": "number", "answer": 9},
                       {"id": "c", "type": "unanswerable"}]
        report = evaluate(predictions, refs)
        self.assertEqual((report["correct"], report["wrong"], report["abstain"]), (2, 1, 1))
        self.assertEqual(report["pa"], 0.25)

    def test_string_normalization_preserves_accents_and_accepts_alias(self):
        expected = record("string", "Cà phê sữa", alias=["SP001"])
        self.assertTrue(matches(record("string", "  CÀ-PHÊ   SỮA  "), expected))
        self.assertTrue(matches(record("string", "sp001"), expected))
        self.assertFalse(matches(record("string", "Ca phe sua"), expected))

    def test_list_order_and_per_position_aliases(self):
        expected = record("list", ["Cà phê", "Trà"], alias=[["P1"], ["P2"]])
        self.assertTrue(matches(record("list", ["p1", "p2"]), expected))
        self.assertFalse(matches(record("list", ["P2", "P1"]), expected))

    def test_table_column_alignment_and_row_order(self):
        expected = record("table", {"columns": ["Khách hàng", "Nợ"],
                                    "rows": [["Lan", 20], ["Tú", 10]]})
        actual = record("table", {"columns": ["Nợ", "Khách hàng"],
                                  "rows": [[10, "Tú"], [20, "LAN"]]})
        self.assertTrue(matches(actual, expected))

    def test_table_first_cell_aliases_per_reference_row(self):
        expected = record("table", {"columns": ["khach_hang", "so_tien_no"],
                                    "rows": [["Lưu Phong Bình", 98000], ["Tú", 10]]},
                          alias=[["KH0178", "Lưu Phong Bình - 0978 611 903"], []])
        for name in ("Lưu Phong Bình - 0978 611 903", "kh0178", "Lưu Phong Bình"):
            with self.subTest(name=name):
                actual = record("table", {"columns": ["khach_hang", "so_tien_no"],
                                          "rows": [["Tú", 10], [name, 98000]]})
                self.assertTrue(matches(actual, expected))
        wrong_amount = record("table", {"columns": ["khach_hang", "so_tien_no"],
                                        "rows": [["KH0178", 98001], ["Tú", 10]]})
        swapped = record("table", {"columns": ["khach_hang", "so_tien_no"],
                                   "rows": [["Tú", 98000], ["KH0178", 10]]})
        duplicate = record("table", {"columns": ["khach_hang", "so_tien_no"],
                                     "rows": [["KH0178", 98000], ["Lưu Phong Bình", 98000]]})
        unaccented = record("table", {"columns": ["khach_hang", "so_tien_no"],
                                      "rows": [["Luu Phong Binh", 98000], ["Tú", 10]]})
        for actual in (wrong_amount, swapped, duplicate, unaccented):
            with self.subTest(actual=actual["answer"]["rows"]):
                self.assertFalse(matches(actual, expected))

    def test_percent_rounding_and_exact_currency(self):
        self.assertTrue(matches(record("percent", -12.451), record("percent", -12.45)))
        self.assertFalse(matches(record("number", 10**12 + 1), record("number", 10**12)))
        self.assertFalse(matches(record("number", 1), record("boolean", True)))

    def test_validation_rejects_wrong_shape_boolean_numbers_and_nonfinite(self):
        invalid = [record("number", True), record("number", "12"), record("number", float("nan")),
                   record("date", "2026-02-30"), record("boolean", 1),
                   record("unanswerable", 5), record("abstain", reason=42),
                   record("table", {"columns": ["x", "y"], "rows": [[1]]})]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_record(item)
        validate_record(record("date", "2026-02"))

    def test_duplicate_unknown_and_missing_ids_rejected(self):
        for rows, expected in (([record("abstain"), record("abstain")], {"q"}),
                               ([record("abstain")], {"different"}), ([], {"q"})):
            with self.assertRaises(ValueError):
                validate_submission(rows, expected)
