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
- ``repeated_headers``: insert the original header halfway through CSV order and sales lists.
- ``cost_convention_only``: document the POS sales columns while leaving the selling cost
  column described only by the README convention.
- ``catalogue_stock_column``: add an empty, documented stock balance column to CSV catalogues.
- ``readme_paraphrase``: the README describes the columns of its sales line lists in other
  words, each followed by an explanation ("số hoá đơn, ghi ở mọi dòng của hoá đơn").
- ``status_in_notes``: a sales line list loses its status column; the order state is
  written in a notes column whose README lists its possible states. Some notes mention
  a state that this column does not record.
- ``order_adjustments``: add two documented non-merchandise rows to an existing order.
- ``ambiguous_status_in_notes``: also inject ambiguous notes on a few orders in different months.
- ``service_tickets``: store ticket totals and status on monthly sheets below a title,
  with extra description columns; retain plain revenue and order-count questions.
- ``debt_in_convention``: document money columns only in conventions, and repeat order
  facts on two product lines. ``debt_inconsistent_total`` changes one of the totals.
- ``staff_catalogue``: give the staff table and columns new documented names.
  ``staff_duplicate_names`` also adds a conflicting code for each name.
- ``short_names``: every other product of a sales line list is written by its initials; a
  ``GhiTat.csv`` list gives the full name of each short form, as the README then says.
- ``unknown_export``: a shop whose sales come from a POS / sales-software export no longer
  says so ("máy POS", "phần mềm bán hàng", a ``pos.*`` file name), as an export from software
  the known export profiles do not cover: its columns are known only by their headers.

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
    ReadmeSalesError,
    _classify,
    _matching_tables,
    _records,
    load_readme_sales,
    parse_amount,
    parse_day,
    parse_sections,
    profile_sections,
)
from datamate.ingestion.sales import column_mapping
from datamate.ingestion.source_names import filename_key, matches_source
from datamate.semantics.text import fold

VARIANTS = ("monthly_files", "duplicate_orders", "summary_rows", "no_column_docs",
            "status_labels", "repeated_headers", "cost_convention_only", "catalogue_stock_column",
            "unknown_export", "readme_paraphrase", "status_in_notes", "short_names",
            "order_adjustments", "service_tickets", "ambiguous_status_in_notes",
            "debt_in_convention", "debt_inconsistent_total", "staff_catalogue",
            "staff_duplicate_names")
STATUS_LABELS = {"tra hang": "Khách trả", "dang giao": "Đang ship"}
DUPLICATE_EVERY = 25
FAMILY_NOTE = "Các file `{template}` là dữ liệu chia theo tháng, mỗi tháng một file, cùng cột."


def _template(path: Path) -> str:
    return f"{path.stem}_T<tháng>_<năm>{path.suffix}"


def _month_rows(table: Any, columns: dict[str, str], date_column: str
                ) -> tuple[list[str], dict[tuple[int, int], list[dict[str, Any]]]] | None:
    """Header and rows grouped by month; continuation rows keep the last date seen. The
    header is the file's own (an always-empty trailing column stays, as an export keeps it)."""
    header: list[str] = [str(name) for name in table.columns if name]
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


