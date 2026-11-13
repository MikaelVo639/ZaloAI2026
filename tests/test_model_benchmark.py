"""Model benchmark keeps labels outside inference and selects repeatable lost plans."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from datamate.contracts import Question
from datamate.demo import create_demo
from datamate.io import read_jsonl, write_jsonl
from datamate.pipeline import Config, predict
from scripts.model_benchmark import _choose, build, score


class ModelBenchmarkTests(unittest.TestCase):
    def test_build_and_score_labelled_train(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            train = root / "train"
            create_demo(train)
            ref = root / "ref"
            predict(train, ref, Config())
            prediction_file = ref / "submission.jsonl"
            left, right = root / "left", root / "right"
            # The grammar now reads every demo variant (small-talk openings are cut), so the
            # selection is exercised with the cut turned off; workers=1 keeps the patch.
            with patch("datamate.agent.baseline.opening_cuts", return_value=[]):
                report = build(train, prediction_file, left, limit=8)
                self.assertEqual(build(train, prediction_file, right, limit=8), report)
            self.assertEqual(report["questions"], 8)
            for relative in ("split/questions.jsonl", "answers.jsonl", "cases.jsonl", "build.json"):
                self.assertEqual((left / relative).read_bytes(), (right / relative).read_bytes())
            self.assertFalse((left / "split" / "answers.jsonl").exists())
            ids = {row["id"] for row in read_jsonl(left / "split" / "questions.jsonl")}
            self.assertEqual(ids, {row["id"] for row in read_jsonl(left / "answers.jsonl")})
            results = score(left, Config(), root / "score")
            # Scored with the cut on, the baseline answers the selected variants itself.
            self.assertEqual((results["correct"], results["wrong"], results["abstain"]), (8, 0, 0))
            self.assertEqual(results["wrong_ids"], [])
            with self.assertRaisesRegex(ValueError, "overwrite"):
                build(train, prediction_file, left, limit=8)
            # Parallel scanning gives the same result as one worker.
            for workers in (1, 2):
                with self.subTest(workers=workers), self.assertRaisesRegex(ValueError, "No correct"):
                    build(train, prediction_file, root / f"none-{workers}", limit=8, workers=workers)

    def test_selection_reaches_rare_intents_before_repeating_common_ones(self):
        questions = [Question(str(n), f"shop-{n}", "sample", date(2026, 2, 3))
                     for n in range(5)]
        buckets = {("average_order", q.business_id): [q] for q in questions[:-1]}
        buckets[("stock", questions[-1].business_id)] = [questions[-1]]
        chosen = _choose({"verbose_chat": buckets}, limit=2)
        self.assertEqual([intent for _, intent, _ in chosen], ["average_order", "stock"])

    def test_build_rejects_missing_labels_and_incomplete_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            train = root / "train"
            create_demo(train)
            ref = root / "predictions.jsonl"
            write_jsonl(ref, [])
            with self.assertRaises(ValueError):
                build(train, ref, root / "missing-predictions")
            (train / "answers.jsonl").unlink()
            with self.assertRaisesRegex(ValueError, "labelled TRAIN"):
                build(train, ref, root / "unlabelled")


if __name__ == "__main__":
    unittest.main()
