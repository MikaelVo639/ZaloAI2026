"""Semantics: question parsing and rules from the CURRENT split."""
import calendar
import re
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from datamate.contracts import Filter, Plan, Question, Threshold
from datamate.semantics.rules import Rules
from datamate.semantics.text import normalize_question

_AMOUNT = r"(?:bao nhieu|bao nhiu|bnhiu|bn|nhieu|nhiu|may)"
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
# Words that carry no condition. Anything else left after intent and period is unsupported.
# Words after "bán được bao nhiêu" that are not a unit of goods.
_NOT_UNITS = frozenset({"tien", "don", "dong", "phan", "khach", "ngay", "lan", "mon", "mat",
                        "san", "loai", "hang", "bill", "order", "nhieu", "nhiu", "may", "bn"})
_FILLERS = re.compile(
    r"\b(?:nhung mat hang nao|nhung san pham nao|mat hang nao|"
    r"cho toi biet|cho minh biet|cho minh hoi|cho hoi|cho minh|cho toi|liet ke|top|"
    r"xem giup|coi gium|check gium|"
    r"giup minh|tinh rieng|tong cong|cua hang|cua shop|la bao nhieu|bao nhieu|bao nhiu|"
    r"bnhiu|nhieu|nhiu|bn|tien|dong|trong|cua|shop|la|co|duoc|nao|nhung|gi|vay|a|nhi|ngay|"
    r"e|z|j|ta|ha|nhe|do|da)\b"
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
     r"(?:(\d+) )?(?:mat hang|san pham|hang|mon)(?: nao)? "
     r"(?:ban chay|ban duoc nhieu|ban duoc nhiu|ban nhieu|ban nhiu|chay) nhat",
     ()),
    ("quantity", "unit_quantity",
     rf"\bban(?: ra| duoc)?(?: tong cong)? {_AMOUNT} (?P<unit>[a-z0-9]+)\b"
     rf"|\bso (?P<unit2>[a-z0-9]+) (?:ban ra|ban duoc|ban)\b",
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


def plan_question(question: Question, rules: Rules, names: Iterable[str] = (),
                  vocabulary: dict[str, Iterable[str]] | None = None) -> Plan | None:
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
    share = False
    group = _group(text, vocabulary or {})
    if group is _AMBIGUOUS:
        return None
    if group:
        text, field, value = group
        if not getattr(rules, field):
            return None
        filters.append(Filter(field, value, rules.row(field) or ""))
        used.append(field)
        text, share = _share(text)
        if share:
            if not getattr(rules, f"{field}_share"):
                return None
            used.append(f"{field}_share")
    text, product = _product(text, names)
    text = re.sub(r"\s+", " ", text).strip()  # removing a name leaves double spaces
    if group and product:
        return None
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
    if share and not re.search(rf"\b{_REVENUE}\b", text):
        text = text + " doanh thu"  # "nhóm X đóng góp bao nhiêu % tổng doanh thu"
    if not reference and not group:
        special = (_months(text, today, rules) or _customers(text, today, rules)
                   or _thresholds(text, today, rules))
        if special is False:
            return None
        if special:
            return special
    intent = ""
    top_k = 1
    best_day = _best_day(text)
    if best_day is not None:
        intent, text = "best_day", best_day
    unit = ""
    for kind, feature, pattern, extras in () if intent else _INTENTS:
        match = re.search(pattern, text)
        if not match:
            continue
        if kind == "quantity":
            unit = match.group("unit") or match.group("unit2")
            if unit in _NOT_UNITS:
                unit = ""
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
    if share:
        # "tháng 1/2025 bằng bao nhiêu % tổng doanh thu tháng 1/2025": the same period twice.
        again = _period(leftover, today, rules)
        if again is not None and again[:2] == (start, end):
            leftover = again[2]
    if group and intent != "revenue":
        return None
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
                "change_percent" if reference else ("share" if share else ""), tuple(filters),
                unit=unit)


