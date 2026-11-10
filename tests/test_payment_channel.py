"""Đợt 3 gói 1: revenue by payment method / sales channel.

Synthetic shop and rulebook rows written for this test, not organizer data. Labels are
matched to question words only through the shop's labels, the README description of them
and a short glossary of standard export codes.
"""

import unittest
from datetime import date

from test_sale_context_fields import _Shop
from test_semantics_periods import rulebook

from datamate.agent import compute
from datamate.agent.validation import check_executed
from datamate.contracts import Filter, Plan, Question
from datamate.semantics.payment import label_terms
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

PAYMENT = ("| Doanh thu theo hình thức thanh toán | theo hình thức **ghi trên đơn**, không theo lần "
           "trả nợ về sau: đơn ghi nợ không tính vào tiền mặt hay chuyển khoản. Đơn **COD** là "
           "hình thức riêng, không tính vào tiền mặt |\n")
LIST = "Câu hỏi nhiều số thì ghi list theo đúng thứ tự câu hỏi nêu.\n"
STATES = ("| Trạng thái đơn | mỗi đơn có đúng một trong bốn trạng thái: hoàn thành, huỷ, trả hàng, "
          "đang giao |\n")
BOOK = rulebook() + STATES + PAYMENT + LIST
DESCRIBED = ("hình thức thanh toán của đơn: cash tiền mặt, transfer chuyển khoản, ewallet ví điện "
             "tử, cod giao hàng thu tiền, debt ghi nợ")
README = f"""# Dữ liệu shop

## Cột trong `don.csv`
- **Mã CT**: mã đơn
- **Ngày**: ngày bán
- **Xử lý**: trạng thái đơn
- **Trả bằng**: {DESCRIBED}
- **Loại bán**: kênh bán

## Cột trong `chi_tiet.csv`
- **Mã CT**: mã đơn
- **Mã SP**: mã hàng
- **SL**: số lượng
- **ĐVT**: đơn vị tính
- **Tiền**: thành tiền sau giảm

## Cột trong `san_pham.csv`
- **Mã SP**: mã hàng
- **Tên SP**: tên
- **ĐV**: đơn vị cơ bản
"""
ORDERS = [["Mã CT", "Ngày", "Xử lý", "Trả bằng", "Loại bán"],
          ["D1", "02/03/2026", "Hoàn thành", "cash", "store"],
          ["D2", "03/03/2026", "Hoàn thành", "transfer", "zalo_oa"],
          ["D3", "04/03/2026", "Hoàn thành", "cod", "phone"],
          ["D4", "05/03/2026", "Hoàn thành", "debt", "store"],
          ["D5", "06/03/2026", "Huỷ", "cash", "store"],
          ["D6", "02/02/2026", "Hoàn thành", "cash", "store"]]
LINES = [["Mã CT", "Mã SP", "SL", "ĐVT", "Tiền"],
         ["D1", "A", "1", "cái", "100000"], ["D1", "B", "1", "cái", "20000"],
         ["D2", "A", "1", "cái", "50000"], ["D3", "B", "2", "cái", "30000"],
         ["D4", "A", "1", "cái", "70000"], ["D5", "A", "1", "cái", "999000"],
         ["D6", "A", "1", "cái", "40000"]]
PRODUCTS = [["Mã SP", "Tên SP", "ĐV"], ["A", "Áo", "cái"], ["B", "Bút", "cái"]]
TODAY = date(2026, 3, 20)
ROWS = PAYMENT.strip()


def ask(text, vocabulary, book=BOOK):
    rules = read_rules(book)
    question = Question("x", "b", text, TODAY)
    return plan_question(question, rules) or plan_question(question, rules, ["Áo", "Bút"],
                                                           vocabulary)


