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
    spell_months,
    parse_rewrite,
    select_examples,
)
from datamate.contracts import BusinessContext, Question
from datamate.demo import create_demo
from datamate.ingestion import build_catalog
from datamate.io import load_split
from datamate.pipeline import Config
from datamate.semantics.planner import plan_question, unread_words
from datamate.semantics.rules import read_rules

# Invented catalogue entries used by EXAMPLES.
NAMES = ("Bánh quy bơ", "Kẹo gừng hộp 10 gói")
STANDARD = "Doanh thu tháng này là bao nhiêu?"
UNPLANNED = "Doanh thu tháng này là bao nhiêu khúc ngó?"


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



def retry_generate(cache, messages, max_input_tokens, max_new_tokens):
    if len(messages) == 2:
        return {"text": UNPLANNED}
    assert messages[-2] == {"role": "assistant", "content": UNPLANNED}
    assert "khuc ngo" in messages[-1]["content"] and UNSURE in messages[-1]["content"]
    return {"text": STANDARD}


def unplanned_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": UNPLANNED}


def unsafe_retry_generate(cache, messages, max_input_tokens, max_new_tokens):
    return {"text": UNPLANNED if len(messages) == 2 else "Doanh thu tháng 6/2026 là bao nhiêu?"}


def disagreement_after_retry_generate(cache, messages, max_input_tokens, max_new_tokens):
    if len(messages) == 2:
        return {"text": UNPLANNED}
    question = _shots(messages)[-1].removeprefix("Câu hỏi: ")
    forward = _shots(messages)[0] == f"Câu hỏi: {select_examples(question)[0][0]}"
    return {"text": STANDARD if forward else "Doanh thu hôm nay là bao nhiêu?"}


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


class SpellMonthsTests(unittest.TestCase):
    """Month spellings written out before the model reads the question (synthetic)."""

    def test_month_spellings(self):
        for raw, spelled in (("dthu th4/2026 tăng hay giảm", "dthu tháng 4/2026 tăng hay giảm"),
                             ("T6/2025 món nào lời nhất", "tháng 6/2025 món nào lời nhất"),
                             ("10/2025 khách nào mua nhiều", "tháng 10/2025 khách nào mua nhiều"),
                             ("trả hết trong 12-2025", "trả hết trong tháng 12/2025"),
                             ("thang 4/25 hom nao", "tháng 4/2025 hom nao"),
                             ("Th4 dthu nhóm", "tháng 4 dthu nhóm"),
                             ("giữa th1/2026, th2/2026", "giữa tháng 1/2026, tháng 2/2026")):
            with self.subTest(raw=raw):
                self.assertEqual(spell_months(raw), spelled)

    def test_dates_quarters_and_impossible_months_stay(self):
        for text in ("đến hết ngày 05/04/2026", "từ 01/02/2026 tới 10/02/2026", "quý 4/2025",
                     "doanh thu tháng 3/2026", "top 3 món th13/2026"):
            with self.subTest(text=text):
                self.assertEqual(spell_months(text), text)

    def test_checks_use_the_spelled_question(self):
        asked = spell_months("dthu th4/25 bn")
        self.assertIsNone(check_rewrite(asked, "Doanh thu tháng 4/2025 là bao nhiêu?"))
        for wrong in ("Doanh thu quý 4/2025 là bao nhiêu?", "Doanh thu năm 2025 là bao nhiêu?"):
            self.assertIsNotNone(check_rewrite(asked, wrong), wrong)
        self.assertIsNone(check_rewrite("dthu quý 4/2025", "Doanh thu quý 4/2025 là bao nhiêu?"))


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

    def test_unread_words_does_not_change_planning(self):
        rules = read_rules(self.split.rulebook)
        for wording, expected in ((UNPLANNED, "khuc ngo"), (STANDARD, None),
                                  ("Tháng này mảnh đốm vừa kể?", None)):
            with self.subTest(wording=wording):
                question = Question("diag", self.question.business_id, wording,
                                    self.question.as_of_date)
                before = plan_question(question, rules)
                self.assertEqual(unread_words(question, rules), expected)
                self.assertEqual(plan_question(question, rules), before)

    def test_each_vote_retries_and_both_plans_must_agree(self):
        candidate = self.agent(retry_generate).answer(self.question, self.context)
        self.assertEqual(candidate.type, "number", candidate.reason)
        attempts = candidate.evidence["rewrites"]
        self.assertEqual([(a["vote"], a["retry"]) for a in attempts],
                         [(1, 0), (1, 1), (2, 0), (2, 1)])
        self.assertEqual([a["rewrite"] for a in attempts], [UNPLANNED, STANDARD] * 2)
        disagreement = self.agent(disagreement_after_retry_generate).answer(
            self.question, self.context)
        self.assertEqual(disagreement.type, "abstain")
        self.assertEqual(disagreement.reason, "Rewrites lead to different plans")
        self.assertEqual(len(disagreement.evidence["rewrites"]), 4)

    def test_second_failure_and_unsafe_retry_abstain(self):
        for generate, reason in ((unplanned_generate, "Grammar cannot plan the rewrite"),
                                 (unsafe_retry_generate, "Rewrite changed the numbers of the question")):
            with self.subTest(generate=generate.__name__):
                candidate = self.agent(generate).answer(self.question, self.context)
                self.assertEqual((candidate.type, candidate.reason), ("abstain", reason))
                self.assertEqual(len(candidate.evidence["rewrites"]), 2)
        unsafe_first = self.agent(number_changing_generate).answer(self.question, self.context)
        self.assertEqual(len(unsafe_first.evidence["rewrites"]), 1)

    def test_disabled_retry_preserves_the_original_failure_and_single_attempt(self):
        candidate = self.agent(retry_generate, rewrite_retries=0).answer(self.question, self.context)
        self.assertEqual((candidate.type, candidate.reason),
                         ("abstain", "Grammar cannot plan the rewrite"))
        attempts = candidate.evidence["rewrites"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["rewrite"], UNPLANNED)
        self.assertNotIn("retry", attempts[0])
        normal = self.agent(standard_generate, rewrite_retries=0).answer(self.question, self.context)
        expected = self.agent(standard_generate).answer(self.question, self.context)
        self.assertEqual((normal.type, normal.answer, normal.reason),
                         (expected.type, expected.answer, expected.reason))
        self.assertEqual([a["prompt_sha256"] for a in normal.evidence["rewrites"]],
                         [a["prompt_sha256"] for a in expected.evidence["rewrites"]])

    def test_config_accepts_the_rewrite_backend(self):
        config = Config.load(Path(__file__).resolve().parents[1] / "configs" / "rewrite.toml")
        self.assertEqual((config.backend, config.min_confidence, config.rewrite_retries),
                         ("rewrite", 0.5, 1))
        with self.assertRaisesRegex(ValueError, "rewrite_retries"):
            Config(rewrite_retries=2)


if __name__ == "__main__":
    unittest.main()


# A synthetic ranking row the deterministic reader leaves off ("đơn nhiều nhất" matches no
# measure pattern) and a fake model fills; rewrites may then target its name.
RULE_ROW = "| Món đắt hàng nhất | món có đơn nhiều nhất |"
# Shape of the real Qwen output on L4 (probe 4b): empty ``ten``, name copied with accents.
RULE_SPEC = {"ten": "", "cach_goi": ["món đắt hàng nhất"], "don_hop_le": "luat_chung",
             "mau_so": "", "kieu_dap_an": "string", "nguon": "", "doi_tuong": "mat_hang",
             "chi_tieu": "so_don", "chon": "lon_nhat", "dieu_kien": {}}
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
