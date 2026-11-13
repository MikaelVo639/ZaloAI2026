"""Model calculation path. Fake models test plumbing and safety, never model quality."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from datamate.agent.baseline import BaselineAgent
from datamate.agent.code_answer import (
    CODE_EXAMPLES,
    build_code_messages,
    check_code,
    extract_code,
    normalize_result,
    rule_text,
)
from datamate.agent.rewrite import UNSURE, RewriteAgent
from datamate.contracts import BusinessContext, Question
from datamate.demo import create_demo
from datamate.ingestion import build_catalog
from datamate.io import load_split
from datamate.pipeline import Config, predict

MONTH_REVENUE = '''```python
def answer(sales, today):
    total = sum(s["amount"] for s in sales if s["status"] == "valid"
                and (s["day"].year, s["day"].month) == (today.year, today.month))
    return {"type": "number", "answer": round(total)}
```'''
# Asked in a way the grammar cannot plan; the rewrite model is unsure.
ODD = "quán mình tính từ mùng một tới bữa nay gom về bao nhiêu"


def fake_load(cache, model_path):
    return {"model_id": "synthetic-test-only", "revision": "none"}


def _is_code_prompt(messages):
    return "def answer(sales, today)" in messages[0]["content"]


def code_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": MONTH_REVENUE if _is_code_prompt(messages) else UNSURE}


def disagreeing_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    if not _is_code_prompt(messages):
        return {"text": UNSURE}
    forward = messages[1]["content"].startswith(f"Câu hỏi: {CODE_EXAMPLES[0][0]}")
    return {"text": MONTH_REVENUE if forward else MONTH_REVENUE.replace("round(total)", "1")}


def none_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    if not _is_code_prompt(messages):
        return {"text": UNSURE}
    return {"text": "```python\ndef answer(sales, today):\n    return None\n```"}


def unsafe_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    if not _is_code_prompt(messages):
        return {"text": UNSURE}
    return {"text": "```python\nimport os\ndef answer(sales, today):\n    return 1\n```"}


def looping_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    if not _is_code_prompt(messages):
        return {"text": UNSURE}
    return {"text": "```python\ndef answer(sales, today):\n    while True:\n        pass\n```"}


BUGGY = MONTH_REVENUE.replace('s["amount"]', 's["revenue"]')  # KeyError: 'revenue'


def repaired_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    if not _is_code_prompt(messages):
        return {"text": UNSURE}
    if "Chạy code bị lỗi" in messages[-1]["content"]:
        assert "revenue" in messages[-1]["content"], messages[-1]
        return {"text": MONTH_REVENUE}
    return {"text": BUGGY}


def calendar_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    if not _is_code_prompt(messages):
        return {"text": UNSURE}
    return {"text": "```python\ndef answer(sales, today):\n"
                    "    day = calendar[today]\n"
                    "    return {\"type\": \"boolean\", \"answer\": day[\"lunar_month\"] >= 1}\n```"}


def always_buggy_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": BUGGY if _is_code_prompt(messages) else UNSURE}


class CodeCheckTests(unittest.TestCase):
    def test_only_definitions_without_imports_or_private_access(self):
        self.assertIsNone(check_code(extract_code(MONTH_REVENUE)))
        for code, reason in (
                ("import os\ndef answer(s, t):\n    return 1", "Import"),
                ("def answer(s, t):\n    return open('x')", "open"),
                ("def answer(s, t):\n    return ().__class__", "private"),
                ("def answer(s, t):\n    return __builtins__", "__builtins__"),
                ("x = 1\ndef answer(s, t):\n    return x", "outside functions"),
                ("def other(s, t):\n    return 1", "no answer function"),
                ("def answer(s, t) return", "parse")):
            with self.subTest(code=code):
                self.assertIn(reason, check_code(code))

    def test_extract_code_needs_the_answer_function(self):
        self.assertIn("def answer(", extract_code(MONTH_REVENUE))
        self.assertIsNone(extract_code("Tôi không chắc."))

    def test_results_are_normalized_to_submission_form(self):
        self.assertEqual(normalize_result({"type": "number", "answer": 1234.0}), ("number", 1234))
        self.assertEqual(normalize_result({"type": "number", "answer": 2.345}), ("number", 2.35))
        self.assertEqual(normalize_result({"type": "percent", "answer": 12.5}), ("percent", 12.5))
        self.assertEqual(normalize_result({"type": "date", "answer": "2026-03-07"}),
                         ("date", "2026-03-07"))
        self.assertEqual(normalize_result({"type": "list", "answer": ["A", 2.0]}),
                         ("list", ["A", 2]))
        self.assertEqual(normalize_result({"type": "boolean", "answer": False}), ("boolean", False))
        for bad in (None, 5, {"type": "table", "answer": {}}, {"type": "number", "answer": True},
                    {"type": "number", "answer": float("nan")}, {"type": "date", "answer": "7/3"},
                    {"type": "list", "answer": []}, {"type": "unanswerable", "answer": None},
                    {"type": "string", "answer": " "}, {"type": "number", "answer": 1, "x": 2}):
            with self.subTest(bad=bad):
                self.assertIsInstance(normalize_result(bad), str)

    def test_rules_keep_general_rows_and_rows_sharing_words(self):
        rulebook = ("## 1. Đơn\n| Luật | Mặc định |\n|---|---|\n| Đơn tính doanh thu | không huỷ |\n"
                    "## 3. Xếp hạng\n| Khách thân thiết | mua từ 5 đơn |\n| Hàng chậm | bán dưới 3 |\n")
        text = rule_text(rulebook, "khách thân thiết tháng này mua bao nhiêu")
        self.assertIn("Đơn tính doanh thu", text)
        self.assertIn("Khách thân thiết", text)
        self.assertNotIn("Hàng chậm", text)

    def test_prompt_has_question_and_today_but_no_routing_ids(self):
        profile = {"fields": ["order_id", "day", "status", "amount"], "values": {},
                   "first_day": "2026-01-01"}
        messages = build_code_messages("dthu thang nay", date(2026, 8, 27), profile, "", [
            CODE_EXAMPLES[0]])
        text = json.dumps(messages, ensure_ascii=False)
        self.assertIn("2026-08-27", text)
        self.assertIn("Câu hỏi: dthu thang nay", messages[1]["content"])
        self.assertNotIn("customer_name", messages[0]["content"])
        self.assertIn("only `sales`", messages[0]["content"])
        with_tables = build_code_messages("tiền nhập hàng tháng này", date(2026, 8, 27),
                                          {**profile, "tables": ["purchases", "stock"]}, "", [])
        self.assertIn("Global list `purchases`", with_tables[0]["content"])
        self.assertIn("Global list `stock`", with_tables[0]["content"])
        self.assertNotIn("purchase_lines", with_tables[0]["content"])
        dated = build_code_messages("tết năm nay bán bao nhiêu", date(2026, 8, 27),
                                    {**profile, "calendar": ["2024-01-01", "2027-12-31"]}, "", [])
        self.assertIn("Global dict `calendar`", dated[0]["content"])


class CodeAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        create_demo(self.root / "input")
        self.split = load_split(self.root / "input")
        self.context = BusinessContext(self.split, build_catalog(self.split.root / "data", "demo_csv"))
        self.question = Question("q", "demo_csv", ODD, date(2026, 8, 27))

    def agent(self, generate, **kwargs):
        class FakeRewriteAgent(RewriteAgent):
            load_model = staticmethod(fake_load)
        FakeRewriteAgent.generate = staticmethod(generate)
        agent = FakeRewriteAgent("unused-test-snapshot", question_timeout=5, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def expected_month_revenue(self):
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        return agent.answer(Question("q", "demo_csv", "Doanh thu tháng này là bao nhiêu?",
                                     date(2026, 8, 27)), self.context)

    def test_agreeing_calculations_answer_after_an_unsure_rewrite(self):
        candidate = self.agent(code_generate, code_fallback=True).answer(self.question, self.context)
        expected = self.expected_month_revenue()
        self.assertEqual((candidate.type, candidate.answer), (expected.type, expected.answer))
        self.assertEqual(candidate.confidence, 0.5)
        self.assertEqual(candidate.evidence["path"], "code")
        self.assertIn("def answer(", candidate.evidence["code"])
        self.assertEqual(candidate.evidence["result"], expected.answer)
        self.assertEqual(len(candidate.evidence["code_votes"]), 2)
        self.assertTrue(candidate.evidence["rewrites"])

    def test_off_by_default(self):
        candidate = self.agent(code_generate).answer(self.question, self.context)
        self.assertEqual((candidate.type, candidate.reason), ("abstain", "Rewrite model was unsure"))
        self.assertNotIn("code_attempt", candidate.evidence)

    def test_disagreeing_empty_unsafe_or_looping_code_abstains(self):
        for generate, reason in ((disagreeing_code_generate, "disagree"),
                                 (none_code_generate, "no answer"),
                                 (unsafe_code_generate, "Import"),
                                 (looping_code_generate, "Timed out")):
            with self.subTest(generate=generate.__name__):
                candidate = self.agent(generate, code_fallback=True).answer(
                    self.question, self.context)
                self.assertEqual(candidate.type, "abstain")
                self.assertIn("Rewrite model was unsure; code:", candidate.reason)
                self.assertIn(reason, candidate.reason)
        # The data worker comes back after a timeout: the next question still computes.
        agent = self.agent(looping_code_generate, code_fallback=True)
        agent.answer(self.question, self.context)
        type(agent).generate = staticmethod(code_generate)
        self.assertEqual(agent.answer(self.question, self.context).type, "number")

    def test_failing_code_is_repaired_once(self):
        candidate = self.agent(repaired_code_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertEqual(candidate.type, "number", candidate.reason)
        self.assertEqual(candidate.answer, self.expected_month_revenue().answer)
        self.assertEqual([(a["vote"], a["repair"]) for a in candidate.evidence["code_votes"]],
                         [(1, 0), (1, 1), (2, 0), (2, 1)])
        self.assertNotIn("revenue\"]", candidate.evidence["code"])
        failed = self.agent(always_buggy_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertIn("KeyError", failed.reason)
        self.assertEqual(len(failed.evidence["code_attempt"]["code_votes"]), 2)

    def test_code_reads_the_split_calendar(self):
        candidate = self.agent(calendar_code_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertEqual(candidate.type, "boolean", candidate.reason)

    def test_code_only_skips_the_grammar(self):
        planned = Question("q", "demo_csv", "Doanh thu tháng này là bao nhiêu?", date(2026, 8, 27))
        candidate = self.agent(code_generate, code_only=True).answer(planned, self.context)
        self.assertEqual(candidate.evidence.get("path"), "code")

    def test_pipeline_logs_code_and_output(self):
        (self.root / "input" / "questions.jsonl").write_text(json.dumps(
            {"id": "q1", "business_id": "demo_csv", "question": ODD, "as_of_date": "2026-08-27"},
            ensure_ascii=False) + "\n", encoding="utf-8")
        config = Config(backend="rewrite", min_confidence=0.5, rewrite_code_fallback=True)
        agent = self.agent(code_generate, code_fallback=True)
        predict(self.root / "input", self.root / "out", config, agent=agent)
        log = json.loads(next((self.root / "out" / "logs").glob("*.json")).read_text())
        self.assertEqual(log["answer"]["type"], "number")
        self.assertIn("def answer(", log["code"])
        self.assertEqual(log["output"], log["answer"]["answer"])

    def test_config_options_are_validated(self):
        for name in ("rewrite_code_fallback", "rewrite_code_only"):
            with self.assertRaisesRegex(ValueError, name):
                Config(**{name: "yes"})


if __name__ == "__main__":
    unittest.main()
