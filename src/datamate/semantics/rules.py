from dataclasses import dataclass

from datamate.semantics.text import fold


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
    for line in rulebook.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            rows[fold(cells[0])] = fold(cells[1])
            originals[fold(cells[0])] = line.strip()
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
        rule_rows=tuple(features.items()),
    )
