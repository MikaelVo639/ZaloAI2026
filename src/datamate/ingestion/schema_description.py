"""Short, answer-free description of a business's tables for a model prompt (direction B).

For every table: file, sheet, header row, the role the README adapter would give it, and
per column the README meaning, the semantic field, a type, a unit and a few sample values
from the first data rows. Nothing is aggregated (no totals, counts per period or
rankings), so the description cannot leak an answer; ``answers.jsonl`` is never read.
"""

from __future__ import annotations

import itertools
import json
from datetime import date, datetime
from typing import Any

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import iter_rows
from datamate.ingestion.source_names import filename_key
from datamate.ingestion.readme_sales import (
    POS_PROFILES,
    ReadmeSalesError,
    Section,
    _records,
    meaning_field,
    parse_amount,
    parse_conventions,
    parse_day,
    parse_sections,
    profile_sections,
)

DESCRIPTION_VERSION = "schema_description.v1"

_MONEY = {"amount", "order_goods", "unit_price", "discount"}
_COUNTS = {"quantity", "quantity_base", "factor", "returned_quantity"}
_IDS = {"order_id", "product_id", "customer_id", "line_no", "staff_id"}
_UNITS = {
    "amount": "VND", "order_goods": "VND", "unit_price": "VND / đơn vị của dòng",
    "discount": "VND", "quantity": "đơn vị của dòng (cột đơn vị tính)",
    "quantity_base": "đơn vị cơ bản", "factor": "số đơn vị cơ bản trong 1 đơn vị lớn",
    "returned_quantity": "đơn vị cơ bản",
}
_ROLE_FIELDS = (
    ("conversions", {"big_unit", "factor"}),
    ("lots", {"order_id", "returned_quantity"}),
)


def _role(fields: set[str]) -> str | None:
    if "_other" in fields:
        return "other"
    for role, needed in _ROLE_FIELDS:
        if needed <= fields:
            return role
    if "amount" in fields and fields & {"product_name", "product_id"}:
        return "sales_lines"
    if "order_id" in fields and fields & {"date", "status"} and "quantity" not in fields:
        return "orders"
    if "product_id" in fields and fields & {"product_name", "name"} and not fields & {
            "order_id", "quantity"}:
        return "products"
    return None


def _short(value: Any, limit: int) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _kind(field: str | None, samples: list[Any]) -> str:
    if field in _MONEY:
        return "money"
    if field in _COUNTS:
        return "number"
    if field == "date":
        return "date"
    if field in _IDS:
        return "id"
    if not samples:
        return "empty"
    if all(isinstance(v, (datetime, date)) for v in samples):
        return "date"
    try:
        for value in samples:
            parse_amount(value)
        return "number"
    except ValueError:
        pass
    try:
        if all(isinstance(v, str) and "/" in v for v in samples):
            for value in samples:
                parse_day(value)
            return "date"
    except (TypeError, ValueError):
        pass
    return "text"


def _section_for(table: Table, sections: list[Section]) -> Section | None:
    for section in sections:
        if filename_key(section.file) == filename_key(table.path.name) and section.sheet in (None, table.name):
            return section
    return None


def describe_schema(catalog: Catalog, *, sample_rows: int = 3, max_text: int = 40
                    ) -> dict[str, Any]:
    """Describe every table of ``catalog``; see the module docstring."""
    documented = parse_sections(catalog.readme)
    sections = documented + profile_sections(catalog, documented)
    tables = []
    seen: dict[tuple, dict[str, Any]] = {}
    for table in catalog.tables:
        section = _section_for(table, sections)
        meanings = section.columns if section else {}
        columns = list(meanings) if section else list(table.columns)
        key = (table.path.name, section.label if section else None, tuple(columns))
        if key in seen and table.path.suffix.lower() == ".xlsx":
            seen[key]["sheets"].append(table.name)  # one more month of the same list
            continue
        rows: list[dict[str, Any]] = []
        header_note = "first row"
        try:
            records = (_records(table, columns) if section
                       else enumerate(iter_rows(table), 1))
            rows = [row for _, row in itertools.islice(records, sample_rows)]
            if section and not set(columns) <= set(table.columns):
                header_note = "below title rows"
        except (ReadmeSalesError, ValueError, KeyError):
            header_note = "not found"
        fields = {meaning_field(m) for m in meanings.values()} - {None}
        described = []
        for column in columns:
            meaning = meanings.get(column)
            field = meaning_field(meaning) if meaning else None
            values = [row.get(column) for row in rows
                      if row.get(column) is not None and str(row.get(column)).strip()]
            entry = {"name": column, "type": _kind(field, values),
                     "samples": [_short(v, max_text) for v in values[:sample_rows]]}
            if meaning:
                entry["meaning"] = meaning
            if field and field != "_other":
                entry["field"] = field
            if field in _UNITS:
                entry["unit"] = _UNITS[field]
            described.append(entry)
        entry_table = {
            "table": table.key,
            "file": table.path.name,
            "sheets": [table.name] if table.path.suffix.lower() == ".xlsx" else [],
            "role": _role(fields) if section else None,
            "documented_by": (section.origin if section else "none"),
            "header": header_note,
            "columns": described,
        }
        seen[key] = entry_table
        tables.append(entry_table)
    conventions = parse_conventions(catalog.readme)
    return {
        "version": DESCRIPTION_VERSION,
        "business_id": catalog.business_id,
        "tables": tables,
        "readme_notes": _notes(catalog.readme),
        "unit_conversions": len(conventions.conversions),
        "export_profile": next((s.origin for s in sections
                                if s.origin in dict(POS_PROFILES)), None),
    }


def _notes(readme: str) -> list[str]:
    """The README "Quy ước" bullet lines (documentation of the shop, not data)."""
    notes, inside = [], False
    for line in readme.splitlines():
        if line.startswith("## "):
            inside = line.strip().lower().startswith("## quy ước")
            continue
        if inside and line.startswith("- "):
            notes.append(line[2:].strip())
    return notes


def schema_text(description: dict[str, Any], *, max_chars: int = 6000) -> str:
    """Compact text form for a prompt; cut at ``max_chars`` on a table boundary."""
    parts = [f"Business {description['business_id']} ({description['version']})"]
    for note in description["readme_notes"]:
        parts.append(f"Quy ước: {note}")
    for table in description["tables"]:
        sheets = table["sheets"]
        where = table["file"] + (f" sheets {sheets[0]}…{sheets[-1]} ({len(sheets)})"
                                 if len(sheets) > 1 else f"::{sheets[0]}" if sheets else "")
        head = f"\n[{where}] role={table['role'] or '-'} header={table['header']}"
        lines = [head]
        for column in table["columns"]:
            bits = [column["type"]]
            if "field" in column:
                bits.append(column["field"])
            if "unit" in column:
                bits.append(column["unit"])
            meaning = f" — {column['meaning']}" if "meaning" in column else ""
            samples = json.dumps(column["samples"], ensure_ascii=False, default=str)
            lines.append(f"  - {column['name']} ({', '.join(bits)}){meaning}; ví dụ {samples}")
        block = "\n".join(lines)
        if sum(len(p) + 1 for p in parts) + len(block) > max_chars:
            parts.append("\n[...]")
            break
        parts.append(block)
    return "\n".join(parts)
