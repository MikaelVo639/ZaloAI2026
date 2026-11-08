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
    _matching_tables,
    _records,
    meaning_field,
    parse_conventions,
    parse_quantity,
    parse_sections,
)
from datamate.semantics.text import fold

_KIOTVIET_STOCK = {"Mã hàng": "product_id", "Tên hàng": "product_name", "ĐVT": "base_unit"}
_CSV_STOCK = {"ID SP": "product_id", "Hàng hóa": "product_name", "Đơn vị": "base_unit"}
# Known conversion exports with an explicitly referenced source in the README.
_CONVERSION_EXPORTS = (
    ("Mã hàng", "ĐVT", "Quy đổi"),
    ("Mã sp", "Đơn vị", "Bằng"),
    ("MaHangHoa", "DonViTinh", "HeSoQuyDoi"),
)


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


@dataclass(frozen=True)
class Conversion:
    key: str
    by_code: bool
    big_unit: str
    base_unit: str
    factor: Decimal
    source: str


def load_conversions(catalog: Catalog) -> tuple[Conversion, ...]:
    """Read documented direct factors (one larger unit expressed in base units)."""
    lines = [line for line in catalog.readme.splitlines() if line.startswith("- ")
             and fold(line.split(":", 1)[0]) == "quy doi don vi"]
    if len(lines) != 1:
        raise StockSourceError("conversion", "No unique README conversion convention")
    line = lines[0]
    sources = re.findall(r"`([^`]+)`", line)
    conversions = []
    if not sources and line.rstrip().endswith(":"):
        items = parse_conventions(catalog.readme, strict=True).conversions
        conversions.extend(Conversion(name, False, big, base, factor, line.strip())
                           for (name, big), (factor, base) in items.items())
    elif len(sources) == 1 and "he so quy doi" in fold(line):
        source = sources[0]
        tables = [t for t in catalog.tables if source in (t.path.name, t.name)]
        sections = parse_sections(catalog.readme)
        for table in tables:
            fields = [s.fields for s in sections if table in _matching_tables(catalog, s)
                      and {"factor", "big_unit"} <= s.fields.keys()]
            if len(fields) == 1:
                cols = fields[0]
                by_code = "product_id" in cols
                identity = cols.get("product_id") or cols.get("product_name")
                if not identity:
                    raise StockSourceError("conversion", "Conversion has no product identity")
                big, factor = cols["big_unit"], cols["factor"]
                base = cols.get("base_unit")
            elif not fields:
                known = [p for p in _CONVERSION_EXPORTS if set(p) <= _header_names(table)]
                if len(known) != 1:
                    continue
                identity, big, factor = known[0]
                by_code, base = True, None
            else:
                raise StockSourceError("conversion", "Ambiguous conversion columns")
            required = {identity, big, factor} | ({base} if base else set())
            for _, row in _records(table, required):
                if any(row[col] in (None, "") for col in required):
                    raise StockSourceError("conversion", "Blank conversion value")
                key = str(row[identity]).strip() if by_code else fold(row[identity])
                conversions.append(Conversion(
                    key, by_code, fold(row[big]), fold(row[base]) if base else "",
                    parse_quantity(row[factor]), table.key))
    if not conversions:
        raise StockSourceError("conversion", "README conversion list is missing or unsupported")
    return tuple(conversions)


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
    elif (table.path.suffix.lower() == ".csv" and f"`{table.path.name}`" in catalog.readme
          and set(_CSV_STOCK) <= _header_names(table)):
        fields = {field: name for name, field in _CSV_STOCK.items()}
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
