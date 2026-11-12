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
     r"|noi dung|product"),
    ("quantity_base", "số lượng đã đổi ra đơn vị cơ bản",
     r"(?:so luong|sl|s l)(?: (?:quy doi|le|dv co ban|don vi co ban|co ban|giao|thuc giao))+"
     r"|quy ra (?:dv|don vi) le"),
    ("quantity", "số lượng",
     r"so luong(?: (?:ban|hang|san pham))?|sl(?: ban)?|s l|soluong|sluong"),
    ("unit", "đơn vị tính", r"dvt|don vi(?: tinh)?|dv(?: tinh)?|donvi|unit"),
    ("unit_price", "đơn giá",
     r"don gia(?: ban)?(?: d)?|gia(?: ban)?(?: le)?|gia (?:1|mot) dv|gia don vi|gia san pham"
     r"|price"),
    ("discount", "giảm giá", r"giam(?: gia)?(?: d)?|chiet khau"),
    ("amount", "thành tiền sau giảm",
     r"thanh tien(?: d)?|t tien|amount|tien ban|tong tien|tri gia|tong|tien"),
    ("customer_id", "mã khách", r"(?:ma|so) (?:khach(?: hang| mua)?|kh)|customer id"),
    ("customer_name", "tên khách",
     r"(?:ten )?khach(?: hang)?(?: ten)?|ten khach(?: hang)?|tenkh|doi tac|ben mua"),
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


def sales_list_name(file: str, sheet: str) -> bool:
    """Whether the file or sheet names a sales list (words split on case, "_", "-")."""
    words = " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part) for part in (file, sheet))
    text = fold(re.sub(r"\.[A-Za-z0-9]+$", "", words.replace("_", " ")))
    return bool(_SALES_NAME.search(text)) and not _OTHER_NAME.search(text)


def header_field(header: str) -> str | None:
    """The field a header names, or None (first pattern wins: more specific ones first)."""
    text = fold(header)
    for field, _, pattern in _COMPILED:
        if pattern.match(text):
            return field
    return None


def looks_like_sales_lines(headers: list[str] | tuple[str, ...]) -> bool:
    """A dated list with money: a product catalogue or a price list is neither."""
    fields = {header_field(header) for header in headers}
    return {"date", "amount"} <= fields


def header_columns(headers: list[str] | tuple[str, ...]) -> dict[str, str] | None:
    """README-style ``{column: meaning}`` for a list of headers, or None when a field is
    named by two columns or the list is not a sales line list."""
    by_field: dict[str, list[str]] = {}
    for header in headers:
        field = header_field(header)
        if field:
            by_field.setdefault(field, []).append(header)
    if any(len(columns) > 1 for columns in by_field.values()):
        return None
    # Product names are enough for totals; with names only the loader marks product identity
    # unverified, and product rankings, counts and filters abstain (they went wrong on train).
    if not LINE_FIELDS <= set(by_field) or not {"product_id", "product_name"} & set(by_field):
        return None
    meanings = {field: meaning for field, meaning, _ in _FIELDS}
    return {columns[0]: meanings[field] for field, columns in by_field.items()}
