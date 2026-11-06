"""Structured form of the CURRENT split's rulebook, for direction B (SEM-02, 12/11).

``parse_rulebook`` turns the markdown rulebook into sections of rules (table rows: key,
definition, extra columns) and notes (bullet / paragraph lines). Nothing is cached or
defaulted: the result is a pure function of the text passed in, so a split whose rulebook
changes a definition gets the changed definition. ``read_rules`` (direction A) enables its
features from the same rows; ``features`` on each rule says which ones, so a model prompt
and the grammar cite the same lines.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from datamate.semantics.rules import read_rules
from datamate.semantics.text import fold

STRUCTURE_VERSION = "rulebook_structure.v1"


@dataclass(frozen=True)
class Rule:
    key: str  # first cell as written ("Đơn tính vào doanh thu")
    definition: str  # second cell as written
    extra: tuple[str, ...] = ()  # further cells (answer-format table: correct / wrong)
    line: str = ""  # original line, the evidence string
    features: tuple[str, ...] = ()  # direction-A features enabled by this line


@dataclass(frozen=True)
class Section:
    title: str  # "1. Doanh thu, đơn hàng"
    rules: tuple[Rule, ...] = ()
    notes: tuple[str, ...] = ()  # bullet and paragraph lines of the section


@dataclass(frozen=True)
class Rulebook:
    preamble: tuple[str, ...]
    sections: tuple[Section, ...]
    features: dict[str, bool] = field(default_factory=dict)  # direction-A flags, for reference

    def find(self, *tokens: str) -> list[Rule]:
        """Rules whose key contains every token (diacritics and case folded)."""
        wanted = [fold(token) for token in tokens]
        return [rule for section in self.sections for rule in section.rules
                if all(token in fold(rule.key) for token in wanted)]

    def to_json(self) -> str:
        return json.dumps({"version": STRUCTURE_VERSION, **asdict(self)}, ensure_ascii=False)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", cell or "--") for cell in cells)


def parse_rulebook(text: str) -> Rulebook:
    rules = read_rules(text)
    by_line: dict[str, list[str]] = {}
    for feature, line in rules.rule_rows:
        by_line.setdefault(line, []).append(feature)
    preamble: list[str] = []
    sections: list[Section] = []
    title: str | None = None
    current_rules: list[Rule] = []
    notes: list[str] = []
    header_seen = False

    def close() -> None:
        if title is not None:
            sections.append(Section(title, tuple(current_rules), tuple(notes)))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"#{2,}\s+(.*)", line)
        if heading:
            close()
            title, current_rules, notes, header_seen = heading.group(1).strip(), [], [], False
            continue
        if line.startswith("# "):
            preamble.append(line[2:].strip())
            continue
        if line.startswith("|"):
            cells = _cells(line)
            if _is_separator(cells):
                continue
            if not header_seen:
                header_seen = True  # the table's header row ("Luật | Mặc định")
                continue
            if len(cells) >= 2:
                current_rules.append(Rule(cells[0], cells[1], tuple(cells[2:]), line,
                                          tuple(by_line.get(line, ()))))
            continue
        header_seen = False
        target = notes if title is not None else preamble
        target.append(line)
    close()
    flags = {name: value for name, value in vars(rules).items() if isinstance(value, bool)}
    return Rulebook(tuple(preamble), tuple(sections), flags)


def rulebook_text(book: Rulebook, *, max_chars: int = 12000) -> str:
    """Compact prompt text: one line per rule, cut on a section boundary."""
    parts = [f"Rulebook ({STRUCTURE_VERSION})"] + list(book.preamble)
    for section in book.sections:
        lines = [f"\n## {section.title}"]
        lines += [f"- {rule.key}: {rule.definition}" + (f" [{' | '.join(rule.extra)}]"
                                                        if rule.extra else "")
                  for rule in section.rules]
        lines += [f"  {note}" for note in section.notes]
        block = "\n".join(lines)
        if sum(len(p) + 1 for p in parts) + len(block) > max_chars:
            parts.append("\n[...]")
            break
        parts.append(block)
    return "\n".join(parts)


def unused_rules(book: Rulebook) -> list[Rule]:
    """Rows no direction-A feature reads: what the grammar cannot answer from yet."""
    return [rule for section in book.sections for rule in section.rules if not rule.features]


__all__ = ["Rule", "Rulebook", "Section", "parse_rulebook", "rulebook_text", "unused_rules"]
