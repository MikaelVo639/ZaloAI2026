"""Sales adapter driven by the business README.

Each business README lists its files and, for every column, a short meaning drawn from a
small vocabulary ("mã đơn", "ngày bán", "thành tiền sau giảm", "số lượng đã đổi ra đơn
vị cơ bản"...). This adapter reads those meanings instead of column names, so a schema
is supported because its README documents it, not because its headers match a list.

What it does, and only when the README and the split rulebook say so:

- finds the header row of report-style sheets (title rows above the header);
- unions files/sheets of one list (``_1``, ``_2026``, one sheet per month);
- fills blank order cells from the first row of the order (rulebook §8);
- joins line rows to the order list (date, status) and to the product list (code, name);
- converts quantities to the base unit with the README conversion list or table;
- marks a whole order returned when the README says how returns are written;
- drops identical rows of the same order (rulebook §8).

Every join is checked (duplicate parent keys, orphan lines, conflicting order states) and
money totals are reconciled where the files allow it. Any failure raises
``ReadmeSalesError``; the caller then abstains for that business.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import iter_rows
from datamate.ingestion.sales import Sale, parse_date, parse_minute
from datamate.ingestion.source_names import filename_key
from datamate.ingestion.status import StatusError, status_policy
from datamate.ingestion.status import order_state as canonical_order_state
from datamate.semantics.text import fold

ADAPTER_VERSION = "readme_sales.v1"
_HEADER_SCAN_ROWS = 15

# Folded README meaning (trailing parenthesis removed) -> semantic field.
_MEANINGS = {
    "ma don": "order_id",
    "ngay ban": "date",
    "trang thai don": "status",
    "ma hang": "product_id",
    "ten hang": "product_name",
    "ten": "name",
    "so luong": "quantity",
    "gia von mot don vi ban": "unit_cost_at_sale",
    "don vi tinh": "unit",
    "don vi co ban": "base_unit",
    "don gia": "unit_price",
    "thanh tien sau giam": "amount",
    "so luong da doi ra don vi co ban": "quantity_base",
    "so thu tu dong trong don": "line_no",
    "ten khach": "customer_name",
    "ma khach": "customer_id",
    "nhom hang": "product_group",
    "nhom khach": "customer_group",
    "giam gia": "discount",
    "don vi lon": "big_unit",
    "kenh ban": "channel",
    "ma nhan vien ban": "staff_id",
    # Order time for the rulebook shifts (ca sáng / ca chiều).
    "gio dat don": "time",
    "thoi diem dat don": "time",
}
_PREFIXES = (
    ("tien hang cua don", "order_goods"),
    ("tong tien hang cua don", "order_goods"),
    ("he so", "factor"),
    ("so luong khach tra lai", "returned_quantity"),
    ("hinh thuc thanh toan", "payment_method"),
    ("so thu tu don", "order_id"),
    ("so hoa don", "order_id"),
    ("ngay sua", "date"),
    ("hang ban", "product_name"),
    ("ten hang", "product_name"),
    ("cac viec da lam", "product_name"),
    ("thanh tien cua dong", "amount"),
    ("tong tien ca phieu", "amount"),
    ("don gia", "unit_price"),
    ("ten khach", "customer_name"),
    ("kenh ban", "channel"),
    ("tho lam phieu", "staff_id"),
    ("ghi chu cua don", "status_note"),
    ("ghi chu", "status_note"),
)
# Meanings that mark a list which is not about sales (imports, stock, prices, delivery...).
_OTHER_LISTS = (
    "ma phieu nhap", "ngay nhap", "ton kho", "ngay bat dau ap dung gia", "ngay giao",
    "han thanh toan", "ma phieu thu", "so luong nhap", "thoi diem thu",
)
_ORDER_FIELDS = ("order_id", "date", "status", "status_note", "customer_name", "customer_id",
                 "customer_group", "channel", "payment_method", "staff_id", "time")


class ReadmeSalesError(ValueError):
    """Typed failure with diagnostics that are safe to log (no row values)."""

    def __init__(self, code: str, message: str, **diagnostics: Any):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.diagnostics = {"code": code, **diagnostics}


@dataclass(frozen=True)
class Section:
    """One ``## Cột trong `file`[, sheet `name`]`` block of the README."""

    file: str
    sheet: str | None
    columns: dict[str, str]  # column name -> README meaning
    origin: str = "readme"  # where the column meanings come from

    @property
    def fields(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for column, meaning in self.columns.items():
            key = meaning_field(meaning)
            if key and key not in result:
                result[key] = column
        return result

    @property
    def label(self) -> str:
        return f"{self.file}::{self.sheet}" if self.sheet else self.file


@dataclass(frozen=True)
class Conventions:
    conversions: dict[tuple[str, str], tuple[Decimal, str]] = field(default_factory=dict)
    conversion_list: bool = False  # README lists every product that has a larger unit
    returns_as_negative_lines: bool = False
    sheet_scoped_order_ids: bool = False
    non_product_adjustments: bool = False
    evidence: tuple[str, ...] = ()


def meaning_field(meaning: str) -> str | None:
    text = fold(meaning).strip()
    if any(text.startswith(marker) for marker in _OTHER_LISTS):
        return "_other"
    # fold() drops punctuation, so cut a trailing "(...)" note before folding.
    base = fold(re.sub(r"\s*\(.*\)\s*\.?\s*$", "", meaning.strip())).strip()
    if base in _MEANINGS:
        return _MEANINGS[base]
    for prefix, key in _PREFIXES:
        if text.startswith(prefix):
            return key
    return None


def field_meanings(readme: str, field: str) -> list[str]:
    """README meanings of every documented column that holds ``field`` (e.g. the label
    list after "hình thức thanh toán của đơn: …"). Empty when the README documents none."""
    return [meaning for section in parse_sections(readme)
            for meaning in section.columns.values() if meaning_field(meaning) == field]


def parse_sections(readme: str) -> list[Section]:
    sections: list[Section] = []
    current: tuple[str, str | None] | None = None
    columns: dict[str, str] = {}
    for line in readme.splitlines():
        heading = re.match(r"##\s+Cột trong\s+`([^`]+)`(?:,\s*sheet\s+`([^`]+)`)?", line)
        if heading or line.startswith("## "):
            if current and columns:
                sections.append(Section(current[0], current[1], dict(columns)))
            current = (heading.group(1), heading.group(2)) if heading else None
            columns = {}
            continue
        item = re.match(r"\s*-\s+\*\*(.+?)\*\*:\s*(.+)$", line)
        if current and item:
            columns[item.group(1).strip()] = item.group(2).strip()
    if current and columns:
        sections.append(Section(current[0], current[1], dict(columns)))
    return sections


# Column meanings of the KiotViet-style POS export, as the READMEs of shops using that
# export document them. Used only for a business whose README says its files are a POS /
# sales-software export and documents no columns for the file; a table must carry every
# required column of a list for that list to be recognised.
_KIOTVIET = (
    ({"Mã hóa đơn", "Thời gian", "Trạng thái", "Tổng tiền hàng"}, {
        "Mã hóa đơn": "mã đơn", "Thời gian": "ngày bán", "Trạng thái": "trạng thái đơn",
        "Tổng tiền hàng": "tổng tiền hàng của đơn (cộng thành tiền các dòng), trước giảm "
                          "giá của đơn",
        "Mã KH": "mã khách (trống nếu khách lẻ)",
        "Khách hàng": "tên khách (Khách lẻ nếu không lưu)",
        "Kênh bán": "kênh bán", "Người bán": "mã nhân viên bán",
        "Phương thức thanh toán": "hình thức thanh toán",
        "Thời gian tạo": "thời điểm đặt đơn (ngày giờ)",
    }),
    ({"Mã hóa đơn", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Thành tiền",
      "Số lượng quy đổi"}, {
        "Mã hóa đơn": "mã đơn", "STT": "số thứ tự dòng trong đơn", "Mã hàng": "mã hàng",
        "Tên hàng": "tên hàng", "Đơn vị tính": "đơn vị tính", "Số lượng": "số lượng",
        "Đơn giá": "đơn giá", "Giảm giá": "giảm giá", "Thành tiền": "thành tiền sau giảm",
        "Số lượng quy đổi": "số lượng đã đổi ra đơn vị cơ bản",
    }),
    ({"Mã hàng", "Tên hàng", "ĐVT", "Nhóm hàng(3 Cấp)"}, {
        "Mã hàng": "mã hàng", "Tên hàng": "tên", "ĐVT": "đơn vị cơ bản",
        "Nhóm hàng(3 Cấp)": "nhóm hàng",
    }),
    # Customer list with its group, as documented in KiotViet-export READMEs.
    ({"Mã khách hàng", "Tên khách hàng", "Nhóm khách hàng"}, {
        "Mã khách hàng": "mã khách", "Tên khách hàng": "tên khách",
        "Nhóm khách hàng": "nhóm khách",
    }),
)
# SQLite POS database (tables don_hang / chi_tiet_don / san_pham). The README of such a
# shop documents the line amount (TriGia) and base quantity (SLQuyDoi) itself.
_SQLITE_POS = (
    ({"MaHoaDon", "NgayLap", "TinhTrang", "TongTienHang"}, {
        "MaHoaDon": "mã đơn", "NgayLap": "ngày bán", "TinhTrang": "trạng thái đơn",
        "TongTienHang": "tổng tiền hàng của đơn (cộng thành tiền các dòng), trước giảm giá "
                        "của đơn",
        "MaKhachHang": "mã khách (trống nếu khách lẻ)", "KenhBanHang": "kênh bán",
        "MaNhanVien": "mã nhân viên bán", "PhuongThucTT": "hình thức thanh toán",
        "ThoiGianLap": "thời điểm đặt đơn (ngày giờ)",
    }),
    ({"MaHoaDon", "SoThuTu", "MaHangHoa", "SL", "SLQuyDoi", "TriGia"}, {
        "MaHoaDon": "mã đơn", "SoThuTu": "số thứ tự dòng trong đơn", "MaHangHoa": "mã hàng",
        "DonViTinh": "đơn vị tính", "SL": "số lượng",
        "SLQuyDoi": "số lượng đã đổi ra đơn vị cơ bản", "GiaDonVi": "đơn giá",
        "ChietKhau": "giảm giá", "TriGia": "thành tiền sau giảm",
    }),
    ({"MaHangHoa", "TenGoi", "DonViCoSo", "DanhMuc"}, {
        "MaHangHoa": "mã hàng", "TenGoi": "tên", "DonViCoSo": "đơn vị cơ bản",
        "DanhMuc": "nhóm hàng",
    }),
    ({"MaKhachHang", "TenGoi", "LoaiKhachHang"}, {
        "MaKhachHang": "mã khách", "TenGoi": "tên khách", "LoaiKhachHang": "nhóm khách",
    }),
)
# "BanHang_" sales-software export (HoaDon / ChiTietHoaDon / HangHoa / BanTheoLo).
_BANHANG = (
    ({"Order ID", "Ngày bán", "Trạng thái", "Tiền hàng trước giảm"}, {
        "Order ID": "mã đơn", "Ngày bán": "ngày bán", "Trạng thái": "trạng thái đơn",
        "Tiền hàng trước giảm": "tổng tiền hàng của đơn (cộng thành tiền các dòng), trước "
                                "giảm giá của đơn",
        "Mã KH mua": "mã khách (trống nếu khách lẻ)",
        "Tên KH": "tên khách (Khách lẻ nếu không lưu)", "Nguồn đơn": "kênh bán",
        "Mã NV bán": "mã nhân viên bán", "Phương thức TT": "hình thức thanh toán",
        "Giờ tạo đơn": "giờ đặt đơn",
    }),
    ({"Mã giao dịch", "TT dòng", "Ma hang", "Slg", "SL cơ bản", "Thanh tien"}, {
        "Mã giao dịch": "mã đơn", "TT dòng": "số thứ tự dòng trong đơn", "Ma hang": "mã hàng",
        "Chi tiết hàng": "tên hàng", "Slg": "số lượng", "Đ.vị": "đơn vị tính",
        "SL cơ bản": "số lượng đã đổi ra đơn vị cơ bản", "Giá niêm yết": "đơn giá",
        "Khuyến mãi": "giảm giá", "Thanh tien": "thành tiền sau giảm",
    }),
    ({"ma_sp", "Món", "Đơn vị", "Phân loại"}, {
        "ma_sp": "mã hàng", "Món": "tên", "Đơn vị": "đơn vị cơ bản", "Phân loại": "nhóm hàng",
    }),
    ({"Customer ID", "ten_kh", "Hạng", "Loại hình khách"}, {
        "Customer ID": "mã khách", "ten_kh": "tên khách", "Hạng": "nhóm khách",
    }),
    ({"Mã hóa đơn", "SL trả (ĐVT cơ bản)"}, {
        "Mã hóa đơn": "mã đơn", "Lô": "số lô bán",
        "SL trả (ĐVT cơ bản)": "số lượng khách trả lại (đơn vị cơ bản); lớn hơn 0: đơn đó "
                               "có trả hàng",
    }),
)
_BANHANG_COMBINED = (
    ({"Đơn số", "Ngày HĐ", "Trạng thái giao", "Hàng hóa", "S.L", "Thành tiền (đ)"}, {
        "Đơn số": "mã đơn", "Ngày HĐ": "ngày bán", "Trạng thái giao": "trạng thái đơn",
        "Hàng hóa": "tên hàng", "S.L": "số lượng", "ĐV": "đơn vị tính",
        "Giá 1 đv": "đơn giá", "Thành tiền (đ)": "thành tiền sau giảm",
        "SL (ĐV cơ bản)": "số lượng đã đổi ra đơn vị cơ bản",
        "Khách hàng (tên)": "tên khách", "Mã khách": "mã khách", "Nguồn": "kênh bán",
        "Mã NV bán": "mã nhân viên bán", "Giờ tạo đơn": "giờ đặt đơn",
        "Hình thức thanh toán": "hình thức thanh toán",
    }),
)
POS_PROFILES = (
    ("pos_export_kiotviet.v1", _KIOTVIET),
    ("pos_export_sqlite.v1", _SQLITE_POS),
    ("sales_export_banhang.v1", _BANHANG),
    ("sales_export_combined.v1", _BANHANG_COMBINED),
)

_PHONE_LEDGER_COLUMNS = {
    "STT": "mã đơn", "Ngày": "ngày bán", "Khách": "tên khách",
    "Hàng": "tên hàng", "SL": "số lượng", "Giá": "đơn giá",
    "Thành tiền": "thành tiền sau giảm", "Ghi chú": "ghi chú của đơn",
}


def _header_names(table: Table) -> set[str]:
    """Header of a table, looking below title rows of an xlsx report if needed."""
    names = set(table.columns)
    if table.path.suffix.lower() != ".xlsx" or len(names - {c for c in names
                                                           if c.startswith("_empty_")}) > 2:
        return names
    from openpyxl import load_workbook

    book = load_workbook(table.path, read_only=True, data_only=True, keep_links=False)
    try:
        for values in book[table.name].iter_rows(max_row=_HEADER_SCAN_ROWS, values_only=True):
            cells = {str(v).strip() for v in values if v is not None and str(v).strip()}
            if len(cells) > 2:
                return cells
    finally:
        book.close()
    return names


def profile_sections(catalog: Catalog, documented: list[Section]) -> list[Section]:
    """Sections for undocumented tables of a POS / sales-software export.

    One export profile per business: the profile must recognise both a sales line list
    and an order list among the undocumented tables, otherwise nothing is added.
    """
    text = fold(catalog.readme)
    covered = {filename_key(s.file) for s in documented}
    headers = [(table, _header_names(table)) for table in catalog.tables
               if filename_key(table.path.name) not in covered]

    # Handwritten phone ledgers document the semantics in their conventions instead of
    # repeating a column dictionary.  Require the complete convention and exact header
    # shape before supplying those meanings.
    phone_evidence = (
        "cac dong cung stt la mot don" in text
        and "khach tra la so tien khach tra cho ca don" in text
        and "ghi chu cua don ghi khach tra lai" in text
    )
    phone_required = set(_PHONE_LEDGER_COLUMNS)
    if phone_evidence:
        sections = [Section(table.path.name, table.name, _PHONE_LEDGER_COLUMNS,
                            origin="phone_ledger_conventions.v1")
                    for table, header in headers if phone_required <= header]
        if sections:
            return sections

    if not ("pos" in text.split() or "phan mem ban hang" in text):
        return []
    for version, lists in POS_PROFILES:
        sections = []
        for table, header in headers:
            for required, meanings in lists:
                if required <= header:
                    columns = {c: m for c, m in meanings.items() if c in header}
                    if "thanh tien sau giam" in map(fold, columns.values()):
                        cost = sale_cost_column(catalog.readme, table, header)
                        if cost:
                            columns[cost] = "giá vốn một đơn vị bán"
                    sections.append(Section(table.path.name, table.name, columns,
                                            origin=version))
                    break
        fields = [set(section.fields) for section in sections]
        has_lines = any("amount" in f for f in fields)
        has_orders = (any("order_goods" in f for f in fields)
                      or any({"order_id", "date", "status", "amount"} <= f for f in fields))
        if has_lines and has_orders:
            return sections
    return []


def sale_cost_column(readme: str, table: Table, header: set[str]) -> str | None:
    """A cost column explicitly documented for one selling unit."""
    for line in readme.splitlines():
        heading, separator, definition = line.partition(":")
        if not (separator and "gia von" in fold(heading)
                and "la gia von mot don vi ban" in fold(definition)):
            continue
        value = re.search(r"\*\*([^*]+)\*\*|\x60([^\x60]+)\x60", definition)
        if value:
            name = value.group(1) or value.group(2)
            source, sep, column = name.rpartition(".")
            if sep and source != table.name:
                continue
            if (column or name) in header:
                return column or name
    return None


def optional_sale_cost(value: Any) -> Decimal | None:
    if _blank(value):
        return None
    try:
        cost = parse_amount(value)
        return cost if cost >= 0 else None
    except ValueError:
        return None


def parse_conventions(readme: str, *, strict: bool = False) -> Conventions:
    conversions: dict[tuple[str, str], tuple[Decimal, str]] = {}
    evidence: list[str] = []
    returns = False
    listed = False
    sheet_scoped = False
    non_products = False
    in_conversion = False
    for line in readme.splitlines():
        text = fold(line)
        if "moi thang mot sheet" in text and not sheet_scoped:
            sheet_scoped = True
            evidence.append(line.strip())
        if ("voucher" in text and "dung" in text and "diem" in text
                and "phi ship" in text and "khong phai hang" in text
                and not non_products):
            non_products = True
            evidence.append(line.strip())
        if line.startswith("- "):
            in_conversion = "quy doi don vi" in text
            if in_conversion and line.rstrip().endswith(":"):  # the list follows
                listed = True
                evidence.append(line.strip())
            if "tra hang" in text and "so luong am" in text and "co tra hang" in text:
                returns = True
                evidence.append(line.strip())
            continue
        item = re.match(r"\s+-\s+(.+?):\s*1\s+(.+?)\s*=\s*([\d.,]+)\s+(.+?)\s*$", line)
        if in_conversion and item:
            factor = Decimal(item.group(3).replace(",", "."))
            key = (_name_key(item.group(1)), _name_key(item.group(2)))
            value = (factor, _name_key(item.group(4)))
            if strict and key in conversions and conversions[key] != value:
                raise ValueError("Conflicting README conversion factors")
            conversions[key] = value
    return Conventions(conversions, listed, returns, sheet_scoped, non_products,
                       tuple(evidence))


def _name_key(value: Any) -> str:
    return re.sub(r"\s+", " ", fold(str(value))).strip()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


# ---------------------------------------------------------------- reading values

def parse_amount(value: Any) -> Decimal:
    """Money as written in shop files: 55,000 · 45,000.0000 · 55.000đ · 25k · 1tr440."""
    if isinstance(value, bool) or _blank(value):
        raise ValueError("missing number")
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
    else:
        # Not fold(): it drops the "." and "," separators the value depends on.
        text = str(value).strip().lower().replace("\u00a0", "").replace(" ", "")
        text = re.sub(r"(?:vnđ|vnd|đồng|dong|đ|₫)$", "", text)
        million = re.fullmatch(r"(-?\d+)tr(\d{0,3})", text)
        if million:
            tail = million.group(2)
            result = Decimal(million.group(1)) * 1000000
            if tail:
                part = Decimal(tail.ljust(3, "0")) * 1000
                result += -part if text.startswith("-") else part
            return result
        multiplier = Decimal(1)
        if text.endswith("tr"):
            text, multiplier = text[:-2], Decimal(1000000)
            # "19,782tr", "16,56tr", "1.5tr": one separator before "tr" is a decimal point
            # (19.782 million), never thousands (19,782 million).
            if re.fullmatch(r"-?\d+[.,]\d+", text):
                text = text.replace(",", ".")
                return Decimal(text) * multiplier
        elif text.endswith("k"):
            text, multiplier = text[:-1], Decimal(1000)
        result = _plain_number(text) * multiplier
    if not result.is_finite():
        raise ValueError("non-finite number")
    return result


def parse_quantity(value: Any) -> Decimal:
    """Counts as written: 3 · '3' · '3 chai' · '1,5 kg'."""
    if isinstance(value, bool) or _blank(value):
        raise ValueError("missing quantity")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    # The number comes first; the unit after it may contain digits ("1 bao 25kg", "2 xe 4m3").
    match = re.fullmatch(r"\s*(-?\d[\d.,]*)\s*(?:[^\d\s.,].*)?", str(value))
    if not match:
        raise ValueError("invalid quantity")
    return _plain_number(match.group(1), decimal_comma=True)


def _plain_number(text: str, decimal_comma: bool = False) -> Decimal:
    if not re.fullmatch(r"-?[\d.,]+", text or ""):
        raise ValueError("invalid number")
    if "," in text and "." in text:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        if re.fullmatch(rf"-?\d{{1,3}}(?:\{sep}\d{{3}})+", text) and not (
                decimal_comma and sep == "," and text.count(",") == 1):
            text = text.replace(sep, "")
        elif text.count(sep) == 1:
            text = text.replace(sep, ".")
        else:
            raise ValueError("ambiguous separators")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid number") from exc


def parse_day(value: Any, sheet: str = "") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 20000 < value < 80000:
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = str(value).strip()
    vietnamese = re.fullmatch(r"(\d{1,2})\s*(?:thg|thang)\s*(\d{1,2})\s*(20\d{2})",
                              fold(text))
    if vietnamese:
        return date(int(vietnamese.group(3)), int(vietnamese.group(2)),
                    int(vietnamese.group(1)))
    if re.fullmatch(r"\d{5}", text) and 20000 < int(text) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(text))
    short = re.fullmatch(r"(\d{1,2})/(\d{1,2})", text)
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", sheet)
    if short and year:
        return date(int(year.group(1)), int(short.group(2)), int(short.group(1)))
    return parse_date(text, sheet)


