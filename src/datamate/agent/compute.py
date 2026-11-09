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

from datamate.agent.business_measures import (
    delivery_count,
    earliest_expiry,
    expiring_lot_cost,
    expiring_lots,
    expiring_products,
    gross_profit,
    net_profit,
    product_key,
    stock_value,
    top_gross_profit,
    unsold_count,
)
from datamate.agent.sales_source import load_business_sales
from datamate.contracts import (
    DEBT_TABLE_COLUMNS,
    Candidate,
    Catalog,
    DebtLedger,
    DebtOrder,
    DebtPayment,
    Plan,
)
from datamate.ingestion.business_measures import load_deliveries, load_expenses
from datamate.ingestion.debt import DebtSourceError, _coverage_day, load_debt_ledger
from datamate.ingestion.drugs import Drug, ingredient_key, load_drugs
from datamate.ingestion.expiry import load_lots
from datamate.ingestion.purchases import (
    PurchaseLine,
    PurchaseSourceError,
    Receipt,
    load_purchase_lines,
    load_purchases,
)
from datamate.ingestion.sales import Sale
from datamate.ingestion.stock import (
    StockItem,
    StockSnapshot,
    StockSourceError,
    load_conversions,
    load_stock,
    load_stock_costs,
)
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


def price_history(cache: dict[str, Any], catalog: Catalog,
                  rulebook: str) -> tuple[dict[tuple[str, Decimal], date], str]:
    from datamate.ingestion.prices import load_price_increases

    if not prepare_sales(cache, catalog, rulebook)["ok"]:
        return {}, ""
    try:
        increases, source, by_code = load_price_increases(catalog)
    except ValueError:
        return {}, ""
    sales, _ = cache["sales"]
    aliases: dict[str, set[str]] = defaultdict(set)
    for sale in sales:
        key = sale.product_id if by_code else fold(sale.product_name or "")
        if key:
            for name in (sale.product_id, sale.product_name):
                if name:
                    aliases[name].add(key)
    by_product: dict[str, dict[Decimal, date]] = defaultdict(dict)
    for (key, price), day in increases.items():
        by_product[key][price] = day
    return {(alias, price): day for alias, keys in aliases.items() if len(keys) == 1
            for price, day in by_product[next(iter(keys))].items()}, source


def select_group(sales: list[Sale], field: str, value: str) -> list[Sale]:
    """Rows whose product group / customer group is exactly the named one (folded)."""
    key = fold(value)
    return [s for s in sales if fold(getattr(s, field) or "") == key]


def share_percent(part: Decimal, whole: Decimal) -> float | None:
    if whole == 0:
        return None
    return float((part * 100 / whole).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


C01_INTENTS = frozenset({"revenue_increasing", "top_debt_customers"})
# Contract C01 §7: further debt questions on the same D01 ledger.
DEBT_INTENTS = frozenset({"debt_total", "debt_customer_count", "debt_collected", "debt_flow"})
DEBT_MEASURE_FEATURES = ("debt_orders", "debt_at_date", "debt_excluded", "customer_identity")
LOT_INTENTS = frozenset({"earliest_expiry", "expiring_lot_count", "expiring_lot_cost",
                         "expiring_product_count", "expiring_products"})
MONTHLY_MEASURE_INTENTS = frozenset({"delivery_count", "gross_profit", "top_gross_profit", "net_profit"})
SALES_MEASURE_INTENTS = (MONTHLY_MEASURE_INTENTS - {"delivery_count"}) | {"unsold_count"}
INDEPENDENT_INTENTS = LOT_INTENTS | {"stock", "stock_split", "delivery_count", "stock_value"}
INTENTS = (frozenset({"revenue", "order_count", "top_products", "average_order",
                     "cancel_count", "cancel_rate", "best_day",
                     "top_customers", "count_where", "quantity", "top_staff", "stock", "stock_split",
                     "purchase_total", "top_supplier", "avg_purchase_price"})
           | C01_INTENTS | DEBT_INTENTS | INDEPENDENT_INTENTS | SALES_MEASURE_INTENTS)
# Plan filters this backend applies, and the intents each one may be combined with.
FILTERABLE = {"product": frozenset({"revenue", "order_count", "quantity", "stock", "stock_split",
                                     "avg_purchase_price", "earliest_expiry"}),
              "shift": frozenset({"revenue", "order_count"}),
              "staff": frozenset({"revenue", "order_count"}),
              "size": frozenset({"quantity"}),
              "product_group": frozenset({"revenue"}),
              "customer_group": frozenset({"revenue", "average_order"}),
              "ingredient": frozenset({"revenue"}),
              "prescription": frozenset({"revenue"})}
GROUP_FIELDS = ("product_group", "customer_group")
DRUG_FIELDS = ("ingredient", "prescription")
SHAPES = {"series": "revenue", "best_period": "revenue"}  # shape -> the one intent it supports
COVERAGE = re.compile(r"Khoảng dữ liệu:\s*(\d{4}-\d{2}-\d{2})\s+đến\s+(\d{4}-\d{2}-\d{2})")


def unsupported(plan: Plan) -> str:
    """Why this backend cannot answer the Plan exactly; "" when it can.

    A Plan field this backend does not implement must abstain, never be ignored: an
    ignored product filter would return the whole shop's revenue for one product.
    """
    if plan.intent not in INTENTS:
        return f"Intent {plan.intent!r} not supported by baseline agent"
    if plan.intent in MONTHLY_MEASURE_INTENTS and (
            plan.start.day != 1 or plan.start > plan.end or
            (plan.start.year, plan.start.month) != (plan.end.year, plan.end.month)):
        return "Measure requires a single month beginning on its first day"
    if plan.intent in INDEPENDENT_INTENTS - {"stock", "stock_split"}:
        if plan.start > plan.end or plan.top_k != 1:
            return "Invalid measure window or ranking size"
        if plan.intent in {"stock_value", "earliest_expiry"} and plan.start != plan.end:
            return "Measure requires a single snapshot day"
        if plan.intent == "earliest_expiry" and len(plan.filters) != 1:
            return "Earliest expiry requires one product"
    if plan.intent in DEBT_INTENTS:
        if (plan.comparison or plan.compare_start is not None or plan.periods or plan.shape
                or plan.unit or plan.rank_by or plan.top_k != 1):
            return f"Unsupported fields for {plan.intent}"
        snapshot = plan.intent in ("debt_total", "debt_customer_count")
        if snapshot and plan.start != plan.end:
            return "Debt balance is a snapshot at one day"
        if plan.intent == "debt_flow":
            if len(plan.filters) != 1 or plan.filters[0].field != "customer":
                return "Debt flow needs one customer"
        elif plan.filters:
            return f"Filters not supported for {plan.intent}"
        t = plan.threshold
        if t is not None and (plan.intent != "debt_total" or t.subject != "order"
                              or t.measure != "age_days" or t.op not in (">", ">=")
                              or not isinstance(t.value, Decimal) or t.value < 0
                              or t.value != t.value.to_integral()):
            return "Only debt_total takes an order age threshold"
        return ""
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
                       and plan.filters[0].field in GROUP_FIELDS + DRUG_FIELDS)
        if not group_share and (plan.intent != "count_where" or plan.threshold is None):
            return f"Comparison 'share' for {plan.intent!r} not supported"
    elif plan.comparison == "shift_change_percent":
        if (plan.intent not in ("revenue", "order_count") or plan.compare_start is not None
                or plan.compare_end is not None or len(plan.filters) != 1
                or plan.filters[0].field != "shift"):
            return "Shift comparison needs one shift filter and no comparison period"
    elif plan.comparison or plan.compare_start or plan.compare_end:
        # Price-increase effect (rulebook §5): units of one product, N days from D against
        # the N days just before D.
        price_effect = (plan.intent == "quantity" and len(plan.filters) == 1
                        and plan.filters[0].field == "product")
        if (plan.comparison != "change_percent" or not (plan.intent == "revenue" or price_effect)
                or (plan.filters and not price_effect)
                or not (plan.compare_start and plan.compare_end)):
            return f"Comparison {plan.comparison!r} for {plan.intent!r} not supported"
        if price_effect and ((plan.start - plan.compare_end).days != 1
                             or (plan.end - plan.start) != (plan.compare_end - plan.compare_start)
                             or plan.start > plan.end):
            return "Price effect requires two adjacent windows of equal length"
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
    if (plan.intent in ("quantity", "stock", "stock_split", "avg_purchase_price")) != bool(plan.unit):
        return "Quantity needs the unit asked for"
    if plan.intent == "avg_purchase_price" and (
            len(plan.filters) != 1 or plan.filters[0].field != "product" or plan.start.day != 1
            or plan.end != date(plan.start.year, plan.start.month,
                                monthrange(plan.start.year, plan.start.month)[1])):
        return "Average purchase price is one product over one full month"
    if plan.intent in ("stock", "stock_split") and (
            plan.start != plan.end or len(plan.filters) != 1 or plan.filters[0].field != "product"
            or plan.top_k != 1):
        return "Stock is one product at the end of one day"
    if plan.intent == "stock_split":
        units = plan.unit.split("|")
        if len(units) != 2 or not all(units) or units[0] == units[1]:
            return "Stock split needs two distinct units"
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
        if item.field == "prescription" and item.value not in ("rx", "non_rx"):
            return "Unknown prescription class"
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


