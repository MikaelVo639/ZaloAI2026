# Ghi nhận từ portal — kiểm tra lại 05/11/2026 (GMT+7)

Nguồn: [DataMate portal, các mục Data / Evaluation / Rule / Submission Guidance](https://challenge.zalo.ai/portal/datamate).
Docker: [DataMate Docker Submission Guide](https://dl-challenge.zalo.ai/2026/datamate_dkewxftdzo/zac2026_DataMate_docker_guideline_final.pdf).
Mốc đối chiếu nội dung: 05/11/2026. Portal và guideline BTC là nguồn chuẩn khi có cập nhật.
Cập nhật riêng mục Rule ngày 04/11/2026: hạn phát hành weights là trước 26/10/2026.

- Input mỗi split: `data/<business_id>/`, README từng business, `questions.jsonl`,
  `rulebook.md`, `calendar.csv`. Train thêm `answers.jsonl` với alias.
- Mỗi câu có `id`, `business_id`, câu hỏi tiếng Việt, `as_of_date` làm mốc “hôm nay”.
- Output JSONL có `id`, `type`, `answer`; type gồm number, percent, string, date,
  boolean, list, table, unanswerable, abstain. Hai type cuối không cần answer.
- PA = (số đúng − số sai) / tổng câu; abstain và thiếu dòng tính 0. Cần đúng cả type.
- String chuẩn hóa NFC/hoa thường/khoảng trắng/dấu câu; giữ dấu tiếng Việt, chấp nhận
  alias có trong dữ liệu và mã. List có thứ tự. Table so cột theo tên, fallback vị trí;
  dòng là tập không có thứ tự. Local scorer trong repo chưa phải scorer BTC.
- Phải đọc rulebook của chính split đang chạy. Không lấy bản train làm mặc định cho test.
- Inference hoàn toàn offline trong Docker, networking bị tắt, không gọi API, không tải weights.
- Model phải phát hành weights công khai **trước 26/10/2026**, licence cho phép sử dụng.
  Tổng tham số của tất cả model dùng trong inference <= **15 tỷ**, kể cả model dùng lần lượt;
  MoE tính toàn bộ tham số. Khai báo model trong README. Starter hiện dùng 0 model.
- Không có người can thiệp, không hard-code logic theo question/business id; không lấy đáp
  án test hoặc dùng public/private test để training.
- `/data` mount read-only; ghi `/result/submission.jsonl` và log theo câu ở `/result/logs/`.
  Chạy `bash /code/predict.sh` không có tham số. Mỗi log cần `id`, `answer`,
  `elapsed_ms` (số nguyên không âm, gồm thời gian đọc dữ liệu), `thinking`
  (null nếu không sinh reasoning); câu tính bằng code cần thêm `code` và `output`.
- Máy tham chiếu: 2 x RTX 5090, mỗi GPU 32 GB VRAM; RAM 512 GB; tối đa 6 giờ toàn bộ private
  test tính cả load model. Blackwell cần CUDA >=12.8 và build PyTorch/vLLM phù hợp.
  Base image GPU cuối theo Docker guideline của BTC, không coi Docker CPU starter là bản cuối.
- Kết quả chạy lại Docker là điểm cuối. Sai lệch PA giữa upload và chạy lại tối đa 0.03;
  log dùng để giải trình. Nếu dừng sớm, các đáp án đã ghi mới được tính.
- Public: tối đa 5 submission/ngày. Hạn solution **23:59 ngày 16/11/2026 GMT+7**;
  mỗi team tối đa 2 lần nộp solution, BTC lấy lần cuối. Image lớn nộp qua link Drive công khai
  kèm MD5 của `.tar.gz` và kích thước `.tar` chưa nén. File `.tar` phải không quá 100 GB.
- Private test mở **09:00 ngày 20/11/2026 GMT+7**; hạn kết quả **23:59 ngày 23/11/2026**.

Các URL tải dữ liệu xuất hiện trong mục Data:

- [Train ZIP](https://dl-challenge.zalo.ai/2026/datamate_dkewxftdzo/train.zip)
- [Public test ZIP](https://dl-challenge.zalo.ai/2026/datamate_dkewxftdzo/public_test.zip)

Baseline không dùng model. Backend GPU cần khai báo model và đóng image theo hướng dẫn BTC.
