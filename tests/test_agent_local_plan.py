"""Synthetic AGENT-02 probes. Fake planners test plumbing, never model quality."""

import csv
import json
import os
import socket
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from datamate.agent.local_plan import (
    MAX_PROMPT_CHARACTERS, LocalPlanAgent, build_messages, parse_model_plan,
)
from datamate.agent.model_runtime import (
    MODEL_ID, MODEL_PARAMETERS, MODEL_REVISION, prepare_model, sha256, verify_snapshot,
)
from datamate.agent.validation import check_executed
from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Candidate, Filter, Plan, Question
from datamate.demo import COLUMNS, ROWS, RULEBOOK, create_demo
from datamate.evaluation.scorer import matches
from datamate.evaluation.validation import finalize, validate_record
from datamate.ingestion import build_catalog
from datamate.io import load_split, read_jsonl
from datamate.pipeline import Config, predict
from datamate.semantics.rules import read_rules


def fake_load(cache, model_path):
    return {"model_id": "synthetic-test-only", "revision": "none"}


def fake_generate(cache, messages, max_input_tokens, max_new_tokens):
    payload = json.loads(messages[1]["content"])
    start, end = payload["resolved_period"]
    return {"text": json.dumps({"intent": "revenue", "start": start, "end": end,
                               "top_k": 1, "unsupported": [],
                               "rules": payload["required_rules"]["revenue"]})}


def slow_generate(cache, **kwargs):
    time.sleep(60)


def invalid_generate(cache, **kwargs):
    return {"text": '{"type":"unanswerable"}'}


def failed_generate(cache, **kwargs):
    raise RuntimeError("synthetic model failure")


def invalid_calculation(cache, **kwargs):
    return Candidate("number", float("inf"), 1.0)


def fake_plan(intent="revenue", **updates):
    result = {"intent": intent, "start": "2026-08-01", "end": "2026-08-27",
              "top_k": 1, "unsupported": [], "rules": list(read_rules(RULEBOOK).source_rows)}
    result.update(updates)
    return result