def open_debts(ledger: DebtLedger, day: date) -> list[tuple[DebtOrder, Decimal]]:
    """Orders still owing at the end of ``day`` (C01 §4): not cancelled, not COD /
    marketplace, ordered by ``day``; payments counted up to ``day``."""
    paid: dict[str, Decimal] = defaultdict(Decimal)
    for payment in ledger.payments:
        if payment.day <= day:
            paid[payment.order_id] += payment.amount
    result = []
    for order in ledger.orders:
        if order.day > day or order.state == "huy" or order.excluded:
            continue
        remaining = order.total - paid[order.order_id]
        if remaining > 0:
            result.append((order, remaining))
    return result


def debt_total(ledger: DebtLedger, day: date, min_age: int | None = None,
               inclusive: bool = False) -> Decimal:
    """Total owed at the end of ``day``, walk-in orders included; optionally only orders
    older than ``min_age`` days (age = day - order day; ``inclusive``: age >= min_age)."""
    total = Decimal(0)
    for order, remaining in open_debts(ledger, day):
        age = (day - order.day).days
        if min_age is None or age > min_age or (inclusive and age == min_age):
            total += remaining
    return total


def debt_customer_count(ledger: DebtLedger, day: date) -> int:
    """Customers (not walk-in) who still owe at the end of ``day``."""
    keys = {_debt_key(order, ledger) for order, _ in open_debts(ledger, day)}
    return len(keys - {None})


def _debt_key(order: DebtOrder, ledger: DebtLedger) -> str | None:
    return (order.customer_id if ledger.uses_customer_codes
            else _customer_name_key(order.customer_name))


def debt_flow(ledger: DebtLedger, start: date, end: date,
              key: str | None = None) -> tuple[Decimal, Decimal]:
    """[nợ thêm, trả được] of credit orders (of one customer when ``key`` is given), C01 §7.

    Nợ thêm: unpaid part of orders placed in the period, after payments on the order day.
    Trả được: payments made in the period after the order day, any order date."""
    payments: dict[str, list[DebtPayment]] = defaultdict(list)
    for payment in ledger.payments:
        payments[payment.order_id].append(payment)
    new = repaid = Decimal(0)
    for order in ledger.orders:
        if not order.on_credit or order.state == "huy" or order.excluded:
            continue
        if key is not None and _debt_key(order, ledger) != key:
            continue
        own = payments[order.order_id]
        if start <= order.day <= end:
            same_day = sum((p.amount for p in own if p.day == order.day), Decimal(0))
            new += max(order.total - same_day, Decimal(0))
        repaid += sum((p.amount for p in own if order.day < p.day and start <= p.day <= end),
                      Decimal(0))
    return new, repaid


