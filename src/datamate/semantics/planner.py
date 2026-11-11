"""Semantics: question parsing and rules from the CURRENT split."""
import calendar
import re
import unicodedata
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from datamate.contracts import Filter, Plan, Question, Threshold
from datamate.semantics.rules import DEBT_TABLE_FEATURES, Rules
from datamate.semantics.text import fold, normalize_question

_AMOUNT = r"(?:bao nhieu|bao nhiu|bnhiu|bn|nhieu|nhiu|may)"
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
# Words that carry no condition. Anything else left after intent and period is unsupported.
# Words after "bán được bao nhiêu" that are not a unit of goods.
_NOT_UNITS = frozenset({"tien", "don", "dong", "khach", "ngay", "lan", "mon", "mat",
                        "san", "loai", "hang", "bill", "order", "nhieu", "nhiu", "may", "bn"})
_FILLERS = re.compile(
    r"\b(?:nhung mat hang nao|nhung san pham nao|mat hang nao|"
    r"cho toi biet|cho minh biet|cho minh hoi|cho hoi|cho minh|cho toi|liet ke|top|"
    r"(?:xem|coi|check) giup|"
    r"giup minh|tinh rieng|tinh|tong cong|cong lai|cua hang|cua shop|la bao nhieu|bao nhieu|bao nhiu|"
    r"bnhiu|nhieu|nhiu|bn|tien|dong|trong|cua|shop|la|co|duoc|nao|nhung|gi|vay|a|nhi|ngay|"
    r"e|z|j|ta|ha|nhe|do|da)\b"
)
_ORDER = r"(?:don|bill|order)(?: hang)?"
_REVENUE = r"(?:doanh thu|dthu)"
_ONE = r"(?:mot|moi|1)"
_AVERAGE = r"(?:trung binh|binh quan)"
# Money on one order, after "trung bình mỗi đơn": "được bao nhiêu doanh thu", "hết bao
# nhiêu tiền", "giá trị bao nhiêu". Without a money word ("mấy món") it is not this measure.
# A bare "doanh thu" is what the rulebook's "thu được bao nhiêu tiền" row has become.
_MONEY_PER_ORDER = (
    rf"(?:(?:co |la |duoc |dat |het |mua |mang ve |mang lai |thu duoc |ban duoc )*"
    rf"{_AMOUNT} (?:tien|{_REVENUE})|(?:gia tri|tri gia|co gia tri)(?: la| duoc)? {_AMOUNT}"
    rf"|(?:co |la |duoc )?{_REVENUE}(?:(?: la| duoc)? {_AMOUNT})?)\b")
_GROUP_AVERAGE = (
    rf"\btrung binh {_ONE} {_ORDER} (?:la )?{_AMOUNT} {_REVENUE}\b"
    rf"|\b(?:mua )?trung binh {_AMOUNT} {_REVENUE} {_ONE} {_ORDER}\b"
)
_UNSOLD = re.compile(
    r"\b(?:mat hang|san pham|hang|mon) (?:(?:bi )?(?:e|ees)(?: han| den muc)?|van dang ban ma)\b"
    r"|\b(?:khong|ko|k|hong|hok) ban (?:ra )?(?:duoc|dc|d c) (?:cai|mon|lan) nao\b")
_DELIVERY = re.compile(r"\bdot (?:giao hang|hang)\b")
_PROFIT = re.compile(r"\b(?:lai|loi(?: nhuan)?) (?:gop|rong|nhat)\b")
_EARLIEST = re.compile(r"\b(?:gan het han nhat|can date nh(?:at|ut))\b")
_STOCK_VALUE = re.compile(
    r"\bgia tri (?:hang ton kho|hang con trong kho)\b"
    rf"|\b(?:tien hang nam trong kho|so hang con trong kho dang {_AMOUNT} tien)\b"
)
_EXPIRY = re.compile(r"\bhet han (?:trong|tu)\b|\b(?:tien von|lo con ton) .*\bhan dung tu\b"
                     r"|\bcon (?:hang|ton(?: kho)?) co han dung tu\b")


def _package_period(text: str, today: date, rules: Rules) -> Plan | bool:
    """Period measures with a closed grammar for their respective units and qualifiers."""
    if _UNSOLD.search(text):
        if not re.search(rf"\b(?:{_AMOUNT}|dem|so)\b", text):
            return True
        intent, features = "unsold_count", ("unsold_count", "product_names")
        remove = (
            r"\b(?:mat hang|san pham|hang|mon)"
            r"(?: (?:(?:bi )?(?:e|ees)(?: han| den muc)?|ban cham den muc|van dang ban ma))?\b"
            r"|\b(?:ban )?(?:khong|ko|k|hong|hok) (?:ai mua|ban[ ]?(?:ra )?(?:duoc|dc|d c)|duoc|dc|d c)"
            r" (?:cai|mon|lan) nao\b|\b(?:dem(?: giup toi)? so|so|total|may|ta)\b")
    elif _DELIVERY.search(text):
        if not re.search(rf"\b(?:{_AMOUNT}|dem|so)\b", text):
            return True
        intent, features = "delivery_count", ("delivery_count", "calendar_month")
        remove = (r"\bdot (?:giao hang|hang)\b|\b(?:xe|tiem|cho giao|cho khach|cho duoc|giao|"
                  r"dem(?: so)?|so|total|may)\b")
    elif _PROFIT.search(text):
        text = re.sub(r"\bloi nhuan (?=gop\b|rong\b)", "loi ", text)
        text = re.sub(r"\b(?:lai|loi) gop (?:ca thang )?(?:cao|nhieu|lon) nh(?:at|ut)\b",
                      "lai gop cao nhat", text)
        if re.search(r"\b(?:lai|loi) nhat\b|\b(?:lai|loi) gop cao nhat\b", text):
            intent, features = "top_gross_profit", ("gross_profit", "top_gross_profit",
                                                     "product_names", "calendar_month")
            if rules.ties_by_code:
                features += ("ties_by_code",)
        elif re.search(r"\b(?:lai|loi) rong\b", text):
            intent, features = "net_profit", ("gross_profit", "net_profit", "calendar_month")
        else:
            intent, features = "gross_profit", ("gross_profit", "calendar_month")
        remove = (r"\b(?:mat hang|san pham|hang mo|hang|mon|tong|mang ve|"
                  r"(?:lai|loi) (?:gop(?: cao nhat)?|rong|nhat)|"
                  r"tru het chi phi(?: roi thi)?|tinh ca thang thi|quan|tiem|con|rua|ta)\b")
    else:
        return False
    if not all(getattr(rules, name) for name in features):
        return True
    if intent != "delivery_count" and not rules.revenue:
        return True
    if intent != "unsold_count" and not re.search(r"\bthang (?:\d|nay\b|truoc\b)", text):
        return True
    text = re.sub(r"\bthang (\d{1,2}) nam (\d{4})\b", r"thang \1/\2", text)
    used = list(features)
    resolved = _period(text, today, rules, used)
    if resolved is None:
        return True
    start, end, leftover = resolved
    leftover = re.sub(remove, " ", re.sub(r"\s+", " ", leftover))
    if _FILLERS.sub(" ", leftover).strip() or start > today:
        return True
    return Plan(intent, start, min(end, today), evidence=_rows(rules, *used))


_TOP_SELLING = r"(?:ban chay|ban duoc nhieu|ban duoc nhiu|ban nhieu|ban nhiu|chay) nh(?:at|ut)"


# (intent, rule feature that must be enabled, pattern, fragments also removed). First match
# wins, so specific intents come before the generic "doanh thu" / "số đơn" ones.
_INTENTS = (
    ("cancel_rate", "cancel_rate",
     rf"t[iy] le (?:{_ORDER} )?(?:bi )?huy(?: {_ORDER})?"
     rf"|(?:co )?{_AMOUNT} phan tram {_ORDER} (?:bi |da )?huy",
     (rf"\b(?:{_AMOUNT} )?phan tram\b",)),
    ("cancel_count", "cancelled_order",
     rf"(?:dem )?so {_ORDER} (?:bi |da )?huy"
     rf"|(?:co )?{_AMOUNT} {_ORDER} (?:bi huy|da huy|o trang thai huy|huy\b)"
     rf"|(?<!khong )(?<!chua )(?<!ko )(?:bi )?huy(?: mat)? {_AMOUNT} {_ORDER}"
     rf"|(?:dem )?{_ORDER} (?:bi )?huy (?:duoc |la |co )?{_AMOUNT}\b(?! phan tram)", ()),
    ("average_order", "average_order",
     rf"gia tri trung binh {_ONE} {_ORDER}"
     rf"|(?:gia tri|{_REVENUE}) {_AVERAGE} (?:tren )?{_ONE} {_ORDER}"
     rf"|trung binh {_ONE} {_ORDER}(?P<mid>.*?) mang ve {_AMOUNT} {_REVENUE}"
     rf"|(?:khach (?:hang )?mua )?{_AVERAGE} (?:tren )?{_ONE} {_ORDER}(?P<mid2>.*?)"
     rf" {_MONEY_PER_ORDER}"
     rf"|{_ONE} {_ORDER} {_AVERAGE}(?P<mid3>.*?) {_MONEY_PER_ORDER}"
     rf"|{_AVERAGE} {_ORDER}(?P<mid4>.*?) (?:gia tri|tri gia) {_AMOUNT}\b", ()),
    ("top_products", None,
     # Keep a period year separate from the requested ranking count.
     r"(?:(?P<count>(?<![/\d])\d+|(?<=\btop )(?:hai|ba|bon|nam|sau)) )?"
     r"(?:mat hang|san pham|hang|mon)(?: nao)? "
     rf"{_TOP_SELLING}\b",
     (r"\bla (?:nhung )?(?:mon|hang) nao$",)),
    ("quantity", "unit_quantity",
     rf"\bban(?: ra)?(?: duoc)?(?: tong cong)? {_AMOUNT} (?P<unit>[a-z][a-z0-9]*)\b"
     rf"|\bso (?P<unit2>[a-z0-9]+) (?:ban ra|ban duoc|ban)\b",
     ()),
    ("revenue", None, rf"(?:tong )?{_REVENUE}", ()),
    ("order_count", None,
     rf"(?:dem )?(?:tong )?so (?:luong )?{_ORDER}(?: ban)?"
     rf"|(?:ban(?: ra)?(?: duoc)? |co )?(?:tong cong )?{_AMOUNT} {_ORDER}",
     ()),
)


_GROUP_PRODUCT_RANK = (
    r"(?:(?<![/\d])(?P<group_count>\d+|(?<=\btop )(?:hai|ba|bon|nam|sau)) )?"
    r"(?:mat hang|san pham|hang|mon)(?: nao)? "
    r"(?:(?P<by_revenue>(?:co )?(?:doanh thu|dthu) (?:(?:cao|lon|nhieu) )?nh(?:at|ut))"
    r"|ban (?:duoc )?(?:nhieu|nhiu) (?P<rank_unit>[a-z]+) nh(?:at|ut)"
    rf"|{_TOP_SELLING})\b"
)
_DAY_QUESTION = re.compile(r"\b(?:(?:roi )?vao )?(?:ngay ?|hom |bua )(?:nao|mo)\b")


def _best_day(text: str) -> str | None:
    """'Ngày nào doanh thu cao nhất' (also after 'bán được nhiều tiền' became 'doanh thu')."""
    reduced, matched = _DAY_QUESTION.subn(" ", text, count=1)
    if not matched:
        return None
    revenue = re.search(rf"\b(?:(?:xet|tinh) theo |dat )?{_REVENUE}\b", reduced)
    if not revenue:
        return None
    reduced = reduced[:revenue.start()] + " " + reduced[revenue.end():]
    # "thấp nhất" leaves "thap" behind, so the leftover check rejects it.
    best = re.search(r"\b(?:(?:ban dat|(?:len )?cao|nhieu) )?nh(?:at|ut)\b", reduced)
    if not best:
        return None
    rest = reduced[:best.start()] + " " + reduced[best.end():]
    return re.sub(r"\bngay ma\b", " ", rest, count=1)


