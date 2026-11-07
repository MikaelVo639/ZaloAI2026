"""README-driven debt ledger adapter (C01 / D01).

Debt is deliberately separate from :class:`~datamate.ingestion.sales.Sale`: the
amount owed by an order is the order's total receivable minus payment events, not
the sum of product-line revenue.  This adapter only accepts columns whose meaning
is documented by the business README and keeps the source checks visible in the
returned ledger lineage.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Iterator

from datamate.contracts import Catalog, DebtLedger, DebtOrder, DebtPayment
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    Section,
    _header_names,
    _matching_tables,
    _records,
    parse_amount,
    parse_day,
    parse_sections,
)
from datamate.ingestion.source_names import filename_key
from datamate.ingestion.status import normalize_label, order_state
from datamate.semantics.text import fold

ADAPTER_VERSION = "readme_debt.v1"


class DebtSourceError(ValueError):
    """Typed source failure safe for the Agent to turn into an abstention.

    Only ``code == "absent"`` means the business explicitly has no debt source.
    Every other code represents an unsupported, incomplete or invalid source and
    must remain an abstention rather than becoming ``unanswerable``.
    """

    def __init__(self, code: str, message: str = "", **diagnostics: Any):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.diagnostics = {"code": code, **diagnostics}


_CREDIT = frozenset({"ghi no", "debt", "no", "mua chiu"})
_COD = frozenset({"cod", "thu ho", "thu ho cod", "giao hang thu tien"})
_ORDINARY_PAYMENT = frozenset({
    "tien mat", "cash", "chuyen khoan", "transfer", "vi dien tu", "ewallet",
})
_MARKETPLACE = frozenset({"shopee", "lazada", "tiki", "tiktok shop"})
_ORDINARY_CHANNEL = frozenset({"store", "phone", "zalo oa"})
_WALK_IN_NAMES = frozenset({"khach le", "kl", "le", "khach vang lai", "vang lai"})


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str | None:
    return None if _blank(value) else str(value).strip()


def _field(meaning: str) -> str | None:
    """Debt semantic field from a README column meaning, never its header."""
    text = fold(re.sub(r"\s*\(.*\)\s*\.?\s*$", "", meaning.strip())).strip()
    exact = {
        "ma don": "order_id",
        "ngay dat": "order_day",
        "ngay ban": "order_day",
        "trang thai don": "status",
        "ma khach": "customer_id",
        "ma khach trong": "customer_id",
        "ten khach": "customer_name",
        "hinh thuc thanh toan": "payment_method",
        "kenh ban": "channel",
        "nguon don": "channel",
        "thoi diem thu": "payment_day",
        "ngay thu": "payment_day",
        "ngay thanh toan": "payment_day",
        "so tien tra lan do": "payment_amount",
        "so khach phai tra cho ca don": "order_total",
        "tong phai thu ca don": "order_total",
        "so tien cua don chua thu tinh den het ngay cuoi du lieu": "closing_debt",
        "con no cuoi du lieu": "closing_debt",
        "con no": "closing_debt",
        "da thanh toan": "paid_to_date",
        "so tien khach da tra cho don": "paid_to_date",
    }
    if text in exact:
        return exact[text]
    if text.startswith("ma don"):
        return "order_id"
    if text.startswith(("thoi diem thu", "ngay thu", "ngay thanh toan")):
        return "payment_day"
    if "so tien tra lan do" in text:
        return "payment_amount"
    if "so khach phai tra cho ca don" in text or "tong phai thu ca don" in text:
        return "order_total"
    if "con no" in text and ("cuoi du lieu" in text or "chua thu" in text):
        return "closing_debt"
    if text.startswith(("con no", "du no")):
        return "closing_debt"
    if "da tra" in text or "da thanh toan" in text:
        return "paid_to_date"
    if text.startswith("tong tien don"):
        return "order_total"
    if text.startswith("so tien") and "lan do" in text:
        return "payment_amount"
    return None


# Debt-specific, reviewed export profiles.  They are selected by complete structural
# signatures and README evidence, never by business id.  A recognised but incomplete
# table remains a partial source and is rejected by _role_sections.
_DEBT_PROFILES = (
    ("debt_pos_kiotviet.v1", (
        ({"Mã hóa đơn", "Thời gian", "Trạng thái", "Tổng sau giảm giá",
          "Phương thức thanh toán"}, {
            "Mã hóa đơn": "mã đơn", "Thời gian": "ngày bán",
            "Trạng thái": "trạng thái đơn",
            "Tổng sau giảm giá": "số khách phải trả cho cả đơn",
            "Phương thức thanh toán": "hình thức thanh toán",
            "Kênh bán": "kênh bán", "Mã KH": "mã khách",
            "Khách hàng": "tên khách", "Khách đã trả": "đã thanh toán",
        }),
        ({"Mã hóa đơn", "Thời gian", "Giá trị"}, {
            "Mã hóa đơn": "mã đơn", "Thời gian": "thời điểm thu",
            "Giá trị": "số tiền trả lần đó",
        }),
        ({"Mã khách hàng", "Tên khách hàng"}, {
            "Mã khách hàng": "mã khách", "Tên khách hàng": "tên khách",
        }),
    )),
    ("debt_banhang.v1", (
        ({"Order ID", "Ngày bán", "Trạng thái", "Tong tien", "Phương thức TT"}, {
            "Order ID": "mã đơn", "Ngày bán": "ngày bán",
            "Trạng thái": "trạng thái đơn", "Tong tien": "tổng phải thu cả đơn",
            "Phương thức TT": "hình thức thanh toán", "Nguồn đơn": "kênh bán",
            "Mã KH mua": "mã khách", "Tên KH": "tên khách",
            "Còn nợ": "còn nợ cuối dữ liệu", "Đã thu": "đã thanh toán",
        }),
        ({"Mã bill", "Thời gian thu", "Số tiền thu"}, {
            "Mã bill": "mã đơn", "Thời gian thu": "thời điểm thu",
            "Số tiền thu": "số tiền trả lần đó",
        }),
        ({"Customer ID", "ten_kh"}, {
            "Customer ID": "mã khách", "ten_kh": "tên khách",
        }),
    )),
)

_DEBT_PROFILE_CANDIDATES = {
    "debt_pos_kiotviet.v1": (
        ({"Mã hóa đơn", "Tổng sau giảm giá"}, _DEBT_PROFILES[0][1][0][1]),
        ({"Mã hóa đơn", "Giá trị"}, _DEBT_PROFILES[0][1][1][1]),
    ),
    "debt_banhang.v1": (
        ({"Order ID", "Tong tien"}, _DEBT_PROFILES[1][1][0][1]),
        ({"Mã bill", "Số tiền thu"}, _DEBT_PROFILES[1][1][1][1]),
    ),
}


def _profile_sections(catalog: Catalog, documented: list[Section]) -> list[Section]:
    text = fold(catalog.readme)
    if not ("pos" in text.split() or "phan mem ban hang" in text):
        return []
    covered = {filename_key(section.file) for section in documented}
    headers = [(table, _header_names(table)) for table in catalog.tables
               if filename_key(table.path.name) not in covered]
    for version, signatures in _DEBT_PROFILES:
        found: list[Section] = []
        for table, header in headers:
            matched = False
            for required, meanings in signatures:
                if required <= header:
                    found.append(Section(
                        table.path.name, table.name,
                        {column: meaning for column, meaning in meanings.items()
                         if column in header}, origin=version))
                    matched = True
                    break
            if matched:
                continue
            for marker, meanings in _DEBT_PROFILE_CANDIDATES[version]:
                if marker <= header:
                    found.append(Section(
                        table.path.name, table.name,
                        {column: meaning for column, meaning in meanings.items()
                         if column in header}, origin=f"{version}.partial"))
                    break
        if any("order_total" in _section_fields(s, {}) for s in found) and any(
                "payment_amount" in _section_fields(s, {}) for s in found):
            return found
    return []


def _debt_references(readme: str) -> tuple[dict[str, str], tuple[str, ...]]:
    """Column references from the README's explicit ``Công nợ`` convention."""
    fields: dict[str, str] = {}
    evidence: list[str] = []
    for line in readme.splitlines():
        heading, separator, _description = line.partition(":")
        if not separator or fold(heading).strip() != "cong no":
            continue
        evidence.append(line.strip())
        matches = list(re.finditer(
            r"\*\*([^*]+)\*\*|`([^`]+)`|(?<![\w`*])([\wÀ-ỹ]+(?:\s+[\wÀ-ỹ]+)*\."
            r"[\wÀ-ỹ]+(?:\s+[\wÀ-ỹ]+)*)(?=\s+(?:là|la)\b)", line))
        for index, match in enumerate(matches):
            column = (match.group(1) or match.group(2) or match.group(3)).strip()
            if fold(column) == "cong no":
                continue
            stop = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            meaning = fold(line[match.end():stop])
            semantic = None
            if "so tien tra lan do" in meaning:
                semantic = "payment_amount"
            elif "so khach phai tra cho ca don" in meaning or "tong phai thu" in meaning:
                semantic = "order_total"
            elif "chua thu" in meaning or "con no cuoi" in meaning:
                semantic = "closing_debt"
            if semantic:
                previous = fields.get(column)
                if previous and previous != semantic:
                    raise DebtSourceError("column_missing", "Debt convention is ambiguous",
                                          column=column)
                fields[column] = semantic
    return fields, tuple(evidence)


