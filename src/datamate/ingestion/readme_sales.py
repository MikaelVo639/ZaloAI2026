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
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import iter_rows
from datamate.ingestion.sales import Sale, parse_date
from datamate.ingestion.status import StatusError, status_policy
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
}
_PREFIXES = (
    ("tien hang cua don", "order_goods"),
    ("tong tien hang cua don", "order_goods"),
    ("he so", "factor"),
    ("so luong khach tra lai", "returned_quantity"),
    ("hinh thuc thanh toan", "payment_method"),
)
# Meanings that mark a list which is not about sales (imports, stock, prices, delivery...).
_OTHER_LISTS = (
    "ma phieu nhap", "ngay nhap", "ton kho", "ngay bat dau ap dung gia", "ngay giao",
    "han thanh toan", "ma phieu thu", "so luong nhap", "thoi diem thu",
)
_ORDER_FIELDS = ("order_id", "date", "status", "customer_name", "customer_id",
                 "customer_group", "channel", "payment_method", "staff_id")


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
    ({"Mã hóa đơn", "SL trả (ĐVT cơ bản)"}, {
        "Mã hóa đơn": "mã đơn", "Lô": "số lô bán",
        "SL trả (ĐVT cơ bản)": "số lượng khách trả lại (đơn vị cơ bản); lớn hơn 0: đơn đó "
                               "có trả hàng",
    }),
)
POS_PROFILES = (
    ("pos_export_kiotviet.v1", _KIOTVIET),
    ("pos_export_sqlite.v1", _SQLITE_POS),
    ("sales_export_banhang.v1", _BANHANG),
)


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
    if not ("pos" in text.split() or "phan mem ban hang" in text):
        return []
    covered = {s.file for s in documented}
    headers = [(table, _header_names(table)) for table in catalog.tables
               if table.path.name not in covered]
    for version, lists in POS_PROFILES:
        sections = []
        for table, header in headers:
            for required, meanings in lists:
                if required <= header:
                    columns = {c: m for c, m in meanings.items() if c in header}
                    sections.append(Section(table.path.name, table.name, columns,
                                            origin=version))
                    break
        fields = [set(section.fields) for section in sections]
        has_lines = any("amount" in f for f in fields)
        has_orders = any("order_goods" in f for f in fields)
        if has_lines and has_orders:
            return sections
    return []


def parse_conventions(readme: str) -> Conventions:
    conversions: dict[tuple[str, str], tuple[Decimal, str]] = {}
    evidence: list[str] = []
    returns = False
    listed = False
    in_conversion = False
    for line in readme.splitlines():
        text = fold(line)
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
            conversions[(_name_key(item.group(1)), _name_key(item.group(2)))] = (
                factor, _name_key(item.group(4)))
    return Conventions(conversions, listed, returns, tuple(evidence))


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
    if re.fullmatch(r"\d{5}", text) and 20000 < int(text) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(text))
    short = re.fullmatch(r"(\d{1,2})/(\d{1,2})", text)
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", sheet)
    if short and year:
        return date(int(year.group(1)), int(short.group(2)), int(short.group(1)))
    return parse_date(text, sheet)


# ---------------------------------------------------------------- tables

def _matching_tables(catalog: Catalog, section: Section) -> list[Table]:
    wanted = section.file.replace("\\", "/").split("/")[-1]
    return [t for t in catalog.tables
            if t.path.name == wanted and (section.sheet is None or t.name == section.sheet)]


def _records(table: Table, columns: Iterable[str]) -> Iterator[tuple[int, dict[str, Any]]]:
    """Rows of ``table`` keyed by header; the header may sit below title rows (xlsx)."""
    needed = set(columns)
    if needed <= set(table.columns):
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
        if "_other" in fields:
            continue
        if {"big_unit", "factor"} <= fields:
            role = "conversions"
        elif {"order_id", "returned_quantity"} <= fields:
            role = "lots"
        elif "amount" in fields and fields & {"product_name", "product_id"}:
            role = "lines"
        elif "order_id" in fields and fields & {"date", "status"} and "quantity" not in fields:
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
    evidence = tuple(x for x in (*status.evidence, blank, duplicates, returns, fee) if x)
    return _Rules(status, blank, duplicates, returns, fee, evidence)


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
        if _blank(row.get("order_id")):
            counts["blank_key_rows"] += 1
            continue
        key = str(row["order_id"]).strip()
        if key in orders:
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
    if not has_order_ids and orders:
        raise ReadmeSalesError("MAPPING_INCOMPLETE", "Lines cannot be joined to orders")

    counts: Counter[str] = Counter()
    seen: set[tuple] = set()
    lines: list[dict[str, Any]] = []
    group: dict[str, Any] = {}
    first_values: dict[str, dict[str, Any]] = defaultdict(dict)
    order_keys = [k for k in ("order_id", "date", "status") if k in line_fields]
    count_keys = [k for k in ("quantity", "quantity_base") if k in line_fields]
    for sheet, ordinal, row in _role_rows(roles["lines"]):
        counts["line_rows_raw"] += 1
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
            key = str(row["order_id"]).strip()
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
        state = order_state.setdefault(key, {"days": set(), "statuses": set(),
                                             "negative": False, "returned": False})
        raw_date = line.get("date") if "date" in line_fields else (parent or {}).get("date")
        raw_status = line.get("status") if "status" in line_fields else (parent or {}).get("status")
        if _blank(raw_status) or _blank(raw_date):
            raise ReadmeSalesError("MISSING_VALUE", "Order date or status is missing",
                                   order_source="lines" if parent is None else "orders")
        try:
            status = rules.status.resolve(raw_status)
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
        if product_id is None and product_name is not None and not products_by_id:
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
                   for name in ("customer_id", "customer_name", "customer_group", "channel")}
        listed = customer_groups.get(context["customer_id"] or "")
        if listed:
            context["customer_group"] = context["customer_group"] or listed["group"]
            context["customer_name"] = context["customer_name"] or listed["name"]
        sales.append(Sale(key, final_day[key], final_status[key], amount, product_id,
                          product_name, quantity_base,
                          product_group=_text((product or {}).get("group")
                                              or line.get("product_group")),
                          **context))
        if not is_fee:
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
    seen: set[str] = set()
    skipped = 0
    for sheet, _ordinal, row in _role_rows(roles["orders"]):
        if all(_blank(row.get(k)) for k in ("order_id", "date", "status")):
            skipped += 1  # total / note rows of the invoice list
            continue
        if _blank(row.get("order_id")):
            raise ReadmeSalesError("MISSING_KEY", "Invoice without order id")
        key = str(row["order_id"]).strip()
        if key in seen:
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Invoice list repeats an order id")
        seen.add(key)
        try:
            status = rules.status.resolve(row.get("status"))
        except StatusError as exc:
            raise ReadmeSalesError("UNKNOWN_STATUS", str(exc)) from exc
        try:
            day = parse_day(row.get("date"), sheet)
            amount = parse_amount(row.get("order_goods"))
        except (TypeError, ValueError) as exc:
            raise ReadmeSalesError("VALUE_INVALID", "Invoice date or amount unreadable") from exc
        sales.append(Sale(key, day, status, amount, None, None, None))
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
