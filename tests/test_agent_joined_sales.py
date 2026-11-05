import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import datamate.agent.sales_source as source_module
import datamate.io as io_module
from datamate.agent import compute
from datamate.agent.baseline import BaselineAgent
from datamate.contracts import BusinessContext, Question, Split
from datamate.ingestion.catalog import build_catalog
from datamate.pipeline import Config, predict
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules


RULEBOOK = """# Synthetic current-split rules
| Luật | Mặc định |
|---|---|
| Đơn tính vào doanh thu | đơn không huỷ và không trả hàng; có trả thì bỏ cả đơn. Đơn đang giao vẫn tính |
| Doanh thu một đơn | tổng thành tiền các dòng hàng, đã trừ giảm giá; không trừ voucher, không cộng thuế VAT, không gồm phí giao hàng |
| Tháng này | từ ngày 1 đến hết hôm nay |
| Tuần | bắt đầu thứ Hai và kết thúc Chủ nhật |
| Bán chạy | theo số lượng quy về đơn vị cơ bản |
| Mặt hàng | Phí phục vụ không phải mặt hàng, không xếp hạng. Vẫn tính vào doanh thu |
| Trạng thái đơn | mỗi đơn là hoàn thành, huỷ, trả hàng hoặc đang giao |
| Hai dòng của cùng một đơn giống nhau ở mọi ô | lỗi xuất file, tính một lần |
"""

ORDER_HEADER = [
    "Hình thức thanh toán", "Dư nợ", "Thanh toán", "Tình trạng đơn",
    "Số phiếu", "Mã khách mua", "Phải thu", "Ngày bán",
]
DETAIL_HEADER = [
    "Tiền bán", "TT dòng", "S.L", "Mã hàng bán", "Quy ra ĐV lẻ",
    "Số CT", "Giảm giá", "Giá 1 đv", "ĐV tính",
]
PRODUCT_HEADER = [
    "Bảo hành", "Giá hiện tại", "Mã vạch EAN", "Mã HH Liên quan",
    "Đơn vị tính", "Tên sản phẩm", "Vị trí kệ", "Nhom hang", "Mã HH",
]