def _debt_customer(ledger: DebtLedger, name: str) -> tuple[str | None, int]:
    """The one customer key whose name folds to ``name``; (None, matches) otherwise."""
    keys = {_debt_key(order, ledger) for order in ledger.orders
            if order.customer_name and fold(order.customer_name) == name}
    keys.discard(None)
    return (next(iter(keys)), 1) if len(keys) == 1 else (None, len(keys))


def _answer_debt_measure(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                         rulebook: str) -> Candidate:
    rules = read_rules(rulebook)
    needed = list(DEBT_MEASURE_FEATURES)
    if plan.intent in ("debt_total", "debt_customer_count"):
        needed.append("until_end_of_day")
    if plan.intent in ("debt_customer_count", "debt_flow"):
        needed.append("debt_by_customer")
    if plan.intent in ("debt_collected", "debt_flow"):
        needed.append("debt_flow_rule")
    if plan.threshold is not None:
        needed.append("debt_age")
    evidence: dict[str, Any] = {"intent": plan.intent, "start": plan.start.isoformat(),
                                "end": plan.end.isoformat()}
    if not all(getattr(rules, feature) for feature in needed):
        return Candidate(reason=f"Split rulebook does not support {plan.intent}",
                         evidence=evidence)
    evidence["rules"] = tuple(dict.fromkeys(rules.row(feature) for feature in needed))
    if re.search(r"\b(?:tra gop|so gop)\b", fold(catalog.readme)):
        # Rulebook: debt questions include instalment orders kept in a separate book,
        # which the D01 ledger does not read.
        return Candidate(reason="Instalment book is not part of the debt ledger",
                         evidence=evidence)
    prepared = prepare_debt(cache, catalog, rulebook)
    if not prepared["ok"]:
        evidence["debt_source_error"] = prepared["debt_source_error"]
        return Candidate(reason=prepared["reason"], evidence=evidence)
    ledger = cache["debt"]
    evidence["ledger"] = ledger.lineage
    if not ledger.first_day <= plan.start <= plan.end <= ledger.last_day:
        return Candidate(reason="Debt question outside ledger coverage", evidence=evidence)
    day = f"date.fromisoformat({plan.end.isoformat()!r})"
    period = f"date.fromisoformat({plan.start.isoformat()!r}), {day}"
    if plan.intent == "debt_total":
        t = plan.threshold
        age = int(t.value) if t is not None else None
        inclusive = t is not None and t.op == ">="
        value = int(_vnd(debt_total(ledger, plan.end, age, inclusive)))
        evidence.update(code=_code(f"_vnd(debt_total(ledger, {day}, {age!r}, {inclusive!r}))",
                                   open_debts, debt_total, _vnd), result=value)
        return Candidate("number", value, 1.0, "", evidence)
    if plan.intent == "debt_customer_count":
        value = debt_customer_count(ledger, plan.end)
        evidence.update(code=_code(f"debt_customer_count(ledger, {day})", open_debts,
                                   debt_customer_count, _debt_key, _customer_name_key),
                        result=value)
        return Candidate("number", value, 1.0, "", evidence)
    if plan.intent == "debt_collected":
        value = int(_vnd(debt_flow(ledger, plan.start, plan.end)[1]))
        evidence.update(code=_code(f"_vnd(debt_flow(ledger, {period})[1])", debt_flow,
                                   _debt_key, _vnd), result=value)
        return Candidate("number", value, 1.0, "", evidence)
    name = fold(plan.filters[0].value)
    key, matches = _debt_customer(ledger, name)
    evidence["customer"] = {"name": name, "matching_keys": matches}
    if key is None:
        return Candidate(reason="Customer name matches no customer or several",
                         evidence=evidence)
    new, repaid = debt_flow(ledger, plan.start, plan.end, key)
    value = [int(_vnd(new)), int(_vnd(repaid))]
    evidence.update(code=_code(f"debt_flow(ledger, {period}, {key!r})", debt_flow, _debt_key,
                               _vnd), result=value)
    return Candidate("list", value, 1.0, "", evidence)


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


def select_shift(selected: list[Sale], first: int, last: int) -> list[Sale]:
    """Sales whose order was placed from minute ``first`` to minute ``last`` of the day."""
    return [s for s in selected if s.minute is not None and first <= s.minute <= last]


def _answer_shift(plan: Plan, sales: list[Sale], rulebook: str,
                  evidence: dict[str, Any]) -> Candidate:
    """Revenue or orders of a rulebook shift, or its % change against the base shift."""
    rules = read_rules(rulebook)
    windows = {name: (first, last) for name, first, last in rules.shifts}
    name = plan.filters[0].value
    if name not in windows:
        return Candidate(reason="Split rulebook defines no such shift", evidence=evidence)
    selected = select_valid(sales, plan.start, plan.end)
    missing = sum(1 for s in selected if s.minute is None)
    evidence["inputs"].update(selected_rows=len(selected), rows_without_time=missing,
                              selected_orders=len({s.order_id for s in selected}),
                              shifts={k: list(v) for k, v in windows.items()})
    if missing or not selected:
        return Candidate(reason="Order time is missing for orders in the period"
                         if missing else "No valid orders in period", evidence=evidence)
    window = (f"date.fromisoformat({plan.start.isoformat()!r}), "
              f"date.fromisoformat({plan.end.isoformat()!r})")
    first, last = windows[name]
    shift_rows = select_shift(selected, first, last)
    measure = revenue_total if plan.intent == "revenue" else order_count
    if plan.comparison != "shift_change_percent":
        value = revenue(shift_rows) if plan.intent == "revenue" else order_count(shift_rows)
        function = revenue if plan.intent == "revenue" else order_count
        evidence.update(
            shift=name, calculation=f"{function.__name__} of valid orders placed in the shift",
            code=_code(f"{function.__name__}(select_shift(select_valid(sales, {window}), "
                       f"{first}, {last}))", select_valid, select_shift, function),
            result=value)
        return Candidate("number", value, 1.0, "", evidence)
    base = rules.shift_base
    if base not in windows or base == name:
        return Candidate(reason="Split rulebook gives no base shift", evidence=evidence)
    base_first, base_last = windows[base]
    current = Decimal(measure(shift_rows))
    previous = Decimal(measure(select_shift(selected, base_first, base_last)))
    value = change_percent(current, previous)
    evidence.update(
        shift=name, base_shift=base, current=str(current), base=str(previous),
        calculation="100 * (shift - base shift) / base shift; 2 decimals half up",
        code=_code(f"change_percent(Decimal({measure.__name__}(select_shift(rows, {first}, "
                   f"{last}))), Decimal({measure.__name__}(select_shift(rows, {base_first}, "
                   f"{base_last}))))  # rows = select_valid(sales, {window})",
                   select_valid, select_shift, measure, change_percent),
        result=value)
    if value is None:
        return Candidate(reason="Base shift has zero; change undefined", evidence=evidence)
    return Candidate("percent", value, 1.0, "", evidence)


