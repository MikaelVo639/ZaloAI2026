"""Column roles read from headers when the README does not describe a list's columns.

A header is matched on its folded words against closed patterns for the fields the sales
loader needs. A list is described this way only when every needed field maps to exactly one
column; ``readme_sales`` then checks the arithmetic of the rows (amount = price x quantity -
discount) before trusting the guessed amount. Anything ambiguous leaves the list undescribed,
so the question abstains as before.

The patterns were measured against the column meanings the TRAIN READMEs document
(scripts/header_roles_check.py); they hold generic sales-ledger words, not any shop's names.
"""

from __future__ import annotations

import re

from datamate.semantics.text import fold

# field -> (meaning the README loader understands, pattern on the folded header)
_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("order_id", "mã đơn",
     r"(?:ma|so) (?:don(?: hang)?|hoa don|hd|bill|ct|chung tu|giao dich|gd|phieu)"
     r"|don so|so don|order id|ma don ban"),
    ("date", "ngày bán",
     r"ngay(?: (?:ban|hd|hoa don|dat(?: hang)?|giao dich|lap|mua|thang|chung tu|ct))?"
     r"|ngayban|ngay ban hang|thoi gian"),
    ("status", "trạng thái đơn",
     r"(?:trang thai|tinh trang)(?: (?:don(?: hang)?|xu ly|giao|hoa don))?|xu ly|ket qua|status"),
    ("product_id", "mã hàng",
     r"ma (?:hang(?: hoa| ban)?|hh|sp|san pham(?: ban)?|mat hang|sku)|sku|ma sku|id sp|barcode"
     r"|ma vach"),
    ("product_name", "tên hàng",
     r"(?:ten )?(?:hang(?: hoa| ban)?|san pham|sp|mat hang|mon)|ten hang(?: hoa)?|tenhang"
     r"|ten goi|noi dung|product"),
    ("quantity_base", "số lượng đã đổi ra đơn vị cơ bản",
     r"(?:so luong|sl|s l)(?: (?:quy doi|le|dv co ban|don vi co ban|co ban|giao|thuc giao))+"
     r"|quy ra (?:dv|don vi) le"),
    ("quantity", "số lượng",
     r"so luong(?: (?:ban|hang|san pham))?|sl(?: ban)?|s l|soluong|sluong|slg"),
    ("unit", "đơn vị tính", r"dvt|don vi(?: tinh)?|dv(?: tinh)?|donvi|unit"),
    ("unit_price", "đơn giá",
     r"don gia(?: ban)?(?: d)?|gia(?: ban)?(?: le)?|gia (?:1|mot) dv|gia don vi|gia san pham"
     r"|gia niem yet|price"),
    ("discount", "giảm giá", r"giam(?: gia)?(?: d)?|chiet khau|khuyen mai"),
    ("amount", "thành tiền sau giảm",
     r"thanh tien(?: d)?|t tien|amount|tien ban|tong tien|tri gia|tong|tien"),
    ("customer_id", "mã khách",
     r"(?:ma|so) (?:khach(?: hang| mua)?|kh(?: mua)?)|customer id"),
    ("customer_name", "tên khách",
     r"(?:ten )?khach(?: hang)?(?: ten)?|ten khach(?: hang)?|tenkh|ten kh|doi tac|ben mua"),
    ("time", "giờ đặt đơn", r"gio(?: tao don| dat don| ban)?|thoi gian tao"),
    ("payment_method", "hình thức thanh toán",
     r"(?:hinh thuc|phuong thuc)(?: (?:thanh toan|tt))?|pttt|httt|tra bang"),
    ("channel", "kênh bán", r"kenh(?: ban)?|nguon(?: don)?|ban qua|loai ban"),
    ("staff_id", "mã nhân viên bán", r"ma nv(?: ban)?|nhan vien(?: ban)?|nguoi ban"),
)
_COMPILED = tuple((field, meaning, re.compile(rf"^(?:{pattern})$"))
                  for field, meaning, pattern in _FIELDS)
