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

MODEL_PERTURBATIONS = ("clause_order", "paraphrase", "verbose_chat", "combo")

PERTURBATIONS = ("unaccented", "month_abbreviation", "particle", "synonym", "transposition",
                 "short_year", "chat_style", "store_word", "greeting", "chat_typos", *MODEL_PERTURBATIONS)


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


# Not "3 tháng 4/2026, 5/2026, 6/2026": there "tháng" belongs to the count of months.
_MONTH_YEAR = re.compile(r"(?<!\d )\b[Tt]háng\s+(\d{1,2})\s*/\s*(\d{4})\b"
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
    (re.compile(r"\btrung bình\b", re.I), "bình quân"),
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


_SHORT_STYLES = ("th {m}/{yy}", "{m}/{yy}", "T{m}/{yy}")


def _short_year(text: str, key: str) -> str | None:
    """"tháng 3/2025" -> "th 3/25": a two-digit year after the month."""
    style = _choice(key, _SHORT_STYLES)

    def replace(match: re.Match[str]) -> str:
        month = match.group(1) or match.group(3)
        year = match.group(2) or match.group(4)
        return style.format(m=month, yy=year[2:]) if "2020" <= year <= "2039" else match.group()

    result = _MONTH_YEAR.sub(replace, text)
    return result if result != text else None


_CALLS = ("em ơi", "ad ơi", "shop ơi", "ủa")
_CHAT_WORDS = (
    (re.compile(r"\bcủa\b"), "kua"),
    (re.compile(r"\bmình\b"), "mik"),
    (re.compile(r"\bđơn hàng\b"), "đh"),
    (re.compile(r"\bgiùm\b|\bgiúp\b"), "jum"),
)


def _chat_style(text: str, key: str) -> str | None:
    """Chat typing: a call at the start, chat spellings, "rồi" at the end."""
    result = text
    for pattern, replacement in _CHAT_WORDS:
        result = pattern.sub(replacement, result)
    body = _END.sub("", result)
    ending = _END.search(result).group(1)
    first = body[:1].lower() + body[1:]
    return f"{_choice(key, _CALLS)} {first} rồi{ending}"


# How a shop owner names the shop: replaces the question's own shop phrase, or follows
# "doanh thu" when the question has none.
_STORE_PHRASE = re.compile(r"\b(?:của cửa hàng|của shop|cửa hàng|shop)\b", re.I)
_STORE_WORDS = ("của quán", "của tiệm", "quán mình", "tiệm mình", "bên mình", "shop mình")


def _store_word(text: str, key: str) -> str | None:
    word = _choice(key, _STORE_WORDS)
    match = _STORE_PHRASE.search(text)
    if match:
        if not match.group().lower().startswith("của"):
            word = word.removeprefix("của ")  # the subject: "tiệm bán", not "của tiệm bán"
        result = text[:match.start()] + word + text[match.end():]
    else:
        result = re.sub(r"\b([Dd]oanh thu)\b", rf"\1 {word}", text, count=1)
    return result if result != text else None


# A greeting before and a thank-you after the question.
_OPENINGS = ("Alo", "Cho em hỏi", "Chị ơi cho em hỏi", "Hello shop", "Dạ cho mình hỏi")
_THANKS = ("", " cảm ơn", " thanks", " nha cảm ơn ạ")


def _greeting(text: str, key: str) -> str | None:
    body = _END.sub("", text)
    ending = _END.search(text).group(1)
    first = body[:1].lower() + body[1:]
    return f"{_choice(key, _OPENINGS)} {first}{_choice(key + ':end', _THANKS)}{ending}"


# Chat spellings of grammar words; at most two per question.
_TYPOS = (
    (re.compile(r"\bbao nhiêu\b"), "bao nhiu"), (re.compile(r"\btrong\b"), "trog"),
    (re.compile(r"\bnhất\b"), "nhứt"), (re.compile(r"\bkhông\b"), "ko"),
    (re.compile(r"\bđược\b"), "đc"), (re.compile(r"\bvậy\b"), "dza"),
    (re.compile(r"\bdoanh thu\b", re.I), "dthu"), (re.compile(r"\btháng\b"), "thg"),
)


def _chat_typos(text: str, key: str) -> str | None:
    present = [(p, r) for p, r in _TYPOS if p.search(text)]
    if not present:
        return None
    start = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(present)
    result = text
    for pattern, replacement in (present[start:] + present[:start])[:2]:
        result = pattern.sub(replacement, result)
    return result


_EXPLICIT_MONTH = r"tháng\s+(?:0?[1-9]|1[0-2])\s*/\s*20\d{2}"
_LEADING_MONTH = re.compile(rf"^({_EXPLICIT_MONTH})(?:,\s*|\s+)(.+)$", re.I)
_TRAILING_MONTH = re.compile(rf"^(.+?)\s+(?:trong\s+)?({_EXPLICIT_MONTH})([?.!]*)$", re.I)
_OTHER_PERIOD = re.compile(
    r"\b(?:tháng|quý|tuần|năm|ngày|Tết|hôm|so với|lần lượt|kỳ|từ|đến)\b|\d\s*[/.-]\s*\d",
    re.I,
)


def _clause_order(text: str, key: str) -> str | None:
    """Move a single explicit month as a unit. Leave comparisons and sequences alone."""
    leading = _LEADING_MONTH.fullmatch(text)
    if leading and not _OTHER_PERIOD.search(leading[2]):
        ending = _END.search(leading[2])[1]
        body = _END.sub("", leading[2])
        return f"{body}, trong {leading[1]}{ending}"
    trailing = _TRAILING_MONTH.fullmatch(text)
    if trailing and not _OTHER_PERIOD.search(trailing[1]):
        return f"{trailing[2]}, {trailing[1]}{trailing[3]}"
    return None


_PARAPHRASES = (
    # A modifier of the same revenue measure. It does not imply cash collected or profit.
    (re.compile(r"^doanh thu\b", re.I), "số doanh thu ghi nhận"),
    # Arithmetic mean of the value of one order, keeping the unit and denominator.
    (re.compile(r"^giá trị trung bình (một|mỗi) đơn\b", re.I),
     r"giá trị bình quân tính cho \1 đơn"),
)


def _paraphrase(text: str, key: str) -> str | None:
    prefix = _LEADING_MONTH.fullmatch(text)
    offset = prefix.start(2) if prefix else 0
    for pattern, wording in _PARAPHRASES:
        body, count = pattern.subn(wording, text[offset:], count=1)
        if count:
            return text[:offset] + body
    return None


_VERBOSE_OPENINGS = (
    "Mình đang gom số liệu để hoàn tất báo cáo nội bộ. Nhờ tra giúp câu này: ",
    "Mình cần đối chiếu số liệu trước khi chốt bản tổng hợp. Câu muốn hỏi là: ",
)


def _verbose_chat(text: str, key: str) -> str:
    return _choice(key, _VERBOSE_OPENINGS) + text


def _combo(text: str, key: str) -> str:
    changed = _clause_order(text, key) or _paraphrase(text, key) or text
    # The particle and context are optional conversational framing, with no new constraints.
    return _verbose_chat(_particle(changed, key) or changed, key + ":combo")


_FUNCTIONS: dict[str, Callable[[str, str], str | None]] = {
    "unaccented": _unaccented,
    "month_abbreviation": _month_abbreviation,
    "particle": _particle,
    "synonym": _synonym,
    "transposition": _transposition,
    "short_year": _short_year,
    "chat_style": _chat_style,
    "store_word": _store_word,
    "greeting": _greeting,
    "chat_typos": _chat_typos,
    "clause_order": _clause_order,
    "paraphrase": _paraphrase,
    "verbose_chat": _verbose_chat,
    "combo": _combo,
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