class ModelPlanTests(unittest.TestCase):
    question = Question("routing-only", "routing-only", "Cộng tiền đơn hợp lệ tháng này",
                        date(2026, 8, 27))

    def parse(self, payload, question=None, rulebook=RULEBOOK):
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return parse_model_plan(text, question or self.question, rulebook)

    def test_plan_has_current_rule_evidence_and_dates(self):
        plan = self.parse(fake_plan())
        self.assertEqual((plan.intent, plan.start, plan.end),
                         ("revenue", date(2026, 8, 1), date(2026, 8, 27)))
        self.assertEqual(plan.evidence, read_rules(RULEBOOK).source_rows)

    def test_rejects_model_answers_code_extra_filters_and_bad_shapes(self):
        for payload in ([], {"answer": 130000}, fake_plan(code="open('answers.jsonl')"),
                        fake_plan(filters={"product": "P1"}), fake_plan(intent="boolean"),
                        fake_plan(top_k=True), fake_plan(top_k=101), fake_plan(top_k=2),
                        fake_plan(unsupported=["payment filter"]), fake_plan(rules=[]),
                        fake_plan(rules=["fabricated rule"]), fake_plan(rules=[{}]),
                        fake_plan(start="2026-02-30"), fake_plan(end="2026-08-28"),
                        fake_plan(start="20260801"), fake_plan(intent=[]),
                        fake_plan(unsupported=False), fake_plan(end="2026-07-31")):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                self.parse(payload)
        for text in ('{"intent":"revenue","intent":"order_count"}',
                     '{"intent":NaN}', '```json\n{}\n```', '{"abstain":1}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parse(text)
        self.assertIsNone(self.parse({"abstain": "unsupported condition"}))

    def test_current_split_rule_changes_disable_intents_and_months(self):
        changed = RULEBOOK.replace("không huỷ", "bao gồm huỷ")
        self.assertNotEqual(changed, RULEBOOK)
        with self.assertRaises(ValueError):
            self.parse(fake_plan(), rulebook=changed)
        changed = RULEBOOK.replace("đến hết hôm nay", "đến cuối tháng")
        with self.assertRaises(ValueError):
            self.parse(fake_plan(), rulebook=changed)
        with self.assertRaises(ValueError):
            self.parse(fake_plan(intent="average_order"))

    def test_rejects_known_filters_comparisons_and_multiple_periods(self):
        for text in ("Doanh thu tháng này so với tháng trước", "Doanh thu tháng này thanh toán tiền mặt",
                     "Doanh thu tháng này của từng ngày", "Doanh thu tháng 8/2026 tháng 7/2026",
                     "Doanh thu Tết", "Doanh thu ngày 30/02/2026"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parse(fake_plan(), replace(self.question, question=text))

    def test_routing_ids_do_not_change_model_plan(self):
        a = self.parse(fake_plan())
        b = self.parse(fake_plan(), replace(self.question, id="other", business_id="other"))
        self.assertEqual(a, b)


class LocalAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        create_demo(self.root / "input")
        self.split = load_split(self.root / "input")
        self.context = BusinessContext(self.split, build_catalog(self.split.root / "data", "demo_csv"))
        self.question = Question("novel-question", "demo_csv", "Cộng tiền đơn hợp lệ tháng này",
                                 date(2026, 8, 27))

    def agent(self, generate=fake_generate, **kwargs):
        class FakeLocalAgent(LocalPlanAgent):
            load_model = staticmethod(fake_load)
        FakeLocalAgent.generate = staticmethod(generate)
        agent = FakeLocalAgent("unused-test-snapshot", **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_prompt_uses_split_metadata_and_omits_routing_ids(self):
        messages = build_messages(self.question, self.context)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["rulebook"], self.split.rulebook)
        self.assertEqual(payload["business_readme"], self.context.catalog.readme)
        self.assertEqual(payload["calendar"], self.split.calendar_path.read_text(encoding="utf-8-sig"))
        self.assertNotIn("id", payload)
        self.assertNotIn("business_id", payload)
        self.assertNotIn(str(self.root), messages[1]["content"])
        self.assertIn("observed_types", payload["schema"][0]["columns"][0])
        other = replace(self.context, split=replace(self.split, rulebook="current split only"))
        self.assertEqual(json.loads(build_messages(self.question, other)[1]["content"])["rulebook"],
                         "current split only")

    def test_context_budget_never_silently_truncates_rules(self):
        context = replace(self.context, catalog=replace(
            self.context.catalog, readme="x" * MAX_PROMPT_CHARACTERS))
        with self.assertRaisesRegex(ValueError, "budget"):
            build_messages(self.question, context)

    def test_model_plan_executes_in_worker_and_has_code_and_output(self):
        candidate = self.agent().answer(self.question, self.context)
        self.assertEqual((candidate.type, candidate.answer, candidate.confidence),
                         ("number", 130000, 0.5))
        evidence = candidate.evidence
        self.assertEqual(evidence["execution"]["status"], "ok")
        self.assertEqual(evidence["planning_execution"]["status"], "ok")
        self.assertEqual(evidence["result"], candidate.answer)
        self.assertIn("def revenue", evidence["code"]["source"])
        self.assertIn("model_output", evidence)
        self.assertEqual(finalize("q", candidate, 1)["type"], "abstain")
        self.assertEqual(finalize("q", candidate, 0.5)["answer"], 130000)

    def test_product_filter_uses_baseline_before_model(self):
        names_rule = "| Tên gọi, nhãn | Các tên cùng chỉ một thứ là cùng một thứ. File có mã thì theo mã |"
        context = replace(self.context, cache={}, split=replace(
            self.split, rulebook=RULEBOOK + names_rule + "\n"))
        agent = self.agent()
        with patch.object(agent.planner_worker, "run", side_effect=AssertionError("Unexpected model call")):
            for text, expected in (("Doanh thu của Cà phê sữa tháng này", 100000),
                                   ("Số đơn của Cà phê sữa tháng này", 2)):
                candidate = agent.answer(replace(self.question, question=text), context)
                self.assertEqual((candidate.type, candidate.answer, candidate.confidence),
                                 ("number", expected, 1.0))
                self.assertEqual(candidate.evidence["filters"][0]["field"], "product")
                self.assertNotIn("model", candidate.evidence)

    def test_comparisons_preserve_baseline_output_without_model(self):
        book = RULEBOOK.replace(
            "| Tháng này | Từ ngày 1 đến hết hôm nay |",
            "| Tháng này | Từ ngày 1 đến hết hôm nay. So với tháng trước thì lấy cùng số ngày của tháng trước |")
        book += "| Phần trăm thay đổi, tăng trưởng | So với kỳ trước; giảm thì ra số âm |\n"
        context = replace(self.context, split=replace(self.split, rulebook=book),
                          catalog=replace(self.context.catalog, readme=
                                          "Khoảng dữ liệu: 2026-07-01 đến 2026-08-28"))
        question = replace(self.question,
                           question="Doanh thu tháng này thay đổi bao nhiêu % so với tháng trước")
        path = self.split.root / "data/demo_csv/sales.csv"
        for previous, expected in ((260000, -50.0), (52000, 150.0)):
            with self.subTest(previous=previous):
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(COLUMNS)
                    writer.writerows(ROWS)
                    writer.writerow(("PREV", "2026-07-01", "completed", "P1", "Cà phê sữa", 1, previous))
                local = self.agent()
                baseline = BaselineAgent()
                self.addCleanup(baseline.close)
                with patch.object(local.planner_worker, "run", side_effect=AssertionError("Unexpected model call")):
                    for agent in (baseline, local):
                        candidate = agent.answer(question, replace(context, cache={}))
                        self.assertEqual((candidate.type, candidate.answer, candidate.confidence),
                                         ("percent", expected, 1.0))
                        self.assertEqual(candidate.evidence["execution"]["status"], "ok")

    def test_no_reference_reads_or_network_during_parent_inference(self):
        # Child tasks are fixed synthetic Python; they perform no model/library I/O.
        original = Path.open

        def guarded(path, *args, **kwargs):
            if path.name == "answers.jsonl":
                raise AssertionError("Reference read during inference")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded), patch.object(
                socket, "create_connection", side_effect=AssertionError("Network during inference")):
            candidate = self.agent().answer(self.question, self.context)
            self.assertEqual(candidate.answer, 130000)

    def test_planning_errors_timeouts_and_invalid_output_abstain(self):
        for task in (slow_generate, failed_generate, invalid_generate):
            with self.subTest(task=task):
                agent = self.agent(task, model_timeout=0.5)
                candidate = agent.answer(self.question, self.context)
                self.assertEqual(candidate.type, "abstain")
                self.assertIsNone(candidate.answer)
                if task == slow_generate:
                    self.assertEqual(candidate.evidence["planning_execution"]["status"], "timeout")
                    self.assertEqual(agent.answer(self.question, self.context).type, "abstain")

    def test_invalid_execution_is_abstain(self):
        agent = self.agent()
        agent.compute = invalid_calculation
        self.assertEqual(agent.answer(self.question, self.context).type, "abstain")

    def test_missing_weights_is_cached_and_deterministic_answers_still_work(self):
        agent = LocalPlanAgent(str(self.root / "missing"))
        self.addCleanup(agent.close)
        first = agent.answer(self.question, self.context)
        self.assertEqual(first.type, "abstain")
        with patch.object(agent.planner_worker, "run", side_effect=AssertionError("Repeated load")):
            self.assertEqual(agent.answer(self.question, self.context).type, "abstain")
            original = next(q for q in self.split.questions if q.id == "demo_1_1")
            self.assertEqual(agent.answer(original, self.context).answer, 130000)

    def test_pipeline_records_model_metadata_and_preserves_baseline(self):
        from datamate.io import write_jsonl

        write_jsonl(self.split.root / "questions.jsonl", [
            {"id": self.question.id, "business_id": self.question.business_id,
             "question": self.question.question, "as_of_date": self.question.as_of_date.isoformat()}])
        report = predict(self.split.root, self.root / "run", Config(backend="local_plan", min_confidence=0.5),
                         agent=self.agent())
        self.assertEqual(read_jsonl(self.root / "run/submission.jsonl")[0]["answer"], 130000)
        self.assertEqual(report["models"][0]["model_id"], "synthetic-test-only")

    def test_new_config_loads_and_invalid_deadlines_are_rejected(self):
        config = Config.load(Path(__file__).resolve().parents[1] / "configs/local_plan.toml")
        self.assertEqual(config.backend, "local_plan")
        for kwargs in ({"model_timeout": 0}, {"ingestion_timeout": float("nan")},
                       {"max_input_tokens": True}, {"max_new_tokens": 100_000}):
            with self.assertRaises(ValueError):
                Config(**kwargs)


class ExecutedOutputTests(unittest.TestCase):
    def candidate(self, kind, value, **kwargs):
        evidence = {"code": {"call": "synthetic()", "source": "synthetic test"}, "result": value}
        evidence.update(kwargs)
        return Candidate(kind, value, 1.0, evidence=evidence)

    def test_boolean_and_table_follow_existing_scorer_format(self):
        for kind, value in (("boolean", True), ("boolean", False),
                            ("table", {"columns": ["Sản phẩm", "Doanh thu"],
                                       "rows": [["Cà phê", 130000], ["Trà", 0]]})):
            with self.subTest(kind=kind, value=value):
                candidate = check_executed(self.candidate(kind, value))
                record = finalize("q", candidate, 1)
                validate_record(record)
                self.assertEqual(record["type"], kind)
                self.assertTrue(matches(record, {"id": "q", "type": kind, "answer": value}))
        reordered = {"columns": ["Doanh thu", "Sản phẩm"], "rows": [[0, "Trà"], [130000, "Cà phê"]]}
        record = finalize("q", self.candidate("table", reordered), 1)
        self.assertTrue(matches(record, {"id": "q", "type": "table", "answer": value}))

    def test_rejects_nonfinite_bad_types_malformed_tables_and_unproven_output(self):
        cases = [("boolean", "true"), ("boolean", 1), ("number", True),
                 ("number", float("inf")),
                 ("table", {"columns": ["a", "a"], "rows": []}),
                 ("table", {"columns": ["a"], "rows": [[1, 2]]}),
                 ("table", {"columns": ["a"], "rows": [[float("nan")]]}),
                 ("date", "2026-02-30")]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                self.assertEqual(check_executed(self.candidate(kind, value)).type, "abstain")
        self.assertEqual(check_executed(Candidate("number", 10, 1)).type, "abstain")
        self.assertEqual(check_executed(Candidate("unanswerable", confidence=1)).type, "abstain")
        bad = self.candidate("number", 10, result=11)
        self.assertEqual(check_executed(bad).type, "abstain")

    def test_type_date_and_count_bounds_come_from_plan_and_execution(self):
        plan = Plan("order_count", date(2026, 8, 1), date(2026, 8, 27))
        for value in (-1, 3, 1.5):
            candidate = self.candidate("number", value, inputs={"selected_orders": 2})
            self.assertEqual(check_executed(candidate, plan).type, "abstain")
        candidate = self.candidate("number", 2, inputs={"selected_orders": 2})
        self.assertEqual(check_executed(candidate, plan).answer, 2)
        self.assertEqual(check_executed(self.candidate("boolean", True), plan).type, "abstain")
        plan = replace(plan, intent="best_day")
        self.assertEqual(check_executed(self.candidate("date", "2026-08-28"), plan).type, "abstain")

    def test_revenue_change_accepts_negative_and_above_100_percent(self):
        plan = Plan("revenue", date(2026, 8, 1), date(2026, 8, 27),
                    compare_start=date(2026, 7, 1), compare_end=date(2026, 7, 27),
                    comparison="change_percent")
        for value in (-100.0, -50.0, 0, 25.0, 150.0):
            with self.subTest(value=value):
                candidate = check_executed(self.candidate("percent", value), plan)
                self.assertEqual((candidate.type, candidate.answer), ("percent", value))
        self.assertEqual(check_executed(self.candidate("number", 25), plan).type, "abstain")

    def test_cancellation_percentage_still_requires_0_to_100(self):
        plan = Plan("cancel_rate", date(2026, 8, 1), date(2026, 8, 27))
        for value in (-0.1, 100.01):
            self.assertEqual(check_executed(self.candidate("percent", value), plan).type, "abstain")
        for value in (0, 100):
            self.assertEqual(check_executed(self.candidate("percent", value), plan).answer, value)

    def test_result_validator_rejects_unsupported_plan_fields(self):
        plan = Plan("revenue", date(2026, 8, 1), date(2026, 8, 27))
        for invalid in (replace(plan, filters=(Filter("payment_method", "tiền mặt"),)),
                        replace(plan, comparison="difference", compare_start=date(2026, 7, 1),
                                compare_end=date(2026, 7, 27))):
            self.assertEqual(check_executed(self.candidate("number", 10), invalid).type, "abstain")


class SnapshotTests(unittest.TestCase):
    def test_optional_loader_uses_only_local_files_and_no_remote_code(self):
        model = MagicMock()
        model.parameters.return_value = [SimpleNamespace(numel=lambda: 7_615_616_512)]
        model.to.return_value = model
        model.eval.return_value = model
        auto_model, auto_tokenizer = MagicMock(), MagicMock()
        auto_model.from_pretrained.return_value = model
        torch = SimpleNamespace(manual_seed=MagicMock(), use_deterministic_algorithms=MagicMock(),
                                bfloat16="bf16", __version__="test-only", version=SimpleNamespace(cuda="test"),
                                cuda=SimpleNamespace(is_available=lambda: True, get_device_name=lambda i: "fake"))
        transformers = SimpleNamespace(AutoModelForCausalLM=auto_model, AutoTokenizer=auto_tokenizer,
                                       __version__="test-only")
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
                "sys.modules", {"torch": torch, "transformers": transformers}), patch.dict(os.environ, {}), \
                patch("datamate.agent.model_runtime.verify_snapshot", return_value={"total_parameters": MODEL_PARAMETERS}):
            path = Path(temporary)
            (path / "model_manifest.json").write_text("{}", encoding="utf-8")
            info = prepare_model({}, str(path))
            for call in (auto_model.from_pretrained.call_args, auto_tokenizer.from_pretrained.call_args):
                self.assertEqual(call.args[0], str(path.resolve()))
                self.assertTrue(call.kwargs["local_files_only"])
                self.assertFalse(call.kwargs["trust_remote_code"])
            self.assertTrue(auto_model.from_pretrained.call_args.kwargs["use_safetensors"])
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
            self.assertEqual(info["actual_parameters"], 7_615_616_512)

    def test_checksums_inventory_revision_and_missing_weights(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {"config.json": "{}", "tokenizer_config.json": "{}", "tokenizer.json": "{}",
                     "LICENSE": "fixture", "model-1.safetensors": "synthetic-not-a-model",
                     "model.safetensors.index.json": json.dumps({"weight_map": {"w": "model-1.safetensors"}})}
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            manifest = {"model_id": MODEL_ID, "revision": MODEL_REVISION,
                        "license": "Apache-2.0", "total_parameters": MODEL_PARAMETERS,
                        "files": {name: sha256(root / name) for name in files}}
            path = root / "model_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(verify_snapshot(root)["revision"], MODEL_REVISION)
            (root / "unlisted.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory"):
                verify_snapshot(root)
            (root / "unlisted.txt").unlink()
            (root / "model-1.safetensors").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_snapshot(root)
            manifest["revision"] = "main"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "declaration"):
                verify_snapshot(root)


if __name__ == "__main__":
    unittest.main()