def plan_question(question: Question, rules: Rules, names: Iterable[str] = (),
                  vocabulary: dict[str, Iterable[str]] | None = None, *,
                  price_increases: dict[tuple[str, Decimal], date] | None = None,
                  price_source: str = "", everyday_wording: bool = True) -> Plan | None:
    """Closed grammar: leftover filters/comparisons cause abstention.

    `names` are product names/codes of the business (from the Agent's data); a question
    naming exactly one of them gets a product filter. Without names, it abstains as before.
    """
    names = tuple(names)
    text = normalize_question(question.question)
    plan = _plan_question(question, text, rules, names, vocabulary, price_increases, price_source)
    if plan is not None or not everyday_wording:
        return plan
    labels = tuple(label for field in ("product_group", "customer_group")
                   for label in (vocabulary or {}).get(field, ()))
    rewritten = normalize_question(question.question, product_names=names + labels,
                                   as_of_year=question.as_of_date.year)
    if rewritten == text:
        return None
    return _plan_question(question, rewritten, rules, names, vocabulary, price_increases,
                          price_source)


def _plan_question(question: Question, text: str, rules: Rules, names: tuple[str, ...],
                   vocabulary: dict[str, Iterable[str]] | None,
                   price_increases: dict[tuple[str, Decimal], date] | None,
                   price_source: str) -> Plan | None:
    today = question.as_of_date
    if re.search(r"\b(?:giao|cho)(?: thanh)? (?:tu )?(?:\d+|hai) dot tro len\b"
                 r"|\b(?:giao|cho) it nhat (?:\d+|hai) dot\b", text):
        return _delivered_orders(text, today, rules)
    if "ngay phai tra" in text and "don ban chiu" in text:
        return _credit_due_count(text, today, rules)
    package = _package_period(text, today, rules)
    if package:
        return package if isinstance(package, Plan) else None
    if _EARLIEST.search(text) or _STOCK_VALUE.search(text) or _EXPIRY.search(text):
        return _package_snapshot(text, today, rules, names)
    used: list[str] = []
    filters: list[Filter] = []
    share = False
    if _PURCHASE_TOTAL.search(text) or _TOP_SUPPLIER.search(text) or _MAIN_SUPPLIER.search(text):
        return _purchase(text, today, rules)
    if _AVG_PURCHASE_CUE.search(text):
        return _avg_purchase(text, today, rules, names)
    if _PRICE_UP.search(text):
        return _price_effect(text, today, rules, names, price_increases or {}, price_source)
    if rules.daily_sale_price:
        price = _daily_sale_price(text, today, rules, names)
        if price is not None:
            return price
    if _DRUG_CUE.search(text):
        return _drug_revenue(question, rules)
    phone = _phone_average(text, today, rules)
    if phone is not None:
        return phone if isinstance(phone, Plan) else None
    if re.search(r"\bhãng\b", unicodedata.normalize("NFC", question.question).casefold()):
        brand = _brand_ranking(text, today, rules, vocabulary or {})
        if brand is not None:
            return brand if isinstance(brand, Plan) else None
    phone_scope = _phone_scope(text, today, rules, names, vocabulary or {})
    if phone_scope is not None:
        return phone_scope if isinstance(phone_scope, Plan) else None
    catalogue_quantity = _catalogue_quantity(text, today, rules, vocabulary or {})
    if catalogue_quantity is not None:
        return catalogue_quantity if isinstance(catalogue_quantity, Plan) else None
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
    pair = False
    labelled = _labels(text, vocabulary or {})
    if labelled is False:
        return None
    if labelled:
        if group:
            return None
        text, clauses = labelled
        for field, value in clauses:
            if field == "payment" and not rules.payment_revenue:
                return None
            filters.append(Filter(field, value, rules.row("payment_revenue") or ""
                                  if field == "payment" else ""))
        if clauses[0][0] == "payment":
            used.append("payment_revenue")
        if len(clauses) == 2:
            if not rules.list_in_question_order:
                return None
            used.append("list_in_question_order")
            if not re.search(rf"\b{_REVENUE}\b", text) or _SHARE.search(text):
                return None
            pair = True
            text = _PAIR_WORDS.sub(" ", text)
            text = re.sub(rf"\b{_REVENUE}\b", " ", text) + " doanh thu"
        else:
            text, share = _share(text)
    text, product = _product(text, names)
    text = re.sub(r"\s+", " ", text).strip()  # removing a name leaves double spaces
    if group and product:
        return None
    if product is _AMBIGUOUS:
        return None
    if product:
        # The named item may occur between the sale verb and the amount.
        text = re.sub(rf"\bban(?= {_REVENUE}\b)", "", text).strip()
        if not rules.product_names:
            return None
        filters.append(Filter("product", product, rules.row("product_names") or ""))
        used.append("product_names")
    if product and _STOCK_CUE.search(text):
        return _stock_plan(text, today, rules, filters)
    if product and _PURCHASE_DIFFERENCE.search(text):
        return _purchase_difference(text, today, rules, filters)
    size = re.search(r"\bsize (m|l)\b", text)
    if size:
        if filters or not rules.size_by_name or len(re.findall(r"\bsize\b", text)) != 1:
            return None
        filters.append(Filter("size", size.group(1).upper(), rules.row("size_by_name") or ""))
        used.append("size_by_name")
        text = text[:size.start()] + " " + text[size.end():]
        # "cộng tất cả các món", "tính mọi loại đồ uống"; "quán" as the subject of "bán".
        text = re.sub(r"\b(?:cong|tinh) (?:tat ca cac|tat ca|moi loai|moi|cac) (?:mon|do uong)\b",
                      " ", text)
        text = re.sub(r"\bquan(?= ban\b)", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    staff = _staff(text, (vocabulary or {}).get("staff_terms", ()), rules.money_is_revenue)
    if staff is False or (staff and filters):
        return None  # several staff codes, or a second filter
    if staff:
        text, code = staff
        filters.append(Filter("staff", code, ""))
    shift = _shift(text, rules)
    if shift is False or (shift and filters):
        return None  # shift words not understood, or a second filter
    shift_compare = False
    if shift:
        text, name, shift_compare = shift
        filters.append(Filter("shift", name, rules.row("shift") or ""))
        used.append("shift")
    before_synonyms = text  # "thu được bao nhiêu tiền khách trả nợ" is not revenue
    text = re.sub(r"\s+", " ", _rule_synonyms(text, rules, used)).strip()
    comparison = _comparison(text)
    if comparison is False:
        return None
    reference = None
    if comparison:
        text, reference = comparison
    if group:
        text = re.sub(r"\b(?:ca|ka) (?:cua hang|shop) thanks(?: e| em)?$", "", text)
        text = re.sub(rf"\b(?:mua(?: tong(?: cong)?)?|mang ve)(?= (?:{_AMOUNT}|{_REVENUE})\b)",
                      " ", text)
        text = re.sub(r"\b((?:so|may|bao nhieu) don) co\b", r"\1", text)
    if share and not re.search(rf"\b{_REVENUE}\b", text):
        text = text + " doanh thu"  # "nhóm X đóng góp bao nhiêu % tổng doanh thu"
    contract = _increasing(text, today, rules)
    if contract is None:
        contract = _debt_table(text, today, rules)
    if contract is None:
        contract = _debt_measure(before_synonyms, today, rules)
    if contract is not None:
        # C01 intents take no filter, group, share or comparison: anything else abstains.
        return contract if contract and not (filters or group or reference) else None
    if not rules.revenue:
        return None
    if not reference and not group and not shift and not staff:
        special = (_months(text, today, rules) or _customers(text, today, rules)
                   or _thresholds(text, today, rules))
        if special is False:
            return None
        if special:
            if filters and (special.periods or special.intent == "top_customers" or (
                    special.threshold is not None and special.threshold.subject == "customer")):
                return None
            return special
    intent = ""
    top_k = 1
    best_day = _best_day(text)
    best_staff = _BEST_STAFF.search(text)
    if best_staff and not filters and not reference:
        if "gioi nhat" in best_staff.group() and not rules.best_staff:
            return None  # "giỏi nhất" means revenue only when the rulebook says so
        if best_staff.group().startswith("tho ") and not (vocabulary or {}).get("repair_staff"):
            return None
        intent = "top_staff"
        text = text[:best_staff.start()] + " " + text[best_staff.end():]
        text = re.sub(r"\b(?:la ai|ai la)\b", " ", text)
        if rules.best_staff:
            used.append("best_staff")
    elif best_day is not None:
        intent, text = "best_day", best_day
    unit = rank_by = ""
    product_group = bool(group and group[1] == "product_group")
    for kind, feature, pattern, extras in () if intent else _INTENTS:
        if kind == "average_order" and group and group[1] == "customer_group":
            pattern = rf"(?:{pattern}|{_GROUP_AVERAGE})"
        if kind == "top_products":
            if product_group:
                pattern = rf"(?:\bthi )?{_GROUP_PRODUCT_RANK}"
            if re.search(r"\btop \d+\b", text):
                pattern = rf"\btop {pattern}(?: la chi\b)?"
            elif not product_group:
                pattern = rf"(?:{pattern}|(?<![/\d])\b(?P<english_count>\d+) best sellers?\b)"
        match = re.search(pattern, text)
        if not match:
            continue
        if kind == "quantity":
            unit = match.group("unit") or match.group("unit2")
            if unit in _NOT_UNITS or (unit == "phan" and
                                       text[match.end():].startswith(" tram")):
                unit = ""
                continue
        if feature and not getattr(rules, feature):
            return None
        intent = kind
        if kind == "top_products":
            parts = match.groupdict()
            count = parts.get("group_count") or parts.get("count") or parts.get("english_count") or "1"
            top_k = int(count) if count.isdecimal() else _COUNT_WORD[count]
            unit = parts.get("rank_unit") or ""
            rank_by = "revenue" if parts.get("by_revenue") or unit in {"tien", "dong"} else ""
            if rank_by:
                unit = ""
            if not 1 <= top_k <= 100 or (not rank_by and not rules.quantity_ranking):
                return None
            if product_group:
                if not (rules.product_names and rules.ties_by_code):
                    return None
                used.extend(("product_names", "ties_by_code"))
            if unit:
                if unit in _NOT_UNITS or not rules.unit_quantity:
                    return None
                used.append("unit_quantity")
        middle = " ".join(value for key, value in match.groupdict().items()
                          if key.startswith("mid") and value)
        text = text[:match.start()] + " " + middle + " " + text[match.end():]
        if kind == "quantity":
            if not filters:
                text = re.sub(rf"\b(?:cong moi (?:mat hang|mon) tinh theo"
                              rf"|tinh (?:het|tat ca) (?:cac )?mat hang ban theo) {re.escape(unit)}\b",
                              " ", text)
            if size:
                text = re.sub(r"\bcong (?:het|tat ca) (?:cac )?mon\b", " ", text)
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
    if intent == "revenue":
        leftover = re.sub(rf"\b{_AMOUNT} dong\b", " ", leftover)
    if labelled and intent != "revenue":
        return None
    if group and intent != "revenue" and (
            share or (group[1], intent) not in {
                ("customer_group", "average_order"), ("product_group", "top_products"),
                ("product_group", "order_count"), ("product_group", "quantity")
            }):
        return None
    if (shift or staff) and intent not in ("revenue", "order_count"):
        return None
    if size and intent != "quantity":
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
                "change_percent" if reference else ("shift_change_percent" if shift_compare
                                                    else ("share" if share else "")),
                tuple(filters),
                unit=unit, shape="by_filter" if pair else "", rank_by=rank_by)


def _rows(rules: Rules, *features: str) -> tuple[str, ...]:
    return rules.source_rows + tuple(row for row in dict.fromkeys(rules.row(f) for f in features)
                                     if row)


def lot_product_question(question: Question) -> bool:
    return bool(_EARLIEST.search(normalize_question(question.question)))


