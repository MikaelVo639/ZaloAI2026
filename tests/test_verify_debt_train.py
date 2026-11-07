"""Regression for the D01 development evidence runner."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_ingestion_debt import README, RULEBOOK


class DebtEvidenceScriptTests(unittest.TestCase):
    def test_relative_root_uses_script_checkout_revision_and_manifest(self):
        repository = Path(__file__).resolve().parent.parent
        cache = repository / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            root = Path(temporary)
            shop = root / "data" / "shop"
            shop.mkdir(parents=True)
            (root / "rulebook.md").write_text(RULEBOOK, encoding="utf-8")
            (shop / "README.md").write_text(README, encoding="utf-8")
            sources = {
                "orders.csv": [
                    ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh",
                     "Mã khách", "Tên khách", "Còn nợ"],
                    ["D1", "01/01/2026", "Xong", "100", "Ghi nợ", "Store", "K1",
                     "An", "50"],
                ],
                "payments.csv": [
                    ["Mã đơn", "Ngày thu", "Tiền thu"],
                    ["D1", "02/01/2026", "50"],
                ],
                "customers.csv": [
                    ["Mã KH", "Khách hàng"], ["K1", "An"],
                ],
            }
            for name, rows in sources.items():
                with (shop / name).open("w", encoding="utf-8-sig", newline="") as stream:
                    csv.writer(stream).writerows(rows)

            rulebook_hash = hashlib.sha256((root / "rulebook.md").read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "rulebook_sha256": rulebook_hash,
                "train_businesses": ["shop"],
                "validation_businesses": ["holdout"],
            }), encoding="utf-8")
            output = root / "evidence.json"
            script = repository / "scripts" / "verify_debt_train.py"
            subprocess.run([
                sys.executable, str(script), "--train-root", ".",
                "--manifest", "manifest.json", "--output", "evidence.json",
                "--business", "shop",
            ], cwd=root, check=True, capture_output=True, text=True)

            artifact = json.loads(output.read_text(encoding="utf-8"))
            expected_revision = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(artifact["revision"], expected_revision)
            self.assertEqual(artifact["manifest"]["sha256"],
                             hashlib.sha256(manifest.read_bytes()).hexdigest())
            self.assertEqual(artifact["adapter"]["path"],
                             "src/datamate/ingestion/debt.py")
            self.assertEqual(artifact["businesses"][0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
