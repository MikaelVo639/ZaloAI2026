"""Chia việc 24 nhóm, 01–05: lead-ins, closing particles, average order value, the shop
and totals, cancelled orders, around measures the grammar already has.

Rulebook rows, product names, groups and sales are synthetic, written for tests.
"""
import unittest
from datetime import date
from decimal import Decimal

from test_group_share_wording import GROUP, SHARE
from test_new_intents import ROWS as INTENT_ROWS
from test_new_intents import rulebook as intents_rulebook
from test_sem02_grammar import ROWS as SEM02_ROWS
from test_shift import SHIFT_ROW

from datamate.agent import compute
from datamate.contracts import Catalog, Question
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import normalize_question as fold_question

BOOK = "\n".join((intents_rulebook().rstrip("\n"), SEM02_ROWS["unit"], SEM02_ROWS["names"],
                  SHIFT_ROW, GROUP.strip(), SHARE.strip())) + "\n"
NAMES = ("Nước rửa chén Sunlight", "Bánh croissant bơ", "Nho xanh Mỹ")
VOCABULARY = {"product_group": ["Đồ uống", "Hải sản"]}
TODAY = date(2026, 6, 15)


def normalize_question(text):
    """The everyday-wording pass (the one with the shop's names)."""
    return fold_question(text, product_names=NAMES, as_of_year=TODAY.year)


def plan(text, names=NAMES, book=BOOK):
    """Like the Agent: first without the shop's names, then with them."""
    rules = read_rules(book)
    question = Question("x", "b", text, TODAY)
    return plan_question(question, rules) or plan_question(question, rules, names, VOCABULARY)


def key(result):
    if result is None:
        return None
    return (result.intent, result.start, result.end, result.unit, result.comparison,
            tuple((f.field, f.value) for f in result.filters))


class LeadInTests(unittest.TestCase):
    """01. Asking to be told, at the start of the question."""

    def test_lead_in_gives_the_plan_of_the_bare_question(self):
        for lead in ("Cho em hỏi, ", "cho mình hỏi xíu ", "Anh xem giúp em, ", "nhờ bạn tính giúp ",
                     "Chị cho em hỏi ",
                     "check jum ", "chị ơi mik mún bít xíu, ", "alo, ", "hỏi nhanh, ",
                     "Cho tôi hỏi ", "Nho xem "):
            for bare in ("Doanh thu ca chiều hôm qua bao nhiêu?",
                         "tháng 5/2026 bán được bao nhiêu chai Nước rửa chén Sunlight?",
                         "tháng 5/2026 nhóm Đồ uống chiếm bao nhiêu % doanh thu?"):
                with self.subTest(lead=lead, bare=bare):
                    expected = plan(bare)
                    self.assertIsNotNone(expected)
                    self.assertEqual(key(plan(lead + bare)), key(expected))
        shift = plan("Cho em hỏi, doanh thu ca chiều hôm qua bao nhiêu?")
        self.assertEqual((shift.intent, shift.start, shift.end, shift.filters[0].value),
                         ("revenue", date(2026, 6, 14), date(2026, 6, 14), "chieu"))

    def test_only_at_the_start_and_names_are_kept(self):
        self.assertEqual(normalize_question("doanh thu hôm nay cho em hỏi"),
                         "doanh thu hom nay cho em hoi")
        # "Nho" (grape) is a name, not "nhờ": no lead-in verb follows it.
        result = plan("Nho xanh Mỹ tháng 5/2026 bán được bao nhiêu kg?")
        self.assertEqual(result.filters[0].value, "Nho xanh Mỹ")
        # "Hồi tháng 1 năm 2025" = back in January 2025.
        self.assertEqual(normalize_question("Hồi T3/2025 bị huỷ mấy đơn vậy?"),
                         "thang 3/2025 bi huy may don")

    def test_conditions_after_a_lead_in_are_not_dropped(self):
        for text in ("Cho em hỏi doanh thu tháng 5/2026 chỉ tính nhóm Sỉ",
                     "Cho em hỏi doanh thu tháng 5/2026 của khách nữ"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))


