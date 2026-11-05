"""Offline AGENT-02 acceptance reporting; labels stay in the Evaluation runner.

This module inspects completed run artifacts, never reference answers. Runtime
projections are workload assumptions, not a guarantee for an unseen private split.
"""

import importlib.metadata
import importlib.util
import json
import math
import platform
import re
from pathlib import Path
from typing import Any

from datamate.agent.model_runtime import MODEL_ID, MODEL_REVISION, sha256
from datamate.io import index_records, read_jsonl


def environment_report(model_path: Path) -> dict[str, Any]:
    issues = []
    versions = {}
    for package in ("torch", "transformers", "huggingface-hub", "safetensors"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
            issues.append(f"Missing optional runtime package: {package}")
    cuda, gpu = None, None
    if importlib.util.find_spec("torch") is not None:
        import torch

        cuda = torch.version.cuda
        version = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", cuda or "")
        if version is None or tuple(map(int, version.groups())) < (12, 8):
            issues.append("Competition GPU environment requires CUDA >=12.8")
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
        else:
            issues.append("No CUDA GPU visible to this Python environment")
    else:
        issues.append("CUDA runtime cannot be checked without torch")
    if not (model_path / "model_manifest.json").is_file():
        issues.append("Prepared model snapshot/manifest is missing")
    return {"ready_for_gpu_acceptance": not issues, "issues": issues,
            "python": platform.python_version(), "os": platform.system(),
            "architecture": platform.machine(), "packages": versions, "cuda": cuda, "gpu": gpu}


def _seconds(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Missing or invalid timing: {name}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Missing or invalid timing: {name}")
    return float(value)


def runtime_report(run_dir: Path, private_questions: int | None = None,
                   private_businesses: int | None = None) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    submission = index_records(read_jsonl(run_dir / "submission.jsonl"))
    logs = {}
    for path in sorted((run_dir / "logs").glob("*.json")):
        log = json.loads(path.read_text(encoding="utf-8"))
        if log["id"] in logs:
            raise ValueError("Duplicate question log")
        logs[log["id"]] = log
    if len(submission) != manifest["questions"] or logs.keys() != submission.keys():
        raise ValueError("Runtime report requires a complete submission and matching logs")
    for identifier, log in logs.items():
        if log["final_answer"] != submission[identifier]:
            raise ValueError("Runtime report log/submission mismatch")
    pipeline_elapsed = _seconds(manifest["elapsed_seconds"], "run.elapsed_seconds")
    experiment_path = run_dir / "experiment.json"
    experiment = json.loads(experiment_path.read_text(encoding="utf-8")) if experiment_path.is_file() else {}
    elapsed = _seconds(experiment.get("predict_wall_seconds", pipeline_elapsed), "inference process wall clock")
    if elapsed + 0.01 < pipeline_elapsed:
        raise ValueError("Inference process time is shorter than pipeline time")
    models = manifest.get("models", [])
    load = sum(_seconds(model["load_execution"]["elapsed_seconds"], "model load") for model in models)
    question_times = [_seconds(log["elapsed_seconds"], "question") for log in logs.values()]
    business_times = {}
    for log in logs.values():
        business_times.setdefault(log["business_id"], _seconds(log["business_ingestion_seconds"], "catalog"))
    if len(business_times) != manifest["businesses"]:
        raise ValueError("Business count differs from run manifest")
    total_questions, total_businesses = sum(question_times), sum(business_times.values())
    if load > total_questions + 0.01 or total_questions + total_businesses > elapsed + 0.01:
        raise ValueError("Timing components exceed the recorded wall-clock run")
    model_generations = sum("model_output" in log.get("evidence", {}) for log in logs.values())
    model_answers = sum("model_output" in log.get("evidence", {})
                        and log["final_answer"]["type"] != "abstain" for log in logs.values())
    report = {"measured_seconds_including_load": elapsed, "pipeline_seconds": pipeline_elapsed,
              "model_load_seconds": load,
              "questions": len(submission), "businesses": len(business_times),
              "model_generations": model_generations, "model_answers": model_answers,
              "models": models, "measured_within_6_hours": elapsed < 21600,
              "submission_sha256": sha256(run_dir / "submission.jsonl")}
    if (private_questions is None) != (private_businesses is None):
        raise ValueError("Provide both private question and business counts")
    if private_questions is not None:
        if (type(private_questions) is not int or type(private_businesses) is not int
                or not 0 < private_businesses <= private_questions or not submission or not business_times):
            raise ValueError("Private counts must be positive integers, businesses <= questions")
        fixed = max(0.0, elapsed - total_questions - total_businesses)
        estimate = (fixed + load + max(0.0, total_questions - load) * private_questions / len(submission)
                    + total_businesses * private_businesses / len(business_times))
        report["private_projection"] = {
            "questions": private_questions, "businesses": private_businesses,
            "seconds": estimate, "estimated_within_6_hours": estimate < 21600,
            "assumption": "Same question and business workload distribution and hardware; not a guarantee",
        }
    return report


def compare_acceptance(baseline: Path, model_first: Path, model_repeat: Path,
                       private_questions: int | None = None,
                       private_businesses: int | None = None) -> dict[str, Any]:
    runs = [baseline, model_first, model_repeat]
    experiments = [json.loads((path / "experiment.json").read_text(encoding="utf-8")) for path in runs]
    identity = ("input_sha256", "holdout_sha256", "reference_sha256")
    for experiment in experiments:
        if experiment.get("status") != "complete" or any(not experiment.get(key) for key in identity):
            raise ValueError("Acceptance requires complete runs with input/holdout/reference checksums")
        if any(experiment[key] != experiments[0][key] for key in identity):
            raise ValueError("A/B input, holdout or reference identity mismatch")
    if experiments[1]["config_sha256"] != experiments[2]["config_sha256"]:
        raise ValueError("Repeat run must use the same model config")
    holdouts = [json.loads((path / "business_split.json").read_text(encoding="utf-8")) for path in runs]
    if any(holdout.get("seed") != 42 for holdout in holdouts):
        raise ValueError("Acceptance requires the team's seed-42 holdout")
    if any(holdout != holdouts[0] for holdout in holdouts[1:]):
        raise ValueError("Holdout manifests differ")
    outcomes = [index_records(read_jsonl(path / "outcomes.jsonl")) for path in runs]
    if any(rows.keys() != outcomes[0].keys() for rows in outcomes[1:]):
        raise ValueError("A/B outcome question IDs differ")
    selected = set(holdouts[0]["validation_ids"])
    if not selected or not selected <= outcomes[0].keys():
        raise ValueError("Validation partition is empty or missing from outcomes")
    changes = [{"id": identifier, "partition": "validation" if identifier in selected else "train",
                "before": outcomes[0][identifier]["outcome"], "after": outcomes[1][identifier]["outcome"],
                "before_prediction": outcomes[0][identifier]["prediction"],
                "after_prediction": outcomes[1][identifier]["prediction"]}
               for identifier in sorted(outcomes[0])
               if outcomes[0][identifier]["prediction"] != outcomes[1][identifier]["prediction"]]
    metrics = [json.loads((path / "metrics.json").read_text(encoding="utf-8")) for path in runs]
    timing = runtime_report(model_first, private_questions, private_businesses)
    repeat_timing = runtime_report(model_repeat, private_questions, private_businesses)
    real_model = all(any(m.get("model_id") == MODEL_ID and m.get("revision") == MODEL_REVISION
                        and m.get("gpu") for m in report["models"])
                     and report["model_generations"] > 0 for report in (timing, repeat_timing))
    reproducible = (model_first / "submission.jsonl").read_bytes() == (model_repeat / "submission.jsonl").read_bytes()
    newly_wrong = [change for change in changes if change["after"] == "wrong" and change["before"] != "wrong"]
    validation_gain = metrics[1]["validation"]["pa"] - metrics[0]["validation"]["pa"]
    # Strict gate: every newly wrong answer is surfaced; Evaluation makes the final A/B choice.
    gates = {"real_model_generation_on_gpu": real_model, "reproducible_submission": reproducible,
             "no_new_wrong_answers": not newly_wrong, "validation_pa_increases": validation_gain > 0,
             "both_measured_runs_under_6_hours": timing["measured_within_6_hours"] and repeat_timing["measured_within_6_hours"]}
    if private_questions is not None:
        gates["private_projection_under_6_hours"] = (
            timing["private_projection"]["estimated_within_6_hours"]
            and repeat_timing["private_projection"]["estimated_within_6_hours"])
    return {"scorer": "local_proxy_not_official", "gates": gates,
            "ready_for_evaluation_review": all(gates.values()),
            "final_direction_requires_evaluation_and_integration": True,
            "baseline_metrics": metrics[0], "model_metrics": metrics[1],
            "validation_pa_delta": validation_gain, "newly_wrong": newly_wrong, "changes": changes,
            "runtime": timing, "repeat_runtime": repeat_timing}
