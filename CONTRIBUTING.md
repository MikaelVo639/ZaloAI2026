# Quy ước đóng góp

## Một cách chạy chung

Dùng Python 3.12 và dependencies từ `uv.lock`. Trước khi nhận việc, chạy:

```sh
uv sync --locked
uv run datamate smoke
uv run python -m unittest discover -s tests -v
```

Các lệnh giả định đang ở thư mục gốc checkout. Không đưa đường dẫn máy, tên tài khoản,
trạng thái cài đặt cá nhân hoặc kết quả từ một phiên làm việc vào hướng dẫn chung.

## Nhận việc và mở nhánh

Mỗi issue nêu module, vấn đề, đầu vào/đầu ra và điều kiện hoàn thành. Gán một người
phụ trách và một người review; phân vai tham khảo `docs/TEAM.md`.

Tạo nhánh từ `main` đã cập nhật. Dùng tên mô tả với tiền tố `codex/`, ví dụ
`codex/ingestion-order-join`, `codex/semantics-week-window`, `codex/ci-smoke`.
Một PR giải quyết một thay đổi có thể
kiểm tra độc lập; không trộn sửa format toàn repo vào thay đổi thuật toán.

## Nội dung PR

Nêu hành vi trước/sau, module bị ảnh hưởng và lệnh kiểm tra đã chạy. Với thay đổi
phép tính hoặc cách hiểu luật, thêm ca tái hiện nhỏ và báo cáo trên cùng holdout.
Dùng template PR có sẵn; báo cáo dài theo `docs/EXPERIMENTS.md`.

Contract chung: `src/datamate/contracts.py`. Khi đổi schema, format metadata, backend
hoặc cấu hình, liệt kê nơi dùng và cách chuyển đổi. Người viết và người review
thống nhất cách ghép trước khi sửa phần dùng đầu ra đó.

## Điều kiện ghép vào main

- Smoke và tests qua; CI bắt buộc của repo qua khi chạy trên remote.
- Một thành viên khác review code và bằng chứng tính toán liên quan.
- Không có dữ liệu BTC, logs, credentials, weights hoặc đường dẫn cá nhân trong diff.
- Đổi dependencies phải cập nhật `pyproject.toml`, `uv.lock`, `requirements.txt` tương ứng.
- Không hard-code đáp án theo question/business id; không dùng public/private test để học.
- Backend inference hoạt động offline. Lỗi thực thi dẫn đến abstain, không tự suy ra unanswerable.

Người tích hợp ghép PR sau khi các điều kiện trên đạt. Việc bảo vệ nhánh và quyền
merge được team cấu hình trên dịch vụ Git đang dùng.

Cấu hình bảo vệ `main`: yêu cầu các checks `python (ubuntu-latest)`,
`python (windows-latest)` và `docker`; nhánh phải cập nhật với main; ít nhất một
approval và review của CODEOWNER; huỷ approval cũ khi có commit mới; giải quyết
hết review conversations. Áp dụng cả admin; không force-push hoặc xoá main.
`CODEOWNERS` trên nhánh mặc định mới có hiệu lực tự yêu cầu review, nên PR đầu
tiên thêm file này cần yêu cầu reviewer thủ công. Không tự duyệt thay thành viên.
