# Luồng và contract

```text
Split + Question
  -> Catalog (file/sheet/table, cột gốc, sample, README)
  -> Plan (intent, start/end, top_k, dòng luật làm căn cứ)
  -> Candidate (type, answer, confidence, reason, evidence)
  -> finalize/validate
  -> submission.jsonl + log + run manifest
```

`Split` chứa rulebook hiện tại và đường dẫn lịch, không chứa nhãn đáp án.
`BusinessContext` chứa catalog và cache của một business. Bộ nhớ được giải phóng
sau khi xử lý business đó; cache không được mang dữ liệu/luật sang split khác.

`Agent.answer(question, context) -> Candidate` là điểm tích hợp agent mới.
Đăng ký backend trong `pipeline.py` và `Config`, thêm cấu hình TOML và test trước
khi đổi mặc định. Có thể truyền `agent=` vào `predict()` khi thử nghiệm trong Python.

## Ingestion hiện có

- CSV: BOM UTF-8, fallback cp1258, nhận delimiter thông dụng; giữ nguyên giá trị gốc.
- XLSX: `openpyxl` read-only/data-only, mỗi sheet là một bảng, không tính lại công thức.
  Công thức thiếu cached value, header nhiều dòng/ô merge, cột trùng tên cần adapter riêng.
- SQLite: `mode=ro` + `query_only`, tất cả bảng người dùng, tên được quote.
- Catalog chỉ lấy header và ít dòng mẫu, không giả định quan hệ join hoặc kiểu nghiệp vụ.

`ingestion/sales.py` chỉ nhận bảng phẳng có order id, ngày, trạng thái, thành tiền dòng
và mã/tên sản phẩm. Alias cột giúp nhận một số export có tên tiếng Việt; đây chưa phải
bộ suy luận schema tổng quát. Nhiều bảng sales/shard được ghép dọc, không tự join bảng khác.

Metadata đơn trống được điền tiếp trong cùng nhóm đơn, reset khi đổi đơn. Dòng có
cùng mã đơn và mọi ô giống nhau bị loại trùng. Trạng thái khác nhau trong cùng đơn
làm adapter từ chối. Chỉ `completed/hoàn thành` và `shipping/đang giao` hợp lệ; huỷ/trả
bỏ cả đơn. Không coi thiếu số liệu là 0.

Số gốc của Excel/SQLite giữ kiểu số; chuỗi số nhận định dạng nghìn và thập phân
Việt/Anh thông dụng. Chuỗi `1.234` có thể mơ hồ giữa thập phân và nghìn: baseline
hiểu là nghìn. Team cần thêm quy tắc theo nguồn khi định dạng này xuất hiện ở số lượng.

## Baseline hiện có

- Tổng doanh thu; số đơn hợp lệ; top sản phẩm theo số lượng cơ bản đã có sẵn.
- Hôm nay/hôm qua, ngày cụ thể, tháng M/YYYY, tháng này/tháng trước, tuần này/tuần
  trước, N ngày gần nhất. Tháng này dừng tại `as_of_date`; tuần bắt đầu thứ Hai.
- Top sản phẩm cần mã, tên và cột số lượng cơ bản; không tự đoán hệ số quy đổi.
- Grammar đóng: phần điều kiện không hiểu khiến câu bị bỏ qua. Chưa hỗ trợ lọc
  sản phẩm/nhóm/thanh toán, so sánh kỳ, công nợ, giá vốn, tồn kho, Tết hoặc SQL/model.
- Chỉ nhận một số dòng luật có cấu trúc đã biết trong rulebook hiện tại. Luật chưa
  hiểu => abstain. Calendar luôn được xác nhận/checksum; baseline chưa có bài toán lịch âm.

`Candidate.confidence=1` chỉ có nghĩa đã qua các cổng của baseline, không bảo đảm
đáp án đúng với mọi dữ liệu. Thêm backend model phải hiệu chỉnh bằng business holdout.

## Điểm cần phát triển

Data & Schema bổ sung adapter order/detail/product với kiểm tra cardinality, khóa
trùng, độ phủ ngày và quy đổi đơn vị. Semantics thêm kế hoạch có nguồn luật cụ thể.
Agent dùng model offline để lập kế hoạch, lấy số từ thực thi; sandbox/timeout worker
phải có trước khi cho chạy code sinh tự động. Evaluation đo hiệu quả và phân tích lỗi.

Catalog xuất bằng `catalog_dict(catalog, relative_to=split.root)` có `source` là
đường dẫn tương đối từ split, dùng dấu `/` trên mọi hệ điều hành. Holdout manifest
ghi `source: questions.jsonl` và checksum questions/rulebook thay cho đường dẫn máy.
Khi chuyển từ metadata có đường dẫn tuyệt đối, tạo lại catalog/holdout bằng CLI.

Log giữ code/call, input, result và nguồn file/cột của bộ tính. Prototype `local_plan`
của AGENT-02 thêm JSON Plan từ model local và metadata weights/runtime; xem
[AGENT_02.md](AGENT_02.md). Baseline vẫn là backend mặc định.
Ngân sách toàn lượt chỉ được kiểm tra giữa các bước. Ingestion/compute và model
load/generate có deadline riêng trong worker, gồm spawn và IPC; đây chưa phải hard
deadline của toàn pipeline.
