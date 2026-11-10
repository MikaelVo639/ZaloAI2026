"""Stock at the end of the last data day, read from the list the business README names.

The README's convention line ("**Tồn kho**: **Tồn cuối kỳ** là tồn cuối ngày cuối có dữ
liệu, theo đơn vị cơ bản") names the stock column; the table holding it is described
column by column in the README, or is a KiotViet stock report (Mã hàng / Tên hàng / ĐVT,
as KiotViet-export READMEs document them).

A README with no stock convention at all but the lot convention ("**Lô, hạn dùng**: mỗi
dòng nhập là một lô, số lô không trùng … Hàng khách trả về lại lô đã bán") gets its stock
from the lots instead: what each lot received, less what sales took from it, plus what
customers brought back to it, counted from the start of the data (rulebook "Đến hết ngày
X"). Every quantity must be documented in base units. Anything else raises
``StockSourceError``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.debt import _coverage_day
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    Section,
    _header_names,
    _matching_tables,
    _records,
    meaning_field,
    parse_amount,
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
            # Matching a section can open XLSX sheets to locate their headers.
            fields = [s.fields for s in sections if {"factor", "big_unit"} <= s.fields.keys()
                      and table in _matching_tables(catalog, s)]
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


def load_stock_costs(catalog: Catalog) -> tuple[dict[str, Decimal], str]:
    """Read the documented base-unit cost from a product catalogue."""
    sources = []
    for section in parse_sections(catalog.readme):
        identity = section.fields.get("product_id")
        if not identity or "base_unit" not in section.fields or "order_id" in section.fields:
            continue
        names = [name for name, meaning in section.columns.items()
                 if fold(meaning) in {"gia von", "gia von mot don vi co ban",
                                      "gia von theo don vi co ban"}]
        if len(names) == 1:
            sources.extend((table, identity, names[0])
                           for table in _matching_tables(catalog, section))
    if len(sources) != 1:
        raise StockSourceError("columns", "No unique product catalogue with base-unit cost")
    table, identity, cost = sources[0]
    costs = {}
    for _, row in _records(table, (identity, cost)):
        code = str(row[identity] or "").strip()
        if row[cost] in (None, ""):
            continue
        value = parse_amount(row[cost])
        if not code or code in costs or value < 0:
            raise StockSourceError("key", "Invalid or repeated catalogue cost")
        costs[code] = value
    return costs, table.key


def load_stock(catalog: Catalog) -> StockSnapshot:
    try:
        column, convention = _convention(catalog.readme)
    except StockSourceError as exc:
        # A README that defines stock some other way ("**Tồn kho**: …") is not overridden.
        if exc.code != "absent" or _stock_line(catalog.readme) or not _lot_rule(catalog.readme):
            raise
        column, convention = None, ""
    day = _coverage_day(catalog.readme)
    if day is None:
        raise StockSourceError("coverage", "README states no last data day")
    if column is None:
        return _lot_stock(catalog, day)
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


# Column meanings of the lot lists. The three quantities count only when the README notes
# them "(đơn vị cơ bản)"; text after ";" is a remark ("lớn hơn 0: đơn đó có trả hàng").
_LOT_MEANINGS = {
    "so lo": "lot", "ma hang": "product_id", "ma don": "order_id",
    "so luong nhap": "received", "so luong ban lay tu lo": "sold",
    "so luong khach tra lai": "returned",
    "ten": "product_name", "ten hang": "product_name", "don vi co ban": "base_unit",
}
_BASE_QUANTITIES = frozenset({"received", "sold", "returned"})
_LOT_RULE = ("moi dong nhap la mot lo", "so lo khong trung", "hang khach tra ve lai lo da ban")


def _stock_line(readme: str) -> bool:
    return any(re.match(r"\s*-\s+\*\*", line) and fold(line.split(":", 1)[0]) == "ton kho"
               for line in readme.splitlines())


def _lot_rule(readme: str) -> str:
    lines = [line.strip() for line in readme.splitlines()
             if re.match(r"\s*-\s+\*\*", line) and all(m in fold(line) for m in _LOT_RULE)]
    return lines[0] if len(lines) == 1 else ""


def _lot_field(meaning: str) -> str | None:
    head = meaning.split(";", 1)[0].strip()
    match = re.fullmatch(r"(.*?)\s*\(([^)]*)\)\s*\.?", head)
    text, note = (match.group(1), match.group(2)) if match else (head, "")
    field = _LOT_MEANINGS.get(fold(text))
    if field in _BASE_QUANTITIES and fold(note) != "don vi co ban":
        return None
    return field


def _lot_sections(catalog: Catalog) -> dict[str, list[tuple[Section, dict[str, str]]]]:
    groups: dict[str, list[tuple[Section, dict[str, str]]]] = {
        "receipts": [], "sales": [], "products": []}
    for section in parse_sections(catalog.readme):
        fields: dict[str, str] = {}
        for column, meaning in section.columns.items():
            field = _lot_field(meaning)
            if field and field not in fields:
                fields[field] = column
        keys = set(fields)
        if {"lot", "product_id", "received"} <= keys and not keys & {"order_id", "sold"}:
            groups["receipts"].append((section, fields))
        elif {"order_id", "lot", "sold", "returned"} <= keys and "received" not in keys:
            groups["sales"].append((section, fields))
        elif ({"product_id", "product_name", "base_unit"} <= keys
              and not keys & {"lot", "order_id"}):
            groups["products"].append((section, fields))
    if not groups["receipts"] or not groups["sales"] or len(groups["products"]) != 1:
        raise StockSourceError("columns", "Lot lists are not documented in base units")
    return groups


def _rows(catalog: Catalog, section: Section, fields: dict[str, str], keys: tuple[str, ...],
          sources: set[str]):
    tables = _matching_tables(catalog, section)
    if not tables:
        raise StockSourceError("source", f"No table for {section.label}")
    for table in tables:
        sources.add(table.key)
        for _ordinal, row in _records(table, [fields[k] for k in keys]):
            values = {k: row.get(fields[k]) for k in keys}
            if all(v in (None, "") for v in values.values()):
                continue
            yield values


def _text(value: object) -> str:
    if value in (None, ""):
        raise StockSourceError("key", "Lot list has a blank lot, order or product")
    return str(value).strip()


def _lot_stock(catalog: Catalog, day: date) -> StockSnapshot:
    """Stock at the end of the data from the lots: received - sold + brought back."""
    rule = _lot_rule(catalog.readme)
    sources: set[str] = set()
    try:
        groups = _lot_sections(catalog)
        product_of: dict[str, str] = {}
        left: dict[str, Decimal] = defaultdict(Decimal)
        for section, fields in groups["receipts"]:
            for row in _rows(catalog, section, fields, ("lot", "product_id", "received"), sources):
                lot = _text(row["lot"])
                if lot in product_of:
                    raise StockSourceError("key", "A lot number is repeated")
                product_of[lot] = _text(row["product_id"])
                left[product_of[lot]] += parse_quantity(row["received"])
        for section, fields in groups["sales"]:
            for row in _rows(catalog, section, fields, ("order_id", "lot", "sold", "returned"),
                             sources):
                _text(row["order_id"])
                lot = _text(row["lot"])
                if lot not in product_of:
                    raise StockSourceError("key", "A sale takes from a lot never received")
                sold, back = parse_quantity(row["sold"]), parse_quantity(row["returned"])
                if sold < 0 or back < 0 or back > sold:
                    raise StockSourceError("value", "A lot sale has an invalid quantity")
                left[product_of[lot]] -= sold - back
        if any(quantity < 0 for quantity in left.values()):
            raise StockSourceError("value", "Stock from the lots falls below zero")
        section, fields = groups["products"][0]
        named: dict[str, tuple[str | None, str | None]] = {}
        for row in _rows(catalog, section, fields, ("product_id", "product_name", "base_unit"),
                         sources):
            code = _text(row["product_id"])
            if code in named:
                raise StockSourceError("key", "Product list repeats a code")
            name, unit = row["product_name"], row["base_unit"]
            named[code] = (None if name in (None, "") else str(name).strip(),
                           None if unit in (None, "") else str(unit).strip())
    except (ReadmeSalesError, ValueError, TypeError) as exc:
        if isinstance(exc, StockSourceError):
            raise
        raise StockSourceError("value", f"Lot lists unreadable: {exc}") from exc
    items = tuple(StockItem(code, *named.get(code, (None, None)), quantity)
                  for code, quantity in sorted(left.items()))
    return StockSnapshot(day, items, " + ".join(sorted(sources)), rule)
