# Thí nghiệm có thể đối chiếu

## Integration — nhóm 19, 21, 23, 24 ngày 10/11/2026

- **19 · Lý do huỷ/trả.** Hai câu được giao hỏi lý do cụ thể. Sổ bán và
  README của hai nguồn chỉ đủ xác định trạng thái cho mục đích này. Giữ
  `abstain` khi câu hỏi có điều kiện lý do, với regression cho cả huỷ và trả.
- **21 · Nhiều đợt giao.** Dùng ngày bán trong sổ đặt đơn để chọn cohort theo
  mã đơn. Nối sổ giao theo mã đơn, gộp theo cặp mã đơn và số đợt, rồi áp
  `as_of_date` lên ngày giao. Đợt đã giao của đơn sau đó trả vẫn được tính.
  `Plan.observed_on` là trường tùy chọn chỉ dùng cho intent này.
- **23 · Nhập trừ bán.** Nối chi tiết và đầu phiếu nhập theo mã phiếu, sau đó
  đối chiếu mã hàng với dòng bán. Quy số lượng nhập về đơn vị cơ bản từ bảng
  quy đổi của shop. Phần bán theo luật doanh thu của split. Số phát sinh tính
  riêng với tồn kho. Thiếu hệ số, mã hàng không rõ hoặc nguồn bán chưa hỗ trợ
  thì giữ `abstain`.
- **24 · Tiền nhập và nhà cung cấp.** Tổng đầu phiếu lặp ở bảng chi tiết
  được tính một lần khi các dòng đồng nhất về ngày, trạng thái, nhà cung cấp
  và tổng tiền. Sổ chỉ có thành tiền dòng được cộng theo mã phiếu. Xếp
  “nhà cung cấp chính” theo số phiếu và “nhập nhiều tiền nhất” theo giá trị
  phiếu, dùng mã nhà cung cấp khi nguồn có cả mã và tên.

Trong probe public so với checkout trước sửa, bảy câu được giao chuyển từ
`abstain` sang có câu trả lời, gồm ba câu đếm đơn giao, hai câu chênh lệch
nhập bán và hai câu về phiếu nhập. Hai câu lý do giữ `abstain`.
Probe đo độ phủ và xem phép tính từ nguồn phát hành, không có nhãn public.
Trên đủ 1.000 câu public, checkout `bdc509a` trả lời 138 câu và bản sửa
trả lời 146 câu. Tám thay đổi đều từ `abstain` sang có đáp án, gồm bảy
câu được giao và một câu tiền nhập dùng cùng adapter. Mốc 420 trong
tài liệu giao việc thuộc lượt audit khác. Lượt public sau sửa chạy lại
toàn bộ shop giao hàng sau khi chuyển đọc sổ sang bước ingestion của
worker. Kết quả chạy lại thay đúng một câu đã hết hạn ở bước tính.

So từng ID trên 3.000 câu train và cùng holdout 630 câu (seed 42,
validation fraction 0,2). Hai bản dùng baseline CPU và cùng dữ liệu.
Lượt train được chạy theo business; các bản ghi chồng giữa các shard
khớp nhau. Nhãn chỉ dùng khi chấm sau inference.

| Tập | Trước sửa, đúng / sai / abstain | Sau sửa, đúng / sai / abstain |
|---|---:|---:|
| Train (3.000) | 2.557 / 0 / 443 | 2.576 / 0 / 424 |
| Holdout (630) | 499 / 0 / 131 | 510 / 0 / 120 |

Toàn bộ thay đổi train là `abstain → correct` theo local proxy. Các kết quả
chi tiết và submission kiểm tra nằm trong ignored `outputs/runs/mikael-ab/`.

`uv sync --locked`, `uv run datamate smoke` và unittest đã chạy qua.
Smoke đạt 21 đúng / 0 sai / 3 abstain theo local proxy. Unittest chạy 394
ca, 393 qua và 1 skip. Trên Windows, cấu hình `safe.directory` chỉ áp
cho checkout và thư mục test tạm trong tiến trình chạy test.

