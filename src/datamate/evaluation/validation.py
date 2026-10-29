import math
import re
from datetime import date
from typing import Any

from datamate.contracts import Candidate
from datamate.io import index_records

TYPES = {"number", "percent", "string", "date", "boolean", "list", "table",
         "unanswerable", "abstain"}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, bool)) or is_number(value) or value is None


def validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record.get("id"), str) or not record["id"]:
        raise ValueError("Missing id")
    kind = record.get("type")
    if kind not in TYPES:
        raise ValueError(f"{record['id']}: unknown answer type {kind!r}")
    if "reason" in record and not isinstance(record["reason"], str):
        raise ValueError(f"{record['id']}: reason must be a string")
    if kind in ("abstain", "unanswerable"):
        if record.get("answer") is not None:
            raise ValueError(f"{record['id']}: {kind} cannot contain an answer value")
        return
    value = record.get("answer")
    valid = False
    if kind in ("number", "percent"):
        valid = is_number(value)
    elif kind == "string":
        valid = isinstance(value, str)
    elif kind == "boolean":
        valid = isinstance(value, bool)
    elif kind == "date" and isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value):
            try:
                date.fromisoformat(value if len(value) == 10 else value + "-01")
                valid = True
            except ValueError:
                pass
    elif kind == "list":
        valid = isinstance(value, list) and all(isinstance(x, str) or is_number(x) for x in value)
    elif kind == "table" and isinstance(value, dict):
        columns, rows = value.get("columns"), value.get("rows")
        valid = (isinstance(columns, list) and bool(columns)
                 and all(isinstance(c, str) for c in columns)
                 and len(set(columns)) == len(columns)
                 and isinstance(rows, list)
                 and all(isinstance(r, list) and len(r) == len(columns)
                         and all(_scalar(v) for v in r) for r in rows))
    if not valid:
        raise ValueError(f"{record['id']}: invalid {kind} answer {value!r}")


def validate_submission(records: list[dict[str, Any]], expected_ids: set[str],
                        require_complete: bool = True) -> dict[str, int]:
    indexed = index_records(records)
    for record in records:
        validate_record(record)
    extras = set(indexed) - expected_ids
    missing = expected_ids - set(indexed)
    if extras:
        raise ValueError(f"Unknown question ids: {sorted(extras)[:5]}")
    if missing and require_complete:
        raise ValueError(f"Missing {len(missing)} question ids: {sorted(missing)[:5]}")
    return {"records": len(records), "expected": len(expected_ids), "missing": len(missing)}


def finalize(identifier: str, candidate: Candidate, min_confidence: float) -> dict[str, Any]:
    if not math.isfinite(candidate.confidence) or candidate.confidence < min_confidence:
        return {"id": identifier, "type": "abstain"}
    result = {"id": identifier, "type": candidate.type}
    if candidate.type not in ("abstain", "unanswerable"):
        result["answer"] = candidate.answer
    if candidate.type == "unanswerable" and candidate.reason:
        result["reason"] = candidate.reason
    validate_record(result)
    return result
