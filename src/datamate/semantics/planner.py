import calendar
import re
from datetime import date, timedelta

from datamate.contracts import Plan, Question
from datamate.semantics.rules import Rules
from datamate.semantics.text import fold


def plan_question(question: Question, rules: Rules) -> Plan | None:
    """Closed grammar: leftover filters/comparisons cause abstention."""
    if not rules.revenue:
        return None
    text = fold(question.question)
    intent = ""
    top_k = 1
    patterns = (
        ("top_products", r"(?:(\d+) )?(?:mat hang|san pham|hang|mon) (?:ban chay|ban duoc nhieu|ban nhieu|chay) nhat"),
        ("revenue", r"(?:tong )?(?:doanh thu|dthu)"),
        ("order_count", r"(?:tong )?so (?:luong )?don(?: hang)?"),
    )
    for kind, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            intent = kind
            if kind == "top_products":
                top_k = int(match.group(1) or 1)
                if not rules.quantity_ranking or not 1 <= top_k <= 100:
                    return None
            text = text[:match.start()] + " " + text[match.end():]
            break
    if not intent:
        return None
    resolved = _period(text, question.as_of_date, rules)
    if resolved is None:
        return None
    start, end, leftover = resolved
    # Do not silently ignore product names, payment methods, groups, shifts, etc.
    leftovers = re.sub(
        r"\b(?:nhung mat hang nao|nhung san pham nao|mat hang nao|"
        r"cho toi biet|cho minh biet|cho hoi|tong cong|cua hang|cua shop|"
        r"la bao nhieu|bao nhieu|bao nhiu|bnhiu|nhiu|bn|tien|dong|trong|"
        r"cua|shop|la|co|duoc|nao|nhung|gi|vay|a|nhi|ngay)\b", " ", leftover,
    )
    if leftovers.strip():
        return None
    # Explicit periods are capped at the as-of date: future events have not happened.
    return Plan(intent, start, min(end, question.as_of_date), top_k, rules.source_rows)


def _period(text: str, today: date, rules: Rules) -> tuple[date, date, str] | None:
    patterns = (
        r"thang (\d{1,2})/(\d{4})",
        r"ngay (\d{1,2})/(\d{1,2})/(\d{4})",
        r"(\d{1,3}) ngay gan nhat",
        r"thang nay|thang truoc|tuan nay|tuan truoc|tuan roi|hom nay|hom qua",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if index == 0:
                month, year = map(int, match.groups())
                start = date(year, month, 1)
                end = date(year, month, calendar.monthrange(year, month)[1])
            elif index == 1:
                day, month, year = map(int, match.groups())
                start = end = date(year, month, day)
            elif index == 2:
                days = int(match.group(1))
                if days < 1:
                    return None
                start, end = today - timedelta(days=days - 1), today
            else:
                token = match.group()
                if token == "hom nay":
                    start = end = today
                elif token == "hom qua":
                    start = end = today - timedelta(days=1)
                elif token == "thang nay" and rules.month_to_date:
                    start, end = today.replace(day=1), today
                elif token == "thang truoc":
                    end = today.replace(day=1) - timedelta(days=1)
                    start = end.replace(day=1)
                elif token.startswith("tuan") and rules.monday_week:
                    monday = today - timedelta(days=today.weekday())
                    if token == "tuan nay":
                        start, end = monday, today
                    else:
                        start, end = monday - timedelta(days=7), monday - timedelta(days=1)
                else:
                    return None
        except ValueError:
            return None
        return start, end, text[:match.start()] + " " + text[match.end():]
    return None