def _package_snapshot(text: str, today: date, rules: Rules,
                      names: Iterable[str]) -> Plan | None:
    stock_value = bool(_STOCK_VALUE.search(text))
    snapshot_words = r"theo ton kho ngay|tinh den het ngay|tinh tai ngay|cuoi ngay"
    if stock_value:
        snapshot_words += r"|het ngay|vao cuoi ngay"
    snapshot = re.search(rf"\b(?:{snapshot_words}) {_DATE}\b", text)
    if snapshot:
        try:
            day, month, year = map(int, snapshot.groups())
            if date(year, month, day) != today:
                return None
        except ValueError:
            return None
        text = text[:snapshot.start()] + " " + text[snapshot.end():]
    filters = ()
    end = today
    if _EARLIEST.search(text):
        if not (rules.earliest_expiry and rules.product_names):
            return None
        text, product = _product(text, names)
        if not product or product is _AMBIGUOUS:
            return None
        filters = (Filter("product", product, rules.row("product_names") or ""),)
        intent, features = "earliest_expiry", ("earliest_expiry", "product_names")
        remove = (r"\b(?:con lo|lo|gan het han nhat|can date nh(?:at|ut)|"
                  r"het han ngay (?:nao|may)|han dung|han su dung|hsd|ngay may|hom nay)\b")
    elif stock_value:
        if not (rules.stock_value and rules.until_end_of_day):
            return None
        intent, features = "stock_value", ("stock_value", "until_end_of_day")
        text = _STOCK_VALUE.sub(" ", text)
        remove = r"\b(?:tong|theo gia von|het hom nay|cuoi ngay hom nay|hom nay)\b"
    else:
        if not rules.expiring_lots:
            return None
        features = ("expiring_lots",)
        if "tien von" in text:
            intent = "expiring_lot_cost"
        elif re.search(r"\b(?:may|bao nhieu|bao nhiu|so) lo\b", text):
            intent = "expiring_lot_count"
        elif re.search(rf"\b{_AMOUNT} mat hang\b", text):
            intent = "expiring_product_count"
        else:
            if not rules.expiry_list_order:
                return None
            intent = "expiring_products"
            features += ("expiry_list_order",)
        horizon = re.search(r"\btrong (\d{1,3}) ngay toi\b", text)
        explicit = re.search(
            rf"\btu (?:(?:ngay )?{_DATE}|nay) den (?:het )?(?:ngay )?{_DATE}\b", text)
        if not horizon and not explicit:
            return None
        if horizon:
            end = today + timedelta(days=int(horizon.group(1)))
        if explicit:
            d1, m1, y1, d2, m2, y2 = explicit.groups()
            try:
                first = date(int(y1), int(m1), int(d1)) if d1 else today
                last = date(int(y2), int(m2), int(d2))
            except ValueError:
                return None
            if not rules.inclusive_range or first != today or last < today or (horizon and last != end):
                return None
            end = last
            features += ("inclusive_range",)
            text = text[:explicit.start()] + " " + text[explicit.end():]
        text = re.sub(r"\btrong \d{1,3} ngay toi\b", " ", text)
        remove = (r"\b(?:tien von dang nam o cac lo con ton co han dung|"
                  r"(?:may|bao nhieu|bao nhiu|so) lo(?: hang)? con ton se het han|"
                  rf"{_AMOUNT} mat hang con lo ton sap het han|tuc han dung|"
                  r"(?:liet )?ke giup cac san pham con lo ton het han|"
                  r"(?:mat )?hang nao het han|ma hien van con ton kho|"
                  r"(?:dang )?con (?:hang|ton(?: kho)?) co han dung|san pham nao|"
                  r"hang se het han|con ton kho|con ton|tien von|hom nay)\b")
    text = re.sub(remove, " ", re.sub(r"\s+", " ", text))
    if _FILLERS.sub(" ", text).strip():
        return None
    return Plan(intent, today, end, evidence=_rows(rules, *features), filters=filters)


# ---------------------------------------------------------------- SEM-02: months
_MONTH = r"thang (\d{1,2})/(\d{4})\b(?!/)"
_BARE_MONTH = r"(?<![\d/])(\d{1,2})/(\d{4})(?![\d/])"
_COUNT_WORD = {"hai": 2, "ba": 3, "bon": 4, "nam": 5, "sau": 6}
_MONTH_PREFIX = r"(?:thang |(?:thg|th|t) ?)"
_MONTH_NUMBER = r"(?:0?[1-9]|1[0-2])"
_SHARED_YEAR = re.compile(
    rf"\b({_MONTH_PREFIX}{_MONTH_NUMBER}(?: (?:va )?(?:{_MONTH_PREFIX})?{_MONTH_NUMBER})*)"
    rf" (?:va )?(?:{_MONTH_PREFIX})?({_MONTH_NUMBER})(?:/| nam )(\d{{4}})\b(?!/)")
_CHRONOLOGICAL = re.compile(
    r"\b(?:(?:ghi (?:(?:\d+|ba) so )?(?:lan luot )?)?theo thu tu thoi gian|"
    r"(?:xep )?tu thang cu (?:den|toi) thang (?:moi|gan nhat))\b")
_SERIES_MONTHS = re.compile(
    r"\b(?:moi thang|tung thang|(?:ke )?lan luot|"
    r"ghi (?:(?:\d+|ba) so )?(?:lan luot )?theo (?:dung )?thu tu do)\b")
_MONTH_WORDS = re.compile(
    r"\b(?:xet|giua|thi|trong|cac|va|hay|vao|qua|roi|so|cua hang|"
    r"thang nao|o thang nao|ket thuc o|lien nhau|lien tiep|lien|"
    r"(?:\d+|hai|ba|bon|nam|sau) thang|thang)\b")


def _months(text: str, today: date, rules: Rules) -> Plan | None | bool:
    """Several months: their revenues in question order, or the best of them.

    None: not this kind of question; False: understood as several months but not answerable
    under the current rulebook (e.g. "tăng liên tục" has no rule)."""
    text = re.sub(r"\s+", " ", text).strip()
    text = _SHARED_YEAR.sub(
        lambda m: " ".join(f"thang {number}/{m[3]}"
                           for number in re.findall(r"\d+", f"{m[1]} {m[2]}")), text)
    run = re.search(r"\b(\d{1,6}|hai|ba|bon|nam|sau) thang lien (?:nhau|tiep) "
                    r"(?:ket thuc (?:o|vao|tai)|tinh (?:den|toi)) " + _MONTH, text)
    span = re.search(rf"\btu {_MONTH} (?:den|toi|qua) (?:het )?{_MONTH}", text)
    if not span:
        span = re.search(rf"\b{_MONTH} (?:den|toi) (?:het )?{_MONTH}", text)
    listed = re.findall(_BARE_MONTH, text)
    if not run and not span and len(listed) < 2:
        return None
    if re.search(r"\blien tuc\b|\btang dan\b|\bgiam dan\b", text):
        return False  # "tăng liên tục": no rulebook definition yet
    money_best = re.search(r"\b(?:nhieu|nhiu) tien nhat\b", text)
    if not re.search(rf"\b{_REVENUE}\b", text) and not money_best:
        return None
    months: list[tuple[int, int]] = []
    reduced = text
    if run:
        count = _COUNT_WORD.get(run.group(1)) or int(run.group(1))
        year, month = int(run.group(3)), int(run.group(2))
        if not 1 <= month <= 12 or not 2 <= count <= 12 * (year - 1) + month:
            return False
        for _ in range(count):
            months.append((year, month))
            month, year = (month - 1, year) if month > 1 else (12, year - 1)
        months.reverse()
        reduced = text[:run.start()] + " " + text[run.end():]
    elif span:
        if not rules.inclusive_range:
            return False
        month, year, last_month, last_year = map(int, span.groups())
        last = (last_year, last_month)
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
    best = re.search(r"\bthang nao\b", reduced) and (money_best or re.search(
        rf"\b(?:cao|lon|nhieu|{_REVENUE}) nhat\b", reduced))
    chronological = _CHRONOLOGICAL.search(reduced)
    direct_list = (not run and not span and re.match(
        rf"^(?:cho|cko)(?: (?:em|minh|toi))? xin {_REVENUE}\b", text))
    series = _SERIES_MONTHS.search(reduced) or chronological or direct_list
    stated_numbers = re.search(r"\bghi (\d+|ba) so\b", reduced)
    if stated_numbers and (_COUNT_WORD.get(stated_numbers.group(1))
                           or int(stated_numbers.group(1))) != len(months):
        return False
    if bool(best) == bool(series):
        return False
    feature = "month_date" if best else "list_in_question_order"
    if not getattr(rules, feature):
        return False
    if best:
        reduced = re.sub(r"^so sanh\b|\bxem (?=thang nao\b)", " ", reduced)
        reduced = re.sub(r"^minh dang xem lai mua (?:he|xuan|thu|dong) (?=giua\b)", " ", reduced)
    if direct_list:
        reduced = re.sub(r"^(?:cho|cko)(?: (?:em|minh|toi))? xin\b", " ", reduced)
    reduced = re.sub(r"^ke giup\b|\bgiup$", " ", reduced.strip())
    reduced = _SERIES_MONTHS.sub(" ", _CHRONOLOGICAL.sub(" ", reduced))
    reduced = re.sub(r"\bban duoc (?:nhieu|nhiu) tien\b|\b(?:cao|lon|nhieu) nhat\b|\bnhat\b",
                     " ", reduced)
    reduced = re.sub(rf"\b(?:tong )?{_REVENUE}\b", " ", reduced)
    reduced = _MONTH_WORDS.sub(" ", reduced)
    if _FILLERS.sub(" ", reduced).strip():
        return False
    try:
        periods = tuple(_month(y, m) for y, m in (sorted(months) if chronological else months))
    except ValueError:
        return False
    if any(end > today for _, end in periods):
        return False  # a month that is still running or in the future
    used = [feature] + (["inclusive_range"] if span else [])
    if money_best and rules.money_is_revenue:
        used.append("money_is_revenue")
    return Plan("revenue", min(p[0] for p in periods), max(p[1] for p in periods),
                evidence=_rows(rules, *used), periods=periods,
                shape="best_period" if best else "series")


# ---------------------------------------------------------------- S01: revenue_increasing
# Contract C01 §4: "doanh thu có tăng liên tục qua các tháng ...", answered by a boolean.
_TREND = re.compile(
    r"\b(?:co )?tang lien tuc\b"
    r"|\b(?:co )?thang sau (?:co )?(?:luon|deu) (?:cao )?hon thang truoc\b")
_PAIR = re.compile(
    r"\bthang (\d{1,2})/(\d{4}) (?:cung |lai )?cao hon thang (\d{1,2})/(\d{4})\b")
_TREND_CONFIRM = re.compile(r"\b(?:phai (?:khong|ko)|dung (?:vay )?khong)$")
_TREND_WORDS = re.compile(
    r"\b(?:xu huong|co phai|tu|qua|roi|r|den|toi|va|cac|thang|(?:\d+|hai|ba|bon|nam|sau) thang|"
    r"khong|ko|k|hok|hong|khum|ha|vay|a|nhi|nhe|z)\b")
_NEGATIVE_TREND = re.compile(
    r"\b(?:giam|tut|di xuong|it nhat|toi thieu|phan tram|lan)\b"
    r"|\b(?:khong|ko|k|hok|hong|khum|chua) (?:co )?(?:tang|(?:luon|deu) (?:cao )?hon|thang sau)\b")


def _increasing(text: str, today: date, rules: Rules) -> Plan | None | bool:
    """None: not a trend question; False: a trend question this grammar must not answer."""
    text = re.sub(r"\s+", " ", text).strip()
    trend = _TREND.search(text)
    pairs = _PAIR.findall(text)
    confirm = _TREND_CONFIRM.search(text)
    if not trend and not (pairs and (re.search(r"\bco phai\b", text)
                                   or (len(pairs) >= 2 and confirm))):
        return None
    if not (rules.revenue and rules.boolean_answer and rules.calendar_month) or rules.trend_rule:
        return False
    if not re.search(rf"\b{_REVENUE}\b", text) or _NEGATIVE_TREND.search(text):
        return False  # "số đơn tăng liên tục", "giảm liên tục", "tăng ít nhất 10 %"
    reduced = _PAIR.sub(" ", text) if pairs else text
    stated = re.search(r"\b(\d+|hai|ba|bon|nam|sau) thang\b", reduced)
    count = (_COUNT_WORD.get(stated.group(1)) or int(stated.group(1))) if stated else None
    if stated:
        reduced = reduced[:stated.start()] + " thang " + reduced[stated.end():]
    span = re.search(r"\btu thang (\d{1,2})/(\d{4}) (?:den|toi) (?:het )?thang (\d{1,2})/(\d{4})\b",
                     reduced)
    used = ["boolean_answer", "calendar_month"]
    if span:
        if not rules.inclusive_range:
            return False
        used.append("inclusive_range")
        months: list[tuple[int, int]] = []
        year, month = int(span.group(2)), int(span.group(1))
        last = (int(span.group(4)), int(span.group(3)))
        while (year, month) <= last and len(months) <= 12:
            months.append((year, month))
            month, year = (month + 1, year) if month < 12 else (1, year + 1)
        reduced = reduced[:span.start()] + " " + reduced[span.end():]
    else:
        months = [(int(y), int(m)) for m, y in re.findall(_BARE_MONTH, reduced)]
        reduced = re.sub(r"\b(?:thang )?" + _BARE_MONTH, " ", reduced)
        if not months and pairs:
            # A pair-only wording still names the complete chain: "B cao hơn A và C cao hơn B".
            # Build A, B, C here; the exact-pair check below rejects gaps or a broken chain.
            months = [(int(pairs[0][3]), int(pairs[0][2]))]
            months.extend((int(y1), int(m1)) for m1, y1, _, _ in pairs)
    if not 2 <= len(months) <= 12 or any(
        12 * (year2 - year1) + month2 - month1 != 1
        for (year1, month1), (year2, month2) in zip(months, months[1:])
    ):
        return False
    if count is not None and count != len(months):
        return False
    if pairs:
        # "có phải tháng B cao hơn tháng A và tháng C cao hơn tháng B": exactly every
        # consecutive pair, later month first.
        stated_pairs = [((int(y1), int(m1)), (int(y2), int(m2))) for m1, y1, m2, y2 in pairs]
        if stated_pairs != [(b, a) for a, b in zip(months, months[1:])]:
            return False
    reduced = _TREND.sub(" ", reduced)
    reduced = _TREND_CONFIRM.sub(" ", reduced.rstrip())
    reduced = re.sub(rf"\b(?:tong )?{_REVENUE}\b|\bcao hon\b", " ", reduced)
    reduced = _TREND_WORDS.sub(" ", reduced)
    if _FILLERS.sub(" ", reduced).strip():
        return False
    try:
        periods = tuple(_month(y, m) for y, m in months)
    except ValueError:
        return False
    if periods[-1][1] > today:
        return False  # a month still running or in the future
    return Plan("revenue_increasing", periods[0][0], periods[-1][1],
                evidence=_rows(rules, *used), periods=periods)


