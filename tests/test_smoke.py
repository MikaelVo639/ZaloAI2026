import tempfile
import unittest
from pathlib import Path

from datamate.demo import run_smoke


class SmokeTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "team checkout with spaces"

    def test_fresh_checkout_can_run_and_reuse_generated_demo(self):
        data = self.root / "data" / "demo"
        output = self.root / "outputs" / "demo"
        first = run_smoke(data, output)
        original = (output / "submission.jsonl").read_bytes()
        second = run_smoke(data, output)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["validation"]["missing"], 0)
        self.assertEqual(first["metrics"]["pa"], 0.875)
        self.assertEqual(first["metrics"], second["metrics"])
        self.assertEqual(original, (output / "submission.jsonl").read_bytes())

    def test_failed_demo_metrics_raise_instead_of_reporting_success(self):
        data, output = self.root / "data", self.root / "output"
        run_smoke(data, output)
        source = data / "data" / "demo_csv" / "sales.csv"
        source.write_text(source.read_text(encoding="utf-8-sig").replace("40000", "40001"),
                          encoding="utf-8-sig")
        with self.assertRaisesRegex(ValueError, "acceptance failed"):
            run_smoke(data, output)
        self.assertTrue((output / "metrics.json").exists())
