"""Synthetic fixture only; never substitutes for organizer data."""

import csv
import sqlite3
from contextlib import closing
from pathlib import Path

from datamate.io import write_jsonl

RULEBOOK = """# Synthetic demo rules (not the organizer's rulebook)
Hôm nay là as_of_date; không tính bản ghi tương lai.
| Luật | Mặc định |
|---|---|
| Đơn tính vào doanh thu | Đơn không huỷ và không trả hàng; trả một phần bỏ cả đơn |
| Doanh thu một đơn | Tổng thành tiền các dòng hàng; không trừ giảm giá cả đơn; không cộng thuế VAT; không gồm phí giao hàng |
| Tháng này | Từ ngày 1 đến hết hôm nay |
| Tuần | Bắt đầu thứ Hai; tuần trước là thứ Hai đến Chủ nhật |
| Bán chạy | Theo số lượng quy về đơn vị cơ bản |
"""
COLUMNS = ("order_id", "order_date", "status", "product_id", "product_name",
           "quantity_base", "line_amount")
ROWS = [
    ("O1", "2026-08-01", "completed", "P1", "Cà phê sữa", 2, 40000),
    ("O1", "2026-08-01", "completed", "P2", "Trà đào", 1, 30000),
    ("O1", "2026-08-01", "completed", "P1", "Cà phê sữa", 2, 40000),  # export duplicate
    ("O2", "2026-08-02", "cancelled", "P2", "Trà đào", 30, 900000),
    ("O3", "2026-08-03", "returned", "P1", "Cà phê sữa", 20, 400000),
    ("O4", "2026-08-27", "completed", "P1", "Cà phê sữa", 3, 60000),
    ("O5", "2026-08-28", "completed", "P2", "Trà đào", 10, 999999),
]


def create_demo(root: Path) -> dict:
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Demo directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "rulebook.md").write_text(RULEBOOK, encoding="utf-8")
    (root / "calendar.csv").write_text("solar_date,lunar_day,lunar_month,lunar_year\n"
                                        "2026-08-27,15,7,2026\n", encoding="utf-8")
    questions, references = [], []
    cases = [
        ("Tổng doanh thu tháng này là bao nhiêu?", "number", 130000),
        ("Số đơn hàng tháng này là bao nhiêu?", "number", 2),
        ("Mặt hàng bán chạy nhất tháng này là gì?", "string", "Cà phê sữa"),
        ("2 mặt hàng bán chạy nhất tháng này là những mặt hàng nào?", "list", ["Cà phê sữa", "Trà đào"]),
        ("Doanh thu 7 ngày gần nhất bao nhiêu?", "number", 60000),
        ("Doanh thu ngày 01/08/2026 bao nhiêu?", "number", 70000),
        ("Doanh thu tháng 1/2024 bao nhiêu?", "unanswerable", None),
        ("Doanh thu tiền mặt tháng này bao nhiêu?", "unanswerable", None),
    ]
    for index, kind in enumerate(("csv", "xlsx", "sqlite"), 1):
        business = f"demo_{kind}"
        directory = root / "data" / business
        directory.mkdir(parents=True)
        (directory / "README.md").write_text(
            "Dữ liệu tự tạo để smoke test. Mỗi dòng là một dòng hàng; "
            "quantity_base là số lượng đơn vị cơ bản; line_amount đã giảm giá dòng. "
            "Không có thông tin phương thức thanh toán.\n"
            "Khoảng dữ liệu: 2026-08-01 đến 2026-08-28.\n", encoding="utf-8")
        if kind == "csv":
            with (directory / "sales.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(COLUMNS)
                writer.writerows(ROWS)
        elif kind == "xlsx":
            from openpyxl import Workbook

            book = Workbook()
            sheet = book.active
            sheet.title = "Sales 2026"
            sheet.append(COLUMNS)
            for row in ROWS:
                sheet.append(row)
            book.save(directory / "sales.xlsx")
            book.close()
        else:
            with closing(sqlite3.connect(directory / "sales.sqlite")) as connection:
                connection.execute("CREATE TABLE sales (order_id TEXT, order_date TEXT, "
                                   "status TEXT, product_id TEXT, product_name TEXT, "
                                   "quantity_base INTEGER, line_amount INTEGER)")
                connection.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?,?)", ROWS)
                connection.commit()
        for case, (text, answer_type, value) in enumerate(cases, 1):
            identifier = f"demo_{index}_{case}"
            questions.append({"id": identifier, "business_id": business, "question": text,
                              "as_of_date": "2026-08-27"})
            references.append({"id": identifier, "type": answer_type, "answer": value})
    write_jsonl(root / "questions.jsonl", questions)
    write_jsonl(root / "answers.jsonl", references)
    return {"data_dir": str(root.resolve()), "businesses": 3, "questions": len(questions)}
