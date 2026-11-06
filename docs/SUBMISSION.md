# Chuẩn bị submission

## Public test

Dùng đúng split public, không dùng dữ liệu demo. Từ thư mục gốc repo:

```sh
uv run datamate predict --data-dir data/raw/public_test --output-dir outputs/submissions/public-v1 --config configs/baseline.toml
uv run datamate validate outputs/submissions/public-v1/submission.jsonl --questions data/raw/public_test/questions.jsonl
```

File nộp là **`outputs/submissions/public-v1/submission.jsonl`**, mỗi dòng một JSON
object có `id`, `type`, và `answer` nếu type cần giá trị. Logs và `run.json` giữ lại
để chạy lại/đối chiếu. Đổi `public-v1` thành tên lượt mới cho mỗi lần tạo submission.

Một thành viên khác kiểm tra đúng split, đủ ID, đúng format và tỷ lệ abstain trước
khi người được phân công upload file vào mục Public Submissions trên portal BTC.
Ghi revision, cấu hình, run và điểm portal trả về vào lịch sử nộp bài của team.
Các lệnh trong repo chỉ tạo file; không tự upload hoặc dùng tài khoản cá nhân.

Baseline có thể abstain rất nhiều. File hợp lệ với toàn abstain chỉ kiểm tra luồng
nộp và có PA bằng 0 theo công thức đã công bố. Tăng chất lượng phải dựa vào đánh giá
train/holdout; không coi việc portal nhận file là bằng chứng mô hình trả lời đúng.

## Solution Docker

Image đọc split tại `/data` và ghi `/result/submission.jsonl`, `/result/logs/` qua
`bash /code/predict.sh` không tham số. Trước khi đóng băng image:

- Chạy quy trình offline ở `docs/VALIDATION.md`, gồm data read-only và network tắt.
- Khai báo model/revision/licence/parameter count và đóng đủ weights vào image.
- Lưu code training/inference, config, dữ liệu và bằng chứng training (nếu có) trong
  `/code/training_code/`, cùng manifest có kích thước và SHA-256. Nếu không train,
  khai báo rõ việc đó trong README và manifest.
- Kiểm tra mỗi log có `id`, `answer`, `elapsed_ms` và `thinking`; câu tính bằng
  code có `code` và `output` phản ánh đúng phép tính đã chạy.
- Chạy image hai lần trên cùng split và so submission; đo runtime gồm load model.
- Export duy nhất image `linux/amd64`. Kiểm tra `.tar` chưa nén không quá 100 GB,
  tính MD5 cho `.tar.gz`, rồi tải lại từ link chia sẻ để xác nhận checksum.
- Đối chiếu base image, tài nguyên và hạn nộp theo guideline mới nhất của BTC.

Phân công một người phụ trách đóng gói và một người kiểm tra độc lập. Cách gửi image
và kết quả private theo portal và thể lệ BTC; xem `docs/COMPETITION.md` để tra nguồn.