def _rows(rules: Rules, *features: str) -> tuple[str, ...]:
    return rules.source_rows + tuple(row for row in dict.fromkeys(rules.row(f) for f in features)
                                     if row)


# ---------------------------------------------------------------- SEM-02: months
_MONTH = r"thang (\d{1,2})/(\d{4})"
_BARE_MONTH = r"(?<![\d/])(\d{1,2})/(\d{4})(?![\d/])"
_COUNT_WORD = {"hai": 2, "ba": 3, "bon": 4, "nam": 5, "sau": 6}
_MONTH_WORDS = re.compile(
    r"\b(?:ghi (?:(?:\d+|ba) so )?(?:lan luot )?theo thu tu (?:thoi gian|do)|"
    r"xet|trong|cac|va|vao|qua|roi|so|cua hang|lan luot|moi thang|tung thang|"
    r"tu thang cu den thang moi|thang nao|o thang nao|ket thuc o|lien nhau|lien tiep|"
    r"(?:\d+|hai|ba|bon|nam|sau) thang|thang)\b")


def _months(text: str, today: date, rules: Rules) -> Plan | None | bool:
    """Several months: their revenues in question order, or the best of them.

    None: not this kind of question; False: understood as several months but not answerable
    under the current rulebook (e.g. "tăng liên tục" has no rule)."""
    text = re.sub(r"\s+", " ", text).strip()
    run = re.search(r"\b(\d+|hai|ba|bon|nam|sau) thang lien (?:nhau|tiep) ket thuc (?:o|vao|tai) "
                    + _MONTH, text)
    span = re.search(r"\btu " + _MONTH + r" (?:den|toi|qua) (?:het )?" + _MONTH, text)
    listed = re.findall(_BARE_MONTH, text)
    if not run and not span and len(listed) < 2:
        return None
    if re.search(r"\blien tuc\b|\btang dan\b|\bgiam dan\b", text):
        return False  # "tăng liên tục": no rulebook definition yet
    if not re.search(rf"\b{_REVENUE}\b", text) and not re.search(r"\bnhieu tien nhat\b", text):
        return None
    months: list[tuple[int, int]] = []
    reduced = text
    if run:
        count = _COUNT_WORD.get(run.group(1)) or int(run.group(1))
        year, month = int(run.group(3)), int(run.group(2))
        for _ in range(count):
            months.insert(0, (year, month))
            month, year = (month - 1, year) if month > 1 else (12, year - 1)
        reduced = text[:run.start()] + " " + text[run.end():]
    elif span:
        if not rules.inclusive_range:
            return False
        year, month = int(span.group(2)), int(span.group(1))
        last = (int(span.group(4)), int(span.group(3)))
        while (year, month) <= last and len(months) <= 24:
            months.append((year, month))
            month, year = (month + 1, year) if month < 12 else (1, year + 1)
        reduced = text[:span.start()] + " " + text[span.end():]
        stated = re.search(r"\b(\d+|hai|ba|bon|nam|sau) thang\b", reduced)
        if stated and (_COUNT_WORD.get(stated.group(1)) or int(stated.group(1))) != len(months):
            return False
    else:
        months = [(int(y), int(m)) for m, y in listed]
        reduced = re.sub(_BARE_MONTH, " ", text)
        stated = re.search(r"\b(\d+|hai|ba|bon|nam|sau) thang\b", reduced)
        if stated and (_COUNT_WORD.get(stated.group(1)) or int(stated.group(1))) != len(months):
            return False
    if len(months) < 2 or len(set(months)) != len(months):
        return False
    best = re.search(r"\bthang nao\b", reduced) and re.search(
        rf"\b(?:cao|lon|nhieu|{_REVENUE}) nhat\b|\bnhieu tien nhat\b", reduced)
    series = re.search(r"\b(?:moi thang|tung thang|lan luot)\b", reduced)
    stated_numbers = re.search(r"\bghi (\d+|ba) so\b", reduced)
    if stated_numbers and (_COUNT_WORD.get(stated_numbers.group(1))
                           or int(stated_numbers.group(1))) != len(months):
        return False
    if bool(best) == bool(series):
        return False
    feature = "month_date" if best else "list_in_question_order"
    if not getattr(rules, feature):
        return False
    reduced = re.sub(r"\bban duoc (?:nhieu|nhiu) tien\b|\b(?:cao|lon|nhieu) nhat\b|\bnhat\b",
                     " ", reduced)
    reduced = re.sub(rf"\b(?:tong )?{_REVENUE}\b", " ", reduced)
    reduced = _MONTH_WORDS.sub(" ", reduced)
    if _FILLERS.sub(" ", reduced).strip():
        return False
    try:
        periods = tuple(_month(y, m) for y, m in months)
    except ValueError:
        return False
    if any(end > today for _, end in periods):
        return False  # a month that is still running or in the future
    used = [feature] + (["inclusive_range"] if span else [])
    return Plan("revenue", min(p[0] for p in periods), max(p[1] for p in periods),
                evidence=_rows(rules, *used), periods=periods,
                shape="best_period" if best else "series")


