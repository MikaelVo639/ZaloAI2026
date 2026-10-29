# Quy trình kiểm tra

Các bước này dùng cho mọi checkout. Báo cáo một lượt chạy cụ thể được lưu trong
thư mục output của lượt đó, theo `docs/EXPERIMENTS.md`.

## Kiểm tra nền tảng

```sh
uv sync --locked
uv run datamate smoke
uv run python -m unittest discover -s tests -v
```

`smoke` tự tạo/reuse demo gồm CSV, Excel và SQLite. Mốc baseline của fixture:

| Chỉ tiêu | Giá trị kỳ vọng |
|---|---|
| Câu hỏi / business | 24 / 3 |
| Record thiếu hoặc id lạ | 0 |
| Đúng / sai / abstain | 21 / 0 / 3 |
| PA local | 0.875 |

Lệnh trả mã lỗi khác 0 nếu không đạt mốc trên. Xem `outputs/demo/metrics.json`,
`submission.jsonl`, `run.json` và `logs/` để tìm nguyên nhân. Nếu chủ động thay hành
vi baseline/fixture, cập nhật kỳ vọng trong code/tests và giải thích trong PR.
Demo dùng dữ liệu tự tạo; số đo này không đại diện cho điểm thi.

Windows: chạy thêm `demo.cmd --no-pause` hoặc `./scripts/demo.ps1` trong terminal
phù hợp để kiểm tra launcher. Chúng dùng cùng smoke command.

## Kiểm tra trên dữ liệu BTC

1. Chuẩn bị dữ liệu theo README và tạo business holdout cố định.
2. Chạy predict vào thư mục run mới, rồi validate với đúng questions của split đó.
3. Với train, evaluate bằng nhãn train; dùng cùng holdout khi so các phiên bản.
4. Kiểm tra lỗi, abstain, thời gian và nguồn luật trong logs. Đủ ID chỉ xác nhận
   đường chạy; không chứng minh đáp án đúng.
5. Ghi commit/config/checksum dữ liệu và metrics vào báo cáo thí nghiệm.

Inference không được mở answers; tests có kiểm tra ranh giới này. Đánh giá mù cần
business/schema chưa dùng để thiết kế giải pháp; tự chia holdout sau khi đã xem dữ
liệu không tự tạo ra một đánh giá mù.

## Docker với network tắt

Tạo demo trước bằng smoke command, sau đó build:

```sh
docker build -t datamate-starter .
```

Linux/macOS:

```sh
mkdir -p outputs/docker
docker run --rm --network none -v "$PWD/data/demo:/data:ro" -v "$PWD/outputs/docker:/result" datamate-starter
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force outputs/docker
docker run --rm --network none --mount "type=bind,source=$((Get-Location).Path)/data/demo,target=/data,readonly" --mount "type=bind,source=$((Get-Location).Path)/outputs/docker,target=/result" datamate-starter
```

Kiểm tra kết quả:

```sh
uv run datamate validate outputs/docker/submission.jsonl --questions data/demo/questions.jsonl
uv run datamate evaluate outputs/docker/submission.jsonl --reference data/demo/answers.jsonl --output outputs/docker/metrics.json
```

Đối chiếu submission giữa hai lượt chạy cùng image/input, kiểm tra logs và thời gian.
Job Docker trong CI dùng input read-only và `--network none`. Trạng thái build/run
của một image phải lấy từ log CI hoặc báo cáo run, không suy ra từ việc có Dockerfile.