def purchase_totals(receipts: tuple[Receipt, ...], start: date, end: date
                    ) -> dict[str | None, Decimal]:
    """Purchase money by supplier: receipts dated in the period, cancelled ones dropped."""
    totals: dict[str | None, Decimal] = defaultdict(Decimal)
    for receipt in receipts:
        if start <= receipt.day <= end and not receipt.cancelled:
            totals[receipt.supplier] += receipt.total
    return totals


def _answer_purchase(cache: dict[str, Any], catalog: Catalog, plan: Plan) -> Candidate:
    evidence: dict[str, Any] = {"intent": plan.intent, "start": plan.start.isoformat(),
                                "end": plan.end.isoformat(), "rules": plan.evidence}
    key = (_catalog_key(catalog), catalog.readme)
    if cache.get("purchases_key") != key:
        try:
            cache["purchases"] = load_purchases(catalog)
        except PurchaseSourceError as exc:
            cache["purchases"] = exc
        cache["purchases_key"] = key
    book = cache["purchases"]
    if isinstance(book, PurchaseSourceError):
        return Candidate(reason=str(book), evidence=evidence)
    first = min(r.day for r in book.receipts)
    last = _coverage_day(catalog.readme) or max(r.day for r in book.receipts)
    evidence.update(purchase_source=book.source, receipts=len(book.receipts),
                    coverage={"first_day": first.isoformat(), "last_day": last.isoformat()})
    if plan.start < first.replace(day=1) or plan.end > last:
        return Candidate(reason="Period outside the purchase records", evidence=evidence)
    window = (f"date.fromisoformat({plan.start.isoformat()!r}), "
              f"date.fromisoformat({plan.end.isoformat()!r})")
    totals = purchase_totals(book.receipts, plan.start, plan.end)
    if plan.intent == "purchase_total":
        value = int(_vnd(sum(totals.values(), Decimal(0))))
        evidence.update(code=_code(f"_vnd(sum(purchase_totals(receipts, {window}).values()))",
                                   purchase_totals, _vnd), result=value)
        return Candidate("number", value, 1.0, "", evidence)
    if not book.supplier_names or None in totals or not totals:
        return Candidate(reason="Supplier names are not recorded on every receipt",
                         evidence=evidence)
    best = max(totals.values())
    leaders = sorted(name for name, amount in totals.items() if amount == best)
    evidence["supplier_totals"] = {name: str(amount) for name, amount in totals.items()}
    if len(leaders) != 1:
        return Candidate(reason="Several suppliers tie for the most purchase money",
                         evidence=evidence)
    evidence.update(code=_code(f"max(purchase_totals(receipts, {window}).items(), key=amount)",
                               purchase_totals), result=leaders[0])
    return Candidate("string", leaders[0], 1.0, "", evidence)


def purchase_lines_of(lines: tuple[PurchaseLine, ...], name: str, codes: set[str]
                      ) -> list[PurchaseLine]:
    """Lines of one product: by its sales code when lines carry codes, else by folded name."""
    if any(line.product_id for line in lines):
        if len(codes) != 1:
            return []
        return [line for line in lines if line.product_id in codes]
    return [line for line in lines if line.product_name and fold(line.product_name) == fold(name)]


def average_purchase_price(lines: list[PurchaseLine], start: date, end: date) -> Decimal | None:
    """Rulebook §5: weighted by quantity bought, receipts dated in the period, cancelled
    receipts dropped; in the purchase unit of the lines. None when nothing was bought."""
    selected = [line for line in lines if start <= line.day <= end and not line.cancelled]
    quantity = sum((line.quantity for line in selected), Decimal(0))
    if not quantity:
        return None
    return sum((line.quantity * line.unit_price for line in selected), Decimal(0)) / quantity


def _answer_avg_purchase(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                         rulebook: str) -> Candidate:
    evidence: dict[str, Any] = {"intent": plan.intent, "start": plan.start.isoformat(),
                                "end": plan.end.isoformat(), "rules": plan.evidence}
    key = (_catalog_key(catalog), catalog.readme)
    if cache.get("purchase_lines_key") != key:
        try:
            cache["purchase_lines"] = load_purchase_lines(catalog)
        except PurchaseSourceError as exc:
            cache["purchase_lines"] = exc
        cache["purchase_lines_key"] = key
    book = cache["purchase_lines"]
    if isinstance(book, PurchaseSourceError):
        return Candidate(reason=str(book), evidence=evidence)
    first = min(line.day for line in book.lines)
    last = _coverage_day(catalog.readme) or max(line.day for line in book.lines)
    evidence.update(purchase_source=book.source, purchase_convention=book.convention,
                    coverage={"first_day": first.isoformat(), "last_day": last.isoformat()})
    if plan.start < first.replace(day=1) or plan.end > last:
        return Candidate(reason="Month outside the purchase records", evidence=evidence)
    name = plan.filters[0].value
    codes: set[str] = set()
    if prepare_sales(cache, catalog, rulebook)["ok"]:
        codes = {s.product_id for s in select_product(cache["sales"][0], name) if s.product_id}
    lines = purchase_lines_of(book.lines, name, codes)
    month = [line for line in lines if plan.start <= line.day <= plan.end and not line.cancelled]
    units = {fold(line.unit) for line in month}
    evidence.update(product_codes=sorted(codes), product_lines=len(lines), month_lines=len(month),
                    purchase_units=sorted(units))
    if not month:
        return Candidate(reason="No purchase line of this product in the month",
                         evidence=evidence)
    if units != {fold(plan.unit)}:
        return Candidate(reason="The unit asked is not the purchase unit of every line",
                         evidence=evidence)
    value = average_purchase_price(lines, plan.start, plan.end)
    if value is None:
        return Candidate(reason="Nothing bought in the month", evidence=evidence)
    result = int(_vnd(value))
    window = (f"date.fromisoformat({plan.start.isoformat()!r}), "
              f"date.fromisoformat({plan.end.isoformat()!r})")
    evidence.update(code=_code(f"_vnd(average_purchase_price(purchase_lines_of(lines, {name!r}, "
                               f"{sorted(codes)!r}), {window}))",
                               purchase_lines_of, average_purchase_price, _vnd),
                    result=result)
    return Candidate("number", result, 1.0, "", evidence)


