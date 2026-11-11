# AGENT-02 — prototype hướng B và bàn giao

Phạm vi @tqhuy0602, reviewer @Kurosuz. Nhánh `codex/agent-local-plan-prototype`.
Trạng thái ngày 06/11/2026: đã tích hợp EVAL-02 #19, INT-02 #21 và SEM/DATA #22,
có regression tổng hợp và quy trình benchmark train cố định; chưa có weights/GPU.
Không coi test với
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
| Holdout/threshold/GPU 6 giờ | Baseline có benchmark train; model vẫn chờ GPU/weights và quy trình Evaluation/Integration bên dưới |

Đã tích hợp SEM-01 #18 và contract SEM/DATA #22: kỳ so sánh, bộ lọc sản phẩm/nhóm,
chuỗi doanh thu nhiều tháng, tháng tốt nhất, top khách, số đếm theo ngưỡng và số
lượng theo đơn vị. Agent dùng chung đường lập Plan deterministic, gồm tên hàng và
vocabulary nhóm, trước khi gọi model. Validation kiểm đúng kiểu, độ dài chuỗi,
tháng thuộc kỳ yêu cầu và giới hạn số đếm. Tỷ lệ huỷ đơn và tỷ lệ khách trong [0,100];
biến động/tỉ trọng doanh thu có thể âm hoặc trên 100. Kỳ trùng/chồng lấn, ngưỡng số
đơn lẻ và trường so sánh bị bỏ qua đều abstain. Model vẫn chỉ sinh Plan đơn kỳ không lọc.
Thay đổi giao thoa:
pipeline/CLI đăng ký backend và các timeout theo cấu hình; manifest ghi model thực
sự đã nạp; CLI override dùng `dataclasses.replace` để giữ các trường mới. Pipeline
đóng worker do nó tạo kể cả khi ghi output lỗi. Probe deadline của Evaluation được
bổ sung split giả để tiếp tục chạy sau bản sửa cache.

## Cách prototype quyết định

1. Grammar hiện tại (kể cả so sánh kỳ và thử lại với tên sản phẩm từ worker) lập được
   Plan thì chạy baseline và giữ confidence của baseline, không gọi model.
2. Câu còn lại chỉ đi tới model nếu rulebook của split bật được phép tính và parser
   thời gian hiện tại xác định đúng một kỳ dương lịch. Schema giữ tên cột và mẫu
   nguồn, mô tả kiểu/ý nghĩa/đơn vị theo adapter README; không đoán đơn vị.
   Mô tả schema/units dùng DATA-03 `describe_schema`, chạy một lần mỗi business
   trong worker có deadline, bỏ business id khỏi payload. Luật có thêm cấu trúc
   `parse_rulebook` của SEM-02 bên cạnh nguyên văn của split; không cắt ngầm schema/luật.
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
mặc định 32.768 của snapshot. Trước tích hợp DATA-03/SEM-02, tokenizer thật xác nhận 5 prompt business train sau
khi scope calendar còn 9.250–18.852 token; trước sửa là 45.541–55.065. Đây chỉ là
kiểm tra đầu vào bằng tokenizer, không phải model generation/benchmark GPU. Prompt
mới có schema/luật có cấu trúc đã kiểm lại trên CPU với đúng 5 business train đó:
**19.626–22.296 input token**, đều dưới 24.576. Đây vẫn là tokenizer-only, không phải
generation/GPU. Vượt budget thì abstain, không cắt ngầm luật hoặc schema.
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
subprocess inference. A dùng `configs/baseline.toml`. Khi so hai config, EVAL-02 hỗ trợ
`--compare-to <run-A> --allow-config-difference`; dữ liệu, holdout, nhãn và
version/checksum scorer vẫn phải khớp. Acceptance kiểm cùng các identity này.

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

## Hướng R: model chỉ viết lại câu hỏi (`configs/rewrite.toml`)