class ClosingParticleTests(unittest.TestCase):
    """02. "hỉ", "rứa", "gồi", "thế" close a question only as written, with their marks."""

    def test_particles_give_the_plan_of_the_bare_question(self):
        for bare in ("Hôm qua doanh thu bao nhiêu", "Hôm qua có mấy đơn",
                     "7 ngày gần nhất được bnhiu đơn", "Mặt hàng bán chạy nhất tháng 5/2026 là gì"):
            for particle in (" hỉ?", " rứa?", " gồi?", " thế?", ", thế? 🤔", " zạ"):
                with self.subTest(bare=bare, particle=particle):
                    self.assertIsNotNone(plan(bare))
                    self.assertEqual(key(plan(bare + particle)), key(plan(bare)))

    def test_words_with_other_marks_stay(self):
        self.assertEqual(plan("Tuần trước bán được bao nhiêu gói?").unit, "goi")
        self.assertEqual(normalize_question("tháng 5 bán bao nhiêu chai nước rửa"),
                         "thang 5 ban bao nhieu chai nuoc rua")
        self.assertEqual(normalize_question("doanh thu trả bằng thẻ"), "doanh thu tra bang the")
        self.assertEqual(normalize_question("doanh thu tra bang the"), "doanh thu tra bang the")
        # Right after a name, a particle is not removed: the question stays unread.
        self.assertIsNone(plan("tháng 5/2026 bán được bao nhiêu chai Nước rửa chén Sunlight rứa?"))

    def test_particles_after_revenue_period_and_store_keep_source_names(self):
        for text, bare in (
                ("Bình quân mỗi đơn tháng 5/2026 đem về bao nhiêu doanh thu thế?",
                 "Bình quân mỗi đơn tháng 5/2026 bao nhiêu doanh thu?"),
                ("Doanh thu tháng 5/2026 zạ?", "Doanh thu tháng 5/2026?"),
                ("Dthu của quán tháng 5/2026 bnhiu rứa?", "Doanh thu tháng 5/2026?")):
            with self.subTest(text=text):
                self.assertEqual(key(plan(text, names=NAMES + ("Rửa", "Quần áo",))),
                                 key(plan(bare)))
        self.assertIsNone(plan("Doanh thu tháng 5/2026 bnhiu rua?", names=NAMES + ("Rửa",)))
        ambiguous = Question("fixture", "shop", "Doanh thu tháng 5/2026 bnhiu rứa?", TODAY)
        self.assertIsNone(plan_question(ambiguous, read_rules(BOOK), NAMES + ("Rứa",)))
        self.assertIsNone(plan("Doanh thu tháng 5/2026 bnhiu luon?", names=NAMES + ("Lươn xào",)))


def sale(order, day, status, amount, **extra):
    return Sale(order, day, status, Decimal(amount), "P1", "Trà", Decimal(1), **extra)


def answer(text, sales):
    result = plan(text)
    if result is None:
        raise AssertionError(f"no plan: {text}")
    catalog = Catalog("b", "", ())
    cache = {"sales_key": compute._catalog_key(catalog),
             "sales": (sales, {"tables": ["fixture"], "raw_rows": len(sales),
                               "sales_rows": len(sales), "duplicates_removed": 0})}
    return compute.answer_plan(cache, catalog, result)


