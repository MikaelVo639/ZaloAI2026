"""Calculations for sales, delivery and operating-expense measures."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from datamate.ingestion.expiry import Lot
from datamate.ingestion.sales import Sale
from datamate.ingestion.stock import StockItem
from datamate.semantics.text import fold


def product_key(sale: Sale) -> tuple[str, str]:
    if sale.product_id and not sale.product_id_is_name:
        return "code", sale.product_id
    key = " ".join(fold(sale.product_name or sale.product_id or "").split())
    if not key:
        raise ValueError("Valid sales lines lack a product identity")
    return "name", key


def unsold_count(rows: list[Sale], start: date, end: date) -> int:
    before, during = set(), set()
    for sale in rows:
        if sale.status == "valid" and sale.day <= end:
            (before if sale.day < start else during).add(product_key(sale))
    return len(before - during)


def gross_profit(rows: list[Sale]) -> Decimal:
    total = Decimal(0)
    for sale in rows:
        if sale.quantity_sold is None or sale.unit_cost_at_sale is None:
            raise ValueError("Selected lines lack quantity or cost per selling unit")
        total += sale.amount - sale.quantity_sold * sale.unit_cost_at_sale
    return total


def top_gross_profit(rows: list[Sale], ties_by_code: bool) -> str:
    grouped: dict[tuple[str, str], list[Sale]] = defaultdict(list)
    names = {}
    for sale in rows:
        if not sale.product_name:
            raise ValueError("Selected lines lack a product name")
        key = product_key(sale)
        grouped[key].append(sale)
        names.setdefault(key, sale.product_name)
    ranked = sorted((-gross_profit(lines), key) for key, lines in grouped.items())
    if not ranked or (not ties_by_code and len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        raise ValueError("No unique product has the highest gross profit")
    return names[ranked[0][1]]


def net_profit(rows: list[Sale], expenses: list[tuple[date, Decimal]],
               start: date, end: date) -> Decimal:
    return gross_profit(rows) - sum(value for day, value in expenses if start <= day <= end)


def delivery_count(deliveries: dict[tuple[str, int], date], start: date, end: date) -> int:
    return sum(start <= day <= end for day in deliveries.values())


def stock_value(items: tuple[StockItem, ...], costs: dict[str, Decimal]) -> Decimal:
    total = Decimal(0)
    for item in items:
        if item.quantity > 0:
            if item.product_id not in costs:
                raise ValueError("In-stock product has no catalogue cost")
            total += item.quantity * costs[item.product_id]
    return total


def expiring_lots(lots: tuple[Lot, ...], first: date, last: date) -> list[Lot]:
    return [lot for lot in lots if lot.remaining > 0 and lot.expires is not None
            and first <= lot.expires <= last]


def earliest_expiry(lots: tuple[Lot, ...], product: str, today: date) -> date:
    matches = expiring_lots(tuple(lot for lot in lots if lot.product == product), today, date.max)
    if not matches:
        raise ValueError("No non-expired lot with remaining stock")
    return min(lot.expires for lot in matches)


def expiring_lot_cost(lots: list[Lot]) -> Decimal:
    total = Decimal(0)
    for lot in lots:
        if lot.unit_cost is None:
            raise ValueError("No base-unit purchase cost for an expiring lot")
        total += lot.remaining * lot.unit_cost
    return total


def expiring_products(lots: list[Lot], names: dict[str, str]) -> list[str]:
    first_expiry = {}
    for lot in lots:
        first_expiry[lot.product] = min(lot.expires, first_expiry.get(lot.product, lot.expires))
    return [names[code] for code in sorted(first_expiry, key=lambda code: (first_expiry[code], code))]
