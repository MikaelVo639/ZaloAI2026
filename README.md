# DataMate starter — Zalo AI Challenge 2026

Repo khung cho team 5 người: chạy **offline**, đọc CSV / Excel nhiều sheet / SQLite,
trả lời một số câu đơn giản bằng phép tính thật, ghi submission và log từng câu.
Không cần API key, GPU hoặc model để chạy baseline này.

Đây là starter kỹ thuật, chưa phải giải pháp thi hoàn chỉnh. Baseline chỉ hiểu một
nhóm câu hỏi và bảng bán hàng dạng phẳng; gặp schema, bộ luật hoặc câu hỏi chưa hỗ
trợ thì `abstain`. Điểm trên dữ liệu demo không thể hiện năng lực trên đề thi.
Bản hiện tại xuất đủ ID, trả lời đúng 10 câu train bằng phép tính thật và bỏ qua
phần còn lại; public hiện đều abstain. Xem [docs/VALIDATION.md](docs/VALIDATION.md)
để biết kết quả local và giới hạn đã kiểm tra.

## Chạy nhanh

Python >= 3.11; khuyến nghị Python 3.12. Dùng [uv](https://docs.astral.sh/uv/):

```powershell
uv sync --locked
uv run datamate demo --output data/demo
uv run datamate predict --data-dir data/demo --output-dir outputs/demo
uv run datamate validate outputs/demo/submission.jsonl --questions data/demo/questions.jsonl
uv run datamate evaluate outputs/demo/submission.jsonl --reference data/demo/answers.jsonl --output outputs/demo/metrics.json
```

Trong Windows, khi đã có `.venv`, mở File Explorer rồi nhấp đúp **`demo.cmd`**
ở thư mục gốc repo. Cửa sổ sẽ giữ kết quả trên màn hình đến khi bạn bấm phím.
Launcher này chạy Python trực tiếp, không phụ thuộc ứng dụng mở file `.ps1`.
Khi chạy tự động trong terminal, dùng `demo.cmd --no-pause`.

Hoặc chạy toàn bộ demo bằng một lệnh trong PowerShell:

```powershell
.\scripts\demo.ps1
```

Nếu không dùng uv:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install --no-deps -e .
.venv\Scripts\python -m datamate demo --output data/demo
.\predict.ps1 -DataDir data/demo -OutputDir outputs/demo
```

Lệnh `demo` yêu cầu thư mục đích trống. Script `scripts/demo.ps1` dùng lại demo đã có.
Kết quả dự kiến của demo tự tạo: **24 câu, 21 đúng, 0 sai, 3 abstain; PA local = 0.875**.
Demo kiểm tra đủ ba định dạng, đơn huỷ/trả, dòng trùng và ngày tương lai.

## Dữ liệu BTC

Trang chính thức: [DataMate](https://challenge.zalo.ai/portal/datamate).
Tải `train.zip` / `public_test.zip` từ mục **Data** bằng tài khoản đã đăng nhập.
Hai file tải tại máy này đã được giữ local; dữ liệu và kết quả đều nằm trong `.gitignore`.

```powershell
uv run datamate import-data train.zip
uv run datamate import-data data/archives/public_test.zip
uv run datamate inspect --data-dir data/raw/train --output outputs/train_catalog.json
uv run datamate predict --data-dir data/raw/train --output-dir outputs/train
uv run datamate validate outputs/train/submission.jsonl --questions data/raw/train/questions.jsonl
uv run datamate evaluate outputs/train/submission.jsonl --reference data/raw/train/answers.jsonl --questions data/raw/train/questions.jsonl --output outputs/train/metrics.json
```

`import-data` giữ nguyên cấu trúc ZIP, không ghi đè file có sẵn. Nếu file ZIP nằm ở
vị trí khác, truyền đường dẫn thực tế. `--data-dir` phải trỏ trực tiếp vào split có
`questions.jsonl`, `rulebook.md`, `calendar.csv`, `data/<business_id>/`.

```powershell
uv run datamate predict --data-dir data/raw/public_test --output-dir outputs/public
uv run datamate validate outputs/public/submission.jsonl --questions data/raw/public_test/questions.jsonl
```

Mọi lệnh ở đây tạo file local. Việc upload submission lên portal do team thực hiện.

## Đầu ra

```text
outputs/<run>/
  submission.jsonl       # đủ một record cho mỗi question id
  logs/<sha256-id>.json  # id, final_answer, elapsed_seconds, reason, evidence, error
  run.json              # cấu hình, version, thời gian, checksum luật/câu hỏi/lịch
```

Pipeline xử lý từng business để giới hạn bộ nhớ, ghi và flush từng đáp án.
Thứ tự dòng có thể khác `questions.jsonl`; nối bằng `id`.
Exception ở một câu được ghi log và chuyển thành `abstain`.
Đầu ra phải nằm ngoài thư mục dữ liệu đầu vào.
Mỗi run nên dùng một thư mục output riêng để không lẫn log cũ.

`backend=abstain` có trong `configs/abstain.toml` để kiểm tra việc đóng gói.
`min_confidence` hiện là cổng chấp nhận của baseline, **không phải xác suất đã hiệu chỉnh**.
`max_seconds` là ngân sách hợp tác giữa các bước; model/worker tương lai cần tự có timeout
cho từng lần gọi và cơ chế dừng tiến trình tính toán.

## Team và cấu trúc

| Người | Sở hữu | Điểm mở rộng đầu tiên |
|---|---|---|
| 1 | `pipeline.py`, `cli.py`, config, Docker, CI | ghép module, đo runtime, đóng gói |
| 2 | `ingestion/` | adapter join order/detail/product; chuẩn hóa có nguồn |
| 3 | `semantics/` | thêm cách hỏi, mốc thời gian, luật theo split |
| 4 | `agent/` | model local lập kế hoạch + SQL/Python có giới hạn |
| 5 | `evaluation/`, tests | đối chiếu scorer BTC, phân tích lỗi, ngưỡng abstain |

Contract dùng chung: `src/datamate/contracts.py`.
Hướng dẫn nhận việc: [docs/TEAM.md](docs/TEAM.md).
Luồng và điểm cắm module: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Các giới hạn đã đọc trên portal: [docs/COMPETITION.md](docs/COMPETITION.md).

## Đánh giá không lẫn business

```powershell
uv run datamate split-businesses --data-dir data/raw/train --output outputs/business_split.json --seed 42
uv run datamate evaluate outputs/train/submission.jsonl --reference data/raw/train/answers.jsonl --questions data/raw/train/questions.jsonl --holdout outputs/business_split.json --output outputs/train/validation_metrics.json
```

Giữ nguyên split qua các thí nghiệm. Chỉ dùng phần train để học hoặc chọn ví dụ;
dùng validation để chọn ngưỡng và so sánh thay đổi. Không dùng public/private test
để training. Inference không mở `answers.jsonl`; test có kiểm tra điều này.

Scorer trong repo là **local proxy**, cài PA `(correct - wrong) / N` và các luật
khớp kiểu/giá trị đã công bố. Chuỗi giữ dấu tiếng Việt; list có thứ tự; bảng so dòng
không theo thứ tự. Một số chi tiết alias trong bảng, sai số số thực và làm tròn có
thể khác scorer BTC. Khi BTC phát scorer chính thức, dùng nó làm nguồn chuẩn.

## Kiểm tra và Docker

```powershell
uv run python -m unittest discover -s tests -v
docker build -t datamate-starter .
New-Item -ItemType Directory -Force outputs/docker
docker run --rm --network none --mount "type=bind,source=$((Get-Location).Path)/data/demo,target=/data,readonly" --mount "type=bind,source=$((Get-Location).Path)/outputs/docker,target=/result" datamate-starter
```

Linux/macOS:

```sh
mkdir -p outputs/docker
docker build -t datamate-starter .
docker run --rm --network none -v "$PWD/data/demo:/data:ro" -v "$PWD/outputs/docker:/result" datamate-starter
# Hoặc chạy predict.sh trực tiếp sau khi cài dependencies:
DATA_DIR=data/demo RESULT_DIR=outputs/shell sh predict.sh
```

CI kiểm tra Windows/Linux và có job build/chạy Docker với network tắt. Dockerfile
hiện là bản CPU cho starter; khi thêm model GPU, dùng đúng base image CUDA do BTC
hướng dẫn. Image cuối phải chứa weights cần dùng và khai báo model trong README.

Để chia sẻ repo: tạo một remote của team rồi `git remote add origin <URL>` và
`git push -u origin main`. Dữ liệu raw, logs, weights và `.env` không được Git theo dõi.

Ở checkout Windows hiện tại, thư mục `D:/zaloai` thuộc Administrators. Nếu Git báo
`dubious ownership`, thêm `-c safe.directory=D:/zaloai` vào lệnh Git tại checkout này,
ví dụ `git -c safe.directory=D:/zaloai status`. Không cần thay cấu hình trust toàn máy.