Rà soát trước PR bổ sung kiểm tra mã nhà cung cấp, khóa hàng, số dòng
phiếu và giới hạn kết quả theo cohort giao hàng. Chạy lại 50 câu train
thuộc các intent bị ảnh hưởng và toàn bộ 1.000 câu public, mọi bản ghi
đều khớp lượt sau sửa ở trên.

Thứ tự tích hợp đề xuất là chốt ánh xạ nguồn với Data & Schema, xem
`Plan` và ngữ pháp với Semantics, rồi xem phép tính và evidence với Agent.
Evaluation kiểm tra kết quả theo từng câu trên cùng split và manifest
holdout trước khi merge.

## AGENT-02 — sửa ngân sách prompt bằng tokenizer thật ngày 05/11/2026

Sau khi có train, chọn 5 câu đủ kỳ dương lịch thuộc **business train** của manifest
seed 42/0.2; không dùng nhãn hay public/private test. Chuẩn bị riêng tokenizer đúng
revision Qwen đã khai báo, không tải weights; Transformers 4.51.3, Tokenizers 0.21.4,
Jinja2 3.1.6, `local_files_only=True` và offline flags khi tokenize.

Prompt ban đầu chứa toàn calendar: **45.541–55.065 token**, vượt cả context 32.768;
calendar riêng chiếm 35.133 token. Đây là lỗi input bị phát hiện trước khi có GPU.
Đã scope calendar tường minh về biên kỳ/as_of_date, giữ checksum toàn file/số dòng/
ngày thiếu; vẫn đọc và kiểm tra mọi dòng, không đổi rulebook/README/schema hay source.
Calendar có ngày trùng/sai header/ragged/ngày không hợp lệ làm abstain trước load.

Trên đúng 5 prompt đó sau sửa: **9.250–18.852 token**, calendar 269–347 token.
Config local_plan tăng input limit từ 8.192 lên 24.576, output 1.024; tổng dưới
32.768. Prompt khác vẫn được kiểm budget và abstain nếu vượt, không bảo đảm 5 mẫu
đại diện cho mọi schema. Loader chuyển sang SDPA; chưa đo GPU/VRAM hoặc generation.

Counts và prompt probes giữ trong ignored `outputs/runs/agent02-readiness-20261105/`.
Tokenization này không có model parameters chạy, không có đáp án hoặc điểm A/B mới.
Sau bản sửa, sync/smoke/full unittest hoàn tất: **163 test, 162 qua, 1 test Windows
skip**; smoke giữ 21 đúng / 0 sai / 3 abstain. Log suite giữ riêng trong ignored
`outputs/runs/agent02-prompt-budget-20261105/`.

## AGENT-02 — benchmark train thật và bộ chạy acceptance ngày 05/11/2026

Đã tự chuẩn bị train công khai từ URL BTC trong `COMPETITION.md`, không tải public/
private test. Archive SHA256:
`c1adbc654a301c30e4cf20b55d301381025f49db1162d88322b552d7472fea01`.
File nguồn giữ nguyên ở ignored `data/raw/train`; không đưa dữ liệu/log vào Git.

Lệnh đã chạy:

```sh
uv run datamate split-businesses --data-dir data/raw/train --output outputs/business_split.json --seed 42 --validation-fraction 0.2
uv run datamate benchmark --checkout . --data-dir data/raw/train --holdout outputs/business_split.json --config configs/baseline.toml --output-dir outputs/runs/agent02-real-train-baseline-20261105
```

Inference revision `43cf869`, metadata `dirty=true`; config `configs/baseline.toml`.
Holdout seed 42/0.2 có 24 business train và 6 validation. SHA256 manifest:
`7e356421711fd5ffe4a7ed29e49b67e03c809fa70e1d8aa0cf3c0a787fea2164`.
Input-tree SHA256 (không gồm answers):
`ab2cfe298b3f3a93a825f973a1e5f479ab5067eadc44bbb1d64fb08ef2fef54d`.
Đã xem câu hỏi thuộc business train để chuẩn bị đề xuất contract; không gọi kết quả
này là đánh giá mù. Evaluation cần đối chiếu checksum manifest với bản team lưu.

