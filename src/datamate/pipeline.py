"""Owner 1: connect modules, isolate question failures, write durable outputs."""

import hashlib
import json
import math
import platform
import time
import tomllib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from datamate import __version__
from datamate.agent.baseline import AbstainAgent, BaselineAgent
from datamate.contracts import Agent, BusinessContext, Candidate
from datamate.evaluation.validation import finalize
from datamate.ingestion import build_catalog
from datamate.io import load_split


@dataclass(frozen=True)
class Config:
    backend: str = "baseline"
    min_confidence: float = 1.0
    max_seconds: float = 21000

    def __post_init__(self) -> None:
        if self.backend not in ("baseline", "abstain"):
            raise ValueError(f"Unknown backend: {self.backend}")
        if not math.isfinite(self.min_confidence) or not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("max_seconds must be positive")

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        return cls(**tomllib.loads(path.read_text(encoding="utf-8"))) if path else cls()


def predict(data_dir: Path, output_dir: Path, config: Config,
            agent: Agent | None = None, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    split = load_split(data_dir)
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(split.root):
        raise ValueError("Output directory must be outside the read-only input split")
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    agent = agent or (BaselineAgent() if config.backend == "baseline" else AbstainAgent())
    groups: dict[str, list] = defaultdict(list)
    for question in split.questions:
        groups[question.business_id].append(question)
    counts: Counter[str] = Counter()
    # Business grouping bounds memory to one business and avoids repeated XLSX scans.
    # Submission lines need not have the same order as questions.jsonl.
    with (output_dir / "submission.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for business_index, (business_id, questions) in enumerate(groups.items(), 1):
            if progress:
                progress(f"[{business_index}/{len(groups)}] {business_id}: {len(questions)} questions")
            context = None
            catalog_error = ""
            ingestion_started = time.perf_counter()
            try:
                if config.backend != "abstain" and time.perf_counter() - started < config.max_seconds:
                    context = BusinessContext(split, build_catalog(split.root / "data", business_id))
            except Exception as exc:
                catalog_error = f"{type(exc).__name__}: {exc}"
            ingestion_seconds = time.perf_counter() - ingestion_started
            for question in questions:
                question_started = time.perf_counter()
                error = ""
                candidate = Candidate()
                try:
                    if time.perf_counter() - started >= config.max_seconds:
                        candidate = Candidate(reason="Run time budget reached")
                    elif catalog_error:
                        candidate = Candidate(reason="Business ingestion failed")
                        error = catalog_error
                    elif context is None:
                        candidate = Candidate(reason="Explicit abstain backend")
                    else:
                        candidate = agent.answer(question, context)
                    record = finalize(question.id, candidate, config.min_confidence)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    record = {"id": question.id, "type": "abstain"}
                # Flush completed answers so an interrupted run retains partial results.
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                counts[record["type"]] += 1
                elapsed = time.perf_counter() - question_started
                log = {"id": question.id, "business_id": question.business_id,
                       "question": question.question, "as_of_date": question.as_of_date.isoformat(),
                       "final_answer": record, "elapsed_seconds": elapsed,
                       "business_ingestion_seconds": ingestion_seconds,
                       "candidate_confidence": candidate.confidence,
                       "reason": candidate.reason, "evidence": candidate.evidence, "error": error}
                filename = hashlib.sha256(question.id.encode()).hexdigest() + ".json"
                (logs_dir / filename).write_text(
                    json.dumps(_safe_log(log), ensure_ascii=False, indent=2, default=str, allow_nan=False),
                    encoding="utf-8")
            del context
    manifest = {"version": __version__, "python": platform.python_version(),
                "config": asdict(config), "questions": len(split.questions),
                "businesses": len(groups), "answer_types": dict(counts),
                "elapsed_seconds": time.perf_counter() - started,
                "rulebook_sha256": hashlib.sha256(split.rulebook.encode()).hexdigest(),
                "questions_sha256": hashlib.sha256((split.root / "questions.jsonl").read_bytes()).hexdigest(),
                "calendar_sha256": hashlib.sha256(split.calendar_path.read_bytes()).hexdigest(),
                "models": []}
    (output_dir / "run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _safe_log(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe_log(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_log(v) for v in value]
    return value
