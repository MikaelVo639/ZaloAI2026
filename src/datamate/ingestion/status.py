"""Order status labels -> the four order states of the current split's rulebook.

The rulebook (§ "Cách ghi trong file", row "Trạng thái đơn") says every order has exactly
one of four states: hoàn thành, huỷ, trả hàng, đang giao. Shops write those states with
their own labels ("Xong", "khách hủy", "Đổi trả", "Đang vận chuyển"...). A label maps to
a state only when

1. it *is* the state name, possibly with the usual particles ("đã", "đơn", "hàng"); or
2. it is listed in the reviewed synonym table below; or
3. by elimination over one status column (``by_elimination``): the column's other labels
   name exactly three states, this is its only unknown label, and it shares the core word
   of the fourth state ("Khách trả" when "trả hàng" is the state left).

Anything else raises: an unknown label is never guessed, so the business abstains. The
mapping to revenue status also needs the rulebook rows that say which states count:
without them no label is accepted at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from datamate.semantics.rules import canonical, rule_line, table_rows
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
        "shipping", "dang ship",
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


@dataclass(frozen=True)
class StatusPolicy:
    """Maps the four states to the adapter's ``valid`` / ``cancelled`` / ``returned``."""

    revenue_status: dict[str, str]
    evidence: tuple[str, ...]
    eliminated: tuple[tuple[str, str], ...] = ()  # (normalised label, state) by elimination

    def resolve(self, label: object) -> str:
        state = order_state(label) or dict(self.eliminated).get(normalize_label(label))
        if state is None:
            raise StatusError(f"Unknown status {label!r}")
        if state not in self.revenue_status:
            raise StatusError(f"Status {label!r} ({state}) has no rule in this split")
        return self.revenue_status[state]


# The word of each state a shop's own label keeps ("Khách trả", "Đã hủy", "Đang ship").
_CORE_WORDS = {"hoan thanh": {"hoan", "thanh", "xong"}, "huy": {"huy"}, "tra hang": {"tra"},
               "dang giao": {"giao", "ship", "chuyen"}}


def eliminated_labels(labels: object) -> dict[str, str]:
    """``{normalised label: state}`` for the one label of a status column read by
    elimination (see ``by_elimination``), or ``{}``."""
    return dict(by_elimination(StatusPolicy({}, ()), labels).eliminated)


def by_elimination(policy: StatusPolicy, labels: object) -> StatusPolicy:
    """``policy`` that also reads the one unknown label of a status column by elimination.

    ``labels`` are the distinct labels of one column. The rulebook says every order has
    exactly one of the four states, so when the known labels name exactly three states and
    one label is unknown, that label is the fourth state, provided it shares the state's
    core word. Otherwise ``policy`` is returned unchanged (unknown labels still raise).
    """
    known: set[str] = set()
    unknown: set[str] = set()
    for label in labels:
        text = normalize_label(label)
        if not text:
            continue
        state = order_state(text)
        (known.add(state) if state else unknown.add(text))
    missing = [state for state in STATES if state not in known]
    if len(unknown) != 1 or len(missing) != 1:
        return policy
    label, state = unknown.pop(), missing[0]
    if not set(label.split()) & _CORE_WORDS[state]:
        return policy
    return StatusPolicy(policy.revenue_status, policy.evidence, ((label, state),))


def status_policy(rulebook: str) -> StatusPolicy:
    """Read which states exist and which count as revenue from the split's rulebook."""
    rows = table_rows(rulebook)
    states = rule_line(rows, "trang thai don", *STATES)
    revenue = rule_line(rows, "don tinh vao doanh thu", "khong huy", "khong tra")
    if states is None or revenue is None:
        raise StatusError("The split rulebook does not define the order states")
    mapping = {"hoan thanh": "valid", "huy": "cancelled", "tra hang": "returned"}
    cancelled = rule_line(rows, "don bi huy", "trang thai huy")
    evidence = [states, revenue] + ([cancelled] if cancelled else [])
    if canonical("dang giao van tinh") in canonical(fold(revenue)):
        mapping["dang giao"] = "valid"
    return StatusPolicy(mapping, tuple(evidence))