| Phân hoạch | N | Đúng | Sai | Abstain | PA local | Coverage |
|---|---:|---:|---:|---:|---:|---:|
| Toàn bộ train | 3000 | 151 | 0 | 2849 | 0.050333 | 0.050333 |
| Validation business | 630 | 78 | 0 | 552 | 0.123810 | 0.123810 |

Thời gian inference subprocess **82,72 giây**, macOS ARM64, 10 logical CPUs, RAM
16 GiB, Python 3.12.16. Model load/generation = **0**. Số liệu khớp kết quả được báo
trong SEM-01 #18, không có thay đổi thuật toán inference ở lượt này. Đây là baseline
CPU/local proxy, **không phải kết quả Qwen, GPU hoặc leaderboard**.

Artifact gồm experiment/input tree/config/holdout/metrics/outcomes/handoff và archive
ở ignored `outputs/runs/agent02-real-train-baseline-20261105/`. Artifact SHA256:
`d40425aa70e817e08301e5e44c5010e83742e50ff4c56d3cb2f23add779b2793`.

`scripts/run_agent02_acceptance.py` chuẩn bị lượt A/B/model repeat trên môi trường
GPU cấp sẵn. Nó kiểm tra CUDA/model local, gọi runner Evaluation, so từng câu mới
thành sai, checksum submission hai lượt và thời gian gồm load/startup. Tạo phép
chiếu theo số câu/business khi người chạy cung cấp quy mô private. Không tải model,
không thuê cloud, không gọi một run baseline là model run.

Máy hiện tại chưa có CUDA/runtime/weights; preflight đã báo thiếu và dừng trước
inference. Người dùng xác nhận chưa có môi trường GPU. Vì vậy chưa có A/B model
thật, ngưỡng confidence hiệu chỉnh hay kết luận private dưới 6 giờ. Contract boolean/
table được soạn ở `AGENT_02_CONTRACT.md`, chờ Semantics và Data xác nhận trước code.

## AGENT-02 — tích hợp SEM-01 #18 ngày 05/11/2026

Commit prototype `26724e1` được ghép với main `e045f3f` trên nhánh Agent. Giữ contract
`Filter`/kỳ so sánh của SEM-01; bộ kiểm tra nhận `percent` cho revenue/change_percent
và chỉ áp [0,100] cho cancel_rate. Baseline và local_plan dùng chung bước lập Plan
có thử lại với tên sản phẩm trước khi gọi model.

`uv sync --locked`, smoke và unittest discover đã hoàn tất. Suite có **150 test:
149 qua, 1 test riêng Windows skip**. Regression thực thi kiểm tra thay đổi doanh
thu −50% / 150%, bộ lọc doanh thu/số đơn theo sản phẩm không gọi model, phạm vi tỷ
lệ huỷ và trường Plan chưa hỗ trợ. Smoke giữ **21 đúng / 0 sai / 3 abstain**, PA
local proxy 0.875 trên fixture tổng hợp. Đây không phải kết quả Qwen hay leaderboard.

Artifact của lượt tích hợp nằm ở ignored `outputs/runs/agent02-main-merge-20261105/`.
Chưa chạy model thật, benchmark train/holdout hay đo GPU; các điều kiện chọn hướng B
và bàn giao trong `AGENT_02.md` vẫn giữ nguyên.

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
Runner chỉ kiểm tra backend là tên hợp lệ, confidence và ngân sách chung; backend
và các thiết lập model được giữ nguyên để checkout đích tự kiểm tra khi predict.
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

## Review tích hợp AGENT-02 — 06/11/2026

