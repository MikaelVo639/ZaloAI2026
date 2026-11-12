"""Data-layout variants of a labelled TRAIN split (LOCAL PROXY).

Each variant rewrites how a business stores the same facts, never the facts themselves,
so every train answer stays valid. A variant split keeps only the questions of the
businesses it changed; ``<name>.answers.jsonl`` beside it holds their train references.

- ``monthly_files``: every dated list documented in the README is split into one file per
  month (``<stem>_T<tháng>_<năm>``), and the README names the file family with
  placeholders instead of the single file.
- ``duplicate_orders``: in every order or invoice list, every 25th row is written twice,
  identical in every cell (an export duplicate the rulebook says to count once).
- ``summary_rows``: every dated list with order ids gets a daily total row after each day,
  the label written in the order id column ("Tổng dd/mm"), no date, status or quantity.
- ``no_column_docs``: the README keeps its file list and conventions but no
  ``## Cột trong …`` section: the columns are known only by their headers.
- ``status_labels``: in every documented list with a status column, the returned state is
  written "Khách trả" and the delivering state "Đang ship" (labels written for the variant).

Never point this at a public or private split.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from datamate.evaluation.robustness import _link_tree
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.readme_sales import (
    _classify,
    _records,
    parse_amount,
    parse_day,
    parse_sections,
)
from datamate.ingestion.source_names import filename_key

VARIANTS = ("monthly_files", "duplicate_orders", "summary_rows", "no_column_docs",
            "status_labels")
STATUS_LABELS = {"tra hang": "Khách trả", "dang giao": "Đang ship"}
DUPLICATE_EVERY = 25
FAMILY_NOTE = "Các file `{template}` là dữ liệu chia theo tháng, mỗi tháng một file, cùng cột."


def _template(path: Path) -> str:
    return f"{path.stem}_T<tháng>_<năm>{path.suffix}"


def _month_rows(table: Any, columns: dict[str, str], date_column: str
                ) -> tuple[list[str], dict[tuple[int, int], list[dict[str, Any]]]] | None:
    """Header and rows grouped by month; continuation rows keep the last date seen."""
    header: list[str] = []
    months: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    last: date | None = None
    for _, row in _records(table, columns):
        for name in row:
            if name not in header:
                header.append(name)
        value = row.get(date_column)
        if value not in (None, ""):
            try:
                last = parse_day(value, table.name)
            except ValueError:
                return None
        if last is None:
            return None
        months[(last.year, last.month)].append(row)
    return (header, months) if len(months) > 1 else None


def _write(path: Path, sheet: str, header: list[str], rows: list[dict[str, Any]],
           encoding: str, delimiter: str) -> None:
    if path.suffix.lower() == ".csv":
        with path.open("x", encoding=encoding, newline="") as stream:
            writer = csv.writer(stream, delimiter=delimiter)
            writer.writerow(header)
            writer.writerows([row.get(name) for name in header] for row in rows)
        return
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = sheet
    book.active.append(header)
    for row in rows:
        book.active.append([row.get(name) for name in header])
    book.save(path)


def monthly_files(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` (one business) with monthly files; False when the
    business has no dated single-table list to split (``destination`` is then not created)."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    split: dict[Path, tuple[Any, list[str], dict]] = {}
    for section in parse_sections(readme):
        column = section.fields.get("date")
        tables = [t for t in catalog.tables
                  if filename_key(t.path.name) == filename_key(section.file)]
        if column is None or len(tables) != 1 or tables[0].path in split:
            continue
        if section.sheet not in (None, tables[0].name):
            continue
        grouped = _month_rows(tables[0], section.columns, column)
        if grouped is not None:
            split[tables[0].path] = (tables[0], *grouped)
    if not split:
        return False
    _link_tree(source, destination)
    for path, (table, header, months) in split.items():
        (destination / path.name).unlink()
        for (year, month), rows in sorted(months.items()):
            name = f"{path.stem}_T{month}_{year}{path.suffix}"
            _write(destination / name, table.name, header, rows,
                   table.encoding or "utf-8", table.delimiter or ",")
        template = _template(path)
        readme = re.sub(rf"`{re.escape(path.name)}`", f"`{template}`", readme)
        readme = re.sub(rf"(?m)^(.*`{re.escape(template)}`.*)$",
                        lambda m: m.group(1) + "\n" + FAMILY_NOTE.format(template=template),
                        readme, count=1)
    target = destination / "README.md"
    target.unlink()
    target.write_text(readme, encoding="utf-8")
    return True


def duplicate_orders(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` with repeated order-list rows; False when the
    business documents no single-table order or invoice list."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    try:
        roles = _classify(parse_sections(readme), catalog)
    except ValueError:
        return False
    entries = roles["orders"].tables if "orders" in roles else []
    files = {table.path for _, table in entries}
    if not entries or any(sum(t.path == path for t in catalog.tables) != 1 for path in files):
        return False
    _link_tree(source, destination)
    for section, table in entries:
        header: list[str] = []
        rows: list[dict[str, Any]] = []
        for ordinal, row in _records(table, section.columns):
            header += [name for name in row if name not in header]
            rows.append(row)
            if ordinal % DUPLICATE_EVERY == 0:
                rows.append(dict(row))
        (destination / table.path.name).unlink()
        _write(destination / table.path.name, table.name, header, rows,
               table.encoding or "utf-8", table.delimiter or ",")
    return True


def _with_totals(rows: list[dict[str, Any]], table: Any, fields: dict[str, str]
                 ) -> list[dict[str, Any]] | None:
    """Rows with a total row after each day (dates carried over continuation rows)."""
    order, day_column = fields["order_id"], fields["date"]
    amount = fields.get("amount") or fields.get("order_goods")
    result: list[dict[str, Any]] = []
    day: date | None = None
    total = 0

    def close() -> None:
        if day is not None:
            result.append({order: f"Tổng {day:%d/%m}", **({amount: str(total)} if amount else {})})

    for row in rows:
        value = row.get(day_column)
        if value not in (None, ""):
            try:
                current = parse_day(value, table.name)
            except ValueError:
                return None
            if current != day:
                close()
                day, total = current, 0
        if amount:
            try:
                total += int(parse_amount(row.get(amount)))
            except (TypeError, ValueError, ArithmeticError):
                pass
        result.append(row)
    close()
    return result


def summary_rows(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` with daily total rows in its dated lists with
    order ids; False when there is none (``destination`` is then not created)."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    rewritten: dict[Path, tuple[Any, list[str], list[dict[str, Any]]]] = {}
    for section in parse_sections(readme):
        fields = section.fields
        tables = [t for t in catalog.tables
                  if filename_key(t.path.name) == filename_key(section.file)]
        if not {"order_id", "date"} <= set(fields) or len(tables) != 1:
            continue
        table = tables[0]
        if table.path in rewritten or section.sheet not in (None, table.name):
            continue
        header: list[str] = []
        rows: list[dict[str, Any]] = []
        for _, row in _records(table, section.columns):
            header += [name for name in row if name not in header]
            rows.append(row)
        with_totals = _with_totals(rows, table, fields)
        if with_totals is not None:
            rewritten[table.path] = (table, header, with_totals)
    if not rewritten:
        return False
    _link_tree(source, destination)
    for path, (table, header, rows) in rewritten.items():
        (destination / path.name).unlink()
        _write(destination / path.name, table.name, header, rows,
               table.encoding or "utf-8", table.delimiter or ",")
    return True


def no_column_docs(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` without the README's column sections; False when
    the README documents no columns."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    kept, skipping = [], False
    for line in readme.splitlines():
        if line.startswith("## "):
            skipping = bool(re.match(r"##\s+Cột trong\b", line))
        if not skipping:
            kept.append(line)
    if len(kept) == len(readme.splitlines()):
        return False
    _link_tree(source, destination)
    (destination / "README.md").unlink()
    (destination / "README.md").write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True


def status_labels(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` with other labels for two states; False when no
    documented single-table list has a returned or delivering order."""
    from datamate.ingestion.status import order_state

    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    rewritten: dict[Path, tuple[Any, list[str], list[dict[str, Any]]]] = {}
    for section in parse_sections(readme):
        column = section.fields.get("status")
        tables = [t for t in catalog.tables
                  if filename_key(t.path.name) == filename_key(section.file)]
        if column is None or len(tables) != 1 or tables[0].path in rewritten:
            continue
        header: list[str] = []
        rows: list[dict[str, Any]] = []
        changed = False
        for _, row in _records(tables[0], section.columns):
            header += [name for name in row if name not in header]
            label = STATUS_LABELS.get(order_state(row.get(column)) or "")
            if label:
                row = {**row, column: label}
                changed = True
            rows.append(row)
        if changed:
            rewritten[tables[0].path] = (tables[0], header, rows)
    if not rewritten:
        return False
    _link_tree(source, destination)
    for path, (table, header, rows) in rewritten.items():
        (destination / path.name).unlink()
        _write(destination / path.name, table.name, header, rows,
               table.encoding or "utf-8", table.delimiter or ",")
    return True


