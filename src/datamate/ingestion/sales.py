"""Deliberately narrow flat-sales adapter; add separate adapters for joined exports."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from datamate.contracts import Catalog
from datamate.ingestion.catalog import iter_rows
from datamate.semantics.text import fold

ALIASES = {
    "order_id": ("order_id", "Mã đơn hàng", "Mã đơn", "Mã hóa đơn", "Số hóa đơn"),
    "date": ("order_date", "Ngày đặt hàng", "Ngày đặt", "Ngày bán", "Ngày", "Thời gian"),
    "status": ("status", "Trạng thái đơn hàng", "Trạng thái đơn", "Trạng thái"),
    "amount": ("line_amount", "Thành tiền", "Tổng tiền", "T.Tiền"),
    "product_id": ("product_id", "Mã hàng", "Mã hàng hóa", "Mã sản phẩm", "SKU"),
    "product_name": ("product_name", "Tên hàng", "Tên hàng hóa", "Tên sản phẩm", "Sản phẩm"),
    "quantity_base": ("quantity_base", "Số lượng quy đổi", "SL quy đổi"),
}
STATUSES = {
    "completed": "valid", "hoan thanh": "valid", "da hoan thanh": "valid", "ok": "valid",
    "dang giao": "valid", "dang giao hang": "valid", "shipping": "valid",
    "cancelled": "cancelled", "canceled": "cancelled", "huy": "cancelled", "da huy": "cancelled",
    "khach huy": "cancelled",
    "returned": "returned", "tra hang": "returned", "da tra hang": "returned",
    "da hoan hang": "returned",
}


@dataclass(frozen=True)
class Sale:
    order_id: str
    day: date
    status: str
    amount: Decimal
    product_id: str | None
    product_name: str | None
    quantity_base: Decimal | None
    # Optional context, None when the source does not record it (DATA-03 / SEM-02).
    base_unit: str | None = None
    product_group: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    customer_group: str | None = None
    channel: str | None = None
    # Minutes after midnight when the order was placed (rulebook shifts); None when the
    # source records no time for the order or its lines disagree.
    minute: int | None = None
    staff_id: str | None = None  # "mã nhân viên bán" of the order, as written
    quantity_sold: Decimal | None = None  # quantity in the selling unit
    unit_cost_at_sale: Decimal | None = None
    product_id_is_name: bool = False
    # "hình thức thanh toán" of the order, as written in the file (e.g. "cash", "Tiền mặt").
    # Matching a question word to it goes through the README's description of the labels.
    payment_method: str | None = None


def parse_minute(value: Any) -> int | None:
    """Clock time of a time or date-time cell ("14:05", "01/03/2026 14:05:09"), in minutes.

    None when the cell holds no clock time; seconds are dropped (13:59:59 is 13:59).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (datetime, time)):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float, Decimal)):
        fraction = Decimal(str(value))
        if not fraction.is_finite() or not 0 <= fraction < 1:
            return None  # a spreadsheet time is a fraction of a day
        # Round to the second first: 14:00 stored as 0.58333… must not become 13:59.
        return int((fraction * 86400).to_integral_value()) // 60
    match = re.search(r"(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?![\d:])", str(value))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def parse_number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid numeric value: {value!r}")
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Non-finite numeric value")
        return result
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"(?:VNĐ|VND|đ|₫)$", "", text, flags=re.IGNORECASE)
    if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", text):
        text = text.replace(",", "").replace(".", "")
    elif "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("Non-finite numeric value")
    return result


def parse_date(value: Any, sheet: str = "") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2}) thg (\d{1,2}) (\d{4})", fold(text))
    if match:
        day, month, year = map(int, match.groups())
        return date(year, month, day)
    year = re.search(r"\b(20\d{2})\b", sheet)
    if year and re.fullmatch(r"\d{1,2}/\d{1,2}", text):
        day, month = map(int, text.split("/"))
        return date(int(year.group(1)), month, day)
    raise ValueError(f"Unsupported date {value!r} in {sheet}")


def column_mapping(columns: tuple[str, ...]) -> dict[str, str]:
    mapping = {}
    for key, aliases in ALIASES.items():
        choices = [c for c in columns if fold(c) in {fold(a) for a in aliases}]
        if len(choices) == 1:
            mapping[key] = choices[0]
    return mapping


def load_sales(catalog: Catalog) -> tuple[list[Sale], dict[str, Any]]:
    tables = []
    for table in catalog.tables:
        mapping = column_mapping(table.columns)
        if {"order_id", "date", "status", "amount"}.issubset(mapping) and (
            "product_name" in mapping or "product_id" in mapping
        ):
            tables.append((table, mapping))
    if not tables:
        raise ValueError("No supported flat sales table; add a schema adapter")
    sales = []
    seen = set()
    order_states: dict[str, tuple[date, str]] = {}
    raw_count = 0
    for table, mapping in tables:
        carry: dict[str, Any] = {}
        for raw in iter_rows(table):
            raw_count += 1
            row = {key: raw.get(column) for key, column in mapping.items()}
            if not any(value not in (None, "") for value in row.values()):
                continue
            if row.get("order_id") not in (None, "") and row["order_id"] != carry.get("order_id"):
                carry = {}
            for key in ("order_id", "date", "status"):
                if row.get(key) in (None, ""):
                    row[key] = carry.get(key)
                else:
                    carry[key] = row[key]
            if row.get("order_id") in (None, ""):
                raise ValueError(f"Missing order id in {table.key}")
            order_id = str(row["order_id"])
            # Same order + identical cells => export duplicate, as specified by rulebook.
            fingerprint = (order_id, tuple((k, str(v)) for k, v in sorted(raw.items())))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            day = parse_date(row["date"], table.name)
            status = STATUSES.get(fold(row["status"]))
            if status is None:
                raise ValueError(f"Unknown status {row['status']!r} in {table.key}")
            if order_id in order_states and order_states[order_id] != (day, status):
                raise ValueError(f"Conflicting date/status for order {order_id}")
            order_states[order_id] = (day, status)
            sales.append(Sale(
                order_id, day, status, parse_number(row["amount"]),
                str(row["product_id"]) if row.get("product_id") not in (None, "") else None,
                str(row["product_name"]) if row.get("product_name") not in (None, "") else None,
                parse_number(row["quantity_base"])
                if row.get("quantity_base") not in (None, "") else None,
            ))
    return sales, {"tables": [t.key for t, _ in tables], "raw_rows": raw_count,
                   "sales_rows": len(sales), "duplicates_removed": raw_count - len(sales)}
