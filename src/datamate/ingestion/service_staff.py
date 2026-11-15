"""A documented list of the people who do service tickets, with their commission rate.

A ticket register often names the worker as the owner calls them ("a Tuấn", "tuấn") while
the list keeps the full name. A spelling is joined to a listed person only when, without
accents, case and a leading form of address, it is the end of exactly one listed full name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from datamate.contracts import Catalog
from datamate.semantics.text import fold

# Forms of address written before a name: "a Tuấn", "anh Tuấn", "chú Ba", "thợ Tư".
_ADDRESS = frozenset({"a", "anh", "c", "chi", "chu", "em", "e", "bac", "co", "ong", "ba",
                      "tho", "thay", "cau", "di", "o"})
_NAME_HEAD = re.compile(r"^(?:ho (?:va )?ten|ten)(?: (?:day du|cua))?(?: (?:tho|nhan vien|ky thuat vien))?$")
_RATE_HEAD = re.compile(r"\bhoa hong\b")


@dataclass(frozen=True)
class Worker:
    name: str
    commission_percent: Decimal | None


def _head(meaning: str) -> str:
    return fold(re.split(r"[:;,]", re.sub(r"\([^)]*\)", " ", meaning), maxsplit=1)[0]).strip()


def worker_list(catalog: Catalog) -> list[Worker] | None:
    """The README's list of workers (full name, commission %), or None when there is none
    or it cannot be read without guessing."""
    from datamate.ingestion.readme_sales import (ReadmeSalesError, _matching_tables,
                                                 _records, parse_sections)

    found = []
    for section in parse_sections(catalog.readme):
        names = [c for c, m in section.columns.items() if _NAME_HEAD.fullmatch(_head(m))]
        rates = [c for c, m in section.columns.items()
                 if _RATE_HEAD.search(fold(m)) and ("%" in c or "phan tram" in fold(m))]
        if len(names) == 1 and len(rates) <= 1 and (rates or "tho" in fold(" ".join(
                section.columns.values())).split()):
            found.append((section, names[0], rates[0] if rates else None))
    if len(found) != 1:
        return None
    section, name_column, rate_column = found[0]
    try:
        tables = _matching_tables(catalog, section)
        if len(tables) != 1:
            return None
        workers = []
        for _ordinal, row in _records(tables[0], [c for c in (name_column, rate_column) if c]):
            name = row.get(name_column)
            if name is None or not str(name).strip():
                continue
            rate = None
            if rate_column is not None:
                raw = row.get(rate_column)
                rate = _percent(raw)
                if rate is None:
                    return None
            workers.append(Worker(str(name).strip(), rate))
    except (ReadmeSalesError, OSError, ValueError, InvalidOperation):
        return None
    keys = [fold(w.name) for w in workers]
    if not workers or len(set(keys)) != len(keys):
        return None
    return workers


def _percent(raw: Any) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        value = Decimal(str(raw))
        # A spreadsheet percent cell holds 0.1 for 10%.
        if value.is_finite() and 0 < value < 1:
            value *= 100
    else:
        text = str(raw).strip().rstrip("%").strip().replace(",", ".")
        try:
            value = Decimal(text)
        except InvalidOperation:
            return None
    return value if value.is_finite() and 0 <= value <= 100 else None


def spelling_key(written: str) -> tuple[str, ...]:
    words = fold(written).split()
    while len(words) > 1 and words[0] in _ADDRESS:
        words = words[1:]
    return tuple(words)


def match_worker(written: str, workers: Iterable[Worker]) -> Worker | None:
    """The one listed worker whose full name ends with this spelling, else None."""
    key = spelling_key(written)
    if not key:
        return None
    hits = [w for w in workers if tuple(fold(w.name).split()[-len(key):]) == key]
    return hits[0] if len(hits) == 1 else None
