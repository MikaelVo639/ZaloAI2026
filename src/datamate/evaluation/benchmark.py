"""Offline experiment runner. Labels are opened only after predict exits."""

import csv
import hashlib
import json
import os
import platform
import random
import shutil
import signal
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from datamate.evaluation.scorer import evaluate
from datamate.evaluation.validation import validate_submission
from datamate.ingestion.catalog import build_catalog
from datamate.io import index_records, load_split, read_jsonl
from datamate.pipeline import Config
from datamate.semantics.text import fold


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_holdout(data, manifest):
    for key in ("train_ids", "validation_ids", "train_businesses", "validation_businesses"):
        if not isinstance(manifest.get(key), list) or not all(
            isinstance(item, str) for item in manifest[key]
        ):
            raise ValueError(f"Malformed holdout {key}")
    split = load_split(data)
    questions = {q.id: q.business_id for q in split.questions}
    if manifest.get("seed") != 42:
        raise ValueError("EVAL-01 requires seed 42")
    for key, expected in (
        ("questions_sha256", digest(data / "questions.jsonl")),
        ("rulebook_sha256", hashlib.sha256(split.rulebook.encode()).hexdigest()),
    ):
        if manifest.get(key) != expected:
            raise ValueError(f"Holdout {key} mismatch")
    partitions = []
    for prefix in ("train", "validation"):
        ids, businesses = manifest[prefix + "_ids"], manifest[prefix + "_businesses"]
        if len(ids) != len(set(ids)) or len(businesses) != len(set(businesses)):
            raise ValueError("Duplicate holdout IDs/businesses")
        if set(ids) - questions.keys():
            raise ValueError("Unknown holdout IDs")
        if {questions[q] for q in ids} != set(businesses):
            raise ValueError("Holdout business mapping mismatch")
        partitions.append((set(ids), set(businesses)))
    a, b = partitions
    if a[0] & b[0] or a[1] & b[1] or a[0] | b[0] != questions.keys():
        raise ValueError("Holdout overlaps or does not partition questions")
    expected_size = max(1, min(len(a[1] | b[1]) - 1, round(len(a[1] | b[1]) * 0.2)))
    if len(b[1]) != expected_size:
        raise ValueError("Holdout validation fraction must be 0.2")
    shuffled = sorted(a[1] | b[1])
    random.Random(42).shuffle(shuffled)
    if set(shuffled[:expected_size]) != b[1]:
        raise ValueError("Holdout businesses do not match seed 42")
    return split


def reason_group(log):
    reason = log.get("reason", "")
    for token, group in (
        ("outside baseline grammar", "grammar_rules"),
        ("No supported flat sales table", "missing_adapter"),
        ("Unknown status", "unknown_status"),
        ("Ranking needs", "ranking_inputs"),
        ("coverage is not established", "coverage"),
        ("Empty data", "empty_period"),
        ("time budget", "run_budget"),
        ("timed out", "timeout"),
        ("worker failed", "execution_error"),
        ("Business ingestion failed", "ingestion_error"),
    ):
        if token in reason:
            return group
    return "other" if reason else "none"


def error_info(log):
    evidence = log.get("evidence") or {}
    details = {}
    if log.get("error"):
        details["pipeline"] = log["error"]
    for field in ("execution", "ingestion_execution"):
        item = evidence.get(field)
        if isinstance(item, dict) and (
            item.get("status") in ("timeout", "error") or item.get("error")
        ):
            details[field] = item
    return details


def topic(log):
    intent = (log.get("evidence") or {}).get("intent")
    if intent:
        return intent
    text = fold(log.get("question", ""))
    for words, group in (
        (("so voi", "cung ky"), "comparison"),
        (("doanh thu", "tien hang", "tien ban"), "revenue"),
        (("don", "bill"), "orders"),
        (("san pham", "mat hang"), "products"),
        (("ton kho",), "inventory"),
        (("cong no", "no tien"), "debt"),
    ):
        if any(word in text for word in words):
            return "diagnostic_" + group
    return "diagnostic_other"


def run_process(command, cwd, timeout, stdout, stderr):
    options = {"start_new_session": True} if os.name != "nt" else {}
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    with stdout.open("wb") as out, stderr.open("wb") as err:
        try:
            process = subprocess.Popen(
                command, cwd=cwd, stdout=out, stderr=err, env=environment, **options
            )
        except OSError as exc:
            err.write(str(exc).encode("utf-8"))
            return 127, False
        try:
            return process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return process.returncode, True


def source_metadata(checkout):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()

    return {
        "revision": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "diff_sha256": hashlib.sha256(git("diff", "HEAD").encode()).hexdigest(),
        "lock_sha256": digest(checkout / "uv.lock"),
    }


