import re
from dataclasses import dataclass

from datamate.semantics.text import fold

DEBT_TABLE_FEATURES = ("table_answer", "until_end_of_day", "debt_orders", "debt_at_date",
                       "debt_by_customer", "debt_excluded", "customer_identity", "ties_by_code")


@dataclass(frozen=True)
class Rules:
    revenue: bool
    month_to_date: bool
    monday_week: bool
    quantity_ranking: bool
    source_rows: tuple[str, ...]
    # Optional wordings; each is enabled only by a matching row of the current rulebook.
    money_is_revenue: bool = False      # "bán được / thu về bao nhiêu tiền" = doanh thu
    goods_money_is_revenue: bool = False  # "tiền hàng", "tiền bán" = doanh thu
    inclusive_range: bool = False       # "từ X đến Y" includes X and Y
    year_to_date: bool = False          # "từ đầu năm" = 01/01 .. hết hôm nay
    calendar_quarter: bool = False      # quý = 3 tháng dương lịch tính từ tháng 1
    implicit_year: bool = False         # tháng/quý không ghi năm: năm nay, sau hôm nay thì năm trước
    average_order: bool = False         # giá trị trung bình một đơn: làm tròn về đồng, nửa đồng lên
    cancelled_order: bool = False       # đơn huỷ = trạng thái huỷ; đơn trả hàng không phải đơn huỷ
    cancel_rate: bool = False           # tỉ lệ huỷ: trên mọi đơn đặt trong kỳ, mọi trạng thái, 2 số lẻ
    change_percent: bool = False        # % thay đổi so với kỳ trước, giảm ra số âm
    same_days_last_month: bool = False  # tháng này so với tháng trước: cùng số ngày
    product_names: bool = False         # tên gọi cùng chỉ một thứ là một; file có mã thì theo mã
    hot_is_best_seller: bool = False    # "hot nhất" cùng nghĩa với "bán chạy nhất"
    # SEM-02
    list_in_question_order: bool = False  # câu nhiều số: list theo đúng thứ tự câu hỏi nêu
    month_date: bool = False            # câu hỏi theo tháng: date "YYYY-MM"
    ties_by_code: bool = False          # bằng nhau: theo mã tăng dần, không có mã thì theo tên
    threshold_inclusive: bool = False   # "từ N trở lên" gồm N; "quá / trên / dưới N" không gồm N
    customer_identity: bool = False     # khách = mã khách (không có mã: tên); khách lẻ không tính
    customers_bought: bool = False      # "khách đã mua": có ít nhất một đơn hợp lệ trong kỳ
    customer_by_revenue: bool = False   # khách mua nhiều tiền nhất: theo doanh thu
    unit_quantity: bool = False         # "bán được bao nhiêu <đơn vị>": mặt hàng có ĐVCB đó
    product_group: bool = False         # nhóm hàng: theo cột nhóm hàng, đúng tên câu hỏi nêu
    product_group_share: bool = False   # mẫu số tỉ trọng nhóm hàng: cả shop, gồm phí phục vụ
    customer_group: bool = False        # nhóm khách: theo cột; đơn vãng lai không thuộc nhóm nào
    customer_group_share: bool = False  # tỉ trọng nhóm khách: chia doanh thu cả shop cùng kỳ
    # S01 (contract C01, docs/AGENT_02_CONTRACT.md §4-§5)
    calendar_month: bool = False        # "Tháng M/Y": cả tháng, ngày 1 đến hết ngày cuối tháng
    boolean_answer: bool = False        # format boolean: true / false
    trend_rule: bool = False            # the rulebook defines "tăng liên tục" / "xu hướng" itself
    table_answer: bool = False          # format table: {"columns", "rows"}, ô số là JSON number
    until_end_of_day: bool = False      # "đến hết ngày X": tình trạng lúc hết ngày, từ đầu dữ liệu
    debt_orders: bool = False           # đơn tính vào nợ: không huỷ, đơn trả hàng vẫn tính
    debt_at_date: bool = False          # nợ một đơn tại X: tổng đơn (gồm phí giao) trừ khoản trả đến X
    debt_by_customer: bool = False      # nợ theo khách: khách lẻ vào tổng nợ, không có dòng
    debt_excluded: bool = False         # đơn COD / sàn: không phải khách nợ
    rule_rows: tuple[tuple[str, str], ...] = ()  # (feature, original rulebook line) used as evidence

    def row(self, feature: str) -> str | None:
        return next((line for name, line in self.rule_rows if name == feature), None)


