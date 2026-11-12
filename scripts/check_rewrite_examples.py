"""Check the rewrite examples against a TRAIN rulebook (local data, not run in CI).

    uv run python scripts/check_rewrite_examples.py --split data/raw/train

Every standard wording must be planned by the grammar under that split's rulebook, and
every everyday wording must pass the rewrite safety checks against its standard wording.
Exit status 1 lists the examples that fail. Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from datamate.agent.rewrite import EXAMPLES, check_rewrite, spell_months
from datamate.contracts import Plan, Question
from datamate.semantics.lunar_periods import plan_tet
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

# Invented catalogue entries and group labels used by the examples.
NAMES = ("Bánh quy bơ", "Kẹo gừng hộp 10 gói", "Trà mây tía")
VOCABULARY = {
    "product_group": ("Đồ ăn vặt",),
    "customer_group": ("Đại lý",),
    "payment_terms": ("tien mat|cash", "chuyen khoan|transfer", "cod|cod"),
    "channel_terms": ("tai quay|store", "dien thoai|phone"),
}


def example_plan(standard: str, rulebook: str, calendar: Path) -> Plan | None:
    rules = read_rules(rulebook)
    question = Question("example", "example", standard, date(2026, 3, 15))
    return (plan_question(question, rules)
            or plan_tet(question, rules, rulebook, calendar)
            or plan_question(question, rules, NAMES, VOCABULARY))


def feature_keys(plan: Plan) -> tuple[str, ...]:
    """Plan features to count separately from the overall intent."""
    keys = {f"filter/{f.field}" for f in plan.filters}
    if plan.comparison:
        keys.add(f"comparison/{plan.comparison}")
    if plan.shape:
        keys.add(f"shape/{plan.shape}")
    if plan.threshold:
        keys.add(f"threshold/{plan.threshold.subject}/{plan.threshold.op}")
    if plan.rank_by:
        keys.add(f"rank_by/{plan.rank_by}")
    if plan.top_k > 1:
        keys.add("top_k/multiple")
    return tuple(sorted(f"{plan.intent}:{key}" for key in keys))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", type=Path, required=True, help="a TRAIN split directory")
    parser.add_argument("--report", type=Path, help="write intent and feature counts as JSON")
    args = parser.parse_args()
    rulebook = (args.split / "rulebook.md").read_text(encoding="utf-8-sig")
    names = (*NAMES, *VOCABULARY["product_group"], *VOCABULARY["customer_group"])
    failures = 0
    by_intent: Counter[str] = Counter()
    by_feature: Counter[str] = Counter()
    for raw, standard in EXAMPLES:
        plan = example_plan(standard, rulebook, args.split / "calendar.csv")
        rejected = check_rewrite(spell_months(raw), standard, names)
        if plan is None or rejected:
            failures += 1
            print(f"FAIL {standard!r}: {'no plan' if plan is None else rejected}")
        else:
            by_intent[plan.intent] += 1
            by_feature.update(feature_keys(plan))
            print(f"ok   {plan.intent:<22} {standard}")
    if args.report:
        args.report.write_text(json.dumps(
            {"examples": len(EXAMPLES), "passed": len(EXAMPLES) - failures,
             "by_intent": dict(by_intent), "by_feature": dict(by_feature)},
            ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"{len(EXAMPLES) - failures}/{len(EXAMPLES)} examples pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
