# Contract C01 — boolean, table và nguồn công nợ

Trạng thái: **đề xuất đã hoàn tất; Data (@longlth2297) đã xác nhận ở `9c4e726`, chờ
Agent (@tqhuy0602) kiểm lại** trên PR #23 và issue #15. Đã sửa theo review Agent lần 1
(khoá khách theo loại shop, nợ thêm không âm) và ghi các lưu ý triển khai của Data vào §6.
Thay bản đề xuất AGENT-02 trước đó.
Liên quan SEM-02 #15, AGENT-02 #16, DATA-03 #14. Giữ nguyên SEM-01 #18 và SEM/DATA #22.

C01 chỉ chốt giao diện. Grammar thuộc S01, adapter công nợ thuộc D01, executor và
validation thuộc A01. Mọi số liệu và ví dụ dưới đây lấy từ business **train**
(không dùng business validation, public hay private test); đây là dữ liệu đã xem,
không phải đánh giá mù.

## 1. Tóm tắt quyết định

| Câu hỏi | Quyết định |
|---|---|
| Có thêm trường `Plan` không? | **Không.** Dùng lại `periods`, `start/end`, `top_k`, `filters`, `threshold` |
| Intent bắt buộc của C01 | `revenue_increasing` (boolean), `top_debt_customers` (table) |
| Intent công nợ khác | Chốt luôn trong cùng contract, làm sau hai intent trên: `debt_total`, `debt_customer_count`, `debt_collected`, `debt_flow` |
| "Đến hết ngày X" | `start == end == X`; intent nằm trong `SNAPSHOT_INTENTS`; tính mọi phát sinh từ đầu dữ liệu đến hết X |
| "Tăng liên tục" | mọi tháng sau **lớn hơn hẳn** tháng ngay trước; bằng nhau là `false`; so tổng Decimal chưa làm tròn |
| Nguồn công nợ | kiểu dữ liệu riêng `DebtOrder` / `DebtPayment` / `DebtLedger` trong `contracts.py`; **không** mở rộng `Sale` |
| Shop dùng mã hay không | `DebtLedger.uses_customer_codes` bắt buộc, do D01 xác định từ README / schema; quyết định khoá khách và khách lẻ (§6.5) |
| Nợ thêm của một đơn | `max(total − paid_on_order_day, 0)`, không âm (§7) |
| Tên cột bảng nợ | `DEBT_TABLE_COLUMNS = ("khach_hang", "so_tien_no")`, đúng ví dụ trong rulebook |
| Backend chưa làm intent / trường nào | abstain qua `compute.unsupported()`; model chỉ sinh các intent đã bật cho nó |

So với bản đề xuất cũ:

- **Thêm:** nguồn công nợ thành kiểu dữ liệu dùng chung. D01 và A01 làm song song được: A01 viết executor trên ledger tổng hợp, D01 điền ledger từ dữ liệu.
- **Thêm:** bốn intent công nợ dạng số / list. Chúng dùng chung ledger và chung luật §4, và chiếm phần lớn họ câu "công nợ" trên train.
- **Đổi:** `start` của câu "đến hết ngày X" là `X`, không phải ngày đầu coverage. Semantics không biết coverage của shop; executor tự tính từ đầu dữ liệu.
- **Thêm:** kiểm tra đối chiếu với cột "Còn nợ" khi README có, làm điều kiện bắt buộc của D01.

## 2. Phạm vi đo trên train

Trên toàn train có 23 câu boolean (đều hỏi doanh thu tăng liên tục qua các tháng),
19 câu table (đều là bảng khách nợ nhiều nhất) và 116 câu về công nợ (tính cả 19 câu table):

| Nhóm câu công nợ | Đáp án | Số câu (toàn train) | Intent C01 |
|---|---|---:|---|
| Bảng k khách nợ nhiều nhất, kèm số tiền | table | 19 | `top_debt_customers` |
| Tổng nợ đến hết ngày X; nợ của đơn quá N ngày | number | 8 + 10 | `debt_total` (+ `Threshold` tuổi nợ) |
| Số khách còn nợ đến hết ngày X | number | 18 | `debt_customer_count` |
| Tiền thu nợ / trả nợ trong kỳ | number | 34 | `debt_collected` |
| Một khách: nợ thêm, trả được trong kỳ | list 2 số | 20 | `debt_flow` + `Filter("customer")` |
| Nợ theo nhóm tuổi 0–7, 8–15, trên 15 ngày | list | 3 | **chưa chốt**, abstain (xem §8) |
| Shop không ghi khoản trả | unanswerable | 4 | `DebtSourceError("absent")` |

Số câu đếm theo từ khoá, mỗi câu một nhóm, nên chỉ là xấp xỉ.

