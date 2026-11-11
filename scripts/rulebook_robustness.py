"""Rewrite a TRAIN rulebook and measure which rules and answers survive (LOCAL PROXY).

    uv run python scripts/rulebook_robustness.py --data-dir data/raw/train
    uv run python scripts/rulebook_robustness.py --data-dir data/raw/train \
        --predict outputs/rulebook-robustness/<run> --original <train submission.jsonl> \
        [--only fillers --only synonyms]

Without ``--predict`` it only reports, per perturbation, the rules the reader stops or starts
recognising. With ``--predict`` it also answers every train question under each rewritten
rulebook and compares the outcome of each question with ``--original`` (the same revision
on the unchanged rulebook). Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from datamate.evaluation.robustness import _link_tree
from datamate.evaluation.rulebook_robustness import PERTURBATIONS, compare, perturb_rulebook
from datamate.evaluation.scorer import evaluate
from datamate.io import read_jsonl
from datamate.pipeline import Config, predict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="the TRAIN split")
    parser.add_argument("--only", choices=PERTURBATIONS, action="append")
    parser.add_argument("--predict", type=Path, help="new output directory for answered splits")
    parser.add_argument("--original", type=Path,
                        help="submission.jsonl of the same revision on the unchanged rulebook")
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.toml"))
    args = parser.parse_args()
    if not (args.data_dir / "answers.jsonl").is_file():
        parser.error("Rulebook perturbations are built from a labelled TRAIN split only")
    names = tuple(args.only or PERTURBATIONS)
    rulebook = (args.data_dir / "rulebook.md").read_text(encoding="utf-8-sig")
    report = compare(rulebook, names)
    if args.predict:
        if args.predict.exists():
            parser.error(f"Refusing to overwrite {args.predict}")
        references = read_jsonl(args.data_dir / "answers.jsonl")
        before = None
        if args.original:
            scored = evaluate(read_jsonl(args.original), references)
            before = {o["id"]: o["outcome"] for o in scored["outcomes"]}
        config = Config.load(args.config)
        for name in names:
            split = args.predict / "splits" / name
            split.mkdir(parents=True)
            (split / "rulebook.md").write_text(perturb_rulebook(name, rulebook), encoding="utf-8")
            for file in ("questions.jsonl", "calendar.csv"):
                shutil.copy2(args.data_dir / file, split / file)
            _link_tree(args.data_dir / "data", split / "data")
            run = args.predict / "runs" / name
            predict(split, run, config, progress=lambda message: print(
                f"[{name}] {message}", file=sys.stderr, flush=True))
            scored = evaluate(read_jsonl(run / "submission.jsonl"), references)
            row = report[name]
            row.update({key: scored[key] for key in ("n", "correct", "wrong", "abstain")})
            if before is not None:
                moves = Counter(f"{before[o['id']]}->{o['outcome']}" for o in scored["outcomes"]
                                if before.get(o["id"]) != o["outcome"])
                row["changed"] = dict(moves)
                row["changed_ids"] = sorted(o["id"] for o in scored["outcomes"]
                                            if before.get(o["id"]) != o["outcome"])
        (args.predict / "report.json").write_text(json.dumps(
            {"scorer": "LOCAL PROXY, not official", "perturbations": report},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
