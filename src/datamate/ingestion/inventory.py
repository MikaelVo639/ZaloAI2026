"""Deterministic, read-only physical inventory for one business.

The inventory is a sidecar to :mod:`datamate.ingestion.catalog`: it preserves the
existing ``Catalog`` and ``iter_rows`` interfaces while adding full-scan evidence.
It deliberately does not infer business semantics or expose raw cell samples.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile

from datamate.contracts import Table
from datamate.ingestion.catalog import (
    _csv_settings,
    _headers,
    iter_rows,
    quote_identifier,
    sqlite_connection,
)
from datamate.io import contained_path

_SAMPLE_ROW_LIMIT = 100
_SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
_SUPPORTED_SUFFIXES = {".csv", ".xlsx", *_SQLITE_SUFFIXES}
_TYPE_ORDER = ("null", "boolean", "integer", "number", "datetime", "date", "time",
               "string", "binary", "other")
_CELL_RANGE = re.compile(r"\$?[A-Z]+\$?(\d+)(?::\$?[A-Z]+\$?(\d+))?")


class _TableReadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _diagnostic(code: str, message: str, *, severity: str = "error") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return "_".join(normalized.split())


def _physical_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bytes):
        return "binary"
    return "other"


def _distinct_token(value: Any) -> tuple[str, str]:
    kind = _physical_type(value)
    if isinstance(value, (date, datetime, time)):
        rendered = value.isoformat()
    elif isinstance(value, bytes):
        rendered = hashlib.sha256(value).hexdigest()
    else:
        rendered = repr(value)
    return kind, rendered


def _logical_type(types: Counter[str]) -> str:
    observed = set(types) - {"null"}
    if not observed:
        return "unknown"
    if observed <= {"integer", "number"}:
        return "integer" if observed == {"integer"} else "number"
    if observed <= {"date", "datetime"}:
        return "date" if observed == {"date"} else "datetime"
    return next(iter(observed)) if len(observed) == 1 else "mixed"


def _column_shell(ordinal: int, raw_name: str, scan_scope: str) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "raw_name": raw_name,
        "normalized_name": _normalized_name(raw_name),
        "physical_types": [],
        "logical_type_candidate": "unknown",
        "null_count": None,
        "empty_string_count": None,
        "distinct_count": None,
        "parse_failure_count": None,
        "observed_null_count": None,
        "observed_empty_string_count": None,
        "observed_distinct_count": None,
        "observed_parse_failure_count": None,
        "unit_candidate": None,
        "number_formats": [],
        "number_format_evidence": None,
        "confidence_status": "unavailable",
        "evidence": {"scan_scope": scan_scope, "observed_row_count": 0},
    }


def _unavailable_table(name: str, table_id: str, raw_names: list[str], scan_scope: str,
                       diagnostics: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "name": name,
        "table_id": table_id,
        "object_type": "table",
        "status": "error",
        "header_rows": [1] if raw_names else [],
        "row_count": None,
        "observed_row_count": 0,
        "column_count": len(raw_names),
        "columns": [_column_shell(index + 1, value, scan_scope)
                    for index, value in enumerate(raw_names)],
        "keys": [],
        "foreign_keys": [],
        "date_coverage": None,
        "diagnostics": diagnostics,
    }


def _validate_headers(values: tuple[Any, ...]) -> tuple[list[str], tuple[str, ...]]:
    raw_names = ["" if value is None else str(value) for value in values]
    if any(not value.strip() for value in raw_names):
        raise _TableReadError("HEADER_AMBIGUOUS", "Header contains a blank column name")
    try:
        columns = _headers(values)
    except ValueError as exc:
        raise _TableReadError("HEADER_AMBIGUOUS", "Header contains duplicate column names") from exc
    normalized = [_normalized_name(value) for value in raw_names]
    if len(set(normalized)) != len(normalized):
        raise _TableReadError(
            "HEADER_AMBIGUOUS", "Header names are ambiguous after search normalization"
        )
    return raw_names, columns


def _profile_rows(table: Table, raw_names: list[str], scan_scope: str,
                  declared_keys: list[list[str]] | None = None,
                  column_metadata: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    states = [{"types": Counter(), "null": 0, "empty": 0, "distinct": set(),
               "dates": []} for _ in table.columns]
    declared_keys = declared_keys or []
    key_states = [{"columns": columns, "null": 0, "distinct": set(), "non_null": 0}
                  for columns in declared_keys]
    observed_rows = 0
    reader = iter_rows(table)
    rows: Iterable[dict[str, Any]] = reader
    if scan_scope == "sample":
        rows = itertools.islice(reader, _SAMPLE_ROW_LIMIT)
    try:
        for row in rows:
            if table.path.suffix.lower() == ".csv" and any(
                    row.get(column) is None for column in table.columns):
                raise _TableReadError("RAGGED_ROW", "CSV row has fewer fields than its header")
            observed_rows += 1
            for state, column in zip(states, table.columns):
                value = row.get(column)
                kind = _physical_type(value)
                state["types"][kind] += 1
                state["distinct"].add(_distinct_token(value))
                if value is None:
                    state["null"] += 1
                elif isinstance(value, str) and value == "":
                    state["empty"] += 1
                if isinstance(value, (date, datetime)):
                    state["dates"].append(value.isoformat())
            for key_state in key_states:
                values = tuple(row.get(column) for column in key_state["columns"])
                if any(value is None for value in values):
                    key_state["null"] += 1
                else:
                    key_state["non_null"] += 1
                    key_state["distinct"].add(tuple(_distinct_token(value) for value in values))
    except _TableReadError:
        raise
    except (ValueError, sqlite3.Error) as exc:
        if "Ragged CSV row" in str(exc):
            raise _TableReadError("RAGGED_ROW", "CSV row has more fields than its header") from exc
        raise _TableReadError("TABLE_READ_ERROR", "Table could not be read") from exc
    finally:
        close = getattr(reader, "close", None)
        if close is not None:
            close()

    columns = []
    date_coverage = []
    for ordinal, (raw_name, state) in enumerate(zip(raw_names, states), 1):
        full = scan_scope == "full"
        physical_types = [{"type": kind, "count": state["types"][kind]}
                          for kind in _TYPE_ORDER if state["types"][kind]]
        column = {
            "ordinal": ordinal,
            "raw_name": raw_name,
            "normalized_name": _normalized_name(raw_name),
            "physical_types": physical_types,
            "logical_type_candidate": _logical_type(state["types"]),
            "null_count": state["null"] if full else None,
            "empty_string_count": state["empty"] if full else None,
            "distinct_count": len(state["distinct"]) if full else None,
            "parse_failure_count": 0 if full else None,
            "observed_null_count": state["null"],
            "observed_empty_string_count": state["empty"],
            "observed_distinct_count": len(state["distinct"]),
            "observed_parse_failure_count": 0,
            "unit_candidate": None,
            "number_formats": [],
            "number_format_evidence": None,
            "confidence_status": "validated" if full else "observed_sample",
            "evidence": {"scan_scope": scan_scope, "observed_row_count": observed_rows},
        }
        if column_metadata is not None:
            column.update(column_metadata[ordinal - 1])
        columns.append(column)
        if state["dates"]:
            date_coverage.append({
                "column": raw_name,
                "observed_min": min(state["dates"]),
                "observed_max": max(state["dates"]),
                "declared_start": None,
                "declared_end": None,
                "scan_scope": scan_scope,
            })

    keys = []
    declared = {tuple(columns) for columns in declared_keys}
    for raw_name, table_name, state in zip(raw_names, table.columns, states):
        if (table_name,) in declared or observed_rows == 0:
            continue
        duplicates = observed_rows - len(state["distinct"])
        if state["null"] == 0 and state["empty"] == 0 and duplicates == 0:
            keys.append({
                "columns": [raw_name],
                "null_count": 0,
                "duplicate_key_count": 0,
                "scan_scope": scan_scope,
                "status": "candidate",
                "verification": "full_scan" if scan_scope == "full" else "sample_only",
                "evidence": "observed_unique_non_blank_values",
            })
    for key_state, key_columns in zip(key_states, declared_keys):
        duplicates = key_state["non_null"] - len(key_state["distinct"])
        valid = scan_scope == "full" and key_state["null"] == 0 and duplicates == 0
        raw_key_names = [raw_names[table.columns.index(column)] for column in key_columns]
        keys.append({
            "columns": raw_key_names,
            "null_count": key_state["null"] if scan_scope == "full" else None,
            "duplicate_key_count": duplicates if scan_scope == "full" else None,
            "observed_null_count": key_state["null"],
            "observed_duplicate_key_count": duplicates,
            "scan_scope": scan_scope,
            "status": "validated" if valid else "declared",
            "verification": "full_scan" if scan_scope == "full" else "sample_only",
            "evidence": "sqlite_primary_key",
        })
    keys.sort(key=lambda item: (item["columns"], item["status"]))
    return {
        "name": table.name,
        "table_id": "",  # caller supplies the source-relative identity
        "object_type": "table",
        "status": "ok",
        "header_rows": [1] if raw_names else [],
        "row_count": observed_rows if scan_scope == "full" else None,
        "observed_row_count": observed_rows,
        "column_count": len(raw_names),
        "columns": columns,
        "keys": keys,
        "foreign_keys": [],
        "date_coverage": date_coverage or None,
        "diagnostics": [],
    }


def _merged_header_sheets(path: Path) -> set[str]:
    """Inspect merge declarations without loading a potentially large workbook in memory."""
    import xml.etree.ElementTree as element_tree

    merged = set()
    with ZipFile(path) as archive:
        workbook = element_tree.fromstring(archive.read("xl/workbook.xml"))
        relations = element_tree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relation_targets = {item.attrib["Id"]: item.attrib["Target"] for item in relations}
        relationship_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        for sheet in workbook.iter():
            if not sheet.tag.endswith("}sheet"):
                continue
            target = relation_targets.get(sheet.attrib.get(relationship_key, ""), "")
            if not target:
                continue
            member = target.lstrip("/")
            if not member.startswith("xl/"):
                member = "xl/" + member
            with archive.open(member) as stream:
                for _, node in element_tree.iterparse(stream, events=("start",)):
                    if not node.tag.endswith("}mergeCell"):
                        continue
                    match = _CELL_RANGE.fullmatch(node.attrib.get("ref", ""))
                    if match and int(match.group(1)) <= 1 <= int(match.group(2) or match.group(1)):
                        merged.add(sheet.attrib["name"])
                        break
    return merged


def _empty_table(name: str, table_id: str, scan_scope: str) -> dict[str, Any]:
    return {
        "name": name,
        "table_id": table_id,
        "object_type": "table",
        "status": "ok",
        "header_rows": [],
        "row_count": 0 if scan_scope == "full" else None,
        "observed_row_count": 0,
        "column_count": 0,
        "columns": [],
        "keys": [],
        "foreign_keys": [],
        "date_coverage": None,
        "diagnostics": [_diagnostic("EMPTY_TABLE", "Table has no header or data", severity="info")],
    }


def _formula_cache_missing(formula_sheet: Any, cached_sheet: Any, scan_scope: str) -> bool:
    formula_rows = formula_sheet.iter_rows()
    cached_rows = cached_sheet.iter_rows()
    paired_rows: Iterable[tuple[Any, Any]] = zip(formula_rows, cached_rows)
    if scan_scope == "sample":
        paired_rows = itertools.islice(paired_rows, _SAMPLE_ROW_LIMIT + 1)
    try:
        for formula_row, cached_row in paired_rows:
            for formula_cell, cached_cell in zip(formula_row, cached_row):
                if formula_cell.data_type == "f" and cached_cell.value is None:
                    return True
    finally:
        formula_rows.close()
        cached_rows.close()
    return False


def _excel_column_metadata(sheet: Any, column_count: int,
                           scan_scope: str) -> list[dict[str, Any]]:
    format_counts = [Counter() for _ in range(column_count)]
    observed_rows = 0
    rows = sheet.iter_rows(min_row=2, max_col=column_count)
    try:
        for row in rows:
            if not any(cell.value is not None for cell in row):
                continue
            if scan_scope == "sample" and observed_rows >= _SAMPLE_ROW_LIMIT:
                break
            observed_rows += 1
            for index, cell in enumerate(row):
                if cell.value is not None:
                    format_counts[index][cell.number_format or "General"] += 1
    finally:
        rows.close()
    coverage = "all_observed_rows" if scan_scope == "full" else "sample_observed_rows"
    return [{
        "number_formats": [
            {"format_code": code, "observed_cell_count": counts[code]}
            for code in sorted(counts)
        ],
        "number_format_evidence": {
            "scan_scope": scan_scope,
            "coverage": coverage,
            "observed_row_count": observed_rows,
        },
    } for counts in format_counts]


def _profile_csv(path: Path, table_id: str, scan_scope: str) -> tuple[list[dict[str, Any]],
                                                                       dict[str, Any]]:
    encoding, delimiter = _csv_settings(path)
    diagnostics = []
    with path.open(encoding=encoding, newline="") as stream:
        sample = stream.read(65536)
    try:
        csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        diagnostics.append(_diagnostic(
            "DELIMITER_FALLBACK", "Delimiter sniffing failed; comma fallback was used",
            severity="warning",
        ))
    with path.open(encoding=encoding, newline="") as stream:
        rows = csv.reader(stream, delimiter=delimiter)
        first = next(rows, None)
        later_non_empty = first is not None and not any(value != "" for value in first) and any(
            any(value != "" for value in row) for row in rows
        )
    name = path.stem
    if first is None or (not any(value != "" for value in first) and not later_non_empty):
        return [_empty_table(name, table_id, scan_scope)], {
            "encoding": encoding, "delimiter": delimiter, "diagnostics": diagnostics
        }
    try:
        if later_non_empty:
            raise _TableReadError("HEADER_AMBIGUOUS", "Data follows a blank header row")
        raw_names, columns = _validate_headers(tuple(first))
    except _TableReadError as exc:
        raw_names = ["" if value is None else str(value) for value in first]
        result = _unavailable_table(
            name, table_id, raw_names, scan_scope, [_diagnostic(exc.code, str(exc))]
        )
        return [result], {"encoding": encoding, "delimiter": delimiter,
                          "diagnostics": diagnostics}
    table = Table(path, name, columns, encoding=encoding, delimiter=delimiter)
    try:
        result = _profile_rows(table, raw_names, scan_scope)
        result["table_id"] = table_id
    except _TableReadError as exc:
        result = _unavailable_table(
            name, table_id, raw_names, scan_scope, [_diagnostic(exc.code, str(exc))]
        )
    return [result], {"encoding": encoding, "delimiter": delimiter,
                      "diagnostics": diagnostics}


def _profile_xlsx(path: Path, source: str, scan_scope: str) -> tuple[list[dict[str, Any]],
                                                                    dict[str, Any]]:
    from openpyxl import load_workbook

    merged_headers = _merged_header_sheets(path)
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    formula_book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    tables = []
    try:
        date_system = book.epoch.date().isoformat()
        for sheet in sorted(book.worksheets, key=lambda item: item.title):
            table_id = f"{source}::{sheet.title}"
            reader = sheet.iter_rows(values_only=True)
            first = next(reader, None)
            later_non_empty = first is not None and not any(
                value is not None for value in first
            ) and any(any(value is not None for value in row) for row in reader)
            reader.close()
            if first is None or (not any(value is not None for value in first)
                                 and not later_non_empty):
                tables.append(_empty_table(sheet.title, table_id, scan_scope))
                continue
            raw_for_error = ["" if value is None else str(value) for value in first]
            try:
                if later_non_empty:
                    raise _TableReadError("HEADER_AMBIGUOUS", "Data follows a blank header row")
                if sheet.title in merged_headers:
                    raise _TableReadError(
                        "HEADER_AMBIGUOUS", "Header row intersects a merged cell range"
                    )
                raw_names, columns = _validate_headers(tuple(first))
                if _formula_cache_missing(
                        formula_book[sheet.title], sheet, scan_scope):
                    raise _TableReadError(
                        "FORMULA_CACHE_MISSING",
                        "Formula cell has no cached value and cannot be counted as null",
                    )
                table = Table(path, sheet.title, columns)
                column_metadata = _excel_column_metadata(sheet, len(columns), scan_scope)
                result = _profile_rows(
                    table, raw_names, scan_scope, column_metadata=column_metadata
                )
                result["table_id"] = table_id
                tables.append(result)
            except _TableReadError as exc:
                tables.append(_unavailable_table(
                    sheet.title, table_id, raw_for_error, scan_scope,
                    [_diagnostic(exc.code, str(exc))],
                ))
    finally:
        book.close()
        formula_book.close()
    return tables, {"date_system": date_system, "diagnostics": []}


def _sqlite_declared_keys(connection: sqlite3.Connection, table_name: str) -> list[list[str]]:
    columns = connection.execute(
        f"PRAGMA table_info({quote_identifier(table_name)})"
    ).fetchall()
    primary = sorted(((row[5], row[1]) for row in columns if row[5]), key=lambda item: item[0])
    return [[name for _, name in primary]] if primary else []


def _sqlite_unique_keys(connection: sqlite3.Connection, table_name: str) -> set[tuple[str, ...]]:
    keys = {tuple(columns) for columns in _sqlite_declared_keys(connection, table_name)}
    indexes = connection.execute(
        f"PRAGMA index_list({quote_identifier(table_name)})"
    ).fetchall()
    for index in indexes:
        if not index[2] or (len(index) > 4 and index[4]):
            continue
        columns = connection.execute(
            f"PRAGMA index_info({quote_identifier(index[1])})"
        ).fetchall()
        ordered = tuple(row[2] for row in sorted(columns, key=lambda row: row[0]))
        if ordered and all(column is not None for column in ordered):
            keys.add(ordered)
    return keys


def _sqlite_scalar(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0])


def _sqlite_foreign_keys(connection: sqlite3.Connection, table_name: str,
                         scan_scope: str) -> tuple[list[dict[str, Any]],
                                                   list[dict[str, str]]]:
    rows = connection.execute(
        f"PRAGMA foreign_key_list({quote_identifier(table_name)})"
    ).fetchall()
    grouped: dict[int, list[tuple[Any, ...]]] = {}
    for row in rows:
        grouped.setdefault(int(row[0]), []).append(row)
    foreign_keys = []
    diagnostics = []
    for constraint_id in sorted(grouped):
        parts = sorted(grouped[constraint_id], key=lambda row: row[1])
        parent_table = str(parts[0][2])
        child_columns = [str(row[3]) for row in parts]
        parent_columns = [None if row[4] is None else str(row[4]) for row in parts]
        parent_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (parent_table,)
        ).fetchone() is not None
        if parent_exists and any(column is None for column in parent_columns):
            implicit = _sqlite_declared_keys(connection, parent_table)
            if len(implicit) == 1 and len(implicit[0]) == len(parent_columns):
                parent_columns = list(implicit[0])
        resolved_parent_columns = [column for column in parent_columns if column is not None]
        parent_column_names = set()
        if parent_exists:
            parent_column_names = {str(row[1]) for row in connection.execute(
                f"PRAGMA table_info({quote_identifier(parent_table)})"
            )}
        resolvable = (
            parent_exists
            and len(resolved_parent_columns) == len(parent_columns)
            and all(column in parent_column_names for column in resolved_parent_columns)
        )
        eligible = resolvable and tuple(resolved_parent_columns) in _sqlite_unique_keys(
            connection, parent_table
        )
        checked_row_count = None
        null_key_count = None
        orphan_count = None
        parent_duplicate_key_count = None
        match_ratio = None
        status = "declared"
        verification = "sample_not_full_scan"
        if not resolvable:
            diagnostics.append(_diagnostic(
                "FK_UNVERIFIABLE",
                f"Declared foreign key {constraint_id} has an unresolved parent table or column",
            ))
            verification = "unresolvable_parent"
        elif not eligible:
            diagnostics.append(_diagnostic(
                "FK_PARENT_KEY_NOT_UNIQUE",
                f"Declared foreign key {constraint_id} does not reference a declared unique key",
            ))
            verification = "parent_key_not_declared_unique"

        if scan_scope == "full" and resolvable:
            parent = quote_identifier(parent_table)
            parent_cols = [quote_identifier(column) for column in resolved_parent_columns]
            child_cols = [quote_identifier(column) for column in child_columns]
            parent_not_null = " AND ".join(f"{column} IS NOT NULL" for column in parent_cols)
            grouped_parent = ", ".join(parent_cols)
            duplicate_query = (
                "SELECT COALESCE(SUM(group_size - 1), 0) FROM ("
                f"SELECT COUNT(*) AS group_size FROM {parent} WHERE {parent_not_null} "
                f"GROUP BY {grouped_parent} HAVING COUNT(*) > 1)"
            )
            parent_duplicate_key_count = _sqlite_scalar(connection, duplicate_query)
            if parent_duplicate_key_count:
                diagnostics.append(_diagnostic(
                    "FK_PARENT_KEY_DUPLICATE",
                    f"Declared foreign key {constraint_id} has duplicate parent key rows",
                ))
            child = quote_identifier(table_name)
            child_not_null = " AND ".join(f"c.{column} IS NOT NULL" for column in child_cols)
            child_has_null = " OR ".join(f"c.{column} IS NULL" for column in child_cols)
            checked_row_count = _sqlite_scalar(
                connection, f"SELECT COUNT(*) FROM {child} AS c WHERE {child_not_null}"
            )
            null_key_count = _sqlite_scalar(
                connection, f"SELECT COUNT(*) FROM {child} AS c WHERE {child_has_null}"
            )
            comparisons = " AND ".join(
                f"p.{parent_column} = c.{child_column}"
                for child_column, parent_column in zip(child_cols, parent_cols)
            )
            orphan_count = _sqlite_scalar(
                connection,
                f"SELECT COUNT(*) FROM {child} AS c WHERE {child_not_null} "
                f"AND NOT EXISTS (SELECT 1 FROM {parent} AS p WHERE {comparisons})",
            )
            match_ratio = (checked_row_count - orphan_count) / checked_row_count \
                if checked_row_count else None
            if orphan_count:
                diagnostics.append(_diagnostic(
                    "FK_ORPHAN_ROWS",
                    f"Declared foreign key {constraint_id} has {orphan_count} orphan row(s)",
                ))
            if eligible and parent_duplicate_key_count == 0:
                status = "validated" if orphan_count == 0 else "violated"
                verification = "full_scan"
            else:
                verification = "full_scan_unverifiable_parent"

        foreign_keys.append({
            "constraint_id": constraint_id,
            "columns": child_columns,
            "referenced_table": parent_table,
            "referenced_columns": parent_columns,
            "on_update": str(parts[0][5]),
            "on_delete": str(parts[0][6]),
            "match": str(parts[0][7]),
            "relationship_type": "declared_foreign_key",
            "origin": "sqlite_ddl",
            "inferred": False,
            "scan_scope": scan_scope,
            "status": status,
            "verification": verification,
            "parent_key_eligible": eligible,
            "checked_row_count": checked_row_count,
            "null_key_count": null_key_count,
            "orphan_count": orphan_count,
            "match_ratio": match_ratio,
            "parent_duplicate_key_count": parent_duplicate_key_count,
        })
    return foreign_keys, diagnostics


def _unsupported_sqlite_view(name: str, table_id: str, columns: tuple[str, ...],
                             scan_scope: str) -> dict[str, Any]:
    result = _unavailable_table(
        name, table_id, list(columns), scan_scope,
        [_diagnostic(
            "SQLITE_VIEW_UNSUPPORTED",
            "SQLite view is inventoried but row profiling is not supported",
            severity="warning",
        )],
    )
    result["object_type"] = "view"
    result["status"] = "unsupported"
    return result


def _profile_sqlite(path: Path, source: str, scan_scope: str) -> tuple[list[dict[str, Any]],
                                                                      dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    file_diagnostics = []
    with closing(sqlite_connection(path)) as connection:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for object_type, name in objects:
            columns = tuple(row[1] for row in connection.execute(
                f"PRAGMA table_info({quote_identifier(name)})"
            ))
            if object_type == "view":
                definitions.append({
                    "object_type": "view", "name": name, "columns": columns,
                    "declared_keys": [], "foreign_keys": [], "diagnostics": [],
                })
                file_diagnostics.append(_diagnostic(
                    "SQLITE_VIEWS_UNSUPPORTED",
                    "One or more SQLite views were inventoried without row profiling",
                    severity="warning",
                ))
                continue
            foreign_keys, diagnostics = _sqlite_foreign_keys(
                connection, name, scan_scope
            )
            definitions.append({
                "object_type": "table",
                "name": name,
                "columns": columns,
                "declared_keys": _sqlite_declared_keys(connection, name),
                "foreign_keys": foreign_keys,
                "diagnostics": diagnostics,
            })
    tables = []
    for definition in definitions:
        name = definition["name"]
        columns = definition["columns"]
        table_id = f"{source}::{name}"
        if definition["object_type"] == "view":
            tables.append(_unsupported_sqlite_view(name, table_id, columns, scan_scope))
            continue
        table = Table(path, name, columns)
        try:
            result = _profile_rows(
                table, list(columns), scan_scope, definition["declared_keys"]
            )
            result["table_id"] = table_id
            result["foreign_keys"] = definition["foreign_keys"]
            result["diagnostics"].extend(definition["diagnostics"])
            tables.append(result)
        except _TableReadError as exc:
            tables.append(_unavailable_table(
                name, table_id, list(columns), scan_scope,
                [_diagnostic(exc.code, str(exc))],
            ))
    return tables, {"diagnostics": file_diagnostics}


def _profile_file(path: Path, source: str, scan_scope: str) -> dict[str, Any]:
    suffix = path.suffix.lower()
    base: dict[str, Any] = {
        "source": source,
        "sha256": None,
        "size_bytes": None,
        "format": suffix.lstrip(".") or "unknown",
        "status": "error",
        "tables": [],
        "diagnostics": [],
    }
    try:
        before_hash = _sha256(path)
        base["sha256"] = before_hash
        base["size_bytes"] = path.stat().st_size
        if suffix not in _SUPPORTED_SUFFIXES:
            base["status"] = "unsupported"
            base["diagnostics"] = [_diagnostic(
                "UNSUPPORTED_FORMAT", "File format is not supported by inventory.v1",
                severity="warning",
            )]
            return base
        if suffix == ".csv":
            tables, metadata = _profile_csv(path, f"{source}::{path.stem}", scan_scope)
        elif suffix == ".xlsx":
            tables, metadata = _profile_xlsx(path, source, scan_scope)
        else:
            tables, metadata = _profile_sqlite(path, source, scan_scope)
        after_hash = _sha256(path)
        if before_hash != after_hash:
            base["diagnostics"] = [_diagnostic(
                "INPUT_CHANGED", "Source checksum changed while it was being scanned"
            )]
            return base
        base.update({key: value for key, value in metadata.items() if key != "diagnostics"})
        base["tables"] = sorted(tables, key=lambda item: item["name"])
        table_errors = [table for table in tables if table["status"] == "error"]
        unsupported_objects = [table for table in tables if table["status"] == "unsupported"]
        if table_errors:
            base["status"] = "error" if len(table_errors) == len(tables) else "partial"
            base["diagnostics"] = metadata["diagnostics"] + [_diagnostic(
                "TABLE_ERRORS", f"{len(table_errors)} table(s) could not be profiled"
            )]
        elif unsupported_objects:
            base["status"] = "partial"
            base["diagnostics"] = metadata["diagnostics"]
        else:
            base["status"] = "ok"
            base["diagnostics"] = metadata["diagnostics"]
    except (OSError, UnicodeError, csv.Error, sqlite3.Error, ValueError, KeyError,
            BadZipFile) as exc:
        code = exc.code if isinstance(exc, _TableReadError) else "FILE_READ_ERROR"
        base["diagnostics"] = [_diagnostic(code, "File could not be profiled")]
    return base


def _manifest_sha(files: list[dict[str, Any]]) -> str:
    identity = [{"source": item["source"], "sha256": item["sha256"],
                 "size_bytes": item["size_bytes"]} for item in files]
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def profile_business(data_root: Path, business_id: str, *,
                     scan_scope: str = "full") -> dict[str, Any]:
    """Return an ``inventory.v1`` physical profile without modifying source data.

    ``data_root`` is the directory containing business directories (normally the
    split's ``data`` directory). ``business_id`` is routing metadata only.
    """
    data_root = Path(data_root)
    if not data_root.is_dir():
        raise ValueError("Missing data root")
    if (not business_id or business_id in {".", ".."} or Path(business_id).is_absolute()
            or "/" in business_id or "\\" in business_id):
        raise ValueError("Invalid business_id")
    if scan_scope not in {"full", "sample"}:
        raise ValueError("scan_scope must be 'full' or 'sample'")
    business_root = contained_path(data_root, business_id)
    if not business_root.is_dir():
        raise ValueError("Missing business directory")

    readme = business_root / "README.md"
    if readme.exists() and not readme.resolve().is_relative_to(business_root.resolve()):
        raise ValueError("README path escapes business directory")
    readme_sha256 = _sha256(readme) if readme.is_file() else None

    paths = []
    diagnostics = []
    for path in business_root.rglob("*"):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(business_root.resolve()):
            raise ValueError("Source path escapes business directory")
        if path.name.casefold() == "readme.md":
            continue
        if path.name.casefold() == "answers.jsonl":
            diagnostics.append(_diagnostic(
                "PROHIBITED_FILE_IGNORED", "answers.jsonl was not read by inventory",
                severity="warning",
            ))
            continue
        paths.append(path)

    files = []
    for path in sorted(paths, key=lambda item: item.relative_to(business_root).as_posix()):
        relative = path.relative_to(business_root).as_posix()
        source = f"data/{business_id}/{relative}"
        files.append(_profile_file(path, source, scan_scope))
    files.sort(key=lambda item: item["source"])
    result = {
        "schema_version": "inventory.v1",
        "scan_scope": scan_scope,
        "source_manifest_sha256": _manifest_sha(files),
        "business_id": business_id,
        "files": files,
        "readme_sha256": readme_sha256,
        "diagnostics": diagnostics,
    }
    # Enforce the public boundary now, so non-finite or non-serializable values do
    # not escape only to fail in a downstream writer.
    json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return result
