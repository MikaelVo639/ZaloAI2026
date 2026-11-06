"""Label-free checks for arbitrary submissions and independently repeated images."""

import json
import math
import subprocess
import time
from pathlib import Path

from datamate.evaluation.benchmark import digest, error_info, input_tree, save
from datamate.evaluation.validation import validate_submission
from datamate.io import index_records, load_split, read_jsonl


def read_run(run, expected_ids):
    predictions = read_jsonl(run / "submission.jsonl")
    validate_submission(predictions, expected_ids)
    indexed = index_records(predictions)
    logs = {}
    for path in (run / "logs").glob("*.json"):
        log = json.loads(path.read_text(encoding="utf-8"))
        key = log.get("id")
        if key in logs or key not in indexed:
            raise ValueError("Duplicate/unknown log ID")
        elapsed = log.get("elapsed_seconds")
        if (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError("Missing/invalid per-question elapsed time")
        if log.get("final_answer") != indexed[key]:
            raise ValueError("Log/submission answer mismatch")
        if error_info(log) and indexed[key]["type"] != "abstain":
            raise ValueError("Execution error must result in abstain")
        logs[key] = log
    if set(logs) != expected_ids:
        raise ValueError("Missing question logs")
    return indexed, logs


def verify_runs(data_dir, run_dirs, output_dir, max_seconds=21600):
    data, output = Path(data_dir).resolve(), Path(output_dir).resolve()
    runs = [Path(p).resolve() for p in run_dirs]
    if len(runs) < 2 or len(set(runs)) != len(runs):
        raise ValueError("Provide at least two distinct independent run directories")
    if not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if output.exists() or output.is_relative_to(data) or any(output.is_relative_to(p) for p in runs):
        raise ValueError("Verification requires a new output directory outside input/run trees")
    split = load_split(data)
    ids = {q.id for q in split.questions}
    checksums = {"questions_sha256": digest(data / "questions.jsonl"),
                 "calendar_sha256": digest(split.calendar_path)}
    import hashlib
    checksums["rulebook_sha256"] = hashlib.sha256(split.rulebook.encode()).hexdigest()
    previous = None
    previous_config = None
    summaries = []
    for run in runs:
        predictions, logs = read_run(run, ids)
        metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
        if not isinstance(metadata.get("config"), dict):
            raise ValueError("Run is missing its configuration")
        if any(metadata.get(k) != v for k, v in checksums.items()):
            raise ValueError("Run input checksum mismatch")
        elapsed = metadata.get("elapsed_seconds")
        if (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or not 0 <= elapsed <= max_seconds):
            raise ValueError("Invalid runtime or run exceeded wall-clock budget")
        if previous is not None and (predictions != previous or metadata.get("config") != previous_config):
            raise ValueError("Repeated answers or configurations differ")
        previous, previous_config = predictions, metadata.get("config")
        abstain = sum(p["type"] == "abstain" for p in predictions.values())
        summaries.append({"run": run.name, "n": len(ids), "abstain": abstain,
                          "abstain_rate": abstain / len(ids) if ids else 0,
                          "execution_error_questions": sum(bool(error_info(log)) for log in logs.values()),
                          "pipeline_elapsed_seconds": elapsed,
                          "submission_sha256": digest(run / "submission.jsonl")})
    result = {"status": "passed", "identical_answers_by_id": True, "runs": summaries,
              "includes_model_loading_confirmed": False,
              "reference_hardware_confirmed": False,
              "note": "Pipeline metadata alone does not prove whole-container runtime or GPU compliance."}
    output.mkdir(parents=True)
    save(output / "verification.json", result)
    return result


def verify_image(image, data_dir, output_dir, max_seconds=21600, gpus=False):
    data, output = Path(data_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or output.is_relative_to(data) or data.is_relative_to(output):
        raise ValueError("Image verification requires a new output tree separate from input")
    if not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    # Resolve once; both executions use exactly the same immutable image ID.
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], encoding="utf-8").strip()
    if not image_id.startswith("sha256:"):
        raise ValueError("Could not resolve immutable Docker image ID")
    before = input_tree(data)
    output.mkdir(parents=True)
    result = {"status": "running", "image_id": image_id, "gpus_requested": gpus,
              "network": "none", "data_read_only": True, "runs": [],
              "reference_hardware_confirmed": False}
    save(output / "image-verification.json", result)
    try:
        for name in ("first", "second"):
            run = output / name
            run.mkdir()
            cidfile = output / (name + ".cid")
            command = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
                       "--shm-size=16g", "--entrypoint", "bash",
                       "--cidfile", str(cidfile), "--mount", f"type=bind,source={data},target=/data,readonly",
                       "--mount", f"type=bind,source={run},target=/result"]
            if gpus:
                command += ["--gpus", "all"]
            command += [image_id, "/code/predict.sh"]
            started = time.monotonic()
            try:
                with (output / (name + ".stdout")).open("wb") as stdout, (output / (name + ".stderr")).open("wb") as stderr:
                    completed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=max_seconds)
                if completed.returncode:
                    raise ValueError(f"Docker run {name} failed: exit {completed.returncode}")
            finally:
                if cidfile.exists():
                    cid = cidfile.read_text().strip()
                    # Cleanup only the container created by this invocation.
                    subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elapsed = time.monotonic() - started
            if elapsed > max_seconds:
                raise ValueError("Whole-container runtime exceeded budget")
            result["runs"].append({"run": name, "wall_seconds_including_load": elapsed})
        if input_tree(data) != before:
            raise ValueError("Input data changed during image verification")
        result["verification"] = verify_runs(data, [output / "first", output / "second"], output / "checks", max_seconds)
        result.update(status="passed", includes_model_loading_confirmed=True)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result.update(status="failed", failure=str(exc))
        save(output / "image-verification.json", result)
        raise ValueError(str(exc)) from exc
    save(output / "image-verification.json", result)
    return result
