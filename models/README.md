# Model manifest

Backend mặc định `baseline` không dùng model: tổng tham số = 0.
Backend thử nghiệm `local_plan` dùng model bên dưới, chỉ bật bằng config riêng.
Chưa có weights trong repo, chưa fine-tune và chưa có kết quả model trên holdout/GPU.

Trước khi thêm model, cập nhật README chính và ghi cho mỗi thành phần:

- tên/revision và URL nguồn;
- ngày phát hành weights (phải trước 26/10/2026 theo Rule 2 của DataMate);
- licence và số tham số đầy đủ;
- định dạng/quantization, checksum weights;
- cách tải trong giai đoạn chuẩn bị image, đường dẫn offline khi inference;
- dữ liệu, code và cấu hình training nếu fine-tune.

Tổng model inference (LLM, embedding, reranker, classifier...) không quá 15 tỷ
tham số, kể cả dùng lần lượt. Không tải model trong `predict.sh`.
Weights cần COPY vào image cuối trước khi đóng băng solution; `.gitignore` chỉ
giúp tránh đưa binary vào Git, không tự đưa weights vào Docker.

## AGENT-02: model được chọn để thử nghiệm

| Trường | Khai báo |
|---|---|
| Model | [Qwen/Qwen2.5-Coder-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct) |
| Revision cố định | `c03e6d358207e414f1eca0bb1891e29f1db0e242` (12/01/2025, cập nhật model card) |
| Weights công khai | **19/09/2024**, theo [thông báo gốc của Qwen](https://qwenlm.github.io/blog/qwen2.5-coder/); không nhầm với đợt bổ sung kích thước model tháng 11 |
| Licence | Apache-2.0, lưu kèm `LICENSE` trong snapshot |
| Tổng tham số | **7,61 tỷ**, gồm embedding; không dùng thêm embedding/reranker/model khác |
| Định dạng | Safetensors BF16 gốc, không quantize, không fine-tune |
| Vai trò | Câu hỏi + schema mẫu + README + rulebook + calendar của split → JSON Plan giới hạn; số liệu do Python thực thi |
| Trạng thái | Prototype; chưa chọn thay grammar, chưa hiệu chỉnh confidence |

Số tham số và licence lấy từ [bảng model chính chủ](https://qwenlm.github.io/blog/qwen2.5-coder-family/).
Revision dùng [commit cố định](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/commit/c03e6d358207e414f1eca0bb1891e29f1db0e242).
Weights khoảng 15,2 GB; RAM/VRAM thực tế phải đo cả KV cache và load model.

### Chuẩn bị và nạp offline

Chỉ thực hiện trên máy chuẩn bị image có network; không gọi bước này từ inference:

```sh
uv sync --locked
uv pip install --python .venv -r requirements-model.txt
uv run --no-sync python scripts/prepare_agent_model.py --output models/qwen2.5-coder-7b-instruct
```

Script tải đúng revision, ghi SHA256 từng file và khai báo vào `model_manifest.json`
ở thư mục weights bị Git ignore. Chưa tải weights thì chưa có checksum để công bố;
không dùng checksum giả. Integration lưu manifest này cùng artifact image, pin toàn
bộ runtime đã kiểm nghiệm trên GPU và COPY cả thư mục vào image, ví dụ `/models/qwen`.
Không đặt weights trong split `/data`.

`local_plan` chỉ nhận thư mục tồn tại, xác minh manifest/checksum rồi gọi
`from_pretrained(..., local_files_only=True, trust_remote_code=False, use_safetensors=True)`.
Worker đặt `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` trước khi import runtime.
Attention dùng SDPA với deterministic algorithms được bật; kernel, VRAM và kết quả
lặp lại cần xác minh trên GPU thực. Config thử nghiệm giới hạn 24.576 input + 1.024
output token; tokenizer-only kiểm tra trên CPU không được tính là đã chạy model.
Xem [hướng dẫn offline chính chủ](https://huggingface.co/docs/transformers/v4.46.3/installation#offline-mode).
Model load và generate đều có deadline; timeout/error luôn `abstain`.
Worker là cơ chế deadline, không phải sandbox thực thi Python bất kỳ; prototype
không chạy code do model sinh. Cần container network tắt của Integration để xác nhận
isolation ở mức OS. Hướng dẫn chạy và phần còn chờ ở [docs/AGENT_02.md](../docs/AGENT_02.md).
