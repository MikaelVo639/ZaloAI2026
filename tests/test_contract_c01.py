"""C01: contract for boolean/table and debt intents (docs/AGENT_02_CONTRACT.md).

These tests lock the agreed interface and ensure that the backend only enables the
C01 intents with an executor. The examples in the contract document stay consistent
with the code.

The synthetic ledgers below are the contract's reference cases for customer keys (§6.5)
and new debt (§7). ``reference_*`` restate the contract in a few lines so that the
expected values stay consistent with it; A01 checks its executor against the same
fixtures and expected values, it does not import these helpers.
"""
import json
import re
import unittest
from collections import Counter, defaultdict
from dataclasses import FrozenInstanceError, fields
from datetime import date
from decimal import Decimal
from pathlib import Path

from test_ingestion_debt import RULEBOOK as D01_RULEBOOK
from test_ingestion_debt import _Shop

from datamate.agent import compute
from datamate.agent.compute import WALK_IN_NAMES
from datamate.agent.local_plan import _capabilities
from datamate.agent.validation import check_executed
from datamate.contracts import (
    DEBT_TABLE_COLUMNS,
    SNAPSHOT_INTENTS,
    Candidate,
    Catalog,
    DebtLedger,
    DebtOrder,
    DebtPayment,
    Filter,
    Plan,
    Threshold,
)
from datamate.demo import RULEBOOK
from datamate.evaluation.validation import validate_record
from datamate.ingestion.debt import load_debt_ledger
from datamate.semantics.text import fold

DOC = Path(__file__).resolve().parents[1] / "docs" / "AGENT_02_CONTRACT.md"
NEW_INTENTS = ("revenue_increasing", "top_debt_customers", "debt_total",
               "debt_customer_count", "debt_collected", "debt_flow")
X = date(2026, 4, 12)
MONTHS = ((date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 28)),
          (date(2026, 3, 1), date(2026, 3, 31)))


def contract_plans() -> dict[str, Plan]:
    """One Plan per new intent, built exactly as the contract describes it."""
    return {
        "revenue_increasing": Plan("revenue_increasing", MONTHS[0][0], MONTHS[-1][1],
                                   periods=MONTHS),
        "top_debt_customers": Plan("top_debt_customers", X, X, top_k=2),
        "debt_total": Plan("debt_total", X, X),
        "debt_total_aged": Plan("debt_total", X, X, threshold=Threshold(
            "order", "age_days", ">", Decimal(7))),
        "debt_customer_count": Plan("debt_customer_count", X, X),
        "debt_collected": Plan("debt_collected", date(2026, 3, 1), date(2026, 3, 31)),
        "debt_flow": Plan("debt_flow", date(2026, 3, 1), date(2026, 3, 31),
                          filters=(Filter("customer", "Khách mẫu A"),)),
    }


def doc_json_blocks() -> list[dict]:
    text = DOC.read_text(encoding="utf-8")
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.S)]


class SharedInterfaceTest(unittest.TestCase):
    def test_existing_plan_construction_is_unchanged(self):
        plan = Plan("revenue", date(2026, 3, 1), date(2026, 3, 31), 1, ("row",))
        self.assertEqual((plan.periods, plan.filters, plan.threshold, plan.comparison),
                         ((), (), None, ""))

    def test_snapshot_intents_and_table_columns(self):
        self.assertEqual(SNAPSHOT_INTENTS,
                         {"top_debt_customers", "debt_total", "debt_customer_count"})
        self.assertEqual(DEBT_TABLE_COLUMNS, ("khach_hang", "so_tien_no"))
        for name, plan in contract_plans().items():
            if plan.intent in SNAPSHOT_INTENTS:
                self.assertEqual(plan.start, plan.end, name)

    def test_debt_source_types(self):
        order = DebtOrder("D1", X, "hoan thanh", Decimal("150000.5"), "Ghi nợ", True,
                          customer_id="KH01", customer_name="Khách mẫu A")
        payment = DebtPayment("D1", X, Decimal("50000"))
        ledger = DebtLedger((order,), (payment,), X, X, uses_customer_codes=True)
        self.assertEqual(order.excluded, "")
        self.assertEqual(ledger.lineage, {})
        self.assertIsInstance(order.total, Decimal)
        with self.assertRaises(FrozenInstanceError):
            order.total = Decimal(0)
        self.assertNotIn("line_amount", {f.name for f in fields(DebtOrder)})


    def test_ledger_requires_explicit_customer_code_mode(self):
        with self.assertRaises(TypeError):
            DebtLedger((), (), X, X)  # uses_customer_codes has no default on purpose


