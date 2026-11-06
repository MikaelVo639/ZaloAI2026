"""C01: contract for boolean/table and debt intents (docs/AGENT_02_CONTRACT.md).

These tests lock the agreed interface before S01/D01/A01 implement it: the new intents
and the debt source types exist, the current backends refuse every new intent instead of
answering it partially, and the examples in the contract document stay consistent with
the code. A01 updates the "refuses" tests when it enables an intent with its regressions.
"""
import json
import re
import unittest
from dataclasses import FrozenInstanceError, fields
from datetime import date
from decimal import Decimal
from pathlib import Path

from datamate.agent import compute
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
        ledger = DebtLedger((order,), (payment,), X, X)
        self.assertEqual(order.excluded, "")
        self.assertEqual(ledger.lineage, {})
        self.assertIsInstance(order.total, Decimal)
        with self.assertRaises(FrozenInstanceError):
            order.total = Decimal(0)
        self.assertNotIn("line_amount", {f.name for f in fields(DebtOrder)})


class CurrentBackendsRefuseNewIntentsTest(unittest.TestCase):
    def test_baseline_reports_every_new_intent_unsupported(self):
        for name, plan in contract_plans().items():
            self.assertTrue(compute.unsupported(plan), name)

    def test_answer_plan_abstains_before_reading_data(self):
        catalog = Catalog("biz_demo", "", ())
        for name, plan in contract_plans().items():
            candidate = compute.answer_plan({}, catalog, plan)
            self.assertEqual(candidate.type, "abstain", name)

    def test_validation_rejects_boolean_and_table_for_unimplemented_plans(self):
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
