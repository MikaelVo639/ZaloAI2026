"""Local source adapters for delivery trips and operating expenses."""

import re
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.debt import parse_day
from datamate.ingestion.readme_sales import (
    _header_names,
    _matching_tables,
    _records,
    parse_amount,
    parse_sections,
)
from datamate.ingestion.sales import parse_number
from datamate.semantics.text import fold


def load_deliveries(catalog: Catalog) -> tuple[dict[tuple[str, int], date], list[str]]:
    """One delivery date per documented order and trip, regardless of order status."""
    deliveries: dict[tuple[str, int], date] = {}
    sources = []
    for section in parse_sections(catalog.readme):
        fields = {}
        for column, meaning in section.columns.items():
            label = fold(meaning)
            if label == "ma don":
                fields["order"] = column
            elif label == "ngay giao":
                fields["date"] = column
            elif label.startswith("dot giao"):
                fields["trip"] = column
        if fields.keys() != {"order", "date", "trip"}:
            continue
        tables = _matching_tables(catalog, section)
        if not tables:
            raise ValueError("Documented delivery table is missing")
        for table in tables:
            sources.append(table.key)
            for _, row in _records(table, fields.values()):
                values = [row[fields[key]] for key in ("order", "trip", "date")]
                if all(value in (None, "") for value in values):
                    continue
                order, trip, raw_date = values
                if order in (None, "") or not str(order).strip():
                    raise ValueError("Delivery row is missing an order code")
                number = parse_number(trip)
                if number <= 0 or number != number.to_integral_value():
                    raise ValueError("Invalid delivery trip number")
                key = str(order).strip(), int(number)
                day = parse_day(raw_date)
                if deliveries.setdefault(key, day) != day:
                    raise ValueError("Delivery dates conflict for the same order and trip")
    if not sources:
        raise ValueError("No documented order/trip/date delivery source")
    return deliveries, sources


def load_expenses(catalog: Catalog) -> tuple[list[tuple[date, Decimal]], str]:
    line = next((line for line in catalog.readme.splitlines()
                 if fold(line.split(":", 1)[0]) == "so chi phi"), "")
    amounts = re.findall(r"\*\*([^*]+)\*\*", line.partition(":")[2])
    if len(amounts) != 1 or "theo thang" not in fold(line):
        raise ValueError("README does not specify a monthly expense ledger")
    amount = amounts[0]
    sources = []
    for table in catalog.tables:
        if (f"`{table.path.name}`" not in catalog.readme
                or "chiphi" not in fold(table.path.stem).replace(" ", "")):
            continue
        headers = _header_names(table)
        for day, category in (("Ngày chi", "Mục"), ("Ngày mua", "Nhóm")):
            if {amount, day, category} <= headers:
                sources.append((table, day, category))
    if len(sources) != 1:
        raise ValueError("No unique documented expense source with date and amount")
    table, day_column, category = sources[0]
    rows = []
    for _, row in _records(table, (amount, day_column, category)):
        day, value = row[day_column], row[amount]
        if day in (None, "") and value in (None, "") and row[category] in (None, ""):
            continue
        rows.append((parse_day(day), parse_amount(value)))
    return rows, table.key
