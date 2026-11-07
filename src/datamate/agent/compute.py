"""Worker-side baseline computations; every answer carries the code that produced it.

Functions taking ``cache`` run inside the child process of ``agent.worker``. The pure
operations (``select_valid``, ``revenue``, ...) are what the evidence ``code`` field
quotes, so a reviewer can re-run the exact calculation from a log.
"""

import inspect
import re
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any, Callable

from datamate.agent.sales_source import load_business_sales
from datamate.contracts import DEBT_TABLE_COLUMNS, Candidate, Catalog, DebtLedger, Plan
from datamate.ingestion.debt import DebtSourceError, _coverage_day, load_debt_ledger
from datamate.ingestion.sales import Sale
from datamate.semantics.rules import DEBT_TABLE_FEATURES, read_rules
from datamate.semantics.text import fold


def select_valid(sales: list[Sale], start: date, end: date) -> list[Sale]:
    return [s for s in sales if start <= s.day <= end and s.status == "valid"]


def revenue(selected: list[Sale]) -> int:
    total = sum((s.amount for s in selected), Decimal(0))
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def order_count(selected: list[Sale]) -> int:
    return len({s.order_id for s in selected})


def select_period(sales: list[Sale], start: date, end: date) -> list[Sale]:
    """All rows ordered in the period, any status (needed for cancellation rates)."""
    return [s for s in sales if start <= s.day <= end]


def cancelled_orders(rows: list[Sale]) -> int:
    # Order status is per order: the adapter rejects conflicting states for one order.
    return len({s.order_id for s in rows if s.status == "cancelled"})


