"""D01 + C01 §6.3a: orders without a channel column, and README label lists after a colon.

Synthetic shops only. A shop that never mentions sales channels and whose order table has
no channel-like column cannot hold marketplace orders: COD is still excluded from the
payment method. Any channel or marketplace word keeps the source ``partial``.
"""

import unittest

from test_ingestion_debt import README, RULEBOOK, _Shop

from datamate.ingestion.debt import DebtSourceError, load_debt_ledger

NO_CHANNEL_README = README.replace("- **Kênh**: kênh bán\n", "")
ORDERS = [
    ["Mã", "Ngày", "Trạng thái", "Tổng thu", "Thanh toán", "Mã khách", "Tên khách", "Còn nợ"],
    ["D1", "01/01/2026", "Xong", "100000", "Ghi nợ", "K1", "An", "25000"],
    ["D2", "02/01/2026", "Xong", "50000", "chuyển khoản", "K2", "Bình", "0"],
    ["D4", "04/01/2026", "Đang giao", "70000", "COD", "", "Khách lẻ", ""],
]
PAYMENTS = [
    ["Mã đơn", "Ngày thu", "Tiền thu"],
    ["D1", "01/01/2026", "25000"],
    ["D1", "15/01/2026", "50000"],
    ["D2", "02/01/2026", "50000"],
]


class OrdersWithoutChannelTests(_Shop):
    def load(self, readme=NO_CHANNEL_README, orders=ORDERS):
        return load_debt_ledger(self.catalog(readme=readme, orders=orders, payments=PAYMENTS),
                                RULEBOOK)

    def test_unrecorded_channel_excludes_only_cod(self):
        ledger = self.load()
        self.assertEqual([order.excluded for order in ledger.orders], ["", "", "cod"])
        self.assertEqual(ledger.lineage["checks"]["channel_column"], "absent")
        self.assertFalse(any(label.startswith("channel:") for label in ledger.lineage["labels"]))
        self.assertEqual(ledger.lineage["checks"]["closing_debt_checked"], 2)

    def test_recorded_channel_is_reported(self):
        ledger = load_debt_ledger(self.catalog(), RULEBOOK)
        self.assertEqual(ledger.lineage["checks"]["channel_column"], "recorded")

    def test_any_channel_word_in_the_readme_keeps_the_source_partial(self):
        for extra in ("- Một số đơn bán qua Shopee.\n", "- Đơn bán trên sàn TMĐT ghi riêng.\n",
                      "- Shop có kênh bán online.\n"):
            with self.subTest(extra=extra):
                with self.assertRaises(DebtSourceError) as raised:
                    self.load(readme=NO_CHANNEL_README + extra)
                self.assertEqual(raised.exception.code, "partial")

    def test_undocumented_channel_like_header_keeps_the_source_partial(self):
        neutral = [row + [value] for row, value in zip(ORDERS, ["Ghi chú", "", "", ""])]
        self.assertEqual(self.load(orders=neutral).lineage["checks"]["channel_column"], "absent")
        for header in ("Kênh", "Nguồn đơn", "Sàn", "Channel"):
            with self.subTest(header=header):
                orders = [row + [value] for row, value in zip(
                    ORDERS, [header, "store", "store", "store"])]
                with self.assertRaises(DebtSourceError) as raised:
                    self.load(orders=orders)
                self.assertIn(raised.exception.code, {"partial", "column_missing"})

    def test_payment_method_is_still_required(self):
        readme = NO_CHANNEL_README.replace("- **Thanh toán**: hình thức thanh toán\n", "")
        with self.assertRaises(DebtSourceError) as raised:
            self.load(readme=readme)
        self.assertEqual(raised.exception.code, "partial")

    def test_unknown_payment_label_is_still_rejected(self):
        orders = [list(row) for row in ORDERS]
        orders[2][4] = "ShopeePay"
        with self.assertRaises(DebtSourceError) as raised:
            self.load(orders=orders)
        self.assertEqual(raised.exception.code, "label")


class ReadmeMeaningWithLabelListTests(_Shop):
    def test_payment_meaning_followed_by_its_labels(self):
        readme = README.replace(
            "- **Thanh toán**: hình thức thanh toán\n",
            "- **Thanh toán**: hình thức thanh toán của đơn: cash tiền mặt, transfer chuyển "
            "khoản, cod giao hàng thu tiền, debt ghi nợ\n")
        ledger = load_debt_ledger(self.catalog(readme=readme), RULEBOOK)
        self.assertEqual(ledger.lineage["columns"]["orders"][0]["fields"]["payment_method"],
                         "Thanh toán")

    def test_customer_list_name_column_described_as_ten(self):
        readme = README.replace("- **Khách hàng**: tên khách\n", "- **Khách hàng**: tên\n")
        ledger = load_debt_ledger(self.catalog(readme=readme), RULEBOOK)
        self.assertEqual(ledger.orders[0].customer_name, "An")
        self.assertEqual(ledger.lineage["columns"]["customers"][0]["fields"]["customer_name"],
                         "Khách hàng")


if __name__ == "__main__":
    unittest.main()
