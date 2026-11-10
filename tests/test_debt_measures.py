"""C01 §7 debt intents on the D01 ledger: debt_total (+ order age), debt_customer_count,
debt_collected and debt_flow. Rulebook rows and ledgers are synthetic, written for tests.
"""
import unittest
from datetime import date
from decimal import Decimal

from test_s01_grammar import rulebook

from datamate.agent import compute
from datamate.agent.validation import check_executed
from datamate.contracts import Catalog, DebtLedger, DebtOrder, DebtPayment, Question
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

FLOW = ("| Nợ thêm, trả nợ, thu nợ trong kỳ | chỉ đơn ghi nợ, không huỷ. Nợ thêm: phần tổng đơn "
        "chưa trả ngay trong ngày mua, của đơn đặt trong kỳ. Trả nợ / thu nợ: các khoản trả sau "
        "ngày mua có ngày trả trong kỳ. Tiền trả cùng ngày mua là trả lúc mua |")
AGE = ("| Tuổi nợ | số ngày tính từ ngày đặt, không từ hạn thanh toán. Đơn đặt hôm nay có tuổi "
       "nợ 0 ngày |")
BOOK = rulebook(extra=FLOW + "\n" + AGE)
TODAY = "2026-07-15"


def plan(text, book=BOOK):
    return plan_question(Question("x", "b", text, date.fromisoformat(TODAY)), read_rules(book))


def order(oid, day, total, customer=None, name=None, state="hoan thanh", credit=True,
          excluded=""):
    return DebtOrder(oid, day, state, Decimal(total), "Ghi nợ" if credit else "Tiền mặt", credit,
                     excluded, customer, name)


# K1 Trần Thị Lan: D1 (1/6, 100k, paid 40k same day, 30k on 10/6), D2 (1/7, 50k, nothing paid).
# K2 Bình: D3 (5/7, 80k, paid 80k on 8/7). Walk-in D4 (2/7, 20k). COD D5 and cancelled D6.
LEDGER = DebtLedger(
    orders=(
        order("D1", date(2026, 6, 1), 100000, "K1", "Trần Thị Lan"),
        order("D2", date(2026, 7, 1), 50000, "K1", "Trần Thị Lan"),
        order("D3", date(2026, 7, 5), 80000, "K2", "Bình"),
        order("D4", date(2026, 7, 2), 20000, None, "Khách lẻ"),
        order("D5", date(2026, 7, 3), 70000, "K2", "Bình", excluded="cod"),
        order("D6", date(2026, 7, 4), 90000, "K2", "Bình", state="huy"),
    ),
    payments=(
        DebtPayment("D1", date(2026, 6, 1), Decimal(40000)),
        DebtPayment("D1", date(2026, 6, 10), Decimal(30000)),
        DebtPayment("D3", date(2026, 7, 8), Decimal(80000)),
        DebtPayment("D5", date(2026, 7, 9), Decimal(10000)),
    ),
    first_day=date(2026, 6, 1), last_day=date(2026, 7, 15), uses_customer_codes=True)


class DebtGrammarTests(unittest.TestCase):
    def test_wordings_from_train_give_the_contract_plans(self):
        cases = (
            ("Tính đến hết ngày 14/07/2026 khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
             "debt_total", date(2026, 7, 14), date(2026, 7, 14)),
            ("Đến hết ngày 14/07/2026 số tiền khách còn thiếu là bao nhiêu?",
             "debt_total", date(2026, 7, 14), date(2026, 7, 14)),
            ("Đến hết ngày 14/07/2026 số khách còn công nợ với cửa hàng là bao nhiêu?",
             "debt_customer_count", date(2026, 7, 14), date(2026, 7, 14)),
            ("Đến hết ngày 14/07/2026 cửa hàng còn bao nhiêu khách chưa trả hết nợ?",
             "debt_customer_count", date(2026, 7, 14), date(2026, 7, 14)),
            ("Tháng 6/2026 cửa hàng thu được bao nhiêu tiền khách trả nợ?",
             "debt_collected", date(2026, 6, 1), date(2026, 6, 30)),
            ("Trong tháng 6/2026 tiền khách trả bớt nợ các đơn mua chịu được bao nhiêu?",
             "debt_collected", date(2026, 6, 1), date(2026, 6, 30)),
            ("Tháng 6/2026 thu hồi được bao nhiêu tiền công nợ từ khách mua chịu?",
             "debt_collected", date(2026, 6, 1), date(2026, 6, 30)),
        )
        for text, intent, start, end in cases:
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.start, result.end), (intent, start, end))
                self.assertEqual(compute.unsupported(result), "")

    def test_order_age_threshold(self):
        result = plan("Tính đến hết ngày 14/07/2026 các đơn đặt đã hơn 7 ngày mà khách chưa "
                      "trả hết còn nợ bao nhiêu tiền?")
        self.assertEqual((result.intent, result.threshold.op, result.threshold.value),
                         ("debt_total", ">", Decimal(7)))
        result = plan("Đến hết ngày 14/07/2026 các khoản nợ có tuổi quá 15 ngày còn bao nhiêu tiền?")
        self.assertEqual(result.threshold.value, Decimal(15))

    def test_customer_flow(self):
        result = plan("Tháng 7/2026 khách Trần Thị Lan ghi nợ thêm bao nhiêu và trả nợ được bao nhiêu, "
                      "ghi hai số theo thứ tự nợ thêm, trả được")
        self.assertIsNone(result)  # July is still running on 15/07: the period is in the future
        result = plan("Tháng 6/2026 khách Vương Hạnh Chi ghi nợ thêm bao nhiêu và trả nợ được "
                      "bao nhiêu")
        self.assertEqual((result.intent, [(f.field, f.value) for f in result.filters]),
                         ("debt_flow", [("customer", "vuong hanh chi")]))

    def test_unread_conditions_and_missing_rules_abstain(self):
        for text in (
            "Đến hết ngày 14/07/2026 khách còn nợ bao nhiêu ở nhóm tuổi nợ 0-6 ngày, 7-15 ngày "
            "và trên 15 ngày?",  # different buckets
            "Đến hết ngày 14/07/2026 khách Trần Thị Lan còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
            "Khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",  # no snapshot day
            "Đến hết ngày 14/07/2026 khách sỉ còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
            "Đến hết ngày 20/07/2026 khách còn nợ cửa hàng tổng cộng bao nhiêu tiền?",  # future
        ):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))
        self.assertIsNone(plan("Tháng 6/2026 thu hồi được bao nhiêu tiền công nợ từ khách "
                               "mua chịu?", book=rulebook(extra=AGE)))