def order(order_id: str, total: int, *, code: str | None = None, name: str | None = None,
          day: date = date(2026, 4, 1), state: str = "hoan thanh", on_credit: bool = True,
          excluded: str = "") -> DebtOrder:
    return DebtOrder(order_id, day, state, Decimal(total), "Ghi nợ" if on_credit else "Tiền mặt",
                     on_credit, excluded, code, name)


def pay(order_id: str, amount: int, day: date = date(2026, 4, 1)) -> DebtPayment:
    return DebtPayment(order_id, day, Decimal(amount))


# Shop without customer codes: customers by the name on the order (§6.5).
NO_CODES = DebtLedger((
    order("D1", 300000, name="Chị Lan"),
    order("D2", 200000, name="chị  lan"),           # same customer, other spelling
    order("D3", 150000, name="Anh Minh"),
    order("D4", 90000, name="Khách lẻ"),           # walk-in: total only
    order("D5", 60000, name="kl"),                 # walk-in: total only
    order("D6", 80000),                            # no name: walk-in
), (pay("D1", 100000),), date(2026, 4, 1), X, uses_customer_codes=False)
NO_CODES_EXPECTED = {"rows": {"chị lan": Decimal(400000), "anh minh": Decimal(150000)},
                     "total": Decimal(780000), "customers": 2}

# Shop that uses codes, but every order of this extract lacks a code: all walk-in.
CODES_ALL_MISSING = DebtLedger((
    order("D1", 300000, name="Chị Lan"),
    order("D2", 120000, name="Anh Minh"),
), (), date(2026, 4, 1), X, uses_customer_codes=True)
CODES_ALL_MISSING_EXPECTED = {"rows": {}, "total": Decimal(420000), "customers": 0}

# Two codes, same name: two customers, two rows; ties go by code.
SAME_NAME_TWO_CODES = DebtLedger((
    order("D1", 50000, code="KH02", name="Nguyễn Văn An"),
    order("D2", 50000, code="KH01", name="Nguyễn Văn An"),
    order("D3", 70000, name="Nguyễn Văn An"),      # no code in a shop with codes: walk-in
), (), date(2026, 4, 1), X, uses_customer_codes=True)
SAME_NAME_TWO_CODES_EXPECTED = {"rows": {"KH01": Decimal(50000), "KH02": Decimal(50000)},
                                "total": Decimal(170000), "customers": 2}

# debt_flow: an order paid above its total on the order day adds no debt (never negative).
OVERPAID = DebtLedger((
    order("D1", 100000, code="KH01", name="Khách mẫu A"),
    order("D2", 200000, code="KH01", name="Khách mẫu A"),
), (pay("D1", 120000), pay("D2", 50000), pay("D2", 30000, date(2026, 4, 5))),
    date(2026, 4, 1), X, uses_customer_codes=True)
OVERPAID_FLOW_EXPECTED = [Decimal(150000), Decimal(30000)]  # [nợ thêm, trả được]

# COD / marketplace orders are not customer debt (rulebook §4), even when marked "ghi nợ":
# they add nothing to nợ thêm nor to tiền thu nợ. A plain credit order is the control.
MARKETPLACE = DebtLedger((
    order("D1", 100000, code="KH01", name="Khách mẫu A", excluded="marketplace"),
    order("D2", 80000, code="KH01", name="Khách mẫu A", day=date(2026, 4, 2)),
    order("D3", 50000, code="KH01", name="Khách mẫu A", day=date(2026, 4, 3), excluded="cod"),
), (pay("D1", 30000, date(2026, 4, 5)), pay("D2", 20000, date(2026, 4, 2)),
    pay("D2", 10000, date(2026, 4, 6)), pay("D3", 50000, date(2026, 4, 7))),
    date(2026, 4, 1), X, uses_customer_codes=True)
MARKETPLACE_ONLY = DebtLedger(MARKETPLACE.orders[:1], MARKETPLACE.payments[:1],
                              date(2026, 4, 1), X, uses_customer_codes=True)
MARKETPLACE_FLOW_EXPECTED = [Decimal(60000), Decimal(10000)]  # only D2 counts
MARKETPLACE_COLLECTED_EXPECTED = Decimal(10000)


def customer_key(debt_order: DebtOrder, ledger: DebtLedger) -> str | None:
    """Contract §6.5, restated: the code in a shop with codes, else the normalized name."""
    if ledger.uses_customer_codes:
        return debt_order.customer_id
    name = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (debt_order.customer_name or "").lower()))
    name = name.strip()
    # Only case, spacing and punctuation are folded; diacritics stay (contract §6.5).
    return None if not name or fold(name) in WALK_IN_NAMES else name