def _stock_snapshot(cache: dict[str, Any], catalog: Catalog) -> StockSnapshot | StockSourceError:
    key = (_catalog_key(catalog), catalog.readme)
    if cache.get("stock_key") != key:
        try:
            cache["stock"] = load_stock(catalog)
        except StockSourceError as exc:
            cache["stock"] = exc
        cache["stock_key"] = key
    return cache["stock"]


def stock_names(cache: dict[str, Any], catalog: Catalog) -> list[str]:
    snapshot = _stock_snapshot(cache, catalog)
    if isinstance(snapshot, StockSourceError):
        return []
    return sorted({name for item in snapshot.items for name in (item.product_id, item.product_name)
                   if name})


def split_stock(quantity: Decimal, factor: Decimal) -> list[int | float]:
    if not quantity.is_finite() or quantity < 0 or not factor.is_finite() or factor <= 1:
        raise ValueError("Stock or conversion factor is invalid")
    big, rest = divmod(quantity, factor)
    return [int(big), int(rest) if rest == rest.to_integral() else float(rest)]


def _answer_stock(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                  rulebook: str) -> Candidate:
    """Stock of one product at the end of the last data day."""
    evidence: dict[str, Any] = {"intent": plan.intent, "start": plan.start.isoformat(),
                                "end": plan.end.isoformat(), "rules": plan.evidence}
    if plan.intent == "stock_split" and not read_rules(rulebook).stock_split:
        return Candidate(reason="Split rulebook does not define stock in larger units",
                         evidence=evidence)
    snapshot = _stock_snapshot(cache, catalog)
    if isinstance(snapshot, StockSourceError):
        return Candidate(reason=str(snapshot), evidence=evidence)
    evidence.update(stock_source=snapshot.source, stock_convention=snapshot.convention,
                    stock_day=snapshot.day.isoformat())
    if plan.end != snapshot.day:
        return Candidate(reason="Stock is known only at the end of the last data day",
                         evidence=evidence)
    name = plan.filters[0].value
    # Always load (or reuse) this business's sales so the codes used for matching never
    # depend on which question the worker answered before; a failed load means no codes.
    codes: set[str] = set()
    if prepare_sales(cache, catalog, rulebook)["ok"]:
        codes = {s.product_id for s in select_product(cache["sales"][0], name) if s.product_id}
    item = stock_item(snapshot.items, name, codes)
    if item is None:
        return Candidate(reason="Product matches no stock row or several", evidence=evidence)
    evidence["stock_item"] = {"product_id": item.product_id, "product_name": item.product_name,
                              "base_unit": item.base_unit, "quantity": str(item.quantity)}
    if plan.intent == "stock_split":
        big, base = plan.unit.split("|")
        if fold(item.base_unit or "") != base:
            return Candidate(reason="The remainder unit must be the product's base unit",
                             evidence=evidence)
        try:
            if cache.get("stock_conversion_key") != cache["stock_key"]:
                cache["stock_conversions"] = load_conversions(catalog)
                cache["stock_conversion_key"] = cache["stock_key"]
            matches = [c for c in cache["stock_conversions"] if c.big_unit == big and
                       c.key == (item.product_id if c.by_code else fold(item.product_name or ""))]
            factors = {(c.factor, c.base_unit or base) for c in matches}
            if len(factors) != 1:
                raise ValueError("No unique factor for the product and larger unit")
            factor, conversion_base = factors.pop()
            if conversion_base != base:
                raise ValueError("Conversion does not use the product's base unit")
            result = split_stock(item.quantity, factor)
        except (ValueError, ArithmeticError) as exc:
            return Candidate(reason=str(exc), evidence=evidence)
        evidence.update(conversion_sources=sorted({c.source for c in matches}), factor=str(factor),
                        code=_code(f"split_stock(Decimal({str(item.quantity)!r}), "
                                   f"Decimal({str(factor)!r}))", split_stock), result=result)
        return Candidate("list", result, 1.0, "", evidence)
    if item.base_unit is None or fold(item.base_unit) != fold(plan.unit):
        return Candidate(reason="The unit asked is not the product's base unit",
                         evidence=evidence)
    value = (int(item.quantity) if item.quantity == item.quantity.to_integral()
             else float(item.quantity))
    evidence.update(code=_code(f"stock_item(stock.items, {name!r}, {sorted(codes)!r}).quantity",
                               stock_item), result=value)
    return Candidate("number", value, 1.0, "", evidence)


def stock_item(items: tuple[StockItem, ...], name: str, codes: set[str]) -> StockItem | None:
    """The one stock row of the product: by its sales code, else by its folded name."""
    explicit = [item for item in items if item.product_id == name]
    if explicit:
        return explicit[0] if len(explicit) == 1 else None
    if len(codes) > 1:
        return None
    by_name = [item for item in items if item.product_name and fold(item.product_name) == fold(name)]
    if len(by_name) > 1:
        return None
    if codes:
        by_code = [item for item in items if item.product_id in codes]
        if by_code:
            return by_code[0] if len(by_code) == 1 else None
    return by_name[0] if len(by_name) == 1 else None


