"""Print the structured ranking-rule specs read from one split's rulebook.

    uv run python scripts/rule_specs.py --split data/raw/train [--json]

Shows which named ranking rows are enabled and why the others are off. This is what a run
reads at inference time (run.json "rule_specs"). Changing the reader after looking at a
non-train report is tuning to that split: build and check the reader on train only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datamate.semantics.rule_spec import report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    args = parser.parse_args()
    data = json.loads(report((args.split / "rulebook.md").read_text(encoding="utf-8-sig")))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for rule in data["rules"]:
        name = rule["nguon"].strip("|").split("|")[0].strip()
        if rule["enabled"]:
            spec = rule["spec"]
            print(f"ON  {name}: {spec['doi_tuong']} / {spec['chi_tieu']} / {spec['chon']} "
                  f"{spec['dieu_kien'] or ''}")
        else:
            print(f"OFF {name}: {'; '.join(rule['reasons'])}")
    enabled = sum(rule["enabled"] for rule in data["rules"])
    print(f"{enabled}/{len(data['rules'])} named ranking rules enabled", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