def reference_debts(ledger: DebtLedger, x: date) -> dict:
    """Contract §5 steps 1-3 and §7 debt_total / debt_customer_count, restated."""
    paid = defaultdict(Decimal)
    for payment in ledger.payments:
        if payment.day <= x:
            paid[payment.order_id] += payment.amount
    rows, total = defaultdict(Decimal), Decimal(0)
    for debt_order in ledger.orders:
        if debt_order.day > x or debt_order.state == "huy" or debt_order.excluded:
            continue
        rest = debt_order.total - paid[debt_order.order_id]
        if rest <= 0:
            continue
        total += rest
        key = customer_key(debt_order, ledger)
        if key is not None:
            rows[key] += rest
    return {"rows": dict(rows), "total": total, "customers": len(rows)}


def reference_flow(ledger: DebtLedger, start: date, end: date,
                   key: str | None = None) -> list[Decimal]:
    """Contract §7, restated: [nợ thêm, trả được] of one customer (debt_flow), or of every
    credit order when ``key`` is None (the second number is then debt_collected)."""
    payments = defaultdict(list)
    for payment in ledger.payments:
        payments[payment.order_id].append(payment)
    new = repaid = Decimal(0)
    for debt_order in ledger.orders:
        if (not debt_order.on_credit or debt_order.state == "huy" or debt_order.excluded
                or (key is not None and customer_key(debt_order, ledger) != key)):
            continue
        own = payments[debt_order.order_id]
        if start <= debt_order.day <= end:
            same_day = sum((p.amount for p in own if p.day == debt_order.day), Decimal(0))
            new += max(debt_order.total - same_day, Decimal(0))
        repaid += sum((p.amount for p in own if p.day > debt_order.day and start <= p.day <= end),
                      Decimal(0))
    return [new, repaid]


def ledger_problems(ledger: DebtLedger) -> list[str]:
    """Contract §6.4 check 7, restated."""
    if not ledger.uses_customer_codes and any(o.customer_id for o in ledger.orders):
        return ["customer_mode"]
    return []


class CustomerKeyAndFlowReferenceTest(unittest.TestCase):
    """Reference cases from Agent reviews (customer keys, new debt, COD / marketplace);
    A01 reuses the fixtures and expectations for its executor."""

    def test_shop_without_customer_codes_groups_by_name(self):
        self.assertEqual(reference_debts(NO_CODES, X), NO_CODES_EXPECTED)

    def test_shop_with_codes_but_no_code_on_any_order_has_no_rows(self):
        self.assertEqual(reference_debts(CODES_ALL_MISSING, X), CODES_ALL_MISSING_EXPECTED)

    def test_two_codes_with_the_same_name_are_two_customers(self):
        result = reference_debts(SAME_NAME_TWO_CODES, X)
        self.assertEqual(result, SAME_NAME_TWO_CODES_EXPECTED)
        ranked = sorted(result["rows"].items(), key=lambda kv: (-kv[1], kv[0]))
        self.assertEqual([key for key, _ in ranked], ["KH01", "KH02"])
        names = Counter(o.customer_name for o in SAME_NAME_TWO_CODES.orders if o.customer_id)
        self.assertEqual(names, {"Nguyễn Văn An": 2})

    def test_codes_on_orders_of_a_shop_without_codes_are_a_data_error(self):
        broken = DebtLedger((order("D1", 1000, code="KH01", name="A"),), (), X, X,
                            uses_customer_codes=False)
        self.assertEqual(ledger_problems(broken), ["customer_mode"])
        for ledger in (NO_CODES, CODES_ALL_MISSING, SAME_NAME_TWO_CODES, OVERPAID, MARKETPLACE):
            self.assertEqual(ledger_problems(ledger), [])

    def test_cod_and_marketplace_orders_are_not_debt_flow(self):
        april = (date(2026, 4, 1), X)
        self.assertEqual(reference_flow(MARKETPLACE_ONLY, *april, "KH01"), [0, 0])
        self.assertEqual(reference_flow(MARKETPLACE, *april, "KH01"), MARKETPLACE_FLOW_EXPECTED)
        self.assertEqual(reference_flow(MARKETPLACE, *april)[1], MARKETPLACE_COLLECTED_EXPECTED)
        self.assertEqual(reference_debts(MARKETPLACE, X)["total"], Decimal(50000))

    def test_new_debt_is_never_negative(self):
        flow = reference_flow(OVERPAID, date(2026, 4, 1), date(2026, 4, 30), "KH01")
        self.assertEqual(flow, OVERPAID_FLOW_EXPECTED)
        self.assertEqual(reference_flow(OVERPAID, date(2026, 4, 1), date(2026, 4, 1), "KH01"),
                         [Decimal(150000), Decimal(0)])


