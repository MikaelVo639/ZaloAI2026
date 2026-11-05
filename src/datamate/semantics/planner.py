"""Semantics: question parsing and rules from the CURRENT split."""
import calendar
import re
from datetime import date, timedelta

from datamate.contracts import Plan, Question
from datamate.semantics.rules import Rules
from datamate.semantics.text import normalize_question

_AMOUNT = r"(?:bao nhieu|bao nhiu|bnhiu|bn|nhieu|nhiu|may)"
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
# Words that carry no condition. Anything else left after intent and period is unsupported.
_FILLERS = re.compile(
    r"\b(?:nhung mat hang nao|nhung san pham nao|mat hang nao|"
    r"cho toi biet|cho minh biet|cho minh hoi|cho hoi|xem giup|coi gium|check gium|"
    r"giup minh|tinh rieng|tong cong|cua hang|cua shop|la bao nhieu|bao nhieu|bao nhiu|"
    r"bnhiu|nhieu|nhiu|bn|tien|dong|trong|cua|shop|la|co|duoc|nao|nhung|gi|vay|a|nhi|ngay|"
    r"e|z|ta|ha|nhe|do)\b"
)
_ORDER = r"(?:don|bill|order)(?: hang)?"
_REVENUE = r"(?:doanh thu|dthu)"
_ONE = r"(?:mot|moi|1)"
# (intent, rule feature that must be enabled, pattern, fragments also removed). First match
# wins, so specific intents come before the generic "doanh thu" / "số đơn" ones.
_INTENTS = (
    ("cancel_rate", "cancel_rate",
     rf"t[iy] le (?:{_ORDER} )?(?:bi )?huy(?: {_ORDER})?"
     rf"|(?:co )?{_AMOUNT} phan tram {_ORDER} (?:bi |da )?huy",
     (rf"\b(?:{_AMOUNT} )?phan tram\b",)),
    ("cancel_count", "cancelled_order",
     rf"(?:dem )?so {_ORDER} (?:bi |da )?huy"
     rf"|(?:co )?{_AMOUNT} {_ORDER} (?:bi huy|da huy|o trang thai huy)"
     rf"|bi huy {_AMOUNT} {_ORDER}", ()),
    ("average_order", "average_order",
     rf"gia tri trung binh {_ONE} {_ORDER}"
     rf"|{_REVENUE} (?:trung binh|binh quan) (?:tren )?{_ONE} {_ORDER}"
     rf"|trung binh {_ONE} {_ORDER}(?P<mid>.*?) mang ve {_AMOUNT} {_REVENUE}", ()),
    ("top_products", None,
     r"(?:(\d+) )?(?:mat hang|san pham|hang|mon) (?:ban chay|ban duoc nhieu|ban nhieu|chay) nhat",
     ()),
    ("revenue", None, rf"(?:tong )?{_REVENUE}", ()),
    ("order_count", None,
     rf"(?:tong )?so (?:luong )?{_ORDER}|(?:ban duoc |ban |co )?(?:tong cong )?{_AMOUNT} {_ORDER}",
     ()),
)


def _best_day(text: str) -> str | None:
    """'Ngày nào doanh thu cao nhất' (also after 'bán được nhiều tiền' became 'doanh thu')."""
    if not re.search(r"\bngay nao\b", text):
        return None
    reduced = re.sub(r"\bngay nao\b", " ", text, count=1)
    revenue = re.search(rf"\b(?:xet theo )?{_REVENUE}\b", reduced)
    if not revenue:
        return None
    reduced = reduced[:revenue.start()] + " " + reduced[revenue.end():]
    # "thấp nhất" leaves "thap" behind, so the leftover check rejects it.
    best = re.search(r"\bban dat nhat\b|\b(?:cao|nhieu) nhat\b|\bnhat\b", reduced)
    if not best:
        return None
    return reduced[:best.start()] + " " + reduced[best.end():]


