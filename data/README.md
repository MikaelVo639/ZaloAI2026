# Dữ liệu

Cấu trúc thống nhất sau khi thành viên tự chuẩn bị dữ liệu:

```text
data/
  archives/train.zip
  archives/public_test.zip
  raw/train/
  raw/public_test/
  demo/
```

Lưu ZIP từ portal vào `archives/`, rồi chạy `datamate import-data` theo README.
`raw/` giữ nguyên cấu trúc BTC; không sửa file gốc. `demo/` do smoke command tự tạo,
không phải dữ liệu thi. Cache và kết quả đặt ngoài split.

Dữ liệu không đi kèm bản clone/ZIP source. Mỗi thành viên chuẩn bị dữ liệu theo
quyền truy cập của mình, rồi đối chiếu checksum gói dữ liệu khi so kết quả. Mọi
thư mục trong đây, trừ README này, được Git bỏ qua.
