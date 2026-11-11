# Quy trình kiểm tra

Các bước này dùng cho mọi checkout. Báo cáo một lượt chạy cụ thể được lưu trong
thư mục output của lượt đó, theo `docs/EXPERIMENTS.md`.

## Kiểm chứng nhanh một benchmark train đã hoàn tất

Trong checkout đã cài môi trường riêng, chạy bằng Python của chính checkout đó:

```sh
python scripts/verify_candidate.py --data-dir data/raw/train --run-dir outputs/candidate --output-dir outputs/candidate-checks
```

Lệnh kiểm tra đường dẫn import, checksum mã nguồn/data/config/holdout/scorer/nhãn,
đầy đủ ID/log, đáp án log khớp submission và chấm lại metrics toàn train/validation.
Lỗi checksum, log hoặc điểm không khớp trả exit code 1.
Chọn thư mục output mới cho mỗi lần kiểm tra.

`verification.json` ghi kết quả và thời gian. `needs-review.csv` liệt kê từng câu
còn sai hoặc abstain để kiểm tra nguồn. Kết quả integrity qua vẫn ghi
`absence_audit_complete=false` và `repeat_verified=false`.

Thêm `--repeat` để chạy lại inference độc lập với config/holdout của benchmark,
sau đó dùng `verify-runs` so đầy đủ đáp án theo ID và log của hai lượt.
Tiến độ predict nằm trong `<output-dir>/repeat/predict.stderr.txt`.
Lỗi inference hoặc đáp án khác nhau làm lệnh thất bại và ghi lý do trong báo cáo.
Trên máy đo ngày 11/11/2026, kiểm tra nhanh mất khoảng 1 giây, inference đủ 3.000 câu
mất khoảng 28 phút. Rà soát thiếu dữ liệu cần kiểm tra từng nguồn trong CSV,
ghi rõ thông tin cần cho câu hỏi và bằng chứng nguồn phát hành không cung cấp.

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

### Nhãn train nghi lỗi

Một câu trả lời đúng luật nhưng lệch nhãn train vẫn được tính là **sai** trong metrics;
scorer không loại ID nào. Nhóm chỉ chấp nhận câu đó khi có đủ ba điều: rulebook của split
quy định rõ cách tính, dữ liệu kiểm lại được theo đúng luật đó, và các câu cùng loại trên
train đều khớp nhãn theo cùng cách tính. Ghi câu vào bảng dưới, báo BTC, và A/B vẫn phải
0 câu sai mới ngoài các ID trong bảng.

| ID | Shop | Câu hỏi | Hệ thống trả | Vì sao giữ |
|---|---|---|---|---|
| `q_02eb42db6665` | `biz_125` | "tháng 5/2025 hàng nào bán chạy nhất" | Bia Sài Gòn Special lon | Xem dưới |

`q_02eb42db6665`: luật "Bán chạy" tính theo số lượng quy về đơn vị cơ bản, không theo
số dòng hay số tiền, bỏ đơn huỷ và trả. Theo cột "Số lượng lẻ" (README: đã đổi ra đơn
vị cơ bản), các đơn "Đã hoàn thành" tháng 5/2025 có Special 303 lon, xếp trên mặt hàng
nhãn chọn (300 lon). Nhãn chỉ khớp khi đếm số dòng, số tiền hoặc số lượng chưa quy đổi.
Trên 58 câu "bán chạy" train mà hai cách xếp (quy đổi và chưa quy đổi) khác nhau, 57 câu
khớp cách quy đổi, không câu nào khớp cách chưa quy đổi. Kiểm lại:

```sh
python - data/raw/train/data/biz_125/ban_hang.csv <<'PY'
import csv, collections, sys
total = collections.Counter()
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8-sig")):
    day, month, year = r["Ngày bán"].split("/")
    if (month, year) == ("05", "2025") and r["Trạng thái xử lý"] == "Đã hoàn thành":
        total[r["Hàng bán"]] += int(r["Số lượng lẻ"])
print(total.most_common(3))
PY
```

## Docker với network tắt

Tạo demo trước bằng smoke command, sau đó build:

```sh
docker build --platform linux/amd64 -t datamate-starter .
```

Linux/macOS:

```sh
mkdir -p outputs/docker
docker run --rm --network none --shm-size=16g --entrypoint bash -v "$PWD/data/demo:/data:ro" -v "$PWD/outputs/docker:/result" datamate-starter /code/predict.sh
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force outputs/docker
docker run --rm --network none --shm-size=16g --entrypoint bash --mount "type=bind,source=$((Get-Location).Path)/data/demo,target=/data,readonly" --mount "type=bind,source=$((Get-Location).Path)/outputs/docker,target=/result" datamate-starter /code/predict.sh
```

Kiểm tra kết quả:

```sh
uv run datamate validate outputs/docker/submission.jsonl --questions data/demo/questions.jsonl
uv run datamate evaluate outputs/docker/submission.jsonl --reference data/demo/answers.jsonl --output outputs/docker/metrics.json
```

Đối chiếu submission giữa hai lượt chạy cùng image/input, kiểm tra logs và thời gian.
Job Docker trong CI dùng input read-only, `--network none` và đúng lệnh
`bash /code/predict.sh` của guideline. Với image GPU cuối, chạy thêm hai GPU
(`--gpus '"device=0,1"'`) trên máy có driver tương thích RTX 5090.
Trạng thái build/run
của một image phải lấy từ log CI hoặc báo cáo run, không suy ra từ việc có Dockerfile.

CI chạy image hai lần vào hai thư mục output mới và xác nhận bằng:

```sh
uv run python scripts/check_demo_output.py --data-dir data/demo outputs/docker/first outputs/docker/second
```

Lệnh kiểm tra đủ ID, mốc 21 đúng/0 sai/3 abstain của fixture, đủ log khớp đáp án,
không có lỗi thực thi, checksum input và kết quả hai lượt giống nhau. Scorer chỉ
đọc nhãn khi kiểm tra bên ngoài container inference.
