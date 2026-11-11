"""Verify a completed TRAIN benchmark; --repeat runs independent inference as well."""

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

from datamate.evaluation.benchmark import (
    benchmark,
    digest,
    error_info,
    input_tree,
    save,
    scorer_identity,
    target_runtime,
    validate_holdout,
)
from datamate.evaluation.release import read_run, verify_runs
from datamate.evaluation.scorer import evaluate
from datamate.io import read_jsonl


def verify(checkout: Path, data: Path, run: Path, output: Path, repeat: bool = False) -> dict:
    checkout, data, run, output = (p.resolve() for p in (checkout, data, run, output))
    if (output.exists() or output.is_relative_to(data) or data.is_relative_to(output)
            or output.is_relative_to(run) or run.is_relative_to(output)):
        raise ValueError("Use a new output directory separate from input and benchmark trees")
    started = time.monotonic()
    metadata = json.loads((run / "experiment.json").read_text(encoding="utf-8"))
    if metadata.get("status") != "complete":
        raise ValueError("Benchmark is incomplete")
    python = checkout / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    runtime = target_runtime(python, checkout)
    if runtime["target_source_sha256"] != metadata.get("target_source_sha256"):
        raise ValueError("Current source differs from the benchmark source")
    if any(metadata.get(key) != value for key, value in scorer_identity().items()):
        raise ValueError("Scorer identity differs from the benchmark")
    if digest(run / "config.toml") != metadata.get("config_sha256"):
        raise ValueError("Benchmark configuration checksum mismatch")
    if digest(run / "business_split.json") != metadata.get("holdout_sha256"):
        raise ValueError("Holdout checksum mismatch")
    manifest = json.loads((run / "business_split.json").read_text(encoding="utf-8"))
    split = validate_holdout(data, manifest)
    tree_hash = hashlib.sha256(json.dumps(input_tree(data)).encode()).hexdigest()
    if tree_hash != metadata.get("input_sha256"):
        raise ValueError("Input tree checksum mismatch")
    if digest(data / "answers.jsonl") != metadata.get("reference_sha256"):
        raise ValueError("Reference checksum mismatch")
    ids = {q.id for q in split.questions}
    predictions, logs = read_run(run, ids)
    run_metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    checksums = {"questions_sha256": digest(data / "questions.jsonl"),
                 "calendar_sha256": digest(data / "calendar.csv"),
                 "rulebook_sha256": hashlib.sha256(split.rulebook.encode()).hexdigest()}
    if any(run_metadata.get(key) != value for key, value in checksums.items()):
        raise ValueError("Pipeline input checksum mismatch")
    if run_metadata.get("config") != metadata.get("target_run", {}).get("config"):
        raise ValueError("Pipeline configuration differs from benchmark metadata")
    references = read_jsonl(data / "answers.jsonl")
    if {row["id"] for row in references} != ids:
        raise ValueError("Question/reference IDs differ")
    recorded = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    scores = {}
    for partition, selected in (("train", ids), ("validation", set(manifest["validation_ids"]))):
        score = evaluate([predictions[key] for key in sorted(selected)],
                         [row for row in references if row["id"] in selected])
        scores[partition] = {key: score[key] for key in ("n", "correct", "wrong", "abstain", "pa")}
        if any(recorded[partition].get(key) != value for key, value in scores[partition].items()):
            raise ValueError(f"Stored {partition} metrics do not match rescoring")
    errors = sum(bool(error_info(log)) for log in logs.values())
    if errors:
        raise ValueError(f"Run contains {errors} execution error(s)")
    all_scores = evaluate(list(predictions.values()), references)
    review = [row for row in all_scores["outcomes"] if row["outcome"] != "correct"]
    output.mkdir(parents=True)
    with (output / "needs-review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("id", "outcome", "reference_type", "question", "reason",
                         "review_required"))
        questions = {q.id: q.question for q in split.questions}
        for row in review:
            key = row["id"]
            writer.writerow((key, row["outcome"], row["reference"]["type"],
                             questions[key], logs[key].get("reason", ""),
                             "source evidence required"))
    report = {"artifact_integrity": "passed", "runtime": runtime, "metrics": scores,
              "review_required_questions": len(review),
              "absence_audit_complete": False, "repeat_verified": False,
              "quick_check_seconds": time.monotonic() - started,
              "repeat_status": "not_requested"}
    save(output / "verification.json", report)
    if repeat:
        report["repeat_status"] = "running"
        save(output / "verification.json", report)
        try:
            benchmark(checkout, data, run / "business_split.json", run / "config.toml",
                      output / "repeat")
            report["repeat_checks"] = verify_runs(
                data, [run, output / "repeat"], output / "repeat-checks")
            report.update(repeat_verified=True, repeat_status="passed")
        except (OSError, ValueError) as exc:
            report.update(repeat_status="failed", failure=str(exc))
            save(output / "verification.json", report)
            raise
        save(output / "verification.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeat", action="store_true",
                        help="Run full inference again and compare answers by ID")
    args = parser.parse_args()
    try:
        report = verify(args.checkout, args.data_dir, args.run_dir, args.output_dir, args.repeat)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