# ---------------------------------------------------------------- tables

def _matching_tables(catalog: Catalog, section: Section) -> list[Table]:
    wanted = filename_key(section.file)
    placeholder = re.compile(r"<[^<>]+>")
    is_template = placeholder.search(wanted) is not None
    if is_template:
        parts = placeholder.split(wanted)
        markers = placeholder.findall(wanted)
        pattern = "^" + "".join(
            re.escape(part) + (r"\d+" if index < len(markers) else "")
            for index, part in enumerate(parts)
        ) + "$"
        files = {t.path for t in catalog.tables
                 if re.fullmatch(pattern, filename_key(t.path.name), re.IGNORECASE)}
    else:
        files = {t.path for t in catalog.tables if filename_key(t.path.name) == wanted}
    if len(files) > 1 and not is_template:
        raise ReadmeSalesError("AMBIGUOUS_SOURCE", "Several files match one README source",
                               source=section.label)
    matches = [t for t in catalog.tables
               if t.path in files and (section.sheet is None or t.name == section.sheet)]
    if section.sheet is None and len(matches) > 1:
        documented_columns = set(section.columns)
        compatible = [table for table in matches
                      if documented_columns <= _header_names(table)]
        if compatible:
            matches = compatible
    return matches


def _records(table: Table, columns: Iterable[str], *,
             allowed_extra_columns: frozenset[str] | None = None
             ) -> Iterator[tuple[int, dict[str, Any]]]:
    """Rows of ``table`` keyed by header; the header may sit below title rows (xlsx)."""
    needed = set(columns)

    def validate_header(names: Iterable[str]) -> None:
        if allowed_extra_columns is None:
            return
        unexpected = sorted(name for name in set(names) - needed - allowed_extra_columns
                            if name and not re.fullmatch(r"_empty_\d+", name))
        if unexpected:
            raise ReadmeSalesError("SCHEMA_MISMATCH", "Source has undocumented columns",
                                   table=table.key, unexpected=unexpected)

    if needed <= set(table.columns):
        validate_header(table.columns)
        for ordinal, row in enumerate(iter_rows(table), 1):
            yield ordinal, row
        return
    if table.path.suffix.lower() != ".xlsx":
        raise ReadmeSalesError("SCHEMA_MISMATCH", "README columns not found",
                               table=table.key, missing=sorted(needed - set(table.columns)))
    from openpyxl import load_workbook

    book = load_workbook(table.path, read_only=True, data_only=True, keep_links=False)
    try:
        rows = book[table.name].iter_rows(values_only=True)
        header = None
        for _ in range(_HEADER_SCAN_ROWS):
            values = next(rows, None)
            if values is None:
                break
            names = [str(v).strip() if v is not None else "" for v in values]
            if needed <= set(names):
                header = names
                break
        if header is None:
            raise ReadmeSalesError("HEADER_NOT_FOUND", "README columns not found near the top",
                                   table=table.key)
        validate_header(header)
        for ordinal, values in enumerate(rows, 1):
            if any(v is not None and (not isinstance(v, str) or v.strip()) for v in values):
                yield ordinal, {name: value for name, value in zip(header, values) if name}
    finally:
        book.close()


