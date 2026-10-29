# Thí nghiệm có thể đối chiếu

## Giữ dữ liệu đánh giá cố định

Tạo `outputs/business_split.json` bằng `split-businesses --seed 42`. Team thống nhất
một seed/validation fraction và giữ cùng danh sách business khi so các thay đổi.
Manifest chứa id, seed, checksum questions/rulebook và đường dẫn tương đối tới
`questions.jsonl`; cùng input sẽ cho cùng manifest ở các checkout khác nhau.

Chỉ dùng business train để học/chọn ví dụ. Không dùng public/private test để training
hoặc tìm đáp án. Ghi rõ mức độ đã xem dữ liệu khi gọi một kết quả là held-out hoặc mù.

## Một thư mục cho một run

Ví dụ: `outputs/runs/baseline-v1/`, `outputs/runs/order-join-v1/`. Không dùng lại output
của run khác vì có thể lẫn log cũ. Giữ config có tên trong `configs/` khi muốn team
chạy lại; output không được commit vào repo nguồn.

Mỗi báo cáo run ghi:

| Trường | Nội dung |
|---|---|
| Giả thuyết / issue | lỗi nào hoặc hành vi nào đang cải thiện |
| Revision | commit đã chạy; ghi rõ nếu còn thay đổi chưa commit |
| Config | đường dẫn config và các CLI overrides |
| Dữ liệu | split, checksum ZIP/raw package, checksum questions/rulebook/calendar |
| Holdout | seed, validation fraction và manifest dùng đánh giá |
| Kết quả | correct, wrong, abstain, PA local, coverage, runtime |
| Phân tích | nhóm câu đổi kết quả, lỗi mới, giới hạn bằng chứng |
| Quyết định | chấp nhận, sửa tiếp hay bỏ giả thuyết; người review |

`run.json` ghi config/version, checksum questions/rulebook/calendar và elapsed time.
Commit, hardware và checksum raw package được bổ sung vào báo cáo; các checksum
metadata không thay thế checksum toàn bộ dữ liệu. Ghi tên GPU/CPU, RAM và phiên bản
runtime khi đo tốc độ; tránh đưa tên tài khoản hoặc đường dẫn máy vào báo cáo chia sẻ.

Kèm summary trong PR; file lớn, logs và report chi tiết được trao đổi qua kênh lưu
artifact mà team thống nhất. PA từ scorer local không phải điểm leaderboard.
