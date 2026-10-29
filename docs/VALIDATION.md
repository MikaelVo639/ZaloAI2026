# Kiểm tra local — 29/10/2026

Môi trường: Windows, Python 3.12.14, dependencies từ `uv.lock`.

| Hạng mục | Kết quả |
|---|---|
| Unit/integration tests | 27 test qua |
| Demo CSV + XLSX + SQLite | 24 câu: 21 đúng, 0 sai, 3 abstain; PA local 0.875 |
| `scripts/demo.ps1` | predict -> validate -> evaluate chạy thành công |
| `predict.ps1` | chạy thành công, đủ 24 ID |
| `predict.sh` qua Git Bash | chạy thành công, đủ 24 ID |
| Train BTC | đủ 3.000 ID, 30 business; khoảng 119 giây; 10 đúng, 0 sai, 2.990 abstain; PA local 0.003333 |
| Public test BTC | đủ 1.000 ID, 14 business; khoảng 24 giây; tất cả abstain |
| Catalog train | đọc và mô tả đủ 30 business |
| Business holdout | 24 business train / 6 validation, seed 42 |
| Docker build/run | chưa chạy: máy này không có Docker CLI trong PATH |
| CI trên GitHub | đã có workflow; chưa chạy trên remote |

Các thời gian trên là một lượt chạy local có các tiến trình kiểm tra khác cùng
hoạt động; không phải benchmark tốc độ hoặc số đo trên máy tham chiếu của BTC.

Lượt train/public xác nhận đường chạy và định dạng. **Độ phủ hiện rất thấp**:
backend mới trả lời được 10 câu train, public chưa có đáp án cụ thể. Chưa có model
local, adapter join và semantic parser tổng quát. Các câu unsupported được chuyển
thành abstain, không giả tạo đáp án hoặc suy ra unanswerable từ lỗi thực thi.

Split business được chuẩn bị cho các thí nghiệm tiếp theo. Lượt kiểm tra tích hợp
này đã xem cấu trúc toàn bộ train, không phải đánh giá mù trên business chưa gặp.

Kết quả local tại máy đã dựng repo:

- `outputs/demo/`: kết quả synthetic demo và metrics.
- `outputs/train-final/`: submission, log và manifest cho train.
- `outputs/public-final/`: submission, log và manifest cho public test.
- `outputs/train_catalog.json`: schema, README, một số dòng mẫu.
- `outputs/business_split.json`: phân chia business cố định.

Tất cả output và dữ liệu BTC chỉ ở local, không đưa vào commit. Chưa upload
submission lên portal. Không có điểm leaderboard chính thức được đo ở đây.

Checksum ZIP đã tải:

```text
train.zip
c1adbc654a301c30e4cf20b55d301381025f49db1162d88322b552d7472fea01

public_test.zip
46fb50f926ed1510b9f099a53918f45e304b256680452c93e74401b4a5442c52
```
