"""Questions about information a sales export does not usually hold.

``asked_attribute`` names the kind of information a question needs when that kind is outside
the measures the planner computes: customer gender or address, staff qualities, the reason
for a cancellation or return, drug dosage. It only reads the question; whether the shop
records that information is decided against the data (agent/absence.py), and only an
abstained question is ever turned into ``unanswerable``.

Patterns are deliberately narrow; anything not matched stays with the planner or abstains.
"""

from __future__ import annotations

import re

from datamate.semantics.text import normalize_question

_CUSTOMER = r"\bkhach(?: hang)?"
_PATTERNS = (
    ("customer_gender", rf"{_CUSTOMER} (?:nam|nu)\b|\bgioi tinh\b"),
    ("customer_address", rf"{_CUSTOMER}(?: (?:o|nha o|song o|tai|thuoc))?"
                         r" (?:duong|pho|phuong|quan)\b|\bdia chi\b"),
    ("staff_quality", r"\b(?:tay nghe|lanh nghe|tan tam|tan tinh|le phep|chu dao|quy men"
                      r"|hay khen|duoc khen|thai do|nhiet tinh|than thien)\b"),
    # "huỷ vì giao trễ", "trả hàng vì lỗi"; not "trả bằng ví điện tử" ("vì" and "ví" fold alike)
    ("order_reason", r"\b(?:huy|tra hang|tra lai|doi tra|tra)\b(?: (?!bang\b)[a-z]+){0,2}"
                     r" (?:vi|ly do)\b(?! dien tu)|\bly do (?:huy|tra|doi tra)\b"),
    ("dosage", r"\b(?:uong|dung) moi lan\b|\blieu (?:dung|luong|uong)\b|\bmoi lan (?:uong|dung)\b"),
    # Revenue split by a label the shop may not record at all (rulebook §1, §7).
    ("sales_channel", r"\b(?:cac )?don (?:ban )?(?:qua|tai|tren) (?:dien thoai|quay|zalo|facebook"
                      r"|fb|shopee|lazada|tiktok|website|web|san|app|online)\b|\bkenh ban\b"),
    ("payment_method", r"\b(?:tra|thanh toan) bang (?:tien mat|chuyen khoan|the|vi dien tu|cod"
                       r"|momo|qr)\b|\b(?:hinh thuc|phuong thuc) thanh toan\b"),
    ("customer_group", r"\bkhach(?: hang)? nhom\b|\bnhom khach\b"),
)


def asked_attribute(question: str) -> str:
    """The unrecorded-kind of information the question asks about, or ``""``."""
    text = normalize_question(question)
    for name, pattern in _PATTERNS:
        if re.search(pattern, text):
            return name
    return ""
