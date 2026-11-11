"""Check the rewrite examples against a TRAIN rulebook (local data, not run in CI).

    uv run python scripts/check_rewrite_examples.py --split data/raw/train

Every standard wording must be planned by the grammar under that split's rulebook, and
every everyday wording must pass the rewrite safety checks against its standard wording.
Exit status 1 lists the examples that fail. Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from datamate.agent.rewrite import EXAMPLES, check_rewrite
from datamate.contracts import Question
from datamate.semantics.lunar_periods import plan_tet
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

# Invented catalogue entries used by the examples.
NAMES = ("Bánh quy bơ", "Kẹo gừng hộp 10 gói")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", type=Path, required=True, help="a TRAIN split directory")
    args = parser.parse_args()
    rulebook = (args.split / "rulebook.md").read_text(encoding="utf-8-sig")
    rules = read_rules(rulebook)
    failures = 0
    for raw, standard in EXAMPLES:
        question = Question("example", "example", standard, date(2026, 3, 15))
        plan = (plan_question(question, rules)
                or plan_tet(question, rules, rulebook, args.split / "calendar.csv")
                or plan_question(question, rules, NAMES))
        rejected = check_rewrite(raw, standard, NAMES)
        if plan is None or rejected:
            failures += 1
            print(f"FAIL {standard!r}: {'no plan' if plan is None else rejected}")
        else:
            print(f"ok   {plan.intent:<22} {standard}")
    print(f"{len(EXAMPLES) - failures}/{len(EXAMPLES)} examples pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
