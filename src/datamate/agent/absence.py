"""Information a business does not record at all.

The split rulebook says a question is unanswerable when the shop does not record the kind
of information the question needs ("shop không ghi loại thông tin câu hỏi cần thì không trả
lời được"). A failed computation alone never proves that: the data may be there in a form
the adapters do not read. So a planned question that abstained becomes ``unanswerable``
only when

1. the current split's rulebook has that absence row, and
2. no trace of the concept the intent needs appears anywhere in the business: not in any
   README line, nor in any file or sheet name, nor in the header of any column that holds
   at least one value (exports often carry empty columns such as "Tồn nhỏ nhất").

The word lists are deliberately broad. Any trace of the concept (even an unrelated word
that folds to the same letters, such as "tôn" for "tồn") keeps the abstain.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import iter_rows
from datamate.semantics.text import fold

_STOCK = ("ton", "ton kho", "kho hang", "trong kho", "xuat kho", "nhap kho", "kiem kho",
          "ma lo", "so lo", "lo hang", "nhap", "stock", "inventory", "warehouse")
_PURCHASE = ("nhap", "nha cung cap", "ncc", "supplier", "vendor", "purchase", "mua vao")
_MONEY = ("gia", "tien", "price", "amount", "cost", "value")
_COST = ("gia von", "gia nhap", "tien nhap", "chi phi", "cost", "cogs")
_EXPIRY = ("han su dung", "han dung", "hsd", "het han", "expiry", "expire", "expiration", "exp")
# Kinds of information asked by questions the planner does not cover (semantics/attributes.py).
_GENDER = ("gioi tinh", "nam nu", "gender", "sex")
_ADDRESS = ("dia chi", "duong", "phuong xa", "xa phuong", "huyen", "quan huyen", "khu vuc",
            "address", "street", "ward", "district")  # not "phuong": "phương thức"
_FEEDBACK = ("danh gia", "khen", "phan hoi", "hai long", "cham diem", "xep loai", "review",
             "feedback", "rating")
_REASON = ("ly do", "nguyen nhan", "ghi chu", "noi dung", "mo ta", "reason", "note")
# Labels of an order: any trace of the field keeps the abstain ("Kênh bán", "PhuongThucTT",
# "LoaiKhachHang", a README line naming them).
_CHANNEL = ("kenh", "kenh ban", "channel", "nguon", "nguon don", "ban qua", "tai quay", "zalo",
            "facebook", "shopee", "lazada", "tiktok", "website", "online", "source")
_PAYMENT = ("thanh toan", "phuong thuc", "hinh thuc", "hinh thuc tt", "pttt", "httt", "tien mat",
            "chuyen khoan", "cod", "quet the", "vi dien tu", "momo", "vnpay", "payment", "pay")
_CUSTOMER_GROUP = ("nhom khach", "loai khach", "hang khach", "phan loai khach", "hang thanh vien",
                   "thanh vien", "customer group", "customer type", "segment", "tier", "vip",
                   "than thiet")
_DOSAGE = ("lieu dung", "lieu luong", "lieu uong", "cach dung", "huong dan", "chi dinh",
           "dosage", "usage")  # not "lieu": "dữ liệu"


def _has(text: str, words: Iterable[str]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _cost(text: str) -> bool:
    return _has(text, _COST) or (_has(text, _PURCHASE) and _has(text, _MONEY))


CONCEPTS: dict[str, Callable[[str], bool]] = {
    "stock": lambda text: _has(text, _STOCK),
    "purchase": lambda text: _has(text, _PURCHASE),
    "cost": _cost,
    "expiry": lambda text: _has(text, _EXPIRY),
    "customer_gender": lambda text: _has(text, _GENDER),
    "customer_address": lambda text: _has(text, _ADDRESS),
    "staff_quality": lambda text: _has(text, _FEEDBACK),
    "order_reason": lambda text: _has(text, _REASON),
    "dosage": lambda text: _has(text, _DOSAGE),
    "sales_channel": lambda text: _has(text, _CHANNEL),
    "payment_method": lambda text: _has(text, _PAYMENT),
    "customer_group": lambda text: _has(text, _CUSTOMER_GROUP),
}

INTENT_CONCEPT = {
    "stock": "stock", "stock_split": "stock", "stock_value": "stock",
    "out_of_stock_count": "stock",
    "purchase_total": "purchase", "top_supplier": "purchase",
    "avg_purchase_price": "cost", "net_purchase": "purchase",
    "gross_profit": "cost", "top_gross_profit": "cost", "net_profit": "cost",
    "earliest_expiry": "expiry", "expiring_lot_count": "expiry", "expiring_lot_cost": "expiry",
    "expiring_product_count": "expiry", "expiring_products": "expiry",
}


# Reorder thresholds of sales software ("Tồn lớn nhất", "Định mức tồn tối thiểu") are
# settings, not stock records; every other "tồn" still counts.
_THRESHOLD = re.compile(r"\b(?:dinh muc )?ton (?:nho nhat|lon nhat|toi thieu|toi da)\b")


def words(text: str) -> str:
    """Folded words; camel-case ASCII names are split ("TonKho.xlsx" -> "ton kho xlsx")."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", unicodedata.normalize("NFC", text))
    folded = " ".join(re.sub(r"[^a-z0-9]+", " ", fold(text)).split())
    return _THRESHOLD.sub("nguong dat hang", folded)


_ALL_WORDS = frozenset((*_STOCK, *_PURCHASE, *_MONEY, *_COST, *_EXPIRY, *_GENDER, *_ADDRESS,
                        *_FEEDBACK, *_REASON, *_DOSAGE, *_CHANNEL, *_PAYMENT, *_CUSTOMER_GROUP))


def _filled_columns(table: Table, columns: list[str]) -> set[str]:
    """The given columns that hold a non-blank value in some row."""
    filled: set[str] = set()
    for row in iter_rows(table):
        filled |= {column for column in columns if str(row.get(column) or "").strip()}
        if len(filled) == len(columns):
            break
    return filled


def recorded(catalog: Catalog, concept: str) -> bool:
    """Whether the business shows any trace of ``concept`` (README, names, filled columns)."""
    found = CONCEPTS[concept]
    if any(found(words(line)) for line in catalog.readme.splitlines() if line.strip()):
        return True
    for table in catalog.tables:
        names = f"{words(table.path.name)} {words(table.name)}"
        if found(names):
            return True
        candidates = [str(column) for column in table.columns
                      if _has(words(str(column)), _ALL_WORDS)]
        if not candidates or not found(" ".join([names, *map(words, candidates)])):
            continue
        filled = _filled_columns(table, candidates)
        if found(" ".join([names, *(words(column) for column in candidates if column in filled)])):
            return True
    return False


def missing_concept(catalog: Catalog, intent: str) -> str:
    """The concept ``intent`` needs when the business records no trace of it, else ``""``."""
    concept = INTENT_CONCEPT.get(intent)
    if concept is None or recorded(catalog, concept):
        return ""
    return concept
