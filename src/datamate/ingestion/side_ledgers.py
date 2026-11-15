"""Side books of a phone shop that the sales book does not carry: phones taken in from
customers (thu máy cũ) and instalment plans (trả góp).

Both are read from their headers only when the README names the book and its money column
("giá thu", "trả trước", "góp/tháng", "đã góp"), and every row must read cleanly; anything
else raises ``SideLedgerError`` so the question is left unanswered.

Instalment book (as such READMEs describe it): the first row of a plan carries the purchase
day, the sales-book number, the price of the whole order and the down payment; each payment
is a row with its day and amount, the first on the plan's own row, later ones on rows that
carry only the plan number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from datamate.contracts import Catalog, Table
from datamate.semantics.text import fold


class SideLedgerError(ValueError):
    pass


@dataclass(frozen=True)
class TradeIn:
    day: date
    price: Decimal


@dataclass(frozen=True)
class Instalment:
    number: str
    day: date
    price: Decimal
    down_payment: Decimal
    payments: tuple[tuple[date, Decimal], ...]


def _header(table: Table) -> dict[str, str]:
    """Folded header -> column, for a table whose header row is its first row."""
    columns = {}
    for column in table.columns:
        key = fold(str(column))
        if key in columns:
            raise SideLedgerError("A side book repeats a column")
        columns[key] = column
    return columns


def _rows(table: Table):
    from datamate.ingestion.catalog import iter_data_rows

    for _ordinal, row in iter_data_rows(table):
        if all(value is None or not str(value).strip() for value in row.values()):
            continue
        yield row


def _blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def trade_ins(catalog: Catalog) -> tuple[TradeIn, ...]:
    from datamate.ingestion.readme_sales import parse_amount, parse_day

    readme = fold(catalog.readme)
    if not re.search(r"\bthu may cu\b", readme) or not re.search(r"\bgia thu\b", readme):
        raise SideLedgerError("The README names no book of phones taken in")
    books = [t for t in catalog.tables if {"ngay", "gia thu"} <= _header(t).keys()]
    if len(books) != 1:
        raise SideLedgerError("No single book of phones taken in")
    columns = _header(books[0])
    result = []
    for row in _rows(books[0]):
        day, price = row.get(columns["ngay"]), row.get(columns["gia thu"])
        if _blank(day) or _blank(price):
            raise SideLedgerError("A phone taken in without a day or a price")
        try:
            entry = TradeIn(parse_day(day), parse_amount(price))
        except (TypeError, ValueError) as exc:
            raise SideLedgerError("Unreadable day or price of a phone taken in") from exc
        if entry.price < 0:
            raise SideLedgerError("Negative price of a phone taken in")
        result.append(entry)
    if not result:
        raise SideLedgerError("The book of phones taken in is empty")
    return tuple(result)


_PLAN_HEADS = {"ngay", "stt", "gia", "tra truoc", "so ky", "ngay gop", "da gop"}


def instalments(catalog: Catalog) -> tuple[Instalment, ...]:
    from datamate.ingestion.readme_sales import parse_amount, parse_day

    readme = fold(catalog.readme)
    if not (re.search(r"\btra gop\b|\bso gop\b", readme) and re.search(r"\btra truoc\b", readme)
            and re.search(r"\bda gop\b", readme)):
        raise SideLedgerError("The README names no instalment book")
    books = [t for t in catalog.tables if _PLAN_HEADS <= _header(t).keys()]
    if len(books) != 1:
        raise SideLedgerError("No single instalment book")
    c = _header(books[0])
    plans: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    try:
        for row in _rows(books[0]):
            number = row.get(c["stt"])
            if _blank(number):
                raise SideLedgerError("An instalment row without a plan number")
            number = str(int(number) if isinstance(number, float) and number.is_integer()
                         else number).strip()
            opening = not _blank(row.get(c["gia"]))
            if opening:
                if number in plans:
                    raise SideLedgerError("Two instalment plans share a number")
                if _blank(row.get(c["ngay"])) or _blank(row.get(c["tra truoc"])):
                    raise SideLedgerError("An instalment plan without a day or down payment")
                plans[number] = {"day": parse_day(row[c["ngay"]]),
                                 "price": parse_amount(row[c["gia"]]),
                                 "down": parse_amount(row[c["tra truoc"]]), "paid": []}
                order.append(number)
            elif number not in plans:
                raise SideLedgerError("A payment before its instalment plan")
            paid_day, paid = row.get(c["ngay gop"]), row.get(c["da gop"])
            if _blank(paid_day) != _blank(paid):
                raise SideLedgerError("A payment without its day or amount")
            if not _blank(paid):
                plans[number]["paid"].append((parse_day(paid_day), parse_amount(paid)))
            elif not opening:
                raise SideLedgerError("A payment row without a payment")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, SideLedgerError):
            raise
        raise SideLedgerError("Unreadable instalment row") from exc
    if not plans:
        raise SideLedgerError("The instalment book is empty")
    result = []
    for number in order:
        plan = plans[number]
        paid = plan["paid"]
        if (plan["down"] < 0
                or any(day < plan["day"] or amount < 0 for day, amount in paid)
                or plan["down"] + sum((amount for _, amount in paid), Decimal(0)) > plan["price"]):
            raise SideLedgerError("An instalment plan does not add up")
        result.append(Instalment(number, plan["day"], plan["price"], plan["down"],
                                 tuple(paid)))
    return tuple(result)
