"""Build and score a TRAIN-only benchmark for the model rewrite fallback (LOCAL PROXY)."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import shutil
import sys
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path

from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Question, Split
from datamate.evaluation.robustness import (
    MODEL_PERTURBATIONS, REPORT_BENCHMARK_TYPES, _link_tree, perturb,
)
from datamate.evaluation.scorer import evaluate
from datamate.evaluation.validation import validate_submission
from datamate.ingestion import build_catalog
from datamate.io import index_records, load_split, read_jsonl, write_jsonl
from datamate.pipeline import Config, predict
from datamate.semantics.text import fold


def _write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _spread(buckets: dict[tuple[str, str], list[Question]]):
    """Interleave intents, then businesses within each intent."""
    businesses = defaultdict(deque)
    for (intent, _business), questions in sorted(buckets.items()):
        businesses[intent].append(deque(
            sorted(questions, key=lambda q: hashlib.sha256(q.id.encode()).digest())
        ))
    intents = deque(businesses.items())
    while intents:
        intent, shops = intents.popleft()
        questions = shops.popleft()
        yield intent, questions.popleft()
        if questions:
            shops.append(questions)
        if shops:
            intents.append((intent, shops))


def _choose(candidates: dict[str, dict[tuple[str, str], list[Question]]],
            limit: int) -> list[tuple[str, str, Question]]:
    """Interleave perturbations and keep at most one variant per original question."""
    queues = {kind: deque(_spread(buckets)) for kind, buckets in candidates.items()}
    chosen = []
    used = set()
    while len(chosen) < limit and any(queues.values()):
        for kind, queue in queues.items():
            if len(chosen) == limit:
                break
            while queue and queue[0][1].id in used:
                queue.popleft()
            if queue:
                intent, question = queue.popleft()
                used.add(question.id)
                chosen.append((kind, intent, question))
    return chosen


def _scan_business(split: Split, kinds: tuple[str, ...], value_ids: frozenset[str],
                   group: tuple[str, list[Question]]) -> tuple:
    business, questions = group
    counts = {kind: Counter() for kind in kinds}
    lost: list[tuple[str, str, Question]] = []
    agent = BaselineAgent()
    try:
        context = BusinessContext(split, build_catalog(split.root / "data", business))
        for question in questions:
            plan = agent._plan(question, context)
            if plan is None:
                continue
            for kind in kinds:
                if kind == "yes_no_wording" and question.id not in value_ids:
                    continue
                changed = perturb(kind, question.question, f"{kind}:{question.id}")
                if changed is None:
                    continue
                before, after = f" {fold(question.question)} ", f" {fold(changed)} "
                if any(f" {fold(f.value)} " in before and f" {fold(f.value)} " not in after
                       for f in plan.filters):
                    continue
                variant = replace(question, question=changed)
                counts[kind]["applicable"] += 1
                if agent._plan(variant, context) is None:
                    counts[kind]["lost"] += 1
                    lost.append((kind, plan.intent, variant))
    finally:
        agent.close()
    print(f"{business} benchmark scan complete", file=sys.stderr, flush=True)
    return counts, lost


def _scan(split: Split, by_business: dict[str, list[Question]], workers: int,
          kinds: tuple[str, ...], value_ids: frozenset[str]):
    scan = partial(_scan_business, split, kinds, value_ids)
    groups = sorted(by_business.items())
    if workers == 1:
        yield from map(scan, groups)
        return
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        yield from pool.map(scan, groups)


def build(data_dir: Path, original: Path, output: Path, limit: int = 480, *,
          workers: int = 1, report_suite: bool = False) -> dict:
    if limit <= 0:
        raise ValueError("Benchmark limit must be positive")
    if workers <= 0:
        raise ValueError("Benchmark workers must be positive")
    split = load_split(data_dir)
    if output.resolve().is_relative_to(split.root):
        raise ValueError("Benchmark output must be outside the source split")
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}")
    label_file = split.root / "answers.jsonl"
    if not label_file.is_file():
        raise ValueError("Benchmark requires a labelled TRAIN split")
    refs = index_records(read_jsonl(label_file))
    questions = index_records(read_jsonl(split.root / "questions.jsonl"))
    predictions = read_jsonl(original)
    if refs.keys() != questions.keys():
        raise ValueError("Training questions and references differ")
    validate_submission(predictions, set(refs), require_complete=True)
    excluded_types = {"unanswerable", "boolean"} if report_suite else {"unanswerable"}
    eligible = {row["id"] for row in evaluate(predictions, list(refs.values()))["outcomes"]
                if row["outcome"] == "correct" and row["reference"]["type"] not in excluded_types}
    by_business = defaultdict(list)
    for question in split.questions:
        if question.id in eligible:
            by_business[question.business_id].append(question)

    kinds = REPORT_BENCHMARK_TYPES if report_suite else MODEL_PERTURBATIONS
    buckets = {kind: defaultdict(list) for kind in kinds}
    counts = {kind: Counter() for kind in kinds}
    value_ids = frozenset(key for key, row in refs.items() if row["type"] in {"number", "string"})
    for report, lost in _scan(split, by_business, workers, kinds, value_ids):
        for kind, tally in report.items():
            counts[kind].update(tally)
        for kind, intent, question in lost:
            buckets[kind][(intent, question.business_id)].append(question)
    if report_suite:
        # Different phrasings of the same original can be compared across the groups;
        # assign a distinct evaluation ID without using it to infer anything.
        chosen = []
        for kind in kinds:
            applicable = counts[kind]["applicable"]
            if not applicable or 100 * counts[kind]["lost"] < 95 * applicable:
                raise ValueError(f"{kind}: fewer than 95% lost their original plan: {dict(counts[kind])}; "
                                 f"all groups: {counts}")
            chosen.extend((kind, intent, q) for intent, q in list(_spread(buckets[kind]))[:limit])
        print({kind: dict(count) for kind, count in counts.items()}, file=sys.stderr)
    else:
        chosen = _choose(buckets, limit)
    if not chosen:
        raise ValueError("No correct training questions lost their plan under these variants")
    selected_split = output / "split"
    data = selected_split / "data"
    data.mkdir(parents=True)
    for file in ("rulebook.md", "calendar.csv"):
        shutil.copy2(split.root / file, selected_split / file)
    for business in sorted({question.business_id for _, _, question in chosen}):
        _link_tree(split.root / "data" / business, data / business)
    picked = [(f"{kind}:{q.id}" if report_suite else q.id, kind, intent, q)
              for kind, intent, q in chosen]
    write_jsonl(selected_split / "questions.jsonl", (
        {**questions[q.id], "id": case_id, "question": q.question}
        for case_id, _, _, q in picked))
    write_jsonl(output / "answers.jsonl", (
        {**refs[q.id], "id": case_id} for case_id, _, _, q in picked))
    write_jsonl(output / "cases.jsonl", (
        {"id": case_id, "business_id": q.business_id, "variant": kind, "intent": intent,
         **({"train_id": q.id} if report_suite else {})}
        for case_id, kind, intent, q in picked))
    report = {
        "scorer": "LOCAL PROXY", "questions": len(chosen),
        "perturbations": {kind: {**counts[kind], "selected": sum(k == kind for k, _, _ in chosen)}
                          for kind in kinds},
        "by_intent": dict(Counter(intent for _, intent, _ in chosen)),
        "by_business": dict(Counter(q.business_id for _, _, q in chosen)),
    }
    _write_report(output / "build.json", report)
    return report


def code_sample(data_dir: Path, output: Path, limit: int = 160) -> dict:
    """TRAIN questions in their original wording, spread over answer types and businesses
    (tables excluded), to score the model calculation alone. Deterministic."""
    if limit <= 0:
        raise ValueError("Sample limit must be positive")
    split = load_split(data_dir)
    if output.resolve().is_relative_to(split.root):
        raise ValueError("Benchmark output must be outside the source split")
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}")
    refs = index_records(read_jsonl(split.root / "answers.jsonl"))
    questions = index_records(read_jsonl(split.root / "questions.jsonl"))
    buckets: dict[tuple[str, str], list[Question]] = defaultdict(list)
    for question in split.questions:
        kind = refs[question.id]["type"]
        if kind != "table":
            buckets[(kind, question.business_id)].append(question)
    chosen = [question for _, question in _spread(buckets)][:limit]
    selected_split = output / "split"
    data = selected_split / "data"
    data.mkdir(parents=True)
    for file in ("rulebook.md", "calendar.csv"):
        shutil.copy2(split.root / file, selected_split / file)
    for business in sorted({question.business_id for question in chosen}):
        _link_tree(split.root / "data" / business, data / business)
    write_jsonl(selected_split / "questions.jsonl", (questions[q.id] for q in chosen))
    write_jsonl(output / "answers.jsonl", (refs[q.id] for q in chosen))
    report = {"scorer": "LOCAL PROXY", "questions": len(chosen),
              "by_type": dict(Counter(refs[q.id]["type"] for q in chosen)),
              "by_business": dict(Counter(q.business_id for q in chosen))}
    _write_report(output / "build.json", report)
    return report


def score(bench: Path, config: Config, output: Path) -> dict:
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}")
    run = predict(bench / "split", output, config)
    predictions = read_jsonl(output / "submission.jsonl")
    # Keep labels outside the inference split and open them only after predict finishes.
    references = read_jsonl(bench / "answers.jsonl")
    validate_submission(predictions, set(index_records(references)), require_complete=True)
    metrics = evaluate(predictions, references)
    report = {k: metrics[k] for k in ("scorer", "n", "correct", "wrong", "abstain", "pa")}
    outcomes = {row["id"]: row["outcome"] for row in metrics["outcomes"]}
    model_rules = {row["nguon"] for audit in run["rule_spec_model"] for row in audit["rows"]
                   if row["decision"] == "on"}
    by_path: dict[str, Counter] = defaultdict(Counter)
    abstain_reasons: Counter[str] = Counter()
    code_fallback: Counter[str] = Counter()
    for path in (output / "logs").glob("*.json"):
        log = json.loads(path.read_text(encoding="utf-8"))
        evidence = log["evidence"]
        if "model_load_execution" in evidence or log["reason"].startswith("Rewrite model stopped"):
            route = "model_unavailable"
        elif evidence.get("path") == "code":
            route = "model_code"
        elif evidence.get("rewrites"):
            route = "model_rewrite"
        elif evidence.get("intent") == "rule_rank" and model_rules.intersection(
            evidence.get("rules", ())
        ):
            route = "model_rule"
        else:
            route = "deterministic" if config.backend in ("baseline", "rewrite") else config.backend
        outcome = outcomes[log["id"]]
        by_path[route][outcome] += 1
        if config.rewrite_code_fallback and not config.rewrite_code_only and (
            route == "model_code" or "code_attempt" in evidence
        ):
            code_fallback[outcome] += 1
        if outcome == "abstain":
            abstain_reasons[log["reason"] or log["error"] or "Unspecified"] += 1
    by_variant: dict[str, Counter] = defaultdict(Counter)
    if (bench / "cases.jsonl").is_file():
        for case in read_jsonl(bench / "cases.jsonl"):
            by_variant[case["variant"]][outcomes[case["id"]]] += 1
    report.update(label="LOCAL PROXY", backend=config.backend, elapsed_seconds=run["elapsed_seconds"],
                  wrong_ids=[identifier for identifier, outcome in outcomes.items() if outcome == "wrong"],
                  by_path=by_path, by_variant=by_variant, abstain_reasons=abstain_reasons,
                  code_fallback=code_fallback)
    _write_report(output / "score.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build", help="select correctly answered TRAIN paraphrases")
    builder.add_argument("--data-dir", type=Path, required=True)
    builder.add_argument("--output-dir", type=Path, required=True)
    builder.add_argument("--original", type=Path, default=Path("outputs/runs/train-ref/submission.jsonl"))
    builder.add_argument("--limit", type=int, default=480)
    builder.add_argument("--report-suite", action="store_true",
                         help="four report suites; --limit is the cap per suite")
    builder.add_argument("--workers", type=int, default=4, help="parallel business scans")
    sampler = commands.add_parser("code-sample",
                                  help="original TRAIN questions for the model calculation alone")
    sampler.add_argument("--data-dir", type=Path, required=True)
    sampler.add_argument("--output-dir", type=Path, required=True)
    sampler.add_argument("--limit", type=int, default=160)
    scorer = commands.add_parser("score", help="run inference, then score against TRAIN labels")
    scorer.add_argument("--bench", type=Path, required=True)
    scorer.add_argument("--config", type=Path, required=True)
    scorer.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        report = build(args.data_dir, args.original, args.output_dir, args.limit,
                       workers=args.workers, report_suite=args.report_suite)
    elif args.command == "code-sample":
        report = code_sample(args.data_dir, args.output_dir, args.limit)
    else:
        report = score(args.bench, Config.load(args.config), args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