# ---------------------------------------------------------------- SEM-02: customers
_CUSTOMER = r"(?:khach hang|khach)"
_TOP_CUSTOMERS = re.compile(
    rf"\b(?:ai la )?(?:top )?(?:(\d+) )?{_CUSTOMER}(?: nao)? "
    r"(?P<what>co tong tien mua cao nhat|mua (?:nhieu|nhiu) tien nhat|chi tieu (?:nhieu|nhiu) nhat"
    r"|chi (?:nhieu|nhiu) tien nhat|mua (?:nhieu|nhiu) don(?: hang)? nhat)\b")
_AT_LEAST = re.compile(r"\b(?:mua )?(?:tu (\d+) don(?: hang)? tro len|it nhat (\d+) don(?: hang)?)\b")
_CUSTOMER_WORDS = re.compile(
    rf"\b(?:trong so {_CUSTOMER} (?:da|co) mua(?: hang)?|so {_CUSTOMER} da mua(?: hang)?|"
    rf"so {_CUSTOMER}|{_CUSTOMER}|chiem|t[iy] le|(?:{_AMOUNT} )?phan tram|la (?:nhung )?ai|ai|"
    r"cho cua hang)\b")


def _customers(text: str, today: date, rules: Rules) -> Plan | None | bool:
    if not re.search(rf"\b{_CUSTOMER}\b|\bai la\b", text):
        return None
    top = _TOP_CUSTOMERS.search(text)
    at_least = _AT_LEAST.search(text)
    if not top and not at_least:
        return None
    if not (rules.customer_identity and rules.customers_bought):
        return False
    used = ["customer_identity", "customers_bought"]
    if top:
        rank_by = "order_count" if "don" in top.group("what") else "revenue"
        if rank_by == "revenue":
            if not rules.customer_by_revenue:
                return False
            used.append("customer_by_revenue")
        if not rules.ties_by_code:
            return False
        used.append("ties_by_code")
        top_k = int(top.group(1) or 1)
        reduced = text[:top.start()] + " " + text[top.end():]
        threshold, comparison = None, ""
        intent = "top_customers"
    else:
        number = int(at_least.group(1) or at_least.group(2))
        if at_least.group(1):
            if not rules.threshold_inclusive:
                return False
            used.append("threshold_inclusive")
        reduced = text[:at_least.start()] + " " + text[at_least.end():]
        comparison = "share" if re.search(r"\bphan tram\b|\bt[iy] le\b", reduced) else ""
        if comparison and not re.search(rf"\b(?:so|trong so) {_CUSTOMER} (?:da|co) mua\b|"
                                        rf"\bphan tram {_CUSTOMER}\b", reduced):
            return False  # a share of what? only "of the customers who bought" is defined
        threshold = Threshold("customer", "order_count", ">=", Decimal(number),
                              rules.row("threshold_inclusive") or "")
        top_k, rank_by, intent = 1, "", "count_where"
        reduced = re.sub(rf"\b(?:co )?(?:{_AMOUNT}) {_CUSTOMER}\b", " ", reduced)
    reduced = _CUSTOMER_WORDS.sub(" ", reduced)
    resolved = _period(reduced, today, rules, used)
    if resolved is None:
        return False
    start, end, leftover = resolved
    if _FILLERS.sub(" ", leftover).strip() or not 1 <= top_k <= 100:
        return False
    return Plan(intent, start, min(end, today), top_k, _rows(rules, *used),
                comparison=comparison, threshold=threshold, rank_by=rank_by)


