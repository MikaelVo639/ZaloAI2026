"""Choose a confidence gate on training businesses, then report validation once."""

import hashlib
import json
import math
import re
import tomllib
from pathlib import Path

from datamate.evaluation.benchmark import digest, input_tree, save, scorer_identity, validate_holdout
from datamate.evaluation.release import read_run
from datamate.evaluation.scorer import evaluate
from datamate.io import index_records, read_jsonl, write_jsonl


def calibrate(run_dir, data_dir, holdout, output_dir):
    run, data, holdout, output = map(Path.resolve, map(Path, (run_dir, data_dir, holdout, output_dir)))
    if output.exists() or output.is_relative_to(data) or data.is_relative_to(output) or output.is_relative_to(run):
        raise ValueError("Calibration requires a new output tree separate from inputs")
    meta = json.loads((run / "experiment.json").read_text(encoding="utf-8"))
    manifest = json.loads(holdout.read_text(encoding="utf-8"))
    split = validate_holdout(data, manifest)
    expected = {**scorer_identity(), "holdout_sha256": digest(holdout),
                "input_sha256": hashlib.sha256(json.dumps(input_tree(data)).encode()).hexdigest(),
                "reference_sha256": digest(data / "answers.jsonl"),
                "config_sha256": digest(run / "config.toml")}
    if meta.get("status") != "complete" or any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError("Calibration requires complete run with matching inputs/labels/holdout/scorer/config")
    config_text = (run / "config.toml").read_text(encoding="utf-8")
    config = tomllib.loads(config_text)
    if config.get("min_confidence", 1.0) != 0 or meta.get("config") != config:
        raise ValueError("Collect unsuppressed candidates using min_confidence=0 first")
    predictions, logs = read_run(run, {q.id for q in split.questions})
    confidences = {}
    for key, prediction in predictions.items():
        value = logs[key].get("candidate_confidence")
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and 0 <= value <= 1)
        if not valid and prediction["type"] != "abstain":
            raise ValueError("Answered candidate has missing/invalid confidence")
        confidences[key] = value if valid else 0.0
    refs = index_records(read_jsonl(data / "answers.jsonl"))
    if set(refs) != set(predictions):
        raise ValueError("Reference/question IDs differ")
    train_ids = set(manifest["train_ids"])
    training = evaluate([predictions[k] for k in sorted(train_ids)], [refs[k] for k in sorted(train_ids)])
    outcomes = index_records(training["outcomes"])
    # Thresholds and objective depend only on training businesses.
    thresholds = sorted({0.0, 1.0} | {confidences[k] for k in train_ids})
    sweep = []
    for threshold in thresholds:
        correct = sum(outcomes[k]["outcome"] == "correct" and confidences[k] >= threshold for k in train_ids)
        wrong = sum(outcomes[k]["outcome"] == "wrong" and confidences[k] >= threshold for k in train_ids)
        sweep.append({"threshold": threshold, "correct": correct, "wrong": wrong,
                      "abstain": len(train_ids) - correct - wrong, "pa": (correct - wrong) / len(train_ids)})
    best = max(sweep, key=lambda row: (row["pa"], row["threshold"]))
    selected = best["threshold"]
    final = {k: predictions[k] if confidences[k] >= selected else {"id": k, "type": "abstain"}
             for k in predictions}
    validation_ids = sorted(manifest["validation_ids"])
    validation = evaluate([final[k] for k in validation_ids], [refs[k] for k in validation_ids])
    # Preserve arbitrary backend-specific TOML settings; edit only the root gate.
    parts = re.split(r"(?m)(?=^\s*\[)", config_text, maxsplit=1)
    prefix, count = re.subn(r"(?m)^\s*min_confidence\s*=.*$", f"min_confidence = {selected!r}", parts[0])
    if count != 1:
        raise ValueError("Expected exactly one explicit root min_confidence setting")
    locked_config = prefix + "".join(parts[1:])
    if tomllib.loads(locked_config) != {**config, "min_confidence": selected}:
        raise ValueError("Calibration changed unrelated config settings")
    report = {"scorer": expected["scorer_version"], "source_revision": meta["revision"],
              "identity": expected, "selection_partition": "train_businesses",
              "tie_break": "higher_threshold", "selected_threshold": selected, "train_sweep": sweep,
              "selected_train": best, "validation": {k: v for k, v in validation.items() if k != "outcomes"},
              "distinct_training_confidences": len({confidences[k] for k in train_ids}),
              "kind": "candidate_replay_not_independent_inference",
              "note": "Re-run locked config and compare with A; do not tune from validation."}
    output.mkdir(parents=True)
    (output / "config.toml").write_text(locked_config, encoding="utf-8", newline="\n")
    write_jsonl(output / "submission.jsonl", [final[k] for k in sorted(final)])
    save(output / "calibration.json", report)
    return {"selected_threshold": selected, "train": best, "validation": report["validation"]}
