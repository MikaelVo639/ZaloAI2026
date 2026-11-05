import math
from typing import Any

from datamate.agent import compute as tasks
from datamate.agent.validation import check_executed
from datamate.agent.worker import Worker, WorkerResult
from datamate.contracts import BusinessContext, Candidate, Question
from datamate.semantics.planner import plan_question
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
    compute = staticmethod(tasks.answer_plan)

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
        if "rules" not in context.cache:
            context.cache["rules"] = read_rules(context.split.rulebook)
        plan = plan_question(question, context.cache["rules"])
        if plan is None:
            return Candidate(reason="Question or split rules outside baseline grammar")
        planned = {"intent": plan.intent, "start": plan.start.isoformat(),
                   "end": plan.end.isoformat(), "rules": plan.evidence}
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

    def _prepare(self, context: BusinessContext) -> Candidate | None:
        """Load the business in the worker once; never retry a failed business."""
        state = context.cache.get("worker_sales")
        if state is not None and not state["ok"]:
            return Candidate(reason=state["reason"], evidence=dict(state["evidence"]))
        if (state is not None and self.worker.alive
                and state["generation"] == self.worker.generation):
            return None
        # First question of the business, or the worker was replaced after a timeout.
        result = self.worker.run(self.prepare, self.ingestion_timeout,
                                 catalog=context.catalog, rulebook=context.split.rulebook)
        evidence: dict[str, Any] = {"ingestion_execution": result.execution()}
        if result.ok and result.value["ok"]:
            context.cache["worker_sales"] = {"ok": True, "generation": self.worker.generation}
            return None
        reason = result.value["reason"] if result.ok else _failure_reason("Sales ingestion", result)
        context.cache["worker_sales"] = {"ok": False, "reason": reason, "evidence": evidence}
        return Candidate(reason=reason, evidence=dict(evidence))


def _failure_reason(step: str, result: WorkerResult) -> str:
    if result.timed_out:
        return f"{step} timed out after {result.timeout_seconds:g}s"
    return f"{step} worker failed: {result.error}"


def _execution_failure(step: str, result: WorkerResult, evidence: dict[str, Any]) -> Candidate:
    return Candidate(reason=_failure_reason(step, result),
                     evidence={**evidence, "execution": result.execution()})
