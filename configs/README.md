# Cấu hình dùng chung

- `baseline.toml`: backend deterministic cho pipeline khởi đầu.
- `abstain.toml`: xuất abstain để kiểm tra luồng nộp/đóng gói.

`backend` chọn engine; backend mới đăng ký trong `Config` và pipeline.
`min_confidence` là ngưỡng nhận Candidate, chưa phải xác suất đã hiệu chỉnh.
`max_seconds` là ngân sách toàn lượt được kiểm tra giữa các bước, không ngắt cưỡng
bức một lần gọi model/worker. Backend cần timeout riêng cho bước thực thi.

Khi thử nghiệm, copy config sang tên mô tả, truyền `--config configs/<name>.toml`
và ghi các CLI overrides vào báo cáo. Chia sẻ config qua PR; đường dẫn input/output
truyền bằng CLI hoặc volume Docker, không ghi đường dẫn máy vào config.
