"""Rulebook robustness: rewrite a TRAIN rulebook and see which rules the code still reads.

A test split may word the same rules differently. Each perturbation below keeps the meaning
of every row and only changes how it is written, so a rule the code stops reading is a
fragility of the rule reader, not a change of meaning. ``negation_flip`` is the opposite
check: it changes meaning, so rules built on the flipped words must stop being read.

Only for training rulebooks; never build these from public or private splits.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import fields

from datamate.semantics.rules import Rules, read_rules

_ROW = re.compile(r"^\s*\|(.*)\|\s*$")
_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _tables(lines: list[str]) -> list[list[int]]:
    """Line numbers of the data rows of each table (header and separator excluded)."""
    tables, current = [], []
    for index, line in enumerate(lines):
        if _ROW.match(line):
            current.append(index)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    result = []
    for rows in tables:
        if len(rows) >= 3 and _SEPARATOR.match(lines[rows[1]]):
            result.append(rows[2:])
    return result


def _headers(lines: list[str]) -> list[int]:
    return [rows[0] - 2 for rows in _tables(lines) if rows]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in _ROW.match(line).group(1).split("|")]


def _join(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _map_rows(text: str, change: Callable[[str, str], tuple[str, str]]) -> str:
    lines = text.split("\n")
    for rows in _tables(lines):
        for index in rows:
            cells = _cells(lines[index])
            if len(cells) >= 2:
                cells[0], cells[1] = change(cells[0], cells[1])
                lines[index] = _join(cells)
    return "\n".join(lines)


def _row_order(text: str) -> str:
    lines = text.split("\n")
    for rows in _tables(lines):
        values = [lines[index] for index in rows]
        for index, value in zip(rows, reversed(values)):
            lines[index] = value
    return "\n".join(lines)


def _header(text: str) -> str:
    lines = text.split("\n")
    for index in _headers(lines):
        cells = _cells(lines[index])
        cells[:2] = ["Khái niệm", "Cách tính"]
        lines[index] = _join(cells)
    return "\n".join(lines)


def _notes(text: str) -> str:
    return _map_rows(text, lambda key, value: (
        key, f"Theo quy ước chung: {value} Áp dụng như nhau cho mọi cửa hàng."))


_FILLERS = ((r"\bcác\b", "tất cả các"), (r"\bkhông\b(?! phải)", "không hề"),
            (r"(?<!chính )\blà\b", "chính là"), (r"\bthành tiền\b", "thành tiền của"))


def _fillers(text: str) -> str:
    def change(key: str, value: str) -> tuple[str, str]:
        for pattern, replacement in _FILLERS:
            value = re.sub(pattern, replacement, value)
        return key, value
    return _map_rows(text, change)


def _key_wording(text: str) -> str:
    return _map_rows(text, lambda key, value: (f"{key} (theo quy ước)", value))


_SYNONYMS = ((r"\bkhách\b(?! hàng)", "khách hàng"), (r"\bhuỷ\b", "hủy bỏ"),
             (r"(?<!bao )\bgồm\b", "bao gồm"), (r"\bđơn\b(?! vị| giá)", "hoá đơn"))


def _synonyms(text: str) -> str:
    def change(key: str, value: str) -> tuple[str, str]:
        for pattern, replacement in _SYNONYMS:
            value = re.sub(pattern, replacement, value)
        return key, value
    return _map_rows(text, change)


def _negation_flip(text: str) -> str:
    """Meaning change: the first "không" of every definition becomes "có"."""
    return _map_rows(text, lambda key, value: (key, re.sub(r"\*{0,2}không\*{0,2}", "có", value,
                                                           count=1)))


PRESERVING = {"row_order": _row_order, "header": _header, "notes": _notes,
              "fillers": _fillers, "key_wording": _key_wording, "synonyms": _synonyms}
CHANGING = {"negation_flip": _negation_flip}
PERTURBATIONS = (*PRESERVING, *CHANGING)


def perturb_rulebook(name: str, text: str) -> str:
    return {**PRESERVING, **CHANGING}[name](text)


def enabled(rules: Rules) -> frozenset[str]:
    """Names of the rules the reader recognised (flags, shifts, counts)."""
    skip = {"source_rows", "rule_rows", "shift_base"}
    return frozenset(f.name for f in fields(rules)
                     if f.name not in skip and getattr(rules, f.name) not in (False, 0, (), ""))


def compare(rulebook: str, names: tuple[str, ...] = PERTURBATIONS) -> dict[str, dict]:
    """Per perturbation: rules lost and gained against the original rulebook."""
    original = enabled(read_rules(rulebook))
    report = {"original": {"enabled": len(original)}}
    for name in names:
        changed = enabled(read_rules(perturb_rulebook(name, rulebook)))
        report[name] = {"enabled": len(changed), "lost": sorted(original - changed),
                        "gained": sorted(changed - original)}
    return report