class RealAdapterLedgerTest(_Shop):
    """The same rules on a ledger built by D01 from files: store credit, marketplace credit
    and COD in one shop (follow-up of the PR #27 review, after D01 merged)."""

    ORDERS = [
        ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Kênh", "Mã khách", "Tên khách",
         "Còn nợ"],
        ["D1", "01/01/2026", "Xong", "100000", "Ghi nợ", "Shopee", "K1", "An", ""],
        ["D2", "02/01/2026", "Xong", "80000", "Ghi nợ", "Store", "K1", "An", "50000"],
        ["D3", "03/01/2026", "Xong", "50000", "COD", "Store", "K1", "An", ""],
        ["D4", "04/01/2026", "Xong", "60000", "Ghi nợ", "phone", "K2", "Bình", "60000"],
    ]
    PAYMENTS = [
        ["Mã đơn", "Ngày thu", "Tiền thu"],
        ["D1", "05/01/2026", "30000"],
        ["D2", "02/01/2026", "20000"],
        ["D2", "06/01/2026", "10000"],
        ["D3", "07/01/2026", "50000"],
    ]

    def test_d01_ledger_gives_the_contract_numbers(self):
        ledger = load_debt_ledger(self.catalog(orders=self.ORDERS, payments=self.PAYMENTS),
                                  D01_RULEBOOK)
        self.assertEqual([o.excluded for o in ledger.orders], ["marketplace", "", "cod", ""])
        x = date(2026, 1, 31)
        self.assertEqual(reference_debts(ledger, x), {
            "rows": {"K1": Decimal(50000), "K2": Decimal(60000)},
            "total": Decimal(110000), "customers": 2})
        ranked, open_orders, total = compute.debt_ranking(ledger, x)
        self.assertEqual([(key, name, amount) for key, name, amount, _ in ranked],
                         [("K2", "Bình", Decimal(60000)), ("K1", "An", Decimal(50000))])
        self.assertEqual((open_orders, total), (2, Decimal(110000)))
        # §7: only D2 and D4 add debt; only D2's later payment is collected.
        self.assertEqual(reference_flow(ledger, date(2026, 1, 1), x),
                         [Decimal(120000), Decimal(10000)])
        self.assertEqual(reference_flow(ledger, date(2026, 1, 1), x, "K1"),
                         [Decimal(60000), Decimal(10000)])


class ContractBackendsTest(unittest.TestCase):
    def test_baseline_implements_every_contract_intent(self):
        # §4-§5 (boolean, table) and the four §7 debt intents on the same D01 ledger.
        for name, plan in contract_plans().items():
            self.assertEqual(compute.unsupported(plan), "", name)

    def test_answer_plan_abstains_before_reading_data(self):
        catalog = Catalog("biz_demo", "", ())
        for name, plan in contract_plans().items():
            candidate = compute.answer_plan({}, catalog, plan)
            self.assertEqual(candidate.type, "abstain", name)

    def test_validation_rejects_boolean_and_table_without_calculation_evidence(self):
        evidence = {"code": {"call": "f()", "source": "def f(): ..."}}
        outputs = {
            "revenue_increasing": ("boolean", True),
            "top_debt_customers": ("table", {"columns": list(DEBT_TABLE_COLUMNS),
                                             "rows": [["Khách mẫu A", 420000]]}),
        }
        plans = contract_plans()
        for intent, (kind, answer) in outputs.items():
            candidate = Candidate(kind, answer, 1.0, "", {**evidence, "result": answer})
            self.assertEqual(check_executed(candidate, plans[intent]).type, "abstain", intent)

    def test_model_planner_does_not_gain_new_intents(self):
        self.assertTrue(set(_capabilities(RULEBOOK)).isdisjoint(NEW_INTENTS))


class ContractDocumentTest(unittest.TestCase):
    def test_plan_examples_use_existing_fields(self):
        plan_fields = {f.name for f in fields(Plan)}
        plans = [block for block in doc_json_blocks() if "intent" in block]
        self.assertEqual({p["intent"] for p in plans},
                         {"revenue_increasing", "top_debt_customers"})
        for example in plans:
            self.assertLessEqual(set(example), plan_fields, example["intent"])
            start, end = date.fromisoformat(example["start"]), date.fromisoformat(example["end"])
            if example["intent"] in SNAPSHOT_INTENTS:
                self.assertEqual(start, end)
            periods = [tuple(map(date.fromisoformat, p)) for p in example.get("periods", [])]
            if periods:
                self.assertEqual((start, end), (periods[0][0], periods[-1][1]))
                self.assertEqual(periods, sorted(periods))

    def test_output_examples_are_valid_submission_records(self):
        outputs = [block for block in doc_json_blocks() if "type" in block]
        self.assertEqual({o["type"] for o in outputs}, {"table"})
        for output in outputs:
            validate_record({"id": "example", **output})
            self.assertEqual(tuple(output["answer"]["columns"]), DEBT_TABLE_COLUMNS)
            self.assertTrue(all(type(row[1]) is int for row in output["answer"]["rows"]))


if __name__ == "__main__":
    unittest.main()