# A guessed sales line list needs these. The status is required: a status column the
# patterns miss would let cancelled orders into revenue. Without an order id column every
# row is one record, as the README loader reads such lists.
LINE_FIELDS = frozenset({"date", "status", "amount"})
# A list is guessed only when its file or sheet name says it is about sales; imports, stock,
# prices or customers lists may carry the same headers (Số phiếu, Ngày, Thành tiền).
_SALES_NAME = re.compile(r"\b(?:ban|ban hang|hoa don|don hang|don ban|sales?|orders?|bill)\b")
_OTHER_NAME = re.compile(
    r"\b(?:nhap(?: hang)?|ton(?: kho)?|kho|bang gia|lich su gia|khach hang|phieu thu|thu tien"
    r"|chi phi|cong no|so no|giao hang|tra hang|tra gop|gop|bao hanh|lo|lo hang)\b")


def _name_text(file: str, sheet: str) -> str:
    words = " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part) for part in (file, sheet))
    return fold(re.sub(r"\.[A-Za-z0-9]+$", "", words.replace("_", " ")))


def sales_list_name(file: str, sheet: str) -> bool:
    """Whether the file or sheet names a sales list (words split on case, "_", "-")."""
    text = _name_text(file, sheet)
    return bool(_SALES_NAME.search(text)) and not _OTHER_NAME.search(text)


def other_list_name(file: str, sheet: str) -> bool:
    """Whether the file or sheet names a list that is not about sales (imports, stock...)."""
    return bool(_OTHER_NAME.search(_name_text(file, sheet)))


def _split_words(header: str) -> str:
    """"SLQuyDoi" -> "SL Quy Doi", "MaHoaDon" -> "Ma Hoa Don" (database-style headers);
    all-capital abbreviations such as "ĐVT" stay whole."""
    out = []
    for i, char in enumerate(header):
        if i and char.isupper():
            before, after = header[i - 1], header[i + 1:i + 2]
            if before.islower() or (before.isupper() and after.islower()):
                out.append(" ")
        out.append(char)
    return "".join(out)


def header_field(header: str) -> str | None:
    """The field a header names, or None (first pattern wins: more specific ones first)."""
    text = fold(_split_words(header))
    for field, _, pattern in _COMPILED:
        if pattern.match(text):
            return field
    return None


def looks_like_sales_lines(headers: list[str] | tuple[str, ...]) -> bool:
    """A dated list with money: a product catalogue or a price list is neither."""
    fields = {header_field(header) for header in headers}
    return {"date", "amount"} <= fields


def header_columns(headers: list[str] | tuple[str, ...],
                   provided: frozenset[str] = frozenset()) -> dict[str, str] | None:
    """README-style ``{column: meaning}`` for a list of headers, or None when a field is
    named by two columns or the list is not a sales line list. ``provided``: fields the
    README conventions already give another column (left out of ``headers``)."""
    by_field: dict[str, list[str]] = {}
    for header in headers:
        field = header_field(header)
        if field:
            by_field.setdefault(field, []).append(header)
    if any(len(columns) > 1 for columns in by_field.values()):
        return None
    # Product names are enough for totals; with names only the loader marks product identity
    # unverified, and product rankings, counts and filters abstain (they went wrong on train).
    if (not LINE_FIELDS - provided <= set(by_field)
            or not {"product_id", "product_name"} & set(by_field)):
        return None
    meanings = {field: meaning for field, meaning, _ in _FIELDS}
    return {columns[0]: meanings[field] for field, columns in by_field.items()}


