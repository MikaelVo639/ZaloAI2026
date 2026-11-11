"""Resolve explicit Tết questions using the current split's rule and calendar."""

import csv
import re
from datetime import date, timedelta
from pathlib import Path

from datamate.contracts import Plan, Question
from datamate.semantics.planner import _AMOUNT, _TOP_SELLING
from datamate.semantics.rules import Rules
from datamate.semantics.text import fold, normalize_question

_TET = r"(?:dip |mua )?tet(?: am lich)? (?:nam )?"
_CHANGE = rf"(?:thay doi|tang hay giam) {_AMOUNT} phan tram"


def _tet_windows(calendar: Path, years: set[int]) -> dict[int, tuple[date, date]]:
    boundaries = {(year - 1, 12, 23) for year in years} | {(year, 1, 10) for year in years}
    dates: dict[tuple[int, int, int], date] = {}
    with calendar.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["lunar_leap"].lower() != "false":
                continue
            key = tuple(int(row[field]) for field in ("lunar_year", "lunar_month", "lunar_day"))
            if key in boundaries:
                if key in dates:
                    raise ValueError("Duplicate lunar calendar boundary")
                dates[key] = date.fromisoformat(row["date"])
    return {year: (dates[(year - 1, 12, 23)], dates[(year, 1, 10)]) for year in years}


def plan_tet(question: Question, rules: Rules, rulebook: str, calendar: Path) -> Plan | None:
    text = normalize_question(question.question, product_names=(),
                              as_of_year=question.as_of_date.year)
    if not re.search(r"\btet\b", text):
        return None
    row = next((line for line in rulebook.splitlines()
                if line.strip().startswith("|") and all(phrase in fold(line) for phrase in (
                    "tet nam", "23 thang chap", "mung 10 thang gieng",
                    "cung so ngay ngay truoc 23 thang chap",
                ))), "")
    if not row or not rules.revenue:
        return None
    ranking = re.fullmatch(rf"{_TET}(\d{{4}}) (?:mat hang|san pham|mon|hang) nao {_TOP_SELLING}", text)
    yearly = re.fullmatch(
        rf"(?:doanh thu|dthu) {_TET}nay (?:{_CHANGE} so (?:voi|vs) {_TET}(?:ngoai|truoc)"
        rf"|so (?:voi|vs) {_TET}(?:ngoai|truoc) {_CHANGE})", text)
    previous = re.fullmatch(
        rf"{_TET}(\d{{4}}) co (?:doanh thu|dthu) hon hay kem (\d{{1,2}}) ngay truoc tet"
        rf" {_AMOUNT} phan tram", text)
    if not (ranking or yearly or previous):
        return None
    year = question.as_of_date.year if yearly else int((ranking or previous)[1])
    try:
        windows = _tet_windows(calendar, {year - 1, year} if yearly else {year})
    except (OSError, KeyError, TypeError, ValueError):
        return None
    start, end = windows[year]
    if not start <= end <= question.as_of_date:
        return None
    if ranking:
        if not rules.quantity_ranking:
            return None
        return Plan("top_products", start, end, evidence=(row, *rules.source_rows))
    if not rules.change_percent:
        return None
    if yearly:
        compare_start, compare_end = windows[year - 1]
        if not compare_start <= compare_end < start:
            return None
    else:
        length = (end - start).days + 1
        if int(previous[2]) != length:
            return None
        compare_start, compare_end = start - timedelta(days=length), start - timedelta(days=1)
    return Plan("revenue", start, end, evidence=(row, *rules.source_rows,
                rules.row("change_percent")), compare_start=compare_start,
                compare_end=compare_end, comparison="change_percent")
