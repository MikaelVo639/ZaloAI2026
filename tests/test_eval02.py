import json
import tempfile
import unittest
from pathlib import Path

from datamate.evaluation.benchmark import compare_runs, scorer_identity
from datamate.evaluation.scorer import matches


class Eval02Tests(unittest.TestCase):
    def test_exact_numbers_in_scalars_lists_and_tables(self):
        def record(kind, value):
            return {"id": "q", "type": kind, "answer": value}
        for kind, actual, expected in (
            ("number", 1.0000000005, 1.0),
            ("number", 9007199254740993, 9007199254740992.0),
            ("number", 12.345, 12.35),
            ("list", [1.0000000005], [1]),
            ("table", {"columns": ["x"], "rows": [[1.0000000005]]},
             {"columns": ["x"], "rows": [[1]]}),
        ):
            with self.subTest(kind=kind, actual=actual):
                self.assertFalse(matches(record(kind, actual), record(kind, expected)))
        self.assertTrue(matches(record("number", 1.0), record("number", 1)))
        self.assertTrue(matches(record("number", 10**30), record("number", 10**30)))

    def test_config_opt_in_preserves_data_and_scorer_guards_and_reports_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = {"input_sha256": "input", "reference_sha256": "labels",
                        "holdout_sha256": "holdout", "config_sha256": "new", **scorer_identity()}
            old = {**identity, "config_sha256": "old", "status": "complete",
                   "revision": "before", "config": {"backend": "baseline"}}
            (root / "experiment.json").write_text(json.dumps(old), encoding="utf-8")
            def row(key, result):
                return {"id": key, "outcome": result,
                        "prediction": {"id": key, "type": "abstain"}}
            before = [row("a", "abstain"), row("b", "correct"), row("c", "abstain")]
            (root / "outcomes.jsonl").write_text(
                "\n".join(json.dumps(r) for r in before), encoding="utf-8")
            after = [row("a", "correct"), row("b", "correct"), row("c", "wrong")]
            with self.assertRaises(ValueError):
                compare_runs(after, root, identity)
            report = compare_runs(after, root, identity, True, {"backend": "model"}, ["a", "b"])
            self.assertTrue(report["validation"]["quality_gate_passed"])
            self.assertEqual(report["config_diff"]["backend"], {"before": "baseline", "after": "model"})
            report = compare_runs(after, root, identity, True, {}, ["a", "b", "c"])
            self.assertEqual(report["validation"]["new_wrong_ids"], ["c"])
            self.assertFalse(report["validation"]["quality_gate_passed"])
            for field in ("input_sha256", "reference_sha256", "holdout_sha256", "scorer_sha256", "scorer_version"):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    compare_runs(after, root, {**identity, field: "different"}, True)
            self.assertFalse(compare_runs(before, root, identity, True)["validation"]["quality_gate_passed"])

    def test_config_flag_requires_comparison(self):
        from datamate.evaluation.benchmark import benchmark
        with self.assertRaisesRegex(ValueError, "requires"):
            benchmark(".", ".", ".", ".", ".", allow_config_difference=True)

    def test_git_metadata_accepts_vietnamese_dirty_diff(self):
        import subprocess
        from datamate.evaluation.benchmark import source_metadata
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            git("init")
            (root / "uv.lock").write_text("lock", encoding="utf-8")
            git("add", "uv.lock")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
            (root / "uv.lock").write_text("Đánh giá dữ liệu", encoding="utf-8")
            metadata = source_metadata(root)
            self.assertTrue(metadata["dirty"])
            self.assertEqual(len(metadata["diff_sha256"]), 64)

    def test_grammar_handoff_has_topics_and_train_only_examples(self):
        from datamate.demo import create_demo
        from datamate.data_tools import business_holdout
        from datamate.evaluation.benchmark import analyze, validate_holdout
        from datamate.io import read_jsonl, write_jsonl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, output = root / "data", root / "output"
            create_demo(data)
            manifest = business_holdout(data, root / "holdout.json")
            (output / "logs").mkdir(parents=True)
            questions = read_jsonl(data / "questions.jsonl")
            predictions = []
            for i, question in enumerate(questions):
                record = {"id": question["id"], "type": "abstain"}
                predictions.append(record)
                log = {**question, "question": ("doanh thu", "đơn", "tồn kho")[i % 3],
                       "reason": "outside baseline grammar", "final_answer": record}
                (output / "logs" / f"{i}.json").write_text(json.dumps(log), encoding="utf-8")
            write_jsonl(output / "submission.jsonl", predictions)
            analyze(output, data, manifest, validate_holdout(data, manifest))
            groups = json.loads((output / "handoff.json").read_text(encoding="utf-8"))["Semantics"]
            self.assertEqual(len(groups), 3)
            self.assertEqual(sum(g["affected_questions"] for g in groups), 24)
            self.assertEqual(len({g["topic"] for g in groups}), 3)
            for group in groups:
                self.assertTrue(group["cases"])
                self.assertTrue(all(c["id"] in manifest["train_ids"] for c in group["cases"]))
