import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from datamate.demo import run_smoke

spec = importlib.util.spec_from_file_location(
    "check_demo_output", Path(__file__).resolve().parents[1] / "scripts/check_demo_output.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PackagingTests(unittest.TestCase):
    def test_repeated_package_outputs_and_corrupt_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, first, second = root / "data", root / "first", root / "second"
            run_smoke(data, first)
            run_smoke(data, second)
            checker.check(data, [first, second])
            log_path = next((second / "logs").glob("*.json"))
            log = json.loads(log_path.read_text(encoding="utf-8"))
            log["error"] = "Worker failed after writing answer"
            log_path.write_text(json.dumps(log), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "execution error"):
                checker.check(data, [first, second])

    def test_missing_log_and_wrong_input_checksum_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, run = root / "data", root / "run"
            run_smoke(data, run)
            log_path = next((run / "logs").glob("*.json"))
            backup = root / "backup.json"
            shutil.copyfile(log_path, backup)
            log_path.unlink()
            with self.assertRaisesRegex(ValueError, "Missing or extra"):
                checker.check(data, [run])
            shutil.copyfile(backup, log_path)
            with (data / "calendar.csv").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                checker.check(data, [run])
