"""Build TRAIN robustness splits, predict them and score with train labels (LOCAL PROXY).

    uv run python scripts/robustness_eval.py --data-dir data/raw/train \
        --output-dir outputs/robustness/<run> [--config configs/baseline.toml] [--only particle]

Reports, per perturbation, correct / wrong / abstain on the rewritten questions and, with
``--original`` (a scored train submission of the same revision), how many rewritten
questions lost an answer or changed a correct answer.
"""

import argparse
import json
import sys
from pathlib import Path

from datamate.evaluation.robustness import PERTURBATIONS, build_splits
from datamate.evaluation.scorer import evaluate
from datamate.io import read_jsonl
from datamate.pipeline import Config, predict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="the TRAIN split")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.toml"))
    parser.add_argument("--only", choices=PERTURBATIONS, action="append")
    parser.add_argument("--original", type=Path,
                        help="submission.jsonl of the unchanged train questions")
    args = parser.parse_args()
    if not (args.data_dir / "answers.jsonl").is_file():
        parser.error("Robustness splits are built from a labelled TRAIN split only")
    names = tuple(args.only or PERTURBATIONS)
    splits = args.output_dir / "splits"
    build_splits(args.data_dir, splits, names)
    config = Config.load(args.config)
    original = None
    if args.original:
        original = {r["id"]: r for r in read_jsonl(args.original)}
    report = {}
    for name in names:
        run = args.output_dir / "runs" / name
        predict(splits / name, run, config,
                progress=lambda message: print(f"[{name}] {message}", file=sys.stderr, flush=True))
        predictions = read_jsonl(run / "submission.jsonl")
        references = read_jsonl(splits / f"{name}.answers.jsonl")
        scored = evaluate(predictions, references)
        row = {key: scored[key] for key in ("n", "correct", "wrong", "abstain")}
        if original is not None:
            before = evaluate([original[r["id"]] for r in references if r["id"] in original],
                              references)
            row["original_correct"] = before["correct"]
            row["original_wrong"] = before["wrong"]
            outcome = {o["id"]: o["outcome"] for o in before["outcomes"]}
            row["lost"] = sum(1 for o in scored["outcomes"]
                              if outcome.get(o["id"]) == "correct" and o["outcome"] != "correct")
            row["new_wrong_ids"] = sorted(o["id"] for o in scored["outcomes"]
                                          if o["outcome"] == "wrong"
                                          and outcome.get(o["id"]) != "wrong")
        report[name] = row
    (args.output_dir / "report.json").write_text(
        json.dumps({"scorer": "LOCAL PROXY, not official", "config": str(args.config),
                    "perturbations": report}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(row["wrong"] for row in report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
