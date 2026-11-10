"""Staff names are aliases only when the source identifies one seller code."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.staff import StaffSourceError, load_staff_directory


class StaffDirectoryTests(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"

    def catalog(self, rows):
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text("# Shop\nFile máy POS xuất ra.\n", encoding="utf-8")
        with closing(sqlite3.connect(root / "pos.sqlite")) as connection:
            connection.execute("CREATE TABLE nhan_vien (MaNhanVien TEXT, TenGoi TEXT)")
            connection.executemany("INSERT INTO nhan_vien VALUES (?, ?)", rows)
            connection.commit()
        return build_catalog(self.data, "shop")

    def test_unique_names_map_to_codes_and_duplicate_names_stay_ambiguous(self):
        directory = load_staff_directory(self.catalog([
            ("NV001", "An"), ("NV002", "Bình"), ("NV003", "An"),
        ]))
        self.assertEqual(directory.names_by_code,
                         {"NV001": "An", "NV002": "Bình", "NV003": "An"})
        self.assertEqual(directory.codes_by_unique_name, {"binh": "NV002"})
        self.assertEqual(directory.ambiguous_names, ("an",))
        self.assertEqual(directory.source, "pos.sqlite::nhan_vien")

    def test_conflicting_duplicate_code_is_rejected(self):
        with self.assertRaises(StaffSourceError):
            load_staff_directory(self.catalog([("NV001", "An"), ("NV001", "Bình")]))

    def test_no_reviewed_directory_returns_empty_mapping(self):
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "README.md").write_text("# Sổ tay\n", encoding="utf-8")
        (root / "sales.csv").write_text("x\n1\n", encoding="utf-8")
        directory = load_staff_directory(build_catalog(self.data, "shop"))
        self.assertEqual(directory.names_by_code, {})
        self.assertEqual(directory.source, "")


if __name__ == "__main__":
    unittest.main()