_BUILDERS = {"monthly_files": monthly_files, "duplicate_orders": duplicate_orders,
             "summary_rows": summary_rows, "no_column_docs": no_column_docs,
             "status_labels": status_labels}


def build_variant(train: Path, output: Path, name: str) -> dict[str, Any]:
    """Write ``output/<name>/`` (inference split) and ``output/<name>.answers.jsonl``."""
    if name not in VARIANTS:
        raise ValueError(f"Unknown variant {name!r}; choose from {VARIANTS}")
    train, output = train.resolve(), output.resolve()
    if not (train / "answers.jsonl").is_file():
        raise ValueError("Data variants need a labelled TRAIN split")
    split = output / name
    if split.exists():
        raise ValueError(f"Refusing to overwrite {split}; choose a new directory")
    (split / "data").mkdir(parents=True)
    for file in ("rulebook.md", "calendar.csv"):
        shutil.copy2(train / file, split / file)
    changed = []
    for business in sorted(p for p in (train / "data").iterdir() if p.is_dir()):
        if _BUILDERS[name](business, split / "data" / business.name):
            changed.append(business.name)
    questions = [json.loads(line) for line in
                 (train / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    kept = [q for q in questions if q["business_id"] in changed]
    answers = {json.loads(line)["id"]: line for line in
               (train / "answers.jsonl").read_text(encoding="utf-8").splitlines() if line}
    (split / "questions.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in kept), encoding="utf-8")
    (output / f"{name}.answers.jsonl").write_text(
        "".join(answers[q["id"]] + "\n" for q in kept), encoding="utf-8")
    return {"variant": name, "businesses": changed, "questions": len(kept)}
