"""Robustness check: meaning-preserving rewrites of TRAIN questions, scored with train labels.

Each perturbation changes only the wording around a question's numbers, dates and names:
diacritics, how a month is abbreviated, a closing particle, a synonym of a grammar word,
one transposed letter in a grammar word. The id and the reference answer stay the same, so
an answer that changes after a rewrite is a regression of the question parser.

Only for training businesses; never build these splits from public or private questions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Callable

PERTURBATIONS = ("unaccented", "month_abbreviation", "particle", "synonym", "transposition")


def _choice(key: str, options: tuple[str, ...]) -> str:
    """Deterministic choice per question id, so a split is reproducible."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


def strip_diacritics(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(
        c for c in decomposed if unicodedata.category(c) != "Mn"))


def _unaccented(text: str, key: str) -> str | None:
    result = strip_diacritics(text)
    return result if result != text else None


_MONTH_YEAR = re.compile(r"\b[Tt]háng\s+(\d{1,2})\s*/\s*(\d{4})\b"
                         r"|\b[Tt]háng\s+(\d{1,2})\s+năm\s+(\d{4})\b")
_MONTH_STYLES = ("T{m}/{y}", "th{m}/{y}", "thg {m}/{y}", "{m}-{y}")


def _month_abbreviation(text: str, key: str) -> str | None:
    style = _choice(key, _MONTH_STYLES)

    def replace(match: re.Match[str]) -> str:
        month = match.group(1) or match.group(3)
        year = match.group(2) or match.group(4)
        return style.format(m=month, y=year)

    result = _MONTH_YEAR.sub(replace, text)
    return result if result != text else None


_PARTICLES = ("vậy", "z", "ạ", "hả", "nhỉ", "nha", "dậy")
_END = re.compile(r"\s*([?.!…]*)\s*$")


def _particle(text: str, key: str) -> str | None:
    particle = _choice(key, _PARTICLES)
    body = _END.sub("", text)
    ending = _END.search(text).group(1)
    if body.lower().endswith(" " + particle):
        return None
    return f"{body} {particle}{ending}"


# Synonyms with the same meaning in every question; each pair is checked on train.
_SYNONYMS = (
    (re.compile(r"\btăng hay giảm\b", re.I), "lên hay xuống"),
    (re.compile(r"\bcủa cửa hàng\b", re.I), ("của quán", "của tiệm")),
    (re.compile(r"\btháng trước\b", re.I), "tháng rồi"),
    (re.compile(r"\bnăm nay\b", re.I), "năm ni"),
    (re.compile(r"\bhôm qua\b", re.I), "bữa qua"),
    (re.compile(r"\bmang về\b", re.I), "đem về"),
)


def _synonym(text: str, key: str) -> str | None:
    result = text
    for pattern, replacement in _SYNONYMS:
        if isinstance(replacement, tuple):
            replacement = _choice(key, replacement)
        result = pattern.sub(replacement, result)
    return result if result != text else None


# Grammar words whose letters are often swapped when typing fast (unaccented on purpose).
_ANCHORS = ("nhieu", "nhat", "doanh", "thang", "tong", "trung")


def _transposition(text: str, key: str) -> str | None:
    plain = strip_diacritics(text)
    words = [m for m in re.finditer(r"[A-Za-z]+", plain) if m.group().lower() in _ANCHORS]
    if not words:
        return None
    word = words[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(words)]
    letters = list(word.group())
    letters[2], letters[3] = letters[3], letters[2]
    swapped = "".join(letters)
    if swapped.lower() == word.group().lower():
        return None
    return plain[:word.start()] + swapped + plain[word.end():]


_FUNCTIONS: dict[str, Callable[[str, str], str | None]] = {
    "unaccented": _unaccented,
    "month_abbreviation": _month_abbreviation,
    "particle": _particle,
    "synonym": _synonym,
    "transposition": _transposition,
}


def perturb(name: str, text: str, key: str) -> str | None:
    """The rewritten question, or None when this perturbation does not apply."""
    return _FUNCTIONS[name](text, key)


def _link_tree(source: Path, destination: Path) -> None:
    """Share the business files with the source split (hard links, else copies)."""
    try:
        shutil.copytree(source, destination, copy_function=os.link)
    except OSError:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)


def build_splits(train: Path, output: Path,
                 names: tuple[str, ...] = PERTURBATIONS) -> dict[str, dict[str, int]]:
    """Write one split per perturbation with only the questions it changed.

    ``output/<name>/`` is an inference split (questions, rulebook, calendar, data) and holds
    no labels; ``output/<name>.answers.jsonl`` holds the matching train references.
    """
    train, output = train.resolve(), output.resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}; choose a new directory")
    questions = [json.loads(line) for line in
                 (train / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    answers = {json.loads(line)["id"]: line for line in
               (train / "answers.jsonl").read_text(encoding="utf-8").splitlines() if line}
    output.mkdir(parents=True)
    report: dict[str, dict[str, int]] = {}
    for name in names:
        split = output / name
        split.mkdir()
        for file in ("rulebook.md", "calendar.csv"):
            shutil.copy2(train / file, split / file)
        _link_tree(train / "data", split / "data")
        changed = []
        for question in questions:
            rewritten = perturb(name, question["question"], f"{name}:{question['id']}")
            if rewritten is not None:
                changed.append({**question, "question": rewritten})
        (split / "questions.jsonl").write_text(
            "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in changed), encoding="utf-8")
        (output / f"{name}.answers.jsonl").write_text(
            "".join(answers[q["id"]] + "\n" for q in changed), encoding="utf-8")
        report[name] = {"questions": len(changed)}
    (output / "robustness.json").write_text(json.dumps(
        {"source": str(train), "perturbations": report}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return report
