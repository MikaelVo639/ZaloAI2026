import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datamate.data_tools import business_holdout
from datamate.demo import create_demo
from datamate.evaluation.benchmark import digest, input_tree, save, scorer_identity
from datamate.evaluation.calibration import calibrate
from datamate.evaluation.release import verify_image, verify_runs
from datamate.io import read_jsonl, write_jsonl


class ReleaseCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data, self.run = self.root / "data", self.root / "first"
        create_demo(self.data)
        self.holdout = self.root / "holdout.json"
        self.manifest = business_holdout(self.data, self.holdout)
        self.refs = read_jsonl(self.data / "answers.jsonl")
        self.predictions = [{k: v for k, v in ref.items() if k != "alias"} for ref in self.refs]
        self.confidences = {p["id"]: .9 for p in self.predictions}
        # A confident correct group and one low-confidence training mistake.
        wrong = next(p for p in self.predictions if p["id"] in self.manifest["train_ids"] and p["type"] == "number")
        wrong["answer"] += 1
        self.confidences[wrong["id"]] = .4
        (self.run / "logs").mkdir(parents=True)
        write_jsonl(self.run / "submission.jsonl", self.predictions)
        for i, prediction in enumerate(self.predictions):
            save(self.run / "logs" / f"{i}.json", {
                "id": prediction["id"], "final_answer": prediction, "elapsed_seconds": .01,
                "candidate_confidence": self.confidences[prediction["id"]]})
        config = {"backend": "baseline", "min_confidence": 0, "max_seconds": 21000}
        (self.run / "config.toml").write_text('backend="baseline"\nmin_confidence=0\nmax_seconds=21000\n', encoding="utf-8")
        save(self.run / "run.json", {"config": config, "elapsed_seconds": 1,
             "questions_sha256": digest(self.data / "questions.jsonl"),
             "calendar_sha256": digest(self.data / "calendar.csv"),
             "rulebook_sha256": hashlib.sha256((self.data / "rulebook.md").read_text(encoding="utf-8").encode()).hexdigest()})
        self.meta = {"status": "complete", "revision": "fixture", "config": config, **scorer_identity(),
                     "config_sha256": digest(self.run / "config.toml"), "holdout_sha256": digest(self.holdout),
                     "input_sha256": hashlib.sha256(json.dumps(input_tree(self.data)).encode()).hexdigest(),
                     "reference_sha256": digest(self.data / "answers.jsonl")}
        save(self.run / "experiment.json", self.meta)
        self.second = self.root / "second"
        shutil.copytree(self.run, self.second)

    def test_calibration_uses_train_and_locks_only_gate(self):
        result = calibrate(self.run, self.data, self.holdout, self.root / "calibration")
        self.assertEqual(result["selected_threshold"], .9)
        self.assertEqual(result["train"]["wrong"], 0)
        self.assertIn('backend="baseline"', (self.root / "calibration/config.toml").read_text())
        for ref in self.refs:
            if ref["id"] in self.manifest["validation_ids"] and ref["type"] == "number":
                ref["answer"] += 999
        write_jsonl(self.data / "answers.jsonl", self.refs)
        self.meta["reference_sha256"] = digest(self.data / "answers.jsonl")
        save(self.run / "experiment.json", self.meta)
        changed = calibrate(self.run, self.data, self.holdout, self.root / "changed-validation")
        self.assertEqual(result["selected_threshold"], changed["selected_threshold"])
        self.assertEqual(result["train"], changed["train"])
        self.assertNotEqual(result["validation"]["pa"], changed["validation"]["pa"])

    def test_calibration_rejects_suppressed_candidates_and_bad_identity(self):
        self.meta["config"]["min_confidence"] = 1
        save(self.run / "experiment.json", self.meta)
        with self.assertRaisesRegex(ValueError, "min_confidence"):
            calibrate(self.run, self.data, self.holdout, self.root / "bad")

    def test_calibration_tie_prefers_higher_gate(self):
        train = set(self.manifest["train_ids"])
        correct = next(p for p in self.predictions if p["id"] in train and self.confidences[p["id"]] == .9)
        for path in (self.run / "logs").glob("*.json"):
            log = json.loads(path.read_text())
            if log["id"] == correct["id"]:
                log["candidate_confidence"] = .4
                save(path, log)
        result = calibrate(self.run, self.data, self.holdout, self.root / "tie")
        self.assertEqual(result["selected_threshold"], .9)

    def test_answered_candidate_cannot_have_nan_confidence(self):
        path = self.run / "logs/0.json"
        log = json.loads(path.read_text())
        log["candidate_confidence"] = float("nan")
        save(path, log)
        with self.assertRaisesRegex(ValueError, "confidence"):
            calibrate(self.run, self.data, self.holdout, self.root / "bad-confidence")
        self.meta["scorer_sha256"] = "wrong"
        save(self.run / "experiment.json", self.meta)
        with self.assertRaisesRegex(ValueError, "matching"):
            calibrate(self.run, self.data, self.holdout, self.root / "bad")

    def test_release_is_label_free_and_matches_by_id(self):
        write_jsonl(self.second / "submission.jsonl", list(reversed(self.predictions)))
        original = Path.open
        def guard(path, *args, **kwargs):
            if path.name == "answers.jsonl":
                raise AssertionError("Release verifier read labels")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guard):
            result = verify_runs(self.data, [self.run, self.second], self.root / "checks")
        self.assertTrue(result["identical_answers_by_id"])
        self.assertFalse(result["includes_model_loading_confirmed"])

    def test_release_rejects_changed_answer_even_with_matching_log(self):
        self.predictions[0] = {"id": self.predictions[0]["id"], "type": "abstain"}
        write_jsonl(self.second / "submission.jsonl", self.predictions)
        log_path = self.second / "logs/0.json"
        log = json.loads(log_path.read_text())
        log["final_answer"] = self.predictions[0]
        save(log_path, log)
        with self.assertRaisesRegex(ValueError, "differ"):
            verify_runs(self.data, [self.run, self.second], self.root / "checks")

    def test_release_rejects_missing_log_runtime_and_checksum(self):
        for field, value in (("elapsed_seconds", 22000), ("questions_sha256", "bad")):
            original = json.loads((self.second / "run.json").read_text())
            save(self.second / "run.json", {**original, field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_runs(self.data, [self.run, self.second], self.root / "checks")
            save(self.second / "run.json", original)
        (self.second / "logs/0.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            verify_runs(self.data, [self.run, self.second], self.root / "checks")

    def test_image_uses_immutable_id_offline_readonly_and_times_container(self):
        commands = []
        def docker(command, **kwargs):
            commands.append(command)
            if command[1] == "run":
                self.assertIn("none", command)
                self.assertIn("never", command)
                self.assertTrue(any("target=/data,readonly" in arg for arg in command))
                self.assertEqual(command[-1], "sha256:fixture")
                destination = next(arg for arg in command if "target=/result" in arg).split("source=", 1)[1].split(",target=")[0]
                shutil.copytree(self.run, destination, dirs_exist_ok=True)
            return subprocess.CompletedProcess(command, 0)
        with patch("datamate.evaluation.release.subprocess.check_output", return_value="sha256:fixture\n"), patch("datamate.evaluation.release.subprocess.run", side_effect=docker):
            result = verify_image("tag", self.data, self.root / "image")
        self.assertEqual(sum(c[1] == "run" for c in commands), 2)
        self.assertTrue(result["includes_model_loading_confirmed"])
        self.assertFalse(result["reference_hardware_confirmed"])

    def test_image_timeout_cleans_only_own_container_and_records_failure(self):
        commands = []
        def docker(command, **kwargs):
            commands.append(command)
            if command[1] == "run":
                Path(command[command.index("--cidfile") + 1]).write_text("owned-container")
                raise subprocess.TimeoutExpired(command, 1)
            return subprocess.CompletedProcess(command, 0)
        with patch("datamate.evaluation.release.subprocess.check_output", return_value="sha256:fixture\n"), patch("datamate.evaluation.release.subprocess.run", side_effect=docker):
            with self.assertRaises(ValueError):
                verify_image("tag", self.data, self.root / "image", max_seconds=1)
        self.assertIn(["docker", "rm", "-f", "owned-container"], commands)
        result = json.loads((self.root / "image/image-verification.json").read_text())
        self.assertEqual(result["status"], "failed")