Backend thử nghiệm `rewrite` (`src/datamate/agent/rewrite.py`) dùng cùng model Qwen,
manifest và cách nạp offline như trên, nhưng model **không lập Plan**:

1. Ngữ pháp hiểu câu hỏi thì chạy baseline, không gọi model.
2. Ngữ pháp không hiểu: chọn 6 ví dụ gần nhất (trigram ký tự sau khi bỏ dấu) trong
   `EXAMPLES`, model viết lại câu theo cách viết chuẩn của ví dụ, hoặc trả `KHONG_RO`.
   Prompt chỉ có câu hỏi và ví dụ, không có id, business id, rulebook hay dữ liệu.
3. Câu viết lại phải giữ đúng mọi số (sau khi bỏ dấu tách nghìn, đổi k/triệu; chữ "một"
   có thể thay số 1), mọi tên trong danh mục, và các từ mang nghĩa (phủ định, huỷ, trả
   hàng, %, so với, trung bình, nhất, nhân viên, khách, nhà cung cấp, nhập, tồn, nợ...).
4. Ngữ pháp lập Plan từ câu viết lại theo rulebook của split hiện tại. Hai lần viết lại
   (ví dụ theo thứ tự gần trước và gần sau) phải ra cùng một Plan.
5. Bộ tính hiện có trả lời; confidence cố định 0,5. Đường này không bao giờ trả
   `unanswerable`. Lỗi, timeout, không chắc hoặc không qua kiểm tra đều là `abstain`.

`EXAMPLES` do nhóm tự viết với tên, mã, ngày và số tiền bịa; không lấy câu train,
public hay private. Mỗi câu chuẩn phải được ngữ pháp lập Plan với rulebook train:

```sh
uv run python scripts/check_rewrite_examples.py --split data/raw/train
```

Trên máy GPU đã chuẩn bị (xem phần trên), đo trên train và các biến thể robustness, so
với baseline cùng dữ liệu. Không chỉnh prompt hay ví dụ theo public.

```sh
uv run --no-sync datamate benchmark --checkout . --data-dir data/raw/train --holdout outputs/business_split.json --config configs/rewrite.toml --output-dir outputs/runs/rewrite-train
uv run --no-sync python scripts/robustness_eval.py --data-dir data/raw/train --output-dir outputs/robustness/rewrite --config configs/rewrite.toml
```

Chỉ đề xuất bật khi số câu đúng thêm lớn hơn rõ rệt số câu sai thêm, và liệt kê từng
câu chuyển sang sai.

## Câu không trả lời được (`agent/absence.py`, `semantics/attributes.py`)

Chỉ một câu đang `abstain` mới có thể đổi thành `unanswerable`, và chỉ khi rulebook của
split có dòng "shop không ghi loại thông tin câu hỏi cần thì không trả lời được". Ba
đường:

1. Câu đã có Plan (tồn kho, nhập hàng, giá vốn, hạn dùng) mà tính không được, và shop
   **không có dấu vết nào** của loại thông tin đó: không dòng README, tên file, tên sheet
   hay tiêu đề cột *có giá trị* nào chứa từ của khái niệm. Danh sách từ rộng có chủ ý.
   Ngưỡng đặt hàng ("Tồn lớn nhất", "Định mức tồn tối thiểu") không tính là tồn kho.
2. Kỳ hỏi nằm hẳn trước ngày bán đầu tiên, khi shop bán ít nhất 5/7 ngày đầu (bán hằng
   ngày nên ngày đầu là ngày bắt đầu dữ liệu), theo dòng "kỳ nằm hoàn toàn ngoài khoảng
   ngày có dữ liệu".
3. Câu grammar không lập Plan nhưng hỏi một loại thông tin cố định (giới tính khách, địa
   chỉ khách, phẩm chất nhân viên, lý do huỷ/trả, liều dùng) mà shop không có dấu vết.
   Chạy trước model rewrite để model không biến câu thành câu doanh thu.

