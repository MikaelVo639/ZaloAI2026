"""Shared interfaces. Coordinate changes here with all five module owners."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Question:
    id: str
    business_id: str
    question: str
    as_of_date: date


@dataclass(frozen=True)
class Split:
    root: Path
    questions: tuple[Question, ...]
    rulebook: str
    calendar_path: Path


@dataclass(frozen=True)
class Table:
    path: Path
    name: str
    columns: tuple[str, ...]
    sample: tuple[dict[str, Any], ...] = ()
    encoding: str = "utf-8-sig"
    delimiter: str = ","

    @property
    def key(self) -> str:
        return f"{self.path.name}::{self.name}"


@dataclass(frozen=True)
class Catalog:
    business_id: str
    readme: str
    tables: tuple[Table, ...]


@dataclass(frozen=True)
class Filter:
    field: str  # "product" | "product_group" | "customer_group" | "customer" (C01: debt_flow)
    value: str  # canonical value from the business data (e.g. product name as written)
    rule: str = ""  # rulebook row that defines how the filter is read


@dataclass(frozen=True)
class Threshold:
    """Count only the subjects whose measure passes ``op value`` (SEM-02)."""

    subject: str  # "customer" | "order" | "day" | "product"
    measure: str  # "order_count" | "revenue" | "age_days" (C01: debt_total, subject "order")
    op: str  # ">=" | ">" | "<=" | "<"
    value: Decimal
    rule: str = ""  # rulebook row about the boundary ("từ N trở lên thì gồm N")


@dataclass(frozen=True)
class Plan:
    intent: str
    start: date
    end: date
    top_k: int = 1
    evidence: tuple[str, ...] = ()
    # Optional; a backend that does not implement a field that is set must abstain.
    compare_start: date | None = None  # comparison period, already resolved from the rulebook
    compare_end: date | None = None
    comparison: str = ""  # "" | "change_percent" | "difference" | "share"
    filters: tuple[Filter, ...] = ()
    # SEM-02. ``periods`` lists several periods in the order the question names them;
    # ``shape`` says what to return over them: "series" (one value per period) or
    # "best_period" (the period with the highest value). ``start``/``end`` then span them.
    periods: tuple[tuple[date, date], ...] = ()
    shape: str = ""
    threshold: Threshold | None = None
    unit: str = ""  # "bán được bao nhiêu <đơn vị>": the base unit asked for (intent quantity)
    rank_by: str = ""  # top_customers: "revenue" (nhiều tiền) | "order_count" (nhiều đơn)


# C01 (docs/AGENT_02_CONTRACT.md). Intents asked "đến hết ngày X" (rulebook §2): the answer is
# the state at the end of X built from everything since the first day of data, so the Plan
# carries ``start == end == X``. No new Plan field: a backend reads the intent.
SNAPSHOT_INTENTS = frozenset({"top_debt_customers", "debt_total", "debt_customer_count"})
# Column names of the top_debt_customers table, as in the rulebook's table example.
DEBT_TABLE_COLUMNS = ("khach_hang", "so_tien_no")


@dataclass(frozen=True)
class DebtOrder:
    """One order as the debt rules need it (C01). Data fills it from the split's README."""

    order_id: str
    day: date  # ngày đặt
    state: str  # one of ingestion.status.STATES ("hoan thanh", "huy", "tra hang", "dang giao")
    total: Decimal  # tổng phải thu cả đơn: đã trừ giảm giá cả đơn, gồm phí giao; not line revenue
    payment_method: str  # label as written on the order
    on_credit: bool  # the order's payment method is "ghi nợ" (needed by debt_collected/debt_flow)
    excluded: str = ""  # "cod" | "marketplace": không phải khách nợ (rulebook §4); "" otherwise
    # Shop with customer codes (DebtLedger.uses_customer_codes): the code; None = khách lẻ,
    # even when the order carries a name. Shop without codes: always None.
    customer_id: str | None = None
    customer_name: str | None = None  # display name (customer list first, else the order)
    source: str = ""  # lineage: "file::table#row"


@dataclass(frozen=True)
class DebtPayment:
    """One payment for one order: "mỗi lần khách trả tiền cho một đơn là một dòng"."""

    order_id: str
    day: date  # day of the payment time; compared by day, never by time of day
    amount: Decimal
    source: str = ""


@dataclass(frozen=True)
class DebtLedger:
    """Orders and payments of one business, after Data's key/join/reconciliation checks."""

    orders: tuple[DebtOrder, ...]
    payments: tuple[DebtPayment, ...]
    first_day: date  # first order day
    last_day: date  # "Dữ liệu có đến hết …" when the README states it, else the last record day
    # How the shop identifies customers (rulebook §3 "Xác định khách"), decided by Data from
    # the README/schema, never from whether codes happen to appear on the orders:
    # True  = one customer per code; an order without a code is khách lẻ;
    # False = the shop has no customer codes; customers are told apart by the name on the order.
    uses_customer_codes: bool
    lineage: dict[str, Any] = field(default_factory=dict)  # tables, columns, README lines, checks


@dataclass
class Candidate:
    type: str = "abstain"
    answer: Any = None
    confidence: float = 0.0
    reason: str = "Unsupported question"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class BusinessContext:
    split: Split
    catalog: Catalog
    cache: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    def answer(self, question: Question, context: BusinessContext) -> Candidate: ...
