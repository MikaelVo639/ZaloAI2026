"""Read raw values without guessing types, units or joins."""

import csv
import itertools
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from datamate.contracts import Catalog, Table
from datamate.io import contained_path


def _headers(values: tuple[Any, ...]) -> tuple[str, ...]:
    headers = tuple(str(x).strip() if x is not None else f"_empty_{i}"
                    for i, x in enumerate(values))
    if len(set(headers)) != len(headers):
        raise ValueError(f"Duplicate column names: {headers}")
    return headers


def _csv_settings(path: Path) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1258"):
        try:
            with path.open(encoding=encoding, newline="") as stream:
                sample = stream.read(65536)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Cannot decode {path}; add an explicit encoding adapter")
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    return encoding, delimiter


def sqlite_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def iter_rows(table: Table) -> Iterator[dict[str, Any]]:
    suffix = table.path.suffix.lower()
    if suffix == ".csv":
        with table.path.open(encoding=table.encoding, newline="") as stream:
            reader = csv.DictReader(stream, delimiter=table.delimiter)
            for row in reader:
                if None in row:
                    raise ValueError(f"Ragged CSV row in {table.key}")
                # Header whitespace is normalized once, not values/identifiers.
                yield {str(key).strip(): value for key, value in row.items()}
    elif suffix == ".xlsx":
        from openpyxl import load_workbook

        book = load_workbook(table.path, read_only=True, data_only=True, keep_links=False)
        try:
            for values in book[table.name].iter_rows(min_row=2, values_only=True):
                if any(value is not None for value in values):
                    yield dict(zip(table.columns, values))
        finally:
            book.close()
    else:
        with closing(sqlite_connection(table.path)) as connection:
            cursor = connection.execute(f"SELECT * FROM {quote_identifier(table.name)}")
            for values in cursor:
                yield dict(zip(table.columns, values))


def build_catalog(data_root: Path, business_id: str, sample_size: int = 3) -> Catalog:
    business_root = contained_path(data_root, business_id)
    if not business_root.is_dir():
        raise ValueError(f"Missing business directory: {business_root}")
    tables = []
    for path in sorted(business_root.rglob("*")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(business_root):
            raise ValueError(f"Symlink escapes business directory: {path}")
        suffix = path.suffix.lower()
        if suffix == ".csv":
            encoding, delimiter = _csv_settings(path)
            with path.open(encoding=encoding, newline="") as stream:
                first = next(csv.reader(stream, delimiter=delimiter), None)
            if first:
                tables.append(Table(path, path.stem, _headers(tuple(first)),
                                    encoding=encoding, delimiter=delimiter))
        elif suffix == ".xlsx":
            from openpyxl import load_workbook

            book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
            try:
                for sheet in book:
                    reader = sheet.iter_rows(values_only=True)
                    first = next(reader, None)
                    if first and any(x is not None for x in first):
                        columns = _headers(first)
                        sample = tuple(dict(zip(columns, values))
                                       for values in itertools.islice(reader, sample_size))
                        tables.append(Table(path, sheet.title, columns, sample))
                    reader.close()
            finally:
                book.close()
        elif suffix in (".sqlite", ".sqlite3", ".db"):
            with closing(sqlite_connection(path)) as connection:
                names = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for (name,) in names:
                    columns = tuple(x[1] for x in connection.execute(
                        f"PRAGMA table_info({quote_identifier(name)})"))
                    values = connection.execute(
                        f"SELECT * FROM {quote_identifier(name)} LIMIT ?", (sample_size,)).fetchall()
                    tables.append(Table(path, name, columns,
                                        tuple(dict(zip(columns, row)) for row in values)))
    sampled = []
    for table in tables:
        if table.path.suffix.lower() != ".csv":
            sampled.append(table)
            continue
        reader = iter_rows(table)
        try:
            sample = tuple(itertools.islice(reader, sample_size))
        finally:
            reader.close()
        sampled.append(Table(table.path, table.name, table.columns, sample,
                             table.encoding, table.delimiter))
    readme = business_root / "README.md"
    return Catalog(business_id, readme.read_text(encoding="utf-8-sig")
                   if readme.is_file() else "", tuple(sampled))


def catalog_dict(catalog: Catalog, *, relative_to: Path) -> dict[str, Any]:
    """Share catalog metadata without embedding a member's filesystem paths."""
    return {
        "business_id": catalog.business_id,
        "readme": catalog.readme,
        "tables": [{"source": t.path.resolve().relative_to(relative_to.resolve()).as_posix(),
                    "name": t.name, "columns": t.columns,
                    "sample": t.sample} for t in catalog.tables],
    }


def json_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)
