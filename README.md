# DataMate — Zalo AI Challenge 2026

Khung làm việc chung cho team: đọc CSV, Excel nhiều sheet và SQLite; chạy baseline
offline; xuất submission và log từng câu. Python >= 3.11, phiên bản chuẩn của dự án
là Python 3.12. Baseline không cần GPU, model weights hoặc API key.

Baseline chỉ hỗ trợ một số câu hỏi và bảng bán hàng dạng phẳng. Câu hỏi/schema/luật
chưa hỗ trợ sẽ thành `abstain`. Đây là nền tảng để phát triển, chưa phải solution
hoàn chỉnh; điểm demo không đại diện cho điểm thi.

AGENT-02 có backend thử nghiệm `local_plan`: Qwen2.5-Coder-7B-Instruct (7,61 tỷ
tham số, Apache-2.0, weights 19/09/2024) sinh JSON Plan rồi worker Python tính số.
Backend mặc định vẫn là `baseline`. Khai báo và nạp offline ở
[models/README.md](models/README.md), cách chạy/thành phần còn chờ ở
[docs/AGENT_02.md](docs/AGENT_02.md). Chưa có benchmark model trên holdout/GPU.

## Bắt đầu sau khi clone

Chạy các lệnh từ thư mục gốc repo, trên Windows, Linux hoặc macOS:

```sh
uv sync --locked
uv run datamate smoke
uv run python -m unittest discover -s tests -v
```

`smoke` tự tạo dữ liệu tổng hợp nếu chưa có, chạy predict, kiểm tra đủ ID và chấm
bằng scorer local. Lệnh trả mã lỗi nếu kết quả khác mốc kiểm tra. Kết quả nằm ở
`outputs/demo/`. Không cần dữ liệu BTC để chạy bước này.

Nếu không dùng uv, tạo virtualenv Python 3.12, kích hoạt rồi cài:

```sh
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python -m datamate smoke
python -m unittest discover -s tests -v
```

Sau khi cài môi trường, Windows có thể nhấp đúp `demo.cmd` trong File Explorer;
PowerShell có `./scripts/demo.ps1`. Hai launcher dùng cùng lệnh `datamate smoke`.
Chạy tự động bằng `demo.cmd --no-pause`. Không nhấp đúp file `.ps1` để chạy script.

## Dữ liệu và baseline

Tải `train.zip` và `public_test.zip` từ mục Data trên [portal BTC](https://challenge.zalo.ai/portal/datamate)
rồi lưu vào `data/archives/`. Dữ liệu BTC và outputs không được phân phối trong Git.

```sh
uv run datamate import-data data/archives/train.zip
uv run datamate import-data data/archives/public_test.zip
uv run datamate inspect --data-dir data/raw/train --output outputs/train_catalog.json
uv run datamate split-businesses --data-dir data/raw/train --output outputs/business_split.json --seed 42
uv run datamate predict --data-dir data/raw/train --output-dir outputs/runs/baseline-v1 --config configs/baseline.toml
uv run datamate validate outputs/runs/baseline-v1/submission.jsonl --questions data/raw/train/questions.jsonl
uv run datamate evaluate outputs/runs/baseline-v1/submission.jsonl --reference data/raw/train/answers.jsonl --questions data/raw/train/questions.jsonl --holdout outputs/business_split.json --output outputs/runs/baseline-v1/validation_metrics.json
```

`import-data` giữ nguyên cấu trúc ZIP và từ chối ghi đè. `--data-dir` trỏ trực tiếp
vào split có `questions.jsonl`, `rulebook.md`, `calendar.csv`, `data/<business_id>/`.
Đổi tên thư mục run khi bắt đầu một thí nghiệm mới.

## Chia việc theo module

| Vai trò | Module chính | Kết quả cần bàn giao |
|---|---|---|
| Integration | pipeline, CLI, config, Docker, CI | luồng chạy chung, đóng gói, giới hạn runtime |
| Data & Schema | `ingestion/` | đọc đủ bảng, join đúng, chuẩn hóa có nguồn |
| Semantics | `semantics/` | hiểu câu hỏi, thời gian và rulebook của split |
| Agent | `agent/` | lập kế hoạch và tính toán, evidence từ thực thi |
| Evaluation | `evaluation/`, tests | scorer, phân tích lỗi và cổng abstain |

Team phân công người phụ trách và người review theo [docs/TEAM.md](docs/TEAM.md).
Đợt phát triển đầu tiên có điều kiện bàn giao trong [docs/BACKLOG.md](docs/BACKLOG.md).
Contract nằm ở `src/datamate/contracts.py`. Thay đổi đầu vào/đầu ra phải được ghi
trong PR cùng các module bị ảnh hưởng. Nhánh `main` phải luôn chạy được smoke/test.

## Quy trình chung

- [CONTRIBUTING.md](CONTRIBUTING.md): nhánh, PR, review và điều kiện ghép code.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): luồng xử lý và điểm mở rộng.
- [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md): holdout và báo cáo thí nghiệm.
- [docs/VALIDATION.md](docs/VALIDATION.md): cách xác nhận một checkout hoạt động.
- [docs/SUBMISSION.md](docs/SUBMISSION.md): tạo và kiểm tra file nộp từ public test.
- [docs/COMPETITION.md](docs/COMPETITION.md): định dạng và giới hạn cuộc thi.

```text
outputs/<run>/
  submission.jsonl       # nối với questions bằng id, không dựa vào thứ tự dòng
  logs/<sha256-id>.json  # đáp án, thời gian, reason, evidence, error
  run.json              # version, config, checksum inputs và thời gian
```

Scorer trong repo là **local proxy**, không phải scorer chính thức. PA local tính
`(correct - wrong) / N`; thiếu dòng hoặc `abstain` tính 0. Inference không đọc
`answers.jsonl`. Log, dữ liệu và weights được giữ ngoài Git.

`configs/abstain.toml` dùng kiểm tra đóng gói. `min_confidence` của baseline không
phải xác suất đã hiệu chỉnh. `max_seconds` được kiểm tra giữa các bước; backend mới
cần timeout riêng cho model/worker. Xem [configs/README.md](configs/README.md).

## Docker

Sau khi tạo demo, build image bằng `docker build -t datamate-starter .` rồi chạy với
network tắt, mount split read-only tại `/data`, output tại `/result`.
Có lệnh cho PowerShell và Linux/macOS trong [docs/VALIDATION.md](docs/VALIDATION.md).
CI có job Python trên Windows/Linux và job Docker chạy offline.

Dockerfile là bản CPU cho starter, chạy từ `/code/predict.sh` theo guideline BTC.
Baseline không train hay sinh dữ liệu training; xem `training_code/README.md` và
`training_code/data_manifest.json`. Khi thêm GPU model, dùng base image theo
guideline BTC và đóng weights vào image trước inference; khai báo model trong
`models/README.md`.
