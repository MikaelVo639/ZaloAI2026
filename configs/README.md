# Cấu hình dùng chung

- `baseline.toml`: backend deterministic cho pipeline khởi đầu.
- `abstain.toml`: xuất abstain để kiểm tra luồng nộp/đóng gói.
- `local_plan.toml`: prototype AGENT-02, giữ baseline cho câu grammar hiểu được,
  còn lại thử model local đã chuẩn bị. Không chọn làm mặc định khi chưa qua holdout.

`backend` chọn engine; backend mới đăng ký trong `Config` và pipeline.
`min_confidence` là ngưỡng nhận Candidate, chưa phải xác suất đã hiệu chỉnh.
`max_seconds` là ngân sách toàn lượt được kiểm tra giữa các bước, không ngắt cưỡng
bức một lần gọi model/worker. Backend cần timeout riêng cho bước thực thi.

Khi thử nghiệm, copy config sang tên mô tả, truyền `--config configs/<name>.toml`
và ghi các CLI overrides vào báo cáo. Chia sẻ config qua PR; đường dẫn input/output
truyền bằng CLI hoặc volume Docker, không ghi đường dẫn máy vào config.

Các timeout (giây) gồm `question_timeout`, `ingestion_timeout`, `model_load_timeout`,
`model_timeout`; worker tính từ lúc bắt đầu gọi, bao gồm spawn và IPC. Model dùng
worker riêng để cache weights không bị xoá khi đổi business. `max_input_tokens` và
`max_new_tokens` giới hạn context/output; vượt ngân sách thì abstain, không cắt luật.
`model_path` phải là thư mục snapshot local có `model_manifest.json`; đường dẫn trong
config được tính từ working directory. Bản image dùng config riêng trỏ tới thư mục
weights đã COPY vào image, không trỏ tới `/data`.

Confidence của Plan do model là hằng số chẩn đoán **0.5**, không lấy từ model và chưa
hiệu chỉnh. Config thử nghiệm nhận mức này để Evaluation có thể đo sai/đúng; ngưỡng
1.0 mặc định chỉ giữ đáp án baseline. Việc bật ngưỡng 0.5 không chứng minh an toàn
ngữ nghĩa; xem giới hạn và bàn giao ở [AGENT_02.md](../docs/AGENT_02.md).

Sau khi có GPU/weights đã chuẩn bị, dùng `scripts/run_agent02_acceptance.py` để
chạy baseline, model và model repeat trên cùng input/holdout. `--check-environment`
kiểm tra readiness mà không tải gì. Bộ chạy ghi cấu hình thực tế vào artifact;
`--private-questions` và `--private-businesses` chỉ tạo ước tính theo workload tương
đương, không phải đo private test. Xem hướng dẫn trong `docs/AGENT_02.md`.