Đã review EVAL-02 #19, INT-02 #21, SEM/DATA #22 và AGENT-02 #20. Đối chiếu
contract của #22 với Agent phát hiện validation cũ từ chối chuỗi doanh thu, tháng
tốt nhất, top khách, số đếm theo ngưỡng, tỉ trọng và số lượng theo đơn vị. Đã sửa
kiểu/phạm vi/độ dài chuỗi và evidence Decimal/JSON number, thêm regression từ
executor qua validation; từ chối kỳ chồng lấn, ngưỡng số đơn lẻ và trường so sánh
không được thực thi. Hai backend cùng dùng vocabulary nhóm khi lập Plan.

Planner local nhận schema DATA-03 trong worker có deadline và luật có cấu trúc
SEM-02 cùng nguyên văn rulebook của split. Acceptance kiểm thêm version/checksum
scorer, tránh so hai lượt chấm bằng quy tắc khác nhau. Verifier Docker giữ network
tắt, `/data` read-only, `/code/predict.sh` và shm 16 GB.

Lượt đầu ở `9a49851` đạt 1.677 đúng / 0 sai / 1.323 abstain; validation 384 đúng /
0 sai / 246 abstain. Log cho thấy ba business train bị `SOURCE_MISSING` vì tên file
ZIP UTF-8 không có cờ tương ứng bị giải mã CP437. Bản sửa trong #22 chỉ dùng lookup
Unicode NFC và phép giải mã đảo được; giữ nguyên file/tên nguồn trong lineage, từ
chối nếu nhiều file khớp một tên README. Không đổi dữ liệu gốc hoặc holdout.

### Kết quả cuối cùng trên cùng holdout

Inference revision `154256b3caac2a85071d5ef32fe708ed7bcce88b`,
`configs/baseline.toml`, seed 42 / validation fraction 0.2. Đây là **baseline CPU,
local proxy trên train đã xem**, không phải điểm chính thức, đánh giá mù hoặc kết
quả Qwen/GPU. Baseline cũ `43cf869` được chấm lại bằng scorer v2 trước khi so sánh.

| Phân vùng | Baseline cũ: đúng / sai / abstain | Tích hợp: đúng / sai / abstain | PA local trước → sau |
|---|---:|---:|---:|
| Toàn train, 3.000 câu | 151 / 0 / 2.849 | **1.930 / 0 / 1.070** | 0.050333 → **0.643333** |
| 6 business validation, 630 câu | 78 / 0 / 552 | **384 / 0 / 246** | 0.123810 → **0.609524** |

Có **1.779 abstain → correct**, không có câu mới sai. Validation quality gate của
Evaluation đạt. Inference subprocess **463,03 giây**, tổng benchmark **498,23 giây**;
0 câu có lỗi thực thi. Diagnostic thiếu worker metadata ở 1.011 câu không được coi
là bằng chứng lỗi hoặc unanswerable. Metadata ghi `dirty=true` vì file local chưa
tracked; diff tracked tại lúc bắt đầu rỗng (SHA256 `e3b0c442…b855`).

Identity và artifact (giữ trong ignored run artifacts):

| Thành phần | SHA256 |
|---|---|
| Input tree | `ab2cfe298b3f3a93a825f973a1e5f479ab5067eadc44bbb1d64fb08ef2fef54d` |
| Holdout | `7e356421711fd5ffe4a7ed29e49b67e03c809fa70e1d8aa0cf3c0a787fea2164` |
| Reference | `0b5fe9d13edf5fae306bda9cafaf699c06b639846c8e1d9e41c39a86d1160b3d` |
| Scorer v2 | `49094297b726fedf05393ba60a1e77e7a1858226dc9438a55a5dfa8b53bb6b07` |
| Submission | `a64e4f71ddd8d5722b330371889c3e93269f192703d31c0dc0d47b566854ba60` |
| Final artifact ZIP | `cea64c67f91e1e42852bd350f8686fe2387bfc707adff6a2bc8066846a46a47c` |

