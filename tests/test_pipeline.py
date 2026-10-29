import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datamate.contracts import Candidate
from datamate.demo import create_demo
from datamate.evaluation.scorer import evaluate
from datamate.evaluation.validation import validate_submission
from datamate.io import read_jsonl
from datamate.pipeline import Config, predict


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "input"
        self.output = self.root / "output"
        create_demo(self.data)

    def test_end_to_end_three_formats_and_logs(self):
        report = predict(self.data, self.output, Config())
        records = read_jsonl(self.output / "submission.jsonl")
        expected = read_jsonl(self.data / "answers.jsonl")
        validate_submission(records, {r["id"] for r in expected})
        score = evaluate(records, expected)
        self.assertEqual((score["correct"], score["wrong"], score["abstain"]), (21, 0, 3))
        self.assertEqual(score["pa"], 0.875)
        self.assertEqual(report["questions"], 24)
        logs = list((self.output / "logs").glob("*.json"))
        self.assertEqual(len(logs), 24)
        for path in logs:
            log = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(log["id"], log["final_answer"]["id"])
            self.assertGreaterEqual(log["elapsed_seconds"], 0)

    def test_inference_never_reads_reference_answers_and_is_deterministic(self):
        original = Path.open

        def guarded(path, *args, **kwargs):
            if path.name == "answers.jsonl":
                raise AssertionError("Reference leakage in inference")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded):
            predict(self.data, self.output, Config())
            predict(self.data, self.root / "rerun", Config())
        self.assertEqual((self.output / "submission.jsonl").read_bytes(),
                         (self.root / "rerun" / "submission.jsonl").read_bytes())

    def test_question_exception_does_not_stop_later_questions(self):
        class FailingAgent:
            def answer(self, question, context):
                if question.id.endswith("_1"):
                    raise RuntimeError("deliberate execution failure")
                return Candidate("number", 7, 1.0)

        predict(self.data, self.output, Config(), agent=FailingAgent())
        records = read_jsonl(self.output / "submission.jsonl")
        self.assertEqual(len(records), 24)
        self.assertEqual(sum(r["type"] == "abstain" for r in records), 3)
        self.assertEqual(sum(r.get("answer") == 7 for r in records), 21)

    def test_changed_split_rules_disable_assumed_revenue_definition(self):
        rulebook = self.data / "rulebook.md"
        rulebook.write_text("Doanh thu bao gồm cả đơn huỷ và trả hàng.", encoding="utf-8")
        predict(self.data, self.output, Config())
        self.assertTrue(all(r["type"] == "abstain"
                            for r in read_jsonl(self.output / "submission.jsonl")))

    def test_duplicate_question_ids_fail_before_submission_creation(self):
        path = self.data / "questions.jsonl"
        first = path.read_text(encoding="utf-8").splitlines()[0]
        with path.open("a", encoding="utf-8") as stream:
            stream.write(first + "\n")
        with self.assertRaisesRegex(ValueError, "Duplicate id"):
            predict(self.data, self.output, Config())
        self.assertFalse((self.output / "submission.jsonl").exists())

    def test_outputs_cannot_be_written_into_input(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            predict(self.data, self.data / "result", Config())

    def test_abstain_backend_emits_every_id_without_business_files(self):
        (self.data / "data").rename(self.root / "hidden_business_files")
        predict(self.data, self.output, Config(backend="abstain"))
        records = read_jsonl(self.output / "submission.jsonl")
        self.assertEqual(len(records), 24)
        self.assertTrue(all(r["type"] == "abstain" for r in records))

    def test_corrupt_business_is_logged_and_other_businesses_continue(self):
        (self.data / "data/demo_csv/sales.csv").write_text(
            'order_id,order_date,status,product_id,product_name,line_amount\n'
            '1,2026-08-01,UNKNOWN,P1,Coffee,100\n', encoding="utf-8")
        predict(self.data, self.output, Config())
        records = read_jsonl(self.output / "submission.jsonl")
        self.assertEqual(len(records), 24)
        self.assertEqual(records[0]["type"], "abstain")
        logs = [json.loads(p.read_text(encoding="utf-8"))
                for p in (self.output / "logs").glob("*.json")]
        self.assertTrue(any("Unknown status" in log["reason"] for log in logs))

    def test_invalid_agent_numbers_cannot_break_submission_or_logs(self):
        class InvalidAgent:
            def answer(self, question, context):
                return Candidate("number", float("nan"), float("nan"),
                                 evidence={"bad_output": float("inf")})

        predict(self.data, self.output, Config(), agent=InvalidAgent())
        records = read_jsonl(self.output / "submission.jsonl")
        self.assertEqual(len(records), 24)
        self.assertTrue(all(r["type"] == "abstain" for r in records))

    def test_vietnamese_export_statuses_and_currency_preserve_calculations(self):
        path = self.data / "data/demo_csv/sales.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        statuses = {"completed": "Đã hoàn thành", "cancelled": "khách hủy", "returned": "Đã hoàn hàng"}
        for row in rows:
            row["status"] = statuses[row["status"]]
            if row["order_id"] == "O4":
                row["status"] = "Đang giao hàng"
            row["line_amount"] = f"{int(row['line_amount']):,}".replace(",", ".") + "đ"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        predict(self.data, self.output, Config())
        records = {r["id"]: r for r in read_jsonl(self.output / "submission.jsonl")}
        self.assertEqual(records["demo_1_1"]["answer"], 130000)
        self.assertEqual(records["demo_1_2"]["answer"], 2)
