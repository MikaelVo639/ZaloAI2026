# Đợt phát triển đầu tiên

Mục tiêu: tăng số câu trả lời có bằng chứng trên train, giữ lỗi thực thi là
`abstain` và giữ inference offline. Giữ nguyên interfaces trong `contracts.py`;
nếu cần mở rộng phải thống nhất với các module sử dụng trước khi ghép code.

| Việc | Phụ trách | Review | Điều kiện bàn giao |
|---|---|---|---|
| INT-01: cổng tích hợp | @MikaelVo639 | @baoduong2342007 | lockfile, smoke/tests Windows/Linux, hai lượt Docker offline khớp kết quả, input read-only, đủ logs; cho phép Integration merge trực tiếp, không bật branch protection |
| EVAL-01: baseline và phân nhóm abstain | @baoduong2342007 | @MikaelVo639 | seed 42, validation fraction 0.2, manifest/checksum cố định; correct/wrong/abstain, PA local, coverage, runtime và nhóm reason/error; artifact riêng cho mỗi run |
| DATA-01: adapter order/detail/product | @longlth2297 | @tqhuy0602 | chọn một schema train từ nhóm lỗi adapter; đọc README; join có kiểm tra cardinality, khóa thiếu/trùng, trả một phần loại cả đơn theo luật split; đối chiếu tiền trước/sau, giữ cột/đơn vị/id nguồn; có regression |
| SEM-01: mở rộng grammar từ lỗi train | @Kurosuz | @longlth2297 | chọn nhóm lớn trong báo cáo EVAL-01; Plan có nguồn luật, thời gian lấy as_of_date; regression ranh giới ngày và luật split thay đổi; bộ lọc chưa hỗ trợ vẫn abstain |
| AGENT-01: thực thi và giới hạn thời gian | @tqhuy0602 | @Kurosuz | kết nối adapter/Plan đã thống nhất; phép tính có code/query và kết quả trong evidence; worker có deadline, timeout/error thành abstain; regression phép tính và timeout |

EVAL-01 cung cấp nhóm lỗi để chọn phạm vi DATA-01 và SEM-01. Agent có thể chuẩn
bị cơ chế deadline trước, tích hợp sau khi adapter/Plan sẵn sàng. Không bắt buộc
thêm model; nếu thêm phải hoàn tất khai báo release date, licence, tổng tham số
và cách load offline trong `models/README.md` trước.

Mỗi việc mở một nhánh `codex/<module>-<mo-ta>`, PR nhỏ kèm ca tái hiện và lệnh
kiểm tra. Chạy `uv sync --locked`, `uv run datamate smoke`,
`uv run python -m unittest discover -s tests -v` trước khi bàn giao. So sánh thuật
toán trên cùng holdout; không dùng public/private test để chọn ví dụ hoặc tối ưu.

Theo [EXPERIMENTS.md](EXPERIMENTS.md), kết quả riêng của checkout ở `outputs/`,
không commit raw data, nhãn, logs, metrics chi tiết hoặc đường dẫn máy vào Git.
Holdout tạo sau khi đã xem dữ liệu không được gọi là đánh giá mù.

## Theo dõi trên GitHub

- [INT-01 / #1](https://github.com/MikaelVo639/ZaloAI2026/issues/1)
- [EVAL-01 / #2](https://github.com/MikaelVo639/ZaloAI2026/issues/2)
- [DATA-01 / #3](https://github.com/MikaelVo639/ZaloAI2026/issues/3)
- [SEM-01 / #4](https://github.com/MikaelVo639/ZaloAI2026/issues/4)
- [AGENT-01 / #5](https://github.com/MikaelVo639/ZaloAI2026/issues/5)
