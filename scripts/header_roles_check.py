"""How well header_schema guesses the column roles TRAIN READMEs document (LOCAL PROXY).

    uv run python scripts/header_roles_check.py --data-dir data/raw/train [--show]

For every documented column whose meaning maps to a field header_schema knows, compare the
field guessed from the header alone. A wrong guess is worse than no guess.
Never point this at a public or private split.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from datamate.ingestion.header_schema import _FIELDS, header_field
from datamate.ingestion.readme_sales import meaning_field, parse_sections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    known = {field for field, _, _ in _FIELDS}
    counts, wrong = Counter(), []
    for readme in sorted((args.data_dir / "data").glob("*/README.md")):
        for section in parse_sections(readme.read_text(encoding="utf-8-sig")):
            if not {"order_id", "amount"} <= set(section.fields):
                continue  # only sales line lists are ever guessed
            for column, meaning in section.columns.items():
                documented = meaning_field(meaning)
                guessed = header_field(column)
                if documented not in known and guessed is None:
                    continue
                outcome = ("right" if guessed == documented else
                           "unguessed" if guessed is None else "wrong")
                counts[outcome] += 1
                if outcome != "right":
                    wrong.append((outcome, readme.parent.name, section.file, column,
                                  documented, guessed))
    print(dict(counts))
    if args.show:
        for row in wrong:
            print(*row, sep=" | ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
