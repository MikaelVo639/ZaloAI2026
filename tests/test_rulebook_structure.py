"""SEM-02 (12/11): structured rulebook of the current split for direction B.

The rulebook below is a synthetic paraphrase written for tests, not organizer data.
"""
import json
import unittest

from datamate.semantics.rulebook import parse_rulebook, rulebook_text, unused_rules

BOOK = """# Luật thử nghiệm
Hôm nay là as_of_date.

## 1. Doanh thu
| Luật | Mặc định |
|---|---|
| Đơn tính vào doanh thu | Đơn không huỷ và không trả hàng; trả một phần bỏ cả đơn |
| Doanh thu một đơn | Tổng thành tiền các dòng hàng; không trừ giảm giá cả đơn; không cộng thuế VAT; không gồm phí giao hàng |
| Quá N, trên N, dưới N | Không gồm N. Từ N trở lên thì gồm N |

## 2. Đáp án
Câu hỏi nhiều số thì ghi list theo đúng thứ tự câu hỏi nêu.

| type | Format | Đúng | Sai |
|---|---|---|---|
| date | "YYYY-MM-DD"; câu hỏi theo tháng: "YYYY-MM" | "2026-08-15" | "15/08/2026" |
"""


class RulebookStructureTests(unittest.TestCase):
    def test_sections_rules_notes_and_features(self):
        book = parse_rulebook(BOOK)
        self.assertEqual([s.title for s in book.sections], ["1. Doanh thu", "2. Đáp án"])
        revenue, answers = book.sections
        self.assertEqual([r.key for r in revenue.rules],
                         ["Đơn tính vào doanh thu", "Doanh thu một đơn", "Quá N, trên N, dưới N"])
        self.assertEqual(revenue.rules[2].features, ("threshold_inclusive",))
        date_rule = answers.rules[0]
        self.assertEqual((date_rule.key, date_rule.extra), ("date", ('"2026-08-15"', '"15/08/2026"')))
        self.assertEqual(date_rule.features, ("month_date",))
        self.assertEqual(answers.notes, ("Câu hỏi nhiều số thì ghi list theo đúng thứ tự câu hỏi nêu.",))
        self.assertTrue(book.features["revenue"] and book.features["threshold_inclusive"])
        self.assertEqual(book.find("quá n")[0].definition, "Không gồm N. Từ N trở lên thì gồm N")
        self.assertIn("Đơn tính vào doanh thu", [r.key for r in unused_rules(book)])
        self.assertEqual(json.loads(book.to_json())["version"], "rulebook_structure.v1")

    def test_follows_the_split_text_with_no_default(self):
        changed = BOOK.replace("Từ N trở lên thì gồm N", "Từ N trở lên thì không gồm N")
        rule = parse_rulebook(changed).find("quá n")[0]
        self.assertEqual(rule.definition, "Không gồm N. Từ N trở lên thì không gồm N")
        self.assertEqual(rule.features, ())  # the grammar no longer reads "từ N trở lên"
        self.assertFalse(parse_rulebook(changed).features["threshold_inclusive"])
        empty = parse_rulebook("")
        self.assertEqual((empty.sections, empty.features["revenue"]), ((), False))

    def test_prompt_text(self):
        text = rulebook_text(parse_rulebook(BOOK))
        self.assertIn("- Quá N, trên N, dưới N: Không gồm N. Từ N trở lên thì gồm N", text)
        self.assertIn("## 2. Đáp án", text)
        short = rulebook_text(parse_rulebook(BOOK), max_chars=120)
        self.assertTrue(short.endswith("[...]") and len(short) < 200)


if __name__ == "__main__":
    unittest.main()