# ---------------------------------------------------------------- S01: top_debt_customers
# Contract C01 §5: "k khách nợ nhiều nhất đến hết ngày X, mỗi người còn nợ bao nhiêu".
_DEBT = re.compile(r"\b(?:dang no|con no|cong no|no)\b")
_DEBT_TOP = re.compile(
    r"\b(?:top )?(\d+) (?:khach hang|khach)(?: nao)?(?: co)? (?:dang |con )?(?:cong )?no "
    r"(?:nhieu|nhiu|lon|cao) nhat\b")
_DEBT_AMOUNTS = re.compile(
    rf"\bmoi nguoi con no {_AMOUNT}\b|\bkem so tien con no\b"
    r"|\b(?:va )?so (?:tien )?no cua tung nguoi\b|\b(?:cho (?:minh |toi )?)?bang\b")
_DEBT_DATE = re.compile(rf"\b(?:tinh )?den het (?:ngay )?{_DATE}\b"
                        r"|\b(?:tinh )?den (?:het )?hom nay\b")
_DEBT_WORDS = re.compile(r"\b(?:giup minh|xem giup|liet ke|cho hoi|cho minh|cho toi|cho|tinh|ha|a|nhe|vay)\b")


def _debt_table(text: str, today: date, rules: Rules) -> Plan | None | bool:
    """None: not a debt ranking question; False: one this grammar must not answer."""
    text = re.sub(r"\s+", " ", text).strip()
    top = _DEBT_TOP.search(text)
    if not top:
        return None if not (_DEBT.search(text) and re.search(rf"\b{_CUSTOMER}\b", text)
                            and re.search(r"\bnhat\b", text)) else False
    features = DEBT_TABLE_FEATURES
    if not all(getattr(rules, feature) for feature in features):
        return False
    reduced = text[:top.start()] + " " + text[top.end():]
    if not _DEBT_AMOUNTS.search(reduced):
        return False  # "khách nào nợ nhiều nhất" without the amounts is not a table (C01 §5)
    reduced = _DEBT_AMOUNTS.sub(" ", reduced)
    moment = _DEBT_DATE.search(reduced)
    if not moment:
        return False  # no "đến hết ngày X": the snapshot date is unknown
    try:
        day = (date(int(moment.group(3)), int(moment.group(2)), int(moment.group(1)))
               if moment.group(1) else today)
    except ValueError:
        return False
    reduced = reduced[:moment.start()] + " " + reduced[moment.end():]
    if _FILLERS.sub(" ", _DEBT_WORDS.sub(" ", reduced)).strip():
        return False
    top_k = int(top.group(1))
    if not 1 <= top_k <= 50 or day > today:
        return False
    return Plan("top_debt_customers", day, day, top_k, _rows(rules, *features))


# ---------------------------------------------------------------- C01 §7: debt measures
# "Tổng nợ đến hết ngày X", "số khách còn nợ", "thu nợ trong kỳ", "khách A nợ thêm / trả
# được". The intent comes from required phrases; every other word must belong to a closed
# vocabulary of that intent, so a condition the grammar does not read makes it abstain.
_DEBT_SNAPSHOT = re.compile(rf"\b(?:tinh )?(?:den|toi) het (?:ngay )?{_DATE}\b"
                            r"|\b(?:tinh )?(?:den|toi) het hom nay\b")
_DEBT_FLOW = re.compile(r"\b(?:ghi no|mua chiu|no) them\b")
_DEBT_REPAID = re.compile(r"\b(?:tra (?:bot )?(?:no )?duoc|tra (?:bot )?no|thu hoi|thu no)\b")
_DEBT_COUNT = re.compile(r"\b(?:bao nhieu|bao nhiu|bnhiu|bn|may|so) khach\b")
_DEBT_OWED = re.compile(r"\b(?:con no|con cong no|con thieu|chua thu|chua tra het)\b"
                        rf"|\bkhoan no\b.*\bcon {_AMOUNT} tien\b")
_DEBT_AGE = re.compile(r"\b(?:qua|hon|tren) (\d{1,4}) ngay\b|\btu (\d{1,4}) ngay tro len\b")
_DEBT_BUCKETS = re.compile(r"\b0 7(?: ngay)? 8 15(?: ngay)? (?:va )?tren 15 ngay\b")
_DEBT_COMMON = frozenset("""
    tinh cho hoi minh toi biet xem coi giup e ta do la cua hang khach tong cong so tien bao
    nhieu nhiu bnhiu bn may duoc no cac nhung don voi o co ma trong
""".split())
_DEBT_VOCABULARY = {
    "debt_total": _DEBT_COMMON | frozenset(
        "con thieu chua thu tra het da dat khoan tuoi cach".split()),
    "debt_age_buckets": _DEBT_COMMON | frozenset(
        "con lai chia tuoi no phan theo nhom lan luot moi ngay 3 ba".split()),
    "debt_customer_count": _DEBT_COMMON | frozenset("con thieu chua tra het dang".split()),
    "debt_collected": _DEBT_COMMON | frozenset("thu hoi tra bot mua chiu da tu".split()),
    "debt_flow": _DEBT_COMMON | frozenset(
        "ghi them mua chiu va tra bot da hai theo thu tu loi".split()),
}
_DEBT_FLOW_NAME = re.compile(
    r"^(?:(?:cho (?:minh |toi )?(?:hoi|biet)|xem giup|coi giup|e|trong)\s+)*"
    r"(?:khach (?:hang )?)?(?P<name>[a-z]+(?: [a-z]+){1,4}?) (?:ghi no|mua chiu|no) them\b")


def _debt_measure(text: str, today: date, rules: Rules) -> Plan | None | bool:
    """None: not one of these debt questions; False: one this grammar must not answer."""
    text = re.sub(r"\s+", " ", text).strip()
    if not re.search(r"\b(?:no|cong no|mua chiu|thieu)\b", text):
        return None
    buckets = _DEBT_BUCKETS.search(text)
    if buckets and re.search(r"\btuoi no\b", text):
        intent = "debt_age_buckets"
    elif _DEBT_FLOW.search(text) and _DEBT_REPAID.search(text):
        intent = "debt_flow"
    elif _DEBT_REPAID.search(text) and not _DEBT_OWED.search(text):
        intent = "debt_collected"
    elif _DEBT_OWED.search(text) and _DEBT_COUNT.search(text):
        intent = "debt_customer_count"
    elif _DEBT_OWED.search(text):
        intent = "debt_total"
    else:
        return None
    features = ["debt_orders", "debt_at_date", "debt_excluded", "customer_identity"]
    snapshot = intent in ("debt_total", "debt_customer_count", "debt_age_buckets")
    if snapshot:
        features.append("until_end_of_day")
    if intent in ("debt_customer_count", "debt_flow"):
        features.append("debt_by_customer")
    if intent in ("debt_collected", "debt_flow"):
        features.append("debt_flow_rule")
    threshold = None
    if snapshot:
        if intent == "debt_age_buckets":
            features.append("debt_age")
            text = text[:buckets.start()] + " " + text[buckets.end():]
        moment = _DEBT_SNAPSHOT.search(text)
        if not moment:
            return False  # the snapshot day is unknown
        try:
            day = (date(int(moment.group(3)), int(moment.group(2)), int(moment.group(1)))
                   if moment.group(1) else today)
        except ValueError:
            return False
        start = end = day
        rest = text[:moment.start()] + " " + text[moment.end():]
        age = _DEBT_AGE.search(rest)
        if age:
            if intent != "debt_total":
                return False
            features.append("debt_age")
            threshold = Threshold("order", "age_days", ">" if age.group(1) else ">=",
                                  Decimal(age.group(1) or age.group(2)),
                                  rules.row("debt_age") or "")
            rest = rest[:age.start()] + " " + rest[age.end():]
        elif (intent != "debt_age_buckets"
              and re.search(r"\b(?:qua|hon|tren|tuoi|nhom)\b", rest)):
            return False  # a qualifier beyond the supported debt grammar
    else:
        resolved = _period(text, today, rules)
        if resolved is None:
            return False
        start, end, rest = resolved
    if not all(getattr(rules, feature) for feature in features):
        return False
    filters: tuple[Filter, ...] = ()
    rest = re.sub(r"\s+", " ", rest).strip()
    if intent == "debt_flow":
        named = _DEBT_FLOW_NAME.search(rest)
        if not named:
            return False
        filters = (Filter("customer", named.group("name"), rules.row("customer_identity") or ""),)
        rest = rest[named.end("name"):]
    if set(rest.split()) - _DEBT_VOCABULARY[intent] - {"tro", "len"}:
        return False
    if end > today or start > end:
        return False
    return Plan(intent, start, end, 1, _rows(rules, *features), filters=filters,
                threshold=threshold)


# ---------------------------------------------------------------- SEM-02: customers
_CUSTOMER = r"(?:khach hang|khach)"
_TOP_CUSTOMERS = re.compile(
    rf"(?<![/\d])\b(?:ai la )?(?:top )?(?:(\d+) )?{_CUSTOMER}(?: nao| mo)? "
    r"(?P<what>co tong tien mua cao nhat|mua (?:nhieu|nhiu) tien nhat|chi tieu (?:nhieu|nhiu) nhat"
    r"|chi (?:nhieu|nhiu) tien nhat|(?:mua|co) (?:nhieu|nhiu) don(?: hang)? nh(?:at|ut)"
    r"|co so don mua (?:nhieu|nhiu) nh(?:at|ut)"
    r"|mua (?:nhieu|nhiu) lan nh(?:at|ut) (?:tinh|dem) theo so don(?: hang)?)\b")
_WHO_BY_REVENUE = re.compile(r"\bai mua hang (?:nhieu|nhiu) tien nhat\b")
_WHO_BY_ORDERS = re.compile(r"\bai mua (?:nhieu|nhiu) don(?: hang)? nh(?:at|ut)\b")
_CUSTOMER_PERCENT = re.compile(rf"\bphan tram {_CUSTOMER}\b")
_BOUGHT_CUSTOMERS = re.compile(rf"\b(?:trong )?so {_CUSTOMER} (?:da|co) mua(?: hang)?\b")
_AT_LEAST = re.compile(r"\b(?:mua )?(?:tu (\d+) don(?: hang)? tro (?:len|leen)|it nhat (\d+) don(?: hang)?)\b")
_CUSTOMER_WORDS = re.compile(
    rf"\b(?:trong so {_CUSTOMER} (?:da|co) mua(?: hang)?|(?:dem )?so {_CUSTOMER} da mua(?: hang)?|"
    rf"dem so {_CUSTOMER} da|"
    rf"so {_CUSTOMER}|{_CUSTOMER}|chiem|t[iy] le|(?:{_AMOUNT} )?phan tram|la (?:nhung )?ai|ai|"
    r"cho cua hang)\b")


