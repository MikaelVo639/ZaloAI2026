"""Semantics: question parsing and rules from the CURRENT split."""
import calendar
import re
from collections.abc import Iterable
from datetime import date, timedelta

from datamate.contracts import Filter, Plan, Question
from datamate.semantics.rules import Rules
from datamate.semantics.text import normalize_question

_AMOUNT = r"(?:bao nhieu|bao nhiu|bnhiu|bn|nhieu|nhiu|may)"
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
# Words that carry no condition. Anything else left after intent and period is unsupported.
_FILLERS = re.compile(
    r"\b(?:nhung mat hang nao|nhung san pham nao|mat hang nao|"
    r"cho toi biet|cho minh biet|cho minh hoi|cho hoi|cho minh|cho toi|liet ke|top|"
    r"xem giup|coi gium|check gium|"
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


def plan_question(question: Question, rules: Rules, names: Iterable[str] = ()) -> Plan | None:
    """Closed grammar: leftover filters/comparisons cause abstention.

    `names` are product names/codes of the business (from the Agent's data); a question
    naming exactly one of them gets a product filter. Without names, it abstains as before.
    """
    if not rules.revenue:
        return None
    today = question.as_of_date
    text = normalize_question(question.question)
    used: list[str] = []
    filters: list[Filter] = []
    text, product = _product(text, names)
    if product is _AMBIGUOUS:
        return None
    if product:
        if not rules.product_names:
            return None
        filters.append(Filter("product", product, rules.row("product_names") or ""))
        used.append("product_names")
    text = _rule_synonyms(text, rules, used)
    comparison = _comparison(text)
    if comparison is False:
        return None
    reference = None
    if comparison:
        text, reference = comparison
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
    info: dict[str, object] = {}
    resolved = _period(text, today, rules, used, info)
    if resolved is None:
        return None
    start, end, leftover = resolved
    # Do not silently ignore product names, payment methods, groups, shifts, etc.
    if _FILLERS.sub(" ", leftover).strip():
        return None
    compare_start = compare_end = None
    if reference:
        if intent != "revenue" or filters or not rules.change_percent:
            return None
        window = _compare_window(reference, start, end, today, info, rules, used)
        if window is None:
            return None
        compare_start, compare_end = window
        used.append("change_percent")
    evidence = rules.source_rows + tuple(
        row for row in dict.fromkeys(rules.row(feature) for feature in used) if row)
    # Explicit periods are capped at the as-of date: future events have not happened.
    return Plan(intent, start, min(end, today), top_k, evidence, compare_start, compare_end,
                "change_percent" if reference else "", tuple(filters))


_AMBIGUOUS = object()
_PRODUCT_MARKER = re.compile(r"(?:\b(?:mat hang|san pham|hang|mon|thuoc|cua)\s*)+$")


def _product(text: str, names: Iterable[str]) -> tuple[str, object]:
    """Remove the one product the question names; several different products are ambiguous."""
    found = []
    for name in set(names):
        key = normalize_question(name)
        # One-word names ("Trà") are too easy to confuse with grammar; codes carry digits.
        if not key or (len(key.split()) < 2 and not any(c.isdigit() for c in key)):
            continue
        for match in re.finditer(rf"(?<!\S){re.escape(key)}(?!\S)", text):
            found.append((match.start(), match.end(), name))
    if not found:
        return text, None
    chosen: list[tuple[int, int, str]] = []
    for item in sorted(found, key=lambda f: (f[0] - f[1], f[0], f[2])):  # longest first
        if all(item[1] <= c[0] or item[0] >= c[1] for c in chosen):
            chosen.append(item)
    if len({normalize_question(c[2]) for c in chosen}) != 1:
        return text, _AMBIGUOUS
    for start, end, _ in sorted(chosen, reverse=True):
        before = _PRODUCT_MARKER.sub(" ", text[:start])
        text = before + " " + text[end:]
    return text, chosen[0][2]


_COMPARE_WORDS = re.compile(r"\b(?:so voi|so vs|cung ky|cung thang|tang hay giam|tang/giam|"
                            r"tang giam|hon hay kem|thay doi|tang truong|chenh lech)\b")
_CHANGE = re.compile(r"\b(?:tang hay giam|tang/giam|tang giam|hon hay kem|thay doi|tang truong)\b")
_PERCENT = re.compile(rf"\b(?:{_AMOUNT} )?phan tram\b")
_REFERENCES = (
    ("month", re.compile(r"\bso (?:voi|vs) thang (\d{1,2})/(\d{4})\b")),
    ("prev_year", re.compile(r"\b(?:so (?:voi |vs )?)?cung (?:ky|thang) nam (?:ngoai|truoc)\b")),
    ("prev_month", re.compile(r"\b(?:so (?:voi |vs )?)?cung ky thang (?:truoc|roi)\b")),
    ("prev_month", re.compile(r"\bso (?:voi|vs) thang (?:lien truoc|truoc do|truoc|roi)\b")),
    ("prev_month", re.compile(r"\bthang (?:lien truoc|truoc do)\b")),
    ("prev_month", re.compile(r"(?<=hon hay kem )thang (?:truoc|roi)\b")),
)


def _comparison(text: str) -> tuple[str, tuple] | None | bool:
    """None: no comparison; False: comparison words not understood; else (text, reference)."""
    if not _COMPARE_WORDS.search(text):
        return None
    for kind, pattern in _REFERENCES:
        match = pattern.search(text)
        if match:
            break
    else:
        return False
    reference = (kind, *map(int, match.groups()))
    text = text[:match.start()] + " " + text[match.end():]
    change, percent = _CHANGE.search(text), _PERCENT.search(text)
    if not change or not percent:
        return False  # "chênh lệch", "hơn bao nhiêu tiền"... not supported yet
    text = _PERCENT.sub(" ", _CHANGE.sub(" ", text, count=1), count=1)
    if _COMPARE_WORDS.search(text):
        return False  # a second comparison
    return text, reference


def _compare_window(reference: tuple, start: date, end: date, today: date,
                    info: dict[str, object], rules: Rules,
                    used: list[str]) -> tuple[date, date] | None:
    kind = reference[0]
    month_to_date = info.get("kind") == "month_to_date"
    full_month = info.get("kind") == "month" and end <= today  # a running month is ambiguous
    if month_to_date and kind == "prev_month" and rules.same_days_last_month:
        last_month_end = start - timedelta(days=1)
        first = last_month_end.replace(day=1)
        used.append("same_days_last_month")
        return first, min(last_month_end, first.replace(day=1) + timedelta(days=today.day - 1))
    if not full_month:
        return None
    if kind == "prev_month":
        previous = start - timedelta(days=1)
        return _month(previous.year, previous.month)
    if kind == "prev_year":
        return _month(start.year - 1, start.month)
    month, year = reference[1], reference[2]
    try:
        window = _month(year, month)
    except ValueError:
        return None
    return window if window[1] <= today and window != (start, end) else None


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


def _period(text: str, today: date, rules: Rules, used: list[str] | None = None,
            info: dict[str, object] | None = None) -> tuple[date, date, str] | None:
    used = [] if used is None else used
    info = {} if info is None else info
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
        if index in (1, 8):
            info["kind"] = "month"
        elif index == 6 or (index == 7 and match.group() == "thang nay"):
            info["kind"] = "month_to_date"
        return start, end, text[:match.start()] + " " + text[match.end():]
    return None
