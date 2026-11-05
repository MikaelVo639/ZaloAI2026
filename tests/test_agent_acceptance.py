"""Synthetic acceptance artifacts; no model quality claim and no reference reads."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

from datamate.agent.acceptance import compare_acceptance, environment_report, runtime_report
from datamate.agent.model_runtime import MODEL_ID, MODEL_REVISION
from datamate.io import write_jsonl


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def artifact(root, model=False, answered=False):
    root.mkdir()
    (root / "logs").mkdir()
    predictions = [{"id": "a", "type": "number", "answer": 10},
                   {"id": "b", "type": "number", "answer": 20} if answered else {"id": "b", "type": "abstain"}]
    write_jsonl(root / "submission.jsonl", predictions)
    info = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "gpu": "synthetic-only",
            "load_execution": {"elapsed_seconds": 2}}
    save(root / "run.json", {"questions": 2, "businesses": 1, "elapsed_seconds": 10,
                              "models": [info] if model else []})
    for index, prediction in enumerate(predictions):
        save(root / "logs" / f"{index}.json", {
            "id": prediction["id"], "business_id": "routing", "final_answer": prediction,
            "elapsed_seconds": 3, "business_ingestion_seconds": 1,
            "evidence": {"model_output": {"text": "synthetic"}} if model and index == 1 else {}})
    save(root / "experiment.json", {"status": "complete", "input_sha256": "input",
                                    "holdout_sha256": "holdout", "reference_sha256": "ref",
                                    "config_sha256": "model" if model else "baseline",
                                    "predict_wall_seconds": 12})
    save(root / "business_split.json", {"seed": 42, "validation_ids": ["b"]})
    write_jsonl(root / "outcomes.jsonl", [
        {"id": p["id"], "prediction": p, "outcome": "abstain" if p["type"] == "abstain" else "correct"}
        for p in predictions])
    save(root / "metrics.json", {"train": {"pa": 1 if answered else 0.5},
                                 "validation": {"pa": 1 if answered else 0}})
    return root


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_projection_scales_questions_and_businesses_and_counts_load_once(self):
        run = artifact(self.root / "run", model=True, answered=True)
        result = runtime_report(run, 4, 2)
        # Process elapsed 12 = fixed 5 + load 2 + question work 4 + catalogs 1.
        # Double workload: fixed 5 + load 2 + question work 8 + catalogs 2 = 17.
        self.assertEqual(result["measured_seconds_including_load"], 12)
        self.assertEqual(result["model_load_seconds"], 2)
        self.assertEqual(result["private_projection"]["seconds"], 17)
        self.assertEqual(result["model_generations"], 1)
        self.assertEqual(result["model_answers"], 1)

    def test_rejects_partial_runs_missing_counts_and_bad_timing(self):
        run = artifact(self.root / "run")
        for counts in ((3, None), (None, 2), (0, 1), (1, 2), (True, 1)):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                runtime_report(run, *counts)
        path = run / "logs/0.json"
        log = json.loads(path.read_text())
        log["elapsed_seconds"] = -1
        save(path, log)
        with self.assertRaisesRegex(ValueError, "timing"):
            runtime_report(run)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "complete"):
            runtime_report(run)

    def test_environment_check_does_not_download_or_read_model_files(self):
        with patch("datamate.agent.acceptance.importlib.metadata.version", side_effect=PackageNotFoundError), patch(
                "datamate.agent.acceptance.importlib.util.find_spec", return_value=None):
            result = environment_report(self.root / "missing")
        self.assertFalse(result["ready_for_gpu_acceptance"])
        self.assertIsNone(result["gpu"])
        self.assertIn("Prepared model snapshot/manifest is missing", result["issues"])


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.baseline = artifact(self.root / "baseline")
        self.first = artifact(self.root / "first", model=True, answered=True)
        self.repeat = artifact(self.root / "repeat", model=True, answered=True)

    def report(self):
        return compare_acceptance(self.baseline, self.first, self.repeat)

    def test_complete_comparison_with_repeat_and_no_new_wrong(self):
        result = self.report()
        self.assertTrue(result["ready_for_evaluation_review"])
        self.assertTrue(result["final_direction_requires_evaluation_and_integration"])
        self.assertEqual(result["validation_pa_delta"], 1)
        self.assertEqual(result["changes"][0]["id"], "b")

    def test_every_new_wrong_answer_is_listed_and_fails_gate(self):
        path = self.first / "outcomes.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[1]["outcome"] = "wrong"
        write_jsonl(path, rows)
        result = self.report()
        self.assertFalse(result["ready_for_evaluation_review"])
        self.assertEqual(result["newly_wrong"][0]["id"], "b")

    def test_mismatched_inputs_config_or_incomplete_runs_are_rejected(self):
        path = self.repeat / "experiment.json"
        original = json.loads(path.read_text())
        for mutation in ({"input_sha256": "changed"}, {"config_sha256": "changed"},
                         {"status": "incomplete"}, {"reference_sha256": None}):
            save(path, {**original, **mutation})
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.report()

    def test_changed_repeat_submission_cannot_pass_reproducibility(self):
        with (self.repeat / "submission.jsonl").open("a") as stream:
            stream.write("\n")
        result = self.report()
        self.assertFalse(result["gates"]["reproducible_submission"])

    def test_fake_or_missing_model_and_long_private_projection_fail_gate(self):
        path = self.first / "run.json"
        manifest = json.loads(path.read_text())
        manifest["models"][0]["model_id"] = "unapproved-model"
        save(path, manifest)
        self.assertFalse(self.report()["gates"]["real_model_generation_on_gpu"])
        result = compare_acceptance(self.baseline, self.first, self.repeat, 100000, 100)
        self.assertFalse(result["gates"]["private_projection_under_6_hours"])


class AcceptanceCommandTests(unittest.TestCase):
    def runner(self):
        spec = importlib.util.spec_from_file_location(
            "agent02_acceptance_command", Path(__file__).resolve().parents[1] / "scripts/run_agent02_acceptance.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_gpu_stops_before_any_inference(self):
        module = self.runner()
        with tempfile.TemporaryDirectory() as temporary, patch("sys.argv", [
                "run_agent02_acceptance", "--output-dir", str(Path(temporary) / "out")]), \
                patch.object(module, "environment_report", return_value={"ready_for_gpu_acceptance": False}), \
                patch.object(module, "benchmark") as benchmark, redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 2)
            benchmark.assert_not_called()
            self.assertFalse((Path(temporary) / "out").exists())

    def test_prepared_environment_runs_three_stages_and_writes_acceptance(self):
        module = self.runner()
        calls = []

        def synthetic_benchmark(checkout, data, holdout, config, output):
            calls.append(output.name)
            artifact(output, model=output.name != "baseline", answered=output.name != "baseline")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            with patch("sys.argv", ["run_agent02_acceptance", "--data-dir", str(Path(temporary) / "data"),
                                   "--output-dir", str(output)]), \
                    patch.object(module, "environment_report", return_value={"ready_for_gpu_acceptance": True}), \
                    patch.object(module, "benchmark", side_effect=synthetic_benchmark), redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
            self.assertEqual(calls, ["baseline", "model-first", "model-repeat"])
            self.assertTrue(json.loads((output / "acceptance.json").read_text())["ready_for_evaluation_review"])


if __name__ == "__main__":
    unittest.main()