# ---------------------------------------------------------------- SEM-02: thresholds
_MONEY_VALUE = r"(\d{1,3}(?: \d{3})+|\d+)"
_AT_LEAST_MONEY = re.compile(rf"\b(?:dat )?(?:tu )?{_MONEY_VALUE} (?:dong |d |vnd )?tro len\b")
_AT_LEAST_ORDERS = re.compile(r"\b(?:o |xuat hien o )(?:tu )?(\d+) don(?: hang)? khac nhau tro len\b")
_THRESHOLD_SUBJECTS = (
    ("day", re.compile(rf"\b(?:dem )?(?:so |co )?(?:{_AMOUNT} )?ngay\b")),
    ("order", re.compile(rf"\b(?:dem )?(?:so |co )?(?:{_AMOUNT} )?{_ORDER}\b")),
)
_THRESHOLD_WORDS = re.compile(
    rf"\b(?:ma|co|dat|ca ngay|cua hang|ban duoc|ban|tien hang|tien ban|(?:tong )?{_REVENUE}|"
    r"duoc mua|la bao nhieu|la)\b")


def _thresholds(text: str, today: date, rules: Rules) -> Plan | None | bool:
    """"Có mấy đơn / ngày doanh thu từ X đồng trở lên", "mấy mặt hàng được mua ở N đơn khác
    nhau trở lên"."""
    text = re.sub(r"\s+", " ", text).strip()
    orders = _AT_LEAST_ORDERS.search(text)
    money = None if orders else _AT_LEAST_MONEY.search(text)
    if not orders and not money:
        return None
    if not rules.threshold_inclusive:
        return False
    used = ["threshold_inclusive"]
    if orders:
        products = re.search(r"\b(?:co )?(?:{0} )?(?:so )?(?:mat hang|san pham)\b".format(_AMOUNT),
                             text)
        if not products or not rules.customers_bought:  # "Số đơn ... đếm số đơn có một mặt hàng"
            return False
        used.append("customers_bought")
        subject, measure = "product", "order_count"
        value = Decimal(orders.group(1))
        reduced = text[:orders.start()] + " " + text[orders.end():]
        reduced = reduced[:products.start()] + " " + reduced[products.end():] \
            if products.end() <= orders.start() else reduced.replace(products.group(), " ", 1)
    else:
        value = Decimal(money.group(1).replace(" ", ""))
        reduced = text[:money.start()] + " " + text[money.end():]
        subject = next((name for name, pattern in _THRESHOLD_SUBJECTS if pattern.search(reduced)),
                       None)
        if subject is None:
            return False
        if not re.search(rf"\b{_REVENUE}\b|\btien (?:hang|ban)\b|\bban duoc\b", reduced):
            return False
        if re.search(r"\bban duoc\b", reduced) and not re.search(rf"\b{_REVENUE}\b", reduced):
            if not rules.money_is_revenue:
                return False
            used.append("money_is_revenue")
        measure = "revenue"
        reduced = dict(_THRESHOLD_SUBJECTS)[subject].sub(" ", reduced, count=1)
    reduced = _THRESHOLD_WORDS.sub(" ", reduced)
    resolved = _period(reduced, today, rules, used)
    if resolved is None:
        return False
    start, end, leftover = resolved
    if _FILLERS.sub(" ", leftover).strip():
        return False
    threshold = Threshold(subject, measure, ">=", value, rules.row("threshold_inclusive") or "")
    return Plan("count_where", start, min(end, today), 1, _rows(rules, *used), threshold=threshold)