Các thư mục local của lượt review:
`outputs/runs/agent02-review-20261106-baseline-v2`,
`outputs/runs/agent02-review-20261106-integrated` và
`outputs/runs/agent02-review-20261106-final`. Smoke/test/preflight/token counts ở
`outputs/reviews/agent02-pr-integration-20261106`; dữ liệu, labels, prompts, logs và
weights không được commit. Sau khi chuẩn bị train và manifest cố định như phần
trước, tái hiện vào các thư mục mới:

```sh
uv sync --locked
uv run datamate smoke
uv run python -m unittest discover -s tests -v
uv run datamate benchmark --checkout . --data-dir data/raw/train --holdout outputs/business_split.json --config configs/baseline.toml --output-dir outputs/runs/agent02-review-reproduction
```

Bộ tích hợp có **240 test: 239 pass, 1 skip Windows-specific**. PR #21 sau xử lý
xung đột có 139 test; PR #22 sau sửa tên file có 195 test, mỗi bộ 1 skip tương tự.
CI Ubuntu/Windows/Docker qua trên các head đã review. Docker daemon local không
khả dụng; bằng chứng chạy container của bản ghép nằm ở CI remote.

Tokenizer local đúng revision Qwen đã khai báo kiểm lại cùng 5 business train của
probe trước: **19.626–22.296 input token**, dưới 24.576. Đây chỉ là tokenizer CPU,
không phải generation hay benchmark model. Preflight xác nhận chưa có CUDA GPU,
optional runtime hoặc model snapshot/manifest, nên **chưa hoàn tất A/B model,
threshold B hoặc đo private dưới 6 giờ**. Boolean/table mới có format validation;
hai intent và nguồn công nợ trong `AGENT_02_CONTRACT.md` vẫn chờ thống nhất. Giữ
#16 mở cho các điều kiện này, không suy ra hoàn tất role chỉ từ PR đã merge.

## E01 — baseline đối chứng trên Windows, 07/11/2026

Đã chạy `423a1924c855c7567565f1c2c9bb1f370d2dc10b` với
`configs/baseline.toml`, Windows và Python 3.12.14. Checkout sạch lúc đo.
`uv sync --locked`, smoke và `git diff --check` qua. Unit test có 239 bài đạt,
1 bài bỏ qua vì tài khoản Windows thiếu quyền tạo symlink.

| Phân vùng | Tổng | Đúng | Sai | Abstain | PA local |
|---|---:|---:|---:|---:|---:|
| Toàn train | 3.000 | 1.930 | 0 | 1.070 | 0.643333 |
| 24 business phát triển | 2.370 | 1.546 | 0 | 824 | 0.652321 |
| 6 business validation | 630 | 384 | 0 | 246 | 0.609524 |

Đây là kết quả chấm local proxy trên dữ liệu train đã xem. Inference gồm startup
mất 738,40 giây và toàn benchmark mất 814,95 giây. Đã đối chiếu đủ 3.000 ID và
log với submission, 0 câu có lỗi thực thi. Submission trùng SHA256 với lượt
review AGENT-02 ngày 06/11. Trường worker metadata vắng ở 1.011 câu theo báo cáo
của runner.

Bản holdout dùng seed 42, fraction 0.2. Đổi CRLF thành LF trên bản manifest
Windows hiện có để khôi phục đúng checksum manifest của nhóm và giữ nguyên toàn
bộ business/ID. Artifact nằm ngoài Git ở `outputs/e01/a0-423a192/`, có
`experiment.json`, `metrics.json`, `handoff.json`, `artifact.zip` và checksum.