def _find(rows: dict[str, str], *key_tokens: str) -> str | None:
    """First rule key containing every token; rule wording may differ between splits."""
    return next((key for key in rows if all(token in key for token in key_tokens)), None)


def read_rules(rulebook: str) -> Rules:
    """Recognize a small, explicit subset; new rule wording requires an adapter.

    No cached training rulebook, identifier lookup or model assumption is used.
    Unrecognized revenue semantics disable the deterministic baseline; an unrecognized
    optional row only disables the wording that depends on it.
    """
    rows = {}
    originals = {}
    conflicts = set()
    for line in rulebook.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            key, value = fold(cells[0]), fold(cells[1])
            if key in rows and rows[key] != value:
                conflicts.add(key)
            rows[key] = value
            originals[key] = line.strip()
    validity = rows.get("don tinh vao doanh thu", "")
    amount = rows.get("doanh thu mot don", "")
    month = rows.get("thang nay", "")
    week = rows.get("tuan", "")
    ranking = rows.get("ban chay", "")

    features: dict[str, str] = {}

    def enable(feature: str, key: str | None, *value_tokens: str) -> bool:
        if key is None or not all(token in rows[key] for token in value_tokens):
            return False
        features[feature] = originals[key]
        return True

    def exact(feature: str, key: str | None, *wordings: str) -> bool:
        if key in conflicts or rows.get(key) not in wordings:
            return False
        features[feature] = originals[key]
        return True

    lines = [(fold(line), line.strip()) for line in rulebook.splitlines()
             if line.strip() and not line.lstrip().startswith("|")]

    def enable_text(feature: str, *tokens: str) -> bool:
        """For rules written as a sentence (answer-format section), not a table row."""
        line = next((original for folded, original in lines
                     if all(token in folded for token in tokens)), None)
        if line is None:
            return False
        features[feature] = line
        return True

    def defines_trend(folded: str) -> bool:
        """Recognize a trend definition without mistaking sort-order rules for one."""
        return ("lien tuc" in folded or "xu huong" in folded
                or ("doanh thu" in folded
                    and ("tang dan" in folded or "giam dan" in folded)))

    money = _find(rows, "ban duoc bao nhieu tien", "thu ve bao nhieu tien")
    return Rules(
        revenue=all(token in validity for token in ("khong huy", "khong tra", "ca don"))
        and all(token in amount for token in (
            "thanh tien cac dong", "khong tru", "khong cong thue vat", "khong gom phi giao hang"
        )),
        month_to_date="ngay 1" in month and "hom nay" in month,
        monday_week="thu hai" in week and "chu nhat" in week,
        quantity_ranking="so luong quy ve don vi co ban" in ranking,
        source_rows=tuple(originals[key] for key in (
            "don tinh vao doanh thu", "doanh thu mot don", "thang nay", "tuan", "ban chay"
        ) if key in originals),
        money_is_revenue=enable("money_is_revenue", money, "doanh thu",
                                "khong phai tien khach da tra"),
        goods_money_is_revenue=enable("goods_money_is_revenue", _find(rows, "doanh thu mot don"),
                                      "tien hang", "tien ban", "cung nghia voi doanh thu"),
        inclusive_range=enable("inclusive_range", _find(rows, "tu x den y"), "gom ca x va y"),
        year_to_date=enable("year_to_date", _find(rows, "tu dau nam"), "01/01", "het hom nay"),
        calendar_quarter=enable("calendar_quarter", _find(rows, "quy q"),
                                "duong lich", "3 thang", "tu thang 1"),
        implicit_year=enable("implicit_year", _find(rows, "khong ghi nam", "thang m"),
                             "nam nay neu khong sau hom nay", "nam truoc"),
        average_order=enable("average_order", _find(rows, "trung binh mot don"),
                             "lam tron ve dong", "nua dong lam tron len"),
        cancelled_order=enable("cancelled_order", _find(rows, "don bi huy"),
                               "trang thai huy", "don tra hang khong phai don huy"),
        cancel_rate=enable("cancel_rate", _find(rows, "ti le don huy"),
                           "moi don dat trong ky", "moi trang thai", "lam tron 2 chu so"),
        change_percent=enable("change_percent", _find(rows, "phan tram thay doi"),
                              "so voi ky truoc", "so am"),
        same_days_last_month=enable("same_days_last_month", _find(rows, "thang nay"),
                                    "so voi thang truoc", "cung so ngay"),
        product_names=enable("product_names", _find(rows, "ten goi"),
                             "cung chi mot thu", "theo ma"),
        hot_is_best_seller=enable("hot_is_best_seller", _find(rows, "ban chay"),
                                  "hot nhat", "cung nghia voi ban chay nhat"),
        list_in_question_order=enable_text("list_in_question_order", "nhieu so",
                                           "list theo dung thu tu cau hoi neu"),
        month_date=enable("month_date", "date" if "date" in rows else None,
                          "cau hoi theo thang", "yyyy mm"),
        ties_by_code=enable_text("ties_by_code", "bang nhau", "theo ma tang dan",
                                 "khong co ma thi theo ten"),
        threshold_inclusive=enable("threshold_inclusive", _find(rows, "tren n", "duoi n"),
                                   "khong gom n", "tu n tro len thi gom n"),
        customer_identity=enable("customer_identity", _find(rows, "xac dinh khach"),
                                 "ma khach", "khach le", "ten ghi tren don", "chi khac cach viet"),
        customers_bought=enable("customers_bought", _find(rows, "so don"), "khach da mua",
                                "it nhat mot don"),
        customer_by_revenue=enable("customer_by_revenue", _find(rows, "xac dinh khach"),
                                   "nhieu tien nhat xep theo doanh thu"),
        product_group=enable("product_group", _find(rows, "nhom hang"), "theo cot nhom hang",
                             "dung ten nhom"),
        product_group_share=enable("product_group_share", _find(rows, "mat hang"),
                                   "mau so ti trong nhom hang"),
        customer_group=enable("customer_group", _find(rows, "doanh thu theo nhom khach"),
                              "theo cot nhom khach", "khong thuoc nhom nao"),
        customer_group_share=enable("customer_group_share", _find(rows, "doanh thu theo nhom khach"),
                                    "chia cho doanh thu ca shop"),
        unit_quantity=enable("unit_quantity", _find(rows, "ban duoc bao nhieu don vi"),
                             "don vi co ban la", "mat hang co don vi co ban khac khong tinh"),
        calendar_month=exact("calendar_month", "thang m/y",
                             "ca thang tu ngay 1 den het ngay cuoi thang"),
        boolean_answer=exact("boolean_answer", "boolean", "true / false"),
        trend_rule=any(defines_trend(key) for key in rows)
        or any(re.search(r"\b(?:tang lien tuc|giam lien tuc|xu huong)\b", v) for v in rows.values())
        or any(defines_trend(folded) for folded, _ in lines),
        table_answer=exact("table_answer", "table", "columns rows o so la json number"),
        until_end_of_day=exact(
            "until_end_of_day", _find(rows, "den het ngay x"),
            "tinh trang luc het ngay do tinh moi phat sinh tu dau du lieu",
            "hoi so con lai con no con ton con hang tinh trang luc het ngay do tinh moi phat "
            "sinh tu dau du lieu den het ngay do khong phai ky thang nay",
        ),
        debt_orders=exact("debt_orders", "don duoc tinh vao no",
                          "don khong huy don tra hang van tinh neu con no"),
        debt_at_date=exact(
            "debt_at_date", "no mot don tai ngay x",
            "phan chua tra cua tong don gom phi giao tru cac khoan tra den het ngay x",
            "phan chua tra cua tong don so khach phai tra ca don da tru giam gia tren ca don "
            "gom phi giao tinh cac khoan tra den het ngay x con no cong cac don con no 0",
        ),
        debt_by_customer=exact(
            "debt_by_customer", "no theo khach",
            "don khach le van vao tong no nhung khong co dong trong bang",
            "xac dinh khach nhu 3 don khach le van tinh vao tong no nhung khong co dong "
            "trong bang no theo khach",
        ),
        debt_excluded=exact(
            "debt_excluded", _find(rows, "don cod", "san tmdt"),
            "khong phai khach no bo khoi cong no",
            "khong phai khach no ke ca khi chua thu duoc tien bo khoi cong no va tuoi no",
        ),
        rule_rows=tuple(features.items()),
    )
