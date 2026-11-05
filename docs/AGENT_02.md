# AGENT-02 — prototype hướng B và bàn giao

Phạm vi @tqhuy0602, reviewer @Kurosuz. Nhánh `codex/agent-local-plan-prototype`.
Trạng thái ngày 05/11/2026: đã có code kiểm thử được bằng dữ liệu tổng hợp; chưa có
weights, train, GPU hay số liệu holdout trong lượt triển khai này. Không coi test với
planner giả lập là kết quả của Qwen. Chưa đủ bằng chứng để chọn B thay A ngày 08/11.

## Đầu ra đã có

| Hạng mục | Trạng thái |
|---|---|
| Chọn/khai báo model trước khi thêm loader | Qwen2.5-Coder-7B-Instruct, BF16 Safetensors, 7,61B tổng, Apache-2.0; revision và ngày weights trong `models/README.md` |
| Prototype câu hỏi + schema + luật → Plan | `agent/local_plan.py`; dùng Plan hiện tại, không chạy code do model sinh |
| Nạp weights offline | `agent/model_runtime.py`; manifest/checksum, local files only, không remote code; script tải chỉ dùng lúc chuẩn bị |
| Deadline | Worker riêng cho model load/generate và worker riêng cho ingestion/compute; bao gồm startup/send/recv |
| Kiểm tra kết quả | `agent/validation.py`; lỗi, timeout, output sai kiểu/phạm vi/evidence → abstain |
| `boolean`/`table` | Có kiểm tra format theo scorer và regression; **chưa thêm intent** khi chưa thống nhất Plan mở rộng |
| Holdout/threshold/GPU 6 giờ | Chờ môi trường/dữ liệu và quy trình Evaluation/Integration bên dưới |

