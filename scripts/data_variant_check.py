"""Answer a data-layout variant of TRAIN and compare with the original run (LOCAL PROXY).

    uv run python scripts/data_variant_check.py --variant monthly_files \
        --data-dir data/raw/train --original outputs/runs/<run> \
        --output-dir outputs/data-variants/<name>

The variant stores the same facts differently (data_variants.py), so a question may move
from abstain to correct, but must never become wrong. Exit status 1 when one does.
Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from datamate.evaluation.data_variants import VARIANTS, build_variant
from datamate.evaluation.scorer import evaluate
from datamate.io import read_jsonl
from datamate.pipeline import Config, predict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--data-dir", type=Path, required=True, help="the TRAIN split")
    parser.add_argument("--original", type=Path, required=True, help="run on the same split")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.toml"))
    args = parser.parse_args()
    built = build_variant(args.data_dir, args.output_dir, args.variant)
    print(json.dumps(built, ensure_ascii=False), file=sys.stderr)
    split = args.output_dir / args.variant
    references = read_jsonl(args.output_dir / f"{args.variant}.answers.jsonl")
    ids = {r["id"] for r in references}
    original = {o["id"]: o["outcome"] for o in evaluate(
        read_jsonl(args.original / "submission.jsonl"),
        read_jsonl(args.data_dir / "answers.jsonl"))["outcomes"] if o["id"] in ids}
    run = args.output_dir / f"{args.variant}-run"
    predict(split, run, Config.load(args.config))
    scored = evaluate(read_jsonl(run / "submission.jsonl"), references)
    outcomes = {o["id"]: o["outcome"] for o in scored["outcomes"]}
    moves = Counter(f"{original[i]}->{outcomes[i]}" for i in ids if original[i] != outcomes[i])
    new_wrong = sorted(i for i in ids if outcomes[i] == "wrong" and original[i] != "wrong")
    report = {"scorer": "LOCAL PROXY, not official", **built,
              "original": dict(Counter(original.values())),
              "variant": dict(Counter(outcomes.values())),
              "moves": dict(moves), "new_wrong": new_wrong}
    (args.output_dir / f"{args.variant}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "businesses"},
                     ensure_ascii=False))
    return 1 if new_wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