def _customers(text: str, today: date, rules: Rules) -> Plan | None | bool:
    top = _TOP_CUSTOMERS.search(text)
    who_orders = _WHO_BY_ORDERS.search(text) if not top else None
    who = who_orders or (_WHO_BY_REVENUE.search(text) if not top else None)
    if not (top or who or re.search(rf"\b{_CUSTOMER}\b|\bai la\b", text)):
        return None
    at_least = _AT_LEAST.search(text)
    if not (top or who or at_least):
        return None
    if (top or who) and at_least:
        return False
    if not (rules.customer_identity and rules.customers_bought):
        return False
    used = ["customer_identity", "customers_bought"]
    comparison = ""
    if top or who:
        match = top or who
        rank_by = "order_count" if who_orders or (top and "don" in top.group("what")) else "revenue"
        if rank_by == "revenue":
            if not rules.customer_by_revenue:
                return False
            used.append("customer_by_revenue")
        if not rules.ties_by_code:
            return False
        used.append("ties_by_code")
        top_k = int(top.group(1) or 1) if top else 1
        reduced = text[:match.start()] + " " + text[match.end():]
        threshold = None
        intent = "top_customers"
    else:
        number = int(at_least.group(1) or at_least.group(2))
        if at_least.group(1):
            if not rules.threshold_inclusive:
                return False
            used.append("threshold_inclusive")
        reduced = text[:at_least.start()] + " " + text[at_least.end():]
        comparison = "share" if re.search(r"\bphan tram\b|\bt[iy] le\b", reduced) else ""
        if comparison:
            if not (_BOUGHT_CUSTOMERS.search(reduced) or _CUSTOMER_PERCENT.search(reduced)):
                return False  # only the customers who bought are a defined denominator
            # The denominator phrase can be the subject ("of those who bought, what %…").
            reduced = _BOUGHT_CUSTOMERS.sub(" ", reduced)
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
_AT_LEAST_MONEY = re.compile(rf"\b(?:dat )?(?:tu )?{_MONEY_VALUE} (?:dong |d |vnd )?tro (?:len|lne)\b")
_AT_LEAST_ORDERS = re.compile(
    r"\b(?:(?:co mat |xuat hien )?o (?:tu )?(\d+) (?:don|bill)(?: hang)? khac nhau tro len"
    r"|(?:co mat |xuat hien )(?:o |trong )it nhat (\d+) (?:don|bill)(?: hang)?(?: khac nhau)?)\b")
_THRESHOLD_SUBJECTS = (
    ("day", re.compile(rf"\b(?:dem )?(?:so |co )?(?:(?:{_AMOUNT} )?ngay(?! {_DATE}\b)"
                       rf"|{_AMOUNT} bua)\b")),
    ("order", re.compile(rf"\b(?:dem )?(?:so |co )?(?:{_AMOUNT} )?{_ORDER}\b")),
)
_THRESHOLD_WORDS = re.compile(
    rf"\b(?:ma|co|dat|ca ngay|cua hang|ban duoc|ban|tien hang|tien ban|(?:tong )?{_REVENUE}|"
    r"duoc mua|la bao nhieu|la)\b")
_THRESHOLD_PREFACE = re.compile(
    r"^(?:minh|toi) (?:tinh|dang|dinh|du dinh) (?:dat|set) nguo(?:ng|g)"
    r"(?: tang (?:qua|qa)| thuong)?(?: ne)? (?=(?:thang|quy|tuan|ngay)\b)"
)


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
        products = re.search(r"\b(?:dem )?(?:co )?(?:{0} )?(?:so )?(?:mat hang|san pham)\b".format(_AMOUNT),
                             text)
        if not products or not rules.customers_bought:  # "Số đơn ... đếm số đơn có một mặt hàng"
            return False
        used.append("customers_bought")
        subject, measure = "product", "order_count"
        value = Decimal(orders.group(1) or orders.group(2))
        reduced = text[:orders.start()] + " " + text[orders.end():]
        reduced = reduced[:products.start()] + " " + reduced[products.end():] \
            if products.end() <= orders.start() else reduced.replace(products.group(), " ", 1)
    else:
        value = Decimal(money.group(1).replace(" ", ""))
        reduced = text[:money.start()] + " " + text[money.end():]
        reduced = _THRESHOLD_PREFACE.sub("", reduced)
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


_BRAND_UNITS = re.compile(
    r"\bso (?P<number>\d+) cua hang nao ban (?:duoc )?(?:nhieu|nhiu) "
    r"(?P<unit>[a-z]+) nh(?:at|ut)\b"
)
_BRAND_FILLERS = re.compile(
    r"^(?:cho|cko) (?:minh|toi) (?:biet|bit)(?: xiu)?\b|\bgiup(?: minh| toi)?$"
)


def _brand_ranking(text: str, today: date, rules: Rules,
                   vocabulary: dict[str, Iterable[str]]) -> Plan | bool | None:
    match = _BRAND_UNITS.search(text)
    if not match:
        return None
    used = ["product_group", "catalogue_size", "unit_quantity", "ties_by_code"]
    if not (rules.revenue and rules.quantity_ranking and vocabulary.get("product_brand")
            and all(getattr(rules, feature) for feature in used)):
        return False
    group_text = text[:match.start()].rstrip()
    groups = [name for name in set(vocabulary.get("product_group", ()))
              if (key := normalize_question(name))
              and re.search(rf"\b{re.escape(key)}$", group_text)]
    size = f"so {match['number']}"
    sizes = [label for label in set(vocabulary.get("product_size", ())) if fold(label) == size]
    if len(groups) != 1 or len(sizes) != 1 or match["unit"] in _NOT_UNITS:
        return False
    reduced = group_text[:-len(normalize_question(groups[0]))] + " " + text[match.end():]
    resolved = _period(reduced, today, rules, used)
    if resolved is None:
        return False
    start, end, leftover = resolved
    leftover = _BRAND_FILLERS.sub(" ", re.sub(r"\s+", " ", leftover).strip())
    if _FILLERS.sub(" ", leftover).strip() or start > today:
        return False
    filters = (Filter("product_group", groups[0], rules.row("product_group") or ""),
               Filter("product_size", sizes[0], rules.row("catalogue_size") or ""))
    return Plan("top_product_brand", start, min(end, today), evidence=_rows(rules, *used),
                filters=filters, unit=match["unit"])


_DUE_AVERAGE = re.compile(
    rf"\b(?:tien )?khach phai tra"
    rf"(?: sau (?:khi )?tru (?:bot|giam gia) va (?:tien )?(?:may cu thu lai|thu may cu))?"
    rf" (?:binh quan|trung binh) {_ONE} {_ORDER} (?:la )?{_AMOUNT}(?: dong| tien)?\b"
)
_PHONE_AVERAGE = re.compile(
    rf"\b(?:khong|ko) tinh phu kien (?:trung binh|binh quan) {_ONE}"
    rf" may (?:dien thoai|dt) ban (?:duoc )?{_AMOUNT} tien\b"
)


def _phone_scope(text: str, today: date, rules: Rules, names: tuple[str, ...],
                 vocabulary: dict[str, Iterable[str]]) -> Plan | bool | None:
    kinds = set(vocabulary.get("phone_kind", ()))
    if not kinds or not (rules.revenue and rules.average_phone_revenue):
        return None
    resolved = _period(text, today, rules)
    if resolved is None:
        return None
    start, end, rest = resolved
    if start > today:
        return None
    rest = re.sub(r"\s+", " ", rest).strip()
    share = re.fullmatch(
        rf"(?:bo phu kien ra thi {_REVENUE} )?may moi chiem {_AMOUNT} phan tram "
        rf"(?:tong )?(?:{_REVENUE}|tien ban) may", rest)
    if share and {"may moi", "may cu"} <= kinds:
        return Plan("revenue", start, min(end, today), comparison="share",
                    filters=(Filter("phone_kind", "may moi"),),
                    evidence=_rows(rules, "average_phone_revenue"))
    group = re.fullmatch(
        rf"nhom (may moi|may cu|phu kien) ban(?: duoc)? {_AMOUNT} tien(?: the)?", rest)
    if group and group[1] in kinds and rules.money_is_revenue:
        return Plan("revenue", start, min(end, today),
                    filters=(Filter("phone_kind", group[1]),),
                    evidence=_rows(rules, "average_phone_revenue", "money_is_revenue"))
    quantity = re.search(rf"\bban(?: ra)?(?: duoc)? {_AMOUNT} (chiec)\b", rest)
    if quantity:
        scope = " ".join((rest[:quantity.start()] + " " + rest[quantity.end():]).split())
        scope = re.sub(r"^cua hang ", "", scope)
        if scope in {"dien thoai cu", "may cu", "may moi"}:
            field, value = "phone_kind", "may cu" if scope == "dien thoai cu" else scope
            if value not in kinds:
                return False
        elif scope in {"dien thoai", "dien thoai gop ca may moi voi may cu"}:
            field, value = "phone_kind", "dien thoai"
        elif scope in vocabulary.get("phone_model", ()) and scope not in {fold(n) for n in names}:
            field, value = "phone_model", scope
        else:
            return None
        return Plan("quantity", start, min(end, today), unit=quantity[1],
                    filters=(Filter(field, value),),
                    evidence=_rows(rules, "average_phone_revenue"))
    return None


