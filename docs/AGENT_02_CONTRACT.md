# Đề xuất contract boolean/table — chờ Semantics và Data xác nhận

Trạng thái: đề xuất, **chưa triển khai vào `contracts.py` hay grammar**.
Liên quan SEM-02 #15, AGENT-02 #16, DATA-03 #14. Giữ nguyên SEM-01 #18.

## Phạm vi cần chốt

Khảo sát câu hỏi chỉ trong business train của manifest seed 42/0.2 cho thấy hai nhu
cầu cụ thể: boolean kiểm tra doanh thu tăng liên tục qua nhiều tháng và table xếp
hạng khách còn nợ. Đây là dữ liệu đã xem, không phải đánh giá mù. Ví dụ dưới đây
là câu tổng hợp để mô tả contract, không kèm nhãn hoặc câu/ID lấy từ test.

- Boolean: “Doanh thu tháng 1, 2 và 3 năm 2026 có tăng liên tục không?”
- Table: “Lập bảng 2 khách còn nợ nhiều nhất tính đến hết ngày 12/04/2026, kèm số tiền nợ.”

Không dùng một cờ `answer_type` để đổi số thành bool hoặc biến list thành table;
kiểu đầu ra phải tương ứng phép tính và nguồn dữ liệu đã xác định.

## Boolean: doanh thu tăng liên tục

SEM/DATA #22 đã thêm `Plan.periods: tuple[tuple[date, date], ...] = ()` cho chuỗi
doanh thu/tháng tốt nhất. Tái dùng trường này, không thêm một cấu trúc `Period`
cạnh tranh. Đề nghị thêm giá trị intent `revenue_increasing` sau khi chốt luật.
Các callers tạo Plan cũ vẫn dùng được; mọi backend phải abstain nếu `periods` được
đặt nhưng nó chưa thực thi trường đó. Không thay nghĩa của `comparison` từ #18.

Ví dụ Plan bằng ký pháp JSON (không phải định dạng wire bắt buộc):

```json
{
  "intent": "revenue_increasing",
  "start": "2026-01-01",
  "end": "2026-03-31",
  "periods": [
    {"start": "2026-01-01", "end": "2026-01-31"},
    {"start": "2026-02-01", "end": "2026-02-28"},
    {"start": "2026-03-01", "end": "2026-03-31"}
  ],
  "comparison": "",
  "filters": []
}
```

Semantics trích đủ các kỳ theo đúng thứ tự thời gian và gắn nguồn luật của split.
Agent kiểm tra ít nhất hai kỳ, không trùng/chồng lấn, không kỳ tương lai, `start/end`
khớp biên của `periods`, không field khác bị bỏ qua. Mỗi kỳ qua cùng cổng coverage
như doanh thu hiện tại. Không có coverage chứng minh được thì abstain.

Đề nghị “tăng liên tục” nghĩa là **mọi kỳ sau lớn hơn kỳ ngay trước**, bằng nhau là
false. Cần Semantics xác nhận từ ngữ và Data xác nhận so sánh tổng Decimal chưa làm
tròn trung gian; kết quả tiền khi xuất riêng vẫn theo luật làm tròn của split.
Đầu ra boolean là JSON `true`/`false`, không 0/1 hoặc chuỗi. Evidence có code và
danh sách từng kỳ, tổng tiền thực thi và kết quả của từng cặp so sánh.

## Table: khách còn nợ nhiều nhất

Đề nghị intent `top_debt_customers`, dùng `top_k` và `end` có sẵn. `start` là ngày
đầu coverage được adapter xác nhận, **không phải đầu tháng**. Không kết hợp filters
hay comparison trong bước đầu. Đầu ra có dạng:

```json
{
  "columns": ["khach_hang", "so_tien_no"],
  "rows": [["Khách mẫu A", 420000], ["Khách mẫu B", 180000]]
}
```

Agent chỉ được tính sau khi Data cung cấp adapter/capability có nguồn cho:

- Mã đơn, ngày đặt, trạng thái, hình thức thanh toán và **tổng phải thu cả đơn** theo
  luật nợ (gồm phí giao, đã trừ giảm giá cả đơn), không dùng doanh thu dòng thay thế.
- Mã khách và tên gốc; nếu nguồn không có mã khách, quy tắc xác định tên theo split.
- Khoản thanh toán gắn với đơn, số tiền, ngày trả; trả sau `end` không được trừ.
- Nhận diện COD/sàn TMĐT và khách lẻ; giữ đơn trả hàng còn nợ, bỏ đơn huỷ theo luật nợ.
- Coverage ngày và kiểm tra khoá/cardinality; không nhân tiền do join nhiều khoản trả.

Tính phần còn nợ > 0 của từng đơn tại hết `end`, rồi cộng theo khách. Bỏ khách lẻ
khỏi bảng theo luật, không bỏ khỏi tổng nợ nếu tính tổng riêng. Xếp tiền giảm dần,
hoà theo mã (hoặc tên khi thực sự không có mã), lấy tối đa `top_k`. Cột tên đứng đầu,
cột số đo theo đúng thứ tự câu hỏi; ô tiền là JSON number, đơn vị VND.

`Sale` hiện tại không có đủ dữ liệu khách/tổng phải thu/khoản trả. Agent **không suy
ra** các trường đó từ `line_amount`, ngày bán hay tên sản phẩm. Data cần đề xuất
nguồn chuẩn hoá công nợ riêng, không mở rộng Sale một cách ngầm định.

## Xác nhận và regression trước khi code

Semantics xác nhận tên intent, Period/Plan, grammar, strict comparison và nguồn luật.
Data xác nhận đủ nguồn công nợ, nhận diện khách, key/join, đơn vị và coverage.
Agent nhận phần executor, evidence, deadline và bộ kiểm tra kết quả.

Regression bắt buộc:

- Boolean true/false; hai kỳ bằng nhau; năm nhuận/đổi năm; thiếu hoặc chồng kỳ;
  luật split đổi; một kỳ ngoài coverage; worker lỗi/timeout vẫn abstain.
- Table thiếu/trùng mã khách, hai khách trùng tên; khách lẻ; đơn huỷ/trả/COD;
  khoản trả sau mốc; nhiều khoản trả không nhân dòng; hoà top-k, bảng rỗng, ô số
  đúng kiểu; thiếu trường hay luật thì abstain.
- Backend cũ phải từ chối mọi trường Plan mới mà nó chưa áp dụng.

Đây không phải sự đồng ý thay cho Semantics/Data. Chỉ sau khi hai role xác nhận
contract và capability dữ liệu mới triển khai các intent trên; không gọi phần kiểm
tra format hiện tại là đã hỗ trợ câu hỏi boolean/table.