_AMBIGUOUS = object()
_GROUP_PREFIX = {
    "product_group": r"(?:cua )?(?:cac |nhung )?(?:mat hang |san pham |hang )?nhom (?:hang )?",
    "customer_group": r"(?:(?:cac )?don (?:hang )?)?(?:tu |cua )?(?:cac |nhung )?khach(?: hang)? "
                      r"(?:thuoc )?nhom ",
}


def _group(text: str, vocabulary: dict[str, Iterable[str]]) -> tuple | object | None:
    """The one product group / customer group the question names, by the shop's own names."""
    found = []
    for field, prefix in _GROUP_PREFIX.items():
        for name in set(vocabulary.get(field, ())):
            key = normalize_question(name)
            if not key:
                continue
            for match in re.finditer(rf"\b{prefix}{re.escape(key)}(?!\S)", text):
                found.append((match.start(), match.end(), field, name))
    if not found:
        return None
    found.sort(key=lambda f: (f[0] - f[1], f[0]))  # longest first ("trà sữa" before "trà")
    best = found[0]
    same_span = {(f[2], normalize_question(f[3])) for f in found if (f[0], f[1]) == best[:2]}
    elsewhere = [f for f in found if f[1] <= best[0] or f[0] >= best[1]]
    if len(same_span) != 1 or any(normalize_question(f[3]) != normalize_question(best[3])
                                  for f in elsewhere):
        return _AMBIGUOUS  # two groups, or the same words name a product and a customer group
    return text[:best[0]] + " " + text[best[1]:], best[2], best[3]


_SHARE = re.compile(r"\bphan tram\b|\bt[iy] trong\b")
_SHARE_WORDS = re.compile(
    rf"\b(?:trong )?tong (?:{_REVENUE})(?: cua cua hang| cua shop)?\b|\bt[iy] trong\b|"
    rf"\b(?:chiem|dong gop|bang)\b|\bphan cua\b|\b(?:la )?(?:{_AMOUNT} )?phan tram\b")


def _share(text: str) -> tuple[str, bool]:
    """"chiếm / đóng góp bao nhiêu % (tổng) doanh thu (cả cửa hàng)", "tỉ trọng ... trong
    doanh thu": the group's share of the whole shop's revenue."""
    if not (_SHARE.search(text) and re.search(rf"\b{_REVENUE}\b", text)):
        return text, False
    text = re.sub(rf"\b(?:trong )?(?:{_REVENUE}) ca (?:cua hang|shop)\b|\btrong {_REVENUE}\b",
                  " tong doanh thu ", text)
    if not re.search(rf"\btong {_REVENUE}\b", text) and not re.search(r"\bchiem\b", text):
        return text, False
    text = re.sub(rf"\bchiem ((?:{_AMOUNT} )?phan tram) ({_REVENUE})\b", r"chiem \1 tong \2",
                  text)
    return re.sub(r"\s+", " ", _SHARE_WORDS.sub(" ", text)).strip(), True


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
    if rules.hot_is_best_seller and re.search(r"\bhot nhat\b", text):
        # The ranking row lists "hot nhất" as a synonym of "bán chạy nhất".
        text = re.sub(r"\bhot nhat\b", "ban chay nhat", text)
        used.append("hot_is_best_seller")
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
        (r"(?:tinh )?tu dau nam(?: (?:toi|den) (?:nay|hien tai|(?:het )?hom nay))?"
         r"|(?:tinh )?tu (?:ngay )?1/1(?: nam nay)? (?:toi|den) (?:het )?(?:ngay )?hom nay",
         "year_to_date"),
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
        leftover = text[:match.start()] + " " + text[match.end():]
        if index == 5:
            # "từ 1/1 đến hết hôm nay, doanh thu năm nay": "năm nay" repeats the same year.
            leftover = re.sub(r"\bnam nay\b", " ", leftover, count=1)
        return start, end, leftover
    return None