class JoinedSalesAgentTests(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()

    @staticmethod
    def _write_csv(path, header, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)

    def _write_joined(self, business="shop", *, orders=None, details=None, products=None):
        target = self.data / business
        target.mkdir(parents=True, exist_ok=True)
        orders = orders if orders is not None else [
            ["cash", "0", "25", "completed", "O1", "", "25", "2026-01-01"],
            ["cash", "0", "100", "cancelled", "O2", "", "100", "2026-01-01"],
            ["cash", "0", "100", "returned", "O3", "", "100", "2026-01-01"],
            ["cod", "0", "40", "shipping", "O4", "", "40", "2026-01-01"],
        ]
        details = details if details is not None else [
            ["20", "1", "2", "P1", "2", "O1", "0", "10", "ly"],
            ["5", "2", "1", "FEE", "1", "O1", "0", "5", "lần"],
            ["100", "1", "10", "P1", "10", "O2", "0", "10", "ly"],
            ["100", "1", "10", "P1", "10", "O3", "0", "10", "ly"],
            ["40", "1", "4", "P2", "4", "O4", "0", "10", "ly"],
        ]
        products = products if products is not None else [
            ["", "10", "", "", "ly", "Cà phê", "", "Đồ uống", "P1"],
            ["", "5", "", "", "lần", "Phí phục vụ", "", "Phí", "FEE"],
            ["", "10", "", "", "ly", "Trà", "", "Đồ uống", "P2"],
        ]
        self._write_csv(target / "orders.csv", ORDER_HEADER, orders)
        self._write_csv(target / "details.csv", DETAIL_HEADER, details)
        self._write_csv(target / "products.csv", PRODUCT_HEADER, products)
        (target / "README.md").write_text(
            "Khoảng dữ liệu: 2026-01-01 đến 2026-01-31", encoding="utf-8"
        )
        return target

    def _context(self, business="shop", rulebook=RULEBOOK):
        split = Split(self.root, (), rulebook, self.root / "calendar.csv")
        return BusinessContext(split, build_catalog(self.data, business))

    @staticmethod
    def _question(text, business="shop", identifier="q"):
        return Question(identifier, business, text, date(2026, 1, 31))

    def _agent(self):
        agent = BaselineAgent()
        self.addCleanup(agent.close)
        return agent

    def _answer_in_process(self, question, context, cache=None):
        """Run the worker task in this process so adapter patches apply."""
        plan = plan_question(question, read_rules(context.split.rulebook))
        self.assertIsNotNone(plan)
        return compute.answer_plan({} if cache is None else cache, context.catalog, plan,
                                   context.split.rulebook)

    def test_joined_revenue_order_count_ranking_statuses_and_service_fee(self):
        self._write_joined()
        context = self._context()
        agent = self._agent()

        revenue = agent.answer(self._question("Doanh thu ngày 01/01/2026"), context)
        orders = agent.answer(self._question("Có bao nhiêu đơn ngày 01/01/2026"), context)
        ranking = agent.answer(
            self._question("Mặt hàng bán chạy nhất ngày 01/01/2026"), context
        )

        self.assertEqual((revenue.type, revenue.answer), ("number", 65))
        self.assertEqual((orders.type, orders.answer), ("number", 2))
        self.assertEqual((ranking.type, ranking.answer), ("string", "Trà"))
        self.assertEqual(revenue.evidence["adapter"], "joined_sales")
        self.assertEqual(revenue.evidence["status_counts"], {
            "cancelled": 1, "returned": 1, "valid": 2,
        })
        self.assertEqual(revenue.evidence["service_fee"]["rows"], 1)
        self.assertEqual(ranking.evidence["selected_rows"], 3)
        self.assertEqual(ranking.evidence["ranking_rows"], 2)
        self.assertEqual(ranking.evidence["inputs"]["ranking_eligible_rows"], 4)
        self.assertIn("ranking_candidates(sales, eligible)", ranking.evidence["code"]["call"])
        self.assertEqual(revenue.evidence["execution"]["status"], "ok")
        self.assertNotIn("lineage", revenue.evidence)
        json.dumps(revenue.evidence, ensure_ascii=False, allow_nan=False)

    def test_flat_schema_keeps_existing_baseline_behavior(self):
        target = self.data / "flat"
        self._write_csv(
            target / "sales.csv",
            ["order_id", "order_date", "status", "line_amount", "product_id",
             "product_name", "quantity_base"],
            [["F1", "2026-01-01", "completed", "10", "P", "Flat product", "3"]],
        )
        context = self._context("flat")
        result = self._agent().answer(
            self._question("Doanh thu ngày 01/01/2026", "flat"), context
        )
        self.assertEqual((result.type, result.answer), ("number", 10))
        self.assertEqual(result.evidence["adapter"], "flat_sales")

    def test_partial_or_ambiguous_joined_schema_abstains_without_flat_fallback(self):
        self._write_joined()
        self._write_csv(self.data / "shop" / "orders-copy.csv", ORDER_HEADER, [])
        with patch.object(source_module, "load_sales",
                          side_effect=AssertionError("must not fall back")):
            result = self._answer_in_process(
                self._question("Doanh thu ngày 01/01/2026"), self._context()
            )
        self.assertEqual(result.type, "abstain")
        self.assertIn("AMBIGUOUS_JOINED_MAPPING", result.reason)
        through_worker = self._agent().answer(
            self._question("Doanh thu ngày 01/01/2026"), self._context()
        )
        self.assertEqual(through_worker.type, "abstain")
        self.assertIn("AMBIGUOUS_JOINED_MAPPING", through_worker.reason)

        (self.data / "shop" / "orders-copy.csv").unlink()
        (self.data / "shop" / "products.csv").unlink()
        with patch.object(source_module, "load_sales",
                          side_effect=AssertionError("must not fall back")):
            partial = self._answer_in_process(
                self._question("Doanh thu ngày 01/01/2026"), self._context()
            )
        self.assertEqual(partial.type, "abstain")
        self.assertIn("AMBIGUOUS_JOINED_MAPPING", partial.reason)

        unsupported = self.data / "unsupported"
        self._write_csv(unsupported / "unknown.csv", ["a", "b"], [["1", "2"]])
        result = self._agent().answer(
            self._question("Doanh thu ngày 01/01/2026", "unsupported"),
            self._context("unsupported"),
        )
        self.assertEqual(result.type, "abstain")
        self.assertIn("No supported flat sales table", result.reason)

    def test_orphan_and_duplicate_parent_are_abstains(self):
        cases = {
            "orphan": {
                "details": [["20", "1", "2", "P1", "2", "MISSING", "0", "10", "ly"]],
                "expected": "ORPHAN_FK",
            },
            "duplicate": {
                "orders": [
                    ["cash", "0", "20", "completed", "O1", "", "20", "2026-01-01"],
                    ["cash", "0", "20", "completed", "O1", "", "20", "2026-01-01"],
                ],
                "details": [["20", "1", "2", "P1", "2", "O1", "0", "10", "ly"]],
                "expected": "DUPLICATE_PARENT_KEY",
            },
        }
        for business, case in cases.items():
            with self.subTest(business=business):
                self._write_joined(
                    business, orders=case.get("orders"), details=case["details"]
                )
                with patch.object(source_module, "load_sales",
                                  side_effect=AssertionError("must not fall back")):
                    result = self._answer_in_process(
                        self._question("Doanh thu ngày 01/01/2026", business),
                        self._context(business),
                    )
                self.assertEqual(result.type, "abstain")
                self.assertIn(case["expected"], result.reason)

    def test_changed_rulebook_disables_joined_capability(self):
        self._write_joined()
        changed = RULEBOOK.replace(
            "| Mặt hàng | Phí phục vụ không phải mặt hàng, không xếp hạng. Vẫn tính vào doanh thu |\n",
            "",
        )
        result = self._agent().answer(
            self._question("Doanh thu ngày 01/01/2026"), self._context(rulebook=changed)
        )
        self.assertEqual(result.type, "abstain")
        self.assertIn("RULE_CAPABILITY_MISSING", result.reason)

    def test_worker_cache_reloads_when_the_split_rulebook_changes(self):
        self._write_joined()
        context = self._context()
        changed = RULEBOOK.replace(
            "| Mặt hàng | Phí phục vụ không phải mặt hàng, không xếp hạng. Vẫn tính vào doanh thu |\n",
            "",
        )
        cache = {}
        self.assertTrue(compute.prepare_sales(cache, context.catalog, RULEBOOK)["ok"])
        missing = compute.prepare_sales(cache, context.catalog, changed)
        self.assertFalse(missing["ok"])
        self.assertIn("RULE_CAPABILITY_MISSING", missing["reason"])

    def test_failure_is_cached_per_context_and_does_not_stop_next_business(self):
        self._write_joined(
            "bad",
            details=[["20", "1", "2", "P1", "2", "MISSING", "0", "10", "ly"]],
        )
        self._write_joined("good")
        agent = self._agent()
        bad_context = self._context("bad")
        good_context = self._context("good")
        # The adapter runs in the worker process: count the parent's ingestion requests.
        with patch.object(agent.worker, "run", wraps=agent.worker.run) as run:
            first = agent.answer(
                self._question("Doanh thu ngày 01/01/2026", "bad", "bad-1"), bad_context
            )
            second = agent.answer(
                self._question("Có bao nhiêu đơn ngày 01/01/2026", "bad", "bad-2"), bad_context
            )
            good = agent.answer(
                self._question("Doanh thu ngày 01/01/2026", "good", "good-1"), good_context
            )
        self.assertEqual((first.type, second.type), ("abstain", "abstain"))
        self.assertIn("ORPHAN_FK", second.reason)
        self.assertEqual((good.type, good.answer), ("number", 65))
        prepares = [call for call in run.call_args_list if call.args[0] is agent.prepare]
        self.assertEqual(len(prepares), 2)
        self.assertIsNot(bad_context.cache["rules"], good_context.cache["rules"])

        cache = {}
        with patch.object(source_module, "load_joined_sales",
                          wraps=source_module.load_joined_sales) as loader:
            for _ in range(2):
                self.assertFalse(
                    compute.prepare_sales(cache, bad_context.catalog, RULEBOOK)["ok"])
            self.assertTrue(compute.prepare_sales(cache, good_context.catalog, RULEBOOK)["ok"])
        self.assertEqual(loader.call_count, 2)

    def test_success_is_read_once_per_business_context(self):
        self._write_joined()
        context = self._context()
        questions = [
            self._question("Doanh thu ngày 01/01/2026", identifier="q1"),
            self._question("Có bao nhiêu đơn ngày 01/01/2026", identifier="q2"),
            self._question("Mặt hàng bán chạy nhất ngày 01/01/2026", identifier="q3"),
        ]
        agent = self._agent()
        with patch.object(agent.worker, "run", wraps=agent.worker.run) as run:
            answers = [agent.answer(question, context) for question in questions]
        self.assertEqual([a.answer for a in answers], [65, 2, "Trà"])
        prepares = [call for call in run.call_args_list if call.args[0] is agent.prepare]
        self.assertEqual(len(prepares), 1)

        cache = {}
        with patch.object(source_module, "load_joined_sales",
                          wraps=source_module.load_joined_sales) as loader:
            in_process = [self._answer_in_process(q, context, cache) for q in questions]
        self.assertEqual([a.answer for a in in_process], [65, 2, "Trà"])
        self.assertEqual(loader.call_count, 1)

    def test_pipeline_inference_never_reads_reference_answers(self):
        self._write_joined()
        (self.root / "rulebook.md").write_text(RULEBOOK, encoding="utf-8")
        (self.root / "calendar.csv").write_text("date,lunar\n", encoding="utf-8")
        (self.root / "questions.jsonl").write_text(json.dumps({
            "id": "q1", "business_id": "shop",
            "question": "Doanh thu ngày 01/01/2026", "as_of_date": "2026-01-31",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.root / "answers.jsonl").write_text("not valid json and must stay unread\n",
                                                   encoding="utf-8")
        output = self.root.parent / (self.root.name + "-output")
        self.addCleanup(lambda: __import__("shutil").rmtree(output, ignore_errors=True))
        original = io_module.read_jsonl
        seen = []

        def guarded(path):
            seen.append(Path(path).name)
            if Path(path).name == "answers.jsonl":
                raise AssertionError("inference read reference answers")
            return original(path)

        with patch.object(io_module, "read_jsonl", side_effect=guarded):
            report = predict(self.root, output, Config())
        self.assertEqual(report["answer_types"], {"number": 1})
        self.assertNotIn("answers.jsonl", seen)
        prediction = json.loads((output / "submission.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(prediction, {"id": "q1", "type": "number", "answer": 65})


if __name__ == "__main__":
    unittest.main()
