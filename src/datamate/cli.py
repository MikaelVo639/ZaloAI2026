import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from datamate.data_tools import business_holdout, import_archive
from datamate.demo import create_demo, run_smoke
from datamate.evaluation.scorer import evaluate
from datamate.evaluation.validation import validate_submission
from datamate.ingestion import build_catalog
from datamate.ingestion.catalog import catalog_dict, json_value
from datamate.io import index_records, load_split, read_jsonl
from datamate.pipeline import Config, predict


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="datamate", description="Offline DataMate team starter")
    commands = result.add_subparsers(dest="command", required=True)
    bench = commands.add_parser('benchmark', help='Offline EVAL-01 run and local-proxy report')
    for name in ('checkout', 'data-dir', 'holdout', 'config', 'output-dir'):
        bench.add_argument('--' + name, type=Path, required=True)
    bench.add_argument('--compare-to', type=Path)
    demo = commands.add_parser("demo", help="Generate synthetic CSV/XLSX/SQLite smoke-test data")
    demo.add_argument("--output", type=Path, default=Path("data/demo"))
    smoke = commands.add_parser("smoke", help="Generate/reuse demo, predict, validate and check metrics")
    smoke.add_argument("--data-dir", type=Path, default=Path("data/demo"))
    smoke.add_argument("--output-dir", type=Path, default=Path("outputs/demo"))
    ingest = commands.add_parser("import-data", help="Extract an organizer ZIP preserving split layout")
    ingest.add_argument("archive", type=Path)
    ingest.add_argument("--destination", type=Path, default=Path("data/raw"))
    inspect = commands.add_parser("inspect", help="Profile headers and small samples, without labels")
    inspect.add_argument("--data-dir", type=Path, required=True)
    inspect.add_argument("--business", help="Optional one-business filter")
    inspect.add_argument("--output", type=Path, default=Path("outputs/catalog.json"))
    run = commands.add_parser("predict", help="Write submission.jsonl, logs/ and run.json")
    run.add_argument("--data-dir", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, default=Path("outputs/baseline"))
    run.add_argument("--config", type=Path)
    run.add_argument("--backend", choices=("baseline", "abstain", "local_plan"))
    validate = commands.add_parser("validate", help="Check format, IDs and completeness")
    validate.add_argument("submission", type=Path)
    validate.add_argument("--questions", type=Path, required=True)
    validate.add_argument("--allow-missing", action="store_true")
    score = commands.add_parser("evaluate", help="LOCAL PROXY score; never the official scorer")
    score.add_argument("submission", type=Path)
    score.add_argument("--reference", type=Path, required=True)
    score.add_argument("--questions", type=Path)
    score.add_argument("--holdout", type=Path, help="Score only validation_ids in a business split")
    score.add_argument("--output", type=Path, default=Path("outputs/metrics.json"))
    holdout = commands.add_parser("split-businesses", help="Create a reproducible business holdout")
    holdout.add_argument("--data-dir", type=Path, required=True)
    holdout.add_argument("--output", type=Path, default=Path("outputs/business_split.json"))
    holdout.add_argument("--validation-fraction", type=float, default=0.2)
    holdout.add_argument("--seed", type=int, default=42)
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == 'benchmark':
            from datamate.evaluation.benchmark import benchmark
            report = benchmark(args.checkout, args.data_dir, args.holdout, args.config,
                               args.output_dir, args.compare_to)
        elif args.command == "demo":
            report = create_demo(args.output)
        elif args.command == "smoke":
            report = run_smoke(args.data_dir, args.output_dir)
        elif args.command == "import-data":
            report = import_archive(args.archive, args.destination)
        elif args.command == "inspect":
            split = load_split(args.data_dir)
            businesses = sorted({q.business_id for q in split.questions})
            if args.business:
                if args.business not in businesses:
                    raise ValueError(f"Unknown business: {args.business}")
                businesses = [args.business]
            report = {"businesses": [catalog_dict(build_catalog(split.root / "data", business),
                                                  relative_to=split.root)
                                      for business in businesses]}
            _save(args.output, report)
            report = {"output": str(args.output.resolve()), "businesses": len(businesses)}
        elif args.command == "predict":
            config = Config.load(args.config)
            if args.backend:
                config = replace(config, backend=args.backend)
            report = predict(args.data_dir, args.output_dir, config,
                             progress=lambda message: print(message, file=sys.stderr, flush=True))
        elif args.command == "validate":
            expected = set(index_records(read_jsonl(args.questions)))
            report = validate_submission(read_jsonl(args.submission), expected,
                                         require_complete=not args.allow_missing)
        elif args.command == "evaluate":
            predictions, references = read_jsonl(args.submission), read_jsonl(args.reference)
            mapping = None
            if args.questions:
                mapping = {q["id"]: q["business_id"] for q in read_jsonl(args.questions)}
            if args.holdout:
                selected = set(json.loads(args.holdout.read_text(encoding="utf-8"))["validation_ids"])
                index_records(predictions)
                index_records(references)
                predictions = [r for r in predictions if r["id"] in selected]
                references = [r for r in references if r["id"] in selected]
                if {r["id"] for r in references} != selected:
                    raise ValueError("Holdout ids are missing from reference data")
            report = evaluate(predictions, references, mapping)
            _save(args.output, report)
            report = {k: v for k, v in report.items() if k != "outcomes"}
        else:
            split = business_holdout(args.data_dir, args.output, args.validation_fraction, args.seed)
            report = {"output": str(args.output.resolve()),
                      "train_businesses": len(split["train_businesses"]),
                      "validation_businesses": len(split["validation_businesses"])}
        print(json.dumps(report, ensure_ascii=False, indent=2, default=json_value))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_value), encoding="utf-8")