def plan_question(question: Question, rules: Rules) -> Plan | None:
    """Closed grammar: leftover filters/comparisons cause abstention."""
    if not rules.revenue:
        return None
    text = normalize_question(question.question)
    used: list[str] = []
    text = _rule_synonyms(text, rules, used)
    intent = ""
    top_k = 1
    best_day = _best_day(text)
    if best_day is not None:
        intent, text = "best_day", best_day
    for kind, feature, pattern, extras in () if intent else _INTENTS:
        match = re.search(pattern, text)
        if not match:
            continue
        if feature and not getattr(rules, feature):
            return None
        intent = kind
        if kind == "top_products":
            top_k = int(match.group(1) or 1)
            if not rules.quantity_ranking or not 1 <= top_k <= 100:
                return None
        middle = match.groupdict().get("mid") or ""
        text = text[:match.start()] + " " + middle + " " + text[match.end():]
        for extra in extras:
            text = re.sub(extra, " ", text)
        if feature:
            used.append(feature)
        break
    if not intent:
        return None
    resolved = _period(text, question.as_of_date, rules, used)
    if resolved is None:
        return None
    start, end, leftover = resolved
    # Do not silently ignore product names, payment methods, groups, shifts, etc.
    if _FILLERS.sub(" ", leftover).strip():
        return None
    evidence = rules.source_rows + tuple(
        row for row in dict.fromkeys(rules.row(feature) for feature in used) if row)
    # Explicit periods are capped at the as-of date: future events have not happened.
    return Plan(intent, start, min(end, question.as_of_date), top_k, evidence)


def _rule_synonyms(text: str, rules: Rules, used: list[str]) -> str:
    """Rewrite phrasings that the current rulebook defines as revenue."""
    replacements = (
        ("money_is_revenue", rules.money_is_revenue,
         rf"\b(?:ban(?: ra)?|thu ve|thu)(?: duoc)?(?: tong cong)? {_AMOUNT} tien\b"),
        ("goods_money_is_revenue", rules.goods_money_is_revenue, r"\btien (?:hang|ban)\b"),
    )
    for feature, enabled, pattern in replacements:
        if enabled and re.search(pattern, text):
            text = re.sub(pattern, " doanh thu ", text)
            used.append(feature)
    return text


def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _quarter(year: int, quarter: int) -> tuple[date, date]:
    first, _ = _month(year, 3 * quarter - 2)
    _, last = _month(year, 3 * quarter)
    return first, last


def _period(text: str, today: date, rules: Rules,
            used: list[str] | None = None) -> tuple[date, date, str] | None:
    used = [] if used is None else used
    # (pattern, rule feature that must be enabled or None). Order matters: a date range
    # before a single date, explicit years before implicit ones.
    patterns = (
        (rf"(?:giai doan |khoang )?tu (?:ngay )?{_DATE} (?:den|toi) (?:het )?(?:ngay )?{_DATE}",
         "inclusive_range"),
        (r"thang (\d{1,2})/(\d{4})", None),
        (r"quy ([1-4])/(\d{4})", "calendar_quarter"),
        (rf"ngay {_DATE}", None),
        (r"(\d{1,3}) ngay gan nhat", None),
        (r"(?:tinh )?tu dau nam(?: (?:toi|den) (?:nay|hien tai|(?:het )?hom nay))?", "year_to_date"),
        (r"tu dau thang(?: nay)?(?: (?:toi|den) (?:nay|(?:het )?hom nay))?", "month_to_date"),
        (r"thang nay|thang truoc|tuan nay|tuan truoc|tuan roi|hom nay|hom qua", None),
        (r"thang (\d{1,2})\b(?!/)", "implicit_year"),
        (r"quy ([1-4])\b(?!/)", "implicit_year"),
    )
    for index, (pattern, feature) in enumerate(patterns):
        match = re.search(pattern, text)
        if not match:
            continue
        if feature == "month_to_date":
            enabled = rules.month_to_date
        else:
            enabled = feature is None or getattr(rules, feature)
        if not enabled or (index == 9 and not rules.calendar_quarter):
            return None
        groups = match.groups()
        try:
            if index == 0:
                start = date(int(groups[2]), int(groups[1]), int(groups[0]))
                end = date(int(groups[5]), int(groups[4]), int(groups[3]))
                if start > end:
                    return None
            elif index == 1:
                start, end = _month(int(groups[1]), int(groups[0]))
            elif index == 2:
                start, end = _quarter(int(groups[1]), int(groups[0]))
            elif index == 3:
                start = end = date(int(groups[2]), int(groups[1]), int(groups[0]))
            elif index == 4:
                days = int(groups[0])
                if days < 1:
                    return None
                start, end = today - timedelta(days=days - 1), today
            elif index == 5:
                start, end = date(today.year, 1, 1), today
            elif index == 6:
                start, end = today.replace(day=1), today
            elif index == 7:
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
            else:
                # Month/quarter without a year: this year unless it starts after today.
                number = int(groups[0])
                resolve = _month if index == 8 else _quarter
                start, end = resolve(today.year, number)
                if start > today:
                    start, end = resolve(today.year - 1, number)
        except ValueError:
            return None
        if feature:
            used.append(feature)
            if index == 9:
                used.append("calendar_quarter")
        return start, end, text[:match.start()] + " " + text[match.end():]
    return None
