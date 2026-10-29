import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from datamate.contracts import Question, Split


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line, parse_constant=_reject_constant)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected a JSON object")
            records.append(value)
    return records


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def index_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for record in records:
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Every record needs a non-empty string id")
        if identifier in result:
            raise ValueError(f"Duplicate id: {identifier}")
        result[identifier] = record
    return result


def contained_path(root: Path, relative: str) -> Path:
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes data directory: {relative}")
    return result


def load_split(root: Path) -> Split:
    root = root.resolve()
    # Deliberately no discovery of answers.jsonl anywhere in inference.
    raw = read_jsonl(root / "questions.jsonl")
    index_records(raw)
    questions = []
    for item in raw:
        for key in ("business_id", "question", "as_of_date"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"{item['id']}: missing or invalid {key}")
        business_id = item["business_id"]
        if business_id in (".", "..") or "/" in business_id or "\\" in business_id:
            raise ValueError(f"Invalid business_id: {business_id}")
        contained_path(root / "data", business_id)
        questions.append(Question(item["id"], business_id, item["question"],
                                  date.fromisoformat(item["as_of_date"])))
    calendar_path = root / "calendar.csv"
    if not calendar_path.is_file():
        raise ValueError(f"Missing calendar: {calendar_path}")
    return Split(root, tuple(questions), (root / "rulebook.md").read_text(encoding="utf-8-sig"),
                 calendar_path)
