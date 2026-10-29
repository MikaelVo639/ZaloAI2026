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
class Plan:
    intent: str
    start: date
    end: date
    top_k: int = 1
    evidence: tuple[str, ...] = ()


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
