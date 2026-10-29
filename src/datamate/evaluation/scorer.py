"""Local proxy based on published rules. This is NOT the organizer's scorer."""

import math
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from datamate.evaluation.validation import is_number, validate_record, validate_submission
from datamate.io import index_records
from datamate.semantics.text import normalize_answer


def _number_matches(actual: Any, expected: Any) -> bool:
    if not is_number(actual) or not is_number(expected):
        return False
    if isinstance(actual, int) and isinstance(expected, int):
        return actual == expected
    return math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9)


def _scalar_matches(actual: Any, expected: Any, aliases: Any = None) -> bool:
    if isinstance(expected, str):
        choices = [expected] + (aliases if isinstance(aliases, list) else [])
        return isinstance(actual, str) and any(
            isinstance(choice, str) and normalize_answer(actual) == normalize_answer(choice)
            for choice in choices)
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if expected is None:
        return actual is None
    return _number_matches(actual, expected)


def _table_matches(actual: Any, expected: Any) -> bool:
    actual_cols = [normalize_answer(c) for c in actual["columns"]]
    expected_cols = [normalize_answer(c) for c in expected["columns"]]
    if len(actual_cols) != len(expected_cols):
        return False
    if len(set(actual_cols)) == len(actual_cols) and set(actual_cols) == set(expected_cols):
        positions = [actual_cols.index(c) for c in expected_cols]
    else:
        positions = list(range(len(expected_cols)))
    # Published rule treats rows as a set. Normalize names but preserve cell types.
    def canonical(row: list[Any]) -> tuple[Any, ...]:
        return tuple(("string", normalize_answer(x)) if isinstance(x, str)
                     else ("bool", x) if isinstance(x, bool)
                     else ("number", Decimal(str(x))) if is_number(x)
                     else ("null", None) for x in row)
    predicted = {canonical([row[i] for i in positions]) for row in actual["rows"]}
    reference = {canonical(row) for row in expected["rows"]}
    return predicted == reference


def matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    kind = expected["type"]
    if actual["type"] != kind:
        return False
    if kind == "unanswerable":
        return True
    a, b = actual.get("answer"), expected.get("answer")
    if kind == "percent":
        rounded = lambda x: Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return rounded(a) == rounded(b)
    if kind == "number":
        return _number_matches(a, b)
    if kind in ("string", "boolean", "date"):
        return _scalar_matches(a, b, expected.get("alias"))
    if kind == "list":
        aliases = expected.get("alias", [])
        return len(a) == len(b) and all(
            _scalar_matches(x, y, aliases[i] if i < len(aliases) else [])
            for i, (x, y) in enumerate(zip(a, b)))
    if kind == "table":
        return _table_matches(a, b)
    return False


def evaluate(predictions: list[dict[str, Any]], references: list[dict[str, Any]],
             businesses: dict[str, str] | None = None) -> dict[str, Any]:
    refs = index_records(references)
    for ref in references:
        validate_record(ref)
    validate_submission(predictions, set(refs), require_complete=False)
    predicted = index_records(predictions)
    outcomes = []
    counts: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = {}
    by_business: dict[str, Counter[str]] = {}
    for identifier, ref in refs.items():
        actual = predicted.get(identifier, {"id": identifier, "type": "abstain"})
        outcome = "abstain" if actual["type"] == "abstain" else (
            "correct" if matches(actual, ref) else "wrong")
        counts[outcome] += 1
        by_type.setdefault(ref["type"], Counter())[outcome] += 1
        if businesses and identifier in businesses:
            by_business.setdefault(businesses[identifier], Counter())[outcome] += 1
        outcomes.append({"id": identifier, "outcome": outcome,
                         "prediction": actual, "reference": ref})
    n = len(refs)
    answered = counts["correct"] + counts["wrong"]
    return {
        "scorer": "local_proxy_v1_not_official", "n": n,
        **{key: counts[key] for key in ("correct", "wrong", "abstain")},
        "pa": (counts["correct"] - counts["wrong"]) / n if n else 0.0,
        "coverage": answered / n if n else 0.0,
        "accuracy_when_answered": counts["correct"] / answered if answered else 0.0,
        "by_type": {k: dict(v) for k, v in by_type.items()},
        "by_business": {k: dict(v) for k, v in by_business.items()},
        "outcomes": outcomes,
    }