Lỗi tính toán hay thiếu adapter không bao giờ được coi là bằng chứng không trả lời được.

## Luật xếp hạng có tên (`semantics/rule_spec.py`, `agent/rule_rank.py`)

Mỗi split, một lần: mỗi dòng rulebook có tên dạng "… nhất" mà `read_rules` **chưa** nhận
được đọc thành một JSON cố định (`doi_tuong`, `chi_tieu`, `chon`, `don_hop_le`,
`dieu_kien`, `cach_goi`, `nguon`) chỉ dùng giá trị trong danh sách cho phép. Dòng
`read_rules` đã nhận (luật có code viết tay, kiểm bằng nhãn train) không bao giờ bị đọc lại.

- **Đối tượng lấy từ định nghĩa**: chủ ngữ mở đầu ("khách có …", "ngày có …"). Tên chỉ
  phân xử "người có …". Tên và định nghĩa chỉ hai đối tượng khác nhau, hoặc không có chủ
  ngữ rõ, thì tắt.
- **Kiểm chiều ngược**: phần nào hiểu được thì lấy ra khỏi định nghĩa; còn sót từ mang
  nghĩa (huỷ, trả, ngày, số, lãi, lượt, phiếu, bảo hành, kể cả, không …) thì tắt.
  `ground` thêm: mọi giá trị có chỗ dựa trong chữ của dòng, và mọi từ nội dung của định
  nghĩa được một trường giải thích (áp cho cả JSON do model điền).
- Mọi luật (bật/tắt, lý do) ghi trong `run.json` mục `rule_specs`;
  `scripts/rule_specs.py --split <split>` in cùng báo cáo. Xem báo cáo của split khác train
  là được; sửa bộ đọc theo nó là chỉnh theo split đó.
- Câu hỏi chỉ dùng luật khi grammar không lập được Plan, câu nêu đúng một `cach_goi` của
  một luật đang bật và một kỳ; chữ còn lại chỉ được là từ hỏi/lịch sự.
- Bộ tính chung (`rule_rank.py`) nhóm theo đối tượng, đo theo chỉ tiêu, lọc theo điều kiện,
  xếp theo chọn; bằng nhau theo dòng "bằng nhau" của rulebook, không có dòng đó (hay xếp
  ngày) mà hoà ở vị trí cắt thì abstain. Khung giờ chưa có format đáp án nên đang tắt.
- **Model** (`configs/rewrite.toml`, `agent/rule_spec_model.py`): một lần mỗi split, với dòng
  xếp hạng bộ đọc code để tắt, model điền JSON từ hai prompt khác thứ tự; hai JSON phải
  giống hệt và qua `ground` mới bật. Nhật ký trong `run.json` mục `rule_spec_model`. Bộ tính
  nhận spec qua worker và kiểm lại: `nguon` phải là dòng nguyên văn của rulebook đang chạy
  và vẫn qua `ground`.
- **Viết lại câu hỏi**: tên các luật đang bật được đưa vào prompt viết lại làm câu đích
  ("<tên luật> <kỳ> là ai?"); câu viết lại vẫn qua mọi kiểm tra cũ và phải được grammar
  hoặc bộ đọc luật lập Plan giống nhau ở hai lần viết.
- Bài thử giấu luật: `scripts/hidden_rule_check.py` bỏ lần lượt từng dòng được câu trả lời
  train trích dẫn và trả lời lại; không câu nào được thành sai (53 dòng, 0 câu sai mới).

## Phần Plan còn cần Semantics + Data thống nhất

Contract C01 cho boolean tăng doanh thu liên tục, table công nợ và nguồn công nợ
nằm ở [AGENT_02_CONTRACT.md](AGENT_02_CONTRACT.md). `contracts.py` đã có kiểu dữ liệu
công nợ và hằng số dùng chung; grammar (S01), adapter (D01) và executor (A01) chưa làm,
nên các câu đó vẫn abstain.

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
