import sqlite3
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from datamate.agent.baseline import BaselineAgent
from datamate.agent.business_measures import (
    gross_profit,
    net_profit,
    top_gross_profit,
    unsold_count,
)
from datamate.agent.compute import answer_plan
from datamate.agent.validation import check_executed
from datamate.contracts import BusinessContext, Question, Split
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.sales import Sale
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules

RULEBOOK = """\
| Đơn tính vào doanh thu | đơn không huỷ và không trả hàng, bỏ cả đơn. Đơn đang giao vẫn tính |
| Doanh thu một đơn | tổng thành tiền các dòng hàng đã trừ giảm giá. Không trừ giảm giá cả đơn, không cộng thuế VAT, không gồm phí giao hàng |
| Tên gọi, nhãn | các tên cùng chỉ một thứ. File có mã thì theo mã |
| Tháng M/Y | cả tháng, từ ngày 1 đến hết ngày cuối tháng |
| Tháng này | từ ngày 1 đến hết hôm nay |
| Từ X đến Y | gồm cả X và Y |
| Đến hết ngày X, tính đến hôm nay | tình trạng lúc hết ngày đó, tính mọi phát sinh từ đầu dữ liệu đến hết ngày đó |
| Mặt hàng không bán được trong kỳ (ế) | mặt hàng đã từng bán trước kỳ, không tính đơn huỷ, đơn trả, nhưng trong kỳ không có đơn nào mua |
| Giao nhiều đợt | số đợt có ngày giao trong tháng X. Mỗi đợt của mỗi đơn, kể cả đợt của đơn sau đó trả hàng |
| Lãi gộp | doanh thu trừ số lượng nhân với giá vốn ghi trên dòng bán cho một đơn vị bán |
| Hàng lời nhất | theo tổng lãi gộp |
| Lãi ròng một tháng | lãi gộp trừ mọi khoản trong sổ chi phí của tháng |
| Lô gần hết hạn nhất của một thuốc | trong các lô còn tồn chưa quá hạn, lô có hạn sớm nhất |
| Hàng hết hạn trong N ngày tới | lô còn tồn có hạn từ D đến hết ngày D + N, theo giá nhập của chính lô |
| Giá trị hàng tồn kho | tồn hết hôm nay nhân giá vốn một đơn vị cơ bản ghi trong danh mục hàng, chỉ tính hàng còn tồn |
- **Danh sách:** liệt kê hàng theo hạn dùng thì hạn sớm nhất của mặt hàng trước, cùng hạn thì theo mã.
"""
TODAY = date(2026, 4, 30)


def plan(text, names=(), rulebook=RULEBOOK):
    return plan_question(Question("q", "shop", text, TODAY), read_rules(rulebook), names)


def sale(day, code, name, status="valid", *, amount=100, qty=2, cost=20):
    return Sale("order", date(2026, 4, day), status, Decimal(amount), code, name, Decimal(24),
                quantity_sold=Decimal(qty), unit_cost_at_sale=None if cost is None else Decimal(cost))


