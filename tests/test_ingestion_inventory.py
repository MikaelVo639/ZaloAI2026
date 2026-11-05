import csv
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904

from datamate.ingestion.inventory import profile_business


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


class InventoryTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "train" / "data"
        self.business = self.data_root / "biz_dev"
        self.business.mkdir(parents=True)
        (self.business / "README.md").write_text("Synthetic fixture", encoding="utf-8")

    def _file(self, result, name):
        return next(item for item in result["files"] if item["source"].endswith("/" + name))

    def _table(self, file_result, name):
        return next(item for item in file_result["tables"] if item["name"] == name)

    def _column(self, table, name):
        return next(item for item in table["columns"] if item["raw_name"] == name)

    def test_full_profile_covers_csv_xlsx_and_read_only_sqlite_without_mutation(self):
        csv_path = self.business / "orders.csv"
        csv_path.write_text(
            "id,name,amount\n001,Alpha,0\n002,,12\n003,\"two\nlines\",12\n",
            encoding="utf-8-sig",
        )

        workbook_path = self.business / "book.xlsx"
        book = Workbook()
        sales = book.active
        sales.title = "Sales"
        sales.append(["when", "enabled", "quantity"])
        sales.append([date(2026, 1, 2), True, 0])
        sales.append([datetime(2026, 2, 3, 4, 5), False, None])
        book.create_sheet("Empty")
        book.save(workbook_path)
        book.close()

        database_path = self.business / "store.sqlite"
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, note TEXT, n REAL)")
            connection.executemany(
                "INSERT INTO facts VALUES (?, ?, ?)",
                [(1, None, 0), (2, "", 2.5), (3, "zero is data", 0)],
            )
            connection.execute("CREATE TABLE empty_table (code TEXT)")
            connection.commit()

        before = {path.name: file_sha256(path) for path in (csv_path, workbook_path, database_path)}
        result = profile_business(self.data_root, "biz_dev")
        after = {path.name: file_sha256(path) for path in (csv_path, workbook_path, database_path)}

        self.assertEqual(before, after)
        self.assertEqual(result["schema_version"], "inventory.v1")
        self.assertEqual(result["scan_scope"], "full")
        self.assertEqual([item["source"] for item in result["files"]], [
            "data/biz_dev/book.xlsx", "data/biz_dev/orders.csv", "data/biz_dev/store.sqlite"
        ])
        self.assertEqual(result["readme_sha256"], file_sha256(self.business / "README.md"))
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        self.assertNotIn('"samples"', json.dumps(result).casefold())

        csv_table = self._table(self._file(result, "orders.csv"), "orders")
        self.assertEqual(csv_table["row_count"], 3)
        self.assertEqual(csv_table["column_count"], 3)
        self.assertEqual(self._column(csv_table, "amount")["empty_string_count"], 0)
        self.assertEqual(self._column(csv_table, "name")["empty_string_count"], 1)
        self.assertEqual(self._column(csv_table, "id")["distinct_count"], 3)

        sales_table = self._table(self._file(result, "book.xlsx"), "Sales")
        self.assertEqual(sales_table["row_count"], 2)
        self.assertEqual(self._column(sales_table, "quantity")["null_count"], 1)
        self.assertEqual(self._column(sales_table, "quantity")["distinct_count"], 2)
        self.assertEqual(sales_table["date_coverage"][0]["observed_min"],
                         "2026-01-02T00:00:00")
        empty_sheet = self._table(self._file(result, "book.xlsx"), "Empty")
        self.assertEqual(empty_sheet["row_count"], 0)

        sqlite_file = self._file(result, "store.sqlite")
        facts = self._table(sqlite_file, "facts")
        self.assertEqual(facts["row_count"], 3)
        note = self._column(facts, "note")
        self.assertEqual((note["null_count"], note["empty_string_count"]), (1, 1))
        number = self._column(facts, "n")
        self.assertEqual(number["null_count"], 0)
        self.assertEqual(number["distinct_count"], 2)
        primary = next(key for key in facts["keys"] if key["columns"] == ["id"])
        self.assertEqual((primary["status"], primary["verification"]),
                         ("validated", "full_scan"))
        self.assertEqual(self._table(sqlite_file, "empty_table")["row_count"], 0)

    def test_sample_scope_keeps_full_counts_unknown_and_does_not_validate_uniqueness(self):
        path = self.business / "late_duplicate.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["id", "value"])
            for number in range(1, 101):
                writer.writerow([number, f"v{number}"])
            writer.writerow([1, "late duplicate"])

        sampled = profile_business(self.data_root, "biz_dev", scan_scope="sample")
        sample_table = self._table(self._file(sampled, "late_duplicate.csv"), "late_duplicate")
        self.assertIsNone(sample_table["row_count"])
        self.assertEqual(sample_table["observed_row_count"], 100)
        sample_id = self._column(sample_table, "id")
        self.assertIsNone(sample_id["null_count"])
        self.assertIsNone(sample_id["distinct_count"])
        self.assertEqual(sample_id["observed_distinct_count"], 100)
        sample_key = next(key for key in sample_table["keys"] if key["columns"] == ["id"])
        self.assertEqual((sample_key["status"], sample_key["verification"]),
                         ("candidate", "sample_only"))

        full = profile_business(self.data_root, "biz_dev", scan_scope="full")
        full_table = self._table(self._file(full, "late_duplicate.csv"), "late_duplicate")
        self.assertEqual(full_table["row_count"], 101)
        self.assertFalse(any(key["columns"] == ["id"] for key in full_table["keys"]))

    def test_encoding_delimiter_and_ambiguous_headers_have_explicit_diagnostics(self):
        encoded = "Ma\u0303;Ten\n001;Vi\u00ea\u0323t\n".encode("cp1258")
        (self.business / "vietnamese.csv").write_bytes(encoded)
        (self.business / "duplicate.csv").write_text("id,id\n1,2\n", encoding="utf-8")
        (self.business / "blank.csv").write_text("id,\n1,x\n", encoding="utf-8")
        (self.business / "blank_first.csv").write_text("\na,b\n1,2\n", encoding="utf-8")

        book = Workbook()
        sheet = book.active
        sheet.title = "Merged"
        sheet.merge_cells("A1:B1")
        sheet["A1"] = "Title"
        sheet.append([1, 2])
        formula = book.create_sheet("Formula")
        formula.append(["calculated"])
        formula.append(["=1+1"])
        book.save(self.business / "merged.xlsx")
        book.close()

        result = profile_business(self.data_root, "biz_dev")
        vietnamese = self._file(result, "vietnamese.csv")
        self.assertEqual(vietnamese["status"], "ok")
        self.assertEqual(vietnamese["encoding"], "cp1258")
        self.assertEqual(vietnamese["delimiter"], ";")
        self.assertEqual(self._table(vietnamese, "vietnamese")["row_count"], 1)
        for name in ("duplicate.csv", "blank.csv", "blank_first.csv"):
            file_result = self._file(result, name)
            self.assertEqual(file_result["status"], "error")
            table = file_result["tables"][0]
            self.assertEqual(table["diagnostics"][0]["code"], "HEADER_AMBIGUOUS")
        merged = self._table(self._file(result, "merged.xlsx"), "Merged")
        self.assertEqual(merged["status"], "error")
        self.assertEqual(merged["diagnostics"][0]["code"], "HEADER_AMBIGUOUS")
        formula = self._table(self._file(result, "merged.xlsx"), "Formula")
        self.assertEqual(formula["status"], "error")
        self.assertEqual(formula["diagnostics"][0]["code"], "FORMULA_CACHE_MISSING")

    def test_corrupt_ragged_and_unsupported_files_do_not_hide_good_results(self):
        (self.business / "good.csv").write_text("id\n1\n", encoding="utf-8")
        (self.business / "ragged.csv").write_text("a,b\n1,2,3\n", encoding="utf-8")
        (self.business / "broken.xlsx").write_bytes(b"not an xlsx")
        (self.business / "notes.txt").write_text("not tabular", encoding="utf-8")

        result = profile_business(self.data_root, "biz_dev")
        self.assertEqual(self._file(result, "good.csv")["status"], "ok")
        ragged = self._file(result, "ragged.csv")
        self.assertEqual(ragged["status"], "error")
        self.assertIsNone(ragged["tables"][0]["row_count"])
        self.assertEqual(self._file(result, "broken.xlsx")["diagnostics"][0]["code"],
                         "FILE_READ_ERROR")
        unsupported = self._file(result, "notes.txt")
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unsupported["diagnostics"][0]["code"], "UNSUPPORTED_FORMAT")

    def test_output_is_stable_across_roots_and_never_reads_answers(self):
        (self.business / "nested").mkdir()
        (self.business / "nested" / "same.csv").write_text("code\n001\n", encoding="utf-8")
        secret = self.business / "answers.jsonl"
        secret.write_bytes(b"\xffmust not decode or hash")

        copied_data = self.root / "other checkout" / "train" / "data"
        shutil.copytree(self.data_root, copied_data)
        first = profile_business(self.data_root, "biz_dev")
        second = profile_business(copied_data, "biz_dev")

        self.assertEqual(first, second)
        rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("must not decode", rendered)
        self.assertNotIn("answers.jsonl", [item["source"] for item in first["files"]])
        self.assertEqual(first["diagnostics"][0]["code"], "PROHIBITED_FILE_IGNORED")

    def test_empty_file_fallback_and_root_validation(self):
        (self.business / "empty.csv").write_bytes(b"")
        result = profile_business(self.data_root, "biz_dev")
        empty_file = self._file(result, "empty.csv")
        self.assertEqual(self._table(empty_file, "empty")["row_count"], 0)
        self.assertIn("DELIMITER_FALLBACK", {item["code"] for item in empty_file["diagnostics"]})

        with self.assertRaisesRegex(ValueError, "Missing data root"):
            profile_business(self.root / "missing", "biz_dev")
        with self.assertRaisesRegex(ValueError, "Invalid business_id"):
            profile_business(self.data_root, "../escape")
        with self.assertRaisesRegex(ValueError, "scan_scope"):
            profile_business(self.data_root, "biz_dev", scan_scope="partial")

    def test_source_changed_during_scan_is_not_reported_as_validated(self):
        source = self.business / "changing.csv"
        source.write_text("id\n1\n", encoding="utf-8")
        actual_hash = file_sha256(source)
        source_hash_calls = 0

        def simulated_hash(path):
            nonlocal source_hash_calls
            if path == source:
                source_hash_calls += 1
                return actual_hash if source_hash_calls == 1 else "f" * 64
            return file_sha256(path)

        with patch("datamate.ingestion.inventory._sha256", side_effect=simulated_hash):
            result = profile_business(self.data_root, "biz_dev")

        changed = self._file(result, "changing.csv")
        self.assertEqual(changed["status"], "error")
        self.assertEqual(changed["tables"], [])
        self.assertEqual(changed["diagnostics"][0]["code"], "INPUT_CHANGED")

    def test_tab_encoding_missing_readme_ragged_short_and_nested_same_basename(self):
        (self.business / "README.md").unlink()
        (self.business / "tab.csv").write_text("id\tname\n1\talpha\n", encoding="utf-8")
        (self.business / "bad_encoding.csv").write_bytes(b"id,name\n1,\x81\n")
        (self.business / "short.csv").write_text("a,b\n1\n", encoding="utf-8")
        (self.business / "same.csv").write_text("id\n1\n", encoding="utf-8")
        (self.business / "nested").mkdir()
        (self.business / "nested" / "same.csv").write_text("id\n1\n2\n", encoding="utf-8")

        result = profile_business(self.data_root, "biz_dev")

        self.assertIsNone(result["readme_sha256"])
        tab = self._file(result, "tab.csv")
        self.assertEqual(tab["delimiter"], "\t")
        self.assertEqual(tab["tables"][0]["row_count"], 1)
        malformed = self._file(result, "bad_encoding.csv")
        self.assertEqual(malformed["status"], "error")
        self.assertEqual(malformed["diagnostics"][0]["code"], "FILE_READ_ERROR")
        short = self._file(result, "short.csv")
        self.assertEqual(short["status"], "error")
        self.assertIsNone(short["tables"][0]["row_count"])
        sources = [item["source"] for item in result["files"] if item["source"].endswith("same.csv")]
        self.assertEqual(sources, ["data/biz_dev/nested/same.csv", "data/biz_dev/same.csv"])
        same_tables = [item["tables"][0] for item in result["files"]
                       if item["source"].endswith("same.csv")]
        self.assertEqual([table["row_count"] for table in same_tables], [2, 1])
        self.assertEqual(len({table["table_id"] for table in same_tables}), 2)

    def test_symlink_escape_is_rejected_when_platform_allows_symlinks(self):
        outside = self.root / "outside.csv"
        outside.write_text("id\n1\n", encoding="utf-8")
        link = self.business / "escape.csv"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            with self.assertRaisesRegex(ValueError, "Invalid business_id"):
                profile_business(self.data_root, "../escape")
            self.skipTest(f"symlink unavailable on this Windows host: {exc}")
        with self.assertRaisesRegex(ValueError, "escapes business directory"):
            profile_business(self.data_root, "biz_dev")

    def test_sqlite_declared_foreign_keys_views_and_quoted_unicode_identifiers(self):
        database = self.business / "relations.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                'CREATE TABLE "cha"" mẹ" ('
                '"mã" TEXT NOT NULL, "phiên" INTEGER NOT NULL, '
                'PRIMARY KEY ("mã", "phiên"))'
            )
            connection.execute(
                'CREATE TABLE "con"" út" ('
                '"mã cha" TEXT, "phiên cha" INTEGER, '
                'FOREIGN KEY ("mã cha", "phiên cha") '
                'REFERENCES "cha"" mẹ" ("mã", "phiên"))'
            )
            connection.execute('INSERT INTO "cha"" mẹ" VALUES ("A", 1)')
            connection.executemany(
                'INSERT INTO "con"" út" VALUES (?, ?)',
                [("A", 1), ("missing", 9), (None, 1)],
            )
            connection.execute(
                'CREATE VIEW "v"" tổng" AS SELECT "mã", "phiên" FROM "cha"" mẹ"'
            )
            connection.execute("CREATE TABLE loose_parent (a TEXT, b INTEGER)")
            connection.execute(
                "CREATE TABLE loose_child (a TEXT, b INTEGER, "
                "FOREIGN KEY (a, b) REFERENCES loose_parent (a, b))"
            )
            connection.executemany(
                "INSERT INTO loose_parent VALUES (?, ?)", [("dup", 1), ("dup", 1)]
            )
            connection.execute("INSERT INTO loose_child VALUES ('dup', 1)")
            connection.commit()
        before = file_sha256(database)

        result = profile_business(self.data_root, "biz_dev")
        repeated = profile_business(self.data_root, "biz_dev")

        self.assertEqual(file_sha256(database), before)
        self.assertEqual(result, repeated)
        sqlite_file = self._file(result, "relations.sqlite")
        self.assertEqual(sqlite_file["status"], "partial")
        child = self._table(sqlite_file, 'con" út')
        foreign_key = child["foreign_keys"][0]
        self.assertEqual(foreign_key["columns"], ["mã cha", "phiên cha"])
        self.assertEqual(foreign_key["referenced_table"], 'cha" mẹ')
        self.assertEqual(foreign_key["referenced_columns"], ["mã", "phiên"])
        self.assertEqual(foreign_key["relationship_type"], "declared_foreign_key")
        self.assertFalse(foreign_key["inferred"])
        self.assertTrue(foreign_key["parent_key_eligible"])
        self.assertEqual(foreign_key["checked_row_count"], 2)
        self.assertEqual(foreign_key["null_key_count"], 1)
        self.assertEqual(foreign_key["orphan_count"], 1)
        self.assertEqual(foreign_key["match_ratio"], 0.5)
        self.assertEqual((foreign_key["status"], foreign_key["verification"]),
                         ("violated", "full_scan"))
        self.assertIn("FK_ORPHAN_ROWS", {item["code"] for item in child["diagnostics"]})

        loose_child = self._table(sqlite_file, "loose_child")
        loose_fk = loose_child["foreign_keys"][0]
        self.assertFalse(loose_fk["parent_key_eligible"])
        self.assertEqual(loose_fk["parent_duplicate_key_count"], 1)
        self.assertEqual(
            {"FK_PARENT_KEY_NOT_UNIQUE", "FK_PARENT_KEY_DUPLICATE"},
            {item["code"] for item in loose_child["diagnostics"]},
        )
        view = self._table(sqlite_file, 'v" tổng')
        self.assertEqual((view["object_type"], view["status"]), ("view", "unsupported"))
        self.assertIsNone(view["row_count"])
        self.assertEqual(view["diagnostics"][0]["code"], "SQLITE_VIEW_UNSUPPORTED")

        sampled = profile_business(self.data_root, "biz_dev", scan_scope="sample")
        sampled_fk = self._table(self._file(sampled, "relations.sqlite"), 'con" út')[
            "foreign_keys"
        ][0]
        self.assertEqual((sampled_fk["status"], sampled_fk["verification"]),
                         ("declared", "sample_not_full_scan"))
        self.assertIsNone(sampled_fk["orphan_count"])
        self.assertIsNone(sampled_fk["checked_row_count"])

    def test_excel_number_formats_and_1900_1904_serial_dates(self):
        windows_path = self.business / "windows_epoch.xlsx"
        windows_book = Workbook()
        windows_sheet = windows_book.active
        windows_sheet.title = "Data"
        windows_sheet.append(["identifier", "event_date", "plain_number"])
        windows_sheet.append([12, 1, 1])
        windows_sheet["A2"].number_format = "00000"
        windows_sheet["B2"].number_format = "yyyy-mm-dd"
        windows_book.save(windows_path)
        windows_book.close()

        mac_path = self.business / "mac_epoch.xlsx"
        mac_book = Workbook()
        mac_book.epoch = CALENDAR_MAC_1904
        mac_sheet = mac_book.active
        mac_sheet.title = "Data"
        mac_sheet.append(["event_date", "plain_number"])
        mac_sheet.append([1, 1])
        mac_sheet["A2"].number_format = "yyyy-mm-dd"
        mac_book.save(mac_path)
        mac_book.close()

        result = profile_business(self.data_root, "biz_dev")

        windows = self._file(result, "windows_epoch.xlsx")
        self.assertEqual(windows["date_system"], "1899-12-30")
        windows_table = self._table(windows, "Data")
        identifier = self._column(windows_table, "identifier")
        self.assertEqual(identifier["logical_type_candidate"], "integer")
        self.assertEqual(identifier["physical_types"], [{"type": "integer", "count": 1}])
        self.assertEqual(identifier["number_formats"], [
            {"format_code": "00000", "observed_cell_count": 1}
        ])
        self.assertEqual(identifier["number_format_evidence"], {
            "scan_scope": "full", "coverage": "all_observed_rows", "observed_row_count": 1
        })
        self.assertEqual(windows_table["date_coverage"], [{
            "column": "event_date",
            "observed_min": "1900-01-01T00:00:00",
            "observed_max": "1900-01-01T00:00:00",
            "declared_start": None,
            "declared_end": None,
            "scan_scope": "full",
        }])
        plain = self._column(windows_table, "plain_number")
        self.assertEqual(plain["logical_type_candidate"], "integer")

        mac = self._file(result, "mac_epoch.xlsx")
        self.assertEqual(mac["date_system"], "1904-01-01")
        mac_table = self._table(mac, "Data")
        self.assertEqual(mac_table["date_coverage"][0]["observed_min"],
                         "1904-01-02T00:00:00")
        self.assertEqual(self._column(mac_table, "plain_number")["logical_type_candidate"],
                         "integer")

        sampled = profile_business(self.data_root, "biz_dev", scan_scope="sample")
        sampled_identifier = self._column(
            self._table(self._file(sampled, "windows_epoch.xlsx"), "Data"), "identifier"
        )
        self.assertEqual(sampled_identifier["number_format_evidence"]["coverage"],
                         "sample_observed_rows")
        self.assertEqual(sampled_identifier["number_format_evidence"]["scan_scope"], "sample")


if __name__ == "__main__":
    unittest.main()