def repeated_headers(source: Path, destination: Path) -> bool:
    """Insert a second CSV header without changing the source records or README."""
    catalog = build_catalog(source.parent, source.name)
    sections = parse_sections(catalog.readme)
    required = {"order_id", "date", "status"}
    rewritten = []
    for table in catalog.tables:
        if table.path.suffix.lower() != ".csv":
            continue
        documented = any(
            matches_source(section.file, table.path.name)
            and section.sheet in (None, table.name)
            and required <= section.fields.keys()
            for section in sections
        )
        if not documented and not required <= column_mapping(table.columns).keys():
            continue
        with table.path.open(encoding=table.encoding, newline="") as stream:
            header, *rows = csv.reader(stream, delimiter=table.delimiter)
        if len(rows) >= 2:
            rows.insert(len(rows) // 2, header)
            rewritten.append((table, header, rows))
    if not rewritten:
        return False
    _link_tree(source, destination)
    for table, header, rows in rewritten:
        target = destination / table.path.relative_to(source)
        target.unlink()
        with target.open("x", encoding=table.encoding, newline="") as stream:
            writer = csv.writer(stream, delimiter=table.delimiter)
            writer.writerow(header)
            writer.writerows(rows)
    return True


def cost_convention_only(source: Path, destination: Path) -> bool:
    """Describe the profiled columns explicitly, leaving cost in its original convention."""
    catalog = build_catalog(source.parent, source.name)
    if parse_sections(catalog.readme):
        return False
    sections = profile_sections(catalog, [])
    if not any("unit_cost_at_sale" in section.fields for section in sections):
        return False
    blocks = []
    for section in sections:
        heading = f"## Cột trong `{section.file}`"
        if section.sheet is not None:
            heading += f", sheet `{section.sheet}`"
        cost = section.fields.get("unit_cost_at_sale")
        blocks.append("\n".join([heading, *(f"- **{column}**: {meaning}"
                                             for column, meaning in section.columns.items()
                                             if column != cost)]))
    _link_tree(source, destination)
    target = destination / "README.md"
    target.unlink()
    target.write_text(catalog.readme.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n",
                      encoding="utf-8")
    return True


def catalogue_stock_column(source: Path, destination: Path) -> bool:
    """Add a documented stock column with no recorded values to a product catalogue."""
    catalog = build_catalog(source.parent, source.name)
    required = {"product_id", "base_unit", "product_group"}
    column = "Tồn kiểm kê"
    candidates = []
    for section in parse_sections(catalog.readme):
        fields = set(section.fields)
        if not required <= fields or not fields & {"product_name", "name"}:
            continue
        if fields & {"_other", "order_id", "quantity", "amount", "date", "status"}:
            continue
        matches = _matching_tables(catalog, section)
        if len(matches) == 1 and matches[0].path.suffix.lower() == ".csv" and section.sheet is None:
            table = matches[0]
            if column not in table.columns:
                candidates.append((section, table))
    if len(candidates) != 1:
        return False
    section, table = candidates[0]
    heading = re.compile(rf"(?m)^(##\s+Cột trong\s+`{re.escape(section.file)}`[ \t]*)$")
    readme, edits = heading.subn(lambda match: f"{match[1]}\n- **{column}**: tồn kho",
                                 catalog.readme)
    if edits != 1:
        return False
    _link_tree(source, destination)
    target = destination / table.path.relative_to(source)
    target.unlink()
    with (table.path.open(encoding=table.encoding, newline="") as original,
          target.open("x", encoding=table.encoding, newline="") as output):
        reader = csv.reader(original, delimiter=table.delimiter)
        writer = csv.writer(output, delimiter=table.delimiter)
        writer.writerow([*next(reader), column])
        writer.writerows([*row, ""] for row in reader)
    target_readme = destination / "README.md"
    target_readme.unlink()
    target_readme.write_text(readme, encoding="utf-8")
    return True


_EXPORT_WORDS = ((re.compile(r"\bmáy POS\b"), "máy tính tiền"), (re.compile(r"\bPOS\b"), "máy"),
                 (re.compile(r"phần mềm bán hàng", re.IGNORECASE), "ứng dụng cửa hàng"))


def unknown_export(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` with no word saying the files are a POS export;
    False when the source shop has no export-profile sales list."""
    catalog = build_catalog(source.parent, source.name)
    if not profile_sections(catalog, parse_sections(catalog.readme)):
        return False
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    for pattern, words in _EXPORT_WORDS:
        readme = pattern.sub(words, readme)
    renamed = {path.name: re.sub(r"^pos(?=\.)", "cua_hang", path.name)
               for path in source.iterdir() if re.match(r"pos\.", path.name)}
    for old, new in renamed.items():
        readme = readme.replace(f"`{old}`", f"`{new}`")
    _link_tree(source, destination)
    for old, new in renamed.items():
        (destination / old).rename(destination / new)
    (destination / "README.md").unlink()
    (destination / "README.md").write_text(readme, encoding="utf-8")
    return True


_PARAPHRASES = {
    "ma don": "số hoá đơn, ghi ở mọi dòng của hoá đơn",
    "ngay ban": "ngày bán; ghi theo hoá đơn",
    "ten hang": "hàng bán, tên đầy đủ",
    "so luong": "số lượng bán",
    "don gia": "đơn giá bán; giá một đơn vị của dòng",
    "thanh tien sau giam": "thành tiền của dòng; đã trừ giảm giá của dòng",
    "kenh ban": "kênh bán: các kênh tiệm dùng",
}


def _sales_line_sections(readme: str) -> list[Any]:
    return [section for section in parse_sections(readme)
            if {"amount"} <= set(section.fields) and section.fields.keys() & {"product_name",
                                                                              "product_id"}]


def _rewrite_meanings(readme: str, sections: list[Any], change: Any) -> str:
    """README with ``change(column, meaning)`` applied to the column lines of ``sections``."""
    labels = {section.label for section in sections}
    out, current = [], None
    for line in readme.splitlines():
        heading = re.match(r"##\s+Cột trong\s+`([^`]+)`(?:,\s*sheet\s+`([^`]+)`)?", line)
        if line.startswith("## "):
            current = None
            if heading:
                current = (f"{heading.group(1)}::{heading.group(2)}" if heading.group(2)
                           else heading.group(1))
        item = re.match(r"(\s*-\s+\*\*(.+?)\*\*:\s*)(.+)$", line)
        if current in labels and item:
            replaced = change(item.group(2).strip(), item.group(3).strip())
            if replaced is None:
                continue
            line = item.group(1) + replaced
        out.append(line)
    return "\n".join(out) + "\n"


def readme_paraphrase(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` with its sales line columns described in other
    words; False when the README describes no sales line list."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    sections = _sales_line_sections(readme)

    def change(_column: str, meaning: str) -> str:
        base = fold(re.sub(r"\s*\(.*\)\s*\.?\s*$", "", meaning)).strip()
        return _PARAPHRASES.get(base, meaning)

    rewritten = _rewrite_meanings(readme, sections, change)
    if not sections or rewritten == readme.rstrip("\n") + "\n":
        return False
    _link_tree(source, destination)
    (destination / "README.md").unlink()
    (destination / "README.md").write_text(rewritten, encoding="utf-8")
    return True


_NOTE_TEXT = {"huy": "Khách huỷ đơn", "tra hang": "Khách trả lại hàng",
              "dang giao": "Đang giao cho khách", "hoan thanh": None}
_NOTE_STATES = {"huy": "huỷ", "tra hang": "trả hàng", "dang giao": "đang giao"}
_OTHER_NOTES = {"huy": "Nhắc khách kiểm tra yêu cầu huỷ của tuần trước",
                "tra hang": "Khách hỏi nơi trả lại vỏ rỗng",
                "dang giao": "Bạn đang giao đơn khác"}


def _one_table_lists(readme: str, catalog: Any, need: str) -> list[tuple[Any, Any]]:
    found = []
    for section in _sales_line_sections(readme):
        tables = [t for t in catalog.tables
                  if filename_key(t.path.name) == filename_key(section.file)
                  and section.sheet in (None, t.name)]
        sheets = [t for t in catalog.tables if tables and t.path == tables[0].path]
        if need in section.fields and len(tables) == 1 and len(sheets) == 1:
            found.append((section, tables[0]))
    return found


def _rewrite_tables(source: Path, destination: Path, readme: str,
                    rewritten: dict[Path, tuple[Any, list[str], list[dict[str, Any]]]],
                    extra: dict[str, list[list[Any]]] | None = None) -> None:
    _link_tree(source, destination)
    for path, (table, header, rows) in rewritten.items():
        (destination / path.name).unlink()
        _write(destination / path.name, table.name, header, rows,
               table.encoding or "utf-8", table.delimiter or ",")
    for name, rows in (extra or {}).items():
        with (destination / name).open("x", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
    (destination / "README.md").unlink()
    (destination / "README.md").write_text(readme, encoding="utf-8")


def status_in_notes(source: Path, destination: Path, *, ambiguous: bool = False) -> bool:
    """Write ``destination`` as ``source`` with the order state of its one-table sales line
    lists written in a notes column; False when no such list has a status column whose
    every label is a known state."""
    from datamate.ingestion.status import eliminated_labels, normalize_label, order_state

    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    rewritten: dict[Path, tuple[Any, list[str], list[dict[str, Any]]]] = {}
    notes: dict[str, tuple[str, str]] = {}
    for section, table in _one_table_lists(readme, catalog, "status"):
        column = section.fields["status"]
        if table.path in rewritten:
            return False
        records = [row for _, row in _records(table, section.columns)]
        eliminated = eliminated_labels({row.get(column) for row in records})
        note = "Ghi chú đơn"
        if any(note in row for row in records[:1]) or note in section.columns:
            return False
        header: list[str] = []
        rows: list[dict[str, Any]] = []
        states = []
        for row in records:
            label = row.get(column)
            state = None
            if label not in (None, "") and str(label).strip():
                state = order_state(label) or eliminated.get(normalize_label(label))
                if state is None:
                    return False
            states.append(state)
        actual = set(states) & _NOTE_STATES.keys()
        declared = actual or {"huy"}
        other = next((v for k, v in _OTHER_NOTES.items() if k not in declared), "Đổi size")
        disputed: set[int] = set()
        if ambiguous and {"date", "order_id"} <= section.fields.keys():
            date_column = section.fields["date"]
            order_column = section.fields["order_id"]
            by_month = {}
            for i, (row, state) in enumerate(zip(records, states)):
                if state != "hoan thanh" or not row.get(order_column) or not row.get(date_column):
                    continue
                try:
                    day = parse_day(row[date_column], table.name)
                except (TypeError, ValueError):
                    continue
                by_month.setdefault((day.year, day.month), i)
            disputed.update(i for k, i in enumerate(by_month.values()) if k in {0, 3, 7})
        negated = {"huy": "Khách chưa huỷ", "tra hang": "Khách chưa trả",
                   "dang giao": "Khách báo chưa giao"}
        for i, (row, state) in enumerate(zip(records, states)):
            row.pop(column, None)
            row[note] = other if state == "hoan thanh" else _NOTE_TEXT.get(state)
            if i in disputed:
                row[note] = negated[min(declared)]
            header += [name for name in row if name not in header]
            rows.append(row)
        rewritten[table.path] = (table, header, rows)
        labels = ", ".join(_NOTE_STATES[state] for state in sorted(declared))
        notes[section.label] = (column, f"ghi chú của đơn; chỉ ghi trạng thái {labels}")
    if not rewritten:
        return False
    sections = [s for s in _sales_line_sections(readme) if s.label in notes]

    def change(column: str, meaning: str) -> str | None:
        return None if column in {c for c, _ in notes.values()} else meaning

    text = _rewrite_meanings(readme, sections, change)
    for label in notes:
        file = label.split("::")[0]
        heading = next(line for line in text.splitlines()
                       if re.match(r"##\s+Cột trong\s+`" + re.escape(file) + "`", line))
        text = text.replace(heading + "\n", f"{heading}\n- **Ghi chú đơn**: {notes[label][1]}\n", 1)
    _rewrite_tables(source, destination, text, rewritten)
    return True


def ambiguous_status_in_notes(source: Path, destination: Path) -> bool:
    return status_in_notes(source, destination, ambiguous=True)


def _initials(name: str) -> str:
    words = re.findall(r"[^\W_]+", name)
    return "".join(word[0].upper() if word.isalpha() else word for word in words)


def short_names(source: Path, destination: Path) -> bool:
    """Write ``destination`` as ``source`` with every other product of its one-table sales
    line lists written short, and a ``GhiTat.csv`` short form -> full name list of every
    product; False when no such list names its products."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    if any(path.name == "GhiTat.csv" for path in source.iterdir()):
        return False
    lists = _one_table_lists(readme, catalog, "product_name")
    if not lists:
        return False
    names: list[str] = []
    records: dict[Path, tuple[Any, Any, list[dict[str, Any]]]] = {}
    for section, table in lists:
        if table.path in records:
            return False
        rows = [row for _, row in _records(table, section.columns)]
        records[table.path] = (section, table, rows)
        column = section.fields["product_name"]
        for row in rows:
            value = row.get(column)
            if value not in (None, "") and str(value).strip() not in names:
                names.append(str(value).strip())
    full_keys = {fold(name) for name in names}
    short: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(names):
        base = _initials(name) or "H"
        code, n = base, 1
        while fold(code) in used or fold(code) in full_keys:
            n += 1
            code = f"{base}{n}"
        used.add(fold(code))
        short[name] = code
    rewritten = {}
    for path, (section, table, rows) in records.items():
        column = section.fields["product_name"]
        header: list[str] = []
        out = []
        for row in rows:
            value = row.get(column)
            written = str(value).strip() if value not in (None, "") else ""
            if written in short and sorted(names).index(written) % 2 == 0:
                row = {**row, column: short[written]}
            header += [name for name in row if name not in header]
            out.append(row)
        rewritten[path] = (table, header, out)
    labels = {section.label for section, _ in lists}
    columns = {section.fields["product_name"] for section, _ in lists}
    sections = [s for s in _sales_line_sections(readme) if s.label in labels]

    def change(column: str, meaning: str) -> str:
        return (f"{meaning}, có thể ghi tắt (tra ở file `GhiTat.csv`)" if column in columns
                else meaning)

    text = _rewrite_meanings(readme, sections, change)
    text = text.replace("## File\n", "## File\n- `GhiTat.csv` (csv)\n", 1)
    table = [["Ghi tắt", "Tên hàng"], *[[code, name] for name, code in sorted(short.items())]]
    _rewrite_tables(source, destination, text, rewritten, {"GhiTat.csv": table})
    return True


def order_adjustments(source: Path, destination: Path) -> bool:
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    for section, table in _one_table_lists(readme, catalog, "product_name"):
        fields = section.fields
        if not {"order_id", "quantity"} <= fields.keys():
            continue
        name = fields["product_name"]
        rows = [row for _, row in _records(table, section.columns)]
        if not rows or any(fold(str(row.get(name))) in {"uu dai quy", "cuoc tan noi"}
                           for row in rows):
            continue
        for index, row in enumerate(rows):
            if not all(row.get(fields[field]) for field in ("order_id", "product_name", "quantity")):
                continue
            adjustments = []
            for label, amount in (("Ưu đãi quý", "-4300"), ("Cước tận nơi", "7900")):
                extra = {column: row.get(column) for field, column in fields.items()
                         if field in {"order_id", "date", "status", "staff_id", "customer_id",
                                      "customer_name", "customer_group", "channel", "time",
                                      "payment_method"}}
                adjustments.append({**extra, name: label, fields["amount"]: amount})
            rows[index + 1:index + 1] = adjustments
            text = _rewrite_meanings(readme, [section], lambda column, meaning: (
                f"{meaning}; dòng không phải mặt hàng: `Ưu đãi quý` là giảm giá cả đơn; "
                "`Cước tận nơi` là phí giao hàng" if column == name else meaning))
            _rewrite_tables(source, destination, text,
                            {table.path: (table, list(row), rows)})
            return True
    return False


def service_tickets(source: Path, destination: Path) -> bool:
    """Project a verified TRAIN line ledger down to one row per ticket."""
    readme = (source / "README.md").read_text(encoding="utf-8-sig")
    catalog = build_catalog(source.parent, source.name)
    if not _one_table_lists(readme, catalog, "status"):
        return False
    try:
        sales, _, _ = load_readme_sales(
            catalog, (source.parents[1] / "rulebook.md").read_text(encoding="utf-8"))
    except ReadmeSalesError:
        return False
    orders: dict[str, tuple[date, str, Any]] = {}
    for sale in sales:
        old = orders.get(sale.order_id)
        orders[sale.order_id] = (sale.day, sale.status, sale.amount + (old[2] if old else 0))
    if not orders:
        return False
    statuses = {"valid": "Hoàn thành", "returned": "Trả hàng", "cancelled": "Huỷ"}
    if not {status for _, status, _ in orders.values()} <= statuses.keys():
        return False
    destination.mkdir(parents=True)
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheets = {}
    for day, status, amount in orders.values():
        month = day.strftime("T%m-%Y")
        if month not in sheets:
            sheet = sheets[month] = workbook.create_sheet(month)
            sheet.append([])
            sheet.append(["Sổ tiếp nhận", month])
            sheet.append(["Ngày phiếu", "Tổng", "Kết quả", "Hẹn lấy", "Việc làm", "Ghi chú"])
        sheets[month].append([day.isoformat(), str(amount), statuses[status], "Khi xong",
                              "Kiểm tra", "Gọi trước khi khách tới"])
    workbook.save(destination / "tickets.xlsx")
    coverage = "\n".join(line for line in readme.splitlines()
                          if "du lieu co den het" in fold(line))
    (destination / "README.md").write_text(
        "## File\n- `tickets.xlsx` (xlsx)\n\n## Cột trong `tickets.xlsx`\n"
        "- **Ngày phiếu**: ngày bán; mỗi dòng là một phiếu dịch vụ\n"
        "- **Tổng**: tổng tiền phiếu; trước giảm giá cả đơn, không gồm phí giao, không gồm thuế\n"
        "- **Kết quả**: trạng thái đơn\n"
        "- **Hẹn lấy**: ngày giao dự kiến, để mô tả lịch hẹn\n"
        "- **Việc làm**: nội dung thực hiện\n- **Ghi chú**: lời nhắc liên hệ\n"
        "\n## Quy ước\n" + coverage + "\n", encoding="utf-8")
    return True


def debt_in_convention(source: Path, destination: Path, *, inconsistent: bool = False) -> bool:
    """Store TRAIN orders with line grain and list the receipts only in the inventory."""
    from datamate.ingestion.debt import DebtSourceError, load_debt_ledger

    catalog = build_catalog(source.parent, source.name)
    book = (source.parents[1] / "rulebook.md").read_text(encoding="utf-8-sig")
    try:
        ledger = load_debt_ledger(catalog, book)
    except DebtSourceError:
        return False
    if not ledger.orders or not ledger.payments:
        return False
    destination.mkdir(parents=True)
    # On coded shops test the paired header route, including a receipt date with no word
    # "thu"; on name-only shops keep some order meanings explicitly documented.
    code_header = ["Mã khách"] if ledger.uses_customer_codes else []
    header = ["Mã đơn", "Ngày bán", "Trạng thái", "Tổng phiếu", "Hình thức thanh toán", "Kênh",
              *code_header, "Tên khách", "Tên hàng", "Số lượng"]
    orders_doc = ("" if ledger.uses_customer_codes else (
        "## Cột trong `register.csv`\n"
        "- **Mã đơn**: mã đơn\n- **Ngày bán**: ngày đặt\n- **Trạng thái**: trạng thái đơn\n"
        "- **Hình thức thanh toán**: hình thức thanh toán\n- **Kênh**: kênh bán\n"
        "- **Tên khách**: tên khách\n- **Tên hàng**: tên hàng\n- **Số lượng**: số lượng\n"))
    (destination / "README.md").write_text(
        "## File\n- `register.csv`\n\n## Danh sách ở file riêng\n- `receipts.csv`\n\n"
        + orders_doc + "\n## Quy ước\n- **Tổng phiếu**: số tiền khách phải trả cho cả đơn.\n"
        "- **Tiền lần thu** là số tiền khách nộp ở lần đó.\n"
        f"- Dữ liệu có đến hết {ledger.last_day:%d/%m/%Y}.\n", encoding="utf-8")
    with (destination / "register.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for i, order in enumerate(ledger.orders):
            channel = "Shopee" if order.excluded == "marketplace" else "Store"
            code = [order.customer_id] if ledger.uses_customer_codes else []
            fields = [order.order_id, order.day.isoformat(), order.state, order.total,
                      order.payment_method, channel, *code, order.customer_name]
            writer.writerow([*fields, "Món A", 1])
            if i == 0:
                if inconsistent:
                    fields[3] += 1
                writer.writerow([*fields, "Món B", 2])
    with (destination / "receipts.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Mã đơn", "Ngày nhận", "Tiền lần thu", "Lời nhắc"])
        writer.writerows((p.order_id, p.day.isoformat(), p.amount, "đã lưu")
                         for p in ledger.payments)
    return True


def debt_inconsistent_total(source: Path, destination: Path) -> bool:
    return debt_in_convention(source, destination, inconsistent=True)


def staff_catalogue(source: Path, destination: Path, *, duplicate: bool = False) -> bool:
    """Rename the documented staff relation without changing any order's employee code."""
    import sqlite3
    from datamate.ingestion.readme_sales import sqlite_staff_codes

    catalog = build_catalog(source.parent, source.name)
    if not sqlite_staff_codes(catalog):
        return False
    table = next(t for t in catalog.tables if t.name == "nhan_vien" and t.path.suffix == ".sqlite")
    _link_tree(source, destination)
    path = destination / table.path.name
    path.unlink()
    shutil.copy2(table.path, path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE nhan_vien RENAME TO bang_ca")
        connection.execute("ALTER TABLE bang_ca RENAME COLUMN MaNhanVien TO KyHieu")
        connection.execute("ALTER TABLE bang_ca RENAME COLUMN TenGoi TO HoTen")
        if duplicate:
            connection.execute('INSERT INTO bang_ca (KyHieu, HoTen) '
                               "SELECT 'AMBIG-' || KyHieu, HoTen FROM bang_ca")
    readme = destination / "README.md"
    readme.unlink()
    readme.write_text(catalog.readme.replace("`nhan_vien`", "`bang_ca`") + (
        f"\n## Cột trong `{table.path.name}`, sheet `bang_ca`\n"
        "- **KyHieu**: mã nhân viên\n- **HoTen**: tên nhân viên\n"), encoding="utf-8")
    return True


def staff_duplicate_names(source: Path, destination: Path) -> bool:
    return staff_catalogue(source, destination, duplicate=True)


_BUILDERS = {"monthly_files": monthly_files, "duplicate_orders": duplicate_orders,
             "summary_rows": summary_rows, "no_column_docs": no_column_docs,
             "status_labels": status_labels, "repeated_headers": repeated_headers,
             "cost_convention_only": cost_convention_only,
             "catalogue_stock_column": catalogue_stock_column, "unknown_export": unknown_export,
             "readme_paraphrase": readme_paraphrase, "status_in_notes": status_in_notes,
             "short_names": short_names, "order_adjustments": order_adjustments,
             "service_tickets": service_tickets, "ambiguous_status_in_notes": ambiguous_status_in_notes,
             "debt_in_convention": debt_in_convention, "debt_inconsistent_total": debt_inconsistent_total,
             "staff_catalogue": staff_catalogue, "staff_duplicate_names": staff_duplicate_names}


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
    if name in {"service_tickets", "debt_in_convention", "debt_inconsistent_total"}:
        from datamate.agent.compute import DEBT_INTENTS
        from datamate.contracts import Question
        from datamate.semantics.planner import plan_question
        from datamate.semantics.rules import read_rules

        rules = read_rules((train / "rulebook.md").read_text(encoding="utf-8"))
        intents = ({"revenue", "order_count"} if name == "service_tickets"
                   else DEBT_INTENTS | {"top_debt_customers"})
        kept = [q for q in kept if (plan := plan_question(Question(
            q["id"], q["business_id"], q["question"], date.fromisoformat(q["as_of_date"])),
            rules)) and plan.intent in intents and not plan.filters]
    if name in {"staff_catalogue", "staff_duplicate_names"}:
        from datamate.ingestion.readme_sales import sqlite_staff_codes

        staff = {business: sqlite_staff_codes(build_catalog(train / "data", business))
                 for business in changed}
        kept = [q for q in kept if any(f" nhan vien {value} " in f" {fold(q['question'])} "
                                     for value in staff[q["business_id"]])]
    answers = {json.loads(line)["id"]: line for line in
               (train / "answers.jsonl").read_text(encoding="utf-8").splitlines() if line}
    (split / "questions.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in kept), encoding="utf-8")
    (output / f"{name}.answers.jsonl").write_text(
        "".join(answers[q["id"]] + "\n" for q in kept), encoding="utf-8")
    return {"variant": name, "businesses": changed, "questions": len(kept)}
