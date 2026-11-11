"""Model rewrite layer. Fake models test plumbing and safety checks, never model quality."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from datamate.agent.baseline import BaselineAgent
from datamate.agent.rewrite import (
    EXAMPLES,
    UNSURE,
    RewriteAgent,
    build_rewrite_messages,
    check_rewrite,
    numbers,
    parse_rewrite,
    select_examples,
)
from datamate.contracts import BusinessContext, Question
from datamate.demo import create_demo
from datamate.ingestion import build_catalog
from datamate.io import load_split
from datamate.pipeline import Config

# Invented catalogue entries used by EXAMPLES.
NAMES = ("Bánh quy bơ", "Kẹo gừng hộp 10 gói")
STANDARD = "Doanh thu tháng này là bao nhiêu?"


def fake_load(cache, model_path):
    return {"model_id": "synthetic-test-only", "revision": "none"}


def _shots(messages):
    return [line for line in messages[1]["content"].splitlines() if line.startswith("Câu hỏi:")]


def standard_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": f"Viết lại: {STANDARD}"}


def disagreeing_generate(cache, messages, max_input_tokens, max_new_tokens):
    # Prompt order is the only difference between the two restatements.
    question = _shots(messages)[-1].removeprefix("Câu hỏi: ")
    most_similar_first = _shots(messages)[0] == f"Câu hỏi: {select_examples(question)[0][0]}"
    return {"text": STANDARD if most_similar_first else "Doanh thu tháng 7/2026 là bao nhiêu?"}


def unsure_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": UNSURE}


def number_changing_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": "Doanh thu tháng 7/2026 là bao nhiêu?"}


def failing_load(cache, model_path):
    raise RuntimeError("no GPU in this test")


class RewriteCheckTests(unittest.TestCase):
    def test_every_example_passes_its_own_checks(self):
        for raw, standard in EXAMPLES:
            with self.subTest(standard=standard):
                self.assertIsNone(check_rewrite(raw, standard, NAMES))

    def test_numbers_are_compared_after_separators_and_k(self):
        self.assertEqual(numbers("từ 500k trở lên"), numbers("từ 500.000 đồng trở lên"))
        self.assertEqual(numbers("2tr"), numbers("2.000.000 đồng"))
        self.assertIsNone(check_rewrite("gia 1 hop", "giá một hộp"))
        self.assertIsNone(check_rewrite("binh quan moi don", "giá trị trung bình một đơn"))
        self.assertIsNotNone(check_rewrite("doanh thu thang 2/2026", "Doanh thu tháng 3/2026"))
        self.assertIsNotNone(check_rewrite("doanh thu tuan truoc", "Doanh thu tuần 9/2026"))
        self.assertIsNotNone(check_rewrite("ngay 1/2/2026", "ngày 2/2026"))

    def test_names_must_be_kept_and_never_invented(self):
        self.assertIsNone(check_rewrite("banh quy bo thang nay", "Bánh quy bơ tháng này", NAMES))
        self.assertIsNotNone(check_rewrite("banh quy bo thang nay", "Doanh thu tháng này", NAMES))
        self.assertIsNotNone(check_rewrite("doanh thu thang nay", "Bánh quy bơ tháng này", NAMES))

    def test_meaning_words_cannot_be_dropped_or_flipped(self):
        cases = (("don ko huy thang nay", "Số đơn tháng này"),           # negation dropped
                 ("so don thang nay", "Số đơn bị huỷ tháng này"),        # cancel invented
                 ("dthu tien mat thang nay", "Doanh thu tháng này"),     # payment dropped
                 ("dthu thang nay so vs thang truoc", "Doanh thu tháng này"),
                 ("so don thang nay", "Số đơn không huỷ tháng này"))     # negation invented
        for question, rewrite in cases:
            with self.subTest(question=question):
                self.assertIsNotNone(check_rewrite(question, rewrite))
        self.assertIsNone(check_rewrite("dthu thang nay so vs thang truoc",
                                        "Doanh thu tháng này so với tháng trước"))

    def test_output_must_be_one_short_line(self):
        self.assertIsNotNone(check_rewrite("dthu thang nay", "Doanh thu\ntháng này"))
        self.assertIsNotNone(check_rewrite("dthu thang nay", "x" * 301))
        self.assertIsNotNone(check_rewrite("dthu thang nay", ""))


class RewritePromptTests(unittest.TestCase):
    def test_parse_strips_prefix_and_quotes_and_reads_unsure(self):
        self.assertEqual(parse_rewrite('Viết lại: "Doanh thu tháng này là bao nhiêu?"\n'),
                         "Doanh thu tháng này là bao nhiêu?")
        self.assertIsNone(parse_rewrite(UNSURE))
        self.assertIsNone(parse_rewrite("  \n "))

    def test_examples_are_chosen_by_similarity_and_deterministic(self):
        chosen = select_examples("thang 2/2026 bi huy may don", 3)
        self.assertEqual(len(chosen), 3)
        self.assertIn("huỷ", chosen[0][1])
        self.assertEqual(chosen, select_examples("thang 2/2026 bi huy may don", 3))

    def test_prompt_has_question_and_examples_but_no_routing_ids(self):
        question = Question("secret-id", "secret-business", "dthu thang nay", date(2026, 8, 27))
        messages = build_rewrite_messages(question.question, select_examples(question.question))
        text = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("secret", text)
        self.assertTrue(messages[1]["content"].endswith("Câu hỏi: dthu thang nay\nViết lại:"))


class RewriteAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        create_demo(root / "input")
        self.split = load_split(root / "input")
        self.context = BusinessContext(self.split, build_catalog(self.split.root / "data", "demo_csv"))
        self.question = Question("q", "demo_csv", "quán mình tháng này thu về nhiêu tiền đó ta",
                                 date(2026, 8, 27))

    def agent(self, generate, load=fake_load, **kwargs):
        class FakeRewriteAgent(RewriteAgent):
            load_model = staticmethod(load)
        FakeRewriteAgent.generate = staticmethod(generate)
        agent = FakeRewriteAgent("unused-test-snapshot", **kwargs)
        self.addCleanup(agent.close)
        return agent

    def baseline_answer(self, text):
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        return agent.answer(Question("q", "demo_csv", text, date(2026, 8, 27)), self.context)

    def test_grammar_answers_never_load_the_model(self):
        agent = self.agent(standard_generate, load=failing_load)
        candidate = agent.answer(Question("q", "demo_csv", STANDARD, date(2026, 8, 27)),
                                 self.context)
        self.assertEqual(candidate.type, "number")
        self.assertEqual(candidate.confidence, 1.0)
        self.assertEqual(agent.models, [])

    def test_agreeing_rewrites_are_planned_and_answered_by_the_grammar(self):
        candidate = self.agent(standard_generate).answer(self.question, self.context)
        expected = self.baseline_answer(STANDARD)
        self.assertEqual((candidate.type, candidate.answer), (expected.type, expected.answer))
        self.assertEqual(candidate.confidence, 0.5)
        self.assertEqual([r["rewrite"] for r in candidate.evidence["rewrites"]], [STANDARD] * 2)
        self.assertEqual(candidate.evidence["intent"], "revenue")

    def test_disagreeing_unsure_or_unsafe_rewrites_abstain(self):
        for generate, reason in ((disagreeing_generate, "Rewrite"),
                                 (unsure_generate, "unsure"),
                                 (number_changing_generate, "numbers")):
            with self.subTest(generate=generate.__name__):
                candidate = self.agent(generate).answer(self.question, self.context)
                self.assertEqual(candidate.type, "abstain")
                self.assertIn(reason, candidate.reason)

    def test_one_vote_uses_one_rewrite(self):
        candidate = self.agent(disagreeing_generate, votes=1).answer(self.question, self.context)
        self.assertEqual(candidate.type, "number")
        self.assertEqual(len(candidate.evidence["rewrites"]), 1)

    def test_model_load_failure_abstains_and_is_cached(self):
        agent = self.agent(standard_generate, load=failing_load)
        first = agent.answer(self.question, self.context)
        second = agent.answer(self.question, self.context)
        self.assertEqual((first.type, second.type), ("abstain", "abstain"))
        self.assertIn("Local model unavailable", second.reason)

    def test_config_accepts_the_rewrite_backend(self):
        config = Config.load(Path(__file__).resolve().parents[1] / "configs" / "rewrite.toml")
        self.assertEqual((config.backend, config.min_confidence), ("rewrite", 0.5))


if __name__ == "__main__":
    unittest.main()


# A synthetic ranking row the deterministic reader leaves off ("đơn nhiều nhất" matches no
# measure pattern) and a fake model fills; rewrites may then target its name.
RULE_ROW = "| Món đắt hàng nhất | món có đơn nhiều nhất |"
RULE_SPEC = {"ten": "mon_dat_hang_nhat", "cach_goi": ["mon dat hang nhat"],
             "doi_tuong": "mat_hang", "chi_tieu": "so_don", "chon": "lon_nhat",
             "don_hop_le": "luat_chung", "dieu_kien": {}, "mau_so": "",
             "kieu_dap_an": "string", "nguon": "ignored"}
RULE_REWRITE = "Món đắt hàng nhất tháng 8/2026 là món gì?"


def rule_generate(cache, messages, max_input_tokens, max_new_tokens):
    if messages[0]["content"].startswith("Convert ONE"):
        return {"text": json.dumps(RULE_SPEC, ensure_ascii=False)}
    if "Món đắt hàng nhất" not in messages[0]["content"]:
        return {"text": UNSURE}  # the rule name must be offered to the rewrite
    return {"text": RULE_REWRITE}


class RewriteRuleSpecTests(unittest.TestCase):
    agent = RewriteAgentTests.agent

    def setUp(self):
        RewriteAgentTests.setUp(self)
        from dataclasses import replace
        self.split = replace(self.split, rulebook=self.split.rulebook + "\n" + RULE_ROW + "\n")
        self.context = BusinessContext(self.split, self.context.catalog)

    def test_model_fills_an_off_ranking_row_once_and_answers_its_name(self):
        from datamate.semantics.rule_spec import enabled_specs
        self.assertEqual(enabled_specs(self.split.rulebook), ())
        agent = self.agent(rule_generate)
        direct = agent.answer(Question("q1", "demo_csv", "Tháng 8/2026 món đắt hàng nhất là "
                                       "món gì?", date(2026, 8, 27)), self.context)
        self.assertEqual((direct.type, direct.answer), ("string", "Cà phê sữa"), direct.reason)
        self.assertEqual(direct.evidence["spec"]["nguon"], RULE_ROW)
        self.assertEqual([r["decision"] for r in agent.rule_spec_audit[0]["rows"]], ["on"])
        rewritten = agent.answer(Question("q2", "demo_csv", "th8/2026 quán bán món nào ra "
                                          "nhiều đơn nhứt", date(2026, 8, 27)), self.context)
        self.assertEqual((rewritten.type, rewritten.answer), ("string", "Cà phê sữa"),
                         rewritten.reason)
        self.assertEqual(rewritten.confidence, 0.5)
        self.assertEqual(len(agent.rule_spec_audit), 1)  # once per rulebook

    def test_without_the_model_the_rule_stays_off(self):
        agent = self.agent(rule_generate, load=failing_load)
        candidate = agent.answer(Question("q1", "demo_csv", "Tháng 8/2026 món đắt hàng nhất "
                                          "là món gì?", date(2026, 8, 27)), self.context)
        self.assertEqual(candidate.type, "abstain")
