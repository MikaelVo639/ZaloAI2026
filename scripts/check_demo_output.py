"""Check packaged demo accuracy, per-question logs and repeatability."""

import argparse
import hashlib
import json
from pathlib import Path

from datamate.evaluation.scorer import evaluate
from datamate.evaluation.validation import validate_submission
from datamate.io import index_records, read_jsonl


def check(data_dir: Path, runs: list[Path]) -> None:
    questions = index_records(read_jsonl(data_dir / "questions.jsonl"))
    references = read_jsonl(data_dir / "answers.jsonl")
    previous = None
    for run in runs:
        records = read_jsonl(run / "submission.jsonl")
        validate_submission(records, set(questions), require_complete=True)
        indexed = index_records(records)
        metrics = evaluate(records, references)
        actual = tuple(metrics[key] for key in ("n", "correct", "wrong", "abstain"))
        if actual != (24, 21, 0, 3):
            raise ValueError(f"Demo metrics differ from fixture expectation: {actual}")
        expected_logs = {hashlib.sha256(key.encode()).hexdigest() + ".json" for key in questions}
        if {p.name for p in (run / "logs").glob("*.json")} != expected_logs:
            raise ValueError("Missing or extra per-question logs")
        for key in questions:
            log_path = run / "logs" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
            log = json.loads(log_path.read_text(encoding="utf-8"))
            if (log["id"] != key or log["answer"] != indexed[key]
                    or log["final_answer"] != indexed[key] or log["error"]):
                raise ValueError("Log does not match submission or contains an execution error")
            if (type(log.get("elapsed_ms")) is not int or log["elapsed_ms"] < 0
                    or log["elapsed_seconds"] < 0 or log.get("thinking") is not None):
                raise ValueError("Invalid elapsed time in log")
            if indexed[key]["type"] != "abstain" and (not log.get("code") or log.get("output") is None):
                raise ValueError("Answered question lacks computation evidence")
            if (indexed[key]["type"] not in ("abstain", "unanswerable")
                    and log["output"] != indexed[key]["answer"]):
                raise ValueError("Computation output does not match submission")
        manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
        for filename, field in (("questions.jsonl", "questions_sha256"),
                                ("calendar.csv", "calendar_sha256")):
            if manifest[field] != hashlib.sha256((data_dir / filename).read_bytes()).hexdigest():
                raise ValueError(f"Input checksum mismatch: {filename}")
        if previous is not None and indexed != previous:
            raise ValueError("Repeated runs produced different answers")
        previous = indexed
    print(f"Verified {len(runs)} demo run(s): expected accuracy, complete logs, matching answers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("runs", type=Path, nargs="+")
    args = parser.parse_args()
    check(args.data_dir, args.runs)