Đã tích hợp main `e045f3f` (SEM-01 #18): `Plan` có kỳ so sánh và `Filter` sản phẩm.
Agent dùng chung đường lập Plan deterministic, gồm bước lấy tên sản phẩm, trước
khi gọi model. Bộ kiểm tra nhận `percent` cho `revenue/change_percent`, cho phép
giá trị âm hoặc trên 100%; chỉ tỷ lệ huỷ đơn bị giới hạn [0,100]. Không mở rộng
thêm contract hay grammar so với SEM-01; model vẫn chỉ sinh tập Plan đơn kỳ không lọc.
Thay đổi giao thoa:
pipeline/CLI đăng ký backend và các timeout theo cấu hình; manifest ghi model thực
sự đã nạp; CLI override dùng `dataclasses.replace` để giữ các trường mới. Pipeline
đóng worker do nó tạo kể cả khi ghi output lỗi. Probe deadline của Evaluation được
bổ sung split giả để tiếp tục chạy sau bản sửa cache.

## Cách prototype quyết định

1. Grammar hiện tại (kể cả so sánh kỳ và thử lại với tên sản phẩm từ worker) lập được
   Plan thì chạy baseline và giữ confidence của baseline, không gọi model.
2. Câu còn lại chỉ đi tới model nếu rulebook của split bật được phép tính và parser
   thời gian hiện tại xác định đúng một kỳ dương lịch. Schema giữ cột, kiểu Python
   quan sát được và mẫu có sẵn; đơn vị được giữ trong README/cột gốc, không đoán.
   Đây là view tạm từ Catalog, chưa thay đầu ra schema/units chính thức của DATA-03.
3. Prompt gồm câu hỏi, `as_of_date`, schema, README, rulebook và calendar của split.
   Không đưa question id/business id vào prompt, không có few-shot lấy từ test/nhãn.
   Calendar được đọc/kiểm tra toàn bộ, giữ SHA256 và số dòng, nhưng prompt chỉ có
   dòng cho biên kỳ đã resolve và `as_of_date`, kèm scope và các ngày nguồn không có.
   Model không hỗ trợ kỳ âm lịch; không dùng bản calendar tóm tắt này để suy luận Tết.
   Rulebook/README/schema không bị cắt ngầm. Vượt giới hạn vẫn abstain.
4. Model chỉ xuất JSON Plan với intent, start/end, top_k, unsupported và các dòng luật
   trích nguyên văn. Từ chối JSON trùng key, NaN, code, đáp án tự sinh, field lạ, intent
   chưa hỗ trợ, kỳ không khớp parser và thiếu luật từ split hiện tại.
5. Model không có quyền suy ra `unanswerable`. Chỉ bộ tính hiện tại có thể trả loại
   này khi có phạm vi dữ liệu tường minh từ README. Worker lỗi/timeout luôn abstain.
6. Bộ tính chạy trong worker, evidence có code/call, nguồn, input, result, execution.
   Bộ kiểm tra đối chiếu type với Plan, count với số đơn input, percent huỷ trong
   [0,100], ngày trong kỳ, danh sách trong top_k và answer với result thực thi.
   Không áp trần tiền tuỳ ý hay ép doanh thu âm thành 0.

**Giới hạn ngữ nghĩa:** schema validation và nguồn luật không chứng minh model hiểu
đúng câu hỏi. Chặn được một số từ khoá lọc/so sánh đã biết, nhưng model vẫn có thể bỏ
sót điều kiện diễn đạt theo cách khác hoặc chọn sai intent/top_k. `unsupported=[]`
do model khai báo cũng không phải bằng chứng đầy đủ. Vì vậy B chỉ là backend thử
nghiệm, confidence cố định 0.5 không phải xác suất, chưa hiệu chỉnh và không thay mặc
định. Evaluation phải liệt kê từng câu chuyển sang sai trước khi đề xuất giữ B.

Lỗi load model được cache cho cả lượt; sau timeout/crash planner không load lại
weights cho từng câu. Các câu grammar hiểu vẫn chạy baseline. Worker dùng spawn,
không phải sandbox code bất kỳ; prototype không gọi exec/eval, SQL sinh tự động hay
API server, kể cả localhost. Isolation OS phải kiểm tra trong image network tắt.

## Chạy trên môi trường GPU đã chuẩn bị

Đọc `models/README.md`, cài requirements optional và chạy script chuẩn bị snapshot
trước. `requirements-model.txt` pin PyTorch 2.7.1 CUDA 12.8 và Transformers 4.51.3;
[PyTorch có wheel cu128 chính chủ](https://pytorch.org/get-started/previous-versions/).
Đây là môi trường đề xuất, chưa phải runtime đã xác minh trên RTX 5090. Integration
pin thêm dependency transitive, lưu môi trường thực tế và kiểm tra image theo BTC.

Không dùng `uv sync` lại sau khi cài optional runtime trước một lượt benchmark;
`uv sync` có thể dọn package ngoài lockfile baseline. Dùng `uv run --no-sync` khi
gọi lệnh ở môi trường GPU đã chuẩn bị:

```sh
uv run --no-sync datamate predict --data-dir data/raw/train --output-dir outputs/runs/agent02-b --config configs/local_plan.toml
uv run --no-sync datamate validate outputs/runs/agent02-b/submission.jsonl --questions data/raw/train/questions.jsonl
```

Đường dẫn model mặc định là `models/qwen2.5-coder-7b-instruct`, tính từ gốc repo.
Khi đóng image, copy config mới trỏ tới thư mục weights trong image. Không tải model
trong predict. Chuẩn bị runtime xong thì chạy lại với network tắt, `/data` read-only;
artifact/checksum weights giữ ngoài Git.

Config thử nghiệm dùng input tối đa 24.576 token, output 1.024, tổng dưới context
mặc định 32.768 của snapshot. Tokenizer thật đã xác nhận 5 prompt business train sau
khi scope calendar còn 9.250–18.852 token; trước sửa là 45.541–55.065. Đây chỉ là
kiểm tra đầu vào bằng tokenizer, không phải model generation/benchmark GPU.
Loader dùng `attn_implementation="sdpa"` để runtime có thể chọn attention kernel
tiết kiệm bộ nhớ thay vì luôn tạo attention matrix eager; [Qwen2 hỗ trợ SDPA](https://huggingface.co/docs/transformers/v4.51.3/model_doc/qwen2).
Hiệu năng/VRAM và tính lặp lại vẫn cần xác minh trên GPU thật.

## Bàn giao Evaluation và đo thời gian

Đã thêm bộ chạy acceptance cho máy GPU **đã được cấp sẵn**, không tải dependency
hay weights trong lúc chạy:

```sh
uv run --no-sync python scripts/run_agent02_acceptance.py --check-environment
uv run --no-sync python scripts/run_agent02_acceptance.py --data-dir data/raw/train --holdout outputs/business_split.json --output-dir outputs/runs/agent02-gpu-acceptance
```

Lệnh đầu chỉ kiểm tra runtime/CUDA >=12.8 và manifest local. Lệnh sau chạy baseline
một lần, model hai lần qua runner Evaluation có hard process deadline, rồi kiểm tra
đúng input/holdout/reference, kết quả lặp lại, từng câu mới chuyển sang sai, và PA
validation. Run phải có model generation trên GPU thực; chỉ có câu trả lời baseline
không được tính là đã thử hướng B. Quyết định A/B cuối vẫn do Evaluation/Integration.

`acceptance.json` tách thời gian model load, phần theo câu và catalog theo business;
đồng hồ subprocess gồm cả startup/import và load model. Khi biết quy mô private,
truyền cả `--private-questions N --private-businesses B` để có phép chiếu tuyến tính.
Đây là ước tính theo phân bố workload/hardware tương đương, không bảo đảm cho input
chưa biết. Không tự thuê cloud hoặc phát sinh chi phí. Trên máy chưa có CUDA/weights,
preflight báo thiếu và dừng; không chạy fallback rồi ghi nhận thành đo model.

Dùng đúng manifest holdout team đã cố định, seed 42, validation fraction 0.2; nếu
chưa có manifest thì Evaluation tạo một lần theo `EXPERIMENTS.md`. Không chọn seed
hoặc threshold theo validation/test. Chạy A/B trên cùng questions/rulebook/calendar
và cây dữ liệu, mỗi run ở thư mục mới:

```sh
uv run --no-sync datamate benchmark --checkout . --data-dir data/raw/train --holdout outputs/business_split.json --config configs/local_plan.toml --output-dir outputs/runs/agent02-b-benchmark
```

Benchmark gọi trực tiếp Python môi trường đã chuẩn bị, không sync/download trong
subprocess inference. A dùng `configs/baseline.toml`. Hai hướng có config khác nhau,
nên không dùng `--compare-to` vốn yêu cầu config giống nhau; Evaluation đối chiếu
outcomes theo ID cùng manifest và ghi rõ config/backend khác nhau.

Bàn giao thư mục run gồm `run.json`, `logs/`, metrics, outcomes, handoff và checksum.
Log chứa model revision, manifest SHA256, runtime Torch/Transformers/CUDA/GPU, prompt
checksum, JSON model output và code/output của bộ tính. Prompt có thể tái tạo từ
input/config/revision cố định; không lưu raw data vào Git. Báo cáo correct/wrong/
abstain, PA **local proxy**, coverage, từng ID chuyển sang sai, số timeout và câu
model thực sự trả lời. Không suy luận chất lượng model từ demo baseline.

Đo toàn bộ trên GPU tham chiếu, dùng `run.json.elapsed_seconds` (gồm nạp model),
`models[].load_execution` và thời gian từng câu. Chạy lại hai lần, so submission;
greedy + deterministic flags chưa bảo đảm tái lập giữa runtime/hardware khác nhau.
Ước tính private test phải dùng số câu/business và workload thực tế; chưa biết quy
mô thì không thể khẳng định dưới 6 giờ. `max_seconds=20000` được kiểm tra giữa câu,
không phải hard deadline của toàn pipeline; runner benchmark có process deadline.
Threshold chỉ được chọn bằng business train rồi kiểm lại validation một lần.

## Phần Plan còn cần Semantics + Data thống nhất

Đề xuất cụ thể cho boolean tăng doanh thu liên tục và table công nợ nằm ở
[AGENT_02_CONTRACT.md](AGENT_02_CONTRACT.md), chưa sửa grammar hay shared contract.

SEM-01 #18 đã thêm `Filter` và các trường kỳ so sánh; dùng lại contract đó. Các khả
năng tiếp theo bên dưới vẫn là đề xuất, chưa viết intent mới trong AGENT-02:

| Thành phần | Đề xuất và điều kiện |
|---|---|
| Bộ lọc | Danh sách predicate trên product_id/category/payment_channel với op trong allowlist và giá trị giữ kiểu gốc; adapter xác nhận cột/đơn vị/nguồn |
| So sánh kỳ | Hai kỳ có tên và metric đã xác định; không thay nghĩa `start/end` hiện tại; quy định rõ mẫu số 0, thiếu coverage và làm tròn |
| Boolean | Biểu thức so sánh hai giá trị thực thi hoặc ngưỡng trích từ câu hỏi; JSON bool thật, không string `"true"` hay số 0/1 |
| Table | Projection/group_by/aggregation có tên cột, kiểu/đơn vị, sắp xếp/top_k và tie rule tường minh; output `{"columns":[...],"rows":[[...]]}` theo scorer hiện có |
| Evidence | Nguồn luật và lineage adapter cho mỗi predicate, metric, kỳ; code/call/output của toàn biểu thức |
| Unsupported | Điều kiện, unit, join, null/tie/zero-denominator chưa có luật → abstain |

Giữ tương thích với callers hiện tại, thống nhất các phần chưa có trong contract
trước khi triển khai. Semantics cần định nghĩa parsing và nguồn luật, Data cần xác nhận
schema/join/units, Agent sẽ tính và kiểm kết quả. Bộ kiểm tra format boolean/table
hiện có không được coi là đã trả lời được những câu hỏi này.
