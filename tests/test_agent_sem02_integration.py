"""Synthetic executor -> AGENT-02 validation regressions for the SEM-02 contract."""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from datamate.agent import compute
from datamate.agent.validation import check_executed
from datamate.contracts import Catalog, Filter, Plan, Threshold
from datamate.ingestion.sales import Sale


class ExtendedExecutionTests(unittest.TestCase):
    periods = ((date(2026, 1, 1), date(2026, 1, 31)),
               (date(2026, 2, 1), date(2026, 2, 28)),
               (date(2026, 3, 1), date(2026, 3, 31)))

    def setUp(self):
        self.catalog = Catalog("routing-only", "", ())
        self.sales = [
            Sale("A", date(2026, 1, 5), "valid", Decimal(100), "P1", "Trà", Decimal("0.1"),
                 base_unit="kg", product_group="Đồ uống", customer_id="K1", customer_name="Lan"),
            Sale("B", date(2026, 2, 5), "valid", Decimal(300), "P1", "Trà", Decimal(2),
                 base_unit="kg", product_group="Đồ uống", customer_id="K1", customer_name="Lan"),
            Sale("C", date(2026, 3, 5), "valid", Decimal(200), "P2", "Bánh", Decimal(3),
                 base_unit="cái", product_group="Đồ ăn", customer_id="K2", customer_name="Mai"),
        ]

    def execute(self, plan):
        cache = {"sales_key": compute._catalog_key(self.catalog), "sales_rulebook": "",
                 "sales": (self.sales, {"tables": [], "sales_rows": len(self.sales)}),
                 "ranking_eligible": None}
        return compute.answer_plan(cache, self.catalog, plan)

    def test_extended_answers_survive_validation(self):
        start, end = self.periods[0][0], self.periods[-1][1]
        threshold = Threshold("customer", "order_count", ">=", Decimal(2))
        cases = [
            (Plan("revenue", start, end, periods=self.periods, shape="series"),
             "list", [100, 300, 200]),
            (Plan("revenue", start, end, periods=self.periods, shape="best_period"),
             "date", "2026-02"),
            (Plan("top_customers", start, end, top_k=2, rank_by="revenue"), "list", ["Lan", "Mai"]),
            (Plan("top_customers", start, end, rank_by="order_count"), "string", "Lan"),
            (Plan("count_where", start, end, threshold=threshold), "number", 1),
            (Plan("count_where", start, end, threshold=threshold, comparison="share"), "percent", 50.0),
            (Plan("quantity", *self.periods[0], unit="kg"), "number", 0.1),
            (Plan("revenue", start, end, comparison="share",
                  filters=(Filter("product_group", "Đồ uống"),)), "percent", 66.67),
        ]
        for plan, kind, answer in cases:
            with self.subTest(plan=plan):
                candidate = check_executed(self.execute(plan), plan)
                self.assertEqual((candidate.type, candidate.answer), (kind, answer), candidate.reason)

    def test_series_rejects_wrong_length_or_nonnumeric_values(self):
        plan = Plan("revenue", self.periods[0][0], self.periods[-1][1],
                    periods=self.periods, shape="series")
        for values in ([100, 300], [100, "300", 200], [100, True, 200]):
            candidate = self.execute(plan)
            candidate.answer = candidate.evidence["result"] = values
            with self.subTest(values=values):
                self.assertEqual(check_executed(candidate, plan).type, "abstain")

    def test_best_month_must_be_one_of_the_requested_periods(self):
        plan = Plan("revenue", date(2026, 1, 1), date(2026, 3, 31),
                    periods=(self.periods[0], self.periods[2]), shape="best_period")
        for value in ("2026-02", "2026-03-05", "2025-12"):
            candidate = self.execute(plan)
            candidate.answer = candidate.evidence["result"] = value
            with self.subTest(value=value):
                self.assertEqual(check_executed(candidate, plan).type, "abstain")

    def test_ignored_comparison_fields_and_fractional_order_thresholds_abstain(self):
        plan = Plan("revenue", date(2026, 1, 1), date(2026, 3, 31), comparison="share",
                    filters=(Filter("product_group", "Đồ uống"),))
        invalid = [replace(plan, compare_start=date(2025, 1, 1), compare_end=date(2025, 3, 31)),
                   Plan("count_where", plan.start, plan.end,
                        threshold=Threshold("customer", "order_count", ">=", Decimal("1.5"))),
                   Plan("revenue", plan.start, plan.end, periods=(self.periods[0], self.periods[0]),
                        shape="series")]
        for item in invalid:
            with self.subTest(plan=item):
                self.assertEqual(self.execute(item).type, "abstain")
