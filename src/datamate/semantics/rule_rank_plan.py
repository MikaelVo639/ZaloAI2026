"""Plan questions that name an enabled rule spec ("<tên luật> tháng 3/2026 là ai?").

Used only when the grammar finds no plan. The question must name exactly one enabled spec
(its ``cach_goi``) and one period; every other word must be a question or politeness word.
Anything else (a product, a group, a second condition) leaves the question unplanned.
"""

from __future__ import annotations

import re

from datamate.contracts import Plan, Question
from datamate.semantics.planner import _period
from datamate.semantics.rule_spec import RuleSpec
from datamate.semantics.rules import Rules, canonical
from datamate.semantics.text import normalize_question

INTENT = "rule_rank"

_LEFTOVER = frozenset((
    "la ai gi nao nhung cac cai the thi vay a a nhi nhe ha z j ta do day oi voi"
    " cho hoi minh toi em biet xem coi check giup giu dum hay"
    " trong cua o tai cua hang shop quan tiem tiem minh ben"
    " khach mat hang san pham mon hang ngay khung gio nhan vien tho nha cung cap"
    " ncc don vi nguoi top so mot"
).split())
_TOP = re.compile(r"\btop (\d{1,2})\b|\b(\d{1,2}) (?:khach|mat hang|san pham|mon|nhan vien"
                  r"|ngay|nha cung cap)\b")


def _matches(text: str, specs: tuple[RuleSpec, ...]) -> list[tuple[RuleSpec, str]]:
    found = []
    for spec in specs:
        for phrase in sorted(spec.cach_goi, key=len, reverse=True):
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                found.append((spec, phrase))
                break
    return found


def plan_rule_rank(question: Question, rules: Rules, specs: tuple[RuleSpec, ...]
                   ) -> Plan | None:
    if not specs or not rules.revenue:
        return None
    # Same canonical form as the rule names ("được", "các" dropped; "khách hàng" = "khách").
    text = canonical(normalize_question(question.question))
    found = _matches(text, specs)
    if len({id(spec) for spec, _ in found}) != 1:
        return None
    spec, phrase = found[0]
    text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text, count=1)
    top_k = 1
    top = _TOP.search(text)
    if top:
        top_k = int(top.group(1) or top.group(2))
        text = text[:top.start()] + " " + text[top.end():]
        if not 1 <= top_k <= 20:
            return None
    parsed = _period(text, question.as_of_date, rules)
    if parsed is None:
        return None
    start, end, leftover = parsed
    if _period(leftover, question.as_of_date, rules) is not None:
        return None  # a second period
    words = re.sub(r"[^a-z0-9 ]", " ", leftover).split()
    if any(word not in _LEFTOVER for word in words):
        return None
    end = min(end, question.as_of_date)
    if start > end:
        return None
    rows = (spec.nguon, *rules.source_rows)
    return Plan(INTENT, start, end, top_k=top_k, evidence=rows,
                rank_by=f"{spec.doi_tuong}:{spec.chi_tieu}")