@dataclass
class _Role:
    sections: list[Section]
    tables: list[tuple[Section, Table]]


_OPTIONAL_ROLES = frozenset({"customers"})


def _classify(sections: list[Section], catalog: Catalog) -> dict[str, _Role]:
    roles: dict[str, _Role] = {}
    for section in sections:
        fields = set(section.fields)
        classified_fields = (fields - {"status_note"}) | ({"status"} if "status_note" in fields else set())
        if "_other" in fields:
            continue
        if {"big_unit", "factor"} <= fields:
            role = "conversions"
        elif {"order_id", "returned_quantity"} <= fields:
            role = "lots"
        elif "amount" in fields and fields & {"product_name", "product_id"}:
            role = "lines"
        elif ("order_id" in classified_fields and classified_fields & {"date", "status"}
              and "quantity" not in classified_fields):
            role = "orders"
        elif ("customer_id" in fields and "customer_group" in fields
              and not fields & {"order_id", "amount", "quantity"}):
            role = "customers"
        elif ("product_id" in fields and fields & {"product_name", "name"}
              and not fields & {"order_id", "quantity"}):
            role = "products"
        else:
            continue
        tables = _matching_tables(catalog, section)
        if not tables and role in _OPTIONAL_ROLES:
            continue  # context only (customer groups): a missing list must not block sales
        if not tables:
            raise ReadmeSalesError("SOURCE_MISSING", "README lists a file that is not present",
                                   source=section.label, role=role)
        entry = roles.setdefault(role, _Role([], []))
        entry.sections.append(section)
        entry.tables.extend((section, table) for table in tables)
    for role, entry in roles.items():
        signatures = {frozenset(s.fields) for s in entry.sections}
        if len(signatures) != 1:
            raise ReadmeSalesError("AMBIGUOUS_ROLE", "Files of one role disagree on fields",
                                   role=role, sources=[s.label for s in entry.sections])
    return roles


