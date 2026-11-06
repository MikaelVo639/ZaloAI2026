"""Run baseline + local model twice on a prepared offline GPU environment.

Never downloads a model or dependencies. The existing Evaluation runner opens labels
only after each inference subprocess exits. Output must be a new ignored run directory.
"""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from datamate.agent.acceptance import compare_acceptance, environment_report
from datamate.evaluation.benchmark import benchmark
from datamate.pipeline import Config


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/train"))
    parser.add_argument("--holdout", type=Path, default=Path("outputs/business_split.json"))
    parser.add_argument("--config", type=Path, default=Path("configs/local_plan.toml"))
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--private-questions", type=int)
    parser.add_argument("--private-businesses", type=int)
    parser.add_argument("--check-environment", action="store_true")
    args = parser.parse_args()
    settings = Config.load(args.config)
    if settings.backend != "local_plan":
        parser.error("Acceptance requires a local_plan config")
    if args.model_path:
        settings = replace(settings, model_path=str(args.model_path.resolve()))
    readiness = environment_report(Path(settings.model_path))
    if args.check_environment:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0 if readiness["ready_for_gpu_acceptance"] else 2
    if not args.output_dir:
        parser.error("--output-dir is required for an acceptance run")
    if not readiness["ready_for_gpu_acceptance"]:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 2
    output, data = args.output_dir.resolve(), args.data_dir.resolve()
    if output.is_relative_to(data) or data.is_relative_to(output):
        parser.error("Input and output must be separate trees")
    if Path(settings.model_path).resolve().is_relative_to(data):
        parser.error("Model weights must be outside the input split")
    if (args.private_questions is None) != (args.private_businesses is None):
        parser.error("Provide both private counts, or neither")
    if args.private_questions is not None and not 0 < args.private_businesses <= args.private_questions:
        parser.error("Private counts must be positive, businesses <= questions")
    output.mkdir(parents=True, exist_ok=False)
    save(output / "environment.json", readiness)
    config_dir = output / "configs"
    config_dir.mkdir()
    configs = {"baseline": replace(settings, backend="baseline", min_confidence=1.0),
               "local_plan": replace(settings, model_path=str(Path(settings.model_path).resolve()))}
    for name, config in configs.items():
        (config_dir / f"{name}.toml").write_text(
            "\n".join(f"{key} = {json.dumps(value)}" for key, value in asdict(config).items()) + "\n",
            encoding="utf-8")
    checkout = Path(__file__).resolve().parents[1]
    try:
        for name, config in (("baseline", "baseline"), ("model-first", "local_plan"),
                             ("model-repeat", "local_plan")):
            print(f"Running {name}", flush=True)
            benchmark(checkout, data, args.holdout, config_dir / f"{config}.toml", output / name)
        report = compare_acceptance(output / "baseline", output / "model-first", output / "model-repeat",
                                    args.private_questions, args.private_businesses)
        save(output / "acceptance.json", report)
        print(json.dumps({"status": "complete", "gates": report["gates"],
                          "ready_for_evaluation_review": report["ready_for_evaluation_review"]}, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        save(output / "failure.json", {"status": "incomplete", "error": str(exc)})
        print(f"Acceptance incomplete: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