def cancel_rate(rows: list[Sale]) -> float | None:
    orders = {s.order_id: s.status for s in rows}
    if not orders:
        return None
    cancelled = sum(1 for status in orders.values() if status == "cancelled")
    rate = Decimal(cancelled) * 100 / Decimal(len(orders))
    return float(rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def average_order_value(selected: list[Sale]) -> int | None:
    orders = {s.order_id for s in selected}
    if not orders:
        return None
    total = sum((s.amount for s in selected), Decimal(0))
    return int((total / len(orders)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def daily_revenue(selected: list[Sale]) -> list[tuple[date, Decimal]]:
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for sale in selected:
        by_day[sale.day] += sale.amount
    return sorted(by_day.items(), key=lambda item: (-item[1], item[0]))


def ranking_candidates(sales: list[Sale], eligible: frozenset[int] | None) -> list[Sale]:
    """Rows allowed into a product ranking (the joined adapter excludes service fees)."""
    if eligible is None:
        return list(sales)
    return [s for index, s in enumerate(sales) if index in eligible]


def product_ranking(selected: list[Sale]) -> list[tuple[str, str, Decimal]]:
    quantities: dict[str, Decimal] = defaultdict(Decimal)
    names: dict[str, str] = {}
    for sale in selected:
        quantities[sale.product_id] += sale.quantity_base
        names.setdefault(sale.product_id, sale.product_name)
    ranked = sorted(quantities, key=lambda key: (-quantities[key], key))
    return [(key, names[key], quantities[key]) for key in ranked]


def period_revenues(sales: list[Sale], periods: list[tuple[date, date]]) -> list[int]:
    """Revenue of each period, in the order given (rulebook: list in question order)."""
    return [revenue(select_valid(sales, start, end)) for start, end in periods]


# How shops write "khách lẻ" (walk-in) in the customer-name column; folded, closed list.
WALK_IN_NAMES = frozenset({"khach le", "kl", "le", "khach vang lai", "vang lai"})


def _customer_name_key(value: str | None) -> str | None:
    name = " ".join(re.sub(r"[^\w\s]", " ", (value or "").lower()).split())
    return None if not name or fold(name) in WALK_IN_NAMES else name


def customer_keys(sales: list[Sale]) -> dict[int, str | None]:
    """Customer of each sale by the rulebook's "Xác định khách": the customer code when the
    shop records codes, otherwise the name written on the order (spacing/case folded);
    walk-in orders (no code / no name / "Khách lẻ") belong to no customer."""
    has_codes = any(s.customer_id for s in sales)
    keys: dict[int, str | None] = {}
    for index, sale in enumerate(sales):
        if has_codes:
            keys[index] = sale.customer_id or None
            continue
        keys[index] = _customer_name_key(sale.customer_name)
    return keys


def customer_totals(sales: list[Sale]) -> dict[str, dict[str, Any]]:
    """Revenue, valid orders and a display name per customer (input: valid rows only)."""
    totals: dict[str, dict[str, Any]] = {}
    for index, key in customer_keys(sales).items():
        if key is None:
            continue
        sale = sales[index]
        entry = totals.setdefault(key, {"revenue": Decimal(0), "orders": set(),
                                        "name": sale.customer_name or key,
                                        "code": sale.customer_id})
        entry["revenue"] += sale.amount
        entry["orders"].add(sale.order_id)
    return totals


def customer_ranking(selected: list[Sale], by: str) -> list[tuple[str, str, Decimal]]:
    """(key, display name, measure) descending; ties by code ascending (no code: name)."""
    totals = customer_totals(selected)
    measure = (lambda e: e["revenue"]) if by == "revenue" else (lambda e: Decimal(len(e["orders"])))
    rows = [(key, entry["code"] or entry["name"], entry["name"], measure(entry))
            for key, entry in totals.items()]
    rows.sort(key=lambda row: (-row[3], fold(row[1])))
    return [(key, name, value) for key, _tie, name, value in rows]


def customers_meeting(selected: list[Sale], minimum_orders: int) -> tuple[int, int]:
    """(customers with at least ``minimum_orders`` valid orders, customers who bought)."""
    totals = customer_totals(selected)
    return sum(len(e["orders"]) >= minimum_orders for e in totals.values()), len(totals)


def count_at_least(selected: list[Sale], subject: str, minimum: Decimal) -> int:
    """Orders / days with revenue >= minimum, or products in >= minimum distinct orders
    (valid rows only; rulebook: "từ N trở lên" includes N)."""
    if subject == "order":
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for sale in selected:
            totals[sale.order_id] += sale.amount
        return sum(_vnd(v) >= minimum for v in totals.values())
    if subject == "day":
        return sum(_vnd(v) >= minimum for _, v in daily_revenue(selected))
    orders: dict[str, set[str]] = defaultdict(set)
    for sale in selected:
        if sale.product_id:
            orders[sale.product_id].add(sale.order_id)
    return sum(len(v) >= minimum for v in orders.values())


def _vnd(value: Decimal) -> Decimal:
    return value.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def quantity_in_unit(selected: list[Sale], unit: str) -> Decimal | None:
    """Base quantity of the products whose base unit is ``unit``; None if a base unit is
    unknown (the rulebook counts only products whose base unit is the one asked)."""
    total = Decimal(0)
    for sale in selected:
        if sale.base_unit is None or sale.quantity_base is None:
            return None
        if fold(sale.base_unit) == fold(unit):
            total += sale.quantity_base
    return total


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


def prepare_sales(cache: dict[str, Any], catalog: Catalog, rulebook: str = "") -> dict[str, Any]:
    """Load one business into the worker cache; adapter failures are cached as well.

    ``rulebook`` is the current split's rulebook: the joined adapter's capabilities
    come from it, so a different rulebook reloads the business.
    """
    key = _catalog_key(catalog)
    if cache.get("sales_key") != key or cache.get("sales_rulebook", "") != rulebook:
        cache.pop("ranking_eligible", None)
        cache["sales_key"] = key
        cache["sales_rulebook"] = rulebook
        try:
            sales, source, eligible = load_business_sales(catalog, rulebook)
            cache["sales"] = (sales, source)
            cache["ranking_eligible"] = eligible
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


def prepare_debt(cache: dict[str, Any], catalog: Catalog, rulebook: str) -> dict[str, Any]:
    """Cache the current business's D01 ledger or typed source rejection in the worker."""
    key = (_catalog_key(catalog), catalog.readme, rulebook)
    if cache.get("debt_key") != key:
        try:
            cache["debt"] = load_debt_ledger(catalog, rulebook)
        except DebtSourceError as exc:
            cache["debt"] = exc
        cache["debt_key"] = key
    loaded = cache["debt"]
    if isinstance(loaded, DebtSourceError):
        return {"ok": False, "reason": str(loaded), "debt_source_error": loaded.diagnostics}
    return {"ok": True}


def product_names(cache: dict[str, Any], catalog: Catalog, rulebook: str = "") -> list[str]:
    """Product names and codes of the loaded business, for the planner's product filter."""
    if not prepare_sales(cache, catalog, rulebook)["ok"]:
        return []
    sales, _ = cache["sales"]
    return sorted({str(n) for s in sales for n in (s.product_name, s.product_id) if n})


def vocabulary(cache: dict[str, Any], catalog: Catalog, rulebook: str = "") -> dict[str, list[str]]:
    """Group names of the loaded business, for the planner's group filters (SEM-02)."""
    if not prepare_sales(cache, catalog, rulebook)["ok"]:
        return {}
    sales, _ = cache["sales"]
    return {field: sorted({getattr(s, field) for s in sales if getattr(s, field)})
            for field in ("product_group", "customer_group")}


def select_group(sales: list[Sale], field: str, value: str) -> list[Sale]:
    """Rows whose product group / customer group is exactly the named one (folded)."""
    key = fold(value)
    return [s for s in sales if fold(getattr(s, field) or "") == key]


def share_percent(part: Decimal, whole: Decimal) -> float | None:
    if whole == 0:
        return None
    return float((part * 100 / whole).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


C01_INTENTS = frozenset({"revenue_increasing", "top_debt_customers"})
INTENTS = frozenset({"revenue", "order_count", "top_products", "average_order",
                     "cancel_count", "cancel_rate", "best_day",
                     "top_customers", "count_where", "quantity"}) | C01_INTENTS
# Plan filters this backend applies, and the intents each one may be combined with.
FILTERABLE = {"product": frozenset({"revenue", "order_count", "quantity"}),
              "product_group": frozenset({"revenue"}),
              "customer_group": frozenset({"revenue"})}
GROUP_FIELDS = ("product_group", "customer_group")
SHAPES = {"series": "revenue", "best_period": "revenue"}  # shape -> the one intent it supports
COVERAGE = re.compile(r"Khoảng dữ liệu:\s*(\d{4}-\d{2}-\d{2})\s+đến\s+(\d{4}-\d{2}-\d{2})")


def unsupported(plan: Plan) -> str:
    """Why this backend cannot answer the Plan exactly; "" when it can.

    A Plan field this backend does not implement must abstain, never be ignored: an
    ignored product filter would return the whole shop's revenue for one product.
    """
    if plan.intent not in INTENTS:
        return f"Intent {plan.intent!r} not supported by baseline agent"
    if plan.intent in C01_INTENTS:
        if (plan.filters or plan.threshold is not None or plan.comparison
                or plan.compare_start is not None or plan.compare_end is not None
                or plan.shape or plan.unit or plan.rank_by or type(plan.top_k) is not int):
            return f"Unsupported fields for {plan.intent}"
        if plan.intent == "top_debt_customers":
            if plan.start != plan.end or plan.periods or not 1 <= plan.top_k <= 50:
                return "Debt table requires a snapshot day and top_k from 1 to 50"
        elif (plan.top_k != 1 or not 2 <= len(plan.periods) <= 12
              or (plan.start, plan.end) != (plan.periods[0][0], plan.periods[-1][1])
              or any(start.day != 1 or end != date(
                  start.year, start.month, monthrange(start.year, start.month)[1])
                     for start, end in plan.periods)
              or any(previous[1].toordinal() + 1 != current[0].toordinal()
                     for previous, current in zip(plan.periods, plan.periods[1:]))):
            return "Revenue trend requires 2 to 12 consecutive full months"
        return ""
    if plan.comparison == "share":
        if plan.compare_start is not None or plan.compare_end is not None:
            return "Share does not support comparison periods"
        group_share = (plan.intent == "revenue" and len(plan.filters) == 1
                       and plan.filters[0].field in GROUP_FIELDS)
        if not group_share and (plan.intent != "count_where" or plan.threshold is None):
            return f"Comparison 'share' for {plan.intent!r} not supported"
    elif plan.comparison or plan.compare_start or plan.compare_end:
        if (plan.comparison != "change_percent" or plan.intent != "revenue" or plan.filters
                or not (plan.compare_start and plan.compare_end)):
            return f"Comparison {plan.comparison!r} for {plan.intent!r} not supported"
    if plan.periods or plan.shape:
        if SHAPES.get(plan.shape) != plan.intent or len(plan.periods) < 2 or plan.filters \
                or plan.comparison:
            return f"Shape {plan.shape!r} for {plan.intent!r} not supported"
        ordered = sorted(plan.periods)
        if (plan.start != ordered[0][0] or plan.end != ordered[-1][1]
                or any(start.day != 1 or end != date(start.year, start.month,
                                                    monthrange(start.year, start.month)[1])
                       for start, end in ordered)
                or any(previous[1] >= current[0] for previous, current in zip(ordered, ordered[1:]))):
            return "Monthly periods must be complete, disjoint and match the Plan span"
    if plan.threshold is not None:
        t = plan.threshold
        supported = {("customer", "order_count"), ("order", "revenue"), ("day", "revenue"),
                     ("product", "order_count")}
        if plan.intent != "count_where" or (t.subject, t.measure) not in supported or t.op != ">=":
            return "Threshold not supported"
        if plan.comparison == "share" and t.subject != "customer":
            return "Share of a threshold count is defined for customers only"
        if (not isinstance(t.value, Decimal) or not t.value.is_finite()
                or (t.measure == "order_count" and (t.value < 0 or t.value != t.value.to_integral()))):
            return "Order thresholds must be finite nonnegative integers"
    elif plan.intent == "count_where":
        return "Customer count needs a threshold"
    if (plan.intent == "quantity") != bool(plan.unit):
        return "Quantity needs the unit asked for"
    if plan.rank_by and (plan.intent != "top_customers"
                         or plan.rank_by not in ("revenue", "order_count")):
        return f"Ranking by {plan.rank_by!r} not supported"
    if plan.intent == "top_customers" and not plan.rank_by:
        return "Customer ranking needs its measure"
    if len(plan.filters) > 1:
        return "Several filters not supported"
    for item in plan.filters:
        if plan.intent not in FILTERABLE.get(item.field, ()):
            return f"Filter {item.field!r} for {plan.intent!r} not supported"
    return ""


def outside_data(sales: list[Sale], start: date, end: date, readme: str) -> str:
    """"" if the window overlaps the sales; "unanswerable" if README coverage excludes it."""
    days = [s.day for s in sales]
    if not (end < min(days) or start > max(days)):
        return ""
    # Absence of sales alone does not establish the business's coverage.
    coverage = COVERAGE.search(readme)
    if coverage:
        first, last = map(date.fromisoformat, coverage.groups())
        if end < first or start > last:
            return "unanswerable"
    return "unknown"


def select_product(sales: list[Sale], value: str) -> list[Sale]:
    """Rows whose product name or code equals the filter value (case/diacritics folded)."""
    key = fold(value)
    return [s for s in sales if key in (fold(s.product_name or ""), fold(s.product_id or ""))]


def select_product_code(sales: list[Sale], code: str) -> list[Sale]:
    """Every row of one product code, whatever spelling of the name the row carries."""
    return [s for s in sales if s.product_id == code]


def revenue_total(selected: list[Sale]) -> Decimal:
    return sum((s.amount for s in selected), Decimal(0))


def revenue_trend(sales: list[Sale], periods: tuple[tuple[date, date], ...]
                  ) -> tuple[list[Decimal], list[bool]]:
    totals = [revenue_total(select_valid(sales, start, end)) for start, end in periods]
    return totals, [current > previous for previous, current in zip(totals, totals[1:])]


def debt_ranking(ledger: DebtLedger, day: date
                 ) -> tuple[list[tuple[str, str, Decimal, int]], int, Decimal]:
    paid: dict[str, Decimal] = defaultdict(Decimal)
    for payment in ledger.payments:
        if payment.day <= day:
            paid[payment.order_id] += payment.amount

    totals: dict[str, Decimal] = defaultdict(Decimal)
    counts: Counter[str] = Counter()
    names: dict[str, Counter[str]] = defaultdict(Counter)
    total_debt = Decimal(0)
    open_orders = 0
    for order in ledger.orders:
        if order.day > day:
            continue
        key = (order.customer_id if ledger.uses_customer_codes
               else _customer_name_key(order.customer_name))
        if key is not None and order.customer_name and order.customer_name.strip():
            names[key][order.customer_name] += 1
        if order.state == "huy" or order.excluded:
            continue
        remaining = order.total - paid[order.order_id]
        if remaining <= 0:
            continue
        open_orders += 1
        total_debt += remaining
        if key is not None:
            totals[key] += remaining
            counts[key] += 1

    ranked = []
    for key in sorted(totals, key=lambda key: (-totals[key], key)):
        display = names[key].most_common(1)
        ranked.append((key, display[0][0] if display else "", totals[key], counts[key]))
    return ranked, open_orders, total_debt


def _c01_rules(plan: Plan, rulebook: str) -> tuple[str, ...]:
    rules = read_rules(rulebook)
    if plan.intent == "revenue_increasing":
        if not rules.revenue or rules.trend_rule:
            return ()
        required = ("boolean_answer", "calendar_month")
        base = rules.source_rows
    else:
        required = DEBT_TABLE_FEATURES
        base = ()
    if not all(getattr(rules, feature) for feature in required):
        return ()
    split_lines = {line.strip() for line in rulebook.splitlines()}
    return tuple(dict.fromkeys((*base, *(rules.row(feature) for feature in required),
                                *(line for line in plan.evidence if line in split_lines))))


def _answer_debt_table(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                       rulebook: str, rules: tuple[str, ...]) -> Candidate:
    day = plan.end
    snapshot = day.isoformat()
    evidence: dict[str, Any] = {"intent": plan.intent, "start": snapshot, "end": snapshot,
                                "snapshot": snapshot, "rules": rules}
    prepared = prepare_debt(cache, catalog, rulebook)
    if not prepared["ok"]:
        diagnostic = prepared["debt_source_error"]
        evidence["debt_source_error"] = diagnostic
        if diagnostic["code"] == "absent" and diagnostic.get("sources_complete") is True:
            evidence["coverage_source"] = "D01 checked the README and all catalog sources"
            return Candidate("unanswerable", None, 1.0, prepared["reason"], evidence)
        return Candidate(reason=prepared["reason"], evidence=evidence)

    ledger = cache["debt"]
    evidence["ledger"] = ledger.lineage
    if not ledger.first_day <= day <= ledger.last_day:
        return Candidate(reason="Debt snapshot outside ledger coverage", evidence=evidence)
    ranked, open_orders, total_debt = debt_ranking(ledger, day)
    evidence.update(
        open_orders=open_orders, total_debt=str(total_debt),
        ranking=[{"customer_key": key, "name": name, "debt": str(amount), "orders": count}
                 for key, name, amount, count in ranked],
        code=_code(f"debt_ranking(ledger, date.fromisoformat({day.isoformat()!r}))",
                   debt_ranking, _customer_name_key, _vnd),
    )
    rows = [[name, int(_vnd(amount))] for _, name, amount, _ in ranked[:plan.top_k]]
    if not rows or any(not name or amount <= 0 for name, amount in rows):
        return Candidate(reason="Debt ranking has no displayable rows", evidence=evidence)
    result = {"columns": list(DEBT_TABLE_COLUMNS), "rows": rows}
    evidence["result"] = result
    return Candidate("table", result, 1.0, "", evidence)


def _answer_revenue_trend(sales: list[Sale], readme: str, plan: Plan,
                          evidence: dict[str, Any]) -> Candidate:
    if not sales:
        return Candidate(reason="Revenue trend has no sales data", evidence=evidence)
    observed_first = min(sale.day for sale in sales)
    observed_last = max(sale.day for sale in sales)
    documented = COVERAGE.search(readme)
    try:
        documented_last = _coverage_day(readme)
        if documented:
            first, last = map(date.fromisoformat, documented.groups())
        else:
            first, last = observed_first, documented_last or observed_last
    except ValueError:
        return Candidate(reason="Invalid README coverage", evidence=evidence)
    evidence["coverage"] = {"first_day": first.isoformat(), "last_day": last.isoformat()}
    if ((documented_last is not None and documented_last != last)
            or not first <= observed_first <= observed_last <= last
            or not first <= plan.start <= plan.end <= last):
        return Candidate(reason="Revenue trend needs full months within sales coverage",
                         evidence=evidence)
    observed_months = {(sale.day.year, sale.day.month) for sale in sales}
    if any((start.year, start.month) not in observed_months for start, _ in plan.periods):
        return Candidate(reason="A requested month has no source records", evidence=evidence)

    totals, comparisons = revenue_trend(sales, plan.periods)
    periods = [{"start": start.isoformat(), "end": end.isoformat(), "revenue": str(total)}
               for (start, end), total in zip(plan.periods, totals)]
    result = all(comparisons)
    windows = tuple((start.isoformat(), end.isoformat()) for start, end in plan.periods)
    evidence.update(
        periods=periods,
        comparisons=[{"previous": previous["start"], "current": current["start"],
                      "greater": greater}
                     for previous, current, greater in zip(periods, periods[1:], comparisons)],
        code=_code(f"revenue_trend(sales, tuple((date.fromisoformat(start), "
                   f"date.fromisoformat(end)) for start, end in {windows!r}))",
                   revenue_trend, select_valid, revenue_total),
        result=result,
    )
    return Candidate("boolean", result, 1.0, "", evidence)


def change_percent(current: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    change = (current - previous) * 100 / previous
    return float(change.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def answer_plan(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                rulebook: str = "") -> Candidate:
    reason = unsupported(plan)
    if reason:
        return Candidate(reason=reason)
    c01_rules = ()
    if plan.intent in C01_INTENTS:
        c01_rules = _c01_rules(plan, rulebook)
        if not c01_rules:
            return Candidate(reason=f"Split rulebook does not support {plan.intent}")
        if plan.intent == "top_debt_customers":
            return _answer_debt_table(cache, catalog, plan, rulebook, c01_rules)
    prepared = prepare_sales(cache, catalog, rulebook)
    if not prepared["ok"]:
        return Candidate(reason=prepared["reason"])
    sales, source = cache["sales"]
    evidence: dict[str, Any] = {
        **source, "intent": plan.intent, "start": plan.start.isoformat(),
        "end": plan.end.isoformat(), "rules": c01_rules or plan.evidence,
        "inputs": {"tables": source["tables"], "sales_rows": source["sales_rows"],
                   "data_first_day": prepared["data_first_day"],
                   "data_last_day": prepared["data_last_day"]}}
    if plan.intent == "revenue_increasing":
        return _answer_revenue_trend(sales, catalog.readme, plan, evidence)
    if plan.compare_start:
        evidence.update(compare_start=plan.compare_start.isoformat(),
                        compare_end=plan.compare_end.isoformat(), comparison=plan.comparison)
    if plan.filters:
        evidence["filters"] = [{"field": f.field, "value": f.value, "rule": f.rule}
                               for f in plan.filters]
    if not sales or plan.start > plan.end:
        return Candidate(reason="Empty data or future-only period", evidence=evidence)
    windows = list(plan.periods) or [(plan.start, plan.end)]
    if plan.compare_start:
        windows.append((plan.compare_start, plan.compare_end))
    for window_start, window_end in windows:
        outside = outside_data(sales, window_start, window_end, catalog.readme)
        if outside == "unanswerable":
            coverage = COVERAGE.search(catalog.readme)
            evidence["coverage_source"] = coverage.group() if coverage else ""
            return Candidate(type="unanswerable", confidence=1.0,
                             reason="Kỳ hỏi nằm hoàn toàn ngoài khoảng dữ liệu ghi trong README",
                             evidence=evidence)
        if outside:
            return Candidate(reason="No sales in period; business coverage is not established",
                             evidence=evidence)
    product_call = ""
    if plan.filters and plan.filters[0].field in GROUP_FIELDS:
        return _answer_group(plan, sales, evidence)
    if plan.filters:
        value = plan.filters[0].value
        matched = select_product(sales, value)
        codes = {s.product_id for s in matched}
        evidence["inputs"].update(product_rows=len(matched), product_codes=sorted(map(str, codes)))
        if len(codes) != 1:
            return Candidate(reason="Product filter matches no product or several product codes",
                             evidence=evidence)
        code = next(iter(codes))
        if code is not None:
            # Rulebook "Tên gọi": a file with codes identifies the product by its code, so
            # every spelling of that product's name counts ("30ml" and "30 ml").
            matched = select_product_code(sales, code)
            evidence["inputs"]["product_rows"] = len(matched)
        sales = matched
        product_call = (f"select_product_code(sales, {code!r})" if code is not None
                        else f"select_product(sales, {value!r})")
    source_expr = product_call or "sales"
    window = f"date.fromisoformat({plan.start.isoformat()!r}), date.fromisoformat({plan.end.isoformat()!r})"
    if plan.shape or plan.intent in ("top_customers", "count_where", "quantity"):
        full = cache["sales"][0]
        return _answer_extended(plan, sales, full, cache.get("ranking_eligible"), evidence,
                                window, source_expr)
    if plan.comparison == "change_percent":
        compare_window = (f"date.fromisoformat({plan.compare_start.isoformat()!r}), "
                          f"date.fromisoformat({plan.compare_end.isoformat()!r})")
        current = revenue_total(select_valid(sales, plan.start, plan.end))
        previous = revenue_total(select_valid(sales, plan.compare_start, plan.compare_end))
        value = change_percent(current, previous)
        evidence.update(
            calculation="100 * (revenue - compare revenue) / compare revenue; 2 decimals half up",
            code=_code(f"change_percent(revenue_total(select_valid({source_expr}, {window})), "
                       f"revenue_total(select_valid({source_expr}, {compare_window})))",
                       select_valid, revenue_total, change_percent),
            current_revenue=str(current), compare_revenue=str(previous), result=value)
        if value is None:
            return Candidate(reason="Comparison period has zero revenue; change undefined",
                             evidence=evidence)
        return Candidate("percent", value, 1.0, "", evidence)
    if plan.intent in ("cancel_count", "cancel_rate"):
        rows = select_period(sales, plan.start, plan.end)
        evidence["inputs"].update(period_rows=len(rows),
                                  period_orders=len({s.order_id for s in rows}))
        if plan.intent == "cancel_count":
            value = cancelled_orders(rows)
            evidence.update(calculation="count distinct order_id with status cancelled",
                            code=_code(f"cancelled_orders(select_period(sales, {window}))",
                                       select_period, cancelled_orders), result=value)
            return Candidate("number", value, 1.0, "", evidence)
        rate = cancel_rate(rows)
        evidence.update(calculation="100 * cancelled / all orders (any status); 2 decimals half up",
                        code=_code(f"cancel_rate(select_period(sales, {window}))",
                                   select_period, cancel_rate), result=rate)
        if rate is None:
            return Candidate(reason="No orders in period; rate undefined", evidence=evidence)
        return Candidate("percent", rate, 1.0, "", evidence)
    selected = select_valid(sales, plan.start, plan.end)
    selection = f"select_valid({source_expr}, {window})"
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
    if plan.intent == "average_order":
        value = average_order_value(selected)
        evidence.update(calculation="revenue / valid orders; round VND half up",
                        code=_code(f"average_order_value({selection})",
                                   select_valid, average_order_value), result=value)
        if value is None:
            return Candidate(reason="No valid orders in period; average undefined",
                             evidence=evidence)
        return Candidate("number", value, 1.0, "", evidence)
    if plan.intent == "best_day":
        days_ranked = daily_revenue(selected)
        evidence.update(calculation="argmax of daily revenue on valid whole orders",
                        code=_code(f"daily_revenue({selection})[0]", select_valid, daily_revenue),
                        # Two rows show whether the top is a tie.
                        ranking=[{"day": d.isoformat(), "revenue": str(v)} for d, v in days_ranked[:2]])
        if not days_ranked:
            return Candidate(reason="No valid sales in period", evidence=evidence)
        if len(days_ranked) > 1 and days_ranked[1][1] == days_ranked[0][1]:
            return Candidate(reason="Tie for the best day; rulebook gives no date tie-break",
                             evidence=evidence)
        evidence["result"] = days_ranked[0][0].isoformat()
        return Candidate("date", evidence["result"], 1.0, "", evidence)
    if plan.intent != "top_products":  # unreachable while INTENTS is checked; keep explicit
        return Candidate(reason=f"Intent {plan.intent!r} not supported", evidence=evidence)
    eligible = cache.get("ranking_eligible")
    ranked_rows = select_valid(ranking_candidates(sales, eligible), plan.start, plan.end)
    evidence["ranking_rows"] = len(ranked_rows)
    if not ranked_rows or any(s.quantity_base is None or not s.product_id or not s.product_name
                              for s in ranked_rows):
        return Candidate(reason="Ranking needs explicit base quantities and product ids/names",
                         evidence=evidence)
    ranking = product_ranking(ranked_rows)
    # ``eligible`` (sale indices from the adapter lineage) can be large: log its size only.
    candidates = "ranking_candidates(sales, None)" if eligible is None else \
        "ranking_candidates(sales, eligible)"
    if eligible is not None:
        evidence["inputs"]["ranking_eligible_rows"] = len(eligible)
    answer = [name for _, name, _ in ranking[:plan.top_k]]
    result = answer[0] if plan.top_k == 1 else answer
    evidence.update(
        calculation="sum(quantity_base) by product_id, descending; ties by id",
        code=_code(f"product_ranking(select_valid({candidates}, {window}))[:{plan.top_k}]",
                   ranking_candidates, select_valid, product_ranking),
        # One row past k shows whether the boundary is a tie.
        ranking=[{"product_id": key, "product_name": name, "quantity_base": str(quantity)}
                 for key, name, quantity in ranking[:plan.top_k + 1]],
        result=result)
    return Candidate("string" if plan.top_k == 1 else "list", result, 1.0, "", evidence)


def _answer_extended(plan: Plan, sales: list[Sale], full: list[Sale],
                     eligible: frozenset[int] | None, evidence: dict[str, Any], window: str,
                     source_expr: str) -> Candidate:
    """SEM-02 Plans: series / best period, customers, quantity in a base unit."""
    if plan.shape:
        values = period_revenues(sales, list(plan.periods))
        periods = [(start.isoformat(), end.isoformat()) for start, end in plan.periods]
        evidence.update(periods=periods, period_revenues=values,
                        code=_code(f"period_revenues(sales, {periods!r})",
                                   period_revenues, select_valid, revenue))
        if plan.shape == "series":
            evidence.update(calculation="revenue of each period, in question order",
                            result=values)
            return Candidate("list", values, 1.0, "", evidence)
        best = max(values)
        if values.count(best) > 1:
            return Candidate(reason="Tie for the best period; months have no code to break it",
                             evidence=evidence)
        result = plan.periods[values.index(best)][0].strftime("%Y-%m")
        evidence.update(calculation="period with the highest revenue", result=result)
        return Candidate("date", result, 1.0, "", evidence)
    selected = select_valid(sales, plan.start, plan.end)
    selection = f"select_valid({source_expr}, {window})"
    evidence["inputs"].update(selected_rows=len(selected))
    if plan.intent == "quantity":
        if eligible is None:
            pool = selected
        else:
            # ``eligible`` indexes the full sales list: drop service-fee rows before filtering.
            keep = {id(full[i]) for i in eligible}
            pool = [s for s in selected if id(s) in keep]
        if not any(s.base_unit and fold(s.base_unit) == fold(plan.unit) for s in full):
            return Candidate(reason=f"No product of this shop has base unit {plan.unit!r}",
                             evidence=evidence)
        value = quantity_in_unit(pool, plan.unit)
        evidence.update(calculation="sum(quantity_base) of products whose base unit is the unit",
                        unit=plan.unit, code=_code(f"quantity_in_unit({selection}, {plan.unit!r})",
                                                   select_valid, quantity_in_unit), result=value)
        if value is None:
            return Candidate(reason="A product's base unit is unknown", evidence=evidence)
        if plan.filters and value == 0:
            return Candidate(reason="The product's base unit is not the unit asked",
                             evidence=evidence)
        result = int(value) if value == value.to_integral() else float(value)
        evidence.update(quantity_base_total=str(value), result=result)
        return Candidate("number", result, 1.0, "", evidence)
    if plan.threshold is not None and plan.threshold.subject != "customer":
        subject, minimum = plan.threshold.subject, plan.threshold.value
        pool = selected
        if subject == "product" and eligible is not None:
            keep = {id(full[i]) for i in eligible}  # service fees are not products
            pool = [s for s in selected if id(s) in keep]
        value = count_at_least(pool, subject, minimum)
        evidence.update(calculation=f"count {subject}s with {plan.threshold.measure} >= {minimum}",
                        code=_code(f"count_at_least({selection}, {subject!r}, Decimal({str(minimum)!r}))",
                                   select_valid, count_at_least, daily_revenue),
                        result=value)
        return Candidate("number", value, 1.0, "", evidence)
    if not any(s.customer_id or s.customer_name for s in selected):
        return Candidate(reason="No customer recorded on the orders", evidence=evidence)
    if plan.intent == "top_customers":
        ranking = customer_ranking(selected, plan.rank_by)
        evidence.update(calculation=f"customers by {plan.rank_by}, descending; ties by code",
                        code=_code(f"customer_ranking({selection}, {plan.rank_by!r})",
                                   select_valid, customer_ranking, customer_totals,
                                   customer_keys),
                        ranking=[{"customer": name, "value": str(value)}
                                 for _, name, value in ranking[:plan.top_k + 1]])
        if len(ranking) < plan.top_k:
            return Candidate(reason="Fewer customers than asked", evidence=evidence)
        answer = [name for _, name, _ in ranking[:plan.top_k]]
        result = answer[0] if plan.top_k == 1 else answer
        evidence["result"] = result
        return Candidate("string" if plan.top_k == 1 else "list", result, 1.0, "", evidence)
    minimum = int(plan.threshold.value)
    meeting, bought = customers_meeting(selected, minimum)
    evidence.update(calculation=f"customers with >= {minimum} valid orders"
                    + (" / customers who bought x 100" if plan.comparison == "share" else ""),
                    code=_code(f"customers_meeting({selection}, {minimum})", select_valid,
                               customers_meeting, customer_totals, customer_keys),
                    customers_meeting=meeting, customers_bought=bought)
    if plan.comparison != "share":
        evidence["result"] = meeting
        return Candidate("number", meeting, 1.0, "", evidence)
    if bought == 0:
        return Candidate(reason="No customer bought in the period", evidence=evidence)
    value = float((Decimal(meeting) * 100 / bought).quantize(Decimal("0.01"),
                                                             rounding=ROUND_HALF_UP))
    evidence["result"] = value
    return Candidate("percent", value, 1.0, "", evidence)


def _answer_group(plan: Plan, sales: list[Sale], evidence: dict[str, Any]) -> Candidate:
    """Revenue of one product group / customer group, or its share of the whole shop."""
    item = plan.filters[0]
    valid = select_valid(sales, plan.start, plan.end)
    if not any(getattr(s, item.field) for s in sales):
        return Candidate(reason=f"No {item.field} recorded in this shop", evidence=evidence)
    part_rows = select_group(valid, item.field, item.value)
    evidence["inputs"].update(selected_rows=len(valid), group_rows=len(part_rows))
    part = revenue_total(part_rows)
    window = (f"date.fromisoformat({plan.start.isoformat()!r}), "
              f"date.fromisoformat({plan.end.isoformat()!r})")
    group_call = f"select_group(select_valid(sales, {window}), {item.field!r}, {item.value!r})"
    if plan.comparison != "share":
        value = revenue(part_rows)
        evidence.update(calculation=f"revenue of the {item.field} on valid whole orders",
                        code=_code(f"revenue({group_call})", select_valid, select_group, revenue),
                        result=value)
        return Candidate("number", value, 1.0, "", evidence)
    whole = revenue_total(valid)
    value = share_percent(part, whole)
    evidence.update(calculation=f"100 * revenue of the {item.field} / revenue of the whole shop "
                    "(walk-in orders and service fees included); 2 decimals half up",
                    code=_code(f"share_percent(revenue_total({group_call}), "
                               f"revenue_total(select_valid(sales, {window})))",
                               select_valid, select_group, revenue_total, share_percent),
                    part_revenue=str(part), whole_revenue=str(whole), result=value)
    if value is None:
        return Candidate(reason="No revenue in the period; share undefined", evidence=evidence)
    return Candidate("percent", value, 1.0, "", evidence)