def _role_rows(entry: _Role) -> Iterator[tuple[str, int, dict[str, Any]]]:
    for section, table in entry.tables:
        fields = section.fields
        for ordinal, raw in _records(table, section.columns):
            row = {key: raw.get(column) for key, column in fields.items()}
            row["_header_repeat"] = bool(fields) and all(
                _name_key(row.get(key)) == _name_key(column)
                for key, column in fields.items()
            )
            # Rulebook §8 compares *every* cell, not only the mapped ones.
            row["_cells"] = tuple(sorted((str(k), repr(v)) for k, v in raw.items()))
            yield table.name, ordinal, row


# ---------------------------------------------------------------- rules

def _rule_line(rulebook: str, key: str, *tokens: str) -> str | None:
    for line in rulebook.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            if fold(cells[0]) == key and all(t in fold(cells[1]) for t in tokens):
                return line.strip()
    return None


@dataclass(frozen=True)
class _Rules:
    status: Any
    blank_cells_inherit: str | None
    duplicate_rows_once: str | None
    whole_order_returns: str | None
    service_fee: str | None
    status_in_notes: str | None
    evidence: tuple[str, ...]


def _rules(rulebook: str) -> _Rules:
    try:
        status = status_policy(rulebook)
    except StatusError as exc:
        raise ReadmeSalesError("RULE_CAPABILITY_MISSING", str(exc)) from exc
    blank = _rule_line(rulebook, "o de trong o cac dong sau cua cung mot nhom dong",
                       "giong o do o dong dau nhom")
    duplicates = _rule_line(rulebook, "hai dong cua cung mot don giong nhau o moi o",
                            "tinh mot lan")
    returns = _rule_line(rulebook, "don tinh vao doanh thu", "ca don")
    fee = _rule_line(rulebook, "mat hang", "phi phuc vu", "khong xep hang")
    notes = _rule_line(rulebook, "so khong co cot trang thai", "ghi chu", "hoan thanh")
    evidence = tuple(x for x in (*status.evidence, blank, duplicates, returns, fee, notes) if x)
    return _Rules(status, blank, duplicates, returns, fee, notes, evidence)


