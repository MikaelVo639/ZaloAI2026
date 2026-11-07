"""Run D01 against reviewed development sources and write non-raw audit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.debt import DebtSourceError, load_debt_ledger


DEFAULT_BUSINESSES = ("biz_106", "biz_109", "biz_124", "biz_129")
VALIDATION_BUSINESSES = frozenset(
    {"biz_107", "biz_111", "biz_115", "biz_120", "biz_123", "biz_127"}
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--business", action="append", dest="businesses")
    args = parser.parse_args()
    businesses = tuple(args.businesses or DEFAULT_BUSINESSES)
    forbidden = sorted(set(businesses) & VALIDATION_BUSINESSES)
    if forbidden:
        raise SystemExit(f"Refusing validation businesses: {', '.join(forbidden)}")

    rulebook_path = args.train_root / "rulebook.md"
    rulebook = rulebook_path.read_text(encoding="utf-8")
    results: list[dict[str, Any]] = []
    for business in businesses:
        catalog = build_catalog(args.train_root / "data", business)
        files = sorted({table.path for table in catalog.tables} | {
            args.train_root / "data" / business / "README.md"
        })
        entry: dict[str, Any] = {
            "business": business,
            "sources": {
                path.relative_to(args.train_root).as_posix(): _sha256(path) for path in files
            },
        }
        try:
            ledger = load_debt_ledger(catalog, rulebook)
            entry.update(
                status="ok",
                orders=len(ledger.orders),
                payments=len(ledger.payments),
                lineage=ledger.lineage,
            )
        except DebtSourceError as exc:
            entry.update(status="unsupported", error=exc.diagnostics)
        results.append(entry)

    artifact = {
        "revision": _revision(),
        "rulebook_sha256": _sha256(rulebook_path),
        "validation_guard": sorted(VALIDATION_BUSINESSES),
        "businesses": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
