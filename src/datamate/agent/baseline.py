import math
from datetime import date
from typing import Any

from datamate.agent import compute as tasks
from datamate.agent.validation import check_executed
from datamate.agent.worker import Worker, WorkerResult
from datamate.contracts import BusinessContext, Candidate, Plan, Question
from datamate.semantics.planner import (
    lot_product_question,
    plan_question,
    price_history_question,
    stock_question,
)
from datamate.semantics.rules import read_rules

QUESTION_TIMEOUT_SECONDS = 30.0
INGESTION_TIMEOUT_SECONDS = 300.0


class AbstainAgent:
    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        return Candidate(reason="Explicit abstain backend")


class BaselineAgent:
    """Plans in-process (pure, fast); loads data and calculates in a deadline worker.

    A timeout or worker error is an abstain: failing to compute proves nothing
    about whether the data can answer the question.
    """

    # Worker tasks; module-level functions so the spawn worker can import them.
    prepare = staticmethod(tasks.prepare_sales)
    prepare_debt = staticmethod(tasks.prepare_debt)
    prepare_delivery_orders = staticmethod(tasks.prepare_delivery_orders)
    compute = staticmethod(tasks.answer_plan)
    names = staticmethod(tasks.product_names)
    vocabulary = staticmethod(tasks.vocabulary)

    def __init__(self, question_timeout: float = QUESTION_TIMEOUT_SECONDS,
                 ingestion_timeout: float = INGESTION_TIMEOUT_SECONDS) -> None:
        for name, value in (("question_timeout", question_timeout),
                            ("ingestion_timeout", ingestion_timeout)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive number of seconds")
        self.question_timeout = question_timeout
        self.ingestion_timeout = ingestion_timeout
        self.worker = Worker()

    def close(self) -> None:
        self.worker.close()

    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        plan = self._plan(question, context)
        if plan is None:
            return Candidate(reason="Question or split rules outside baseline grammar")
        return self._answer_plan(plan, context, as_of_date=question.as_of_date)

    def _plan(self, question: Question, context: BusinessContext) -> Plan | None:
        """Shared deterministic planning, including SEM-01's product-name retry."""
        if "rules" not in context.cache:
            context.cache["rules"] = read_rules(context.split.rulebook)
        rules = context.cache["rules"]
        plan = plan_question(question, rules)
        if plan is None and rules.earliest_expiry and lot_product_question(question):
            result = self.worker.run(tasks.lot_names, self.question_timeout,
                                     catalog=context.catalog, day=question.as_of_date)
            return plan_question(question, rules, result.value) if result.ok else None
        if plan is None and rules.until_end_of_day and stock_question(question):
            if "stock_names" not in context.cache:
                result = self.worker.run(tasks.stock_names, self.question_timeout,
                                         catalog=context.catalog)
                context.cache["stock_names"] = result.value if result.ok else []
            stock_plan = plan_question(question, rules, context.cache["stock_names"])
            if stock_plan and stock_plan.intent in ("stock", "stock_split"):
                return stock_plan
        if plan is None:
            # Retry with this business's product names/codes ("doanh thu <mặt hàng> …").
            names = self._product_names(context)
            if names:
                vocabulary = self._vocabulary(context)
                plan = plan_question(question, rules, names, vocabulary)
                if plan is None and price_history_question(question, rules):
                    if "price_history" not in context.cache:
                        result = self.worker.run(tasks.price_history, self.question_timeout,
                                                 catalog=context.catalog,
                                                 rulebook=context.split.rulebook)
                        context.cache["price_history"] = result.value if result.ok else ({}, "")
                    increases, source = context.cache["price_history"]
                    plan = plan_question(question, rules, names, vocabulary,
                                         price_increases=increases, price_source=source)
        return plan

    def _answer_plan(self, plan: Plan, context: BusinessContext, *,
                     as_of_date: date | None = None) -> Candidate:
        planned = {"intent": plan.intent, "start": plan.start.isoformat(),
                   "end": plan.end.isoformat(), "rules": plan.evidence}
        if plan.intent in tasks.C01_INTENTS | tasks.DEBT_INTENTS:
            reason = tasks.unsupported(plan)
            if reason or (as_of_date is not None and plan.end > as_of_date):
                return Candidate(reason=reason or "Requested date is after as_of_date",
                                 evidence=planned)
        failure = None
        if plan.intent == "delivered_order_count":
            failure = self._prepare(context, source="delivery_orders")
        elif plan.intent == "top_debt_customers" or plan.intent in tasks.DEBT_INTENTS:
            failure = self._prepare(context, source="debt")
        elif plan.intent not in tasks.INDEPENDENT_INTENTS:
            failure = self._prepare(context)
        if failure is not None:
            failure.evidence = {**planned, **failure.evidence}
            return failure
        result = self.worker.run(self.compute, self.question_timeout,
                                 catalog=context.catalog, plan=plan,
                                 rulebook=context.split.rulebook)
        if not result.ok:
            return _execution_failure("Calculation", result, planned)
        candidate = check_executed(result.value, plan)
        candidate.evidence["execution"] = result.execution()
        return candidate

    def _product_names(self, context: BusinessContext) -> list[str]:
        """Names/codes read once per business in the worker; [] when the data cannot load."""
        if "product_names" not in context.cache:
            names: list[str] = []
            if self._prepare(context) is None:
                result = self.worker.run(self.names, self.question_timeout,
                                         catalog=context.catalog,
                                         rulebook=context.split.rulebook)
                if result.ok:
                    names = result.value
            context.cache["product_names"] = names
        return context.cache["product_names"]

    def _vocabulary(self, context: BusinessContext) -> dict[str, list[str]]:
        """Group names (product groups, customer groups) read once per business."""
        if "vocabulary" not in context.cache:
            vocabulary: dict[str, list[str]] = {}
            if self._prepare(context) is None:
                result = self.worker.run(self.vocabulary, self.question_timeout,
                                         catalog=context.catalog,
                                         rulebook=context.split.rulebook)
                if result.ok:
                    vocabulary = result.value
            context.cache["vocabulary"] = vocabulary
        return context.cache["vocabulary"]

    def _prepare(self, context: BusinessContext, *, source: str = "sales") -> Candidate | None:
        """Load the required source once per worker generation and split rulebook."""
        cache_key = f"worker_{source}"
        rulebook = context.split.rulebook
        state = context.cache.get(cache_key)
        if state is not None and state["rulebook"] != rulebook:
            state = None
        if state is not None and not state["ok"]:
            return Candidate(reason=state["reason"], evidence=dict(state["evidence"]))
        if (state is not None and self.worker.alive
                and state["generation"] == self.worker.generation):
            return None
        # First question of the business, or the worker was replaced after a timeout.
        task, step = {
            "sales": (self.prepare, "Sales ingestion"),
            "debt": (self.prepare_debt, "Debt ingestion"),
            "delivery_orders": (self.prepare_delivery_orders, "Delivery ingestion"),
        }[source]
        result = self.worker.run(task, self.ingestion_timeout,
                                 catalog=context.catalog, rulebook=rulebook)
        evidence: dict[str, Any] = {"ingestion_execution": result.execution()}
        if result.ok and (source == "debt" or result.value["ok"]):
            # The debt worker retains typed failures for interpretation with the Plan.
            context.cache[cache_key] = {"ok": True, "generation": self.worker.generation,
                                        "rulebook": rulebook}
            return None
        reason = result.value["reason"] if result.ok else _failure_reason(step, result)
        context.cache[cache_key] = {"ok": False, "reason": reason, "evidence": evidence,
                                    "rulebook": rulebook}
        return Candidate(reason=reason, evidence=dict(evidence))


def _failure_reason(step: str, result: WorkerResult) -> str:
    if result.timed_out:
        return f"{step} timed out after {result.timeout_seconds:g}s"
    return f"{step} worker failed: {result.error}"


def _execution_failure(step: str, result: WorkerResult, evidence: dict[str, Any]) -> Candidate:
    return Candidate(reason=_failure_reason(step, result),
                     evidence={**evidence, "execution": result.execution()})