| Thành phần | SHA256 |
|---|---|
| Input tree do runner Windows ghi | `b90ce894e1c351a377a8eeda12b100eca461b442c08184a0402d1f789cd4f1b4` |
| Input tree khi xếp đường dẫn theo POSIX | `ab2cfe298b3f3a93a825f973a1e5f479ab5067eadc44bbb1d64fb08ef2fef54d` |
| Reference | `0b5fe9d13edf5fae306bda9cafaf699c06b639846c8e1d9e41c39a86d1160b3d` |
| Holdout | `7e356421711fd5ffe4a7ed29e49b67e03c809fa70e1d8aa0cf3c0a787fea2164` |
| Scorer v2 | `49094297b726fedf05393ba60a1e77e7a1858226dc9438a55a5dfa8b53bb6b07` |
| Config | `6b53304a32ac03b2b8c79a871369f2ebbba1c8839167446ab2847f043af1d371` |
| Submission | `a64e4f71ddd8d5722b330371889c3e93269f192703d31c0dc0d47b566854ba60` |
| Artifact ZIP | `8408f6997fb8b5ddfd0387cc26ac4a9b9ba4cd98d6b67ea5de8d61d1e6882fa1` |

`e01_abstain_triage.csv` gán một chủ đề chính cho từng ID theo từ khóa và giữ
riêng lý do runner ghi trong log. `e01_abstain_handoff.json` có số lượng theo
phân vùng và ví dụ từ business phát triển. Các nhóm dưới đây là gợi ý để Long và
các chủ module đối chiếu với log và nguồn dữ liệu.

| Nhóm cần rà soát | Business phát triển | Validation | Tổng |
|---|---:|---:|---:|
| Giá bán | 135 | 8 | 143 |
| Công nợ | 81 | 37 | 118 |
| Nhập hàng | 77 | 26 | 103 |
| Tồn kho hoặc lô | 47 | 19 | 66 |
| Dấu hiệu vướng nguồn hoặc mapping khác | 13 | 9 | 22 |
| Xu hướng doanh thu | 20 | 1 | 21 |
| Nhóm khác đã có nhãn trong CSV | 321 | 107 | 428 |
| Cần phân loại tiếp | 130 | 39 | 169 |
| **Tổng** | **824** | **246** | **1.070** |

Có 30 câu khớp nhiều chủ đề. Handoff đánh dấu 14 ứng viên train để rà soát
`top_debt_customers` và 20 ứng viên train để rà soát `revenue_increasing`.
Mỗi intent có tối đa 3 ví dụ để đối chiếu với contract. Minh review baseline,
Long xác nhận nhóm dữ liệu, Huy và Kurosuz rà lại các ca được chuyển tiếp.

Khi có head thuật toán cần review, chuẩn bị môi trường cho checkout đích rồi
chạy từ checkout Evaluation với dữ liệu, manifest, scorer và config của A0.
Giữ cùng máy Windows khi so thời gian. Dùng thư mục mới cho từng head.

```sh
uv run datamate benchmark --checkout ../candidate-checkout --data-dir data/raw/train --holdout outputs/e01/business_split.json --config outputs/e01/a0-423a192/config.toml --output-dir outputs/e01/candidate-new --compare-to outputs/e01/a0-423a192
```

Xem `comparison.json` và lấy tất cả chuyển trạng thái sang `wrong` trong
`changes`, kể cả ngoài validation. So runtime và số ID sai trước khi chốt PR.
Runner Windows và POSIX tính input tree theo thứ tự khác nhau. Nếu đổi máy sang
Linux, chạy A0 lại trên máy đó trước khi so. Nếu đổi scorer trên cùng môi
trường, tạo lượt `rescore` mới cho A0.

**A vẫn là mặc định. B chưa đánh giá.** GPU preflight trên máy Windows này
chưa đạt vì thiếu runtime CUDA và snapshot weights đã chuẩn bị. E02 chạy
`scripts/run_agent02_acceptance.py` trên máy GPU đã chuẩn bị, dùng revision,
config và manifest đã chốt cho A và hai lượt B. Xem từng gate trong
`acceptance.json`, gồm generation thật, kết quả hai lượt, lỗi mới và runtime
bao gồm load. Nếu hiệu chỉnh B, thu candidate với `min_confidence=0`, chọn
ngưỡng trên business train, khóa config rồi chạy lại acceptance trên config đó.
