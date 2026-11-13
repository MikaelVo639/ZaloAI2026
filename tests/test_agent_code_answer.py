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
    code_doubt,
    extract_code,
    normalize_result,
    out_of_reach,
    round_money,
    rule_text,
    unknown_filter,
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


NAMES = ("```python\ndef answer(sales, today):\n"
         "    names = sorted({s['product_name'] for s in sales if s['product_name']})\n"
         "    return {'type': 'string', 'answer': names[0]%s}\n```")


def named_code_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": NAMES % "" if _is_code_prompt(messages) else UNSURE}


def decorated_name_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": NAMES % " + ' (SP01)'" if _is_code_prompt(messages) else UNSURE}


def invented_name_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": NAMES % " + ' Plus'" if _is_code_prompt(messages) else UNSURE}


def always_buggy_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": BUGGY if _is_code_prompt(messages) else UNSURE}


class CodeCheckTests(unittest.TestCase):
    def test_only_definitions_without_imports_or_private_access(self):
        self.assertIsNone(check_code(extract_code(MONTH_REVENUE)))
        for code, reason in (
                ("import os\ndef answer(s, t):\n    return 1", "not allowed: os"),
                ("from os import path\ndef answer(s, t):\n    return 1", "not allowed: os"),
                ("from collections import _x\ndef answer(s, t):\n    return 1", "not allowed"),
                ("def answer(s, t):\n    return open('x')", "open"),
                ("def answer(s, t):\n    return ().__class__", "private"),
                ("def answer(s, t):\n    return __builtins__", "__builtins__"),
                ("x = f()\ndef answer(s, t):\n    return x", "outside functions"),
                ("print(1)\ndef answer(s, t):\n    return 1", "outside functions"),
                ("def other(s, t):\n    return 1", "no answer function"),
                ("def answer(s, t) return", "parse")):
            with self.subTest(code=code):
                self.assertIn(reason, check_code(code))

    def test_standard_imports_constants_and_print_are_accepted(self):
        from datamate.agent.code_answer import _call, _globals
        code = ("import datetime\nfrom collections import OrderedDict\nimport calendar as cal\n"
                "LIMIT = 2\n"
                "def answer(s, t):\n    print('debug')\n"
                "    days = cal.monthrange(t.year, t.month)[1]\n"
                "    first = datetime.date(t.year, t.month, 1)\n"
                "    return {'type': 'number', 'answer': days + first.day + LIMIT}")
        self.assertIsNone(check_code(code))
        self.assertEqual(_call(_globals(), code, [], date(2026, 2, 10)),
                         {"value": {"type": "number", "answer": 31}})

    def test_module_views_hide_modules_and_missing_tables_give_no_answer(self):
        from datamate.agent.code_answer import _call, _globals
        namespace = _globals()
        self.assertFalse(hasattr(namespace["statistics"], "sys"))
        code = "def answer(s, t):\n    return {'type': 'number', 'answer': len(purchases)}"
        self.assertEqual(_call(namespace, code, [], date(2026, 2, 10))["value"], None)
        with self.assertRaises(NameError):
            _call(_globals(), "def answer(s, t):\n    return undefined_name", [], None)
        with self.assertRaises(ImportError):
            _globals()["__builtins__"]["__import__"]("os")

    def test_code_must_read_what_the_question_is_about(self):
        # Shapes of the wrong model calculations on the TRAIN model benchmark (LOCAL PROXY).
        lines_only = 'def answer(sales, today):\n    return {"type": "number", "answer": len(sales)}'
        for question, reason in (
                ("tháng 3/2026 có bao nhiêu bill bị huỷ", "orders"),
                ("doanh thu bình quân mỗi đơn tháng 1/2026", "orders"),
                ("tháng này cửa hàng bỏ ra bao nhiêu tiền nhập hàng", "purchases"),
                ("Hết hôm nay, Gas 12kg còn lại mấy bình trong kho?", "stock"),
                ("cuoi ngay hom nay con bao nhieu tuyp", "stock"),
                ("tổng dthu 30 ngày gần nhất", None)):
            with self.subTest(question=question):
                doubt = code_doubt(question, lines_only)
                self.assertIn(reason, doubt or "") if reason else self.assertIsNone(doubt)
        self.assertIsNone(code_doubt("doanh thu của Tôn lạnh 0.4mm tháng 7/2025", lines_only))
        self.assertIsNone(code_doubt("doanh thu hàng không kê đơn tháng 2/2025", lines_only))
        self.assertIn("days ending today", code_doubt(
            "tổng dthu 30 ngày gần nhất",
            "def answer(sales, today):\n    start = today - timedelta(days=30)\n    return None"))
        self.assertIsNone(code_doubt(
            "tổng dthu 30 ngày gần nhất",
            "def answer(sales, today):\n    start = today - timedelta(days=29)\n    return None"))
        self.assertIn("one line's amount", code_doubt("số ngày doanh thu từ 7.900.000", (
            "def answer(sales, today):\n"
            "    return len({s['day'] for s in sales if s['amount'] >= 7900000})")))
        self.assertIn("ranks single lines", code_doubt("ai mua nhiều tiền nhất", (
            "def answer(sales, today):\n"
            "    return max(sales, key=lambda s: s['amount'])['customer_name']")))
        self.assertIsNotNone(out_of_reach("tiền khách trả bớt nợ các đơn mua chịu được bao nhiêu"))
        self.assertIsNone(out_of_reach("doanh thu tháng này bao nhiêu"))

    def test_filters_must_name_values_in_the_data(self):
        lines = [{"product_group": "Giảm đau", "product_name": "Gạo ST25", "channel": None}]
        refused = unknown_filter(
            'def answer(sales, today):\n'
            '    return [s for s in sales if s["product_group"] == "Thuốc kê đơn"]', lines)
        self.assertIn("Thuốc kê đơn", refused or "")
        self.assertIsNotNone(unknown_filter(
            'def answer(sales, today):\n'
            '    return [s for s in sales if s.get("product_group") in ["giảm đau", "kháng sinh"]]',
            lines))
        for code in ('def answer(sales, today):\n'
                     '    return [s for s in sales if fold(s["product_name"]) == fold("gao st25")]',
                     'def answer(sales, today):\n'
                     '    return [s for s in sales if "gao" in fold(s["product_name"])]',
                     'def answer(sales, today):\n'
                     '    return [s for s in sales if s["status"] == "valid"]'):
            with self.subTest(code=code):
                self.assertIsNone(unknown_filter(code, lines))

    def test_money_is_rounded_to_whole_vnd(self):
        self.assertEqual(round_money("doanh thu bình quân mỗi đơn tháng 3/2026", "number",
                                     532061.5), 532062)
        self.assertEqual(round_money("giá nhập bình quân một hộp", "number", 92896.49), 92896)
        self.assertEqual(round_money("bán được bao nhiêu tiền", "number", 1500.5), 1501)
        for question, answer in (("trung bình mỗi đơn có mấy dòng hàng", 2.35),
                                 ("bao nhiêu chai bán ra tháng này", 120.5),
                                 ("doanh thu tăng bao nhiêu", 12.5)):
            with self.subTest(question=question):
                self.assertEqual(round_money(question, "number", answer), answer)
        self.assertEqual(round_money("tỷ lệ doanh thu", "percent", 12.345), 12.345)

    def test_answers_the_question_rules_out(self):
        from datamate.agent.code_answer import implausible
        for question, kind, answer, reason in (
                ("Nhóm bánh chiếm bao nhiêu % doanh thu?", "number", 12.5, "percent"),
                ("Tỷ lệ đơn bị huỷ tháng này?", "number", 3, "percent"),
                ("Tháng này bán được bao nhiêu tuýp kem 5% tuýp 5g?", "percent", 12, "percent"),
                ("Ngày nào bán nhiều nhất?", "string", "Thứ bảy", "date"),
                ("Tháng này doanh thu có cao hơn tháng trước không?", "number", 5, "yes/no"),
                ("Doanh thu tháng này bao nhiêu?", "boolean", True, "yes/no"),
                ("Doanh thu tháng này bao nhiêu?", "number", 0, "zero"),
                ("Nhóm bánh chiếm bao nhiêu % doanh thu?", "percent", 0.0, "zero"),
                ("Doanh thu tháng này bao nhiêu?", "number", -5000, "negative")):
            with self.subTest(question=question, kind=kind):
                self.assertIn(reason, implausible(question, kind, answer))
        for question, kind, answer in (
                ("Tháng này bán được bao nhiêu tuýp kem 5% tuýp 5g?", "number", 12),
                ("Doanh thu tháng này tăng giảm bao nhiêu % so với tháng trước?", "percent", -4.5),
                ("Tháng này lãi ròng bao nhiêu?", "number", -62000),
                ("Doanh thu 3 tháng qua có tăng liên tục hok", "boolean", False),
                ("Tháng nào doanh thu cao nhất?", "date", "2026-03-01")):
            with self.subTest(question=question):
                self.assertIsNone(implausible(question, kind, answer))

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
                                 (unsafe_code_generate, "not allowed: os"),
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
        asked = Question("q", "demo_csv", "Hôm nay có rơi vào tháng Giêng âm lịch không?",
                         self.question.as_of_date)
        candidate = self.agent(calendar_code_generate, code_fallback=True).answer(
            asked, self.context)
        self.assertEqual(candidate.type, "boolean", candidate.reason)
        # The same code answers a revenue question with True/False: refused, not repaired.
        refused = self.agent(calendar_code_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertIn("does not fit the question", refused.reason)
        self.assertEqual(len(refused.evidence["code_attempt"]["code_votes"]), 1)

    def test_names_must_come_from_the_data(self):
        named = self.agent(named_code_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertEqual(named.type, "string", named.reason)
        # A name the code decorated is answered bare; a name it made up is refused.
        decorated = self.agent(decorated_name_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertEqual((decorated.type, decorated.answer), (named.type, named.answer))
        invented = self.agent(invented_name_generate, code_fallback=True).answer(
            self.question, self.context)
        self.assertIn("not in the data", invented.reason)
        self.assertIn("Plus", invented.reason)

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