class AverageOrderTests(unittest.TestCase):
    """03. Money on one order, in both word orders and with "bình quân"."""

    def test_wordings(self):
        for text in ("Tuần trước bình quân mỗi đơn bnhiu tiền?",
                     "Tuần trước bình quân mỗi o đờ mang về bao nhiêu doanh thu?",
                     "Dthu bq mỗi đơn tuần trc là bnhiu?",
                     "Nhờ bạn tính dúp doanh thu bình quân chên mỗi đơn của tuần trước?",
                     "Tuần trước mỗi đơn trung bình đem về bao nhiêu doanh thu vậy?",
                     "Tuần trước mỗi đơn bình quân được bao nhiêu doanh thu nhỉ?",
                     "Tuần trước trung bình một đơn có doanh thu bao nhiêu?",
                     "Tuần trước khách mua trung bình mỗi đơn hết bao nhiêu tiền?",
                     "Tuần trước trung bình mỗi đơn giá trị bnhiu?",
                     "Binh quan don hang tuan truoc gia tri bnhiu z?",
                     "Giá trị bình quân 1 đơn tuần trước được bao nhiêu thế?",
                     "Tuần trước mỗi đơn trung bình thu được bao nhiêu tiền?"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.start, result.end),
                                 ("average_order", date(2026, 6, 8), date(2026, 6, 14)))
                self.assertIn(INTENT_ROWS["average"], result.evidence)

    def test_other_measures_per_order_or_per_day_stay_out(self):
        for text in ("Tuần trước trung bình mỗi đơn có mấy món?",
                     "Trung bình mỗi ngày tuần trước có bao nhiêu đơn?",
                     "Tuần trước trung bình mỗi đơn khách phải trả bao nhiêu?",
                     "Tuần trước bình quân mỗi đơn bnhiu tiền ship?"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertTrue(result is None or result.intent != "average_order", text)

    def test_needs_the_rule_row(self):
        self.assertIsNone(plan("Tuần trước bình quân mỗi đơn bnhiu tiền?",
                               book=BOOK.replace(INTENT_ROWS["average"], "")))

    def test_value_counts_each_order_once(self):
        day = date(2026, 6, 14)
        sales = [sale("A", day, "valid", 50000), sale("A", day, "valid", 30000),
                 sale("B", day, "valid", 120000), sale("C", day, "cancelled", 900000)]
        result = answer("Hôm qua mỗi đơn trung bình thu được bao nhiêu tiền?", sales)
        self.assertEqual((result.type, result.answer), ("number", 100000))
        result = answer("Doanh thu bq mỗi đơn hôm qua là bao nhiêu?", sales)
        self.assertEqual((result.type, result.answer), ("number", 100000))


class ShopAndTotalTests(unittest.TestCase):
    """04. The shop ("quán", "tiệm") and totals ("tổng", "tất cả", "thu về")."""

    def test_shop_and_total_words(self):
        same = {
            "Tháng 5/2026 doanh thu bao nhiêu tiền?": "doanh thu tháng 5/2026",
            "Tháng 5/2026 tiệm mình thu được bao nhiêu doanh thu thế?": "doanh thu tháng 5/2026",
            "Tháng 5/2026 sốp có doanh thu bao nhiêu tiền?": "doanh thu tháng 5/2026",
            "Tháng 5/2026 dthu cũa tiệm ra bao nhiêu rứa?": "doanh thu tháng 5/2026",
            "Tháng 5/2026 tiệm tổng dthu được bao nhiêu vậy?": "doanh thu tháng 5/2026",
            "Tháng 5/2026 doanh thu mấy đồng?": "doanh thu tháng 5/2026",
            "Tháng 5/2026 bán ra được mấy đơn rồi?": "tháng 5/2026 có mấy đơn",
            "Tính tất cả doanh thu của tiệm hôm qua là bao nhiêu?": "doanh thu hôm qua",
            "Hôm qua quán bán được nhiêu tiền rứa?": "doanh thu hôm qua",
            "Hôm qua tiệm thu về bao nhiêu doanh thu?": "doanh thu hôm qua",
            "Hqua có dthu tổng bnhiu?": "doanh thu hôm qua",
            "T3/2026 tiem co tat ca bao nhieu don?": "tháng 3/2026 có bao nhiêu đơn",
            "Hom ni quan duoc may don roi?": "hôm nay có mấy đơn",
            "T10/2025 quan ban duoc bao nhieu tien?": "doanh thu tháng 10/2025",
        }
        for text, bare in same.items():
            with self.subTest(text=text):
                self.assertIsNotNone(plan(bare))
                self.assertEqual(key(plan(text)), key(plan(bare)))

    def test_group_and_share_are_kept(self):
        result = plan("Hôm qua nhóm Đồ uống thu về bao nhiêu tiền?")
        self.assertEqual((result.intent, result.filters[0].field, result.filters[0].value),
                         ("revenue", "product_group", "Đồ uống"))
        share = plan("05/2026 nhóm hải sản chiếm mấy % dthu cả quán?")
        self.assertEqual((share.comparison, share.filters[0].value), ("share", "Hải sản"))
        self.assertEqual(key(plan("05/2026 nhom hai san chiem may phan tram dthu ca quan")),
                         key(share))

    def test_unaccented_quan_is_the_shop_only_without_such_product_names(self):
        text = "Tháng 5/2026 quan ban duoc bao nhieu cai?"
        self.assertEqual(plan(text).unit, "cai")
        clothes = NAMES + ("Quần ống suông",)
        self.assertIsNone(plan(text, names=clothes))
        named = plan("Tháng 5/2026 bán được bao nhiêu cái Quần ống suông?", names=clothes)
        self.assertEqual(named.filters[0].value, "Quần ống suông")
        # "bình quân", "thuốc tiêm" are not the shop.
        self.assertIsNone(plan("Tháng 5/2026 thuoc tiem ban duoc bao nhieu tien?"))
        self.assertEqual(key(plan("Tháng 5/2026 dthu wan mik được nhiêu?", names=clothes)),
                         key(plan("Doanh thu tháng 5/2026?")))


class CancelledOrderTests(unittest.TestCase):
    """05. "đơn huỷ", "huỷ mất mấy đơn": counted by status, apart from the rate."""

    def test_wordings(self):
        for text in ("có bao nhiêu đơn huỷ trong th5/2026", "5-2026 huỷ mất mấy đơn z?",
                     "Tháng 5/2026 kó bnhiu đơn bị huỷ vậy?",
                     "Th5/2026 đếm đh huỷ được bnhiu?", "Th5/2026 có mấy đơn huỷ z?",
                     "Thang 5 nam 2026 quan co may don bi huy?", "alo th5/2026 wan mik huy bnhiu don"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.start, result.end),
                                 ("cancel_count", date(2026, 5, 1), date(2026, 5, 31)))

    def test_negation_reason_and_rate_stay_apart(self):
        for text in ("Th5/2026 có mấy đơn không bị huỷ?", "Th5/2026 có bao nhiêu đơn chưa huỷ?",
                     "Th5/2026 ko bnhiu đơn bị huỷ?",
                     "Th5/2026 có mấy đơn bị huỷ vì giao trễ z?",
                     "Th5/2026 đơn huỷ chiếm bao nhiêu %?"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertTrue(result is None or result.intent != "cancel_count", text)

    def test_count_is_the_cancelled_status_only(self):
        day = date(2026, 5, 4)
        sales = ([sale(f"H{i}", day, "cancelled", 1000) for i in range(3)]
                 + [sale(f"T{i}", day, "returned", 1000) for i in range(2)]
                 + [sale(f"V{i}", day, "valid", 1000) for i in range(5)])
        result = answer("Có bao nhiêu đơn huỷ trong tháng 5/2026?", sales)
        self.assertEqual((result.type, result.answer), ("number", 3))


if __name__ == "__main__":
    unittest.main()
