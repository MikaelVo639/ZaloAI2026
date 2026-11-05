import os
import tempfile
import time
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from datamate.agent import compute
from datamate.agent.baseline import BaselineAgent
from datamate.agent.worker import Worker
from datamate.contracts import BusinessContext
from datamate.demo import create_demo
from datamate.ingestion import build_catalog
from datamate.ingestion.sales import Sale
from datamate.io import load_split

# Worker tasks must be module-level: the spawn child imports them by name.


def double(cache, value):
    return value * 2


def remember(cache, value):
    cache.setdefault("seen", []).append(value)
    return list(cache["seen"])


def sleep_for(cache, seconds):
    time.sleep(seconds)
    return "finished"


def missing_column(cache):
    raise KeyError("amount")


def hard_crash(cache):
    os._exit(3)


def unpicklable(cache):
    return lambda: None


def slow_revenue(cache, catalog, plan, rulebook=""):
    if plan.intent == "revenue":
        time.sleep(60)
    return compute.answer_plan(cache, catalog, plan, rulebook)


def failing_calculation(cache, catalog, plan, rulebook=""):
    raise KeyError("amount")


def slow_prepare(cache, catalog, rulebook=""):
    time.sleep(60)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = Worker()
        self.addCleanup(self.worker.close)

    def test_returns_value_and_keeps_cache_between_tasks(self):
        result = self.worker.run(double, 10, value=21)
        self.assertEqual((result.ok, result.value, result.status), (True, 42, "ok"))
        self.worker.run(remember, 10, value="a")
        self.assertEqual(self.worker.run(remember, 10, value="b").value, ["a", "b"])
        self.assertEqual(self.worker.generation, 1)

    def test_exception_is_reported_and_worker_stays_usable(self):
        result = self.worker.run(missing_column, 10)
        self.assertEqual(result.status, "error")
        self.assertIn("KeyError", result.error)
        self.assertEqual(self.worker.run(double, 10, value=1).value, 2)
        self.assertEqual(self.worker.generation, 1)

    def test_timeout_kills_child_and_next_task_gets_fresh_worker(self):
        self.worker.run(remember, 10, value="before")
        started = time.perf_counter()
        result = self.worker.run(sleep_for, 0.5, seconds=60)
        self.assertLess(time.perf_counter() - started, 5)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.execution()["status"], "timeout")
        # The killed child's cache is gone; the replacement starts empty.
        self.assertEqual(self.worker.run(remember, 10, value="after").value, ["after"])
        self.assertEqual(self.worker.generation, 2)

    def test_crashed_child_is_reported_and_replaced(self):
        result = self.worker.run(hard_crash, 10)
        self.assertEqual(result.status, "error")
        self.assertIn("exited", result.error)
        self.assertEqual(self.worker.run(double, 10, value=4).value, 8)

    def test_unpicklable_result_is_an_error(self):
        result = self.worker.run(unpicklable, 10)
        self.assertEqual(result.status, "error")
        self.assertIn("Unsendable", result.error)

    def test_invalid_timeout_is_rejected(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.worker.run(double, timeout, value=1)

    def test_deadline_includes_blocked_send_and_receive(self):
        class Process:
            def is_alive(self):
                return True

        class Connection:
            def __init__(self, slow_step):
                self.slow_step = slow_step

            def send(self, message):
                if self.slow_step == "send":
                    time.sleep(0.2)

            def recv(self):
                if self.slow_step == "recv":
                    time.sleep(0.2)
                return True, 42, ""

        for step in ("send", "recv"):
            with self.subTest(step=step):
                self.worker._process = Process()
                self.worker._connection = Connection(step)
                started = time.perf_counter()
                result = self.worker.run(double, 0.02, value=21)
                self.assertLess(time.perf_counter() - started, 0.15)
                self.assertTrue(result.timed_out)
                self.assertFalse(self.worker.alive)

    def test_deadline_covers_slow_start_and_late_child_is_cleaned_up(self):
        original = self.worker._start

        def slow_start(cancelled):
            time.sleep(0.15)
            original(cancelled)

        with patch.object(self.worker, "_start", side_effect=slow_start):
            result = self.worker.run(double, 0.02, value=21)
            self.assertTrue(result.timed_out)
            time.sleep(0.35)
        self.assertFalse(self.worker.alive)
        self.assertEqual(self.worker.run(double, 10, value=2).value, 4)


def sale(order, day, status, product, quantity, amount):
    return Sale(order, date(2026, 8, day), status, Decimal(str(amount)), product,
                f"name {product}", Decimal(str(quantity)))


class CalculationTests(unittest.TestCase):
    def test_selection_is_inclusive_and_keeps_only_valid_orders(self):
        sales = [sale("A", 1, "valid", "P1", 1, 10), sale("B", 2, "cancelled", "P1", 1, 999),
                 sale("C", 3, "returned", "P1", 1, 999), sale("D", 7, "valid", "P1", 1, 20),
                 sale("E", 8, "valid", "P1", 1, 999)]
        selected = compute.select_valid(sales, date(2026, 8, 1), date(2026, 8, 7))
        self.assertEqual([s.order_id for s in selected], ["A", "D"])
        self.assertEqual(compute.revenue(selected), 30)

    def test_revenue_rounds_half_up_once_after_summing(self):
        sales = [sale("A", 1, "valid", "P1", 1, "0.25"), sale("A", 1, "valid", "P2", 1, "0.25")]
        self.assertEqual(compute.revenue(sales), 1)
        self.assertEqual(compute.revenue([sale("A", 1, "valid", "P1", 1, "2.5")]), 3)

    def test_order_count_counts_distinct_orders(self):
        sales = [sale("A", 1, "valid", "P1", 1, 1), sale("A", 1, "valid", "P2", 1, 1),
                 sale("B", 1, "valid", "P1", 1, 1)]
        self.assertEqual(compute.order_count(sales), 2)

    def test_ranking_sums_quantities_and_breaks_ties_by_code(self):
        sales = [sale("A", 1, "valid", "P9", 2, 1), sale("B", 1, "valid", "P9", 3, 1),
                 sale("C", 1, "valid", "P2", 5, 1), sale("D", 1, "valid", "P1", 1, 1)]
        ranking = compute.product_ranking(sales)
        self.assertEqual([key for key, _, _ in ranking], ["P2", "P9", "P1"])
        self.assertEqual(ranking[1][2], Decimal(5))


class AgentDeadlineTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "input"
        create_demo(root)
        self.split = load_split(root)

    def context(self):
        return BusinessContext(self.split, build_catalog(self.split.root / "data", "demo_csv"))

    def question(self, case):
        return next(q for q in self.split.questions if q.id == f"demo_1_{case}")

    def agent(self, cls=BaselineAgent, **kwargs):
        agent = cls(**kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_answer_evidence_has_code_inputs_result_and_execution(self):
        candidate = self.agent().answer(self.question(1), self.context())
        self.assertEqual((candidate.type, candidate.answer), ("number", 130000))
        evidence = candidate.evidence
        self.assertEqual(evidence["result"], 130000)
        self.assertEqual(evidence["code"]["call"],
                         "revenue(select_valid(sales, date.fromisoformat('2026-08-01'), "
                         "date.fromisoformat('2026-08-27')))")
        self.assertIn("def revenue", evidence["code"]["source"])
        self.assertIn("def select_valid", evidence["code"]["source"])
        self.assertEqual(evidence["inputs"]["tables"], ["sales.csv::sales"])
        self.assertEqual((evidence["inputs"]["selected_rows"],
                          evidence["inputs"]["selected_orders"]), (3, 2))
        self.assertEqual(evidence["execution"]["status"], "ok")

    def test_ranking_evidence_shows_the_row_after_k(self):
        candidate = self.agent().answer(self.question(3), self.context())
        self.assertEqual(candidate.answer, "Cà phê sữa")
        self.assertEqual([row["product_id"] for row in candidate.evidence["ranking"]],
                         ["P1", "P2"])

    def test_calculation_timeout_abstains_then_worker_recovers(self):
        agent = self.agent(type("SlowAgent", (BaselineAgent,),
                                {"compute": staticmethod(slow_revenue)}),
                           question_timeout=1)
        context = self.context()
        started = time.perf_counter()
        candidate = agent.answer(self.question(1), context)
        self.assertLess(time.perf_counter() - started, 10)
        self.assertEqual(candidate.type, "abstain")
        self.assertIn("timed out", candidate.reason)
        self.assertEqual(candidate.evidence["execution"]["status"], "timeout")
        # The replacement worker reloads the business before the next question.
        follow_up = agent.answer(self.question(2), context)
        self.assertEqual((follow_up.type, follow_up.answer), ("number", 2))
        self.assertEqual(agent.worker.generation, 2)

    def test_calculation_error_is_abstain_not_unanswerable(self):
        agent = self.agent(type("FailingAgent", (BaselineAgent,),
                                {"compute": staticmethod(failing_calculation)}))
        # Question 7 is out of the data range; an error must still not claim that.
        candidate = agent.answer(self.question(7), self.context())
        self.assertEqual(candidate.type, "abstain")
        self.assertIn("KeyError", candidate.reason)
        self.assertEqual(candidate.evidence["execution"]["status"], "error")

    def test_stopped_or_dead_worker_reloads_under_ingestion_deadline(self):
        for stop in (lambda w: w.stop(), lambda w: (w._process.kill(), w._process.join())):
            with self.subTest(stop=stop):
                agent, context = self.agent(), self.context()
                self.assertIsNone(agent._prepare(context))
                stop(agent.worker)
                with patch.object(agent.worker, "run", wraps=agent.worker.run) as run:
                    self.assertIsNone(agent._prepare(context))
                    self.assertEqual(run.call_args.args[1], agent.ingestion_timeout)

    def test_ingestion_timeout_is_not_retried_for_each_question(self):
        agent = self.agent(type("SlowLoadAgent", (BaselineAgent,),
                                {"prepare": staticmethod(slow_prepare)}),
                           ingestion_timeout=0.5)
        context = self.context()
        first = agent.answer(self.question(1), context)
        started = time.perf_counter()
        second = agent.answer(self.question(2), context)
        self.assertLess(time.perf_counter() - started, 0.4)
        for candidate in (first, second):
            self.assertEqual(candidate.type, "abstain")
            self.assertIn("Sales ingestion timed out", candidate.reason)
            self.assertEqual(candidate.evidence["ingestion_execution"]["status"], "timeout")

    def test_out_of_range_period_is_unanswerable_only_with_readme_coverage(self):
        candidate = self.agent().answer(self.question(7), self.context())
        self.assertEqual(candidate.type, "unanswerable")
        self.assertIn("Khoảng dữ liệu", candidate.evidence["coverage_source"])

    def test_timeouts_must_be_positive(self):
        with self.assertRaises(ValueError):
            BaselineAgent(question_timeout=0)
        with self.assertRaises(ValueError):
            BaselineAgent(ingestion_timeout=float("nan"))


if __name__ == "__main__":
    unittest.main()
