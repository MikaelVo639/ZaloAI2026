# Nhận việc cho 5 người

Mỗi người một nhánh: `infra/...`, `ingestion/...`, `semantics/...`, `agent/...`,
`evaluation/...`. Nhánh `main` phải luôn chạy được demo và test. Dùng PR nhỏ, kèm
lệnh kiểm tra và dữ liệu chứng minh thay đổi; một người khác review trước khi ghép.

| Người | Việc đầu tiên | Bàn giao có thể kiểm tra |
|---|---|---|
| 1 — tích hợp | cài trên máy thứ hai; chạy CI/Docker; theo dõi runtime | lệnh chạy thống nhất, image offline, log đúng contract |
| 2 — dữ liệu | xem `outputs/train_catalog.json`; hỗ trợ export tách order/detail | adapter với join không nhân dòng, test tổng tiền/đơn |
| 3 — luật | chọn câu đang abstain; thêm mốc thời gian và metric cụ thể | Plan có dòng luật, ca biên as-of/tuần/tháng |
| 4 — agent | chọn model đúng thể lệ, chạy local trong Docker | Agent dùng contract, kết quả từ thực thi, có deadline |
| 5 — đánh giá | cố định holdout theo business, nhóm lỗi từ metrics/logs | bảng đúng/sai/bỏ qua, chênh lệch trước-sau, ngưỡng gate |

Prompt có thể dùng khi giao việc cho coding assistant:

1. **Tích hợp:** “Đọc AGENTS.md và contracts.py. Giữ CLI hiện có chạy được.
   Thêm [thay đổi cụ thể], chạy demo/test, báo file đổi và lệnh đã kiểm tra.”
2. **Dữ liệu:** “Đọc catalog/README của business thuộc phần train, không hard-code id.
   Thêm adapter cho [schema cụ thể]; giữ raw data, kiểm tra join không tăng tổng tiền,
   thêm fixture nhỏ cho missing key/duplicate/đơn trả.”
3. **Luật:** “Thêm [ý nghĩa câu hỏi] vào semantics. Đọc rulebook của split hiện tại,
   trả Plan có nguồn luật, viết ca ranh giới ngày và phần câu không hỗ trợ.”
4. **Agent:** “Thêm backend offline qua Agent protocol. Không gọi dịch vụ ngoài.
   Tôn trọng giới hạn model, worker có timeout, ghi code/query và output vào evidence.”
5. **Đánh giá:** “Đọc report local và log train/validation, chọn nhóm lỗi gây mất PA.
   Không dùng test để học. Đưa ca tái hiện nhỏ, đo lại cùng holdout và phân tích sai số.”

Mỗi ngày xem chung một nhóm câu sai. Giao lỗi về đúng module trước khi thêm model
hoặc mở rộng prompt. Thay contract phải báo người dùng đầu ra đó.
