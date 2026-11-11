import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datamate.data_tools import business_holdout
from datamate.demo import create_demo
from datamate.evaluation.benchmark import analyze, digest, input_tree, save, scorer_identity
from datamate.io import load_split
from datamate.pipeline import Config, predict
from scripts.verify_candidate import verify


class CandidateVerificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data, self.run = self.root / "data", self.root / "run"
        create_demo(self.data)
        predict(self.data, self.run, Config(backend="abstain"))
        holdout = business_holdout(self.data, self.run / "business_split.json")
        (self.run / "config.toml").write_text('backend="abstain"\n', encoding="utf-8")
        analyze(self.run, self.data, holdout, load_split(self.data))
        run_metadata = json.loads((self.run / "run.json").read_text(encoding="utf-8"))
        save(self.run / "experiment.json", {
            "status": "complete", "target_source_sha256": "test-source",
            "config_sha256": digest(self.run / "config.toml"),
            "holdout_sha256": digest(self.run / "business_split.json"),
            "reference_sha256": digest(self.data / "answers.jsonl"),
            "input_sha256": hashlib.sha256(json.dumps(input_tree(self.data)).encode()).hexdigest(),
            "target_run": run_metadata, **scorer_identity(),
        })
        mock = patch("scripts.verify_candidate.target_runtime",
                     return_value={"target_source_sha256": "test-source"})
        mock.start()
        self.addCleanup(mock.stop)

    def check(self):
        return verify(self.root, self.data, self.run, self.root / "checks")

    def test_integrity_pass_does_not_certify_absence_or_reproducibility(self):
        result = self.check()
        self.assertEqual(result["artifact_integrity"], "passed")
        self.assertFalse(result["absence_audit_complete"])
        self.assertFalse(result["repeat_verified"])
        self.assertEqual(result["review_required_questions"], 24)

    def test_rejects_modified_metrics(self):
        path = self.run / "metrics.json"
        metrics = json.loads(path.read_text(encoding="utf-8"))
        metrics["train"]["correct"] += 1
        save(path, metrics)
        with self.assertRaisesRegex(ValueError, "do not match rescoring"):
            self.check()

    def test_rejects_missing_log(self):
        next((self.run / "logs").glob("*.json")).unlink()
        with self.assertRaisesRegex(ValueError, "Missing question logs"):
            self.check()
