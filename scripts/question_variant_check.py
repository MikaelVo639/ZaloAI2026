"""Grammar check on TRAIN question variants: the variant must plan like the original.

    uv run python scripts/question_variant_check.py --data-dir data/raw/train \
        [--only store_word --only greeting] [--examples 5]

Each perturbation (evaluation/robustness.py) keeps a question's meaning, numbers, dates
and names. A variant whose plan equals the original plan gets the same answer without
running any calculation, so this is a fast proxy for the robustness splits. A variant
that plans *differently* may answer wrongly and makes the exit status 1. LOCAL PROXY.
Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Question
from datamate.evaluation.robustness import PERTURBATIONS, perturb
from datamate.ingestion.catalog import build_catalog
from datamate.io import load_split


def _key(plan):
    return None if plan is None else replace(plan, evidence=())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="the TRAIN split")
    parser.add_argument("--only", choices=PERTURBATIONS, action="append")
    parser.add_argument("--examples", type=int, default=0,
                        help="print up to N lost/changed questions per perturbation")
    parser.add_argument("--output", type=Path, help="write the report as JSON")
    args = parser.parse_args()
    if not (args.data_dir / "answers.jsonl").is_file():
        parser.error("Question variants need a labelled TRAIN split")
    names = tuple(args.only or PERTURBATIONS)
    split = load_split(args.data_dir)
    by_business: dict[str, list[Question]] = defaultdict(list)
    for question in split.questions:
        by_business[question.business_id].append(question)
    counts = {name: Counter() for name in names}
    examples: dict[str, list] = {name: [] for name in names}
    agent = BaselineAgent()
    try:
        for business, questions in by_business.items():
            context = BusinessContext(split, build_catalog(split.root / "data", business))
            for question in questions:
                original = _key(agent._plan(question, context))
                for name in names:
                    text = perturb(name, question.question, f"{name}:{question.id}")
                    if text is None:
                        continue
                    variant = _key(agent._plan(replace(question, question=text), context))
                    outcome = ("same" if variant == original else
                               "lost" if variant is None else
                               "gained" if original is None else "changed")
                    counts[name][outcome] += 1
                    if outcome in ("lost", "changed") and len(examples[name]) < args.examples:
                        examples[name].append((outcome, question.question, text))
            print(f"{business} done", file=sys.stderr, flush=True)
    finally:
        agent.close()
    report = {"scorer": "LOCAL PROXY, plan agreement on train",
              "perturbations": {name: dict(counts[name]) for name in names}}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for name in names:
        for outcome, before, after in examples[name]:
            print(f"[{name}] {outcome}: {before}\n    -> {after}")
    if args.output:
        args.output.write_text(json.dumps({**report, "examples": examples},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(c["changed"] for c in counts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
