"""Run D01 against reviewed development sources and write non-raw audit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import datamate.ingestion.debt as debt_module
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.debt import DebtSourceError, load_debt_ledger


DEFAULT_BUSINESSES = ("biz_106", "biz_109", "biz_124", "biz_129")
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_root(path: Path) -> Path:
    value = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return Path(value).resolve()


def _revision(repository: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--business", action="append", dest="businesses")
    args = parser.parse_args()
    train_root = args.train_root.resolve()
    manifest_path = args.manifest.resolve()
    output_path = args.output.resolve()
    repository = _git_root(Path(__file__).resolve().parent)
    adapter_path = Path(debt_module.__file__).resolve()
    adapter_repository = _git_root(adapter_path.parent)
    if adapter_repository != repository or not adapter_path.is_relative_to(repository):
        raise SystemExit("Adapter import does not belong to the script checkout")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    train_businesses = frozenset(manifest.get("train_businesses", ()))
    validation_businesses = frozenset(manifest.get("validation_businesses", ()))
    if not train_businesses or not validation_businesses:
        raise SystemExit("Manifest lacks train_businesses or validation_businesses")
    businesses = tuple(args.businesses or DEFAULT_BUSINESSES)
    forbidden = sorted(set(businesses) & validation_businesses)
    if forbidden:
        raise SystemExit(f"Refusing validation businesses: {', '.join(forbidden)}")
    outside_train = sorted(set(businesses) - train_businesses)
    if outside_train:
        raise SystemExit(f"Businesses are not in manifest train split: {', '.join(outside_train)}")

    rulebook_path = train_root / "rulebook.md"
    expected_rulebook = manifest.get("rulebook_sha256")
    if expected_rulebook and _sha256(rulebook_path) != expected_rulebook:
        raise SystemExit("Rulebook checksum does not match manifest")
    rulebook = rulebook_path.read_text(encoding="utf-8")
    results: list[dict[str, Any]] = []
    for business in businesses:
        catalog = build_catalog(train_root / "data", business)
        files = sorted({table.path for table in catalog.tables} | {
            train_root / "data" / business / "README.md"
        })
        entry: dict[str, Any] = {
            "business": business,
            "sources": {
                path.relative_to(train_root).as_posix(): _sha256(path) for path in files
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
        "revision": _revision(repository),
        "adapter": {
            "path": adapter_path.relative_to(repository).as_posix(),
            "sha256": _sha256(adapter_path),
        },
        "manifest": {
            "name": manifest_path.name,
            "sha256": _sha256(manifest_path),
        },
        "rulebook_sha256": _sha256(rulebook_path),
        "validation_guard": sorted(validation_businesses),
        "businesses": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
