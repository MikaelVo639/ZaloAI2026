"""Hidden-rule check on a TRAIN split: remove one rulebook row at a time (LOCAL PROXY).

    uv run python scripts/hidden_rule_check.py --data-dir data/raw/train \
        --original outputs/runs/<run> --output-dir outputs/hidden-rules/<name> [--shard 0/4]

For every table row that some answer of ``--original`` cites as evidence, the row is
removed from the rulebook and only the questions citing it are answered again. A question
whose rule is hidden may abstain; it must never become wrong. Exit status 1 when any does.
Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

from datamate.evaluation.robustness import _link_tree
from datamate.evaluation.scorer import evaluate
from datamate.io import read_jsonl, write_jsonl
from datamate.pipeline import Config, predict


def cited_rows(run: Path) -> dict[str, set[str]]:
    """Table row -> ids of questions whose answer cites it."""
    rows: dict[str, set[str]] = defaultdict(set)
    for path in (run / "logs").glob("*.json"):
        log = json.loads(path.read_text(encoding="utf-8"))
        if log["final_answer"]["type"] == "abstain":
            continue
        evidence = log.get("evidence") or {}
        cited = list(evidence.get("rules") or ())
        spec = evidence.get("spec")
        if isinstance(spec, dict):
            cited.append(spec.get("nguon", ""))
        for line in cited:
            if isinstance(line, str) and line.strip().startswith("|"):
                rows[line.strip()].add(log["id"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="the TRAIN split")
    parser.add_argument("--original", type=Path, required=True, help="run directory with logs")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.toml"))
    parser.add_argument("--shard", default="0/1", help="i/n: run every n-th row from i")
    args = parser.parse_args()
    if not (args.data_dir / "answers.jsonl").is_file():
        parser.error("The hidden-rule check needs a labelled TRAIN split")
    index, count = map(int, args.shard.split("/"))
    references = read_jsonl(args.data_dir / "answers.jsonl")
    original = {o["id"]: o["outcome"] for o in evaluate(
        read_jsonl(args.original / "submission.jsonl"), references)["outcomes"]}
    questions = {q["id"]: q for q in read_jsonl(args.data_dir / "questions.jsonl")}
    rulebook = (args.data_dir / "rulebook.md").read_text(encoding="utf-8-sig")
    rows = sorted(cited_rows(args.original).items())[index::count]
    config = Config.load(args.config)
    report, failed = [], False
    for number, (row, ids) in enumerate(rows):
        lines = [line for line in rulebook.split("\n") if line.strip() != row]
        if len(lines) == len(rulebook.split("\n")):
            continue  # cited text is not a verbatim row of this rulebook
        split = args.output_dir / "splits" / f"{index}-{number}"
        if split.exists():
            shutil.rmtree(split)
        split.mkdir(parents=True)
        (split / "rulebook.md").write_text("\n".join(lines), encoding="utf-8")
        write_jsonl(split / "questions.jsonl", [questions[i] for i in sorted(ids)])
        shutil.copy2(args.data_dir / "calendar.csv", split / "calendar.csv")
        _link_tree(args.data_dir / "data", split / "data")
        run = args.output_dir / "runs" / f"{index}-{number}"
        if run.exists():
            shutil.rmtree(run)
        predict(split, run, config)
        scored = evaluate(read_jsonl(run / "submission.jsonl"),
                          [r for r in references if r["id"] in ids])
        moves = Counter(f"{original[o['id']]}->{o['outcome']}" for o in scored["outcomes"]
                        if original[o["id"]] != o["outcome"])
        new_wrong = sorted(o["id"] for o in scored["outcomes"]
                           if o["outcome"] == "wrong" and original[o["id"]] != "wrong")
        failed |= bool(new_wrong)
        report.append({"row": row, "questions": len(ids), "moves": dict(moves),
                       "new_wrong": new_wrong})
        print(f"[{index}] {number + 1}/{len(rows)} {len(ids)} q, new wrong {len(new_wrong)}: "
              f"{row[:70]}", file=sys.stderr, flush=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / f"report-{index}.json").write_text(json.dumps(
        {"scorer": "LOCAL PROXY, not official", "rows": report}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"rows": len(report), "rows_with_new_wrong":
                      sum(bool(r["new_wrong"]) for r in report)}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