def select_size(sales: list[Sale], size: str) -> list[Sale]:
    """Items whose name ends with "(M)" / "(L)", case-insensitive (rulebook "Size M, size L")."""
    suffix = f"({size.lower()})"
    return [s for s in sales if s.product_name and s.product_name.strip().lower().endswith(suffix)]


def select_staff(selected: list[Sale], code: str) -> list[Sale]:
    """Sales of orders sold by the staff member with this code (case-insensitive)."""
    return [s for s in selected if s.staff_id is not None and s.staff_id.upper() == code.upper()]


def best_staff(selected: list[Sale]) -> tuple[str, Decimal]:
    """Staff code with the highest revenue; ties by code ascending."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for sale in selected:
        totals[sale.staff_id] += sale.amount
    code = min(totals, key=lambda key: (-totals[key], key))
    return code, totals[code]


def _answer_staff(plan: Plan, sales: list[Sale], evidence: dict[str, Any]) -> Candidate:
    """Revenue / orders of one staff code, or the staff code with the most revenue."""
    selected = select_valid(sales, plan.start, plan.end)
    missing = sum(1 for s in selected if s.staff_id is None)
    evidence["inputs"].update(selected_rows=len(selected), rows_without_staff=missing,
                              selected_orders=len({s.order_id for s in selected}))
    if missing or not selected:
        return Candidate(reason="Seller is missing for orders in the period"
                         if missing else "No valid orders in period", evidence=evidence)
    window = (f"date.fromisoformat({plan.start.isoformat()!r}), "
              f"date.fromisoformat({plan.end.isoformat()!r})")
    if plan.intent == "top_staff":
        code, total = best_staff(selected)
        evidence.update(calculation="staff code with the highest revenue; ties by code",
                        code=_code(f"best_staff(select_valid(sales, {window}))[0]",
                                   select_valid, best_staff),
                        staff_revenue=str(total), result=code)
        return Candidate("string", code, 1.0, "", evidence)
    code = plan.filters[0].value
    rows = select_staff(selected, code)
    if not rows:
        return Candidate(reason="No order of this staff code in the period", evidence=evidence)
    function = revenue if plan.intent == "revenue" else order_count
    value = function(rows)
    evidence.update(staff=code, calculation=f"{function.__name__} of the staff member's orders",
                    code=_code(f"{function.__name__}(select_staff(select_valid(sales, {window}), "
                               f"{code!r}))", select_valid, select_staff, function),
                    result=value)
    return Candidate("number", value, 1.0, "", evidence)


def change_percent(current: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    change = (current - previous) * 100 / previous
    return float(change.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _answer_deliveries(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                       rulebook: str) -> Candidate:
    rules = read_rules(rulebook)
    if not (rules.delivery_count and rules.calendar_month):
        return Candidate(reason="Current rulebook does not define monthly delivery counts")
    key = ("deliveries", _catalog_key(catalog))
    try:
        if key not in cache:
            cache[key] = load_deliveries(catalog)
        deliveries, tables = cache[key]
        last_day = _coverage_day(catalog.readme) or max(deliveries.values(), default=None)
    except ValueError as exc:
        return Candidate(reason=str(exc))
    if (not deliveries or plan.start > plan.end or plan.start > last_day
            or plan.end < min(deliveries.values())):
        return Candidate(reason="Delivery period coverage is not established")
    value = delivery_count(deliveries, plan.start, plan.end)
    evidence = {"intent": plan.intent, "tables": tables, "rules": plan.evidence,
                "start": plan.start.isoformat(), "end": plan.end.isoformat(),
                "code": _code(f"delivery_count(deliveries, {plan.start!r}, {plan.end!r})",
                              delivery_count), "result": value}
    return Candidate("number", value, 1.0, "", evidence)


def _lots(cache: dict[str, Any], catalog: Catalog, day: date, with_cost: bool = False):
    key = ("lots", _catalog_key(catalog), day, with_cost)
    if key not in cache:
        cache[key] = load_lots(catalog, day, with_cost=with_cost)
    return cache[key]


def lot_names(cache: dict[str, Any], catalog: Catalog, day: date) -> list[str]:
    try:
        _, names, _ = _lots(cache, catalog, day)
    except ValueError:
        return []
    return sorted(set(names) | set(names.values()))


def _answer_inventory_measure(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                              rulebook: str) -> Candidate:
    rules = read_rules(rulebook)
    start, end = plan.start, plan.end
    evidence = {"intent": plan.intent, "start": start.isoformat(), "end": end.isoformat(),
                "rules": plan.evidence}
    kind = "number"
    try:
        if plan.intent == "stock_value":
            if not (rules.stock_value and rules.until_end_of_day):
                raise ValueError("Current rulebook does not define stock value")
            snapshot = _stock_snapshot(cache, catalog)
            if isinstance(snapshot, StockSourceError):
                raise snapshot
            if snapshot.day != start:
                raise ValueError("Stock is available only at the end of the last data day")
            costs, source = load_stock_costs(catalog)
            value = int(_vnd(stock_value(snapshot.items, costs)))
            code = _code("int(_vnd(stock_value(snapshot.items, costs)))", stock_value, _vnd)
            sources = [snapshot.source, source]
        elif plan.intent == "earliest_expiry":
            if not (rules.earliest_expiry and rules.product_names):
                raise ValueError("Current rulebook does not define earliest expiry")
            lots, names, sources = _lots(cache, catalog, start)
            key = fold(plan.filters[0].value)
            matches = [code for code, name in names.items() if key in (fold(code), fold(name))]
            if len(matches) != 1:
                raise ValueError("Expiry question must match exactly one catalogue product")
            product = matches[0]
            kind, value = "date", earliest_expiry(lots, product, start).isoformat()
            code = _code(f"earliest_expiry(lots, {product!r}, {start!r}).isoformat()",
                         expiring_lots, earliest_expiry)
        else:
            if not rules.expiring_lots:
                raise ValueError("Current rulebook does not define the expiry horizon")
            lots, names, sources = _lots(cache, catalog, start, plan.intent == "expiring_lot_cost")
            selected = expiring_lots(lots, start, end)
            call = f"expiring_lots(lots, {start!r}, {end!r})"
            if plan.intent == "expiring_lot_cost":
                value = int(_vnd(expiring_lot_cost(selected)))
                code = _code(f"int(_vnd(expiring_lot_cost({call})))",
                             expiring_lots, expiring_lot_cost, _vnd)
            elif plan.intent == "expiring_lot_count":
                value = len(selected)
                code = _code(f"len({call})", expiring_lots)
            elif plan.intent == "expiring_product_count":
                value = len({lot.product for lot in selected})
                code = _code(f"len({{lot.product for lot in {call}}})", expiring_lots)
            else:
                if not rules.expiry_list_order:
                    raise ValueError("Current rulebook does not define expiry list order")
                kind, value = "list", expiring_products(selected, names)
                code = _code(f"expiring_products({call}, names)", expiring_lots, expiring_products)
    except ValueError as exc:
        return Candidate(reason=str(exc), evidence=evidence)
    evidence.update(tables=sources, code=code, result=value)
    return Candidate(kind, value, 1.0, "", evidence)


def _answer_sales_measure(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                          rulebook: str, sales: list[Sale], evidence: dict[str, Any]) -> Candidate:
    rules = read_rules(rulebook)
    if (not rules.revenue or not getattr(rules, plan.intent)
            or (plan.intent in {"unsold_count", "top_gross_profit"} and not rules.product_names)
            or (plan.intent != "unsold_count" and not (rules.gross_profit and rules.calendar_month))):
        return Candidate(reason="Required measure rules are absent in the current split")
    start, end = plan.start, plan.end
    products = [row for row in ranking_candidates(sales, cache.get("ranking_eligible"))
                if fold(row.product_name or "") != "phi phuc vu"]
    kind = "number"
    try:
        if plan.intent == "unsold_count":
            value = unsold_count(products, start, end)
            code = _code(f"unsold_count(products, {start!r}, {end!r})", product_key, unsold_count)
        else:
            rows = select_valid(products if plan.intent == "top_gross_profit" else sales, start, end)
            if not any(sale.unit_cost_at_sale is not None for sale in sales):
                raise ValueError("No documented costs per selling unit in sales data")
            if plan.intent == "top_gross_profit":
                kind, value = "string", top_gross_profit(rows, rules.ties_by_code)
                code = _code(f"top_gross_profit(rows, {rules.ties_by_code!r})",
                             product_key, gross_profit, top_gross_profit)
            elif plan.intent == "net_profit":
                if "expenses" not in cache:
                    cache["expenses"] = load_expenses(catalog)
                expenses, source = cache["expenses"]
                evidence["expense_table"] = source
                value = int(_vnd(net_profit(rows, expenses, start, end)))
                code = _code(f"int(_vnd(net_profit(rows, expenses, {start!r}, {end!r})))",
                             gross_profit, net_profit, _vnd)
            else:
                value = int(_vnd(gross_profit(rows)))
                code = _code("int(_vnd(gross_profit(rows)))", gross_profit, _vnd)
    except ValueError as exc:
        return Candidate(reason=str(exc), evidence=evidence)
    evidence.update(code=code, result=value)
    return Candidate(kind, value, 1.0, "", evidence)


def answer_plan(cache: dict[str, Any], catalog: Catalog, plan: Plan,
                rulebook: str = "") -> Candidate:
    reason = unsupported(plan)
    if reason:
        return Candidate(reason=reason)
    if plan.intent == "delivery_count":
        return _answer_deliveries(cache, catalog, plan, rulebook)
    if plan.intent in LOT_INTENTS | {"stock_value"}:
        return _answer_inventory_measure(cache, catalog, plan, rulebook)
    if plan.intent in DEBT_INTENTS:
        return _answer_debt_measure(cache, catalog, plan, rulebook)
    if plan.intent in ("stock", "stock_split"):
        return _answer_stock(cache, catalog, plan, rulebook)
    if plan.intent in ("purchase_total", "top_supplier"):
        return _answer_purchase(cache, catalog, plan)
    if plan.intent == "avg_purchase_price":
        return _answer_avg_purchase(cache, catalog, plan, rulebook)
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
    if plan.intent in SALES_MEASURE_INTENTS:
        return _answer_sales_measure(cache, catalog, plan, rulebook, sales, evidence)
    product_call = ""
    if plan.filters and plan.filters[0].field == "shift":
        return _answer_shift(plan, sales, rulebook, evidence)
    if plan.filters and plan.filters[0].field == "staff" or plan.intent == "top_staff":
        return _answer_staff(plan, sales, evidence)
    if plan.filters and plan.filters[0].field in GROUP_FIELDS:
        return _answer_group(plan, sales, evidence)
    if plan.filters and plan.filters[0].field in DRUG_FIELDS:
        return _answer_drug_revenue(cache, catalog, rulebook, plan, sales, evidence)
    if plan.filters and plan.filters[0].field == "size":
        size = plan.filters[0].value
        sales = select_size(sales, size)
        evidence["inputs"]["size_rows"] = len(sales)
        if not sales:
            return Candidate(reason=f"No item named with size ({size})", evidence=evidence)
        product_call = f"select_size(sales, {size!r})"
    elif plan.filters:
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


def _answer_price_effect(plan: Plan, sales: list[Sale], evidence: dict[str, Any], window: str,
                         source_expr: str) -> Candidate:
    """Units of one product sold N days from the price increase against the N days before."""
    if not sales or any(s.base_unit is None or fold(s.base_unit) != fold(plan.unit)
                        for s in sales):
        return Candidate(reason="The unit asked is not the product's base unit",
                         evidence=evidence)
    current = quantity_in_unit(select_valid(sales, plan.start, plan.end), plan.unit)
    previous = quantity_in_unit(select_valid(sales, plan.compare_start, plan.compare_end),
                                plan.unit)
    if current is None or previous is None:
        return Candidate(reason="A sold quantity is unknown", evidence=evidence)
    value = change_percent(current, previous)
    compare_window = (f"date.fromisoformat({plan.compare_start.isoformat()!r}), "
                      f"date.fromisoformat({plan.compare_end.isoformat()!r})")
    evidence.update(
        calculation="100 * (units from D - units before D) / units before D; 2 decimals half up",
        code=_code(f"change_percent(quantity_in_unit(select_valid({source_expr}, {window}), "
                   f"{plan.unit!r}), quantity_in_unit(select_valid({source_expr}, "
                   f"{compare_window}), {plan.unit!r}))",
                   select_valid, quantity_in_unit, change_percent),
        current_quantity=str(current), compare_quantity=str(previous), result=value)
    if value is None:
        return Candidate(reason="Nothing sold before the increase; change undefined",
                         evidence=evidence)
    return Candidate("percent", value, 1.0, "", evidence)


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
        if plan.comparison == "change_percent":
            return _answer_price_effect(plan, sales, evidence, window, source_expr)
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


def select_drugs(rows: list[Sale], products: dict[str, Drug], field: str,
                 value: str) -> list[Sale]:
    if any(not row.product_id for row in rows):
        raise ValueError("A valid sales line has no product identity for the drug join")
    if field == "ingredient":
        labels = {d.ingredient for d in products.values()
                  if d.ingredient and ingredient_key(d.ingredient) == ingredient_key(value)}
        if len(labels) != 1:
            raise ValueError("Ingredient does not match exactly one drug catalog label")
        label = labels.pop()
        codes = {code for code, drug in products.items() if drug.ingredient == label}
        return [row for row in rows if row.product_id in codes]
    codes = {code for code, drug in products.items() if drug.prescription}
    return [row for row in rows if (row.product_id in codes) == (value == "rx")]


def _answer_drug_revenue(cache: dict[str, Any], catalog: Catalog, rulebook: str, plan: Plan,
                         sales: list[Sale], evidence: dict[str, Any]) -> Candidate:
    field, value = plan.filters[0].field, plan.filters[0].value
    rules = read_rules(rulebook)
    if not rules.revenue or not getattr(rules, f"{field}_revenue"):
        return Candidate(reason="Split rulebook does not support the drug filter", evidence=evidence)
    key = (_catalog_key(catalog), catalog.readme)
    if cache.get("drugs_key") != key:
        try:
            cache["drugs"] = load_drugs(catalog)
        except ValueError as exc:
            cache["drugs"] = exc
        cache["drugs_key"] = key
    drugs = cache["drugs"]
    if isinstance(drugs, ValueError):
        return Candidate(reason=str(drugs), evidence=evidence)
    if not any(row.product_id in drugs.products for row in sales):
        return Candidate(reason="Sales identities do not join to the drug catalog", evidence=evidence)
    rows = select_valid(sales, plan.start, plan.end)
    try:
        selected = select_drugs(rows, drugs.products, field, value)
    except ValueError as exc:
        return Candidate(reason=str(exc), evidence=evidence)
    evidence.update(drug_source=drugs.source, drug_convention=drugs.convention,
                    matching_sales_lines=len(selected), valid_sales_lines=len(rows))
    window = f"date.fromisoformat({plan.start.isoformat()!r}), date.fromisoformat({plan.end.isoformat()!r})"
    all_rows = f"select_valid(sales, {window})"
    subset = f"select_drugs({all_rows}, drugs.products, {field!r}, {value!r})"
    if plan.comparison == "share":
        part, total = revenue_total(selected), revenue_total(rows)
        result = share_percent(part, total)
        evidence.update(drug_revenue=str(part), total_revenue=str(total), result=result,
                        code=_code(f"share_percent(revenue_total({subset}), revenue_total({all_rows}))",
                                   select_valid, select_drugs, ingredient_key, revenue_total, share_percent))
        if result is None:
            return Candidate(reason="Total revenue is zero; drug revenue share is undefined",
                             evidence=evidence)
        return Candidate("percent", result, 1.0, "", evidence)
    result = revenue(selected)
    evidence.update(result=result, code=_code(f"revenue({subset})", select_valid, select_drugs,
                                             ingredient_key, revenue))
    return Candidate("number", result, 1.0, "", evidence)


def _answer_group(plan: Plan, sales: list[Sale], evidence: dict[str, Any]) -> Candidate:
    """Revenue of one product group / customer group, or its share of the whole shop."""
    item = plan.filters[0]
    valid = select_valid(sales, plan.start, plan.end)
    if not any(getattr(s, item.field) for s in sales):
        return Candidate(reason=f"No {item.field} recorded in this shop", evidence=evidence)
    if (item.field == "customer_group" and evidence.get("customer_order_columns")
            and any(s.customer_id and not s.customer_group for s in valid)):
        return Candidate(reason="A recorded customer's group is unknown in the period",
                         evidence=evidence)
    part_rows = select_group(valid, item.field, item.value)
    evidence["inputs"].update(selected_rows=len(valid), group_rows=len(part_rows))
    part = revenue_total(part_rows)
    window = (f"date.fromisoformat({plan.start.isoformat()!r}), "
              f"date.fromisoformat({plan.end.isoformat()!r})")
    group_call = f"select_group(select_valid(sales, {window}), {item.field!r}, {item.value!r})"
    if plan.intent == "average_order":
        value = average_order_value(part_rows)
        if value is None:
            return Candidate(reason="No valid orders in customer group", evidence=evidence)
        evidence.update(calculation="group revenue / distinct valid group orders; round VND half up",
                        code=_code(f"average_order_value({group_call})",
                                   select_valid, select_group, average_order_value),
                        result=value)
        return Candidate("number", value, 1.0, "", evidence)
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
