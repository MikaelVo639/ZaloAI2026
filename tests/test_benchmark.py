import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datamate.data_tools import business_holdout
from datamate.demo import create_demo
from datamate.evaluation.benchmark import (
    analyze,
    benchmark,
    compare_runs,
    error_info,
    reason_group,
    run_process,
    validate_holdout,
)
from datamate.pipeline import Config, predict


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="eval paths with spaces ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        create_demo(self.data)
        self.manifest = business_holdout(self.data, self.root / "holdout.json")

    def test_holdout_checks_identity_partitions_and_businesses(self):
        validate_holdout(self.data, self.manifest)
        for mutate in (
            lambda m: m.update(questions_sha256="wrong"),
            lambda m: m.update(rulebook_sha256="wrong"),
            lambda m: m["validation_ids"].append(m["validation_ids"][0]),
            lambda m: m["validation_ids"].append("unknown"),
            lambda m: m["train_businesses"].extend(m["validation_businesses"]),
            lambda m: m["validation_ids"].pop(),
        ):
            m = json.loads(json.dumps(self.manifest))
            mutate(m)
            with self.subTest(m=m), self.assertRaises(ValueError):
                validate_holdout(self.data, m)

    def test_nested_errors_are_counted_per_question_and_adapter_reason_survives(self):
        log = {
            "error": "outer",
            "reason": "Unknown status x",
            "evidence": {
                "execution": {"status": "error"},
                "ingestion_execution": {"status": "timeout"},
            },
        }
        self.assertEqual(len(error_info(log)), 3)
        self.assertEqual(reason_group(log), "unknown_status")
        self.assertEqual(
            error_info(
                {
                    "reason": "Unknown status x",
                    "evidence": {"ingestion_execution": {"status": "ok"}},
                }
            ),
            {},
        )
        self.assertEqual(error_info({}), {})

    def test_analysis_legacy_logs_missing_logs_and_comparison_by_id(self):
        out = self.root / "run"
        predict(self.data, out, Config())
        split = validate_holdout(self.data, self.manifest)
        rows, metrics = analyze(out, self.data, self.manifest, split)
        self.assertEqual(metrics["train"]["correct"], 21)
        self.assertEqual(metrics["worker_metadata_missing_questions"], 3)
        log_path = next((out / "logs").glob("*.json"))
        log = json.loads(log_path.read_text(encoding="utf-8"))
        log.update(
            error="outer",
            evidence={
                "execution": {"status": "error"},
                "ingestion_execution": {"status": "timeout"},
            },
        )
        log_path.write_text(json.dumps(log), encoding="utf-8")
        _, nested_metrics = analyze(out, self.data, self.manifest, split)
        self.assertEqual(nested_metrics["execution_error_questions"], 1)
        save = {"status": "complete", "revision": "test", "input_sha256": "x"}
        (out / "experiment.json").write_text(json.dumps(save), encoding="utf-8")
        self.assertTrue(
            compare_runs(list(reversed(rows)), out, {"input_sha256": "x"})["identical_answers"]
        )
        with self.assertRaises(ValueError):
            compare_runs(rows, out, {"input_sha256": "other"})
        changed = json.loads(json.dumps(rows))
        changed[0]["prediction"] = {"id": changed[0]["id"], "type": "abstain"}
        changed[0]["outcome"] = "abstain"
        self.assertEqual(len(compare_runs(changed, out, {"input_sha256": "x"})["changes"]), 1)
        log_path.write_text(json.dumps({**log, "final_answer": {"id": log["id"], "type": "abstain"}}), encoding="utf-8")
        if log["final_answer"]["type"] != "abstain":
            with self.assertRaisesRegex(ValueError, "mismatch"):
                analyze(out, self.data, self.manifest, split)
        next((out / "logs").glob("*.json")).unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            analyze(out, self.data, self.manifest, split)

    def test_subprocess_spaces_and_hard_timeout(self):
        code, expired = run_process(
            [sys.executable, "-c", "print(42)"],
            self.root,
            5,
            self.root / "stdout",
            self.root / "stderr",
        )
        self.assertEqual((code, expired), (0, False))
        code, expired = run_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            self.root,
            0.2,
            self.root / "stdout2",
            self.root / "stderr2",
        )
        self.assertTrue(expired)

    def test_timeout_kills_descendants(self):
        import os
        import time

        pid_file = self.root / "child.pid"
        child = "import time; time.sleep(60)"
        parent = (
            "import subprocess, sys, pathlib, time; "
            f'p=subprocess.Popen([sys.executable,"-c",{child!r}]); '
            f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(60)"
        )
        _, expired = run_process(
            [sys.executable, "-c", parent],
            self.root,
            2,
            self.root / "tree.out",
            self.root / "tree.err",
        )
        self.assertTrue(expired)
        pid = int(pid_file.read_text())
        if os.name == "nt":
            import ctypes

            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                code = ctypes.c_ulong()
                kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                kernel.CloseHandle(handle)
                self.assertNotEqual(code.value, 259)
        else:
            time.sleep(0.1)
            proc = Path(f"/proc/{pid}/stat")
            if proc.exists():
                self.assertEqual(proc.read_text().split()[2], "Z")

    @unittest.skipUnless(sys.platform == "win32", "Windows inherited handle semantics")
    def test_waits_for_log_handle_release_and_bounds_wait(self):
        import threading

        from datamate.evaluation.benchmark import wait_windows_log_release

        path = self.root / "locked log"
        writer = path.open("wb")
        try:
            with self.assertRaises(TimeoutError):
                wait_windows_log_release([path], timeout=0.03)
            timer = threading.Timer(0.1, writer.close)
            timer.start()
            self.addCleanup(timer.join)
            wait_windows_log_release([path])
            self.assertTrue(writer.closed)
            path.unlink()  # No sharing violation immediately after the wait.
        finally:
            writer.close()

    def test_runner_uses_target_python_and_reads_labels_only_after_exit(self):
        checkout = self.root / "checkout with spaces"
        python = (
            checkout / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        )
        python.parent.mkdir(parents=True)
        python.touch()
        config = self.root / "config.toml"
        config.write_text('backend="baseline"\n', encoding="utf-8")
        output = self.root / "benchmark run"
        original = Path.open
        exited = False

        def guarded(path, *args, **kwargs):
            if path.name == "answers.jsonl" and not exited:
                raise AssertionError("Labels read before inference exited")
            return original(path, *args, **kwargs)

        def fake_process(command, cwd, timeout, stdout, stderr):
            nonlocal exited
            self.assertEqual(command[0], str(python))
            self.assertEqual(cwd, checkout)
            self.assertEqual(timeout, 21000)
            predict(self.data, output, Config())
            exited = True
            return 0, False

        with (
            patch(
                "datamate.evaluation.benchmark.source_metadata",
                return_value={"revision": "test", "dirty": False},
            ),
            patch("datamate.evaluation.benchmark.run_process", side_effect=fake_process),
            patch("datamate.evaluation.benchmark.target_runtime", return_value={}),
            patch.object(Path, "open", guarded),
        ):
            report = benchmark(checkout, self.data, self.root / "holdout.json", config, output)
        self.assertEqual(report["status"], "complete")
        self.assertTrue((output / "artifact.zip.sha256").is_file())
        from datamate.evaluation.benchmark import rescore, scorer_identity
        metadata = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["scorer_sha256"], scorer_identity()["scorer_sha256"])
        rescored = self.root / "rescored"
        self.assertEqual(rescore(output, self.data, self.root / "holdout.json", rescored)["status"], "complete")
        self.assertEqual((output / "submission.jsonl").read_bytes(), (rescored / "submission.jsonl").read_bytes())
        handoff = json.loads((rescored / "handoff.json").read_text(encoding="utf-8"))
        for groups in handoff.values():
            for group in groups:
                self.assertIn("topic", group)
                self.assertTrue(all(case["id"] in self.manifest["train_ids"] for case in group["cases"]))
        metadata["reference_sha256"] = "wrong"
        (output / "experiment.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            rescore(output, self.data, self.root / "holdout.json", self.root / "bad rescore")

        with self.assertRaisesRegex(ValueError, "nonempty"):
            benchmark(checkout, self.data, self.root / "holdout.json", config, output)
        failed = self.root / "failed run"
        with (
            patch(
                "datamate.evaluation.benchmark.source_metadata", return_value={"revision": "test"}
            ),
            patch("datamate.evaluation.benchmark.run_process", return_value=(1, False)),
            patch("datamate.evaluation.benchmark.target_runtime", return_value={}),
        ):
            with self.assertRaisesRegex(ValueError, "Predict failed"):
                benchmark(checkout, self.data, self.root / "holdout.json", config, failed)
        self.assertEqual(
            json.loads((failed / "experiment.json").read_text())["status"], "incomplete"
        )
        self.assertTrue((failed / "artifact.zip").is_file())

    def test_runtime_rejects_package_from_another_checkout(self):
        from datamate.evaluation.benchmark import target_runtime

        checkout = self.root / "other_checkout"
        checkout.mkdir()
        with self.assertRaisesRegex(ValueError, "Wrong datamate package"):
            target_runtime(Path(sys.executable), checkout)

    def test_cli_benchmark_arguments(self):
        from datamate.cli import parser

        args = parser().parse_args(
            [
                "benchmark",
                "--checkout",
                "checkout with spaces",
                "--data-dir",
                "data",
                "--holdout",
                "manifest",
                "--config",
                "config",
                "--output-dir",
                "new run",
                "--compare-to",
                "old run",
            ]
        )
        self.assertEqual(args.checkout, Path("checkout with spaces"))
        self.assertEqual(args.compare_to, Path("old run"))


if __name__ == "__main__":
    unittest.main()
