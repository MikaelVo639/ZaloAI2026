"""Stock at the end of the last data day, read from the list the business README names.

The README's convention line ("**Tồn kho**: **Tồn cuối kỳ** là tồn cuối ngày cuối có dữ
liệu, theo đơn vị cơ bản") names the stock column; the table holding it is described
column by column in the README, or is a KiotViet stock report (Mã hàng / Tên hàng / ĐVT,
as KiotViet-export READMEs document them). Anything else raises ``StockSourceError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.debt import _coverage_day
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    _header_names,
    _records,
    meaning_field,
    parse_quantity,
    parse_sections,
)
from datamate.semantics.text import fold

_KIOTVIET_STOCK = {"Mã hàng": "product_id", "Tên hàng": "product_name", "ĐVT": "base_unit"}


class StockSourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class StockItem:
    product_id: str | None
    product_name: str | None
    base_unit: str | None
    quantity: Decimal


@dataclass(frozen=True)
class StockSnapshot:
    day: date  # "Dữ liệu có đến hết …": the stock is the end of this day
    items: tuple[StockItem, ...]
    source: str
    convention: str


def _convention(readme: str) -> tuple[str, str]:
    for line in readme.splitlines():
        text = fold(line)
        if ("ton kho" in text and "ton cuoi ngay cuoi co du lieu" in text
                and "theo don vi co ban" in text):
            names = re.findall(r"\*\*([^*]+)\*\*", line)
            if len(names) == 2 and fold(names[0]) == "ton kho":
                return names[1].strip(), line.strip()
    raise StockSourceError("absent", "README states no end-of-data stock column")


def load_stock(catalog: Catalog) -> StockSnapshot:
    column, convention = _convention(catalog.readme)
    day = _coverage_day(catalog.readme)
    if day is None:
        raise StockSourceError("coverage", "README states no last data day")
    tables = [table for table in catalog.tables if column in _header_names(table)]
    if len(tables) != 1:
        raise StockSourceError("source", "The stock column is in no table or in several")
    table = tables[0]
    documented = {s.file: s for s in parse_sections(catalog.readme)}.get(table.path.name)
    if documented is not None:
        fields = {meaning_field(meaning) or ("stock" if name == column else None): name
                  for name, meaning in documented.columns.items()}
        fields["stock"] = column
        fields.pop(None, None)
        fields = {k: v for k, v in fields.items()
                  if k in ("product_id", "product_name", "name", "base_unit", "stock")}
        if "name" in fields:
            fields.setdefault("product_name", fields.pop("name"))
    elif set(_KIOTVIET_STOCK) <= _header_names(table):
        fields = {field: name for name, field in _KIOTVIET_STOCK.items()}
        fields["stock"] = column
    else:
        raise StockSourceError("columns", "Stock list columns are not documented")
    if "product_id" not in fields and "product_name" not in fields:
        raise StockSourceError("columns", "Stock list has no product code or name column")
    items: list[StockItem] = []
    seen: set[str] = set()
    try:
        for _ordinal, row in _records(table, fields.values()):
            raw = row.get(fields["stock"])
            code = row.get(fields.get("product_id", ""), None)
            name = row.get(fields.get("product_name", ""), None)
            if raw in (None, "") and code in (None, "") and name in (None, ""):
                continue
            key = str(code if code not in (None, "") else name).strip()
            if not key or key in seen:
                raise StockSourceError("key", "Stock list has a blank or repeated product")
            seen.add(key)
            unit = row.get(fields.get("base_unit", ""), None)
            items.append(StockItem(
                None if code in (None, "") else str(code).strip(),
                None if name in (None, "") else str(name).strip(),
                None if unit in (None, "") else str(unit).strip(),
                parse_quantity(raw)))
    except (ReadmeSalesError, ValueError, TypeError) as exc:
        if isinstance(exc, StockSourceError):
            raise
        raise StockSourceError("value", f"Stock list unreadable: {exc}") from exc
    return StockSnapshot(day, tuple(items), table.key, convention)
