"""Đợt 3, PR dùng chung: Sale.payment_method read from the column the README describes.

Synthetic shop; the value is kept as written so the payment grammar can match it through the
README's own label description.
"""

import unittest

from test_sale_context_fields import RULEBOOK, _Shop

from datamate.ingestion.readme_sales import load_readme_sales

README = """# Dữ liệu shop

## Cột trong `don.csv`
- **Mã CT**: mã đơn
- **Ngày**: ngày bán
- **Xử lý**: trạng thái đơn
- **Trả bằng**: hình thức thanh toán của đơn: cash tiền mặt, transfer chuyển khoản, cod giao hàng thu tiền, debt ghi nợ

## Cột trong `chi_tiet.csv`
- **Mã CT**: mã đơn
- **Mã SP**: mã hàng
- **SL**: số lượng
- **ĐVT**: đơn vị tính
- **Tiền**: thành tiền sau giảm

## Cột trong `san_pham.csv`
- **Mã SP**: mã hàng
- **Tên SP**: tên
- **ĐV**: đơn vị cơ bản
"""
ORDERS = [["Mã CT", "Ngày", "Xử lý", "Trả bằng"],
          ["D1", "01/03/2026", "Hoàn thành", "cash"],
          ["D2", "01/03/2026", "Hoàn thành", " transfer "],
          ["D3", "02/03/2026", "Hoàn thành", ""]]
LINES = [["Mã CT", "Mã SP", "SL", "ĐVT", "Tiền"],
         ["D1", "A", "1", "cái", "10000"],
         ["D1", "B", "2", "cái", "6000"],
         ["D2", "A", "1", "cái", "10000"],
         ["D3", "B", "1", "cái", "3000"]]
PRODUCTS = [["Mã SP", "Tên SP", "ĐV"], ["A", "Áo", "cái"], ["B", "Bút", "cái"]]


class PaymentFieldTests(_Shop):
    def load(self, readme=README, orders=ORDERS):
        catalog = self.shop(readme, {"don.csv": orders, "chi_tiet.csv": LINES,
                                     "san_pham.csv": PRODUCTS})
        sales, _evidence, _eligible = load_readme_sales(catalog, RULEBOOK)
        return sales

    def test_payment_method_is_copied_to_every_line_of_the_order(self):
        sales = self.load()
        self.assertEqual([(s.order_id, s.payment_method) for s in sales],
                         [("D1", "cash"), ("D1", "cash"), ("D2", "transfer"), ("D3", None)])

    def test_no_payment_column_leaves_the_field_empty(self):
        readme = README.replace(
            "- **Trả bằng**: hình thức thanh toán của đơn: cash tiền mặt, transfer chuyển khoản, "
            "cod giao hàng thu tiền, debt ghi nợ\n", "")
        orders = [row[:3] for row in ORDERS]
        self.assertTrue(all(s.payment_method is None for s in self.load(readme, orders)))

    def test_other_fields_are_unchanged(self):
        sales = self.load()
        self.assertEqual([s.amount for s in sales], [10000, 6000, 10000, 3000])
        self.assertTrue(all(s.channel is None for s in sales))


if __name__ == "__main__":
    unittest.main()