def _referenced_field(references: dict[str, str], section: Section,
                      column: str) -> str | None:
    if column in references:
        return references[column]
    column_key = fold(column)
    source_keys = {
        fold(section.sheet or ""),
        fold(section.file.rsplit("/", 1)[-1].rsplit(".", 1)[0]),
    }
    candidates = set()
    for reference, semantic in references.items():
        if "." not in reference:
            continue
        source, referenced_column = reference.rsplit(".", 1)
        if fold(referenced_column) == column_key and fold(source) in source_keys:
            candidates.add(semantic)
    if len(candidates) > 1:
        raise DebtSourceError("column_missing", "Qualified debt column is ambiguous",
                              source=section.label, column=column)
    return next(iter(candidates), None)


def _section_fields(section: Section, references: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for column, meaning in section.columns.items():
        semantic = _referenced_field(references, section, column) or _field(meaning)
        if semantic:
            if semantic in result and result[semantic] != column:
                raise DebtSourceError("column_missing", "Several columns have one debt meaning",
                                      source=section.label, field=semantic)
            result[semantic] = column
    return result


def _documented_sources_complete(catalog: Catalog, sections: Iterable[Section]) -> bool:
    documented = {filename_key(section.file) for section in sections}
    in_files = False
    for line in catalog.readme.splitlines():
        if line.startswith("## "):
            in_files = fold(line[3:]).strip() == "file"
            continue
        if in_files:
            match = re.match(r"\s*-\s+`([^`]+)`", line)
            if match:
                documented.add(filename_key(match.group(1)))
    present = {filename_key(table.path.name) for table in catalog.tables}
    return bool(catalog.readme.strip()) and bool(documented) and present <= documented


def _has_debt_hint(catalog: Catalog, sections: Iterable[Section],
                   references: dict[str, str], evidence: Iterable[str]) -> bool:
    if references or any(evidence):
        return True
    return any(_field(meaning) in {"payment_amount", "paid_to_date", "closing_debt"}
               for section in sections for meaning in section.columns.values())


def _rule_rows(rulebook: str) -> tuple[str, ...]:
    wanted = {
        "don duoc tinh vao no": None,
        "no mot don tai ngay x": None,
        "don cod don ban qua san tmdt": None,
    }
    for line in rulebook.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2:
            key = fold(cells[0])
            if key in wanted:
                wanted[key] = line.strip()
    missing = [key for key, row in wanted.items() if row is None]
    if missing:
        raise DebtSourceError("rules_missing", "Split rulebook lacks debt rules",
                              rules=missing)
    return tuple(row for row in wanted.values() if row is not None)


def _role_sections(catalog: Catalog, sections: list[Section], references: dict[str, str]
                   ) -> tuple[dict[str, list[tuple[Section, dict[str, str]]]], dict[str, Any]]:
    roles: dict[str, list[tuple[Section, dict[str, str]]]] = defaultdict(list)
    required = {
        "orders": {"order_id", "order_day", "status", "order_total",
                   "payment_method", "channel"},
        "payments": {"order_id", "payment_day", "payment_amount"},
    }
    all_mappings: dict[str, dict[str, str]] = {}
    resolved_references: set[str] = set()
    incomplete: list[dict[str, Any]] = []
    for section in sections:
        for column in section.columns:
            referenced = _referenced_field(references, section, column)
            if referenced:
                resolved_references.add(referenced)
        fields = _section_fields(section, references)
        all_mappings[section.label] = fields
        role = None
        if required["orders"] <= set(fields):
            role = "orders"
        elif required["payments"] <= set(fields):
            role = "payments"
        elif {"customer_id", "customer_name"} <= set(fields) and "order_id" not in fields:
            role = "customers"
        elif fields.get("order_total") or fields.get("closing_debt") or fields.get(
                "paid_to_date") or ("order_day" in fields and "status" in fields):
            incomplete.append({"source": section.label, "role": "orders",
                               "fields": sorted(fields)})
        elif "payment_amount" in fields or "payment_day" in fields:
            incomplete.append({"source": section.label, "role": "payments",
                               "fields": sorted(fields)})
        if role:
            try:
                tables = _matching_tables(catalog, section)
            except ReadmeSalesError as exc:
                raise DebtSourceError("column_missing", str(exc), source=section.label) from exc
            if not tables:
                raise DebtSourceError("column_missing", "README source is not present",
                                      source=section.label)
            roles[role].append((section, fields))
    if incomplete:
        raise DebtSourceError("partial", "A documented debt source is incomplete",
                              sources=incomplete)
    missing_references = {"payment_amount", "order_total"} - resolved_references
    if missing_references:
        code = "partial" if not sections else "column_missing"
        raise DebtSourceError(code, "README debt columns do not exist",
                              fields=sorted(missing_references))
    for role in ("orders", "payments"):
        if not roles.get(role):
            raise DebtSourceError("partial", "README lacks a complete debt source",
                                  role=role, mappings=all_mappings)
        signatures = {frozenset(fields) for _, fields in roles[role]}
        if len(signatures) != 1:
            raise DebtSourceError("column_missing", "Sources of one debt role disagree",
                                  role=role)
    return roles, all_mappings


def _rows(catalog: Catalog, entries: Iterable[tuple[Section, dict[str, str]]]
          ) -> Iterator[tuple[Section, str, int, dict[str, Any]]]:
    for section, fields in entries:
        try:
            tables = _matching_tables(catalog, section)
            for table in tables:
                for ordinal, raw in _records(table, section.columns):
                    row = {field: raw.get(column) for field, column in fields.items()}
                    if any(not _blank(value) for value in row.values()):
                        yield section, table.key, ordinal, row
        except ReadmeSalesError as exc:
            raise DebtSourceError("column_missing", str(exc), source=section.label) from exc


def _required(row: dict[str, Any], fields: Iterable[str], *, role: str, source: str) -> None:
    missing = [field for field in fields if _blank(row.get(field))]
    if missing:
        code = "key" if "order_id" in missing else "column_missing"
        raise DebtSourceError(code, "Required debt value is blank",
                              role=role, source=source, fields=missing)


def _amount(value: Any, *, field: str, source: str,
            multiplier: Decimal = Decimal(1)) -> Decimal:
    try:
        result = parse_amount(value)
    except (TypeError, ValueError) as exc:
        raise DebtSourceError("amount", "Debt amount could not be parsed",
                              field=field, source=source) from exc
    if result < 0:
        raise DebtSourceError("amount", "Debt amount has an unsupported sign",
                              field=field, source=source)
    return result * multiplier


def _money_unit(readme: str) -> tuple[str, Decimal, tuple[str, ...]]:
    """Return the reviewed unit used by all debt money columns.

    The challenge sources use VND when no scale is stated.  A global thousand-VND
    convention is explicit enough to convert; any other stated scale is unsupported.
    """
    def declares_unit(line: str) -> bool:
        text = fold(line)
        return ("don vi tien" in text
                or bool(re.search(r"\b(?:nghin|ngan|trieu|ty) dong\b", text))
                or bool(re.search(r"\b(?:vnd|eur|usd|cent|xu)\b", text)))

    evidence = tuple(line.strip() for line in readme.splitlines() if declares_unit(line))
    if not evidence:
        return "vnd", Decimal(1), ()

    units: set[str] = set()
    scoped: list[str] = []
    for line in evidence:
        text = fold(line)
        line_units: set[str] = set()
        if re.search(r"\.(?:csv|xlsx|xls|db|sqlite)\b", line, re.IGNORECASE) or "`" in line:
            scoped.append(line)
        if "nghin dong" in text or "ngan dong" in text:
            line_units.add("thousand_vnd")
        if re.search(r"\bvnd\b", text) or (
                re.search(r"\bdong\b", text)
                and not re.search(r"\b(?:nghin|ngan|trieu|ty) dong\b", text)):
            line_units.add("vnd")
        if (re.search(r"\b(?:eur|usd|cent|xu)\b", text)
                or "trieu dong" in text or "ty dong" in text
                or not line_units):
            line_units.add("unsupported")
        units.update(line_units)
    if scoped:
        raise DebtSourceError("amount", "Per-source debt money units are unsupported",
                              evidence=scoped)
    if len(units) != 1 or "unsupported" in units:
        raise DebtSourceError("amount", "Debt money unit is unsupported or conflicting",
                              evidence=list(evidence), units=sorted(units))
    unit = next(iter(units))
    multiplier = Decimal(1000) if unit == "thousand_vnd" else Decimal(1)
    return unit, multiplier, evidence


def _day(value: Any, *, field: str, source: str, sheet: str) -> date:
    try:
        return parse_day(value, sheet)
    except (TypeError, ValueError) as exc:
        raise DebtSourceError("column_missing", "Debt date could not be parsed",
                              field=field, source=source) from exc


def _payment_labels(payment_method: Any, channel: Any, *, source: str
                    ) -> tuple[str, bool, str]:
    raw_payment = str(payment_method).strip()
    payment = normalize_label(raw_payment)
    sales_channel = normalize_label(channel)
    known_payments = _CREDIT | _COD | _ORDINARY_PAYMENT
    if payment not in known_payments:
        raise DebtSourceError("label", "Unknown payment method", source=source,
                              label=raw_payment)
    prefixed_marketplace = (sales_channel.startswith("san ")
                            and sales_channel.removeprefix("san ") in _MARKETPLACE)
    if sales_channel not in (_MARKETPLACE | _ORDINARY_CHANNEL) and not prefixed_marketplace:
        raise DebtSourceError("label", "Unknown sales channel", source=source,
                              label=str(channel).strip())
    excluded = "cod" if payment in _COD else (
        "marketplace" if sales_channel in _MARKETPLACE or prefixed_marketplace else "")
    return raw_payment, payment in _CREDIT, excluded


def _coverage_day(readme: str) -> date | None:
    for line in readme.splitlines():
        text = fold(line)
        if "du lieu co den het" not in text:
            continue
        match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", line)
        if not match:
            raise DebtSourceError("column_missing", "README coverage date is invalid")
        try:
            return parse_day(match.group(1))
        except ValueError as exc:
            raise DebtSourceError("column_missing", "README coverage date is invalid") from exc
    return None


def load_debt_ledger(catalog: Catalog, rulebook: str) -> DebtLedger:
    """Build a validated debt ledger from one business catalog.

    The loader never guesses a join, unit or source column.  It either returns a
    fully checked ledger or raises :class:`DebtSourceError` with a stable code.
    """
    sections = parse_sections(catalog.readme)
    sections += _profile_sections(catalog, sections)
    references, readme_lines = _debt_references(catalog.readme)
    if not _has_debt_hint(catalog, sections, references, readme_lines):
        if _documented_sources_complete(catalog, sections):
            raise DebtSourceError("absent", "README documents no debt source")
        raise DebtSourceError("column_missing", "README is incomplete for debt detection")
    required_references = {"payment_amount", "order_total"}
    if not required_references <= set(references.values()):
        code = "partial" if not references else "column_missing"
        raise DebtSourceError(code, "README debt convention is incomplete",
                              fields=sorted(required_references - set(references.values())))
    rules = _rule_rows(rulebook)
    roles, mappings = _role_sections(catalog, sections, references)
    money_unit, money_multiplier, money_evidence = _money_unit(catalog.readme)

    order_schema = [(section, fields) for section, fields in roles["orders"]]
    if all("customer_id" in fields for _section, fields in order_schema):
        uses_customer_codes = True
        customer_mode_basis = [
            {"source": section.label, "column": fields["customer_id"]}
            for section, fields in order_schema
        ]
    elif all("customer_id" not in fields and "customer_name" in fields
             for _section, fields in order_schema):
        uses_customer_codes = False
        customer_mode_basis = [
            {"source": section.label, "column": fields["customer_name"]}
            for section, fields in order_schema
        ]
    else:
        raise DebtSourceError(
            "customer_mode",
            "README/schema does not identify customers consistently by code or name",
            sources=[section.label for section, _fields in order_schema],
        )

    customers: dict[str, str] = {}
    for section, table_key, ordinal, row in _rows(catalog, roles.get("customers", [])):
        source = f"{table_key}#{ordinal}"
        _required(row, ("customer_id", "customer_name"), role="customers", source=source)
        key = str(row["customer_id"]).strip()
        if key in customers:
            raise DebtSourceError("key", "Customer list repeats a customer id",
                                  source=source)
        customers[key] = str(row["customer_name"]).strip()

    raw_orders: list[dict[str, Any]] = []
    seen_orders: set[str] = set()
    label_counts: Counter[str] = Counter()
    for section, table_key, ordinal, row in _rows(catalog, roles["orders"]):
        source = f"{table_key}#{ordinal}"
        _required(row, ("order_id", "order_day", "status", "order_total",
                        "payment_method", "channel"), role="orders", source=source)
        order_id = str(row["order_id"]).strip()
        if order_id in seen_orders:
            raise DebtSourceError("key", "Order list repeats an order id", source=source)
        seen_orders.add(order_id)
        state = order_state(row["status"])
        if state is None:
            raise DebtSourceError("label", "Unknown order status", source=source,
                                  label=str(row["status"]).strip())
        payment_method, on_credit, excluded = _payment_labels(
            row["payment_method"], row["channel"], source=source)
        label_counts[f"state:{state}"] += 1
        label_counts[f"payment:{normalize_label(payment_method)}"] += 1
        label_counts[f"channel:{normalize_label(row['channel'])}"] += 1
        raw_orders.append({
            "order_id": order_id,
            "day": _day(row["order_day"], field="order_day", source=source,
                        sheet=section.sheet or ""),
            "state": state,
            "total": _amount(row["order_total"], field="order_total", source=source,
                             multiplier=money_multiplier),
            "payment_method": payment_method,
            "on_credit": on_credit,
            "excluded": excluded,
            "customer_id": _text(row.get("customer_id")),
            "customer_name": _text(row.get("customer_name")),
            "closing_debt": row.get("closing_debt"),
            "closing_column": "closing_debt" in row,
            "source": source,
        })
    if not raw_orders:
        raise DebtSourceError("column_missing", "Order debt source has no records")

    orders: list[DebtOrder] = []
    closing: dict[str, Decimal] = {}
    for raw in raw_orders:
        customer_id = raw["customer_id"]
        if not uses_customer_codes and customer_id:
            raise DebtSourceError("customer_mode", "A no-code shop produced a customer code",
                                  source=raw["source"])
        name = customers.get(customer_id or "") or raw["customer_name"]
        if (not uses_customer_codes or customer_id is None) and name and normalize_label(
                name) in _WALK_IN_NAMES:
            customer_id, name = None, None
        if raw["closing_column"] and not _blank(raw["closing_debt"]):
            closing[raw["order_id"]] = _amount(
                raw["closing_debt"], field="closing_debt", source=raw["source"],
                multiplier=money_multiplier)
        orders.append(DebtOrder(
            raw["order_id"], raw["day"], raw["state"], raw["total"],
            raw["payment_method"], raw["on_credit"], raw["excluded"],
            customer_id, name, raw["source"],
        ))

    payments: list[DebtPayment] = []
    zero_payments = 0
    for section, table_key, ordinal, row in _rows(catalog, roles["payments"]):
        source = f"{table_key}#{ordinal}"
        _required(row, ("order_id", "payment_day", "payment_amount"),
                  role="payments", source=source)
        order_id = str(row["order_id"]).strip()
        if order_id not in seen_orders:
            raise DebtSourceError("orphan", "Payment references a missing order",
                                  source=source)
        amount = _amount(row["payment_amount"], field="payment_amount", source=source,
                         multiplier=money_multiplier)
        zero_payments += amount == 0
        payments.append(DebtPayment(
            order_id,
            _day(row["payment_day"], field="payment_day", source=source,
                 sheet=section.sheet or ""),
            amount,
            source,
        ))

    observed_last = max([order.day for order in orders] + [payment.day for payment in payments])
    documented_last = _coverage_day(catalog.readme)
    last_day = documented_last or observed_last
    if last_day < observed_last:
        raise DebtSourceError("reconcile", "README coverage ends before a debt record",
                              last_day=last_day.isoformat(), observed=observed_last.isoformat())

    paid: dict[str, Decimal] = defaultdict(Decimal)
    for payment in payments:
        if payment.day <= last_day:
            paid[payment.order_id] += payment.amount
    checked = 0
    closing_column_present = any(raw["closing_column"] for raw in raw_orders)
    if closing_column_present:
        for order in orders:
            if order.state == "huy" or order.excluded:
                continue
            if order.order_id not in closing:
                raise DebtSourceError("reconcile", "Closing debt is missing for an order",
                                      source=order.source)
            calculated = max(order.total - paid[order.order_id], Decimal(0))
            if calculated != closing[order.order_id]:
                raise DebtSourceError("reconcile", "Closing debt does not match payments",
                                      source=order.source)
            checked += 1

    source_labels = {
        role: [section.label for section, _fields in entries]
        for role, entries in roles.items()
    }
    columns = {
        role: [{"source": section.label, "origin": section.origin,
                "fields": dict(sorted(fields.items()))}
               for section, fields in entries]
        for role, entries in roles.items()
    }
    lineage = {
        "adapter_version": ADAPTER_VERSION,
        "sources": source_labels,
        "columns": columns,
        "readme": list(readme_lines),
        "rules": list(rules),
        "checks": {
            "orders_unique": len(orders),
            "payments_joined": len(payments),
            "zero_payments": zero_payments,
            "orphan_payments": 0,
            "closing_debt_present": closing_column_present,
            "closing_debt_checked": checked,
        },
        "labels": dict(sorted(label_counts.items())),
        "coverage": {
            "first_day": min(order.day for order in orders).isoformat(),
            "last_day": last_day.isoformat(),
            "source": "readme" if documented_last else "observed",
        },
        "money": {
            "unit": money_unit,
            "multiplier_to_vnd": str(money_multiplier),
            "evidence": list(money_evidence),
        },
        "customer_identity": {
            "uses_customer_codes": uses_customer_codes,
            "basis": customer_mode_basis,
        },
        "mappings": mappings,
    }
    return DebtLedger(tuple(orders), tuple(payments), min(order.day for order in orders),
                      last_day, uses_customer_codes, lineage)