class DebtExecutionTests(unittest.TestCase):
    def test_measures_follow_contract_section_7(self):
        day = date(2026, 7, 14)
        # D1 30k + D2 50k + walk-in D4 20k; D3 paid, D5 COD, D6 cancelled.
        self.assertEqual(compute.debt_total(LEDGER, day), Decimal(100000))
        # Ages on 14/7: D1 43 days, D2 13 days, walk-in D4 12 days.
        self.assertEqual(compute.debt_total(LEDGER, day, 7), Decimal(100000))
        self.assertEqual(compute.debt_total(LEDGER, day, 12), Decimal(80000))
        self.assertEqual(compute.debt_total(LEDGER, day, 13, inclusive=True), Decimal(80000))
        self.assertEqual(compute.debt_total(LEDGER, day, 13), Decimal(30000))
        self.assertEqual(compute.debt_age_buckets(LEDGER, day), [0, 70000, 30000])
        self.assertEqual(compute.debt_age_buckets(LEDGER, date(2026, 7, 9)), [20000, 50000, 30000])
        self.assertEqual(compute.debt_customer_count(LEDGER, day), 1)  # K1; walk-in not counted
        # June: new debt 100k - 40k paid on the day; repaid 30k (10/6).
        self.assertEqual(compute.debt_flow(LEDGER, date(2026, 6, 1), date(2026, 6, 30)),
                         (Decimal(60000), Decimal(30000)))
        # July: D2 50k + D3 80k + walk-in 20k new; D3's 80k on 8/7 repaid; COD D5 excluded.
        self.assertEqual(compute.debt_flow(LEDGER, date(2026, 7, 1), date(2026, 7, 14)),
                         (Decimal(150000), Decimal(80000)))
        self.assertEqual(compute.debt_flow(LEDGER, date(2026, 7, 1), date(2026, 7, 14), "K2"),
                         (Decimal(80000), Decimal(80000)))

    def answer(self, text, ledger=LEDGER, readme=""):
        result = plan(text)
        cache = {"debt_key": None}
        catalog = Catalog("b", readme, ())
        original = compute.prepare_debt
        compute.prepare_debt = lambda cache, catalog, rulebook: (
            cache.update(debt=ledger) or {"ok": True})
        try:
            return result, compute.answer_plan(cache, catalog, result, BOOK)
        finally:
            compute.prepare_debt = original

    def test_answers_pass_validation(self):
        for text, expected in (
            ("Tính đến hết ngày 14/07/2026 khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
             100000),
            ("Đến hết ngày 14/07/2026 số khách còn công nợ với cửa hàng là bao nhiêu?", 1),
            ("Đến hết ngày 14/07/2026 khách còn nợ bao nhiêu ở nhóm tuổi nợ 0–7 ngày, "
             "8–15 ngày và trên 15 ngày?", [0, 70000, 30000]),
            ("Tháng 6/2026 thu hồi được bao nhiêu tiền công nợ từ khách mua chịu?", 30000),
            ("Tháng 6/2026 khách Trần Thị Lan ghi nợ thêm bao nhiêu và trả nợ được bao nhiêu",
             [60000, 30000]),
        ):
            with self.subTest(text=text):
                result, candidate = self.answer(text)
                self.assertEqual(candidate.answer, expected)
                self.assertIs(check_executed(candidate, result), candidate)

    def test_unknown_customer_and_instalments_abstain(self):
        _, candidate = self.answer("Tháng 6/2026 khách Mai ghi nợ thêm bao nhiêu và trả nợ được "
                                   "bao nhiêu")
        self.assertEqual(candidate.type, "abstain")
        _, candidate = self.answer(
            "Đến hết ngày 14/07/2026 số khách còn công nợ với cửa hàng là bao nhiêu?",
            readme="Sổ góp: mỗi đơn trả góp có dòng đầu ghi ngày mua.")
        self.assertEqual(candidate.type, "abstain")


if __name__ == "__main__":
    unittest.main()