def _catalogue_quantity(text: str, today: date, rules: Rules,
                        vocabulary: dict[str, Iterable[str]]) -> Plan | bool | None:
    if not (rules.revenue and rules.catalogue_size and rules.product_group and rules.unit_quantity):
        return None
    cue = re.search(r"\b(?:(bim)(?: ta)? (size (?:nb|[smlx]+))|(sua bot) (so \d+))"
                    r"(?: cua moi hang)?\b", text)
    if not cue:
        return None
    group, size = ("bim ta", cue[2]) if cue[1] else ("sua bot", cue[4])
    group_labels = [name for name in vocabulary.get("product_group", ()) if fold(name) == group]
    size_labels = [name for name in vocabulary.get("product_size", ()) if fold(name) == size]
    if len(group_labels) != 1 or len(size_labels) != 1:
        return False
    rest = text[:cue.start()] + " " + text[cue.end():]
    rest = re.sub(r"\b(?:(?:tinh|gop) (?:het )?)?(?:tat ca (?:cac )?|moi )hang\b", " ", rest)
    rest = re.sub(r"\b(?:sop|shop)(?= ban\b)", "cua hang", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    unit = "bich" if cue[1] else "lon"
    quantity = re.search(rf"\bban(?: ra)?(?: duoc)?(?: tong cong)? {_AMOUNT} {unit}\b", rest)
    if not quantity:
        return False
    rest = rest[:quantity.start()] + " " + rest[quantity.end():]
    resolved = _period(rest, today, rules)
    if resolved is None:
        return False
    start, end, rest = resolved
    if start > today or _FILLERS.sub(" ", rest).strip():
        return False
    return Plan("quantity", start, min(end, today), unit=unit,
                filters=(Filter("product_group", group_labels[0]), Filter("product_size", size_labels[0])),
                evidence=_rows(rules, "catalogue_size", "product_group", "unit_quantity"))


def _phone_average(text: str, today: date, rules: Rules) -> Plan | bool | None:
    match = _DUE_AVERAGE.search(text)
    intent, used = "average_amount_due", ["average_amount_due", "average_order"]
    if match is None:
        match = _PHONE_AVERAGE.search(text)
        if match is None:
            return None
        intent, used = "average_phone_revenue", ["average_phone_revenue", "money_is_revenue"]
    if not rules.revenue or not all(getattr(rules, feature) for feature in used):
        return False
    reduced = text[:match.start()] + " " + text[match.end():]
    resolved = _period(reduced, today, rules, used)
    if resolved is None:
        return False
    start, end, leftover = resolved
    if _FILLERS.sub(" ", leftover).strip() or start > today:
        return False
    return Plan(intent, start, min(end, today), evidence=_rows(rules, *used))


# Đợt 3 gói 1: "các đơn trả bằng <hình thức>", "các đơn bán qua / tại <kênh>".
_LABEL_CLAUSE = {
    "payment": re.compile(r"\b(?:cua )?(?:cac )?don(?: hang)? (?:duoc )?"
                          r"(?:(?:tra|thanh toan)(?: bang)? |(?=chuyen khoan\b))"),
    "channel": re.compile(r"\b(?:cua )?(?:(?:cac )?don(?: hang)? (?:duoc )?(?:ban )?|ban )"
                          r"(?:qua |tren |(?=tai ))"),
}
_PAIR_WORDS = re.compile(
    r"\b(?:lan luot|lan nuot|moi loai|moi ben|tung loai|tung ben|moi kenh|ca hai|con|voi|va)\b"
    r"|^so\b")


def _labels(text: str, vocabulary: dict[str, Iterable[str]]) -> tuple | bool | None:
    """One or two payment / channel clauses, by the words the shop's labels allow.

    None: no such clause. False: a clause whose label is unknown, ambiguous, of mixed
    kinds or repeated, or more than two clauses (abstain rather than drop a condition).
    """
    clauses = []
    for field, pattern in _LABEL_CLAUSE.items():
        terms = sorted((tuple(t.split("|", 1)) for t in vocabulary.get(f"{field}_terms", ())),
                       key=lambda t: -len(t[0]))
        for match in pattern.finditer(text):
            rest = text[match.end():]
            hit = next(((term, label) for term, label in terms
                        if re.match(rf"{re.escape(term)}(?!\S)", rest)), None)
            if hit is None:
                # Shorthand "đơn trả …" may refer to returned goods. Leave that
                # condition in the text unless it names a known payment method.
                if field == "payment" and not match.group().endswith("bang "):
                    continue
                if field == "channel" and not re.search(r"\bdon\b", match.group()):
                    continue
                return False if vocabulary.get(f"{field}_terms") is not None else None
            clauses.append((match.start(), match.end() + len(hit[0]), field, hit[1]))
    if not clauses:
        return None
    clauses.sort()
    if (len(clauses) > 2 or len({c[2] for c in clauses}) != 1
            or len({c[3] for c in clauses}) != len(clauses)
            or any(a[1] > b[0] for a, b in zip(clauses, clauses[1:]))):
        return False
    for start, end, _, _ in reversed(clauses):
        text = text[:start] + " " + text[end:]
    return re.sub(r"\s+", " ", text).strip(), [(c[2], c[3]) for c in clauses]


_AMBIGUOUS = object()
_GROUP_PREFIX = {
    "product_group": r"(?:cua )?(?:cac |nhung )?(?:mua hang |mat hang |san pham |hang )?nhom (?:hang )?",
    "customer_group": r"(?:(?<!moi )(?<!mot )(?<!1 )(?:cac )?don (?:hang )?)?"
                      r"(?:tu |cua |do )?(?:cac |nhung )?khach(?: hang)? "
                      r"(?:thuoc )?nhom |(?:cua )?nhom khach(?: hang)? ",
}


def _group(text: str, vocabulary: dict[str, Iterable[str]]) -> tuple | object | None:
    """The one product group / customer group the question names, by the shop's own names."""
    found = []
    for field, prefix in _GROUP_PREFIX.items():
        for name in set(vocabulary.get(field, ())):
            key = normalize_question(name)
            if not key:
                continue
            # "các đơn do khách nhóm X mua", "các khách thuộc nhóm X mua …"
            suffix = "(?: mua)?" if field == "customer_group" else ""
            for match in re.finditer(rf"\b(?:{prefix}){re.escape(key)}{suffix}(?!\S)", text):
                found.append((match.start(), match.end(), field, name))
    if not found:
        return None
    found.sort(key=lambda f: (f[0] - f[1], f[0]))  # longest first ("trà sữa" before "trà")
    best = found[0]
    same_span = {(f[2], unicodedata.normalize("NFC", f[3]).casefold()) for f in found
                 if (f[0], f[1]) == best[:2]}
    elsewhere = [f for f in found if f[1] <= best[0] or f[0] >= best[1]]
    if len(same_span) != 1 or any(
            (f[2], normalize_question(f[3])) != (best[2], normalize_question(best[3]))
            for f in elsewhere):
        return _AMBIGUOUS  # two groups, or the same words name a product and a customer group
    return text[:best[0]] + " " + text[best[1]:], best[2], best[3]


_SHARE = re.compile(r"\bphan tram\b|\bt[iy] trong\b")
_SHARE_WORDS = re.compile(
    rf"\b(?:(?:trong|tren) )?tong (?:{_REVENUE})(?: cua cua hang| cua shop)?\b|\bt[iy] trong\b|"
    rf"\b(?:chiem|dong gop|mang ve|bang)\b|\bphan(?: cua)?\b|\b(?:la )?(?:{_AMOUNT} )?phan tram\b")


def _share(text: str) -> tuple[str, bool]:
    """"chiếm / đóng góp bao nhiêu % (tổng) doanh thu (cả cửa hàng)", "tỉ trọng ... trong
    doanh thu": the group's share of the whole shop's revenue."""
    if not (_SHARE.search(text) and re.search(rf"\b{_REVENUE}\b", text)):
        return text, False
    # Not the "trọng" of "tỉ trọng doanh thu".
    text = re.sub(rf"\b(?:trong )?(?:{_REVENUE}) ca (?:cua hang|shop|thang)\b"
                  rf"|(?<!\bti )(?<!\bty )\btrong {_REVENUE}\b",
                  " tong doanh thu ", text)
    text = re.sub(rf"\b(?:chiem|dong gop) ((?:{_AMOUNT} )?phan tram) (?:tren )?({_REVENUE})\b",
                  r"chiem \1 tong \2", text)
    if not re.search(rf"\btong {_REVENUE}\b", text) and not re.search(r"\bchiem\b", text):
        return text, False
    text = _SHARE_WORDS.sub(" ", text)
    text = re.sub(rf"\b{_REVENUE}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip(), True


_SALE_PRICE = (
    re.compile(rf"gia ban {_ONE} (?P<unit>[a-z][a-z0-9]*)(?: vao)? "
               rf"(?:la )?{_AMOUNT}(?: tien)?(?: ta)?"),
    re.compile(rf"(?:cua hang )?ban(?: ra)? (?:(?:voi )?don gia|gia) {_AMOUNT}(?: tien)? "
               rf"{_ONE} (?P<unit>[a-z][a-z0-9]*)"),
)


def _daily_sale_price(text: str, today: date, rules: Rules,
                      names: Iterable[str]) -> Plan | None:
    if not re.search(rf"\b(?:don gia|gia ban|gia {_AMOUNT})\b", text):
        return None
    text = re.sub(r"^(?:(?:xem|coi|check) giup|cho (?:minh |toi )?hoi|e+) ", "", text)
    text = re.sub(rf"\bhom (?={_DATE}\b)", "ngay ", text)
    used = ["daily_sale_price"]
    resolved = _period(text, today, rules, used)
    if resolved is None or not resolved[0] == resolved[1] <= today:
        return None
    day, _, leftover = resolved
    rest, product = _product(leftover, names)
    if not isinstance(product, str):
        return None
    for pattern in _SALE_PRICE:
        match = pattern.fullmatch(" ".join(rest.split()))
        if match:
            return Plan("daily_sale_price", day, day,
                        filters=(Filter("product", product),), unit=match["unit"],
                        evidence=_rows(rules, *used))
    return None


_PRODUCT_MARKER = re.compile(r"(?:\b(?:mat hang|san pham|hang|mon|thuoc|cua)\s*)+$")

# Whole phrases only. Match them against the source name and keep its identity.
_PRODUCT_WORDINGS = {
    "chao ngheu": "chao ngao",
    "dau hu chien gion": "dau phu ran gion",
    "gia do dien thoai de ban": "ke dien thoai de ban",
}


def _product(text: str, names: Iterable[str]) -> tuple[str, object]:
    """Remove the one product the question names; several different products are ambiguous."""
    found = []
    names_by_key = [(name, normalize_question(name)) for name in set(names)]
    keys = {key for _, key in names_by_key}
    for name, key in names_by_key:
        # One-word names ("Trà") are too easy to confuse with grammar; codes carry digits.
        if not key or (len(key.split()) < 2 and not any(c.isdigit() for c in key)):
            continue
        alias = _PRODUCT_WORDINGS.get(key)
        if key.startswith("cu sac "):
            alias = "coc " + key.removeprefix("cu ")
        terms = (key, alias) if alias and alias not in keys else (key,)
        pattern = "|".join(re.escape(term) for term in terms)
        for match in re.finditer(rf"(?<!\S)(?:{pattern})(?!\S)", text):
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
                            r"tang giam|hon hay kem|cao hay thap hon|thay doi|tang truong|"
                            r"chenh lech|so thang)\b")
_CHANGE = re.compile(r"\b(?:tang hay giam|tang/giam|tang giam|hon hay kem|cao hay thap hon|"
                     r"thay doi|tang truong|chenh lech|chenh)\b")
_PERCENT = re.compile(rf"\b(?:{_AMOUNT} )?phan tram\b")
_REFERENCES = (
    ("month", re.compile(r"\bso (?:voi |vs )?thang (\d{1,2})/(\d{4})\b(?!/)")),
    ("prev_year", re.compile(r"\b(?:so (?:voi |vs )?)?cung (?:ky|thang) nam (?:ngoai|truoc)\b")),
    ("prev_month", re.compile(r"\b(?:so (?:voi |vs )?)?cung ky thang (?:truoc|roi)\b")),
    ("prev_month", re.compile(r"\bso (?:voi |vs )?thang (?:lien truoc|truoc do|truoc|roi)\b")),
    ("prev_month", re.compile(r"\bthang (?:lien truoc|truoc do)\b")),
    ("prev_month", re.compile(r"(?<=hon hay kem )thang (?:truoc|roi)\b")),
)


_PURCHASE_TOTAL = re.compile(
    rf"\btong gia tri cac phieu nhap hang\b|\btong tien hang nhap vao\b|\btong tien nhap hang\b"
    rf"|\bbo ra {_AMOUNT} tien nhap hang\b|\bnhap hang het {_AMOUNT} tien\b"
    rf"|\btien nhap hang (?=thang nay tong {_AMOUNT}$)"
    r"|\btien hang nhap(?: vao| vo)?\b")
_TOP_SUPPLIER = re.compile(
    r"\bnha cung cap nao duoc cua hang nhap hang (?:nhieu|nhiu) tien nhat\b"
    r"|\bcua hang nhap (?:nhieu|nhiu) tien hang nhat tu nha cung cap nao\b"
    r"|\btien nhap hang tu nha cung cap nao cao nhat\b"
    r"|\bnha cung cap nao co tong tien nhap hang (?:cao|lon) nhat\b"
    r"|\b(?:cua hang )?nhap tien hang cua (?:nha cung cap|ncc) nao (?:cao|lon) nh(?:at|ut)\b")
_MAIN_SUPPLIER = re.compile(r"\bnha cung cap chinh\b")
_PURCHASE_DIFFERENCE = re.compile(
    r"\b(?:(?:so|tong) (?P<unit>[a-z]+) nhap(?: vao| vo)? tru (?:so|tong) (?P=unit) ban"
    r"|(?:so nhap tru so|nhap tru) ban)(?: ra)?\b")


def _purchase_difference(text: str, today: date, rules: Rules,
                         filters: list[Filter]) -> Plan | None:
    if not (rules.purchase_sales_difference and rules.revenue):
        return None
    match = _PURCHASE_DIFFERENCE.search(text)
    resolved = _period(text[:match.start()] + " " + text[match.end():], today, rules)
    if resolved is None:
        return None
    start, end, rest = resolved
    rest = re.sub(r"\b(?:chenh(?: lech)?|lay|thi duoc)\b", " ", rest)
    unit = match["unit"] or ""
    if not unit:
        amount = re.search(rf"\b{_AMOUNT} ([a-z]+)$", rest.strip())
        if amount:
            unit = amount[1]
            rest = rest.strip()[:amount.start()]
    if not unit or _FILLERS.sub(" ", rest).strip() or start > min(end, today):
        return None
    return Plan("purchase_sales_difference", start, min(end, today),
                evidence=_rows(rules, "purchase_sales_difference", "revenue", "product_names"),
                filters=tuple(filters), unit=unit)


def _credit_due_count(text: str, today: date, rules: Rules) -> Plan | None:
    """Count credit orders by recorded due date as observed through today."""
    if not (rules.until_end_of_day and rules.debt_orders):
        return None
    cutoff = re.match(r"(?:tinh )?(?:toi|den) (?:het )?hom nay\b", text)
    if not cutoff:
        return None
    rest = text[cutoff.end():].strip()
    rest = re.sub(
        rf"^(?:trong moi don ban chiu )?(?:co )?{_AMOUNT} don"
        r"(?: ban chiu)?(?: co| ghi)? ngay phai tra(?: ghi tren so)? ",
        "", rest, count=1,
    )
    if rest == text[cutoff.end():].strip():
        return None
    if re.fullmatch(r"(?:con )?(?:sau|muon hon) hom nay"
                    r"(?: han dung hom nay khong tinh)?", rest):
        start, end = today + timedelta(days=1), date.max
    else:
        month = re.fullmatch(r"(?:trong|roi vao) thang (\d{1,2})/(\d{4})", rest)
        if not month or not rules.calendar_month or not 1 <= int(month[1]) <= 12:
            return None
        start, end = _month(int(month[2]), int(month[1]))
    return Plan("credit_due_count", start, end,
                evidence=_rows(rules, "until_end_of_day", "debt_orders", "calendar_month"),
                observed_on=today)


def _delivered_orders(text: str, today: date, rules: Rules) -> Plan | None:
    if not (rules.delivered_order_count and rules.calendar_month and rules.threshold_inclusive):
        return None
    cutoff = r"\b(?:tinh )?(?:den|toi) (?:het )?(?:hom nay|hnay)\b"
    if not re.search(cutoff, text):
        return None
    text = re.sub(cutoff, " ", text)
    match = re.search(r"\b(?:giao|cho)(?: thanh)? "
                      r"(?:tu (\d+|hai) dot tro len|(\d+|hai) dot tro len|it nhat (\d+|hai) dot)\b", text)
    if not match or not re.search(rf"\b{_AMOUNT} don\b", text):
        return None
    minimum = next(group for group in match.groups() if group)
    minimum = 2 if minimum == "hai" else int(minimum)
    rest = text[:match.start()] + " " + text[match.end():]
    if not re.search(r"\bdon dat thang\b", rest):
        return None
    resolved = _period(rest, today, rules)
    if resolved is None:
        return None
    start, end, rest = resolved
    rest = re.sub(rf"\b(?:(?:cac |{_AMOUNT} )?don dat|{_AMOUNT} don)\b", " ", rest)
    if minimum < 1 or start > today or _FILLERS.sub(" ", rest).strip():
        return None
    return Plan("delivered_order_count", start, min(end, today),
                evidence=_rows(rules, "delivered_order_count", "calendar_month", "threshold_inclusive"),
                threshold=Threshold("order", "delivery_count", ">=", Decimal(minimum),
                                    rules.row("threshold_inclusive") or ""),
                observed_on=today)


def _purchase(text: str, today: date, rules: Rules) -> Plan | None:
    """"Tổng tiền nhập hàng tháng M", "nhà cung cấp nào … nhiều tiền nhất" (rulebook §5)."""
    top = _TOP_SUPPLIER.search(text)
    total = _PURCHASE_TOTAL.search(text)
    if top and total and top.start() <= total.start() and total.end() <= top.end():
        total = None  # The money phrase is part of the supplier ranking criterion.
    matches = [(feature, match) for feature, match in (
        ("purchase_total", total), ("top_supplier", top),
        ("main_supplier", _MAIN_SUPPLIER.search(text))) if match]
    if len(matches) != 1:
        return None
    intent, match = matches[0]
    if not getattr(rules, intent) or not rules.purchase_total:
        return None
    rest = text[:match.start()] + " " + text[match.end():]
    resolved = _period(rest, today, rules)
    if resolved is None:
        return None
    start, end, leftover = resolved
    if intent == "main_supplier":
        if not re.search(r"\b(?:la ai|la nha cung cap nao)\b", leftover):
            return None
        leftover = re.sub(r"\b(?:la ai|la nha cung cap nao|minh)\b", " ", leftover)
    elif intent == "purchase_total":
        leftover = re.sub(rf"\btong (?:la )?{_AMOUNT}\b", " ", leftover)
    if _FILLERS.sub(" ", leftover).strip() or start > min(end, today):
        return None
    rows = ("purchase_total", intent) if intent != "purchase_total" else (intent,)
    return Plan(intent, start, min(end, today), 1, _rows(rules, *rows))


_AVG_PURCHASE_CUE = re.compile(r"\bnhap\b.*\bbinh quan\b|\bbinh quan\b.*\bnhap\b")
# The purchase unit: one word, optionally followed by a size token ("xe 4m3", "hộp 30").
_PURCHASE_UNIT = r"(?P<unit>[a-z]+(?: \d[a-z0-9]*)?)"
_AVG_PURCHASE = (
    re.compile(rf"\bgia nhap binh quan (?:cua )?{_ONE} {_PURCHASE_UNIT}(?= |$)"),
    re.compile(rf"\bnhap vao (?:voi )?gia binh quan {_ONE} {_PURCHASE_UNIT}(?= |$)"),
    re.compile(rf"\bnhap binh quan {_AMOUNT} tien {_ONE} {_PURCHASE_UNIT}(?= |$)"),
)


def _avg_purchase(text: str, today: date, rules: Rules, names: Iterable[str]) -> Plan | None:
    """"Giá nhập bình quân một <đơn vị> <hàng> tháng M/Y" (rulebook §5), in the purchase unit."""
    if not rules.avg_purchase_price or not rules.product_names:
        return None
    text, product = _product(text, names)
    if not product or product is _AMBIGUOUS:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    found = [m for pattern in _AVG_PURCHASE for m in pattern.finditer(text)]
    if len(found) != 1 or found[0].group("unit") in _NOT_UNITS:
        return None
    match = found[0]
    rest = text[:match.start()] + " " + text[match.end():]
    resolved = _period(rest, today, rules)
    if resolved is None:
        return None
    start, end, leftover = resolved
    if _FILLERS.sub(" ", leftover).strip() or end > today:
        return None
    return Plan("avg_purchase_price", start, end, 1,
                _rows(rules, "product_names", "avg_purchase_price"),
                filters=(Filter("product", product, rules.row("product_names") or ""),),
                unit=match.group("unit"))


_PRICE_VALUE = r"(?:\d{1,3}(?: \d{3})+|\d+)"
_PRICE_SUFFIX = r"(?: ?d| dong)"
_PRICE_UP = re.compile(
    rf"\b(?:ngay )?(?:(?:tang|doi) gia(?: tu (?P<old>{_PRICE_VALUE}){_PRICE_SUFFIX})? len"
    rf"|len gia) (?P<new>{_PRICE_VALUE}){_PRICE_SUFFIX}(?:/(?P<unit>[a-z][a-z0-9]*))?\b")
_EFFECT_UNIT = re.compile(
    r"\bso ([a-z][a-z0-9]*) ban (?:ra|duoc)\b|\b(?:luong ban )?tinh theo (?:so )?([a-z][a-z0-9]*)\b")
_DRUG_CUE = re.compile(r"\b(?:hoat chat|ke don|(?:don|toa) (?:cua )?bac si)\b")
# "Không cần đơn (toa) bác sĩ" is the plain wording of "không kê đơn"; "cần/phải có đơn bác sĩ"
# is "kê đơn". Read them as the rulebook's terms before matching the closed wordings.
_NO_PRESCRIPTION = re.compile(
    r"\b(?:khong|k|ko|hok|hong) (?:can|phai co) (?:don|toa) (?:cua )?bac si\b")
_PRESCRIPTION = re.compile(r"\b(?:can|phai co) (?:don|toa) (?:cua )?bac si\b")
_DRUG_NAME = r"(?:cac )?(?:thuoc|hang|mat hang|san pham)"
_INGREDIENT = rf"{_DRUG_NAME} (?:co )?hoat chat (?P<ingredient>[a-z0-9]+(?: [a-z0-9+]+)*?)"
_RX = rf"{_DRUG_NAME} (?P<rx>khong |phai )?ke don"
_DRUG_FILTER = rf"(?:{_INGREDIENT}|{_RX})"
_DRUG_AMOUNT = rf"(?:tong cong )?{_AMOUNT} tien"
_DRUG_SELL = rf"ban(?: ra)?(?: duoc)? {_DRUG_AMOUNT}"
_DRUG_TOTAL = rf"(?:tong )?{_REVENUE}(?: cua nha thuoc)?"
_DRUG_PERCENT = rf"{_AMOUNT} phan tram"
_DRUG_SHARE_PATTERNS = (
    rf"(?:(?:tien ban|{_REVENUE}) )?{_DRUG_FILTER} (?:chiem|bang) {_DRUG_PERCENT} {_DRUG_TOTAL}",
    rf"trong {_DRUG_TOTAL} phan cua {_DRUG_FILTER} la {_DRUG_PERCENT}",
    rf"t[iy] le (?:{_REVENUE} )?{_DRUG_FILTER} tren {_DRUG_TOTAL} la {_DRUG_PERCENT}",
)
_DRUG_REVENUE_PATTERNS = (
    rf"(?:tong )?(?:{_REVENUE}|tien ban) {_DRUG_FILTER}(?: (?:la )?{_AMOUNT})?",
    rf"(?:nha thuoc )?(?:ban )?{_DRUG_FILTER} {_DRUG_SELL}",
    rf"(?:nha thuoc )?ban {_DRUG_FILTER} (?:duoc )?{_DRUG_AMOUNT}",
    rf"(?:nha thuoc )?thu (?:duoc |ve )?{_DRUG_AMOUNT} tu {_DRUG_FILTER}",
)


def _drug_revenue(question: Question, rules: Rules) -> Plan | None:
    """Closed medication-revenue wording, with the entire ingredient label retained."""
    marker = "dmtplusseparator"  # normalize_question otherwise drops the combination separator.
    if marker in fold(question.question) or not rules.revenue:
        return None
    text = normalize_question(question.question.replace("+", f" {marker} ")).replace(marker, "+")
    text = _PRESCRIPTION.sub("ke don", _NO_PRESCRIPTION.sub("khong ke don", text))
    text = re.sub(r"\b(?:k|ko|hok|hong) (?=ke don\b)", "khong ", text)
    used: list[str] = []
    period = _period(text, question.as_of_date, rules, used)
    if period is None:
        return None
    start, end, text = period
    if end > question.as_of_date:
        return None
    text = " ".join(text.split())
    text = re.sub(r"^(?:cho (?:minh |toi )?(?:hoi|biet)|(?:xem|coi|check) giup|tinh giup) ", "", text)
    text = re.sub(r" (?:ta|do)$", "", text)

    field, value = "", ""
    share = False
    for patterns, is_share in ((_DRUG_SHARE_PATTERNS, True), (_DRUG_REVENUE_PATTERNS, False)):
        match = next((m for pattern in patterns if (m := re.fullmatch(pattern, text))), None)
        if match:
            share = is_share
            if match["ingredient"]:
                field, value = "ingredient", match["ingredient"]
                if (any(not part.strip() for part in value.split("+"))
                        or re.search(rf"\b(?:{_AMOUNT}|chi nhanh|thang|ngay)\b", value)):
                    return None
            else:
                field, value = "prescription", "non_rx" if match["rx"] == "khong " else "rx"
            break
    if not field and re.fullmatch(
            rf"tru (?:cac )?thuoc ke don ra (?:cac )?mat hang con lai {_DRUG_SELL}", text):
        field, value = "prescription", "non_rx"
    if not field or not getattr(rules, f"{field}_revenue"):
        return None
    if re.search(r"\btien ban\b", text):
        if not rules.goods_money_is_revenue:
            return None
        used.append("goods_money_is_revenue")
    elif not share and not re.search(rf"\b{_REVENUE}\b", text):
        if not rules.money_is_revenue:
            return None
        used.append("money_is_revenue")
    feature = f"{field}_revenue"
    return Plan("revenue", start, end, evidence=_rows(rules, *used, feature),
                filters=(Filter(field, value, rules.row(feature) or ""),),
                comparison="share" if share else "")


_EFFECT_WORDS = frozenset("""
    tu ngay so voi vs ke truoc do thi tang hay giam thay doi bao nhieu nhiu bnhiu bn may phan tram
    ban ra duoc trong luong tinh theo cho hoi minh xem coi check giup ta e a nhi vao
""".split())
_EFFECT_FROM = re.compile(rf"\b(\d{{1,3}}) ngay (?:ke tu (?:ngay )?(?:{_DATE}|do|(?:tang|doi) gia|tang)|dau)\b")
_EFFECT_BEFORE = re.compile(r"\b(\d{1,3}) ngay (?:ngay |lien )?truoc(?: ngay do| do)?\b")
_EFFECT_PERCENT = re.compile(rf"\b{_AMOUNT} phan tram\b")


def price_history_question(question: Question, rules: Rules) -> bool:
    text = normalize_question(question.question)
    return rules.price_effect_lookup and bool(_PRICE_UP.search(text)) and not re.search(_DATE, text)


def _price_effect(text: str, today: date, rules: Rules, names: Iterable[str],
                  price_increases: dict[tuple[str, Decimal], date], price_source: str) -> Plan | None:
    """Compare equal windows of base units; look up D only from a documented price history."""
    if not (rules.price_effect and rules.change_percent and rules.product_names and rules.revenue):
        return None
    text, product = _product(text, names)
    if not product or product is _AMBIGUOUS:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    changes = list(_PRICE_UP.finditer(text))
    if len(changes) != 1:
        return None
    change = changes[0]
    new_price = Decimal(change["new"].replace(" ", ""))
    if change["old"] and Decimal(change["old"].replace(" ", "")) >= new_price:
        return None
    text = _PRICE_UP.sub(" ", text)
    days = set(re.findall(_DATE, text))
    after, before = list(_EFFECT_FROM.finditer(text)), list(_EFFECT_BEFORE.finditer(text))
    units = {a or b for a, b in _EFFECT_UNIT.findall(text)}
    if (len(days) > 1 or len(after) != 1 or len(before) != 1
            or after[0][1] != before[0][1] or len(units) != 1
            or not _EFFECT_PERCENT.search(text)):
        return None
    unit = units.pop()
    if unit in _NOT_UNITS - {"phan"} or change["unit"] not in (None, unit):
        return None
    rest = _EFFECT_FROM.sub(" ", text)
    rest = _EFFECT_BEFORE.sub(" ", rest)
    rest = re.sub(_DATE, " ", rest)
    rest = _EFFECT_UNIT.sub(" ", rest)
    rest = re.sub(r"\b(?:trung binh moi ngay|nhieu hay it hon|nhiu hay it hon|"
                  r"tu hoi|ke tu khi)\b", " ", rest)
    if set(rest.split()) - _EFFECT_WORDS:
        return None
    n = int(after[0][1])
    if not 1 <= n <= 90:
        return None
    evidence = _rows(rules, "product_names", "price_effect", "change_percent")
    try:
        if days:
            day, month, year = map(int, days.pop())
            start = date(year, month, day)
        else:
            if not rules.price_effect_lookup or change["old"] or not price_source:
                return None
            start = price_increases.get((product, new_price))
            if start is None:
                return None
            evidence += (price_source,)
        end = start + timedelta(days=n - 1)
        previous_start = start - timedelta(days=n)
    except (ValueError, OverflowError):
        return None
    if end > today:
        return None
    return Plan("quantity", start, end, 1, evidence,
                compare_start=previous_start, compare_end=start - timedelta(days=1),
                comparison="change_percent",
                filters=(Filter("product", product, rules.row("product_names") or ""),),
                unit=unit)


_STOCK_CUE = re.compile(r"\b(?:ton kho|kho con ton|kho con|con ton|ton cuoi)\b"
                        r"|\bcuoi (?:ngay )?hom nay\b|\bluc cuoi ngay\b|\bhet hom nay\b")
_STOCK_UNIT = re.compile(rf"\b{_AMOUNT} ([a-z][a-z0-9]*)\b")
_STOCK_PAIR = re.compile(
    rf"\b{_AMOUNT} (?P<big>[a-z][a-z0-9]*(?: \d+[a-z][a-z0-9]*)?) (?:chan|nguyen) "
    rf"(?:va )?(?:{_AMOUNT} (?P<base>[a-z][a-z0-9]*) le|le {_AMOUNT} (?P<base2>[a-z][a-z0-9]*))\b")
_STOCK_WORDS = frozenset("""
    ton kho con cuoi ngay hom nay la luc trong tinh den cua duoc dc bua het
""".split())


def stock_question(question: Question) -> bool:
    text = normalize_question(question.question)
    return bool(_STOCK_CUE.search(text) and re.search(r"\bhom nay\b", text))


def _stock_plan(text: str, today: date, rules: Rules, filters: list[Filter]) -> Plan | None:
    if not rules.until_end_of_day or not re.search(r"\bhom nay\b", text):
        return None
    text = re.sub(r"\bcon lai\b", "con", text)
    pair = _STOCK_PAIR.search(text)
    features = ("product_names", "until_end_of_day")
    if pair:
        rest = text[:pair.start()] + " " + text[pair.end():]
        if not rules.stock_split or set(_FILLERS.sub(" ", rest).split()) - _STOCK_WORDS:
            return None
        big, base = pair["big"], pair["base"] or pair["base2"]
        if big == base or base in _NOT_UNITS - {"phan"} or big in _NOT_UNITS:
            return None
        return Plan("stock_split", today, today, evidence=_rows(rules, *features, "stock_split"),
                    filters=tuple(filters), unit=f"{big}|{base}")
    unit = _stock_unit(text)
    if unit is None:
        return None
    return Plan("stock", today, today, evidence=_rows(rules, *features),
                filters=tuple(filters), unit=unit)


def _stock_unit(text: str) -> str | None:
    """The base unit asked for today's stock; None for anything else (another day, a split
    into big and small units, an unknown word)."""
    if re.search(r"\b(?:chan|nguyen|le)\b", text):
        return None
    units = [m for m in _STOCK_UNIT.finditer(text) if m.group(1) not in _NOT_UNITS]
    if len(units) != 1:
        return None
    rest = text[:units[0].start()] + " " + text[units[0].end():]
    if set(_FILLERS.sub(" ", rest).split()) - _STOCK_WORDS:
        return None
    return units[0].group(1)


_STAFF = re.compile(r"\b(?:cac )?don (?:hang )?do nhan vien (?:ma )?(nv\d{1,4}) ban\b"
                    r"|\b(?:cua )?nhan vien (?:ma )?(nv\d{1,4})\b")
_BEST_STAFF = re.compile(
    r"\b(?:ai la )?nhan vien ban gioi nhat(?: la ai)?\b"
    r"|\b(?:ai la )?(?:nhan vien|nguoi ban|nv) (?:nao )?"
    rf"(?:(?:(?:co|ban(?: duoc)?) )?{_REVENUE}(?: cao)?|ban(?: duoc)? (?:nhieu|nhiu) tien) nh(?:at|ut)(?: la ai)?\b"
    rf"|\btho nao lam ra {_REVENUE} cao nh(?:at|ut)\b")


def _staff(text: str, terms: Iterable[str], money_is_revenue: bool) -> tuple[str, str] | None | bool:
    """Resolve an explicit seller code or a uniquely documented person's name."""
    code_matches = [(m, (m.group(1) or m.group(2)).upper()) for m in _STAFF.finditer(text)]
    if not code_matches:
        code_matches = [(m, m["code"].upper()) for m in re.finditer(
            r"\b(?:nv )?(?P<code>nv\d{1,4})(?= ban\b)", text)]
    matches = code_matches[:]
    for term in terms:
        role, name, code = term.split("|", 2)
        prefix = (r"(?:(?:cac|kac) phieu )?(?:tho )?" if role == "repair" else
                  r"(?:(?:cac|kac) don(?: hang)? )?(?:do )?(?:nhan vien|nv) ")
        for match in re.finditer(rf"\b{prefix}{re.escape(name)}(?: lam\b| ban\b)?(?!\S)", text):
            matched = match.group()
            if role == "repair" and matched == name and not re.search(r"\bban\b", text):
                continue
            matches.append((match, code))
    if not matches:
        return None
    if len({code for _, code in matches}) != 1:
        return False
    code = matches[0][1]
    rest = text
    for match, _ in sorted(matches, key=lambda pair: pair[0].start(), reverse=True):
        rest = rest[:match.start()] + " " + rest[match.end():]
    if money_is_revenue and any(match.group().endswith(" ban") for match, _ in matches):
        rest = re.sub(rf"\b(?:cong lai )?(?:duoc )?{_AMOUNT} tien(?: hang)?\b",
                      " doanh thu ", rest, count=1)
    return rest, code


_SHIFT = re.compile(r"\b(?:rieng |trong |cua )?ca (sang|chieu)\b")
_SHIFT_CHANGE = re.compile(r"\b(?:so voi|so vs|chenh lech|chenh|hon hay kem|tang hay giam)\b")


def _shift(text: str, rules: Rules) -> tuple[str, str, bool] | None | bool:
    """Rulebook shifts: "riêng ca chiều …", "doanh thu ca chiều chênh ca sáng bao nhiêu %".

    None: no shift; False: shift words not understood or not defined by this split;
    else (text without the shift words, shift asked about, whether it is compared with
    the rulebook's base shift).
    """
    found = list(_SHIFT.finditer(text))
    if not found:
        return None
    if not rules.shifts:
        return False
    names = [match.group(1) for match in found]
    if len(found) == 1:
        if _SHIFT_CHANGE.search(text) or re.search(r"\bphan tram\b", text):
            return False  # compared with something else, or a share of the day
        return text[:found[0].start()] + " " + text[found[0].end():], names[0], False
    if len(found) != 2 or names[0] == names[1]:
        return False
    # The base is the shift after "so với" ("so với ca sáng, ca chiều …"), else the second.
    before = text[:found[0].start()].rstrip()
    subject, base = (names[1], names[0]) if re.search(r"\bso (?:voi|vs)$", before) else names
    if base != rules.shift_base:
        return False  # only "<other shift> so với <base shift>" is defined by the rulebook
    if text[found[0].end():found[1].start()].strip() == "so":
        rest = before + " so voi " + text[found[1].end():]
    else:
        rest = _SHIFT.sub(" ", text)
    if not _SHIFT_CHANGE.search(rest) or not re.search(rf"\b(?:{_AMOUNT} )?phan tram\b", rest):
        return False  # "chênh bao nhiêu tiền / đơn" is not a percentage
    rest = _SHIFT_CHANGE.sub(" ", rest)
    rest = re.sub(rf"\b(?:{_AMOUNT} )?phan tram\b", " ", rest, count=1)
    rest = re.sub(r"\bthi\b", " ", rest)
    return rest, subject, True


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
    before = re.sub(r"\bdem $", "", text[:match.start()])
    after = re.sub(r"^ thi\b", "", text[match.end():])
    text = re.sub(r"\s+", " ", before + " " + after).strip()
    change, percent = _CHANGE.search(text), _PERCENT.search(text)
    if not change or not percent:
        return False  # "chênh lệch", "hơn bao nhiêu tiền"... not supported yet
    text = _PERCENT.sub(" ", _CHANGE.sub(" ", text, count=1), count=1)
    if change[0] in {"chenh", "chenh lech"}:
        text = re.sub(r"\btang hay giam$", "", text.strip())
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
         rf"(?<!doanh )\b(?:ban(?: ra)?|thu ve|thu)(?: duoc)?(?: tong cong)? {_AMOUNT} tien\b"),
        ("goods_money_is_revenue", rules.goods_money_is_revenue, r"\btien (?:hang|ban(?: hang)?)\b"),
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