## 3. Quy ước chung cho mọi intent mới

1. Không đổi nghĩa trường nào đã có (`comparison`, `filters`, `shape`, `periods` của SEM-02).
2. Intent mới không kết hợp `comparison`, `shape`, `unit`, `rank_by`. Tổ hợp hợp lệ
   duy nhất là các dòng trong §4–§6. Còn lại backend phải trả lý do từ
   `unsupported(plan)` và abstain.
3. Semantics chỉ đặt intent khi `read_rules` thấy đủ các dòng luật ghi ở từng intent
   **trong rulebook của split đang chạy**. Nguyên văn dòng luật vào `Plan.evidence`
   (và `Filter.rule`, `Threshold.rule` khi có).
4. Kỳ đã giải sẵn trong `Plan`; Agent không đọc lại chữ của câu hỏi.
5. Kỳ có ngày sau `as_of_date` → abstain. Không cắt kỳ ngầm. Riêng chuỗi tháng: tháng
   đang chạy dở cũng abstain.
6. Tiền cộng bằng `Decimal`, chỉ làm tròn **một lần** ở số cuối, về đồng, nửa đồng lên
   (rulebook §9). Đáp án train có số lẻ như `174667` (từ `174666,67`); làm tròn từng
   đơn rồi cộng sẽ lệch.
7. `unanswerable` chỉ khi có bằng chứng tường minh (§6.4). Worker lỗi, timeout, thiếu
   luật, thiếu cột, đối chiếu không khớp → **abstain**, không bao giờ `unanswerable`.

## 4. `revenue_increasing` — boolean

### Câu hỏi và Plan

Cách hỏi trên train:

- "doanh thu có tăng liên tục qua 3 tháng A, B, C không";
- "từ tháng A qua tháng B rồi tháng C, doanh thu có tăng liên tục không";
- "từ tháng A đến tháng C, doanh thu tháng sau có luôn cao hơn tháng trước không";
- "có phải tháng B cao hơn tháng A và tháng C cao hơn tháng B" (câu tự nêu cặp so sánh).

```json
{
  "intent": "revenue_increasing",
  "start": "2026-01-01",
  "end": "2026-03-31",
  "periods": [["2026-01-01", "2026-01-31"], ["2026-02-01", "2026-02-28"], ["2026-03-01", "2026-03-31"]],
  "evidence": ["<dòng luật doanh thu §1>", "<dòng Tháng M/Y §2>", "<dòng boolean §9>"]
}
```

| Trường | Quy định |
|---|---|
| `periods` | ≥ 2 và ≤ 12 tháng dương lịch **trọn tháng**, theo thứ tự thời gian tăng dần, không trùng, không chồng lấn. "Từ tháng A đến tháng C" mở ra mọi tháng ở giữa. Câu nêu tháng không theo thứ tự thời gian → abstain |
| `start`, `end` | `periods[0][0]` và `periods[-1][1]` |
| `end` | phải ≤ `as_of_date`; tháng đang chạy dở → abstain |
| Trường khác | mặc định; có `filters`, `comparison`, `shape`, `threshold` → abstain |

