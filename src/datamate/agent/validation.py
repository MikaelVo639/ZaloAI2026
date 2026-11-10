"""Checks on executed output; these do not establish semantic correctness."""

import math
from datetime import date
from decimal import Decimal
from typing import Any

from datamate.agent.compute import C01_INTENTS, _vnd, unsupported
from datamate.contracts import DEBT_TABLE_COLUMNS, Candidate, Plan
from datamate.evaluation.validation import is_number, validate_record

ANSWER_TYPES = {
    "revenue": "number", "order_count": "number", "average_order": "number",
    "cancel_count": "number", "cancel_rate": "percent", "best_day": "date",
    "quantity": "number", "count_where": "number",
    "revenue_increasing": "boolean", "top_debt_customers": "table",
    "debt_total": "number", "debt_customer_count": "number", "debt_collected": "number",
    "debt_flow": "list", "debt_age_buckets": "list", "top_staff": "string", "stock": "number", "stock_split": "list",
    "purchase_total": "number", "top_supplier": "string",
    "avg_purchase_price": "number", "daily_sale_price": "number",
    "unsold_count": "number", "delivery_count": "number",
    "gross_profit": "number", "top_gross_profit": "string", "net_profit": "number",
    "stock_value": "number", "earliest_expiry": "date", "expiring_products": "list",
    "expiring_lot_count": "number", "expiring_lot_cost": "number", "expiring_product_count": "number",
}