_NOTE_RETURNED = re.compile(r"\b(?:khach tra lai|tra lai hang|tra hang|hoan tra(?: hang)?)\b")
_NOTE_CANCELLED = re.compile(r"\b(?:cancel|canceled|cancelled)\b")
# "chưa giao": a README that writes only cancelled / delivering orders in the notes uses it
# for orders not delivered yet, i.e. the rulebook's "đang giao".
_NOTE_DELIVERING = re.compile(r"\b(?:dang giao|dang ship|cho giao|chua giao)\b")


def _note_state(value: Any) -> str:
    """Order state named by a free-form note; a note naming no state is "hoàn thành".

    A note that is exactly a status label is read like a status cell. Otherwise the note
    must mention the state: "huỷ" counts only with its tone mark, because the folded
    "huy" is also the given name Huy ("giao cho anh Huy"). A note naming two different
    states is not guessed.
    """
    state = canonical_order_state(value)
    if state is not None:
        return state
    raw = unicodedata.normalize("NFC", str(value or "")).lower()
    text = _name_key(value or "")
    found = set()
    if _NOTE_RETURNED.search(text):
        found.add("tra hang")
    if "huỷ" in raw or "hủy" in raw or _NOTE_CANCELLED.search(text):
        found.add("huy")
    if _NOTE_DELIVERING.search(text):
        found.add("dang giao")
    if len(found) > 1:
        raise StatusError(f"Order note names several states: {value!r}")
    return found.pop() if found else "hoan thanh"


def _resolve_status(value: Any, rules: _Rules, *, from_note: bool) -> str:
    """Resolve a dedicated status cell, or a free-form note when the rulebook permits it."""
    if not from_note:
        return rules.status.resolve(value)
    if rules.status_in_notes is None:
        raise StatusError("The split rulebook does not define statuses written in notes")
    return rules.status.resolve(_note_state(value))


# ---------------------------------------------------------------- loading

def load_readme_sales(catalog: Catalog, rulebook: str
                      ) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    """Load sales of one business from its README-described lists.

    Returns ``(sales, evidence, ranking_eligible)``.
    """
    sections = parse_sections(catalog.readme)
    sections += profile_sections(catalog, sections)
    if not sections:
        raise ReadmeSalesError("README_COLUMNS_MISSING", "README has no column descriptions")
    roles = _classify(sections, catalog)
    rules = _rules(rulebook)
    conventions = parse_conventions(catalog.readme)
    if "lines" in roles:
        return _from_lines(catalog, roles, rules, conventions)
    if "orders" in roles and "order_goods" in roles["orders"].sections[0].fields:
        return _from_orders(roles, rules)
    raise ReadmeSalesError("NO_SALES_LIST", "README describes no sales line list",
                           roles=sorted(roles))


def _orders_index(roles: dict[str, _Role], rules: _Rules
                  ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    orders: dict[str, dict[str, Any]] = {}
    counts = Counter()
    if "orders" not in roles:
        return orders, counts
    for sheet, _ordinal, row in _role_rows(roles["orders"]):
        if row["_header_repeat"]:
            counts["repeated_headers_skipped"] += 1
            continue
        if _blank(row.get("order_id")):
            counts["blank_key_rows"] += 1
            continue
        key = str(row["order_id"]).strip()
        if (_blank(row.get("date")) and _blank(row.get("status"))
                and fold(key).startswith("tong ")):
            counts["summary_rows"] += 1
            continue
        if key in orders:
            if rules.duplicate_rows_once and row["_cells"] == orders[key]["_cells"]:
                counts["duplicate_rows_dropped"] += 1
                continue
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Order list repeats an order id",
                                   role="orders")
        orders[key] = {**row, "_sheet": sheet}
    counts["orders"] = len(orders)
    return orders, counts