_TO_TODAY = r"(?:tinh )?(?:den|toi) (?:het )?(?:hom nay|hien tai|nay|(?:bay )?(?:chu|gio))\b"
_INCLUDING_TODAY = r"(?:tinh (?:ca|luon)|ke ca) hom nay"


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
        (rf"\b(?:{_INCLUDING_TODAY} (?:thi )?)?(\d{{1,3}}) ngay "
         rf"(?:gan nhat(?: {_INCLUDING_TODAY})?|qua {_INCLUDING_TODAY})\b", None),
        (rf"\b(?:tinh )?tu dau nam(?: nay)?(?: {_TO_TODAY})?"
         rf"|\b(?:dau nam(?: nay)?|nam nay) {_TO_TODAY}"
         r"|\b(?:tinh )?tu (?:ngay )?1/1(?: nam nay)? (?:toi|den) (?:het )?(?:ngay )?hom nay\b",
         "year_to_date"),
        (rf"\b(?:tinh )?tu dau thang(?: nay)?(?: {_TO_TODAY})?"
         rf"|\btu (?:ngay |mung )?1 thang nay(?: {_TO_TODAY})?", "month_to_date"),
        (rf"\b(thang nay|tuan nay)(?: {_TO_TODAY})?"
         r"|\b(thang truoc|tuan truoc|tuan roi|hom qua|(?:rieng (?:ngay )?)?hom nay)\b", None),
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
                token = groups[0] or groups[1]
                if token.endswith("hom nay"):
                    start = end = today
                elif token == "hom qua":
                    start = end = today - timedelta(days=1)
                elif token == "thang nay" and rules.month_to_date:
                    start, end = today.replace(day=1), today
                    used.append("month_to_date")
                elif token == "thang truoc":
                    end = today.replace(day=1) - timedelta(days=1)
                    start = end.replace(day=1)
                elif token.startswith("tuan") and rules.monday_week:
                    monday = today - timedelta(days=today.weekday())
                    used.append("monday_week")
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
        elif index == 6 or (index == 7 and groups[0] == "thang nay"):
            info["kind"] = "month_to_date"
        leftover = text[:match.start()] + " " + text[match.end():]
        if index == 5:
            # "từ 1/1 đến hết hôm nay, doanh thu năm nay": "năm nay" repeats the same year.
            leftover = re.sub(r"\bnam nay\b", " ", leftover, count=1)
        return start, end, leftover
    return None
