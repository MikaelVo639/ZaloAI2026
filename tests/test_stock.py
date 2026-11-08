"""End-of-data stock of one product (README "Tồn kho" convention). Shops are synthetic."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from test_semantics_periods import rulebook

from datamate.agent import compute
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion import build_catalog
from datamate.ingestion.stock import StockSourceError, load_stock
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

UNTIL = ("| Đến hết ngày X | Tình trạng lúc hết ngày đó, tính mọi phát sinh từ đầu dữ liệu |")
NAMES = ("| Tên gọi | Tên gọi khác nhau cùng chỉ một thứ là một mặt hàng; file có mã thì theo mã |")
BOOK = rulebook() + UNTIL + "\n" + NAMES + "\n"
SPLIT_RULE = ("| Tồn theo đơn vị lớn | list 2 số [số đơn vị lớn nguyên, số đơn vị cơ bản lẻ]; "
              "không có phần lẻ thì số thứ hai là 0. Phần lẻ vẫn tính bằng đơn vị cơ bản |\n")
README = """# Dữ liệu shop

## File
- `ton_kho.csv` (csv)

## Cột trong `ton_kho.csv`
- **Mã**: mã hàng
- **Tên**: tên hàng
- **ĐVT**: đơn vị cơ bản
- **Tồn cuối**: tồn kho (đơn vị cơ bản)

## Quy ước
- Dữ liệu có đến hết 14/07/2026.
- **Tồn kho**: **Tồn cuối** là tồn cuối ngày cuối có dữ liệu, theo đơn vị cơ bản.
"""
STOCK = "Mã,Tên,ĐVT,Tồn cuối\nSP1,Bia Tiger lon,lon,43\nSP2,Panadol hộp 10 vỉ,viên,120\n"


def plan(text, today="2026-07-14", book=BOOK):
    names = ["Bia Tiger lon", "Panadol hộp 10 vỉ", "SP1", "SP2"]
    return plan_question(Question("x", "b", text, date.fromisoformat(today)), read_rules(book),
                         names)


class StockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def catalog(self, readme=README, stock=STOCK):
        shop = Path(self.directory.name) / "b"
        shop.mkdir(exist_ok=True)
        (shop / "README.md").write_text(readme, encoding="utf-8")
        (shop / "ton_kho.csv").write_text(stock, encoding="utf-8")
        return build_catalog(Path(self.directory.name), "b")

    def test_load_stock_from_the_readme_convention(self):
        snapshot = load_stock(self.catalog())
        self.assertEqual(snapshot.day, date(2026, 7, 14))
        self.assertEqual([(i.product_id, i.base_unit, int(i.quantity)) for i in snapshot.items],
                         [("SP1", "lon", 43), ("SP2", "viên", 120)])
        with self.assertRaises(StockSourceError):
            load_stock(self.catalog(readme=README.replace("- **Tồn kho**", "- **Kho**")))

    def test_wordings(self):
        for text in ("Tồn kho Bia Tiger lon cuối ngày hôm nay là bao nhiêu lon?",
                     "Hôm nay kho còn tồn bao nhiêu lon Bia Tiger lon lúc cuối ngày?",
                     "Tính đến cuối ngày hôm nay trong kho còn mấy lon Bia Tiger lon?"):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual((result.intent, result.unit, result.start), ("stock", "lon",
                                                                              date(2026, 7, 14)))
                self.assertEqual(result.filters[0].value, "Bia Tiger lon")
        for text in ("Tồn kho Panadol hộp 10 vỉ cuối ngày hôm nay được mấy hộp chẵn mấy viên lẻ?",
                     "Tồn kho Bia Tiger lon ngày 01/07/2026 là bao nhiêu lon?",
                     "Tồn kho Bia Tiger lon ở chi nhánh 2 cuối ngày hôm nay là bao nhiêu lon?"):
            with self.subTest(text=text):
                self.assertIsNone(plan(text))

    def answer(self, text, today="2026-07-14"):
        result = plan(text, today)
        return result, compute.answer_plan({}, self.catalog(), result, BOOK)

    def test_answer_in_base_unit_only_on_the_last_data_day(self):
        result, candidate = self.answer("Tồn kho Bia Tiger lon cuối ngày hôm nay là bao nhiêu lon?")
        self.assertEqual(candidate.answer, 43)
        self.assertIs(check_executed(candidate, result), candidate)
        _, candidate = self.answer("Tồn kho Panadol hộp 10 vỉ cuối ngày hôm nay là bao nhiêu hộp?")
        self.assertEqual(candidate.type, "abstain")  # not the base unit
        _, candidate = self.answer("Tồn kho Bia Tiger lon cuối ngày hôm nay là bao nhiêu lon?",
                                   today="2026-07-10")
        self.assertEqual(candidate.type, "abstain")  # stock only known at the end of 14/07

    def test_split_stock_from_documented_base_conversion(self):
        book = BOOK + SPLIT_RULE
        readme = README + ("- **Quy đổi đơn vị**: số lượng ghi theo đơn vị của dòng:\n"
                           "  - Panadol hộp 10 vỉ: 1 hộp = 100 viên\n"
                           "  - Panadol hộp 10 vỉ: 1 vỉ = 10 viên\n")
        text = "Tồn cuối hôm nay của Panadol hộp 10 vỉ là mấy hộp chẵn, mấy viên lẻ?"
        result = plan(text, book=book)
        self.assertIsNone(plan(text, book=BOOK))
        self.assertIsNone(plan(text + " Ở chi nhánh 2?", book=book))
        catalog = self.catalog(readme)
        candidate = compute.answer_plan({}, catalog, result, book)
        self.assertEqual((candidate.type, candidate.answer), ("list", [1, 20]))
        self.assertIs(check_executed(candidate, result), candidate)
        wrong_unit = plan(text.replace("viên lẻ", "vỉ lẻ"), book=book)
        self.assertEqual(compute.answer_plan({}, catalog, wrong_unit, book).type, "abstain")
        self.assertEqual(compute.answer_plan({}, catalog, result, BOOK).type, "abstain")
        self.assertEqual(compute.answer_plan({}, self.catalog(), result, book).type, "abstain")
        self.assertEqual(compute.answer_plan({}, self.catalog(readme, STOCK.replace("120", "100")),
                                             result, book).answer, [1, 0])

    def test_documented_csv_stock_report_profile(self):
        readme = ("## File\n- `ton_kho.csv` (csv)\n\n## Quy ước\n"
                  "- Dữ liệu có đến hết 14/07/2026.\n"
                  "- **Tồn kho**: **Ton kho** là tồn cuối ngày cuối có dữ liệu, theo đơn vị cơ bản.\n")
        csv = "ID SP,Hàng hóa,Đơn vị,Ton kho\nSP1,Bia Tiger lon,lon,43\n"
        snapshot = load_stock(self.catalog(readme, csv))
        self.assertEqual(snapshot.items[0].product_id, "SP1")
        with self.assertRaises(StockSourceError):
            load_stock(self.catalog(readme.replace("`ton_kho.csv`", "`other.csv`"), csv))


if __name__ == "__main__":
    unittest.main()