def _products_index(roles: dict[str, _Role]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    if "products" not in roles:
        return by_id, by_name
    ambiguous: set[str] = set()
    for _sheet, _ordinal, row in _role_rows(roles["products"]):
        if _blank(row.get("product_id")):
            continue
        key = str(row["product_id"]).strip()
        if key in by_id:
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Product list repeats a code",
                                   role="products")
        name = row.get("name", row.get("product_name"))
        record = {"id": key, "name": None if _blank(name) else str(name).strip(),
                  "base_unit": row.get("base_unit"), "group": row.get("product_group")}
        by_id[key] = record
        if record["name"]:
            name_key = _name_key(record["name"])
            if name_key in by_name:
                ambiguous.add(name_key)
            by_name[name_key] = record
    for name_key in ambiguous:
        by_name.pop(name_key, None)
    return by_id, by_name


def _customers_index(roles: dict[str, _Role]) -> dict[str, dict[str, str | None]]:
    """Customer code -> {group, name}, from the customer list."""
    customers: dict[str, dict[str, str | None]] = {}
    if "customers" not in roles:
        return customers
    for _sheet, _ordinal, row in _role_rows(roles["customers"]):
        if _blank(row.get("customer_id")):
            continue
        key = str(row["customer_id"]).strip()
        if key in customers:
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Customer list repeats a code",
                                   role="customers")
        customers[key] = {"group": _text(row.get("customer_group")),
                          "name": _text(row.get("customer_name", row.get("name")))}
    return customers


def joined_customer_sources(
    catalog: Catalog, orders: Table,
) -> tuple[dict[str, str], dict[str, dict[str, str | None]], str | None]:
    """Optional customer fields documented for the joined adapter's actual order table."""
    order_fields: list[dict[str, str]] = []
    lists = []
    for section in parse_sections(catalog.readme):
        fields = section.fields
        if not fields.keys() & {"customer_id", "customer_name", "customer_group"}:
            continue
        if "order_id" not in fields:
            if {"customer_id", "customer_group"} <= fields.keys():
                lists.append(section)
            continue
        if section.file != orders.path.name or section.sheet not in (None, orders.name):
            continue
        order_fields.append({key: column for key, column in fields.items()
                             if key in {"customer_id", "customer_name", "customer_group"}})
    if not order_fields:
        return {}, {}, None
    fields = order_fields[0]
    if (any(candidate != fields for candidate in order_fields)
            or set(fields.values()) - set(orders.columns)):
        return {}, {}, "CUSTOMER_ORDER_MAPPING_AMBIGUOUS"
    if "customer_id" not in fields:
        return fields, {}, None
    try:
        return fields, _customers_index(_classify(lists, catalog)), None
    except ReadmeSalesError as exc:
        return fields, {}, exc.code


def _conversion_table(roles: dict[str, _Role], conventions: Conventions
                      ) -> tuple[dict[tuple[str, str], tuple[Decimal, str]], bool]:
    conversions = dict(conventions.conversions)
    complete = conventions.conversion_list
    if "conversions" in roles:
        complete = True
        for _sheet, _ordinal, row in _role_rows(roles["conversions"]):
            product = row.get("product_name", row.get("product_id"))
            if _blank(product) or _blank(row.get("big_unit")):
                continue
            factor = parse_quantity(row.get("factor"))
            conversions[(_name_key(product), _name_key(row["big_unit"]))] = (
                factor, _name_key(row.get("base_unit") or ""))
    return conversions, complete


