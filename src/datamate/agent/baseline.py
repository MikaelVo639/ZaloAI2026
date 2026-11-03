import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from datamate.contracts import BusinessContext, Candidate, Question
from datamate.ingestion.sales import load_sales
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

INTENTS = frozenset({"revenue", "order_count", "top_products", "average_order",
                     "cancel_count", "cancel_rate", "best_day"})


class AbstainAgent:
    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        return Candidate(reason="Explicit abstain backend")


class BaselineAgent:
    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        if "rules" not in context.cache:
            context.cache["rules"] = read_rules(context.split.rulebook)
        plan = plan_question(question, context.cache["rules"])
        if plan is None:
            return Candidate(reason="Question or split rules outside baseline grammar")
        if plan.intent not in INTENTS:
            # A Plan intent this backend cannot compute must not fall through to a ranking.
            return Candidate(reason=f"Intent {plan.intent!r} not supported by baseline agent")
        if "sales" not in context.cache:
            # Cache adapter failures as well: do not rescan a bad workbook per question.
            try:
                context.cache["sales"] = load_sales(context.catalog)
            except (ValueError, OSError) as exc:
                context.cache["sales"] = exc
        cached = context.cache["sales"]
        if isinstance(cached, Exception):
            return Candidate(reason=str(cached))
        sales, source = cached
        evidence = {**source, "intent": plan.intent, "start": plan.start.isoformat(),
                    "end": plan.end.isoformat(), "rules": plan.evidence}
        if not sales or plan.start > plan.end:
            return Candidate(reason="Empty data or future-only period", evidence=evidence)
        days = [s.day for s in sales]
        if plan.end < min(days) or plan.start > max(days):
            # Absence of sales alone does not establish the business's coverage.
            coverage = re.search(r"Khoảng dữ liệu:\s*(\d{4}-\d{2}-\d{2})\s+đến\s+"
                                 r"(\d{4}-\d{2}-\d{2})", context.catalog.readme)
            if coverage:
                first, last = map(date.fromisoformat, coverage.groups())
                if plan.end < first or plan.start > last:
                    evidence["coverage_source"] = coverage.group()
                    return Candidate(type="unanswerable", confidence=1.0,
                                     reason="Kỳ hỏi nằm hoàn toàn ngoài khoảng dữ liệu ghi trong README",
                                     evidence=evidence)
            return Candidate(reason="No sales in period; business coverage is not established",
                             evidence=evidence)
        in_period = [s for s in sales if plan.start <= s.day <= plan.end]
        if plan.intent in ("cancel_count", "cancel_rate"):
            # Order status is per order (the adapter rejects conflicting states).
            orders = {s.order_id: s.status for s in in_period}
            cancelled = sum(1 for status in orders.values() if status == "cancelled")
            evidence["orders_all_statuses"] = len(orders)
            evidence["orders_cancelled"] = cancelled
            if plan.intent == "cancel_count":
                evidence["calculation"] = "count distinct order_id with status cancelled"
                return Candidate("number", cancelled, 1.0, "", evidence)
            if not orders:
                return Candidate(reason="No orders in period; rate undefined", evidence=evidence)
            rate = (Decimal(cancelled) * 100 / Decimal(len(orders))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            evidence["calculation"] = "100 * cancelled / all orders (any status); 2 decimals half up"
            return Candidate("percent", float(rate), 1.0, "", evidence)
        selected = [s for s in in_period if s.status == "valid"]
        evidence["selected_rows"] = len(selected)
        if plan.intent == "average_order":
            orders = {s.order_id for s in selected}
            if not orders:
                return Candidate(reason="No valid orders in period; average undefined",
                                 evidence=evidence)
            total = sum((s.amount for s in selected), Decimal(0))
            value = (total / len(orders)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            evidence.update(revenue=str(total), valid_orders=len(orders),
                            calculation="revenue / valid orders; round VND half up")
            return Candidate("number", int(value), 1.0, "", evidence)
        if plan.intent == "best_day":
            by_day: dict[date, Decimal] = defaultdict(Decimal)
            for sale in selected:
                by_day[sale.day] += sale.amount
            if not by_day:
                return Candidate(reason="No valid sales in period", evidence=evidence)
            top = max(by_day.values())
            best = sorted(day for day, value in by_day.items() if value == top)
            evidence.update(best_revenue=str(top), tied_days=[d.isoformat() for d in best],
                            calculation="argmax of daily revenue on valid whole orders")
            if len(best) > 1:
                return Candidate(reason="Tie for the best day; rulebook gives no date tie-break",
                                 evidence=evidence)
            return Candidate("date", best[0].isoformat(), 1.0, "", evidence)
        if plan.intent == "revenue":
            value = sum((s.amount for s in selected), Decimal(0)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP)
            evidence["calculation"] = "sum(line_amount) on valid whole orders; round VND half up"
            return Candidate("number", int(value), 1.0, "", evidence)
        if plan.intent == "order_count":
            evidence["calculation"] = "count distinct order_id on valid whole orders"
            return Candidate("number", len({s.order_id for s in selected}), 1.0, "", evidence)
        if not selected or any(s.quantity_base is None or not s.product_id or not s.product_name
                               for s in selected):
            return Candidate(reason="Ranking needs explicit base quantities and product ids/names",
                             evidence=evidence)
        quantities: dict[str, Decimal] = defaultdict(Decimal)
        names = {}
        for sale in selected:
            quantities[sale.product_id] += sale.quantity_base
            names.setdefault(sale.product_id, sale.product_name)
        ranked = sorted(quantities, key=lambda key: (-quantities[key], key))[:plan.top_k]
        answer = [names[key] for key in ranked]
        evidence["calculation"] = "sum(quantity_base) by product_id, descending; ties by id"
        return Candidate("string" if plan.top_k == 1 else "list",
                         answer[0] if plan.top_k == 1 else answer, 1.0, "", evidence)
