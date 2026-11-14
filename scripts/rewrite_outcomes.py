"""Summarize logged rewrite outcomes; prints counts only, never question or model text."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def summarize(run: Path) -> dict:
    outcomes = Counter()
    abstentions = Counter()
    by_business = defaultdict(Counter)
    for path in (run / "logs").glob("*.json"):
        log = json.loads(path.read_text(encoding="utf-8"))
        for attempt in log["evidence"].get("rewrites", ()):
            kind = attempt.get("outcome", "legacy_unclassified")
            outcomes[kind] += 1
            if log["final_answer"]["type"] == "abstain":
                abstentions[kind] += 1
                by_business[log["business_id"]][kind] += 1
    return {"attempts": outcomes, "abstained_attempts": abstentions, "by_business": by_business}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run), ensure_ascii=False, indent=2, sort_keys=True))
