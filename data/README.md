# Dữ liệu local

- `archives/`: ZIP gốc, không commit.
- `raw/train/`, `raw/public_test/`: dữ liệu BTC sau `datamate import-data`.
- `demo/`: fixture tổng hợp do `datamate demo` sinh ra, không phải dữ liệu BTC.

Giữ file gốc không đổi. Cache và kết quả để ngoài split; inference chỉ đọc dữ liệu.
Mọi thư mục trong đây, trừ README này, được Git bỏ qua.
