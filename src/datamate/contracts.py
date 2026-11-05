"""Shared interfaces. Coordinate changes here with all five module owners."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Question:
    id: str
    business_id: str
    question: str
    as_of_date: date


@dataclass(frozen=True)
class Split:
    root: Path
    questions: tuple[Question, ...]
    rulebook: str
    calendar_path: Path


@dataclass(frozen=True)
class Table:
    path: Path
    name: str
    columns: tuple[str, ...]
    sample: tuple[dict[str, Any], ...] = ()
    encoding: str = "utf-8-sig"
    delimiter: str = ","

    @property
    def key(self) -> str:
        return f"{self.path.name}::{self.name}"


@dataclass(frozen=True)
class Catalog:
    business_id: str
    readme: str
    tables: tuple[Table, ...]


@dataclass(frozen=True)
class Filter:
    field: str  # "product" for now; later "product_group", "payment_method", "shift", ...
    value: str  # canonical value from the business data (e.g. product name as written)
    rule: str = ""  # rulebook row that defines how the filter is read


@dataclass(frozen=True)
class Plan:
    intent: str
    start: date
    end: date
    top_k: int = 1
    evidence: tuple[str, ...] = ()
    # Optional; a backend that does not implement a field that is set must abstain.
    compare_start: date | None = None  # comparison period, already resolved from the rulebook
    compare_end: date | None = None
    comparison: str = ""  # "" | "change_percent" | "difference"
    filters: tuple[Filter, ...] = ()


@dataclass
class Candidate:
    type: str = "abstain"
    answer: Any = None
    confidence: float = 0.0
    reason: str = "Unsupported question"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class BusinessContext:
    split: Split
    catalog: Catalog
    cache: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    def answer(self, question: Question, context: BusinessContext) -> Candidate: ...