def environment_metadata():
    result = {
        "os": platform.platform(),
        "cpu": platform.processor(),
        "logical_cpus": os.cpu_count(),
    }
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "available",
                    "page_total",
                    "page_available",
                    "virtual_total",
                    "virtual_available",
                    "extended",
                )
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        result["ram_bytes"] = (
            status.total
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            else None
        )
    else:
        result["ram_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return result


def input_tree(data):
    return [
        {"path": p.relative_to(data).as_posix(), "sha256": digest(p)}
        for p in sorted(data.rglob("*"))
        if p.is_file() and p.name != "answers.jsonl"
    ]


def schema_catalog(data, businesses):
    result = {}
    for business in sorted(businesses):
        try:
            catalog = build_catalog(data / "data", business, sample_size=0)
            tables = [
                {
                    "source": t.path.relative_to(data).as_posix(),
                    "table": t.name,
                    "format": t.path.suffix,
                    "columns": list(t.columns),
                }
                for t in catalog.tables
            ]
            signature = [(t["format"], t["columns"]) for t in tables]
            result[business] = {
                "fingerprint": hashlib.sha256(
                    json.dumps(signature, ensure_ascii=False).encode()
                ).hexdigest(),
                "tables": tables,
                "readme": f"data/{business}/README.md",
            }
        except (ValueError, OSError) as exc:
            result[business] = {"fingerprint": "catalog_error", "error": str(exc)}
    return result


def csv_rows(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compare_runs(current, previous, identity):
    old = json.loads((previous / "experiment.json").read_text(encoding="utf-8"))
    if old.get("status") != "complete" or any(old.get(k) != identity[k] for k in identity):
        raise ValueError("Comparison requires complete runs with identical inputs/holdout/config")
    before = index_records(read_jsonl(previous / "outcomes.jsonl"))
    after = index_records(current)
    if before.keys() != after.keys():
        raise ValueError("Comparison question IDs differ")
    changes = []
    for identifier in sorted(after):
        a, b = before[identifier], after[identifier]
        if a["prediction"] != b["prediction"] or a["outcome"] != b["outcome"]:
            changes.append(
                {
                    "id": identifier,
                    "transition": a["outcome"] + " -> " + b["outcome"],
                    "before": a,
                    "after": b,
                }
            )
    return {
        "previous_revision": old["revision"],
        "changes": changes,
        "transitions": dict(Counter(c["transition"] for c in changes)),
        "identical_answers": not changes,
    }


def analyze(output, data, manifest, split):
    references = read_jsonl(data / "answers.jsonl")
    ref_index = index_records(references)
    questions = {q.id: q for q in split.questions}
    if ref_index.keys() != questions.keys():
        raise ValueError("Reference/question IDs differ")
    predictions = read_jsonl(output / "submission.jsonl")
    validate_submission(predictions, set(questions))
    logs = {}
    for path in (output / "logs").glob("*.json"):
        log = json.loads(path.read_text(encoding="utf-8"))
        identifier = log["id"]
        if identifier in logs or identifier not in questions:
            raise ValueError("Duplicate/unknown log ID")
        logs[identifier] = log
    if logs.keys() != questions.keys():
        raise ValueError("Missing question logs")
    for prediction in predictions:
        if logs[prediction["id"]]["final_answer"] != prediction:
            raise ValueError("Log/submission answer mismatch")
    mapping = {q.id: q.business_id for q in split.questions}
    full = evaluate(predictions, references, mapping)
    selected = set(manifest["validation_ids"])
    validation = evaluate(
        [p for p in predictions if p["id"] in selected],
        [r for r in references if r["id"] in selected],
        mapping,
    )
    schemas = schema_catalog(data, set(mapping.values()))
    rows, groups = [], defaultdict(list)
    for outcome in full["outcomes"]:
        log = logs[outcome["id"]]
        question = questions[outcome["id"]]
        errors = error_info(log)
        group = reason_group(log)
        owner = (
            "Semantics"
            if group == "grammar_rules"
            else "Data"
            if group in ("missing_adapter", "unknown_status", "ingestion_error", "ranking_inputs")
            else "Agent"
        )
        row = {
            **outcome,
            "business_id": question.business_id,
            "question": question.question,
            "as_of_date": question.as_of_date.isoformat(),
            "reason": log.get("reason", ""),
            "group": group,
            "topic": topic(log),
            "errors": errors,
            "schema": schemas[question.business_id]["fingerprint"],
            "source": schemas[question.business_id],
            "evidence": log.get("evidence", {}),
            "worker_metadata_available": "execution" in (log.get("evidence") or {})
            or "ingestion_execution" in (log.get("evidence") or {}),
        }
        rows.append(row)
        if outcome["outcome"] != "correct":
            groups[(owner, group)].append(row)
    with (output / "outcomes.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    metrics = {
        "train": {k: v for k, v in full.items() if k != "outcomes"},
        "validation": {k: v for k, v in validation.items() if k != "outcomes"},
        "reasons": dict(Counter(row["reason"] for row in rows)),
        "execution_error_questions": sum(bool(row["errors"]) for row in rows),
        "worker_metadata_missing_questions": sum(
            not row["worker_metadata_available"] for row in rows
        ),
    }
    for field in ("group", "topic", "schema"):
        counts = Counter((row[field], row["outcome"]) for row in rows)
        csv_rows(
            output / (field + "_counts.csv"),
            [{"group": k[0], "outcome": k[1], "count": v} for k, v in sorted(counts.items())],
            ["group", "outcome", "count"],
        )
    csv_rows(
        output / "reason_counts.csv",
        [{"reason": k, "count": v} for k, v in sorted(metrics["reasons"].items())],
        ["reason", "count"],
    )
    handoff = {}
    for owner in ("Data", "Semantics", "Agent"):
        ranked = sorted(
            ((g, cases) for (o, g), cases in groups.items() if o == owner),
            key=lambda item: (-len(item[1]), item[0]),
        )[:3]
        handoff[owner] = [
            {
                "group": g,
                "affected_questions": len(cases),
                "cases": sorted(cases, key=lambda r: r["id"])[:3],
            }
            for g, cases in ranked
        ]
    save(output / "handoff.json", handoff)
    save(output / "schemas.json", schemas)
    save(output / "metrics.json", metrics)
    return rows, metrics


def benchmark(checkout, data_dir, holdout, config, output_dir, compare_to=None):
    started = time.perf_counter()
    checkout, data, holdout, config, output = map(
        Path.resolve, map(Path, (checkout, data_dir, holdout, config, output_dir))
    )
    if output.is_relative_to(data) or data.is_relative_to(output):
        raise ValueError("Output and input must be separate trees")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing a nonempty output directory")
    manifest = json.loads(holdout.read_text(encoding="utf-8"))
    split = validate_holdout(data, manifest)
    settings = Config.load(config)
    python = checkout / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise ValueError("Prepare target checkout with uv sync --locked first")
    tree = input_tree(data)
    identity = {
        "input_sha256": hashlib.sha256(json.dumps(tree).encode()).hexdigest(),
        "holdout_sha256": digest(holdout),
        "config_sha256": digest(config),
    }
    metadata = {
        **source_metadata(checkout),
        **identity,
        "status": "running",
        "config": vars(settings),
        "validation_fraction": 0.2,
        "environment": environment_metadata(),
        "runner_source_sha256": digest(Path(__file__)),
        "data_exposure": "Full train inspected; not blind; local proxy, not official.",
    }
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(holdout, output / "business_split.json")
    shutil.copyfile(config, output / "config.toml")
    save(output / "input_tree.json", tree)
    save(output / "experiment.json", metadata)
    prediction_started = time.perf_counter()
    code, timed_out = run_process(
        [
            str(python),
            "-m",
            "datamate",
            "predict",
            "--data-dir",
            str(data),
            "--config",
            str(config),
            "--output-dir",
            str(output),
        ],
        checkout,
        settings.max_seconds,
        output / "predict.stdout.txt",
        output / "predict.stderr.txt",
    )
    metadata.update(
        predict_wall_seconds=time.perf_counter() - prediction_started,
        returncode=code,
        timed_out=timed_out,
    )
    try:
        if code or timed_out:
            raise ValueError("Predict failed or exceeded wall-clock budget")
        if input_tree(data) != tree:
            raise ValueError("Input changed during prediction")
        rows, metrics = analyze(output, data, manifest, split)
        metadata["reference_sha256"] = digest(data / "answers.jsonl")
        metadata["target_run"] = json.loads((output / "run.json").read_text(encoding="utf-8"))
        if compare_to:
            comparison = compare_runs(
                rows,
                Path(compare_to).resolve(),
                {**identity, "reference_sha256": metadata["reference_sha256"]},
            )
            save(output / "comparison.json", comparison)
        metadata["status"] = "complete"
        lines = [
            "# EVAL-01 — local proxy (not official)",
            "",
            metadata["data_exposure"],
            "",
            f"Revision: {metadata['revision']} (dirty={metadata['dirty']})",
            "",
            "| Split | N | Correct | Wrong | Abstain | PA | Coverage |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for name in ("train", "validation"):
            m = metrics[name]
            lines.append(
                f"| {name} | {m['n']} | {m['correct']} | {m['wrong']} | {m['abstain']} | {m['pa']:.6f} | {m['coverage']:.6f} |"
            )
        lines += [
            "",
            f"Predict wall seconds: {metadata['predict_wall_seconds']:.3f}",
            f"Questions with execution errors: {metrics['execution_error_questions']}",
            f"Questions without worker metadata: {metrics['worker_metadata_missing_questions']}",
        ]
        (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (ValueError, OSError, KeyError) as exc:
        metadata.update(status="incomplete", failure=str(exc))
    metadata["total_wall_seconds"] = time.perf_counter() - started
    save(output / "experiment.json", metadata)
    archive = output / "artifact.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file() and path != archive:
                bundle.write(path, path.relative_to(output).as_posix())
    (output / "artifact.zip.sha256").write_text(
        digest(archive) + "  artifact.zip\n", encoding="ascii"
    )
    if metadata["status"] != "complete":
        raise ValueError(metadata["failure"])
    return {
        "status": metadata["status"],
        "revision": metadata["revision"],
        "metrics": {
            name: {k: metrics[name][k] for k in ("n", "correct", "wrong", "abstain", "pa")}
            for name in ("train", "validation")
        },
    }
