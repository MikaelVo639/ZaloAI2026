"""Fail when public-test text is copied into the repository (run before opening a PR).

    uv run python scripts/check_no_public_text.py [--public data/raw/public_test]

Looks for public questions and public-only rulebook rows, written verbatim (case, accents
and spacing ignored), in tracked source, tests, scripts, configs and docs. Questions or rows
that also appear in the train split are allowed. Skips with status 0 when the public split
is not present, so it is safe after a fresh clone and in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from datamate.semantics.text import fold

ROOTS = ("src", "tests", "scripts", "configs", "docs", "README.md", "AGENTS.md")


def _plain(text: str) -> str:
    return " ".join(fold(text).split())


def _questions(split: Path) -> set[str]:
    path = split / "questions.jsonl"
    if not path.is_file():
        return set()
    return {_plain(json.loads(line)["question"])
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _rows(split: Path) -> set[str]:
    path = split / "rulebook.md"
    if not path.is_file():
        return set()
    rows = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            rows.add(_plain(cells[1]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--public", type=Path, default=Path("data/raw/public_test"))
    parser.add_argument("--train", type=Path, default=Path("data/raw/train"))
    args = parser.parse_args()
    if not (args.public / "questions.jsonl").is_file():
        print(f"skip: no public split at {args.public}")
        return 0
    phrases = {text for text in _questions(args.public) - _questions(args.train) if len(text) >= 25}
    phrases |= {text for text in _rows(args.public) - _rows(args.train) if len(text) >= 30}
    files = subprocess.run(["git", "ls-files", "--", *ROOTS], capture_output=True, text=True,
                           check=True).stdout.split()
    hits = []
    for name in files:
        try:
            text = _plain(Path(name).read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            continue
        hits.extend((name, phrase) for phrase in phrases if phrase in text)
    for name, phrase in hits:
        print(f"{name}: {phrase[:100]}")
    print(f"{len(hits)} public text match(es) in {len(files)} tracked files")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
