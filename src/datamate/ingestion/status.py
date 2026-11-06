"""Order status labels -> the four order states of the current split's rulebook.

The rulebook (§ "Cách ghi trong file", row "Trạng thái đơn") says every order has exactly
one of four states: hoàn thành, huỷ, trả hàng, đang giao. Shops write those states with
their own labels ("Xong", "khách hủy", "Đổi trả", "Đang vận chuyển"...). A label maps to
a state only when

1. it *is* the state name, possibly with the usual particles ("đã", "đơn", "hàng"); or
2. it is listed in the reviewed synonym table below.

Anything else raises: an unknown label is never guessed, so the business abstains. The
mapping to revenue status also needs the rulebook rows that say which states count:
without them no label is accepted at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from datamate.semantics.text import fold

STATES = ("hoan thanh", "huy", "tra hang", "dang giao")

# Reviewed synonyms, folded. Each entry is a label seen in exported sales files whose
# meaning is one of the four states; keep this list closed and small.
SYNONYMS: dict[str, frozenset[str]] = {
    "hoan thanh": frozenset({
        "xong", "da xong", "ok", "thanh cong", "da lay", "da giao", "completed", "complete",
        "done",
    }),
    "huy": frozenset({
        "khach huy", "huy don", "khong giao duoc", "cancelled", "canceled", "cancel",
    }),
    "tra hang": frozenset({
        "doi tra", "hoan tra", "hang tra ve", "hoan hang", "tra lai", "returned",
    }),
    "dang giao": frozenset({
        "dang giao dich", "cho giao", "dang xu ly", "dang gui", "dang van chuyen",
        "shipping",
    }),
}

_LEADING = ("da ", "don ")
_TRAILING = (" don", " hang")


class StatusError(ValueError):
    """A status label or the rules needed to read it are missing."""


def normalize_label(label: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", fold(str(label)))).strip()


def order_state(label: object) -> str | None:
    """Return one of ``STATES`` for a raw label, or ``None`` when the label is unknown."""
    text = normalize_label(label)
    if not text:
        return None
    candidates = {text}
    for prefix in _LEADING:
        if text.startswith(prefix):
            candidates.add(text[len(prefix):])
    for value in list(candidates):
        for suffix in _TRAILING:
            if value.endswith(suffix):
                candidates.add(value[:-len(suffix)])
    for state in STATES:
        if state in candidates:
            return state
    for state, labels in SYNONYMS.items():
        if candidates & labels:
            return state
    return None


def _rule_rows(rulebook: str) -> list[tuple[str, str, str]]:
    rows = []
    for line in rulebook.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2:
            rows.append((fold(cells[0]), fold(cells[1]), line.strip()))
    return rows


def _row(rows: list[tuple[str, str, str]], key: str, *tokens: str) -> str | None:
    for row_key, value, original in rows:
        if row_key == key and all(token in value for token in tokens):
            return original
    return None


@dataclass(frozen=True)
class StatusPolicy:
    """Maps the four states to the adapter's ``valid`` / ``cancelled`` / ``returned``."""

    revenue_status: dict[str, str]
    evidence: tuple[str, ...]

    def resolve(self, label: object) -> str:
        state = order_state(label)
        if state is None:
            raise StatusError(f"Unknown status {label!r}")
        if state not in self.revenue_status:
            raise StatusError(f"Status {label!r} ({state}) has no rule in this split")
        return self.revenue_status[state]


def status_policy(rulebook: str) -> StatusPolicy:
    """Read which states exist and which count as revenue from the split's rulebook."""
    rows = _rule_rows(rulebook)
    states = _row(rows, "trang thai don", *STATES)
    revenue = _row(rows, "don tinh vao doanh thu", "khong huy", "khong tra")
    if states is None or revenue is None:
        raise StatusError("The split rulebook does not define the order states")
    mapping = {"hoan thanh": "valid", "huy": "cancelled", "tra hang": "returned"}
    cancelled = _row(rows, "don bi huy", "trang thai huy")
    evidence = [states, revenue] + ([cancelled] if cancelled else [])
    if "dang giao van tinh" in fold(revenue):
        mapping["dang giao"] = "valid"
    return StatusPolicy(mapping, tuple(evidence))
