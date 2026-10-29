# Model manifest

Starter hiện tại không dùng model, không có weights hoặc bước training.
Baseline là grammar và phép tính Python xác định; tổng số tham số model = 0.

Trước khi thêm model, cập nhật README chính và ghi cho mỗi thành phần:

- tên/revision và URL nguồn;
- ngày phát hành weights (phải trước 01/10/2026 theo thể lệ đã đọc);
- licence và số tham số đầy đủ;
- định dạng/quantization, checksum weights;
- cách tải trong giai đoạn chuẩn bị image, đường dẫn offline khi inference;
- dữ liệu, code và cấu hình training nếu fine-tune.

Tổng model inference (LLM, embedding, reranker, classifier...) không quá 15 tỷ
tham số, kể cả dùng lần lượt. Không tải model trong `predict.sh`.
Weights cần COPY vào image cuối trước khi đóng băng solution; `.gitignore` chỉ
giúp tránh đưa binary vào Git, không tự đưa weights vào Docker.
