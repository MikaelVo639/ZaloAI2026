"""Price-increase effect (rulebook §5): units of one product N days from D against the N
days just before D. Rulebook rows and sales below are synthetic, written for tests."""
import unittest
from datetime import date
from decimal import Decimal

from test_semantics_periods import rulebook

from datamate.agent.compute import _answer_price_effect, unsupported
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

NAMES = "| Tên gọi | Tên gọi khác nhau cùng chỉ một thứ là một mặt hàng; file có mã thì theo mã |"
CHANGE = "| Phần trăm thay đổi, tăng trưởng | so với **kỳ trước**; giảm thì ra **số âm** |"
EFFECT = ("| Hiệu ứng tăng giá | lần tăng giá = ngày D giá mới cao hơn giá liền trước (theo bảng "
          "giá). So số lượng (đơn vị cơ bản) **N ngày kể từ D** (tính cả D) với **N ngày ngay "
          "trước D**; cả hai khoảng bỏ đơn huỷ và đơn trả |")
NAMES_IN_DATA = ["Trà vải (L)", "Prospan siro ho chai 100ml", "Cơm trắng"]


def plan(text, extra=EFFECT, today="2026-07-31"):
    rules = read_rules(rulebook() + NAMES + "\n" + CHANGE + "\n" + extra + "\n")
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), rules,
                         NAMES_IN_DATA)


def sale(order, day, quantity, status="valid", unit="ly"):
    return Sale(order, date.fromisoformat(day), status, Decimal(1000), "SP1", "Trà vải (L)",
                Decimal(quantity), base_unit=unit)


# Before 05/06 (29/05-04/06): 4 + 6 = 10 ly; from 05/06 (05/06-11/06): 7 ly. 28/05 and
# 12/06 lie outside both windows; cancelled and returned orders never count.
SALES = [sale("A", "2026-05-28", 50), sale("B", "2026-05-29", 4), sale("C", "2026-06-04", 6),
         sale("D", "2026-06-03", 9, "cancelled"), sale("E", "2026-06-05", 7),
         sale("F", "2026-06-06", 5, "returned"), sale("G", "2026-06-12", 40)]


class PriceEffectGrammarTests(unittest.TestCase):
    def test_wordings_with_the_increase_day(self):
        for text, unit in (
            ("So số ly Trà vải (L) bán ra trong 7 ngày kể từ ngày 05/06/2026, ngày tăng giá lên "
             "47.000đ, với 7 ngày ngay trước ngày đó: tăng hay giảm bao nhiêu %?", "ly"),
            ("Từ ngày 05/06/2026, Trà vải (L) tăng giá lên 47.000đ. So 7 ngày kể từ ngày "
             "05/06/2026 với 7 ngày ngay trước đó, số ly bán ra thay đổi bao nhiêu phần trăm?",
             "ly"),
            ("Ngày 05/06/2026 Trà vải (L) tăng giá từ 45.000đ lên 47.000đ. Số ly bán được "
             "trong 7 ngày kể từ ngày đó so với 7 ngày trước đó thay đổi bao nhiêu %?", "ly"),
            ("trà vải (l) lên giá 47.000đ từ ngày 05/06/2026; so 7 ngày kể từ ngày tăng giá với "
             "7 ngày ngay trước, lượng bán tính theo ly tăng hay giảm nhiêu %", "ly"),
            ("xem giúp từ ngày 05/06/2026 cơm trắng tăng giá lên 10.500đ so 7 ngày kể từ ngày "
             "05/06/2026 vs 7 ngày ngay trước đó số phần bán ra thay đổi bao nhiêu phần trăm",
             "phan"),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual(
                    (result.intent, result.unit, result.comparison, result.start, result.end,
                     result.compare_start, result.compare_end),
                    ("quantity", unit, "change_percent", date(2026, 6, 5), date(2026, 6, 11),
                     date(2026, 5, 29), date(2026, 6, 4)))
                self.assertEqual(unsupported(result), "")

    def test_other_wordings_abstain(self):
        for text in (
            # No written day: the increase day must be read from the price table.
            "Kể từ khi Prospan siro ho chai 100ml lên giá 97.500 đồng/chai, 14 ngày đầu bán đc "
            "nhiều hay ít hơn 14 ngày liền trước đó bao nhiêu phần trăm, tính theo số chai?",
            # A daily average, revenue, two spans or a window not over yet.
            "Từ ngày 05/06/2026 Trà vải (L) lên giá 47.000đ, so 7 ngày kể từ ngày tăng giá với "
            "7 ngày trước đó, trung bình mỗi ngày số ly bán ra tăng hay giảm bao nhiêu %?",
            "Từ ngày 05/06/2026, Trà vải (L) tăng giá lên 47.000đ. So 7 ngày kể từ ngày "
            "05/06/2026 với 7 ngày ngay trước đó, doanh thu thay đổi bao nhiêu phần trăm?",
            "Từ ngày 05/06/2026, Trà vải (L) tăng giá lên 47.000đ. So 7 ngày kể từ ngày "
            "05/06/2026 với 14 ngày ngay trước đó, số ly bán ra thay đổi bao nhiêu phần trăm?",
            "Từ ngày 28/07/2026, Trà vải (L) tăng giá lên 47.000đ. So 7 ngày kể từ ngày "
            "28/07/2026 với 7 ngày ngay trước đó, số ly bán ra thay đổi bao nhiêu phần trăm?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))
        self.assertIsNone(plan("Từ ngày 05/06/2026, Trà vải (L) tăng giá lên 47.000đ. So 7 ngày "
                               "kể từ ngày 05/06/2026 với 7 ngày ngay trước đó, số ly bán ra "
                               "thay đổi bao nhiêu phần trăm?", extra=""))


class PriceEffectExecutionTests(unittest.TestCase):
    TEXT = ("Từ ngày 05/06/2026, Trà vải (L) tăng giá lên 47.000đ. So 7 ngày kể từ ngày "
            "05/06/2026 với 7 ngày ngay trước đó, số ly bán ra thay đổi bao nhiêu phần trăm?")

    def run_plan(self, sales):
        result = plan(self.TEXT)
        return result, _answer_price_effect(result, sales, {"inputs": {}}, "", "sales")

    def test_change_of_valid_units(self):
        result, candidate = self.run_plan(SALES)
        self.assertEqual((candidate.type, candidate.answer), ("percent", -30.0))
        self.assertIs(check_executed(candidate, result), candidate)

    def test_other_base_unit_or_nothing_before_abstains(self):
        _, candidate = self.run_plan([sale("B", "2026-05-29", 4, unit="cái"),
                                      sale("E", "2026-06-05", 7, unit="cái")])
        self.assertEqual(candidate.type, "abstain")
        _, candidate = self.run_plan([sale("E", "2026-06-05", 7)])
        self.assertEqual(candidate.type, "abstain")


if __name__ == "__main__":
    unittest.main()