class VocabularyTests(unittest.TestCase):
    def test_readme_description_names_the_label(self):
        terms = dict(t.split("|") for t in label_terms(["cash", "transfer", "cod"], [DESCRIBED],
                                                       "payment"))
        self.assertEqual(terms["tien mat"], "cash")
        self.assertEqual(terms["tm"], "cash")
        self.assertEqual(terms["ck"], "transfer")
        self.assertEqual(terms["giao hang thu tien"], "cod")
        self.assertEqual(terms["cod"], "cod")

    def test_labels_written_in_vietnamese_need_no_description(self):
        terms = dict(t.split("|") for t in label_terms(
            ["Tiền mặt", "Chuyển khoản", "Thu hộ (COD)"], [], "payment"))
        self.assertEqual(terms["tien mat"], "Tiền mặt")
        self.assertEqual(terms["cod"], "Thu hộ (COD)")
        self.assertEqual(terms["thu ho"], "Thu hộ (COD)")

    def test_glossary_only_when_the_readme_is_silent(self):
        terms = dict(t.split("|") for t in label_terms(["store", "zalo_oa", "phone"], [],
                                                       "channel"))
        self.assertEqual((terms["tai quay"], terms["zalo oa"], terms["dien thoai"]),
                         ("store", "zalo_oa", "phone"))
        # A README description replaces the glossary for that label.
        described = label_terms(["store"], ["kênh bán: store bán online"], "channel")
        self.assertNotIn("tai quay|store", described)
        self.assertIn("ban online|store", described)

    def test_a_word_naming_two_labels_is_dropped(self):
        terms = label_terms(["Tiền mặt", "tien mat"], [], "payment")
        self.assertFalse(any(t.startswith("tien mat|") for t in terms))


