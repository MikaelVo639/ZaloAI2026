# Thí nghiệm có thể đối chiếu

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

## EVAL-02: scorer, A/B và các mốc bàn giao

Dương (@baoduong2342007) sở hữu Evaluation; reviewer @MikaelVo639.
Scorer v2 so `number` bằng giá trị Decimal chính xác, không tolerance hoặc làm tròn
prediction; percent giữ quy tắc hai chữ số. Scalar/list/table thống nhất phép so số.
Mọi điểm đều là **local proxy, không phải điểm BTC**. Holdout đã được xem, không mù.

Giữ manifest EVAL-01 (seed 42, fraction 0.2), không tạo lại theo điểm.
Benchmark mới ghi version/checksum scorer (gồm cả validation và chuẩn hóa text).
Run cũ thiếu identity scorer không được so trực tiếp. Chấm lại từ inference đã hoàn
thành vào thư mục mới; giữ nguyên artifact gốc:

```sh
uv run datamate rescore --run-dir outputs/eval01/main-5001a846 --data-dir data/raw/train --holdout outputs/eval01/business_split.json --output-dir outputs/eval02/baseline-v2
uv run datamate benchmark --checkout . --data-dir data/raw/train --holdout outputs/eval01/business_split.json --config configs/baseline.toml --output-dir outputs/eval02/main-v2 --compare-to outputs/eval02/baseline-v2
```

Mỗi PR thuật toán: cùng input, nhãn, holdout, scorer và config. Ghi revision/dirty,
lockfile, môi trường, runtime và checksum. Summary vào PR; artifacts/log/nhãn ở ngoài
Git. Handoff chia grammar theo topic chẩn đoán, tối đa ba nhóm/module, ba ca/nhóm;
cases chỉ từ 24 business train, số lượng train/validation ghi riêng. Topic không
phải Plan và chỉ phản ánh bước đang chặn. Artifact thô vẫn có kết quả validation
để audit; không lấy chúng làm ví dụ phát triển.

Ngày 08/11, khi có B từ Agent, thêm `--allow-config-difference` vào benchmark B
với `--compare-to` trỏ run A. Cờ này chỉ bỏ kiểm tra config hash: dữ liệu, nhãn,
holdout và scorer vẫn phải giống. comparison.json ghi hai config/hash và diff,
chuyển trạng thái theo ID, PA validation và mọi ID validation mới sai.
Chỉ giữ B nếu PA validation tăng **và không có ID đúng/abstain chuyển sang sai**;
Integration còn phải xác nhận image đúng thể lệ. Gate không tự phê duyệt image.

Ngày 12/11 nếu giữ B: thu candidate với min_confidence=0 trên train, giữ lỗi và
candidate không hợp lệ là abstain. Thử các confidence hữu hạn trong [0,1], thêm 0
và 1; chọn ngưỡng PA cao nhất trên **24 business train**, hòa chọn ngưỡng cao hơn.
Khóa config rồi đánh giá một lần trên 6 business validation; không chỉnh ngưỡng
lại từ validation. Nếu gate không đạt, báo fail và giữ A.

Ngày 15/11: dùng đúng image ứng viên do Integration cung cấp, ghi checksum image,
chạy hai lần offline với /data read-only vào hai output mới. Dùng validate kiểm
tra đủ/không trùng ID; đối chiếu đáp án theo ID và log từng câu (đáp án, thời gian,
lỗi, evidence). Báo tỷ lệ abstain và thời gian gồm load model; xác nhận dưới 6 giờ
trên máy tham chiếu trước khi ký bàn giao. Demo không thay thế benchmark thật.
Evaluation không tự nộp portal; Integration phụ trách solution trước hạn.

### Các lệnh hoàn tất Evaluation

Thu candidate bằng config có `min_confidence=0`, rồi chọn ngưỡng. Config baseline
`configs/eval-candidates.toml` dùng kiểm tra công cụ; đây không phải model B.

```sh
uv run datamate benchmark --checkout . --data-dir data/raw/train --holdout outputs/eval01/business_split.json --config configs/eval-candidates.toml --output-dir outputs/eval02/candidates
uv run datamate calibrate --run-dir outputs/eval02/candidates --data-dir data/raw/train --holdout outputs/eval01/business_split.json --output-dir outputs/eval02/calibration
```

`calibration/config.toml` là cấu hình đã khóa. Lệnh kiểm tra checksum data/nhãn/
holdout/scorer, chỉ xét confidence từ business train, hòa chọn ngưỡng cao hơn.
Validation không tham gia chọn ngưỡng. Output là replay candidate, không phải một
lượt inference độc lập. Chạy lại config đã khóa bằng benchmark và so với A qua
`--allow-config-difference`; không suy ra chất lượng B từ baseline confidence=1.

Kiểm tra hai lượt có sẵn mà không đọc nhãn:

```sh
uv run datamate verify-runs --data-dir data/raw/train --run-dir outputs/release/first --run-dir outputs/release/second --output-dir outputs/release/checks
```

Lệnh kiểm tra format, đầy đủ ID/log, đáp án/log, lỗi chỉ được abstain, checksum
questions/rules/calendar, config, thời gian và đáp án theo ID qua hai lượt. Nó
không chứng minh thời gian load model hoặc tính đúng của câu trả lời.

Với image đã có trên Docker daemon:

```sh
uv run datamate verify-image --image datamate-candidate --data-dir data/raw/train --output-dir outputs/release/docker --gpus
```

Bỏ `--gpus` cho image CPU. Lệnh resolve image ID bất biến một lần, không pull;
chạy hai lượt với `--network none`, input `/data` read-only và output riêng. Đo
wall-clock toàn container (mỗi lượt tối đa 21600 giây), kiểm tra input tree không
đổi, cleanup đúng container của lượt chạy khi timeout. Ghi image ID, thời gian,
log và báo cáo vào output. CI dùng cùng lệnh với CPU demo. Kết quả này không tự
xác nhận GPU tham chiếu, licence/weights hoặc chấp nhận bản nộp; Integration vẫn
phải kiểm tra trên máy yêu cầu và giao đúng image cuối.
