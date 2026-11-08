"""Effective price changes from a history documented by the business README."""

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    _header_names,
    _matching_tables,
    _records,
    meaning_field,
    parse_amount,
    parse_day,
    parse_sections,
)
from datamate.ingestion.sales import parse_date
from datamate.semantics.text import fold

# Identity fields of the documented sales-software exports. Other tables need
# explicit column descriptions.
_EXPORT_CODE = ("MaHangHoa", "Mã sản phẩm")


def load_price_increases(
    catalog: Catalog,
) -> tuple[dict[tuple[str, Decimal], date], str, bool]:
    """Return the first actual increase to each price, source, and whether keys are codes."""
    conventions = [line for line in catalog.readme.splitlines()
                   if "gia ban" in fold(line) and "theo don vi co ban" in fold(line)
                   and "den truoc lan doi gia sau" in fold(line)
                   and "dong dau moi mat hang la gia luc bat dau du lieu" in fold(line)]
    if len(conventions) != 1:
        raise ValueError("README does not define one effective price history")
    convention = conventions[0]
    references = re.findall(r"`([^`]+)\.([^`.]+)`", convention)
    options = []
    if len(references) == 2 and references[0][0] == references[1][0]:
        (list_name, new), (_, day) = references
        for table in catalog.tables:
            if table.name == list_name and table.path.suffix in (".sqlite", ".sqlite3", ".db"):
                options.append((table, new, day, None, _EXPORT_CODE[0], True))
    else:
        bold = re.findall(r"\*\*([^*]+)\*\*", convention)
        if len(bold) != 4 or fold(bold[0]) != "gia ban" or "trong" not in fold(convention):
            raise ValueError("README does not identify the price history columns")
        _, new, day, old = bold
        sections = parse_sections(catalog.readme)
        for table in catalog.tables:
            if not {new, day, old} <= _header_names(table):
                continue
            documented = [s for s in sections if table in _matching_tables(catalog, s)]
            if documented:
                identities = [(col, meaning_field(meaning)) for s in documented
                              for col, meaning in s.columns.items()
                              if meaning_field(meaning) in ("product_id", "product_name", "name")]
                codes = [col for col, role in identities if role == "product_id"]
                names = [col for col, role in identities if role != "product_id"]
                keys = codes or names
                if len(keys) == 1:
                    options.append((table, new, day, old, keys[0], bool(codes)))
            elif ("phan mem ban hang" in fold(catalog.readme)
                  and table.path.name.startswith("BanHang_")
                  and f"`{table.path.name}`" in catalog.readme and _EXPORT_CODE[1] in table.columns):
                options.append((table, new, day, old, _EXPORT_CODE[1], True))
    if len(options) != 1:
        raise ValueError("Price history is missing or ambiguous")
    table, new, day, old, identity, by_code = options[0]
    needed = {new, day, identity} | ({old} if old else set())
    rows = defaultdict(dict)
    try:
        for _, row in _records(table, needed):
            key = str(row[identity]).strip() if row.get(identity) is not None else ""
            if not key:
                raise ValueError("Price history has a blank product identity")
            key = key if by_code else fold(key)
            when = (parse_day(row.get(day)) if table.path.suffix.lower() == ".xlsx"
                    else parse_date(row.get(day)))
            price = parse_amount(row.get(new))
            raw_prior = row.get(old) if old else None
            prior = parse_amount(raw_prior) if raw_prior not in ("", None) else None
            if when in rows[key] or price < 0 or (prior is not None and prior < 0):
                raise ValueError("Price history has duplicate days or negative prices")
            rows[key][when] = (price, prior)
    except ReadmeSalesError as exc:
        raise ValueError("Price history does not match the README columns") from exc
    increases = {}
    for key, changes in rows.items():
        previous = None
        for when, (price, prior) in sorted(changes.items()):
            if old and prior != previous:
                raise ValueError("Previous price disagrees with the effective price history")
            if previous is not None and price > previous:
                increases.setdefault((key, price), when)
            previous = price
    return increases, f"{table.key}: {convention.strip()}", by_code
