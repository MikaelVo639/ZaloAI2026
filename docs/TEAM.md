# Phân công và giao tiếp trong team

Năm vai trò dưới đây là phạm vi sở hữu module. Team tự gán thành viên vào vai trò,
chọn người review dự phòng và cập nhật assignee trên issue/PR khi nhận việc.

## Thành viên và review

| Người | GitHub | Vai trò | Reviewer chính |
|---|---|---|---|
| 1 | @MikaelVo639 | Integration / quản lý repo | @baoduong2342007 |
| 2 | @longlth2297 | Data & Schema | @tqhuy0602 |
| 3 | @Kurosuz | Semantics | @longlth2297 |
| 4 | @tqhuy0602 | Agent | @Kurosuz |
| 5 | @baoduong2342007 | Evaluation | @MikaelVo639 |

Danh sách việc và điều kiện bàn giao nằm trong [BACKLOG.md](BACKLOG.md).
`CODEOWNERS` tự đề nghị reviewer theo module; người phụ trách vẫn phải yêu cầu
review giao thoa khi đổi luật, phép tính, join hoặc contract. Tác giả không tự
duyệt PR của mình. Các tests dùng chung do Evaluation điều phối, chủ module bổ
sung regression cùng thay đổi của mình.

| Vai trò | Sở hữu | Đầu vào -> đầu ra | Người review phù hợp |
|---|---|---|---|
| Integration | `pipeline.py`, `cli.py`, scripts, config, Docker, CI | các module -> submission/log có thể chạy lại | Evaluation |
| Data & Schema | `ingestion/` | file gốc + README -> Catalog/adapter có nguồn | Agent |
| Semantics | `semantics/` | Question + luật của split -> Plan | Data & Schema |
| Agent | `agent/` | Plan + dữ liệu -> Candidate + evidence | Semantics |
| Evaluation | `evaluation/`, tests | Candidate/submission + nhãn train -> kiểm tra và báo cáo | Integration |

Review giao thoa theo vấn đề: join cần Data & Schema, luật nghiệp vụ cần Semantics,
phép tính cần Agent, ảnh hưởng điểm cần Evaluation. Đổi contract cần các vai trò
sử dụng contract cùng xem PR; vai trò sở hữu không có nghĩa chỉ một người được sửa.

## Việc khởi đầu

| Vai trò | Việc ưu tiên | Điều kiện hoàn thành |
|---|---|---|
| Integration | xác nhận checkout mới, scripts và Docker | một lệnh smoke chung; Docker đọc input read-only, đủ output/log |
| Data & Schema | tạo catalog rồi thêm adapter order/detail/product | join không nhân dòng; có ca thiếu khóa, trùng khóa, đơn trả |
| Semantics | mở rộng nhóm câu đang abstain | Plan có nguồn luật; ca ranh giới ngày và điều kiện chưa hỗ trợ |
| Agent | tích hợp backend tính toán hoặc model local đúng thể lệ | kết quả từ thực thi; code/query trong evidence; worker có deadline |
| Evaluation | cố định business holdout và nhóm lỗi | báo cáo đúng/sai/bỏ qua; so trước/sau cùng dữ liệu và config |

## Khi giao việc cho coding assistant

Luôn chỉ rõ module, hành vi cần đổi, contract giữ nguyên hay cần sửa, ca tái hiện
và lệnh xác nhận. Yêu cầu đọc `AGENTS.md`, không đưa answers vào inference và không
hard-code id. Thay đổi ở vai trò khác phải được ghi rõ trong báo cáo/PR.

Ví dụ: “Thêm adapter cho export tách order/detail trong ingestion. Giữ raw data,
kiểm tra cardinality và tổng tiền trước/sau join, thêm fixture nhỏ cho duplicate
và đơn trả. Giữ Catalog/Agent contract; chạy smoke và các tests liên quan.”

## Vòng phối hợp

Mỗi thay đổi thuật toán bắt đầu bằng lỗi cụ thể hoặc giả thuyết đo được. Cùng xem
câu sai/abstain, giao về đúng module, rồi đối chiếu cùng business holdout. Ghi quyết
định trong issue/PR để người khác tiếp tục được mà không cần lịch sử chat cá nhân.
