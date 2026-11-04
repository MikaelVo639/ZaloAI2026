"""Worker-side baseline computations; every answer carries the code that produced it.

Functions taking ``cache`` run inside the child process of ``agent.worker``. The pure
operations (``select_valid``, ``revenue``, ...) are what the evidence ``code`` field
quotes, so a reviewer can re-run the exact calculation from a log.
"""

import inspect
import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any, Callable

from datamate.contracts import Candidate, Catalog, Plan
from datamate.ingestion.sales import Sale, load_sales


def select_valid(sales: list[Sale], start: date, end: date) -> list[Sale]:
    return [s for s in sales if start <= s.day <= end and s.status == "valid"]


def revenue(selected: list[Sale]) -> int:
    total = sum((s.amount for s in selected), Decimal(0))
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def order_count(selected: list[Sale]) -> int:
    return len({s.order_id for s in selected})


def product_ranking(selected: list[Sale]) -> list[tuple[str, str, Decimal]]:
    quantities: dict[str, Decimal] = defaultdict(Decimal)
    names: dict[str, str] = {}
    for sale in selected:
        quantities[sale.product_id] += sale.quantity_base
        names.setdefault(sale.product_id, sale.product_name)
    ranked = sorted(quantities, key=lambda key: (-quantities[key], key))
    return [(key, names[key], quantities[key]) for key in ranked]


@lru_cache(maxsize=None)
def _source(function: Callable[..., Any]) -> str:
    try:
        return inspect.getsource(function)
    except (OSError, TypeError):
        return f"{function.__module__}.{function.__qualname__}"


def _code(call: str, *functions: Callable[..., Any]) -> dict[str, Any]:
    return {"call": call, "functions": [f"{f.__module__}.{f.__qualname__}" for f in functions],
            "source": "\n".join(_source(f) for f in functions)}


def _catalog_key(catalog: Catalog) -> tuple:
    return catalog.business_id, tuple((str(t.path), t.name) for t in catalog.tables)


def prepare_sales(cache: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    """Load one business into the worker cache; adapter failures are cached as well."""
    key = _catalog_key(catalog)
    if cache.get("sales_key") != key:
        cache.clear()  # one business at a time keeps worker memory bounded
        cache["sales_key"] = key
        try:
            cache["sales"] = load_sales(catalog)
        except (ValueError, OSError) as exc:
            cache["sales"] = exc
    loaded = cache["sales"]
    if isinstance(loaded, Exception):
        return {"ok": False, "reason": str(loaded)}
    sales, source = loaded
    days = [s.day for s in sales]
    return {"ok": True, "source": source,
            "data_first_day": min(days).isoformat() if days else None,
            "data_last_day": max(days).isoformat() if days else None}


def answer_plan(cache: dict[str, Any], catalog: Catalog, plan: Plan) -> Candidate:
    prepared = prepare_sales(cache, catalog)
    if not prepared["ok"]:
        return Candidate(reason=prepared["reason"])
    sales, source = cache["sales"]
    evidence: dict[str, Any] = {
        **source, "intent": plan.intent, "start": plan.start.isoformat(),
        "end": plan.end.isoformat(), "rules": plan.evidence,
        "inputs": {"tables": source["tables"], "sales_rows": source["sales_rows"],
                   "data_first_day": prepared["data_first_day"],
                   "data_last_day": prepared["data_last_day"]}}
    if not sales or plan.start > plan.end:
        return Candidate(reason="Empty data or future-only period", evidence=evidence)
    days = [s.day for s in sales]
    if plan.end < min(days) or plan.start > max(days):
        # Absence of sales alone does not establish the business's coverage.
        coverage = re.search(r"Khoảng dữ liệu:\s*(\d{4}-\d{2}-\d{2})\s+đến\s+"
                             r"(\d{4}-\d{2}-\d{2})", catalog.readme)
        if coverage:
            first, last = map(date.fromisoformat, coverage.groups())
            if plan.end < first or plan.start > last:
                evidence["coverage_source"] = coverage.group()
                return Candidate(type="unanswerable", confidence=1.0,
                                 reason="Kỳ hỏi nằm hoàn toàn ngoài khoảng dữ liệu ghi trong README",
                                 evidence=evidence)
        return Candidate(reason="No sales in period; business coverage is not established",
                         evidence=evidence)
    selected = select_valid(sales, plan.start, plan.end)
    selection = (f"select_valid(sales, date.fromisoformat({plan.start.isoformat()!r}), "
                 f"date.fromisoformat({plan.end.isoformat()!r}))")
    evidence["selected_rows"] = len(selected)
    evidence["inputs"].update(selected_rows=len(selected),
                              selected_orders=len({s.order_id for s in selected}))
    if plan.intent == "revenue":
        value = revenue(selected)
        evidence.update(calculation="sum(line_amount) on valid whole orders; round VND half up",
                        code=_code(f"revenue({selection})", select_valid, revenue), result=value)
        return Candidate("number", value, 1.0, "", evidence)
    if plan.intent == "order_count":
        value = order_count(selected)
        evidence.update(calculation="count distinct order_id on valid whole orders",
                        code=_code(f"order_count({selection})", select_valid, order_count),
                        result=value)
        return Candidate("number", value, 1.0, "", evidence)
    if not selected or any(s.quantity_base is None or not s.product_id or not s.product_name
                           for s in selected):
        return Candidate(reason="Ranking needs explicit base quantities and product ids/names",
                         evidence=evidence)
    ranking = product_ranking(selected)
    answer = [name for _, name, _ in ranking[:plan.top_k]]
    result = answer[0] if plan.top_k == 1 else answer
    evidence.update(
        calculation="sum(quantity_base) by product_id, descending; ties by id",
        code=_code(f"product_ranking({selection})[:{plan.top_k}]",
                   select_valid, product_ranking),
        # One row past k shows whether the boundary is a tie.
        ranking=[{"product_id": key, "product_name": name, "quantity_base": str(quantity)}
                 for key, name, quantity in ranking[:plan.top_k + 1]],
        result=result)
    return Candidate("string" if plan.top_k == 1 else "list", result, 1.0, "", evidence)
