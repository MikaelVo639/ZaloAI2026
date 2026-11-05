"""Checks on executed output; these do not establish semantic correctness."""

import math
from datetime import date

from datamate.agent.compute import unsupported
from datamate.contracts import Candidate, Plan
from datamate.evaluation.validation import validate_record

ANSWER_TYPES = {
    "revenue": "number", "order_count": "number", "average_order": "number",
    "cancel_count": "number", "cancel_rate": "percent", "best_day": "date",
}


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
            if plan.comparison == "change_percent":
                expected = "percent"
            elif plan.intent == "top_products":
                expected = "string" if plan.top_k == 1 else "list"
            else:
                expected = ANSWER_TYPES.get(plan.intent)
            if expected is None or candidate.type != expected:
                raise ValueError("Answer type disagrees with Plan")
            if plan.intent == "cancel_rate" and not 0 <= candidate.answer <= 100:
                raise ValueError("Cancellation percentage outside [0, 100]")
            if plan.intent in {"order_count", "cancel_count"}:
                count = candidate.answer
                bound_key = "period_orders" if plan.intent == "cancel_count" else "selected_orders"
                bound = evidence.get("inputs", {}).get(bound_key)
                if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                        or not isinstance(bound, int) or not 0 <= count <= bound):
                    raise ValueError("Order count outside executed input bounds")
            if candidate.type == "date":
                if not plan.start <= date.fromisoformat(candidate.answer) <= plan.end:
                    raise ValueError("Date outside requested period")
            if candidate.type == "list":
                if (not 1 <= len(candidate.answer) <= plan.top_k
                        or not all(isinstance(x, str) and x for x in candidate.answer)):
                    raise ValueError("Product list outside Plan limits")
            if candidate.type == "string" and not candidate.answer:
                raise ValueError("Empty product name")
        return candidate
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        evidence = (dict(candidate.evidence) if isinstance(candidate, Candidate)
                    and isinstance(candidate.evidence, dict) else {})
        evidence["validation"] = {"status": "rejected", "error": str(exc)}
        return Candidate(reason=f"Executed result rejected: {exc}", evidence=evidence)
