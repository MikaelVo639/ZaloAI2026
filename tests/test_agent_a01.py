import csv
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from datamate.agent import compute
from datamate.agent.baseline import BaselineAgent
from datamate.agent.local_plan import LocalPlanAgent
from datamate.agent.validation import check_executed
from datamate.contracts import BusinessContext, Filter, Question, Split
from datamate.demo import COLUMNS
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.debt import DebtSourceError
from test_contract_c01 import (
    CODES_ALL_MISSING, MARKETPLACE, NO_CODES, NO_CODES_EXPECTED, SAME_NAME_TWO_CODES, X,
    contract_plans, pay,
)
from test_ingestion_debt import _Shop
from test_s01_grammar import rulebook


def failed_debt_source(cache, **kwargs):
    raise RuntimeError("Synthetic debt worker failure")


class A01Tests(_Shop):
    def context(self, catalog, book=None):
        split = Split(self.data.parent, (), rulebook() if book is None else book,
                      self.data.parent / "calendar.csv")
        return BusinessContext(split, catalog)

    @staticmethod
    def debt_question(day=12):
        return Question(
            "route-only", "shop",
            f"2 khách nợ nhiều nhất đến hết ngày {day}/01/2026 kèm số tiền còn nợ",
            date(2026, 1, 31),
        )

    def sales_catalog(self, march_amount="100.2", coverage=""):
        root = self.data / "shop"
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text(coverage, encoding="utf-8")
        with (root / "sales.csv").open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([
                COLUMNS,
                ("F", "2026-02-01", "completed", "P", "Trà", 1, "100.1"),
                ("C", "2026-02-28", "cancelled", "P", "Trà", 1, "900"),
                ("M", "2026-03-31", "completed", "P", "Trà", 1, march_amount),
            ])
        return build_catalog(self.data, "shop")

    def test_revenue_trend_through_planner_worker_and_validation(self):
        question = Question("route-only", "shop", "Doanh thu 2 tháng 2/2026 3/2026 có tăng "
                            "liên tục không?", date(2026, 3, 31))
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        for amount, expected in (("100.2", True), ("100.1", False), ("99.9", False)):
            with self.subTest(amount=amount):
                # Each fixture is a new source snapshot. Start a fresh worker cache.
                agent.worker.stop()
                context = self.context(self.sales_catalog(amount))
                result = agent.answer(question, context)
                self.assertEqual(result.type, "boolean", result.reason)
                self.assertIs(result.answer, expected)
                self.assertEqual([p["revenue"] for p in result.evidence["periods"]], ["100.1", amount])
                self.assertIs(result.evidence["comparisons"][0]["greater"], expected)

        partial = self.context(self.sales_catalog(coverage="Dữ liệu có đến hết 30/03/2026."))
        agent.worker.stop()
        self.assertEqual(agent.answer(question, partial).type, "abstain")

    def test_debt_table_uses_d01_for_both_backends_and_the_snapshot(self):
        catalog = self.catalog()
        for agent in (BaselineAgent(), LocalPlanAgent(model_path="unused-in-grammar-test")):
            with self.subTest(backend=type(agent).__name__):
                self.addCleanup(agent.close)
                context = self.context(catalog)
                first = agent.answer(self.debt_question(), context)
                self.assertEqual(first.type, "table", first.reason)
                self.assertEqual(first.answer, {
                    "columns": ["khach_hang", "so_tien_no"],
                    "rows": [["An", 75001], ["Bình", 50000]],
                })
                self.assertIn("money", first.evidence["ledger"])
                last = agent.answer(self.debt_question(31), context)
                self.assertEqual(last.answer["rows"], [["Bình", 50000], ["An", 25001]])
                self.assertNotIn("worker_sales", context.cache)
                if isinstance(agent, LocalPlanAgent):
                    self.assertFalse(agent.planner_worker.alive)

    def test_debt_absence_requires_documented_sources(self):
        root = self.data / "shop"
        root.mkdir(parents=True)
        (root / "orders.csv").write_text("Mã,Hạn\nD1,12/01/2026\n", encoding="utf-8")
        readme = root / "README.md"
        readme.write_text(
            "## File\n- `orders.csv`\n## Cột trong `orders.csv`\n"
            "- **Mã**: mã đơn\n- **Hạn**: hạn thanh toán\n", encoding="utf-8",
        )
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        result = agent.answer(self.debt_question(), self.context(build_catalog(self.data, "shop")))
        self.assertEqual(result.type, "unanswerable")
        self.assertIs(result.evidence["debt_source_error"]["sources_complete"], True)

        readme.write_text("## File\n- `orders.csv`\n", encoding="utf-8")
        result = agent.answer(self.debt_question(), self.context(build_catalog(self.data, "shop")))
        self.assertEqual(result.type, "abstain")
        self.assertEqual(result.evidence["debt_source_error"]["code"], "column_missing")

    def test_reconcile_and_worker_failure_are_abstentions(self):
        catalog = self.catalog(payments=[["Mã đơn", "Ngày thu", "Tiền thu"],
                                         ["D1", "01/01/2026", "1"]])
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        result = agent.answer(self.debt_question(), self.context(catalog))
        self.assertEqual(result.type, "abstain")
        self.assertEqual(result.evidence["debt_source_error"]["code"], "reconcile")
        agent.prepare_debt = failed_debt_source
        failed = agent.answer(self.debt_question(), self.context(catalog))
        self.assertEqual(failed.type, "abstain")
        self.assertEqual(failed.evidence["ingestion_execution"]["status"], "error")

    def test_debt_ranking_matches_customer_key_contract_and_keeps_walk_ins_in_total(self):
        ranked, orders, total = compute.debt_ranking(NO_CODES, X)
        self.assertEqual({key: amount for key, _, amount, _ in ranked}, NO_CODES_EXPECTED["rows"])
        self.assertEqual((orders, total), (6, NO_CODES_EXPECTED["total"]))
        self.assertEqual(ranked[0][1], "Chị Lan")
        ranked, orders, total = compute.debt_ranking(CODES_ALL_MISSING, X)
        self.assertEqual((ranked, orders, total), ([], 2, Decimal(420000)))
        ranked, _, _ = compute.debt_ranking(SAME_NAME_TWO_CODES, X)
        self.assertEqual([entry[0] for entry in ranked], ["KH01", "KH02"])
        self.assertEqual(ranked[0][1], ranked[1][1])
        ranking, _, _ = compute.debt_ranking(MARKETPLACE, X)
        self.assertEqual(ranking[0][2], Decimal(50000))
        paid = replace(MARKETPLACE, payments=MARKETPLACE.payments + (pay("D2", 100000),))
        self.assertEqual(compute.debt_ranking(paid, X), ([], 0, Decimal(0)))

    def test_debt_loader_cache_keeps_typed_failures_and_survives_sales_preparation(self):
        catalog = self.catalog()
        cache = {}
        with patch.object(compute, "load_debt_ledger", return_value=NO_CODES) as load:
            self.assertTrue(compute.prepare_debt(cache, catalog, rulebook())["ok"])
            with patch.object(compute, "load_business_sales", return_value=([], {}, None)):
                compute.prepare_sales(cache, catalog, rulebook())
            self.assertTrue(compute.prepare_debt(cache, catalog, rulebook())["ok"])
            self.assertEqual(load.call_count, 1)
            compute.prepare_debt(cache, catalog, rulebook("table"))
            self.assertEqual(load.call_count, 2)
        with patch.object(compute, "load_debt_ledger",
                          side_effect=DebtSourceError("partial", "synthetic")) as load:
            for _ in range(2):
                self.assertEqual(compute.prepare_debt(cache, catalog, "")["debt_source_error"]["code"],
                                 "partial")
            self.assertEqual(load.call_count, 1)

    def test_executor_and_validation_reject_conditions_or_inconsistent_evidence(self):
        catalog = self.catalog()
        plan = replace(contract_plans()["top_debt_customers"], start=date(2026, 1, 12),
                       end=date(2026, 1, 12))
        for invalid in (replace(plan, filters=(Filter("customer", "An"),)),
                        replace(plan, top_k=True), replace(plan, start=date(2026, 1, 11))):
            self.assertEqual(compute.answer_plan({}, catalog, invalid, rulebook()).type, "abstain")
        self.assertEqual(compute.answer_plan({}, catalog, plan, rulebook("table")).type, "abstain")
        result = compute.answer_plan({}, catalog, plan, rulebook())
        self.assertEqual(check_executed(result, plan).type, "table")
        forged = deepcopy(result)
        forged.evidence["ranking"][1]["customer_key"] = forged.evidence["ranking"][0]["customer_key"]
        self.assertEqual(check_executed(forged, plan).type, "abstain")
        forged = deepcopy(result)
        forged.answer["rows"][0][1] = 1
        self.assertEqual(check_executed(forged, plan).type, "abstain")