# Joined lists (an order list and a line list sharing the order id, as POS exports keep
# them): each table is given one role from its headers alone, and readme_sales then checks
# the line arithmetic and that every line finds its order.
_PURCHASE_HEADER = re.compile(r"\b(?:nhap|nha cung cap|ncc)\b")
_STOCK_HEADER = re.compile(r"^ton(?: (?:cuoi(?: ky)?|kho|thuc|hien tai|cuoi ngay))?$")
_CONVERSION_HEADER = re.compile(r"\b(?:quy doi|he so|dv lon|don vi lon)\b")
_PRODUCT_GROUP = re.compile(
    r"^(?:nhom hang(?: \d+ cap)?|danh muc(?: hang)?|phan loai|nhom (?:sp|san pham|mat hang))$")
_CUSTOMER_GROUP = re.compile(r"^(?:nhom khach(?: hang)?|loai khach(?: hang)?|nhom kh)$")
_LINE_KEEP = ("order_id", "product_id", "product_name", "quantity", "quantity_base", "unit",
              "unit_price", "discount", "amount")
_ORDER_KEEP = ("order_id", "date", "status", "customer_id", "customer_name", "channel",
               "payment_method", "staff_id", "time")


def table_role(headers: list[str] | tuple[str, ...]) -> tuple[str, dict[str, str]] | None:
    """``(role, {column: README meaning})`` for one table of a joined export, or None.

    Roles: "lines" (order id, product, quantity, unit price and amount; no date or status),
    "orders" (order id, date and status; no product or quantity), "products" (product code
    and name only) and "customers" (customer code, name and group). A field named by two
    columns, or any purchase / supplier header, leaves the table without a role.
    """
    folded = {header: fold(_split_words(header)) for header in headers}
    if any(_PURCHASE_HEADER.search(text) for text in folded.values()):
        return None
    by_field: dict[str, list[str]] = {}
    for header in headers:
        field = header_field(header)
        if field:
            by_field.setdefault(field, []).append(header)
    fields = set(by_field)
    meanings = {field: meaning for field, meaning, _ in _FIELDS}

    def described(keep: tuple[str, ...], renamed: dict[str, str] | None = None
                  ) -> dict[str, str] | None:
        if any(len(by_field.get(field, ())) > 1 for field in keep):
            return None
        return {by_field[field][0]: (renamed or {}).get(field, meanings[field])
                for field in keep if field in by_field}

    def one(pattern: re.Pattern[str]) -> str | None:
        found = [header for header, text in folded.items() if pattern.match(text)]
        return found[0] if len(found) == 1 else None

    if ({"order_id", "quantity", "unit_price", "amount"} <= fields
            and fields & {"product_id", "product_name"} and not fields & {"date", "status"}):
        columns = described(_LINE_KEEP)
        return ("lines", columns) if columns else None
    if ({"order_id", "date", "status"} <= fields
            and not fields & {"quantity", "quantity_base", "unit_price", "product_id",
                              "product_name"}):
        # "Số khách" in a restaurant bill is a head count, not a customer code.
        keep = tuple(field for field in _ORDER_KEEP if not (
            field == "customer_id" and field in by_field
            and folded[by_field[field][0]] == "so khach"))
        columns = described(keep)
        return ("orders", columns) if columns else None
    if ({"product_id", "product_name"} <= fields
            and not fields & {"order_id", "quantity", "quantity_base", "amount", "date"}
            and not any(_STOCK_HEADER.match(text) for text in folded.values())):
        columns = described(("product_id", "product_name"), {"product_name": "tên"})
        if columns is None:
            return None
        if (len(by_field.get("unit", ())) == 1
                and not any(_CONVERSION_HEADER.search(text) for text in folded.values())):
            columns[by_field["unit"][0]] = "đơn vị cơ bản"
        group = one(_PRODUCT_GROUP)
        if group:
            columns[group] = "nhóm hàng"
        return "products", columns
    if ("customer_id" in fields and "customer_name" in fields
            and not fields & {"order_id", "quantity", "amount", "product_id"}):
        columns = described(("customer_id", "customer_name"))
        group = one(_CUSTOMER_GROUP)
        if columns is None or group is None:
            return None
        return "customers", {**columns, group: "nhóm khách"}
    return None
