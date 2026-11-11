"""Đợt 3 gói 1: words a question may use for a payment method / sales channel of one shop.

The shop's own labels come from its data (``Sale.payment_method``, ``Sale.channel``). A
question word is accepted for a label only when

* it is the label itself, after normalisation ("zalo_oa" ~ "zalo oa", "Thu hộ (COD)" ~
  "thu ho cod", "cod", "thu ho"); or
* the README describes the label with it ("cash tiền mặt, transfer chuyển khoản, …"); or
* the label is one of the few standard English export codes below and the README gives no
  description of that label. The glossary is language, not shop-specific: it never names a
  shop and never overrides a README description.

A word that would name two labels of the same kind is dropped, so the question abstains.
"""

import re
from collections.abc import Iterable

from datamate.semantics.text import normalize_question

FIELDS = {"payment": "payment_method", "channel": "channel"}  # Filter.field -> Sale attribute

# Standard export codes -> Vietnamese wording, used only when the README is silent.
_GLOSSARY = {
    "payment": {
        "cash": ("tien mat",),
        "transfer": ("chuyen khoan",),
        "bank transfer": ("chuyen khoan",),
        "ewallet": ("vi dien tu",),
        "e wallet": ("vi dien tu",),
        "cod": ("cod",),
        "debt": ("ghi no",),
    },
    "channel": {
        "store": ("tai quay", "tai cua hang"),
        "phone": ("dien thoai",),
        "zalo oa": ("zalo oa",),
    },
}
# Chat abbreviations of the Vietnamese wording, question side only.
_ABBREVIATIONS = {"tien mat": ("tm", "tien tuoi"), "chuyen khoan": ("ck",)}


def _key(value: str) -> str:
    return normalize_question(value.replace("_", " ")).strip()


def label_descriptions(meanings: Iterable[str]) -> dict[str, list[str]]:
    """``{label key: [description words]}`` from README column meanings such as
    "hình thức thanh toán của đơn: cash tiền mặt, transfer chuyển khoản, …"."""
    found: dict[str, list[str]] = {}
    for meaning in meanings:
        if ":" not in meaning:
            continue
        for item in meaning.split(":", 1)[1].split(","):
            words = _key(item).split()
            if len(words) >= 2:
                found.setdefault(words[0], []).append(" ".join(words[1:]))
    return found


def label_terms(labels: Iterable[str], meanings: Iterable[str], kind: str) -> list[str]:
    """``"term|label"`` pairs for the planner; terms naming two labels are dropped.

    ``meanings`` are the README meanings of the shop's columns holding this field.
    """
    described = label_descriptions(meanings)
    terms: dict[str, set[str]] = {}
    for label in sorted(set(labels)):
        key = _key(label)
        if not key:
            continue
        words = {key}
        inner = re.findall(r"\(([^)]*)\)", label)
        if inner:  # "Thu hộ (COD)": the code in brackets and the words outside
            words |= {_key(x) for x in inner} | {_key(re.sub(r"\([^)]*\)", " ", label))}
        if key in described:
            words |= set(described[key])
        else:
            words |= set(_GLOSSARY[kind].get(key, ()))
        for word in list(words):
            words |= set(_ABBREVIATIONS.get(word, ()))
        for word in words:
            if word:
                terms.setdefault(word, set()).add(label)
    return sorted(f"{term}|{next(iter(owners))}" for term, owners in terms.items()
                  if len(owners) == 1)
