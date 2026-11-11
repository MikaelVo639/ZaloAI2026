import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from datamate.data_tools import business_holdout, import_archive
from datamate.demo import create_demo
from datamate.ingestion.catalog import build_catalog, catalog_dict, sqlite_connection


class DataToolTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_archive_paths_cannot_escape_destination(self):
        archive = self.root / "bad.zip"
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("../escaped.txt", "bad")
        with self.assertRaisesRegex(ValueError, "Invalid archive path"):
            import_archive(archive, self.root / "raw")
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_import_preserves_split_and_refuses_overwrite(self):
        archive = self.root / "good.zip"
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("train/rulebook.md", "rules")
        report = import_archive(archive, self.root / "raw")
        self.assertEqual(len(report["sha256"]), 64)
        self.assertEqual((self.root / "raw/train/rulebook.md").read_text(), "rules")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            import_archive(archive, self.root / "raw")

    def test_holdout_has_no_business_overlap_and_is_reproducible(self):
        data = self.root / "demo"
        create_demo(data)
        split = business_holdout(data, self.root / "a.json", seed=9)
        repeated = business_holdout(data, self.root / "b.json", seed=9)
        self.assertEqual(split, repeated)
        self.assertFalse(set(split["train_businesses"]) & set(split["validation_businesses"]))
        self.assertEqual(len(split["train_ids"]) + len(split["validation_ids"]), 24)

    def test_sqlite_sources_are_opened_read_only(self):
        path = self.root / "source.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE example (n INTEGER)")
        connection.close()
        connection = sqlite_connection(path)
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("INSERT INTO example VALUES (1)")

    def test_shared_metadata_is_identical_in_different_checkout_paths(self):
        original = self.root / "checkout one" / "data" / "train"
        copied = self.root / "another checkout" / "data" / "train"
        create_demo(original)
        shutil.copytree(original, copied)
        a = business_holdout(original, self.root / "first.json")
        b = business_holdout(copied, self.root / "second.json")
        self.assertEqual(a, b)
        self.assertEqual(a["source"], "questions.jsonl")
        self.assertEqual(len(a["questions_sha256"]), 64)
        for business in ("demo_csv", "demo_xlsx", "demo_sqlite"):
            first = catalog_dict(build_catalog(original / "data", business), relative_to=original)
            second = catalog_dict(build_catalog(copied / "data", business), relative_to=copied)
            self.assertEqual(first, second)
            for table in first["tables"]:
                self.assertTrue(table["source"].startswith(f"data/{business}/"))
                self.assertNotIn("\\", table["source"])
