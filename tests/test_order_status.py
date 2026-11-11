"""DATA-03 (07/11–08/11): order states from the split rulebook; DATA-02 XLSX mapping reached by the agent."""

import csv
import tempfile
import unittest
from pathlib import Path

from datamate.agent import sales_source
from datamate.ingestion.catalog import build_catalog
from datamate.ingestion.status import StatusError, order_state, status_policy

RULEBOOK = """# Synthetic split rules
| Luật | Mặc định |
|---|---|
| Đơn tính vào doanh thu | đơn không huỷ và không trả hàng. Đơn có trả hàng, kể cả chỉ trả một phần, là đơn trả hàng: bỏ cả đơn. Đơn đang giao vẫn tính |
| Đơn bị huỷ | đơn có trạng thái huỷ. Đơn trả hàng không phải đơn huỷ |
| Doanh thu một đơn | tổng thành tiền các dòng hàng, đã trừ giảm giá |
| Bán chạy | theo số lượng quy về đơn vị cơ bản |
| Mặt hàng | Phí phục vụ không phải mặt hàng: không xếp hạng, không đếm. Vẫn tính vào doanh thu |
| Ô để trống ở các dòng sau của cùng một nhóm dòng | giống ô đó ở dòng đầu nhóm |
| Trạng thái đơn | mỗi đơn có đúng một trong bốn trạng thái: hoàn thành, huỷ, trả hàng, đang giao |
| Hai dòng của cùng một đơn giống nhau ở mọi ô | lỗi xuất file, tính một lần |
"""

class _Shop(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parent.parent / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"

    def shop(self, readme, files, business="shop"):
        root = self.data / business
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text(readme, encoding="utf-8")
        for name, rows in files.items():
            path = root / name
            if name.endswith(".csv"):
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    csv.writer(stream).writerows(rows)
            else:
                from openpyxl import Workbook

                book = Workbook()
                book.remove(book.active)
                for sheet, sheet_rows in rows.items():
                    worksheet = book.create_sheet(sheet)
                    for row in sheet_rows:
                        worksheet.append(row)
                book.save(path)
        return build_catalog(self.data, business)


class StatusTests(unittest.TestCase):
    def test_labels_map_to_the_four_rulebook_states(self):
        cases = {
            "Hoàn thành": "hoan thanh", "Đã hoàn thành": "hoan thanh", "Xong": "hoan thanh",
            "ok": "hoan thanh", "Thành công": "hoan thanh", "đã lấy": "hoan thanh",
            "Đã hủy": "huy", "Huỷ đơn": "huy", "khách hủy": "huy", "Đã hủy đơn": "huy",
            "Trả hàng": "tra hang", "Đã trả hàng": "tra hang", "Đổi trả": "tra hang",
            "Đã hoàn hàng": "tra hang", "hàng trả về": "tra hang",
            "Đang giao hàng": "dang giao", "Đang vận chuyển": "dang giao",
            "completed": "hoan thanh", "cancelled": "huy", "returned": "tra hang",
            "shipping": "dang giao",
        }
        for label, state in cases.items():
            with self.subTest(label=label):
                self.assertEqual(order_state(label), state)

    def test_unknown_labels_are_not_guessed(self):
        for label in ("Đặt hàng", "Chờ duyệt", "Lưu tạm", "", None, "123"):
            with self.subTest(label=label):
                self.assertIsNone(order_state(label))
        with self.assertRaises(StatusError):
            status_policy(RULEBOOK).resolve("Lưu tạm")

    def test_policy_needs_the_split_rules(self):
        policy = status_policy(RULEBOOK)
        self.assertEqual(policy.resolve("Đang giao"), "valid")
        self.assertEqual(policy.resolve("Đổi trả"), "returned")
        with self.assertRaises(StatusError):
            status_policy(RULEBOOK.replace("| Trạng thái đơn |", "| Ghi chú |"))
        # Without "đang giao vẫn tính" a shipping order has no revenue rule: it raises.
        without_shipping = RULEBOOK.replace(" Đơn đang giao vẫn tính", "")
        with self.assertRaises(StatusError):
            status_policy(without_shipping).resolve("Đang giao")
        self.assertEqual(status_policy(without_shipping).resolve("Xong"), "valid")


XLSX_README = "\n".join((
    "- **Mã hóa đơn**: mã đơn",
    "- **Thành tiền**: thành tiền sau giảm",
    "- **STT**: số thứ tự dòng trong đơn",
    "- **Số lượng quy đổi**: số lượng đã đổi ra đơn vị cơ bản",
    "- **ĐVT**: đơn vị cơ bản",
))


class XlsxMappingSelectionTests(_Shop):
    """07/11: the DATA-02 XLSX order/detail/product mapping is now reached by the agent."""

    def files(self, statuses=("Hoàn thành", "Đã hủy", "Đã trả hàng", "Đang giao hàng")):
        orders = [["Mã hóa đơn", "Thời gian", "Tổng tiền hàng", "Trạng thái"]]
        details = [["Mã hóa đơn", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Giảm giá",
                    "Thành tiền", "Số lượng quy đổi", "Đơn vị tính", "STT"]]
        for number, status in enumerate(statuses, 1):
            orders.append([f"O{number}", f"0{number}/01/2026", "10,000", status])
            details.append([f"O{number}", "P1", "Nước", 1, "10,000", "0", "10,000", 1, "chai", 1])
        products = [["Mã hàng", "Tên hàng", "Nhóm hàng(3 Cấp)", "ĐVT"],
                    ["P1", "Nước", "Đồ uống", "chai"]]
        return {"orders.xlsx": {"Sheet1": orders}, "details.xlsx": {"Sheet1": details},
                "products.xlsx": {"Sheet1": products}}

    def test_xlsx_export_uses_the_joined_adapter_with_split_status_rules(self):
        sales, evidence, eligible = sales_source.load_business_sales(
            self.shop(XLSX_README, self.files()), RULEBOOK)
        self.assertEqual(evidence["adapter"], "joined_sales_xlsx")
        self.assertEqual([s.status for s in sales], ["valid", "cancelled", "returned", "valid"])
        self.assertEqual(evidence["rules"]["status_mapping"]["Đang giao hàng"], "valid")
        self.assertEqual(eligible, frozenset(range(4)))

    def test_unknown_xlsx_status_raises(self):
        catalog = self.shop(XLSX_README, self.files(("Hoàn thành", "Lưu tạm")))
        with self.assertRaises(ValueError) as caught:
            sales_source.load_business_sales(catalog, RULEBOOK)
        self.assertIn("UNKNOWN_STATUS", str(caught.exception))

    def test_xlsx_export_without_readme_evidence_is_left_to_other_adapters(self):
        catalog = self.shop("undocumented export", self.files())
        self.assertIsNone(sales_source._xlsx_mapping(catalog))


if __name__ == "__main__":
    unittest.main()
