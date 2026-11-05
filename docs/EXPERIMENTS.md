# Thí nghiệm có thể đối chiếu

## AGENT-02 — kiểm tra prototype ngày 05/11/2026

Nhánh `codex/agent-local-plan-prototype`, base revision `5701f829a60d06c14e02ffe1700bf84b3beae7ed`,
working tree có thay đổi chưa commit. Giả thuyết: JSON Plan có kiểm tra + worker
deadline có thể dùng lại executor và cung cấp evidence trước khi đo chất lượng model.
Thiết kế và hướng dẫn A/B ở [AGENT_02.md](AGENT_02.md); không thay baseline mặc định.

- `uv sync --locked`, `uv run datamate smoke`, unittest discover đều hoàn tất.
  Suite có 132 test: 131 qua, 1 test riêng Windows skip trên macOS.
- Smoke trên demo tổng hợp giữ 21 correct / 0 wrong / 3 abstain, PA local 0.875,
  coverage 0.875. Đây là baseline trên fixture, **không phải điểm model hay leaderboard**.
- Hai probe `reproduce_pr9_deadlines.py` không còn tái hiện lỗi: ingestion được gọi
  lại sau stop và IPC send quá hạn trả timeout. Probe là mô phỏng, không đo tải thật.
- Kiểm tra planner giả lập bao phủ Plan → compute → output/log; lỗi/timeout và kết
  quả bất thường thành abstain; boolean/table qua kiểm tra format scorer hiện tại.
- Không có train/weights/GPU trong checkout dùng kiểm tra; **chưa chạy holdout** seed
  42/0.2, chưa chọn confidence threshold và chưa ước tính thời gian private test.

Artifact của lượt này giữ ở thư mục ignored
`outputs/runs/agent02-synthetic-20261105/`: receipt/checksums, probe deadline, và log
code/output của một câu tổng hợp dùng planner giả lập. Không đưa artifact vào Git.
Quyết định: sẵn sàng review code prototype; chưa có bằng chứng chấp nhận hướng B.
Evaluation cần benchmark model thật; Semantics/Data cần thống nhất contract mở rộng;
Integration cần xác minh runtime GPU và image offline.

## Benchmark EVAL-01

Chuẩn bị Python 3.12 và chạy `uv sync --locked` trong từng checkout cần đo.
Tạo holdout một lần bằng `split-businesses --seed 42 --validation-fraction 0.2`.
Sau đó gọi công cụ từ checkout Evaluation, các đường dẫn được tính từ thư mục
đang gọi lệnh:

```sh
uv run datamate benchmark --checkout ../target-checkout --data-dir data/raw/train --holdout outputs/business_split.json --config configs/baseline.toml --output-dir outputs/runs/new-run
```

Thêm `--compare-to outputs/runs/previous-run` để so với run đã hoàn tất. Checkout
đích chỉ cần có lệnh `predict`, nên cùng runner đo được revision cũ chưa có
`benchmark`. Runner gọi trực tiếp Python trong virtualenv đích, không tải package
hoặc model trong lúc inference. Network isolation thực tế được kiểm tra bằng
Docker CI; lệnh benchmark tại máy không tự thay đổi cấu hình network của OS.

Runner kiểm tra checksum và phân hoạch holdout trước khi chạy. `max_seconds` của
config là giới hạn wall-clock của subprocess; quá hạn thì dừng cả cây tiến trình
do runner tạo. Thiếu đáp án/log hoặc subprocess lỗi => `incomplete`, giữ output
một phần và không báo điểm của một run đầy đủ. Không dùng lại thư mục output.

Mỗi run có `experiment.json`, checksum input tree, bản sao config/holdout,
`metrics.json`, CSV nhóm lỗi, `outcomes.jsonl`, `handoff.json`, `summary.md`, cùng
`artifact.zip` và SHA256. `reference_sha256` chỉ được tính sau khi predict kết thúc.
Input-tree checksum dùng danh sách JSON có thứ tự path POSIX + SHA256, loại
`answers.jsonl`; không so trực tiếp checksum này với ZIP hoặc manifest tạo theo
cách khác. So sánh yêu cầu cùng input/nhãn/holdout/config; runtime còn phụ thuộc
hardware, Python và lockfile được ghi riêng trong metadata.

`error`, `evidence.execution`, `evidence.ingestion_execution` được đọc cùng nhau;
tổng execution error đếm số câu bị ảnh hưởng, không cộng trùng từng dấu hiệu lỗi.
`status=ok` của worker không chứng minh adapter thành công: reason adapter vẫn
được phân nhóm. Thiếu worker metadata ở log cũ được ghi nhận riêng. Các nhóm câu
có tiền tố `diagnostic_` là nhãn từ khóa phục vụ phân tích, không phải Plan đã
được agent xác nhận. Grammar có thể che lỗi ở bước sau; nhóm reason chỉ phản ánh
bước đang chặn câu hỏi. Thời gian business lặp trong log không được cộng thành runtime.

Ca AGENT-01 không cần dữ liệu BTC:

```sh
uv run python scripts/reproduce_pr9_deadlines.py --checkout ../agent-checkout
```

Hai probe mô phỏng trạng thái sau worker stop và IPC send chậm. Trường
`reproduced=true` ghi nhận lỗi; đây không phải benchmark IPC thật. Agent bổ sung
regression và bản sửa trong PR của mình. Evaluation giữ artifact trước/sau theo SHA,
không tự thay đổi worker. Toàn bộ train đã được phân tích; đây không phải đánh giá mù.

## Giữ dữ liệu đánh giá cố định

Tạo `outputs/business_split.json` bằng `split-businesses --seed 42`. Team thống nhất
một seed/validation fraction và giữ cùng danh sách business khi so các thay đổi.
Mốc chung cho đợt đầu: seed **42**, validation fraction **0.2**. Evaluation lưu
manifest gốc cùng artifact baseline và chia sẻ qua kênh artifact của team. Mọi
người đối chiếu checksum questions/rulebook và danh sách validation trước khi so
kết quả; không tự chia lại hoặc chọn seed theo điểm.
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

## Lệnh baseline cho đợt đầu

Thay `baseline-v1` bằng tên run mới, giữ nguyên seed/fraction và config khi đối chiếu:

```sh
uv run datamate split-businesses --data-dir data/raw/train --output outputs/runs/baseline-v1/business_split.json --seed 42 --validation-fraction 0.2
uv run datamate predict --data-dir data/raw/train --output-dir outputs/runs/baseline-v1/train --config configs/baseline.toml
uv run datamate validate outputs/runs/baseline-v1/train/submission.jsonl --questions data/raw/train/questions.jsonl
uv run datamate evaluate outputs/runs/baseline-v1/train/submission.jsonl --reference data/raw/train/answers.jsonl --questions data/raw/train/questions.jsonl --holdout outputs/runs/baseline-v1/business_split.json --output outputs/runs/baseline-v1/train/validation_metrics.json
```

Đếm `reason` và `error` trong logs riêng biệt. Grammar/rules chưa hỗ trợ có thể
che lỗi adapter ở bước sau, nên không suy ra một nguyên nhân duy nhất từ số
abstain. Báo cáo toàn train và validation riêng; ghi rõ dữ liệu đã xem, không gọi
lượt baseline đã phân tích toàn train là kiểm tra mù.
