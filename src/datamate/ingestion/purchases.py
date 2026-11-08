"""Purchase receipts (phiếu nhập) of one business, from the lists its README describes.

Two documented shapes: a receipt list with the total of each receipt ("tổng tiền phiếu
nhập", optional "trạng thái phiếu nhập"), or a purchase book whose lines carry "tiền nhập
của cả dòng" and are summed per receipt. A KiotViet receipt export without a README
section is read with the meanings KiotViet-export READMEs give its columns. Anything
undocumented, inconsistent or with an unknown status raises ``PurchaseSourceError``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    Section,
    _header_names,
    _matching_tables,
    _records,
    parse_amount,
    parse_day,
    parse_sections,
)
from datamate.semantics.text import fold

_FIELDS = {
    "ma phieu nhap": "receipt_id",
    "ngay nhap": "day",
    "tong tien phieu nhap": "total",
    "trang thai phieu nhap": "status",
    "tien nhap cua ca dong": "line_amount",
    "nha cung cap": "supplier_name",
    "ma nha cung cap": "supplier_code",
}
_KIOTVIET_RECEIPTS = {
    "Mã nhập hàng": "mã phiếu nhập", "Thời gian": "ngày nhập", "Mã NCC": "mã nhà cung cấp",
    "Cần trả NCC": "tổng tiền phiếu nhập", "Trạng thái": "trạng thái phiếu nhập",
}
_DONE = frozenset({"da nhap hang", "da nhap", "hoan thanh"})


class PurchaseSourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    day: date
    total: Decimal
    supplier: str | None  # supplier name, or code when the source only records a code
    cancelled: bool


@dataclass(frozen=True)
class PurchaseBook:
    receipts: tuple[Receipt, ...]
    supplier_names: bool  # True when ``supplier`` is a name, False for codes / unknown
    source: str


def _field(meaning: str) -> str | None:
    text = fold(re.sub(r"\([^)]*\)\s*$", "", meaning)).strip()
    return _FIELDS.get(text)


def _sections(catalog: Catalog) -> list[tuple[Section, dict[str, str]]]:
    found = []
    for section in parse_sections(catalog.readme):
        fields = {}
        for column, meaning in section.columns.items():
            field = _field(meaning)
            if field and field not in fields:
                fields[field] = column
        if {"receipt_id", "day"} <= set(fields) and ({"total", "line_amount"} & set(fields)):
            found.append((section, fields))
    if found:
        return found
    documented = {s.file for s in parse_sections(catalog.readme)}
    for table in catalog.tables:
        if table.path.name not in documented and set(_KIOTVIET_RECEIPTS) <= _header_names(table):
            section = Section(table.path.name, table.name, dict(_KIOTVIET_RECEIPTS))
            found.append((section, {_field(m): c for c, m in _KIOTVIET_RECEIPTS.items()}))
    return found


def _cancelled(label: object) -> bool:
    text = fold(str(label or ""))
    if "huy" in text.split():
        return True
    if text in _DONE:
        return False
    raise PurchaseSourceError("status", "Unknown purchase receipt status")


def load_purchases(catalog: Catalog) -> PurchaseBook:
    sections = _sections(catalog)
    headers = [(s, f) for s, f in sections if "total" in f]
    lines = [(s, f) for s, f in sections if "line_amount" in f and "total" not in f]
    chosen = headers or lines
    if not chosen:
        raise PurchaseSourceError("absent", "README describes no purchase receipts")
    if len(chosen) != 1:
        raise PurchaseSourceError("source", "Several purchase lists of one shape")
    section, fields = chosen[0]
    supplier_field = ("supplier_name" if "supplier_name" in fields
                      else "supplier_code" if "supplier_code" in fields else None)
    try:
        tables = _matching_tables(catalog, section)
        if not tables:
            raise PurchaseSourceError("source", "README purchase list is not present")
        rows = [row for table in tables
                for _ordinal, row in _records(table, section.columns)]
    except ReadmeSalesError as exc:
        raise PurchaseSourceError("source", str(exc)) from exc
    receipts: dict[str, dict] = {}
    totals: dict[str, Decimal] = defaultdict(Decimal)
    try:
        for row in rows:
            raw_id = row.get(fields["receipt_id"])
            if raw_id in (None, "") or not str(raw_id).strip():
                continue  # total / note rows
            key = str(raw_id).strip()
            day = parse_day(row.get(fields["day"]))
            supplier = row.get(fields[supplier_field]) if supplier_field else None
            supplier = None if supplier in (None, "") else str(supplier).strip()
            cancelled = _cancelled(row.get(fields["status"])) if "status" in fields else False
            record = {"day": day, "supplier": supplier, "cancelled": cancelled}
            if headers:
                if key in receipts:
                    raise PurchaseSourceError("key", "Receipt list repeats a receipt id")
                totals[key] = parse_amount(row.get(fields["total"]))
            else:
                if key in receipts and receipts[key] != record:
                    raise PurchaseSourceError("key", "Lines of one receipt disagree")
                totals[key] += parse_amount(row.get(fields["line_amount"]))
            receipts[key] = record
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PurchaseSourceError):
            raise
        raise PurchaseSourceError("value", f"Purchase value unreadable: {exc}") from exc
    if not receipts:
        raise PurchaseSourceError("absent", "Purchase list is empty")
    return PurchaseBook(
        tuple(Receipt(key, r["day"], totals[key], r["supplier"], r["cancelled"])
              for key, r in receipts.items()),
        supplier_field == "supplier_name", section.label)