class BusinessMeasureTests(unittest.TestCase):
    def test_unsold_counts_prior_valid_products_by_code_or_folded_name(self):
        rows = [sale(23, "P1", "Tên cũ"), sale(24, "P1", "Tên mới"),
                sale(23, "P2", "Tên mới"), sale(24, "P2", "Tên mới", "returned"),
                sale(23, "P3", "Chưa bán", "cancelled"), sale(30, "P4", "Hàng mới"),
                sale(23, None, "Cà phê"), sale(30, None, "CA PHE")]
        question = plan("Có bao nhiêu mặt hàng vẫn đang bán mà trong 7 ngày gần nhất "
                        "không bán ra được cái nào?")
        self.assertEqual((question.intent, question.start, question.end),
                         ("unsold_count", date(2026, 4, 24), TODAY))
        self.assertEqual(unsold_count(rows, question.start, question.end), 1)
        variant = plan("Giai đoạn từ 24/04/2026 đến hết 30/04/2026 có tổng cộng mấy mặt hàng "
                       "ế, bán ko được món nào?")
        self.assertEqual((variant.intent, variant.start, variant.end),
                         (question.intent, question.start, question.end))
        self.assertIsNone(plan("7 ngày gần nhất có mấy mặt hàng ế theo nhóm xi măng?"))
        self.assertIsNone(plan("7 ngày gần nhất có mấy mặt hàng ế?", rulebook=""))

    def test_profit_uses_selling_units_and_every_expense_in_the_month(self):
        rows = [sale(5, "P1", "A"), sale(7, "P2", "B", amount=75, qty=1, cost=10)]
        self.assertEqual(gross_profit(rows), Decimal(125))
        self.assertEqual(top_gross_profit(rows, ties_by_code=True), "B")
        tied = [sale(5, "P2", "B"), sale(5, "P1", "A")]
        self.assertEqual(top_gross_profit(tied, ties_by_code=True), "A")
        self.assertEqual(net_profit(rows, [(TODAY, Decimal(30)), (TODAY, Decimal(6)),
                                          (date(2026, 5, 1), Decimal(50))],
                                    TODAY.replace(day=1), TODAY), 89)
        with self.assertRaises(ValueError):
            gross_profit([sale(5, "P1", "A", cost=None)])
        self.assertEqual(plan("Tháng 4/2026 mặt hàng nào lãi gộp cao nhất?").intent,
                         "top_gross_profit")

    def test_delivery_joins_by_order_and_trip_without_sales(self):
        with TemporaryDirectory() as folder:
            shop = Path(folder) / "shop"
            shop.mkdir()
            (shop / "README.md").write_text(
                "## Cột trong `deliveries.csv`\n- **Bill**: mã đơn\n"
                "- **Lần giao**: đợt giao (1, 2, 3)\n- **Ngày giao hàng**: ngày giao\n"
                "\n## Quy ước\n- Dữ liệu có đến hết 30/04/2026.\n", encoding="utf-8")
            source = shop / "deliveries.csv"
            header = "Bill,Lần giao,Ngày giao hàng,Status\n"
            data = ("A,1,31/03/2026,valid\nA,2,01/04/2026,returned\n"
                    "A,2,01/04/2026,returned\nB,1,30/04/2026,valid\n")
            source.write_text(header + data, encoding="utf-8")
            question = plan("Tháng này cửa hàng đã chở giao bao nhiêu đợt hàng cho khách?")
            catalog = build_catalog(Path(folder), "shop")
            candidate = check_executed(answer_plan({}, catalog, question, RULEBOOK), question)
            self.assertEqual((candidate.type, candidate.answer), ("number", 2))
            source.write_text(header + data + "A,2,02/04/2026,returned\n", encoding="utf-8")
            self.assertEqual(answer_plan({}, catalog, question, RULEBOOK).type, "abstain")

    def test_delivered_order_cohort_uses_placement_month_and_observation_date(self):
        book = RULEBOOK.replace("số đợt có ngày giao", "tính đến hết hôm nay. số đợt có ngày giao")
        book += "| Quá N, hơn N, trên N, dưới N | không gồm N. Từ N trở lên thì gồm N |\n"
        question = Question("q", "shop", "Tới hôm nay, mấy đơn đặt tháng 3/2026 đã được "
                            "chở thành 2 đợt trở lên?", TODAY)
        query = plan_question(question, read_rules(book))
        self.assertEqual((query.start, query.end, query.observed_on),
                         (date(2026, 3, 1), date(2026, 3, 31), TODAY))
        with TemporaryDirectory() as folder:
            shop = Path(folder) / "shop"
            shop.mkdir()
            (shop / "README.md").write_text(
                "## Cột trong `delivery.csv`\n- **ID**: mã đơn\n- **Trip**: đợt giao\n"
                "- **Day**: ngày giao\n"
                "## Cột trong `sales.csv`\n- **ID**: mã đơn\n- **Day**: ngày bán\n"
                "- **Qty**: số lượng\n- **Money**: thành tiền sau giảm\n", encoding="utf-8")
            (shop / "sales.csv").write_text(
                "ID,Day,Qty,Money\nA,30/03/2026,1,100\nA,10/04/2026,-1,-100\n"
                "B,31/03/2026,1,100\nC,01/04/2026,1,100\n", encoding="utf-8")
            (shop / "delivery.csv").write_text(
                "ID,Trip,Day\nA,1,30/03/2026\nA,1,30/03/2026\nA,2,30/04/2026\n"
                "B,1,01/04/2026\nB,2,01/05/2026\nC,1,01/04/2026\nC,2,02/04/2026\n",
                encoding="utf-8")
            root = Path(folder)
            context = BusinessContext(Split(root, (question,), book, root / "calendar.csv"),
                                      build_catalog(root, "shop"))
            agent = BaselineAgent()
            self.addCleanup(agent.close)
            candidate = agent.answer(question, context)
            self.assertEqual((candidate.type, candidate.answer), ("number", 1), candidate.reason)
            self.assertTrue(context.cache["worker_delivery_orders"]["ok"])

    def test_reason_qualifiers_never_become_plain_status_counts(self):
        for text in ("Tháng 4/2026 có mấy đơn bị huỷ vì giao trễ?",
                     "Tháng 4/2026 có mấy đơn khách trả lại vì gọi nhầm món?"):
            self.assertIsNone(plan(text))

    def test_expiry_and_stock_value_use_end_of_day_sources(self):
        with TemporaryDirectory() as folder:
            shop = Path(folder) / "shop"
            shop.mkdir()
            (shop / "README.md").write_text(
                "## Cột trong `products.csv`\n- **Code**: mã hàng\n- **Name**: tên\n"
                "- **Unit**: đơn vị cơ bản\n- **Cost**: giá vốn một đơn vị cơ bản\n"
                "- **Stock**: tồn kho\n"
                "## Cột trong `lots.csv`\n- **Lot**: số lô\n- **Code**: mã hàng\n"
                "- **Expiry**: hạn sử dụng\n- **Received**: số lượng nhập (đơn vị cơ bản)\n"
                "## Cột trong `taken.csv`\n- **Order**: mã đơn\n- **Lot**: số lô\n"
                "- **Sold**: số lượng bán lấy từ lô (đơn vị cơ bản)\n"
                "- **Returned**: số lượng khách trả lại (đơn vị cơ bản)\n"
                "## Quy ước\n- Dữ liệu có đến hết 30/04/2026.\n"
                "- **Lô, hạn dùng**: mỗi dòng nhập là một lô, số lô không trùng. "
                "Hàng khách trả về lại lô đã bán.\n"
                "- **Tồn kho**: **Stock** là tồn cuối ngày cuối có dữ liệu, theo đơn vị cơ bản.\n",
                encoding="utf-8")
            (shop / "products.csv").write_text("Code,Name,Unit,Cost,Stock\nP9,Thuốc X,viên,4,8\n"
                                               "P1,Thuốc Y,viên,5,2\n",
                                               encoding="utf-8")
            (shop / "lots.csv").write_text(
                "Lot,Code,Expiry,Received\nA,P9,29/04/2026,2\nB,P9,30/04/2026,5\n"
                "C,P9,01/05/2026,2\nD,P1,02/05/2026,3\nE,P9,03/05/2026,2\n", encoding="utf-8")
            (shop / "taken.csv").write_text("Order,Lot,Sold,Returned\nA,B,5,1\nB,C,2,0\n",
                                            encoding="utf-8")
            catalog = build_catalog(Path(folder), "shop")
            cases = [
                ("Lô Thuốc X gần hết hạn nhất có hạn dùng ngày nào?", "date", "2026-04-30"),
                ("Lô Thuốc Y gần hết hạn nhất có hạn dùng ngày nào?", "date", "2026-05-02"),
                ("Có mấy lô hàng còn tồn sẽ hết hạn trong 2 ngày tới?", "number", 2),
                ("Có mấy mặt hàng còn lô tồn sắp hết hạn trong 2 ngày tới?", "number", 2),
                ("Hàng nào hết hạn trong 2 ngày tới mà hiện vẫn còn tồn kho?", "list",
                 ["Thuốc X", "Thuốc Y"]),
                ("Tổng giá trị hàng tồn kho hôm nay là bao nhiêu?", "number", 42),
            ]
            for text, kind, expected in cases:
                with self.subTest(text=text):
                    question = plan(text, ("Thuốc X", "Thuốc Y"))
                    self.assertIsNotNone(question)
                    result = check_executed(answer_plan({}, catalog, question, RULEBOOK), question)
                    self.assertEqual((result.type, result.answer), (kind, expected), result.reason)

    def test_lot_value_uses_the_joined_receipt_price_and_remaining_base_units(self):
        with TemporaryDirectory() as folder:
            shop = Path(folder) / "shop"
            shop.mkdir()
            (shop / "README.md").write_text(
                "`lo_hang`, `chi_tiet_don_lo`, `san_pham`, `chi_tiet_nhap`\n"
                "- Dữ liệu có đến hết 30/04/2026.\n"
                "- **Lô, hạn dùng**: mỗi dòng nhập là một lô, số lô không trùng. "
                "Hàng khách trả về lại lô đã bán.\n"
                "- **Nhập hàng**: `chi_tiet_nhap.DonGiaNhap` là giá nhập một đơn vị, "
                "theo đơn vị nhập của dòng.\n", encoding="utf-8")
            with closing(sqlite3.connect(shop / "export.sqlite")) as db:
                db.executescript("""
                    CREATE TABLE lo_hang (MaLo, MaHangHoa, HanSuDung, SLNhapQuyDoi, MaPhieuNhap);
                    INSERT INTO lo_hang VALUES ('L1', 'P1', '2026-05-02', 20, 'PN1');
                    CREATE TABLE chi_tiet_don_lo (MaHoaDon, MaLo, SLQuyDoi, SLTraQuyDoi);
                    INSERT INTO chi_tiet_don_lo VALUES ('O1', 'L1', 6, 1);
                    CREATE TABLE san_pham (MaHangHoa, TenGoi, DonViCoSo);
                    INSERT INTO san_pham VALUES ('P1', 'Thuốc X', 'viên');
                    CREATE TABLE chi_tiet_nhap (MaLo, MaHangHoa, MaPhieuNhap, SL, DonGiaNhap);
                    INSERT INTO chi_tiet_nhap VALUES ('L1', 'P1', 'PN1', 2, 70);
                """)
            catalog = build_catalog(Path(folder), "shop")
            question = plan("Tiền vốn đang nằm ở các lô còn tồn có hạn dùng từ 30/04/2026 "
                            "đến hết 02/05/2026 là bao nhiêu?")
            result = check_executed(answer_plan({}, catalog, question, RULEBOOK), question)
            self.assertEqual((result.type, result.answer), ("number", 105), result.reason)


if __name__ == "__main__":
    unittest.main()
