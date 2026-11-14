"""Re-plan accepted restatements from a labelled TRAIN model benchmark, without a GPU.

    uv run python scripts/replay_train_rewrites.py --train data/raw/train \\
        --bench outputs/model-bench-report/train --logs <score-rewrite-v9-.../logs>

Run the same command before and after a grammar change. Only a plan equal to the
original train question's plan counts as recovered. No model or reference reads at inference.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext
from datamate.ingestion import build_catalog
from datamate.io import index_records, load_split, read_jsonl
from datamate.semantics.planner import unread_words


def _key(plan):
    return replace(plan, evidence=()) if plan is not None else None


def replay(train: Path, bench: Path, logs: Path) -> dict:
    originals = load_split(train)
    original_by_id = {q.id: q for q in originals.questions}
    sample = load_split(bench / "split")
    if not sample.questions or not (train / "answers.jsonl").is_file() or not (
            bench / "answers.jsonl").is_file():
        raise ValueError("Replay requires a labelled train split and a nonempty benchmark")
    train_answers = index_records(read_jsonl(train / "answers.jsonl"))
    bench_answers = index_records(read_jsonl(bench / "answers.jsonl"))
    originals_for_case = {}
    if (bench / "cases.jsonl").exists():
        originals_for_case = {row["id"]: row.get("train_id", row["id"])
                              for row in read_jsonl(bench / "cases.jsonl")}
    if ({q.id for q in sample.questions} != bench_answers.keys()
            or any(train_answers.get(originals_for_case.get(key, key)) !=
                   {**row, "id": originals_for_case.get(key, key)}
                   for key, row in bench_answers.items())):
        raise ValueError("Benchmark labels do not match train")
    if (sample.rulebook != originals.rulebook
            or sample.calendar_path.read_bytes() != originals.calendar_path.read_bytes()):
        raise ValueError("Benchmark rules and calendar do not match train")
    bench_by_id = {q.id: q for q in sample.questions}
    by_business = defaultdict(list)
    seen = set()
    for path in sorted(logs.glob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        identifier = entry.get("id")
        question = bench_by_id.get(identifier)
        original = original_by_id.get(originals_for_case.get(identifier, identifier))
        if (not question or not original or identifier in seen
                or question.business_id != original.business_id
                or question.as_of_date != original.as_of_date
                or entry.get("question") != question.question
                or entry.get("business_id") != question.business_id
                or entry.get("as_of_date") != question.as_of_date.isoformat()):
            raise ValueError("Log does not match the train benchmark questions")
        seen.add(identifier)
        attempts = entry["evidence"].get("rewrites", ())
        vote_sizes = Counter(a.get("vote") for a in attempts if "retry" in a)
        for attempt in attempts:
            retry = attempt.get("retry")
            # Unplanned first attempts log the actual leftover words (even if None).
            # An unsuccessful final retry has no such field, but the vote logs why
            # it retried; never mix guard rejections into the grammar sample.
            marked = attempt.get("outcome") == "khong_doc" or "unread_words" in attempt
            final_unplanned = (entry.get("reason", "").startswith(
                "Grammar cannot plan the rewrite") and (retry is None or retry == 1)
                and (retry is None or vote_sizes[attempt["vote"]] == 2))
            if (marked or ("outcome" not in attempt and final_unplanned)) and attempt.get("rewrite") and not attempt.get("rejected"):
                by_business[original.business_id].append((original, attempt))
    if seen != bench_by_id.keys():
        raise ValueError("Logs must cover the complete benchmark (or its recorded subset)")

    counts = Counter()
    leftovers = Counter()
    recovered, different = set(), set()
    agent = BaselineAgent()
    try:
        for business, attempts in by_business.items():
            catalog = build_catalog(originals.root / "data", business)
            context = BusinessContext(originals, catalog)
            expected = {}
            for original, attempt in attempts:
                if original.id not in expected:
                    expected[original.id] = _key(agent._plan(original, context))
                if expected[original.id] is None:
                    counts["original_unplanned"] += 1
                    continue
                restatement = replace(original, question=attempt["rewrite"])
                actual = _key(agent._plan(restatement, context))
                if actual is None:
                    counts["unplanned"] += 1
                    words = unread_words(restatement, context.cache["rules"],
                                         agent._product_names(context), agent._vocabulary(context))
                    leftovers[words or "(no leftover words reported)"] += 1
                elif actual == expected[original.id]:
                    counts["same_plan"] += 1
                    recovered.add(original.id)
                else:
                    counts["different_plan"] += 1
                    different.add(original.id)
    finally:
        agent.close()
    return {"scorer": "LOCAL PROXY, train plan agreement", "logs": len(seen),
            "attempts": dict(counts), "same_plan_questions": len(recovered),
            "different_plan_questions": len(different), "unread_words": leftovers.most_common(30)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("train", "bench", "logs"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.train, args.bench, args.logs)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["different_plan_questions"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
