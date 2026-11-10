"""End-of-data stock of one product (README "Tồn kho" convention). Shops are synthetic."""
import tempfile
import unittest
from unittest import mock
from datetime import date
from decimal import Decimal
from pathlib import Path

from test_semantics_periods import rulebook

from datamate.agent import compute
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion import build_catalog
from datamate.ingestion import stock as stock_source
from datamate.ingestion.sales import Sale
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

    def test_bua_nay_reads_as_hom_nay(self):
        result = plan("Tồn kho Bia Tiger lon cuối ngày bữa nay là bao nhiêu lon?")
        self.assertEqual((result.intent, result.start), ("stock", date(2026, 7, 14)))

    def test_sales_codes_match_on_a_cold_cache(self):
        # The stock file names the product differently from sales; only the sales code links
        # them. A fresh worker cache must load sales itself, so the answer never depends on
        # which question the worker happened to answer before.
        stock = STOCK.replace("SP1,Bia Tiger lon,", "SP1,Bia Tiger,")
        sale = Sale("O1", date(2026, 7, 1), "valid", Decimal(1), "SP1", "Bia Tiger lon",
                    Decimal(1))

        def fake_prepare(cache, catalog, rulebook=""):
            cache["sales"] = ([sale], "sales")
            return {"ok": True}

        result = plan("Tồn kho Bia Tiger lon cuối ngày hôm nay là bao nhiêu lon?")
        with mock.patch.object(compute, "prepare_sales", fake_prepare):
            candidate = compute.answer_plan({}, self.catalog(stock=stock), result, BOOK)
        self.assertEqual(candidate.answer, 43)
        # Sales that cannot load leave only the name, which no longer matches: abstain.
        failed = compute.answer_plan({}, self.catalog(stock=stock), result, BOOK)
        self.assertEqual(failed.type, "abstain")

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

    def test_conversion_lookup_only_inspects_documented_conversion_sections(self):
        readme = README + """- **Quy đổi đơn vị**: hệ số quy đổi ở `quy_doi.csv`.

## Cột trong `quy_doi.csv`
- **Mã**: mã hàng
- **ĐVT lớn**: đơn vị lớn
- **Hệ số**: hệ số
"""
        root = Path(self.directory.name)
        self.catalog(readme)
        (root / "b" / "quy_doi.csv").write_text(
            "Mã,ĐVT lớn,Hệ số\nSP1,thùng,24\n", encoding="utf-8"
        )
        catalog = build_catalog(root, "b")
        with mock.patch.object(stock_source, "_matching_tables",
                               wraps=stock_source._matching_tables) as matching:
            conversions = stock_source.load_conversions(catalog)
        self.assertEqual([(c.key, c.big_unit, c.factor) for c in conversions],
                         [("SP1", "thung", Decimal(24))])
        self.assertEqual([call.args[1].file for call in matching.call_args_list], ["quy_doi.csv"])

    def test_documented_csv_stock_report_profile(self):
        readme = ("## File\n- `ton_kho.csv` (csv)\n\n## Quy ước\n"
                  "- Dữ liệu có đến hết 14/07/2026.\n"
                  "- **Tồn kho**: **Ton kho** là tồn cuối ngày cuối có dữ liệu, theo đơn vị cơ bản.\n")
        csv = "ID SP,Hàng hóa,Đơn vị,Ton kho\nSP1,Bia Tiger lon,lon,43\n"
        snapshot = load_stock(self.catalog(readme, csv))
        self.assertEqual(snapshot.items[0].product_id, "SP1")
        with self.assertRaises(StockSourceError):
            load_stock(self.catalog(readme.replace("`ton_kho.csv`", "`other.csv`"), csv))