Luật bắt buộc (S01): cờ `revenue` (định nghĩa doanh thu §1), dòng "Tháng M/Y" (§2),
và dòng format `boolean` (§9, `true` / `false`). Rulebook train **không có** dòng định
nghĩa "tăng liên tục"; nghĩa "lớn hơn hẳn" lấy từ chính câu hỏi ("tháng sau luôn cao
hơn tháng trước"). Nếu rulebook của split có dòng nào nói về "tăng liên tục" / "xu
hướng" mà nội dung khác nghĩa này → S01 tắt intent (abstain).

Ngoài phạm vi, abstain: "giảm liên tục", "tăng ít nhất x%", doanh thu một mặt hàng /
nhóm tăng liên tục, kỳ theo tuần / quý, số đơn tăng liên tục.

### Thực thi và output

- Doanh thu mỗi tháng = tổng `Sale.amount` của `select_valid` trong tháng, **chưa làm tròn**.
- Kết quả = `all(tháng[i] > tháng[i-1])`. Bằng nhau là `false`.
- Mỗi tháng phải nằm **trọn** trong `[ngày đầu dữ liệu, ngày cuối dữ liệu]`; không thì
  abstain. Luật §7 "tính trên phần có dữ liệu" không dùng ở đây, vì so một tháng thiếu
  ngày với tháng đủ ngày cho kết luận sai.
- Output: `{"type": "boolean", "answer": true}`. JSON bool thật, không `"có"`, không `1`.
- Evidence: `code.call`, `code.source`, danh sách `{"start", "end", "revenue"}` từng tháng
  (chuỗi Decimal), danh sách cặp `{"previous", "current", "greater": bool}`.
- Validation (A01): `type(answer) is bool`; `answer == all(greater)`; số tháng trong
  evidence bằng `len(periods)` và đúng biên.

### Kiểm chứng trên train

Theo luật trên, dùng tổng doanh thu từ adapter hiện có: **22 / 22** câu boolean của
business train khớp nhãn (câu thứ 23 thuộc business validation, không dùng).

## 5. `top_debt_customers` — table

### Câu hỏi và Plan

Cách hỏi trên train: "k khách nào đang nợ nhiều nhất, mỗi người còn nợ bao nhiêu",
"top k khách có công nợ lớn nhất … và số nợ của từng người", "bảng k khách còn nợ
nhiều nhất …, kèm số tiền còn nợ", luôn kèm "(tính) đến hết ngày X".

```json
{
  "intent": "top_debt_customers",
  "start": "2026-04-12",
  "end": "2026-04-12",
  "top_k": 2,
  "evidence": ["<§4 Đơn được tính vào nợ>", "<§4 Nợ một đơn tại ngày X>", "<§4 Nợ theo khách>",
               "<§4 Đơn COD, đơn bán qua sàn>", "<§2 Đến hết ngày X>", "<§3 Xác định khách>",
               "<§9 dòng table>", "<§9 Bằng nhau … theo mã>"]
}
```

| Trường | Quy định |
|---|---|
| `start`, `end` | cả hai là `X`. "Tính đến hôm nay" → `X = as_of_date`. Câu không nêu mốc → abstain |
| `top_k` | 1–50, lấy từ câu |
| Trường khác | mặc định |

Chỉ dùng table khi câu **hỏi cả số tiền** ("mỗi người còn nợ bao nhiêu", "kèm số tiền",
"số nợ của từng người", "bảng"). "Khách nào nợ nhiều nhất" không hỏi số tiền là
string / list, ngoài C01 → abstain.

### Thực thi và output (trên `DebtLedger`)

1. Đơn tính vào nợ tại X: `day <= X`, `state != "huy"`, `excluded == ""` (bỏ COD và sàn).
   Đơn trả hàng **vẫn tính** nếu còn nợ.
2. Đã trả của đơn = tổng `DebtPayment.amount` có `day <= X`. Còn nợ của đơn = `total − đã trả`;
   chỉ giữ đơn còn nợ `> 0`.
3. Cộng theo **khoá khách** (§6.5, theo `ledger.uses_customer_codes`). Đơn khách lẻ
   (không có khoá) **không có dòng** trong bảng nhưng vẫn vào tổng nợ trong evidence.
4. Xếp số nợ giảm dần, bằng nhau theo mã tăng dần (không có mã thì theo tên), lấy `top_k`.
5. Ô tiền làm tròn một lần về đồng (quy ước 6). Ít hơn `top_k` khách còn nợ thì trả số
   dòng có được; **không khách nào còn nợ → abstain** (chưa có nhãn train cho bảng rỗng).
6. `X > as_of_date`, `X < ledger.first_day` hoặc `X > ledger.last_day` → abstain.

Output đúng định dạng rulebook:

```json
{
  "type": "table",
  "answer": {
    "columns": ["khach_hang", "so_tien_no"],
    "rows": [["Khách mẫu A", 420000], ["Khách mẫu B", 180000]]
  }
}
```

- Cột đầu là tên khách, lấy từ danh sách khách nếu có, nếu không thì cách viết xuất
  hiện nhiều nhất trên đơn của khách đó (bằng nhau thì cách viết gặp trước). Trên các
  business train đã kiểm, tên trong nhãn trùng cả hai nguồn này.
- Ô tiền là JSON number nguyên (VND), không phải chuỗi.
- Evidence: `ledger.lineage`, mốc X, số đơn còn nợ, tổng nợ cả shop (kể cả khách lẻ),
  từng dòng `{customer_key, name, debt, orders}` trước khi cắt `top_k`.
- Validation (A01): `columns == list(DEBT_TABLE_COLUMNS)`; `1 <= len(rows) <= top_k`;
  tên là chuỗi khác rỗng; tiền là số nguyên > 0, giảm dần; không trùng khách; rows khớp
  evidence.

### Kiểm chứng trên train

Dựng ledger thủ công cho 3 business train có file đơn + file thu tiền rõ ràng, áp đúng
các bước trên: **7 / 7** câu table của các business đó khớp nhãn (so tên qua mã khách).
5 câu table khác của business train nằm ở một shop có file đơn dạng báo cáo (dòng tiêu đề
phía trên header), chưa dựng ledger thủ công; D01 sẽ kiểm qua adapter thật.
Đối chiếu với cột "Còn nợ" ở ngày cuối dữ liệu: **khớp từng đơn**, mọi khác biệt chỉ nằm
ở đơn COD. Cột này tính cả COD, còn luật nợ thì bỏ COD.

## 6. Nguồn công nợ — việc của D01

### 6.1 Kiểu dữ liệu (`contracts.py`)

`DebtOrder(order_id, day, state, total, payment_method, on_credit, excluded,
customer_id, customer_name, source)`, `DebtPayment(order_id, day, amount, source)`,
`DebtLedger(orders, payments, first_day, last_day, uses_customer_codes, lineage)`.
Nghĩa từng trường ghi ngay trong code. `uses_customer_codes` **bắt buộc**, không có giá
trị mặc định, để D01 phải quyết định tường minh (§6.5). Đề nghị hàm của D01:

```python
# src/datamate/ingestion/debt.py
def load_debt_ledger(catalog: Catalog, rulebook: str) -> DebtLedger: ...
class DebtSourceError(ValueError):
    code: str  # "absent" | "partial" | "rules_missing" | "column_missing" | "key" | "orphan"
               # | "label" | "amount" | "reconcile" | "customer_mode"
```

A01 gọi nó một lần mỗi business trong worker (`compute.prepare_debt`, giống `prepare_sales`).

### 6.2 Đọc theo README, không theo tên cột

README train ghi công nợ theo một mẫu (có shop ghi tên cột kiểu `bảng.cột` thay vì in đậm):

> **Công nợ**: mỗi lần khách trả tiền cho một đơn là một dòng, **‹cột A›** là số tiền
> trả lần đó; **‹cột B›** là số khách phải trả cho cả đơn[; **‹cột C›** là số tiền của
> đơn chưa thu, tính đến hết ngày cuối dữ liệu].

| Trường ledger | Lấy từ | Ghi chú |
|---|---|---|
| `DebtPayment.amount` | cột A | bảng chứa cột A là bảng khoản trả |
| `DebtPayment.order_id` | cột mã đơn của bảng khoản trả (nghĩa README "mã đơn", hoặc cột có giá trị là mã đơn của bảng đơn) | |
| `DebtPayment.day` | ngày của cột thời điểm thu | so theo **ngày**; tiền trả cùng ngày đặt là trả lúc mua |
| `DebtOrder.total` | cột B | **không** lấy doanh thu dòng, tiền hàng trước giảm hay tổng dòng chi tiết |
| `DebtOrder.state` | `ingestion.status.order_state` | cùng bảng đồng nghĩa với adapter bán hàng; nhãn lạ → lỗi |
| `DebtOrder.payment_method`, `on_credit`, `excluded` | cột hình thức thanh toán; cột kênh / nguồn đơn | bảng nhãn đóng, xem 6.3 |
| `uses_customer_codes` | README / schema của shop (§6.5) | **không** suy ra từ việc có mã xuất hiện trên đơn |
| `customer_id` | mã khách trên đơn, chỉ khi `uses_customer_codes` | đơn trống mã → `None` (khách lẻ), kể cả khi đơn có tên |
| `customer_name` | tên từ danh sách khách hoặc trên đơn, giữ nguyên cách viết | shop không dùng mã: đây là căn cứ xác định khách |
| `first_day`, `last_day` | ngày đặt sớm nhất; "Dữ liệu có đến hết dd/mm/yyyy" của README | README không ghi: `last_day` = ngày lớn nhất của đơn và khoản trả đã đọc, `lineage["coverage"] = "observed"` (có ghi: `"readme"`). Quy ước phòng thủ, chưa gặp trên train |

### 6.3 Nhãn đóng

- **Ghi nợ** (`on_credit`): "ghi nợ", "debt", "nợ", "mua chịu".
- **COD** (`excluded="cod"`): "cod", "thu hộ", "thu hộ (COD)", "giao hàng thu tiền".
- **Sàn TMĐT** (`excluded="marketplace"`): kênh "shopee", "lazada", "tiki", "tiktok shop", "sàn …".
- Nhãn hình thức khác đã gặp trên train: "tiền mặt" / "cash", "chuyển khoản" / "transfer",
  "ví điện tử" / "ewallet". Kênh: "store", "phone", "zalo_oa".
- So nhãn sau khi chuẩn hoá: bỏ khoảng trắng đầu / cuối, gộp khoảng trắng, chữ thường,
  bỏ dấu (`fold`), bỏ ngoặc; rồi so **trùng khớp cả nhãn** với danh sách, không so chuỗi
  con. Ví dụ "Thu hộ (COD)" → "thu ho cod".
- Nhãn nằm ngoài các danh sách trên → `DebtSourceError("label")` → abstain. Không đoán.
  Thêm nhãn mới phải qua review như bảng trạng thái.
- `lineage` ghi tập nhãn thực gặp của từng cột (trạng thái, thanh toán, kênh) kèm số dòng,
  để reviewer thấy toàn bộ giá trị mà không phải mở dữ liệu.

### 6.4 Kiểm tra bắt buộc

0. **Đủ nguồn.** Phải xác định được cả: mã đơn, ngày đặt, tổng phải thu (cột B) trên
   bảng đơn; và bảng khoản trả có mã đơn, số tiền (cột A), ngày trả. Thiếu một phần →
   `DebtSourceError("partial")` (hoặc `"column_missing"` khi README có nêu cột nhưng file
   không có). Chỉ có cột "Còn nợ" hay "Khách đã trả" **không đủ**: các cột này là trạng
   thái ở ngày cuối dữ liệu, không tính được nợ tại X sớm hơn; chúng chỉ dùng để đối chiếu.
1. Mã đơn đầy đủ và không trùng; mọi khoản trả trỏ tới **đúng một** đơn (không mồ côi).
   Các kiểm tra 1, 2, 4 làm **sau khi gộp mọi sheet / file cùng vai trò** (sheet theo
   tháng, file `_1`, `_2`…), không kiểm riêng từng sheet.
2. Một đơn nhiều khoản trả thì cộng, không nhân dòng đơn. Không join khoản trả với dòng
   chi tiết hàng.
3. Số tiền, ngày đọc được hết; ô lỗi → lỗi, không bỏ qua dòng. `total < 0` hoặc khoản trả
   âm → `DebtSourceError("amount")`. Rulebook ghi "Hoàn tiền trả hàng không trừ", nên
   không coi khoản âm là hoàn tiền. Khoản trả bằng 0 giữ lại, không ảnh hưởng kết quả;
   số dòng như vậy ghi vào `lineage`. Đây là kiểm tra phòng thủ, chưa thấy trên train.
4. **Đối chiếu** khi README có cột "còn nợ cuối dữ liệu": với mọi đơn không huỷ và không
   `excluded`, nợ tính được tại `last_day` phải **bằng đúng** cột đó. Lệch một đơn →
   `DebtSourceError("reconcile")`.
5. Rulebook của split phải có các dòng §4 "Đơn được tính vào nợ", "Nợ một đơn tại ngày X",
   "Đơn COD, đơn bán qua sàn TMĐT"; thiếu → `DebtSourceError("rules_missing")`.
6. `DebtSourceError("absent")` chỉ khi **cả ba** điều sau đúng:
   - README mô tả đủ mọi file của shop;
   - không có dòng "Công nợ" nào;
   - không có bảng / cột nào mang nghĩa **số tiền** của nợ: khoản trả, đã trả, còn nợ,
     tổng phải thu kèm khoản trả.

   Chỉ mã lỗi này cho phép Agent trả `unanswerable` (kèm dòng §7 rulebook "shop không ghi
   loại thông tin câu hỏi cần").
   - Cột chỉ về **thời hạn** (hạn nợ, hạn thanh toán, số ngày được nợ) không phải số tiền
     của nợ. Shop chỉ có các cột này vẫn là `absent`. Trên train, 4 câu công nợ ở các shop
     không có số tiền nợ (một trong số đó chỉ ghi hạn nợ) đều có nhãn `unanswerable`.
   - Có dấu hiệu nguồn một phần (chỉ "Còn nợ", thiếu cột, join mơ hồ, đối chiếu lệch) thì
     dùng mã khác (`partial`, `column_missing`, `orphan`, `reconcile`…) → abstain.
   - Lỗi worker, timeout, thiếu luật **không bao giờ** thành `unanswerable`.
7. `uses_customer_codes=False` thì **mọi** `customer_id` là `None`; có đơn mang mã →
   `DebtSourceError("customer_mode")`. Không xác định được shop có dùng mã hay không →
   cùng mã lỗi, abstain.
8. `lineage` ghi:
   - file::bảng / sheet đã gộp cho từng vai trò, cột đã dùng cho từng trường, dòng README;
   - căn cứ của `uses_customer_codes`, nguồn coverage (`readme` / `observed`), dòng luật của split;
   - tập nhãn thực gặp (6.3);
   - kết quả kiểm tra 0–4 và 7: số đơn, số khoản trả, số trùng / mồ côi, số đơn đã đối chiếu và số lệch.

### 6.5 Khoá khách: shop dùng mã và shop không dùng mã

Luật §3 "Xác định khách" xử lý hai loại shop khác nhau, nên ledger ghi rõ loại shop thay vì
để `customer_id=None` mang hai nghĩa.

**D01 xác định `uses_customer_codes` từ README / schema**, không từ dữ liệu:

- `True` khi README (hoặc profile POS đã duyệt) mô tả một cột **mã khách** của đơn, ví dụ
  "Mã khách mua: mã khách (trống nếu khách lẻ)", hoặc danh sách khách có cột mã nối với đơn.
- `False` khi README mô tả khách chỉ bằng tên trên đơn và không có cột mã khách nào.
- README không cho biết → `DebtSourceError("customer_mode")` → abstain.
- Shop dùng mã nhưng trong dữ liệu mọi đơn đều trống mã vẫn là `True`: mọi đơn là khách
  lẻ, không ai có dòng trong bảng nợ.

**A01 tính khoá khách của một đơn như sau:**

| `uses_customer_codes` | Khoá khách | Khách lẻ (không có khoá) |
|---|---|---|
| `True` | `customer_id` | `customer_id is None`, **kể cả khi đơn có tên** |
| `False` | tên trên đơn đã gộp hoa thường, khoảng trắng và dấu câu (cùng cách gộp với `top_customers` của SEM-02) | tên trống hoặc tên thuộc `WALK_IN_NAMES` ("khách lẻ", "kl", "lẻ", "vãng lai"…) |

- Shop dùng mã: hai mã khác nhau trùng tên là **hai khách**, hai dòng trong bảng. Xếp
  bằng nhau theo mã.
- Shop không dùng mã: "Chị Lan" và "chị  lan" là một khách; hai tên khác chữ là hai khách.
  Xếp bằng nhau theo tên. Tên hiển thị là cách viết gặp nhiều nhất.
- Đơn khách lẻ vẫn vào `debt_total` và tổng nợ trong evidence, nhưng không vào bảng nợ,
  `debt_customer_count` hay `debt_flow`.
- Gộp tên chỉ bỏ khác biệt hoa thường / khoảng trắng / dấu câu. Tên khác dấu tiếng Việt
  ("Le An Duc" và "Lê An Đức") ở shop không dùng mã **chưa chốt**: Data xác nhận; trước
  khi chốt, coi là hai khách như SEM-02 đang làm.

Ghi chú cho SEM-02: `compute.customer_keys` hiện suy ra "shop có mã" từ việc có mã xuất
hiện trong dữ liệu. Nên chuyển sang cùng cờ do Data cung cấp; việc này ngoài C01.

## 7. Bốn intent công nợ dùng chung ledger (làm sau §4–§5)

| Intent | Plan | Đáp án | Thực thi | Luật cần |
|---|---|---|---|---|
| `debt_total` | `start == end == X` | number | tổng nợ còn lại tại X, **kể cả khách lẻ** | §4 ba dòng ở 6.4.5, §2 "Đến hết ngày X" |
| `debt_total` + `Threshold("order", "age_days", ">", N)` | như trên | number | chỉ đơn có tuổi `X − day > N` ("quá / hơn / trên N ngày"); "từ N ngày trở lên" là `">="` | + §4 "Tuổi nợ", §2 "Quá N…" |
| `debt_customer_count` | `start == end == X` | number | số khoá khách (không tính khách lẻ) có nợ > 0 | + §3 "Xác định khách", §4 "Nợ theo khách" |
| `debt_collected` | kỳ `start..end` | number | tổng khoản trả có `start <= day <= end`, `day > ngày đặt`, của đơn `on_credit`, không huỷ, `excluded == ""` (bỏ COD / sàn; kể cả đơn kỳ trước, đơn trả hàng) | §4 "Nợ thêm, trả nợ, thu nợ trong kỳ", "Đơn COD, đơn bán qua sàn TMĐT" |
| `debt_flow` + `Filter("customer", tên)` | kỳ `start..end` | list `[nợ thêm, trả được]` | nợ thêm = tổng theo đơn của `max(total − paid_on_order_day, 0)`, với các đơn `on_credit`, không huỷ, `excluded == ""`, đặt trong kỳ; trả được như `debt_collected`; chỉ đơn của khách đó (khoá §6.5) | + §3 "Xác định khách" |

- Đơn COD / sàn (`excluded != ""`) **không** vào `debt_collected` hay `debt_flow`, kể cả khi
  hình thức ghi là ghi nợ (`on_credit=True`): rulebook §4 "không phải khách nợ … bỏ khỏi
  công nợ". Ví dụ: đơn sàn 100.000, khách trả 30.000 sau ngày đặt → nợ thêm 0, trả được 0.
- Nợ thêm của **một đơn** không bao giờ âm:

  ```python
  new_debt = max(order.total - paid_on_order_day, Decimal(0))
  # paid_on_order_day = tổng DebtPayment.amount của đơn đó có day == order.day
  ```

  Ví dụ: tổng đơn 100.000 VND, trả ngay trong ngày đặt 120.000 VND → nợ thêm **0**, không
  phải −20.000. Phần trả dư không được trừ vào nợ thêm của đơn khác.
- `Filter("customer").value` là tên như trong dữ liệu. Semantics lấy danh sách tên
  khách qua `vocabulary` (thêm khoá `"customer"`) giống tên nhóm. Tên khớp nhiều mã khách
  → abstain.
- Thứ tự list theo câu hỏi ("nợ thêm, trả được"); câu đảo thứ tự → abstain trong bước đầu.
- Kiểm chứng trên cùng 3 business train ở §5: **46 / 46** câu number / list công nợ khớp
  nhãn (tổng nợ 2, quá N ngày 2, số khách 10, thu nợ 18, nợ thêm / trả được 12, nhóm
  tuổi 2 với luật `0–7, 8–15, > 15`).

## 8. Chưa chốt, giữ abstain

| Nội dung | Lý do | Điều kiện để làm |
|---|---|---|
| Nợ theo nhóm tuổi (list 3 số) | cần trường mới cho các khoảng tuổi; ít câu | đề xuất `Plan.age_bins: tuple[tuple[int, int \| None], ...]` ở PR riêng |
| Lọc doanh thu theo hình thức thanh toán | luật §1 có, nhưng `Sale` chưa có hình thức thanh toán | D01/DATA thêm `Sale.payment_method` theo cùng bảng nhãn 6.3, rồi `Filter("payment_method")` |
| Doanh thu theo kênh bán | rulebook train không có luật | chờ luật |
| "Giảm liên tục", tăng theo tuần / quý, tăng của một mặt hàng | ngoài phạm vi C01 | contract riêng |
| Câu "khách nào nợ nhiều nhất" không hỏi số tiền | đáp án string / list, không phải table | thêm sau khi table chạy ổn |
| Bảng rỗng | chưa có nhãn train | quyết định lại khi có ví dụ |

## 9. Tương thích và trách nhiệm

- `Plan`, `Filter`, `Threshold` không đổi chữ ký; callers cũ chạy nguyên. Thêm
  `SNAPSHOT_INTENTS`, `DEBT_TABLE_COLUMNS`, `DebtOrder`, `DebtPayment`, `DebtLedger`.
- Backend `baseline` hiện tại: `unsupported()` trả lý do cho mọi intent mới, nên câu
  tương ứng abstain cho tới khi A01 thêm intent vào `INTENTS` cùng regression.
  `tests/test_contract_c01.py` khoá hành vi này; A01 sửa test đó khi bật từng intent.
- `local_plan`: model chỉ sinh các intent trong `_capabilities` (doanh thu một kỳ…).
  Intent mới **không** tự bật cho model; muốn bật phải qua A/B riêng.
- `agent/validation.check_executed`: Plan có intent mới mà backend chưa xử lý → bị chặn
  bởi `unsupported`; A01 thêm `boolean` / `table` vào `ANSWER_TYPES` khi làm executor.

| Role | Việc theo contract này |
|---|---|
| S01 (Semantics) | grammar §4, §5, rồi §7; cờ luật mới trong `rules.py` (`boolean_answer`, `table_answer`, `debt_rules`, `debt_age`, `debt_flow`); khoá `"customer"` trong `vocabulary` |
| D01 (Data) | `ingestion/debt.py` theo §6; regression cho từng kiểm tra 6.4 |
| A01 (Agent) | `prepare_debt`, executor §4, §5, §7, validation, evidence; `unsupported()` mở đúng các tổ hợp |
| E01 (Evaluation) | scorer local chưa nhận alias trong ô bảng; nên so tên ô bảng với `alias` của dòng như list |

## 10. Regression bắt buộc

**Boolean** (S01 + A01):

- true / false;
- hai tháng bằng nhau → false;
- tiền lẻ dễ đổi thứ tự nếu làm tròn sớm;
- tháng 2 năm nhuận; chuỗi qua đổi năm (12 → 1);
- thiếu tháng / tháng trùng / tháng chồng lấn / không theo thứ tự → abstain;
- tháng tương lai hoặc đang chạy dở → abstain;
- tháng ngoài khoảng dữ liệu → abstain;
- rulebook đổi (mất dòng doanh thu / boolean, hoặc có dòng "tăng liên tục" khác nghĩa) → abstain.

**Table** (D01 + A01):

- thiếu / trùng mã đơn; khoản trả mồ côi;
- khoá khách theo §6.5 (fixture có sẵn trong `tests/test_contract_c01.py`, A01 dùng lại):
  - shop **không dùng mã**: tên khác hoa thường / khoảng trắng là một khách, "kl" / "Khách lẻ" là khách lẻ;
  - shop **dùng mã nhưng mọi đơn trống mã**: không ai có dòng, tổng nợ vẫn đủ, bảng → abstain;
  - **hai mã khác nhau trùng tên** → hai dòng;
  - `uses_customer_codes=False` mà có đơn mang mã → lỗi `customer_mode`, abstain;
- khách lẻ chỉ vào tổng nợ, không có dòng;
- đơn huỷ bỏ; đơn trả hàng còn nợ vẫn tính; COD / sàn bỏ;
- khoản trả trước / đúng / sau mốc X; nhiều khoản trả một đơn không nhân dòng;
- trả dư không tạo nợ âm;
- bằng nhau ở ranh giới top-k → theo mã; ít hơn k khách nợ; không ai nợ → abstain;
- ô số là int, tên cột đúng `DEBT_TABLE_COLUMNS`;
- đối chiếu "Còn nợ" lệch → abstain.

**`debt_flow` / `debt_collected`** (A01, fixture trong `tests/test_contract_c01.py`):

- tổng đơn 100.000, trả trong ngày đặt 120.000 → nợ thêm 0 (`OVERPAID`);
- khoản trả sau ngày đặt chỉ vào "trả được";
- đơn sàn có `on_credit=True` (100.000, trả 30.000 sau ngày đặt) → `[0, 0]` (`MARKETPLACE_ONLY`);
- cùng khách thêm một đơn ghi nợ thường và một đơn COD → chỉ đơn thường được tính:
  `[60.000, 10.000]`, thu nợ cả shop 10.000 (`MARKETPLACE`).

**Cả hai:** thiếu nguồn / luật, worker lỗi / timeout → abstain; evidence `code` / `result`
khớp đáp án; backend cũ từ chối intent mới (`test_contract_c01.py`); regression SEM-02
hiện có vẫn qua.

## 11. Tái lập các kết quả kiểm chứng

Các con số 22 / 22, 7 / 7, 46 / 46 ở §4, §5, §7 chạy bằng một script **ngoài Git**, vì
script đọc nhãn train và dựng ledger thủ công cho từng business (ánh xạ cột viết tay):

```sh
# từ gốc repo, dữ liệu train đã giải nén, manifest holdout của nhóm
uv run python <thư mục C01>/verify/verify_c01_train.py --data-dir data/raw/train --holdout outputs/business_split.json
```

- Script `C01/verify/verify_c01_train.py` và log mới nhất
  `C01/verify/logs/verify_c01_train_20261107.txt` (sau khi bỏ COD / sàn khỏi thu nợ và
  nợ thêm, kết quả không đổi vì ở 3 shop đã kiểm không có đơn ghi nợ nào là COD / sàn) nằm trong thư
  mục bàn giao C01, chia sẻ cho nhóm qua kênh chung, không commit. Log ghi: boolean 22 / 22 (bỏ 1 câu của business validation),
  table 7 / 7, number / list 46 / 46, đối chiếu "Còn nợ" lệch 0 đơn ở cả 2 shop có cột này.
- Đầu log ghi revision của checkout và SHA256 của `questions.jsonl`, `answers.jsonl`,
  `rulebook.md`, manifest holdout. `C01/verify/MANIFEST.json` ghi SHA256 của script, log
  và từng file dữ liệu của các business đã dùng, để reviewer kiểm đúng input trước khi
  chạy lại.
- Script chỉ dùng business train của manifest và từ chối chạy nếu business nằm trong
  validation.
- Không dùng trong inference; chỉ để reviewer kiểm lại luật của contract.

## 12. Xác nhận

| Role | Người | Xác nhận | Trạng thái |
|---|---|---|---|
| Semantics | @Kurosuz | tên intent, Plan, luật bắt buộc, nghĩa "tăng liên tục", phạm vi abstain | đề xuất trong PR C01 |
| Data | @longlth2297 | nguồn / cột / nhãn §6, kiểm tra 6.4, cách xác định `uses_customer_codes` §6.5, mã lỗi `absent` / `customer_mode` | **đã xác nhận** ở `9c4e726`; các lưu ý triển khai của Data đã ghi vào §6.2–6.4 (không đổi interface) |
| Agent | @tqhuy0602 | executor / evidence / validation §4–§5, khoá khách §6.5, nợ thêm §7, tương thích backend, regression §10 | review lần 1 (2 điểm) và lần 2 sau merge (COD / sàn trong §7) đã sửa; chờ kiểm lại PR bổ sung |

Chỉ coi C01 xong khi cả ba xác nhận trên PR và issue #15. Phần kiểm tra format
boolean / table hiện có **không** được coi là đã trả lời được các câu này.