def _from_lines(catalog: Catalog, roles: dict[str, _Role], rules: _Rules,
                conventions: Conventions) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    line_fields = set(roles["lines"].sections[0].fields)
    orders, order_counts = _orders_index(roles, rules)
    products_by_id, products_by_name = _products_index(roles)
    conversions, conversion_complete = _conversion_table(roles, conventions)
    customer_groups = _customers_index(roles)
    has_order_ids = "order_id" in line_fields
    order_id_column = roles["lines"].sections[0].fields.get("order_id", "")
    sheet_scoped_order_ids = (conventions.sheet_scoped_order_ids
                              and _name_key(order_id_column) == "stt")
    if not has_order_ids and orders:
        raise ReadmeSalesError("MAPPING_INCOMPLETE", "Lines cannot be joined to orders")

    counts: Counter[str] = Counter()
    seen: set[tuple] = set()
    lines: list[dict[str, Any]] = []
    group: dict[str, Any] = {}
    first_values: dict[str, dict[str, Any]] = defaultdict(dict)
    order_keys = [k for k in ("order_id", "date", "status", "status_note") if k in line_fields]
    count_keys = [k for k in ("quantity", "quantity_base") if k in line_fields]
    previous_sheet: str | None = None
    for sheet, ordinal, row in _role_rows(roles["lines"]):
        counts["line_rows_raw"] += 1
        if sheet != previous_sheet:
            group = {}
            previous_sheet = sheet
        if row["_header_repeat"]:
            counts["repeated_headers_skipped"] += 1
            continue
        raw_order_id = "" if _blank(row.get("order_id")) else str(row["order_id"]).strip()
        if orders and raw_order_id not in orders and fold(raw_order_id).startswith("tong "):
            counts["summary_rows_skipped"] += 1
            continue
        if (_blank(row.get("order_id")) and _blank(row.get("product_id"))
                and _blank(row.get("product_name")) and _blank(row.get("quantity"))
                and _blank(row.get("quantity_base"))):
            counts["summary_rows_skipped"] += 1
            continue
        if all(_blank(row.get(k)) for k in order_keys + count_keys):
            # Subtotal, daily total or note rows ("Cộng", "Tổng ngày 01/03", "Ghi chú: ...")
            # carry no order and no quantity: they are not sales lines.
            counts["summary_rows_skipped"] += 1
            continue
        if has_order_ids:
            if _blank(row.get("order_id")):
                if not group or rules.blank_cells_inherit is None:
                    raise ReadmeSalesError("MISSING_KEY", "Line without order id",
                                           role="lines", sheet=sheet)
                counts["order_cells_inherited"] += 1
                row = {**row, **{k: v for k, v in group.items() if _blank(row.get(k))}}
            raw_key = str(row["order_id"]).strip()
            key = f"{sheet}::{raw_key}" if sheet_scoped_order_ids else raw_key
            seen_first = first_values[key]
            for name in _ORDER_FIELDS:
                if name in line_fields:
                    if _blank(row.get(name)) and name in seen_first:
                        if rules.blank_cells_inherit is None:
                            raise ReadmeSalesError("MISSING_VALUE", "Blank order cell",
                                                   field=name)
                        row[name] = seen_first[name]
                    elif not _blank(row.get(name)):
                        seen_first.setdefault(name, row[name])
            group = {k: row.get(k) for k in _ORDER_FIELDS if k in line_fields}
            fingerprint = (key, sheet, row["_cells"])
            if fingerprint in seen and rules.duplicate_rows_once:
                counts["duplicates_removed"] += 1
                continue
            seen.add(fingerprint)
        else:
            key = f"{sheet}#{ordinal}"
            counts["rows_as_records"] += 1
        lines.append({**row, "_order": key, "_sheet": sheet})

    status_counts: Counter[str] = Counter()
    order_state: dict[str, dict[str, Any]] = {}
    orphan_lines = 0
    for line in lines:
        key = line["_order"]
        parent = orders.get(key) if orders else None
        if orders and parent is None:
            orphan_lines += 1
            continue
        state = order_state.setdefault(key, {"days": set(), "statuses": set(), "times": set(),
                                             "negative": False, "returned": False})
        raw_date = line.get("date") if "date" in line_fields else (parent or {}).get("date")
        if "status" in line_fields:
            raw_status, status_from_note = line.get("status"), False
        elif parent is not None and "status" in parent:
            raw_status, status_from_note = parent["status"], False
        elif "status_note" in line_fields:
            raw_status, status_from_note = line.get("status_note"), True
        else:
            raw_status = (parent or {}).get("status_note")
            status_from_note = parent is not None and "status_note" in parent
        if _blank(raw_date) or (_blank(raw_status) and not status_from_note):
            raise ReadmeSalesError("MISSING_VALUE", "Order date or status is missing",
                                   order_source="lines" if parent is None else "orders")
        try:
            status = _resolve_status(raw_status, rules, from_note=status_from_note)
        except StatusError as exc:
            raise ReadmeSalesError("UNKNOWN_STATUS", str(exc)) from exc
        sheet = line["_sheet"] if "date" in line_fields else (parent or {}).get("_sheet", "")
        try:
            day = parse_day(raw_date, sheet)
        except (TypeError, ValueError) as exc:
            raise ReadmeSalesError("DATE_INVALID", "Order date could not be parsed") from exc
        line["_status"], line["_day"] = status, day
        quantity = None
        if not _blank(line.get("quantity")):
            try:
                quantity = parse_quantity(line["quantity"])
            except ValueError as exc:
                raise ReadmeSalesError("NUMERIC_AMBIGUOUS", "Quantity could not be parsed",
                                       field="quantity") from exc
        line["_quantity"] = quantity
        negative = quantity is not None and quantity < 0
        state["negative"] |= negative
        if not (negative and conventions.returns_as_negative_lines):
            state["days"].add(day)
            state["statuses"].add(status)
            raw_time = line.get("time") if "time" in line_fields else (parent or {}).get("time")
            state["times"].add(parse_minute(raw_time))
    if orphan_lines:
        raise ReadmeSalesError("ORPHAN_FK", "Lines reference orders missing from the order list",
                               orphan_lines=orphan_lines)

    if "lots" in roles:
        for _sheet, _ordinal, row in _role_rows(roles["lots"]):
            if _blank(row.get("order_id")) or _blank(row.get("returned_quantity")):
                continue
            if parse_quantity(row["returned_quantity"]) > 0:
                key = str(row["order_id"]).strip()
                if key in order_state:
                    order_state[key]["returned"] = True

    final_status: dict[str, str] = {}
    final_day: dict[str, date] = {}
    final_time: dict[str, int | None] = {}
    for key, state in order_state.items():
        statuses = set(state["statuses"])
        if state["returned"] or (state["negative"] and conventions.returns_as_negative_lines):
            if rules.whole_order_returns is None:
                raise ReadmeSalesError("RULE_CAPABILITY_MISSING",
                                       "Returned order needs the whole-order return rule")
            statuses = {"returned"}
        elif "returned" in statuses and len(statuses) > 1:
            statuses = {"returned"}
        if len(statuses) != 1 or len(state["days"]) != 1:
            raise ReadmeSalesError("CONFLICTING_ORDER", "An order has several dates or states",
                                   states=len(statuses), days=len(state["days"]))
        final_status[key] = next(iter(statuses))
        final_day[key] = next(iter(state["days"]))
        # One clock time per order; lines that disagree or a missing time leave it unknown.
        times = state["times"]
        final_time[key] = next(iter(times)) if len(times) == 1 else None
        status_counts[final_status[key]] += 1

    sales: list[Sale] = []
    eligible: set[int] = set()
    amount_by_order: dict[str, Decimal] = defaultdict(Decimal)
    product_join = Counter()
    unit_sources = Counter()
    base_units: dict[str, set[str]] = defaultdict(set)
    fee_rows = 0
    for line in lines:
        key = line["_order"]
        line_name = _name_key(line.get("product_name") or "")
        if (conventions.non_product_adjustments
                and (line_name == "voucher" or line_name == "phi ship"
                     or (line_name.startswith("dung ") and " diem" in line_name))):
            counts["non_product_adjustments_skipped"] += 1
            continue
        try:
            amount = parse_amount(line.get("amount"))
        except ValueError as exc:
            raise ReadmeSalesError("NUMERIC_AMBIGUOUS", "Line amount could not be parsed",
                                   field="amount") from exc
        product = None
        if not _blank(line.get("product_id")):
            product = products_by_id.get(str(line["product_id"]).strip())
            product_join["by_code" if product else "code_not_in_list"] += 1
        elif not _blank(line.get("product_name")):
            product = products_by_name.get(_name_key(line["product_name"]))
            product_join["by_name" if product else "name_not_in_list"] += 1
        product_id = (product or {}).get("id") or (
            None if _blank(line.get("product_id")) else str(line["product_id"]).strip())
        product_name = (None if _blank(line.get("product_name"))
                        else str(line["product_name"]).strip()) or (product or {}).get("name")
        product_id_is_name = product_id is None and product_name is not None and not products_by_id
        if product_id_is_name:
            product_id = product_name  # no product codes in this shop: the name identifies
        quantity_base, source = _base_quantity(line, product, conversions, conversion_complete)
        unit_sources[source] += 1
        unit_key = product_id or product_name
        if unit_key is not None:
            candidate = _base_unit(line, product, conversions, source, quantity_base)
            if candidate:
                base_units[unit_key].add(candidate)
        is_fee = product_name is not None and _name_key(product_name) == "phi phuc vu"
        fee_rows += is_fee
        index = len(sales)
        parent = orders.get(key) or {}
        context = {name: _text(line.get(name) if name in line_fields else parent.get(name))
                   for name in ("customer_id", "customer_name", "customer_group", "channel",
                                "staff_id", "payment_method")}
        listed = customer_groups.get(context["customer_id"] or "")
        if listed:
            context["customer_group"] = context["customer_group"] or listed["group"]
            context["customer_name"] = context["customer_name"] or listed["name"]
        sales.append(Sale(key, final_day[key], final_status[key], amount, product_id,
                          product_name, quantity_base, minute=final_time[key],
                          quantity_sold=line["_quantity"],
                          unit_cost_at_sale=optional_sale_cost(line.get("unit_cost_at_sale")),
                          product_id_is_name=product_id_is_name,
                          product_group=_text((product or {}).get("group")
                                              or line.get("product_group")),
                          **context))
        if not is_fee and count_keys:
            eligible.add(index)
        elif rules.service_fee is None:
            eligible.clear()
        amount_by_order[key] += amount

    # One base unit per product, or None when the files disagree or say nothing.
    resolved_units = {k: next(iter(v)) for k, v in base_units.items() if len(v) == 1}
    sales = [replace(s, base_unit=resolved_units.get(s.product_id or s.product_name or ""))
             for s in sales]
    totals = _reconcile_totals(orders, amount_by_order, final_status)
    evidence = {
        "adapter_version": ADAPTER_VERSION,
        "sources": {role: [s.label for s in entry.sections] for role, entry in roles.items()},
        "column_meanings": sorted({s.origin for e in roles.values() for s in e.sections}),
        "tables": sorted({table.key for entry in roles.values() for _, table in entry.tables}),
        "sales_rows": len(sales),
        "counts": {**counts, **order_counts, "orders_in_lines": len(order_state)},
        "status_counts": dict(sorted(status_counts.items())),
        "product_join": dict(product_join),
        "quantity_base_source": dict(unit_sources),
        "base_units": {"resolved": len(resolved_units),
                       "conflicting": sum(len(v) > 1 for v in base_units.values())},
        "service_fee_rows": fee_rows,
        "amount_reconciliation": totals,
        "order_ids": "from_file" if has_order_ids else "one_record_per_row",
        "rules": list(rules.evidence),
        "readme_conventions": list(conventions.evidence),
    }
    if fee_rows and rules.service_fee is None:
        eligible = set()
    return sales, evidence, frozenset(eligible)