LOT_README = """## Cột trong `san_pham.csv`
- **SKU**: mã hàng
- **Tên**: tên
- **ĐVT**: đơn vị cơ bản

## Cột trong `nhap_lo.csv`
- **Phiếu**: mã phiếu nhập
- **SKU**: mã hàng
- **Lô**: số lô
- **SL nhập**: số lượng nhập (đơn vị cơ bản)

## Cột trong `ban_lo.csv`
- **Đơn**: mã đơn
- **Lô**: số lô
- **SL**: số lượng bán lấy từ lô (đơn vị cơ bản)
- **SL trả**: số lượng khách trả lại (đơn vị cơ bản); lớn hơn 0: đơn đó có trả hàng

## Quy ước
- Dữ liệu có đến hết 14/07/2026.
- **Lô, hạn dùng**: mỗi dòng nhập là một lô, số lô không trùng. Một dòng bán có thể lấy từ nhiều lô. Hàng khách trả về lại lô đã bán.
- **Quy đổi đơn vị**: số lượng ghi theo đơn vị của dòng:
  - Panadol hộp 10 vỉ: 1 hộp = 100 viên
"""
PRODUCTS = "SKU,Tên,ĐVT\nSP1,Bia Tiger lon,lon\nSP2,Panadol hộp 10 vỉ,viên\n"
# SP2: lots L2 (100) + L3 (150) = 250 received; sold 60 + 40 - 30 returned = 70 out -> 180.
RECEIPTS = "Phiếu,SKU,Lô,SL nhập\nP1,SP1,L1,48\nP1,SP2,L2,100\nP2,SP2,L3,150\n"
LOT_SALES = "Đơn,Lô,SL,SL trả\nO1,L1,5,0\nO2,L2,60,0\nO3,L3,40,30\n"


class StockFromLotsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def catalog(self, readme=LOT_README, receipts=RECEIPTS, sales=LOT_SALES):
        shop = Path(self.directory.name) / "b"
        shop.mkdir(exist_ok=True)
        for name, text in (("README.md", readme), ("san_pham.csv", PRODUCTS),
                           ("nhap_lo.csv", receipts), ("ban_lo.csv", sales)):
            (shop / name).write_text(text, encoding="utf-8")
        return build_catalog(Path(self.directory.name), "b")

    def test_received_less_sold_plus_brought_back(self):
        snapshot = load_stock(self.catalog())
        self.assertEqual(snapshot.day, date(2026, 7, 14))
        self.assertEqual([(i.product_id, i.product_name, i.base_unit, int(i.quantity))
                          for i in snapshot.items],
                         [("SP1", "Bia Tiger lon", "lon", 43),
                          ("SP2", "Panadol hộp 10 vỉ", "viên", 180)])
        self.assertIn("mỗi dòng nhập là một lô", snapshot.convention)
        book = BOOK + SPLIT_RULE
        result = plan("Tồn cuối hôm nay của Panadol hộp 10 vỉ là mấy hộp chẵn, mấy viên lẻ?",
                      book=book)
        candidate = compute.answer_plan({}, self.catalog(), result, book)
        self.assertEqual((candidate.type, candidate.answer), ("list", [1, 80]))

    def test_undocumented_or_inconsistent_lots_abstain(self):
        rule = "- **Lô, hạn dùng**: mỗi dòng nhập là một lô"
        cases = {
            # No lot convention, or the README defines stock its own way.
            "absent": dict(readme=LOT_README.replace(rule, "- **Lô**: số lô")),
            "own stock line": dict(readme=LOT_README + "- **Tồn kho**: mỗi dòng sổ kho là tồn "
                                   "sau lần nhập/bán đó, theo đơn vị cơ bản.\n"),
            # Quantities not documented in base units.
            "unit": dict(readme=LOT_README.replace("số lượng nhập (đơn vị cơ bản)",
                                                   "số lượng nhập (đơn vị nhập)")),
            # A repeated lot, a sale from an unknown lot, more brought back than sold,
            # stock below zero.
            "repeated": dict(receipts=RECEIPTS + "P3,SP1,L1,10\n"),
            "unknown lot": dict(sales=LOT_SALES + "O4,L9,1,0\n"),
            "returned": dict(sales=LOT_SALES + "O4,L1,1,2\n"),
            "negative": dict(sales=LOT_SALES + "O4,L1,50,0\n"),
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name), self.assertRaises(StockSourceError):
                load_stock(self.catalog(**kwargs))

if __name__ == "__main__":
    unittest.main()