def _decimal_evidence(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Monetary evidence must use Decimal strings")
    amount = Decimal(value)
    if not amount.is_finite():
        raise ValueError("Monetary evidence must be finite")
    return amount


def _check_trend(candidate: Candidate, plan: Plan) -> None:
    periods = candidate.evidence.get("periods")
    pairs = candidate.evidence.get("comparisons")
    if not isinstance(periods, list) or len(periods) != len(plan.periods):
        raise ValueError("Revenue evidence must cover each requested month")
    totals = []
    for item, (start, end) in zip(periods, plan.periods):
        if (not isinstance(item, dict)
                or (item.get("start"), item.get("end")) != (start.isoformat(), end.isoformat())):
            raise ValueError("Revenue evidence has incorrect month boundaries")
        totals.append(_decimal_evidence(item.get("revenue")))
    if not isinstance(pairs, list) or len(pairs) != len(periods) - 1:
        raise ValueError("Revenue evidence must compare adjacent months")
    outcomes = []
    for index, pair in enumerate(pairs):
        greater = totals[index + 1] > totals[index]
        if (not isinstance(pair, dict) or pair.get("greater") is not greater
                or pair.get("previous") != periods[index]["start"]
                or pair.get("current") != periods[index + 1]["start"]):
            raise ValueError("Revenue comparison disagrees with Decimal totals")
        outcomes.append(greater)
    if candidate.answer is not all(outcomes):
        raise ValueError("Boolean disagrees with the executed comparisons")


def _check_debt_table(candidate: Candidate, plan: Plan) -> None:
    answer = candidate.answer
    rows = answer["rows"]
    if answer["columns"] != list(DEBT_TABLE_COLUMNS) or not 1 <= len(rows) <= plan.top_k:
        raise ValueError("Debt table shape disagrees with the Plan")
    if any(not isinstance(name, str) or not name.strip()
           or type(amount) is not int or amount <= 0 for name, amount in rows):
        raise ValueError("Debt rows require a customer name and positive integer VND")

    evidence = candidate.evidence
    if evidence.get("snapshot") != plan.end.isoformat() or not isinstance(evidence.get("ledger"), dict):
        raise ValueError("Debt evidence needs a matching snapshot and ledger lineage")
    ranking = evidence.get("ranking")
    if not isinstance(ranking, list):
        raise ValueError("Debt evidence needs the executed ranking")
    entries = []
    seen = set()
    for item in ranking:
        key, name, count = item["customer_key"], item["name"], item["orders"]
        amount = _decimal_evidence(item["debt"])
        if (not isinstance(key, str) or not key or key in seen or not isinstance(name, str)
                or type(count) is not int or count <= 0 or amount <= 0):
            raise ValueError("Invalid or duplicate customer in debt evidence")
        seen.add(key)
        entries.append((key, name, amount, count))
    if entries != sorted(entries, key=lambda entry: (-entry[2], entry[0])):
        raise ValueError("Debt evidence disagrees with the sort and tie rule")
    expected = [[name, int(_vnd(amount))] for _, name, amount, _ in entries[:plan.top_k]]
    if rows != expected:
        raise ValueError("Debt table differs from the ranked evidence")
    open_orders = evidence.get("open_orders")
    if (type(open_orders) is not int or open_orders < sum(entry[3] for entry in entries)
            or _decimal_evidence(evidence.get("total_debt")) < sum(entry[2] for entry in entries)):
        raise ValueError("Debt totals are inconsistent with the customer ranking")


def check_executed(candidate: Candidate, plan: Plan | None = None) -> Candidate:
    """Invalid output becomes abstain, never evidence of unanswerability.

    Boolean/table shape validation is already part of the shared scorer contract.
    No new Plan intent or business interpretation is introduced here.
    """
    try:
        if not isinstance(candidate, Candidate):
            raise ValueError("Worker did not return a Candidate")
        if not isinstance(candidate.evidence, dict):
            raise ValueError("Worker evidence must be a dictionary")
        if candidate.type == "abstain":
            return candidate
        if plan is not None:
            reason = unsupported(plan)
            if reason:
                raise ValueError(reason)
        if (isinstance(candidate.confidence, bool)
                or not math.isfinite(candidate.confidence)
                or not 0 <= candidate.confidence <= 1):
            raise ValueError("Invalid confidence")
        if candidate.type == "unanswerable":
            if not candidate.evidence.get("coverage_source"):
                raise ValueError("Unanswerable needs explicit data coverage evidence")
            if plan is not None and plan.intent in C01_INTENTS:
                diagnostic = candidate.evidence.get("debt_source_error", {})
                if (plan.intent != "top_debt_customers" or not isinstance(diagnostic, dict)
                        or candidate.evidence.get("snapshot") != plan.end.isoformat()
                        or diagnostic.get("code") != "absent"
                        or diagnostic.get("sources_complete") is not True):
                    raise ValueError("C01 unanswerable requires a verified absent debt source")
            validate_record({"id": "execution", "type": candidate.type,
                             "answer": candidate.answer})
            return candidate
        validate_record({"id": "execution", "type": candidate.type, "answer": candidate.answer})
        evidence = candidate.evidence
        code = evidence.get("code", {})
        if not isinstance(code, dict) or not code.get("source") or not code.get("call"):
            raise ValueError("Answer needs executed code evidence")
        if "result" not in evidence or evidence["result"] != candidate.answer:
            raise ValueError("Answer differs from execution output")
        if plan is not None:
            if plan.shape in ("series", "by_filter"):
                expected = "list"
            elif plan.shape == "best_period":
                expected = "date"
            elif plan.comparison in {"change_percent", "share", "shift_change_percent"}:
                expected = "percent"
            elif plan.intent in {"top_products", "top_customers"}:
                expected = "string" if plan.top_k == 1 else "list"
            else:
                expected = ANSWER_TYPES.get(plan.intent)
            if expected is None or candidate.type != expected:
                raise ValueError("Answer type disagrees with Plan")
            if plan.intent == "revenue_increasing":
                _check_trend(candidate, plan)
            if plan.intent == "top_debt_customers":
                _check_debt_table(candidate, plan)
            if plan.intent == "cancel_rate" and not 0 <= candidate.answer <= 100:
                raise ValueError("Cancellation percentage outside [0, 100]")
            if plan.intent == "count_where":
                bound = (evidence.get("customers_bought") if plan.threshold.subject == "customer"
                         else evidence.get("inputs", {}).get("selected_rows"))
                if candidate.type == "percent":
                    if not 0 <= candidate.answer <= 100:
                        raise ValueError("Customer percentage outside [0, 100]")
                elif (type(candidate.answer) is not int or candidate.answer < 0
                      or type(bound) is not int or candidate.answer > bound):
                    raise ValueError("Threshold count outside executed input bounds")
            if plan.intent in {"order_count", "cancel_count"} and not plan.comparison:
                count = candidate.answer
                bound_key = "period_orders" if plan.intent == "cancel_count" else "selected_orders"
                bound = evidence.get("inputs", {}).get(bound_key)
                if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                        or not isinstance(bound, int) or not 0 <= count <= bound):
                    raise ValueError("Order count outside executed input bounds")
            if candidate.type == "date":
                if plan.shape == "best_period":
                    if candidate.answer not in {start.strftime("%Y-%m") for start, _ in plan.periods}:
                        raise ValueError("Month outside requested periods")
                elif plan.intent == "earliest_expiry":
                    if date.fromisoformat(candidate.answer) < plan.start:
                        raise ValueError("Expiry is before the stock snapshot")
                elif not plan.start <= date.fromisoformat(candidate.answer) <= plan.end:
                    raise ValueError("Date outside requested period")
            if candidate.type == "list":
                if plan.intent == "stock_split":
                    if (len(candidate.answer) != 2 or type(candidate.answer[0]) is not int
                            or candidate.answer[0] < 0 or not is_number(candidate.answer[1])
                            or candidate.answer[1] < 0):
                        raise ValueError("Stock split requires two nonnegative quantities")
                elif plan.intent == "debt_flow":
                    if (len(candidate.answer) != 2 or not all(
                            type(x) is int and x >= 0 for x in candidate.answer)):
                        raise ValueError("Debt flow is [nợ thêm, trả được] in whole VND")
                elif plan.intent == "debt_age_buckets":
                    if (len(candidate.answer) != 3 or not all(
                            type(x) is int and x >= 0 for x in candidate.answer)):
                        raise ValueError("Debt age report requires three nonnegative VND balances")
                elif plan.shape == "series":
                    if (len(candidate.answer) != len(plan.periods)
                            or not all(is_number(x) for x in candidate.answer)):
                        raise ValueError("Revenue series disagrees with requested periods")
                elif plan.intent == "expiring_products":
                    if not all(isinstance(x, str) and x for x in candidate.answer):
                        raise ValueError("Expiring products must be catalogue names")
                elif plan.shape == "by_filter":
                    if (len(candidate.answer) != len(plan.filters)
                            or not all(type(x) is int and x >= 0 for x in candidate.answer)):
                        raise ValueError("Revenue per label disagrees with the Plan filters")
                elif (not 1 <= len(candidate.answer) <= plan.top_k
                      or not all(isinstance(x, str) and x for x in candidate.answer)):
                    raise ValueError("Ranking list outside Plan limits")
            if candidate.type == "string" and not candidate.answer:
                raise ValueError("Empty product name")
        return candidate
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError) as exc:
        evidence = (dict(candidate.evidence) if isinstance(candidate, Candidate)
                    and isinstance(candidate.evidence, dict) else {})
        evidence["validation"] = {"status": "rejected", "error": str(exc)}
        return Candidate(reason=f"Executed result rejected: {exc}", evidence=evidence)