def _text(value: Any) -> str | None:
    return None if _blank(value) else str(value).strip()


def _base_unit(line: dict[str, Any], product: dict[str, Any] | None,
               conversions: dict[tuple[str, str], tuple[Decimal, str]], source: str,
               quantity_base: Decimal | None) -> str | None:
    """Base unit of the line's product, from the product list, the conversion list, or a
    line whose quantity is already in base units."""
    if product and not _blank(product.get("base_unit")):
        return _name_key(product["base_unit"])
    names = {_name_key(x) for x in (line.get("product_name"), line.get("product_id"),
                                    (product or {}).get("name")) if not _blank(x)}
    for (name, _unit), (_factor, base) in conversions.items():
        if name in names and base:
            return base
    unit = _name_key(line.get("unit") or "")
    if not unit:
        return None
    if source in ("base_unit", "no_larger_unit"):
        return unit
    if source == "file" and quantity_base is not None and line.get("_quantity") == quantity_base:
        return unit
    return None


def _base_quantity(line: dict[str, Any], product: dict[str, Any] | None,
                   conversions: dict[tuple[str, str], tuple[Decimal, str]],
                   complete: bool) -> tuple[Decimal | None, str]:
    if not _blank(line.get("quantity_base")):
        try:
            return parse_quantity(line["quantity_base"]), "file"
        except ValueError as exc:
            raise ReadmeSalesError("NUMERIC_AMBIGUOUS", "Base quantity could not be parsed",
                                   field="quantity_base") from exc
    quantity = line.get("_quantity")
    if quantity is None:
        return None, "missing"
    unit = _name_key(line.get("unit") or "")
    names = {_name_key(x) for x in (line.get("product_name"), (product or {}).get("name"),
                                    line.get("product_id")) if not _blank(x)}
    base = _name_key((product or {}).get("base_unit") or "")
    if unit and base and unit == base:
        return quantity, "base_unit"
    for name in names:
        if (name, unit) in conversions:
            return quantity * conversions[(name, unit)][0], "conversion"
    known = {pair[0] for pair in conversions}
    for name in names:
        if name in known and any(conversions[(name, u)][1] == unit
                                 for (n, u) in conversions if n == name):
            return quantity, "base_unit"
    if complete and names and not names & known:
        return quantity, "no_larger_unit"
    return None, "unknown_unit"


def _reconcile_totals(orders: dict[str, dict[str, Any]], amount_by_order: dict[str, Decimal],
                      status: dict[str, str]) -> dict[str, Any]:
    compared = differs = unparsed = 0
    for key, row in orders.items():
        if key not in amount_by_order or _blank(row.get("order_goods")):
            continue
        try:
            goods = parse_amount(row["order_goods"])
        except ValueError:
            unparsed += 1
            continue
        compared += 1
        differs += goods != amount_by_order[key]
    return {"order_goods_vs_line_sum": {"compared": compared, "differs": differs,
                                        "unparsed": unparsed},
            "orders_without_lines": len(set(orders) - set(amount_by_order))}


def _from_orders(roles: dict[str, _Role], rules: _Rules
                 ) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    """Shops that only keep an invoice list: one Sale per order, no product."""
    sales: list[Sale] = []
    status_counts: Counter[str] = Counter()
    seen: dict[str, tuple[tuple[str, str], ...]] = {}
    skipped = 0
    for sheet, _ordinal, row in _role_rows(roles["orders"]):
        if row["_header_repeat"]:
            skipped += 1
            continue
        if all(_blank(row.get(k)) for k in ("order_id", "date", "status")):
            skipped += 1  # total / note rows of the invoice list
            continue
        if _blank(row.get("order_id")):
            raise ReadmeSalesError("MISSING_KEY", "Invoice without order id")
        key = str(row["order_id"]).strip()
        if (_blank(row.get("date")) and _blank(row.get("status"))
                and fold(key).startswith("tong ")):
            skipped += 1
            continue
        if key in seen:
            if rules.duplicate_rows_once and row["_cells"] == seen[key]:
                skipped += 1
                continue
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Invoice list repeats an order id")
        seen[key] = row["_cells"]
        try:
            status = rules.status.resolve(row.get("status"))
        except StatusError as exc:
            raise ReadmeSalesError("UNKNOWN_STATUS", str(exc)) from exc
        try:
            day = parse_day(row.get("date"), sheet)
            amount = parse_amount(row.get("order_goods"))
        except (TypeError, ValueError) as exc:
            raise ReadmeSalesError("VALUE_INVALID", "Invoice date or amount unreadable") from exc
        sales.append(Sale(key, day, status, amount, None, None, None,
                          minute=parse_minute(row.get("time"))))
        status_counts[status] += 1
    evidence = {
        "adapter_version": ADAPTER_VERSION,
        "sources": {"orders": [s.label for s in roles["orders"].sections]},
        "tables": sorted({t.key for _, t in roles["orders"].tables}),
        "sales_rows": len(sales),
        "status_counts": dict(sorted(status_counts.items())),
        "summary_rows_skipped": skipped,
        "order_ids": "from_file",
        "grain": "one row per order; amount is the order's goods total (README)",
        "rules": list(rules.evidence),
    }
    return sales, evidence, frozenset()
