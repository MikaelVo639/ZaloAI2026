"""Drug revenue joins and filters are verified with synthetic catalogs and sales."""

import sqlite3
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from test_semantics_periods import rulebook

from datamate.agent.compute import _answer_drug_revenue, unsupported
from datamate.agent.validation import check_executed
from datamate.contracts import Question
from datamate.ingestion import build_catalog
from datamate.ingestion.drugs import load_drugs
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

MONEY = ("| Bán được bao nhiêu tiền, thu về bao nhiêu tiền | là doanh thu, "
         "không phải tiền khách đã trả |\n")
INGREDIENT = ("| Doanh thu theo hoạt chất | doanh thu (§1) các thuốc có hoạt chất ghi đúng như vậy "
              "trong danh mục thuốc. Thuốc phối hợp nhiều hoạt chất là một loại riêng, không tính "
              "vào từng hoạt chất trong đó |\n")
RX = ("| Thuốc kê đơn | sản phẩm ghi kê đơn trong danh mục thuốc; mọi sản phẩm còn lại là hàng "
      "không kê đơn. Tỉ lệ kê đơn tính theo doanh thu |\n")
BOOK = rulebook() + MONEY + INGREDIENT + RX
CONVENTION = ('- **Thuốc**: **Cần toa** đúng (TRUE/1) là thuốc kê đơn. Hoạt chất ghi không kèm '
              'hàm lượng; nhiều chất nối bằng " + ".\n')
README = """## Cột trong `dm.csv`
- **SKU**: mã hàng
- **Chất**: hoạt chất (trống với vật tư)
- **Cần toa**: thuốc kê đơn

## Quy ước
""" + CONVENTION
ROWS = ("SKU,Chất,Cần toa\nA01,Paracetamol,FALSE\nA02,Paracetamol + Caffeine,1\n"
        "A03,Paracetamol,0\nA04,,FALSE\n")


def plan(text, book=BOOK):
    return plan_question(Question("x", "b", text, date(2026, 4, 12)), read_rules(book))


def sale(code, amount, *, status="valid", day=date(2026, 3, 15)):
    return Sale(code, day, status, Decimal(amount), code, code, Decimal(1))


class DrugRevenueTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.shop = self.root / "b"
        self.shop.mkdir()

    def catalog(self, rows=ROWS, readme=README):
        (self.shop / "dm.csv").write_text(rows, encoding="utf-8")
        (self.shop / "README.md").write_text(readme, encoding="utf-8")
        return build_catalog(self.root, "b")

    def test_telex_typos_need_no_drug_specific_patterns(self):
        # "thuoocs" (thuốc) and the closing "nhir" (nhỉ) go through the shared Telex decoder.
        typed = plan("tháng 3/2026 thuốc kê đơn chiếm bao nhiêu % doanh thu của nhà thuoocs nhir")
        plain = plan("tháng 3/2026 thuốc kê đơn chiếm bao nhiêu % doanh thu của nhà thuốc")
        self.assertIsNotNone(plain)
        self.assertEqual((typed.filters, typed.comparison), (plain.filters, plain.comparison))

    def test_doctor_prescription_wording_reads_as_ke_don(self):
        # "Cần đơn bác sĩ" is what "kê đơn" means; "không cần đơn bác sĩ" is "không kê đơn".
        for text, value in (
            ("Tháng 3/2026 bán hàng không cần đơn bác sĩ được bao nhiêu tiền?", "non_rx"),
            ("thang 3/2026 ban hang ko can toa bac si duoc bao nhieu tien z", "non_rx"),
            ("Tháng 3/2026 bán thuốc cần đơn bác sĩ được bao nhiêu tiền?", "rx"),
        ):
            with self.subTest(text=text):
                result = plan(text)
                self.assertEqual([(f.field, f.value) for f in result.filters],
                                 [("prescription", value)])
        # Without the rulebook row there is no prescription label to read.
        self.assertIsNone(plan("Tháng 3/2026 bán hàng không cần đơn bác sĩ được bao nhiêu tiền?",
                               book=rulebook() + MONEY))

    def test_formula_and_exact_join(self):
        cases = (
            ("Doanh thu thuốc hoạt chất Paracetamol tháng 3/2026 là bao nhiêu?", "number", 150),
            ("Thuốc có hoạt chất Paracetamol + Caffeine tháng 3/2026 bán được bao nhiêu tiền?",
             "number", 80),
            ("Tỉ lệ doanh thu thuốc kê đơn trên tổng doanh thu tháng 3/2026 là bao nhiêu %?",
             "percent", 30.77),
            ("Trừ thuốc kê đơn ra, tháng 3/2026 các mặt hàng còn lại bán được bao nhiêu tiền?",
             "number", 180),
        )
        sales = [sale("A01", 100), sale("A02", 80), sale("A03", 50), sale("A04", 10),
                 sale("B01", 20), sale("A02", 900, status="cancelled"),
                 sale("A02", 900, status="returned"), sale("A01", 600, day=date(2026, 4, 1))]
        catalog = self.catalog()
        for question, kind, value in cases:
            with self.subTest(question=question):
                result = plan(question)
                self.assertEqual(unsupported(result), "")
                candidate = _answer_drug_revenue({}, catalog, BOOK, result, sales, {})
                self.assertEqual((candidate.type, candidate.answer), (kind, value))
                self.assertIs(check_executed(candidate, result), candidate)

    def test_partial_ingredient_or_unknown_condition_is_not_answered(self):
        base = "Doanh thu thuốc hoạt chất Paracetamol tháng 3/2026 là bao nhiêu?"
        self.assertIsNone(plan(base, BOOK.replace(INGREDIENT, "")))
        self.assertIsNone(plan(base.replace("3/2026", "4/2026")))
        self.assertIsNone(plan(base + " Chỉ chi nhánh 1."))
        self.assertIsNone(plan("Thuốc có hoạt chất Paracetamol tháng này uống mấy viên?"))
        for text in (base.replace("Paracetamol", "Caffeine"),
                     base.replace("Paracetamol", "Caffeine + Paracetamol"),
                     base.replace("Paracetamol", "Paracetamol Caffeine")):
            result = plan(text)
            self.assertEqual(_answer_drug_revenue({}, self.catalog(), BOOK, result,
                                                 [sale("A02", 80)], {}).type, "abstain")

    def test_invalid_flag_duplicate_code_and_undocumented_source(self):
        for rows, readme in ((ROWS.replace("Caffeine,1", "Caffeine,2"), README),
                             (ROWS.replace("A02", "A01"), README),
                             (ROWS, README.replace(CONVENTION, ""))):
            with self.subTest(rows=rows, readme=readme):
                with self.assertRaises(ValueError):
                    load_drugs(self.catalog(rows, readme))

    def test_sqlite_table_named_by_the_readme(self):
        with closing(sqlite3.connect(self.shop / "shop.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE meds (MaHangHoa, ThanhPhanChinh, RxFlag)")
            connection.execute("INSERT INTO meds VALUES ('AB1', 'Paracetamol', 1)")
            connection.execute("CREATE TABLE other (MaHangHoa, ThanhPhanChinh, RxFlag)")
            connection.execute("INSERT INTO other VALUES ('AB1', 'Caffeine', 1)")
        catalog = self.catalog(readme=README.replace("**Cần toa** đúng", "`meds.RxFlag` đúng"))
        drugs = load_drugs(catalog)
        self.assertEqual(drugs.products["AB1"].ingredient, "Paracetamol")
        self.assertTrue(drugs.products["AB1"].prescription)