class PaymentShopTests(_Shop):
    def setUp(self):
        super().setUp()
        self.catalog = self.shop(README, {"don.csv": ORDERS, "chi_tiet.csv": LINES,
                                          "san_pham.csv": PRODUCTS})
        self.cache = {}
        self.vocabulary = compute.vocabulary(self.cache, self.catalog, BOOK)

    def answer(self, text, book=BOOK):
        plan = ask(text, self.vocabulary, book)
        self.assertIsNotNone(plan, text)
        candidate = compute.answer_plan(self.cache, self.catalog, plan, BOOK)
        return plan, check_executed(candidate, plan)

    def test_revenue_of_one_payment_method(self):
        for text, value in (
                ("doanh thu các đơn trả bằng tiền mặt tháng 3/2026 là bao nhiêu", 120000),
                ("dthu cac don tra bang tm thang 3/2026", 120000),
                ("tháng 3/2026 dthu các đơn trả bằng ck được bao nhiêu", 50000),
                ("revenue cac don tra bang cod thang 3/2026 la bn", 30000),
                ("doanh thu các đơn trả bằng COD tháng 3/2026", 30000)):
            with self.subTest(text=text):
                plan, candidate = self.answer(text)
                self.assertEqual(plan.filters[0].field, "payment")
                self.assertIn(ROWS, plan.evidence)
                self.assertEqual((candidate.type, candidate.answer), ("number", value))

    def test_revenue_of_one_channel_and_its_share(self):
        plan, candidate = self.answer("tháng 3/2026 doanh thu các đơn bán tại quầy được nhiêu")
        self.assertEqual((plan.filters, candidate.answer),
                         ((Filter("channel", "store", ""),), 190000))
        _, candidate = self.answer("Tháng 3/2026, doanh thu các đơn bán qua điện thoại chiếm "
                                   "bao nhiêu phần trăm tổng doanh thu?")
        self.assertEqual((candidate.type, candidate.answer), ("percent", 11.11))  # 30k / 270k
        _, candidate = self.answer("Tỉ trọng doanh thu các đơn bán qua Zalo OA trong tổng doanh "
                                   "thu tháng 3/2026 là bao nhiêu phần trăm ạ?")
        self.assertEqual(candidate.answer, 18.52)

    def test_two_labels_give_a_list_in_question_order(self):
        for text, value in (
                ("Doanh thu các đơn trả bằng COD và doanh thu các đơn trả bằng tiền mặt tháng "
                 "3/2026 lần lượt là bao nhiêu?", [30000, 120000]),
                ("tháng 3/2026, dthu các đơn trả bằng ck và các đơn trả bằng tm mỗi loại bao "
                 "nhiêu", [50000, 120000]),
                ("So doanh thu các đơn bán tại quầy với doanh thu các đơn bán qua Zalo OA tháng "
                 "3/2026: mỗi bên bao nhiêu?", [190000, 50000]),
                ("Tháng 3/2026, doanh thu các đơn bán qua điện thoại là bao nhiêu, doanh thu các "
                 "đơn bán tại quầy là bao nhiêu?", [30000, 190000])):
            with self.subTest(text=text):
                plan, candidate = self.answer(text)
                self.assertEqual(plan.shape, "by_filter")
                self.assertEqual((candidate.type, candidate.answer), ("list", value))

    def test_two_short_channel_clauses_keep_the_question_order(self):
        _, result = self.answer("Tháng 3/2026 dthu đơn qua điện thoại bao nhiêu, "
                                "còn đơn tại quầy bao nhiêu?")
        self.assertEqual((result.type, result.answer), ("list", [30000, 190000]))
        self.assertIsNone(ask("Số đơn qua điện thoại và đơn tại quầy tháng 3/2026 lần lượt "
                              "là bao nhiêu?", self.vocabulary))

    def test_debt_orders_are_not_cash(self):
        # "Ghi nợ" is its own method: D4 is neither cash nor transfer, even if paid later.
        _, candidate = self.answer("doanh thu các đơn trả bằng tiền mặt tháng 3/2026")
        self.assertEqual(candidate.answer, 120000)  # D1 only; cancelled D5 excluded too

    def test_unclear_wordings_abstain(self):
        for text in (
                "doanh thu các đơn trả bằng thẻ tín dụng tháng 3/2026",          # unknown label
                "doanh thu các đơn trả bằng tiền mặt và các đơn bán qua Zalo OA tháng 3/2026",
                "doanh thu các đơn trả bằng tiền mặt, ck và cod tháng 3/2026 lần lượt",
                "doanh thu các đơn trả bằng tiền mặt và các đơn trả bằng tm tháng 3/2026",
                "số đơn trả bằng tiền mặt tháng 3/2026",                            # not revenue
                "doanh thu các đơn trả bằng tiền mặt tháng 3/2026 so với tháng 2/2026"):
            with self.subTest(text=text):
                self.assertIsNone(ask(text, self.vocabulary))

    def test_rules_of_the_split_gate_the_wording(self):
        without = rulebook() + STATES + LIST
        self.assertIsNone(ask("doanh thu các đơn trả bằng tiền mặt tháng 3/2026",
                              self.vocabulary, without))
        no_list = rulebook() + STATES + PAYMENT
        self.assertIsNone(ask("doanh thu các đơn trả bằng COD và các đơn trả bằng tiền mặt "
                              "tháng 3/2026 lần lượt", self.vocabulary, no_list))

    def test_shop_without_the_column_abstains(self):
        vocabulary = {**self.vocabulary, "payment_terms": [], "channel_terms": []}
        self.assertIsNone(ask("doanh thu các đơn trả bằng tiền mặt tháng 3/2026", vocabulary))
        self.assertIsNone(ask("doanh thu các đơn bán tại quầy tháng 3/2026", vocabulary))

    def test_returned_orders_and_sold_goods_are_not_payment_clauses(self):
        self.assertIsNone(ask("số đơn trả hàng tháng 3/2026", self.vocabulary))
        plan = ask("tổng số đơn bán tháng 3/2026", self.vocabulary)
        self.assertEqual((plan.intent, plan.filters), ("order_count", ()))


class ContractTests(unittest.TestCase):
    def test_by_filter_shape_is_restricted(self):
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        two = (Filter("payment", "cash"), Filter("payment", "cod"))
        self.assertEqual(compute.unsupported(Plan("revenue", start, end, filters=two,
                                                  shape="by_filter")), "")
        for plan in (Plan("order_count", start, end, filters=two, shape="by_filter"),
                     Plan("revenue", start, end, filters=two[:1], shape="by_filter"),
                     Plan("revenue", start, end, shape="by_filter",
                          filters=(Filter("payment", "cash"), Filter("channel", "store"))),
                     Plan("revenue", start, end, filters=two, comparison="share",
                          shape="by_filter"),
                     Plan("revenue", start, end, filters=two)):
            with self.subTest(plan=plan):
                self.assertTrue(compute.unsupported(plan))
        self.assertEqual(compute.unsupported(Plan("revenue", start, end,
                                                  filters=two[:1], comparison="share")), "")


if __name__ == "__main__":
    unittest.main()
