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
from contextlib import ExitStack, closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable, Iterator

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import is_repeated_header, iter_data_rows
from datamate.ingestion.sales import (
    AMBIGUOUS_STATE,
    Sale,
    is_total_label,
    parse_date,
    parse_minute,
    price_per_base_unit,
)
from datamate.ingestion.header_schema import (
    header_columns,
    header_field,
    looks_like_sales_lines,
    other_list_name,
    sales_list_name,
    table_role,
)
from datamate.ingestion.source_names import filename_key, is_family, matches_source
from datamate.ingestion.status import StatusError, by_elimination, order_state, status_policy
from datamate.semantics.rules import rule_line
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
    "ma nhan vien": "employee_code",
    "ten nhan vien": "employee_name",
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
    # Train READMEs describe line price columns as "đơn giá" (e.g. "Giá bán: đơn giá").
    ("don gia", "unit_price"),
)
# Meanings that mark a list which is not about sales (imports, stock, prices, delivery...).
_OTHER_LISTS = (
    "ma phieu nhap", "ngay nhap", "ton kho", "ngay bat dau ap dung gia", "ngay giao",
    "han thanh toan", "ma phieu thu", "so luong nhap", "thoi diem thu",
)
_ORDER_FIELDS = ("order_id", "date", "status", "customer_name", "customer_id",
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
    return _meaning_head_field(meaning)


# A meaning followed by its explanation ("ngày bán (dòng phí để trống)", "kênh bán: Tại tiệm,
# …", "thành tiền của dòng; …"): its head read as a ledger header would be. Order sequence
# numbers ("số thứ tự đơn trong sổ") are order ids; "số thứ tự dòng" stays a line number.
_ORDER_SEQUENCE = re.compile(
    r"^(?:so thu tu|stt) (?:don|hoa don|phieu ban)(?: hang)?(?: trong (?:so|ngay|thang))?$")
_HEAD_AMOUNT = re.compile(r"^thanh tien(?: cua)? (?:dong|moi dong|ca dong)$")
# "Ghi chú của đơn; đơn huỷ hoặc đang giao được ghi ở đây": no status column, the order's
# state is written in its notes (status_note). A blank note is a completed order.
_NOTE_HEADS = frozenset({"ghi chu", "ghi chu cua don", "ghi chu don", "ghi chu cua hoa don",
                         "note", "notes"})
_NOTE_LABELS = (
    ("huy", re.compile(r"\bhuy\b")),
    ("tra hang", re.compile(r"\b(?:tra lai|tra hang|doi tra|hoan tra|hoan hang)\b")),
    ("dang giao", re.compile(r"\bdang (?:giao|ship|van chuyen)\b")),
)
_NOTE_NEGATIONS = {
    state: re.compile(rf"\b(?:khong|chua|ko) {verb}\b")
    for state, verb in (("huy", "huy"), ("tra hang", "tra"), ("dang giao", "giao"))
}


def _note_labels(text: str) -> frozenset[str]:
    return frozenset(state for state, pattern in _NOTE_LABELS if pattern.search(text))


def note_state(note: Any, allowed: frozenset[str] | None = None) -> str | None:
    """The order state a note names ("Khách trả lại hàng" -> "tra hang"), None for a note
    that names none; a note naming several states, or a negated one, raises."""
    whole = order_state(note)  # the note is a status label itself ("Khách huỷ")
    if whole and whole != "hoan thanh" and (allowed is None or whole in allowed):
        return whole
    text = fold(str(note))
    if any(pattern.search(text) for state, pattern in _NOTE_NEGATIONS.items()
           if allowed is None or state in allowed):
        raise ReadmeSalesError("STATUS_AMBIGUOUS", "An order note negates a state")
    found = _note_labels(text)
    if allowed is not None:
        found &= allowed
    if len(found) > 1:
        raise ReadmeSalesError("STATUS_AMBIGUOUS", "An order note names several states")
    return next(iter(found), None)


def _states_from_notes(lines: list[dict[str, Any]]) -> None:
    """Give every line the state its order's notes name; "hoan thanh" when none does."""
    by_order: dict[str, set[str]] = defaultdict(set)
    ambiguous: set[str] = set()
    for line in lines:
        note, key = line.get("status_note"), line["_order"]
        if not _blank(note):
            try:
                state = note_state(note, line.get("_note_labels"))
            except ReadmeSalesError as exc:
                if exc.code != "STATUS_AMBIGUOUS":
                    raise
                ambiguous.add(key)
            else:
                if state:
                    by_order[key].add(state)
    for line in lines:
        key = line["_order"]
        states = by_order.get(key, set())
        line["_state_ambiguous"] = key in ambiguous or len(states) > 1
        line["status"] = None if line["_state_ambiguous"] else next(iter(states), "hoan thanh")


# "hàng bán, có thể ghi tắt (tra ở sheet Ghi tắt)": names in the sales list may be short
# forms, resolved through one list of short form -> full name (and its kind of goods).
_SHORT_MEANING = re.compile(r"\b(?:ghi tat|viet tat)\b")
_SHORT_HEADER = re.compile(r"^(?:ghi tat|viet tat|ten tat|ten viet tat|ten ghi tat|ma tat)$")
_KIND_HEADER = re.compile(r"^(?:loai|loai hang|nhom|nhom hang)$")


def _expand_abbreviations(catalog: Catalog, lines_role: "_Role",
                          lines: list[dict[str, Any]]) -> bool | None:
    """Replace short product names by their full names when the README says the sales list
    may write them short. None: the README says nothing of short names; True: every name
    is a known short form or full name; False: some name stays unresolved, or no unique
    list of short forms exists (product identity is then unverified)."""
    section = lines_role.sections[0]
    column = section.fields.get("product_name")
    if not column or not _SHORT_MEANING.search(fold(section.columns.get(column, ""))):
        return None
    used = {table.key for _, table in lines_role.tables}
    lists = []
    for table, header in _table_headers(t for t in catalog.tables if t.key not in used):
        short = [h for h in header if _SHORT_HEADER.match(fold(h))]
        full = [h for h in header if h not in short and header_field(h) == "product_name"]
        kind = [h for h in header if _KIND_HEADER.match(fold(h))]
        if len(short) == 1 and len(full) == 1 and len(kind) <= 1:
            lists.append((table, short[0], full[0], kind[0] if kind else None))
    if len(lists) != 1:
        return False
    table, short, full, kind = lists[0]
    shorts: dict[str, tuple[str, str | None]] = {}
    fulls: dict[str, str | None] = {}
    for _, row in _records(table, [c for c in (short, full, kind) if c]):
        if _blank(row.get(short)) or _blank(row.get(full)):
            continue
        entry = (str(row[full]).strip(), _text(row.get(kind)) if kind else None)
        full_key = _name_key(entry[0])
        fulls.setdefault(full_key, entry[1])
        short_key = _name_key(row[short])
        if short_key != full_key and _name_key(
                shorts.setdefault(short_key, entry)[0]) != full_key:
            raise ReadmeSalesError("AMBIGUOUS_SHORT_NAME", "A short name has several items")
    for key, entry in shorts.items():
        if key in fulls and _name_key(entry[0]) != key:
            raise ReadmeSalesError("AMBIGUOUS_SHORT_NAME", "A short name is another item's name")
    resolved = True
    for line in lines:
        if _blank(line.get("product_name")):
            continue
        key = _name_key(line["product_name"])
        if key in fulls:  # written in full: the line keeps its own spelling
            group = fulls[key]
        elif key in shorts:
            line["product_name"], group = shorts[key]
        else:
            resolved = False
            continue
        if group and _blank(line.get("product_group")):
            line["product_group"] = group
    return resolved


_HEAD_FIELDS = frozenset({"order_id", "date", "product_id", "product_name", "quantity",
                          "unit", "unit_price", "discount", "customer_id", "customer_name",
                          "channel", "payment_method", "staff_id"})


_SERVICE_AMOUNT = re.compile(
    r"^(?:tong tien|thanh tien)(?: cua)? (?:ca )?(?:phieu|phieu dich vu)$")
_SERVICE_DATE = re.compile(
    r"^ngay(?: (?:phieu|lap|ghi|ban|dat|lam|tiep nhan|dich vu|xu ly)(?: phieu)?)?$")
_SERVICE_GRAIN = re.compile(
    r"\bmoi dong (?:la |ghi |ghi lai |luu |luu lai |tuong ung (?:voi )?)?"
    r"(?:mot |1 )(?:phieu|hoa don)\b"
    r"|\bmoi dong (?:mot|1) (?:[^ ;.]+ ){1,5}(?:mot|1) phieu\b")
# A ticket register whose README names no price adjustment and no ticket state at all: the
# ticket total is what the shop takes and every written ticket is a done job.
_TICKET_TOTAL = re.compile(r"^tong tien (?:ca |cua )?phieu$")
_PRICE_ADJUSTMENT = re.compile(
    r"\b(?:giam gia|giam tru|chiet khau|khuyen mai|uu dai|bot|phi (?:giao|ship|van chuyen|dich vu)"
    r"|thue|vat|phu thu|tien coc|dat coc)\b")
_TICKET_STATE = re.compile(
    r"\b(?:huy|tra lai|tra hang|trang thai|hoan tien|hoan tra|tam tinh|bao gia|cho (?:sua|lay|xu ly)"
    r"|dang (?:sua|lam)|chua (?:xong|lam|lay|hoan thanh|thanh toan))\b")
_SERVICE_STAFF = re.compile(r"^(?:tho|ky thuat vien|nguoi (?:lam|sua))(?: (?:lam|sua))?(?: phieu)?$")
_SERVICE_NOTE = re.compile(r"^ghi chu(?: (?:cua )?phieu)?$")
_ONLY_COMPLETE = re.compile(
    r"\b(?:chi (?:ghi|luu) (?:lai )?(?:cac )?phieu|(?:tat ca|moi) (?:cac )?phieu) "
    r"(?:deu )?(?:da )?(?:hoan thanh|thanh toan)\b")


def _service_sheet(section: Section, readme: str) -> bool:
    # A grain statement from the README's general conventions is safe only when the
    # same README documents a single table of this shape (checked by the loader).
    if section.origin != "readme":
        return False
    fields = _service_fields(section)
    if ("date" not in fields or not fields.keys() & {"order_goods", "service_goods"}
            or fields.keys() & {"product_id", "product_name", "quantity", "order_id", "amount"}):
        return False
    if _SERVICE_GRAIN.search(fold(" ".join(section.columns.values()))):
        return True
    if not _SERVICE_GRAIN.search(fold(readme)):
        return False
    # A general convention alone must identify a single detail register. A summary
    # alongside it is described as one row per date/employee, not one row per ticket.
    candidates = [s for s in parse_sections(readme)
                  if {"date", "service_goods"} <= _service_fields(s).keys()]
    return len(candidates) == 1 and candidates[0].label == section.label


def _service_fields(section: Section) -> dict[str, str]:
    fields = section.fields
    if "date" not in fields:
        days = [column for column, meaning in section.columns.items()
                if _SERVICE_DATE.fullmatch(fold(re.split(r"[:;,]", meaning, maxsplit=1)[0]))
                or (re.match(r"^ngay (?:[a-z]+ )?[a-z]+$", fold(meaning))
                    and fold(column).startswith("ngay")
                    and not re.search(r"\b(?:giao|du kien|hen|han|nhap|tra|sinh)\b", fold(meaning)))]
        if len(days) == 1:
            fields["date"] = days[0]
    for column, meaning in section.columns.items():
        head = fold(re.split(r"[:;,]", meaning, maxsplit=1)[0]).strip()
        if "staff_id" not in fields and _SERVICE_STAFF.fullmatch(head):
            fields["staff_id"] = column
        elif "ticket_note" not in fields and _SERVICE_NOTE.fullmatch(head):
            fields["ticket_note"] = column
    return fields


def _meaning_head_field(meaning: str) -> str | None:
    raw = re.sub(r"\([^)]*\)", " ", re.split(r"[:;,]", meaning, maxsplit=1)[0])
    if re.search(r"\bhãng\b", unicodedata.normalize("NFC", raw.lower())):
        return None  # "hãng" (brand) folds to the same letters as "hàng" (goods)
    head = fold(raw).strip()
    if head in _NOTE_HEADS and _note_labels(fold(meaning)):
        return "status_note"
    if not head or head in _MEANINGS:
        return _MEANINGS.get(head)
    if _ORDER_SEQUENCE.match(head):
        return "order_id"
    if _HEAD_AMOUNT.match(head):
        return "amount"
    if _SERVICE_AMOUNT.fullmatch(head):
        return "service_goods"
    field = header_field(head)
    return field if field in _HEAD_FIELDS else None


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
POS_PROFILES = (
    ("pos_export_kiotviet.v1", _KIOTVIET),
    ("pos_export_sqlite.v1", _SQLITE_POS),
    ("sales_export_banhang.v1", _BANHANG),
)


def _table_headers(tables: Iterable[Table]) -> list[tuple[Table, set[str]]]:
    """Look below title rows while opening at most one workbook at a time."""
    headers = []
    for path, group in groupby(tables, key=lambda table: table.path):
        with ExitStack() as resources:
            book = None
            for table in group:
                names = set(table.columns)
                if (path.suffix.lower() == ".xlsx"
                        and sum(not name.startswith("_empty_") for name in names) <= 2):
                    if book is None:
                        from openpyxl import load_workbook

                        book = resources.enter_context(closing(load_workbook(
                            path, read_only=True, data_only=True, keep_links=False)))
                    rows = book[table.name].iter_rows(max_row=_HEADER_SCAN_ROWS, values_only=True)
                    with closing(rows):
                        for values in rows:
                            cells = {str(v).strip() for v in values if v is not None and str(v).strip()}
                            if len(cells) > 2:
                                names = cells
                                break
                headers.append((table, names))
    return headers


def _header_names(table: Table) -> set[str]:
    return _table_headers((table,))[0][1]


def profile_sections(catalog: Catalog, documented: list[Section]) -> list[Section]:
    """Sections for undocumented tables of a POS / sales-software export.

    One export profile per business: the profile must recognise both a sales line list
    and an order list among the undocumented tables, otherwise nothing is added.
    """
    text = fold(catalog.readme)
    if not ("pos" in text.split() or "phan mem ban hang" in text):
        return []
    headers = _table_headers(table for table in catalog.tables
                             if not any(matches_source(s.file, table.path.name)
                                        and s.sheet in (None, table.name) for s in documented))
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
        has_orders = any("order_goods" in f for f in fields)
        if has_lines and has_orders:
            return sections
    return []


def staff_codes(catalog: Catalog, *, reserved_names: Iterable[str] = ()) -> dict[str, str]:
    """Read a documented staff catalogue, or the verified legacy SQLite POS list."""
    blocked = {fold(name) for name in reserved_names}
    documented = []
    try:
        for section in parse_sections(catalog.readme):
            fields = section.fields
            if {"employee_code", "employee_name"} <= fields.keys() and not (
                    fields.keys() & {"order_id", "date", "quantity", "status", "amount"}):
                counts = Counter(meaning_field(value) for value in section.columns.values())
                if counts["employee_code"] != 1 or counts["employee_name"] != 1:
                    return {}
                tables = _matching_tables(catalog, section)
                if not tables:
                    return {}
                documented.extend((table, fields["employee_code"], fields["employee_name"])
                                  for table in tables)
        if documented:
            if len({(t.path, t.name) for t, _, _ in documented}) != len(documented):
                return {}
            return _staff_code_map(documented, blocked)
        return sqlite_staff_codes(catalog, reserved_names=blocked)
    except (ReadmeSalesError, OSError):
        return {}


def _staff_code_map(sources: list[tuple[Table, str, str]],
                    blocked: set[str]) -> dict[str, str]:
    by_name: dict[str, set[str]] = defaultdict(set)
    by_code: dict[str, set[str]] = defaultdict(set)
    for table, code_col, name_col in sources:
        for _, row in _records(table, (code_col, name_col)):
            code, name = _text(row[code_col]), _text(row[name_col])
            if code and name:
                name = _name_key(name)
                by_name[name].add(code)
                by_code[code.upper()].add(name)
    return {name: code for name, codes in by_name.items()
            if len(codes) == 1 and name not in blocked
            for code in codes if len(by_code[code.upper()]) == 1}


def sqlite_staff_codes(catalog: Catalog, *, reserved_names: Iterable[str] = ()) -> dict[str, str]:
    """Verified legacy staff table paired with SQLite POS orders."""
    if "`nhan_vien`" not in catalog.readme.lower() or "pos" not in fold(catalog.readme).split():
        return {}
    order_keys = _SQLITE_POS[0][0] | {"MaNhanVien"}
    databases = {table.path for table in catalog.tables if table.path.suffix == ".sqlite"
                 and order_keys <= set(table.columns)}
    if len(databases) != 1:
        return {}
    tables = [table for table in catalog.tables if table.path in databases
              and table.name.lower() == "nhan_vien"
              and {"MaNhanVien", "TenGoi"} <= set(table.columns)]
    if len(tables) != 1:
        return {}
    return _staff_code_map([(tables[0], "MaNhanVien", "TenGoi")], set(reserved_names))


# amount = unit price x quantity - line discount on most rows, and never more than price x
# quantity on (almost) all: an unlisted discount may lower a line, nothing raises it.
_EXACT_SHARE, _AT_MOST_SHARE = Decimal("0.90"), Decimal("0.99")
_ARITHMETIC_ROWS = 2000


def header_sections(catalog: Catalog, documented: list[Section]) -> list[Section]:
    """Sales lists the README leaves undescribed, described from their headers.

    First a single line list: only a list whose file or sheet name says "sales"
    (header_schema.sales_list_name), whose headers name every needed field exactly once
    (header_columns), and whose rows check out (_amounts_check_out) is described; the
    guessed "amount" is then the line amount after discount, as the loader reads it. All or
    nothing: when one sales-named list cannot be described, none is, since a missing month
    would silently shrink every total. Failing that, an order list joined to a line list
    (_joined_header_sections).
    """
    undocumented = [t for t in catalog.tables
                    if not any(matches_source(s.file, t.path.name) and s.sheet in (None, t.name)
                               for s in documented)]
    headers = _table_headers(undocumented)
    named = convention_columns(catalog.readme)
    sections = []
    for table, header in headers:
        if not (sales_list_name(table.path.name, table.name)
                and looks_like_sales_lines(sorted(header))):
            continue
        given = {column: meaning for column, meaning in named.items() if column in header}
        provided = frozenset(filter(None, map(meaning_field, given.values())))
        provided |= {"status"} if "status_note" in provided else set()
        columns = header_columns(sorted(set(header) - set(given)), provided)
        if columns is not None:
            columns.update(given)
            if _SHORT_MEANING.search(fold(catalog.readme)):
                for column, meaning in columns.items():
                    if meaning_field(meaning) == "product_name":
                        columns[column] = f"{meaning}, có thể ghi tắt"
        section = None if columns is None else Section(table.path.name, table.name, columns,
                                                       origin="header")
        if section is None or not _amounts_check_out(table, section):
            sections = []
            break
        sections.append(section)
    documented_orders = any("order_id" in s.fields and s.fields.keys() & {"date", "status"}
                            and "quantity" not in s.fields for s in documented)
    if sections and not any("product_group" in s.fields for s in documented):
        sections += _header_catalogue(headers)
    return sections or _joined_header_sections(headers, documented_orders)


# A product list the README does not describe: a table named as goods whose headers name
# the product code, the product name and the product group exactly once each.
_CATALOGUE_NAME = re.compile(r"\b(?:hang hoa|hanghoa|danh muc|danhmuc|san pham|sanpham)\b")
_CATALOGUE_HEADS = {
    "mã hàng": re.compile(r"ma (?:h ?hoa|hang(?: hoa)?|san pham|sp)"),
    "tên hàng": re.compile(r"ten (?:hang(?: hoa)?|san pham|sp)"),
    "nhóm hàng": re.compile(r"nhom (?:hang(?: hoa)?|san pham|sp)"),
}
_HEADER_GROUPS = "header_product_groups"


def _header_catalogue(headers: list[tuple[Table, set[str]]]) -> list[Section]:
    found = []
    for table, header in headers:
        name = fold(re.sub(r"[_\-.]", " ", f"{table.path.stem} {table.name}"))
        name = re.sub(r"(?<=[a-z])(?=hang|hoa)", " ", name)
        if not _CATALOGUE_NAME.search(name) or looks_like_sales_lines(sorted(header)):
            continue
        columns = {}
        for meaning, pattern in _CATALOGUE_HEADS.items():
            hits = [c for c in header if pattern.fullmatch(fold(re.sub(r"[.]", " ", c)).strip())]
            if len(hits) != 1:
                break
            columns[hits[0]] = meaning
        else:
            found.append(Section(table.path.name, table.name, columns, origin=_HEADER_GROUPS))
    return found if len(found) == 1 else []


def convention_columns(readme: str) -> dict[str, str]:
    """README meanings of columns the conventions name in bold: "các dòng cùng **STT** là
    một đơn" (the order id) and a notes column the conventions say records an order state
    ("**Đơn khách trả lại**: **Ghi chú** của đơn ghi khách trả lại")."""
    found: dict[str, str] = {}
    note_states: dict[str, set[str]] = defaultdict(set)
    in_columns = False
    for line in readme.splitlines():
        if line.startswith("## "):
            in_columns = line.startswith("## Cột trong ")
        if in_columns:
            continue
        text = fold(line)
        for match in re.finditer(r"\*\*([^*]+)\*\*", line):
            column = match.group(1).strip()
            before = fold(line[:match.start()])
            after = fold(line[match.end():])
            if before.endswith("cac dong cung") and after.startswith("la mot don"):
                found[column] = "mã đơn"
            elif (fold(column) in _NOTE_HEADS and _note_labels(text)
                  and fold(column) not in fold(line.split(":", 1)[0])):
                note_states[column].update(_note_labels(text))
    found.update({column: "ghi chú của đơn; ghi đơn " + ", ".join(sorted(states))
                  for column, states in note_states.items()})
    return found


def _joined_header_sections(headers: list[tuple[Table, set[str]]],
                            documented_orders: bool = False) -> list[Section]:
    """An order list and a line list joined by the order id, as POS exports keep them,
    described from their headers (header_schema.table_role), with an optional product and
    customer list.

    Every table of one role must read the same columns (a list split over several files);
    exactly one shape per role, every line list must pass _amounts_check_out, and lists
    named as imports, stock, prices... are never used. The order list may instead be one the
    README documents (``documented_orders``). The loader then refuses lines whose order is
    missing from the order list, so a wrong pairing abstains.
    """
    by_role: dict[str, list[tuple[Table, Section]]] = defaultdict(list)
    for table, header in headers:
        if other_list_name(table.path.name, table.name):
            continue
        guess = table_role(sorted(header))
        if guess is not None:
            role, columns = guess
            by_role[role].append((table, Section(table.path.name, table.name, columns,
                                                 origin="header")))
    sections: list[Section] = []
    for role in ("lines", "orders", "products", "customers"):
        found = by_role.get(role, [])
        if role == "orders" and documented_orders:
            continue
        if len({tuple(sorted(section.columns.items())) for _, section in found}) != 1:
            if role in ("lines", "orders"):
                return []
            continue
        if role == "lines" and not all(_amounts_check_out(t, s) for t, s in found):
            return []
        sections.extend(section for _, section in found)
    return sections


# Read literal labels from the product column only after the README says they are not
# products. Require a delivery fee or an explicitly whole-order discount, not a line
# discount or a fee whose treatment the rulebook includes in revenue.
_NOT_PRODUCTS = re.compile(r"\bkhong phai (?:dong hang|mat hang|hang|san pham)\b")
_ORDER_ADJUSTMENT = re.compile(
    r"\b(?:phi giao(?: hang)?|(?:giam gia|chiet khau) "
    r"(?:(?:tren |cua |cho )?(?:ca |toan bo )|ca )?(?:don(?: hang)?|hoa don))\b")
_LITERAL_LABEL = re.compile(r'`([^`]+)`|"([^"]+)"|“([^”]+)”')
_LISTED_ADJUSTMENT = re.compile(
    rf"(?:{_ORDER_ADJUSTMENT.pattern}|(?:su dung|dung|doi|tru) diem(?: tich luy)?)")


def _adjustment_labels(section: Section, fields: dict[str, str]) -> frozenset[str]:
    meaning = section.columns.get(fields.get("product_name", ""), "")
    if section.origin != "readme" or not _NOT_PRODUCTS.search(fold(meaning)):
        return frozenset()
    labels = set()
    # A list introduced by "not products" can use a colon or parentheses, without
    # quoting each label. Keep only whole entries, never substrings of item names.
    for separator in re.finditer(r"[:(]", meaning):
        head = re.split(r"[;.)]", meaning[:separator.start()])[-1]
        if not _NOT_PRODUCTS.search(fold(head)):
            continue
        end = re.split(r"[.;)]", meaning[separator.end():], maxsplit=1)[0]
        for name in re.split(r"[,/]", end):
            key = _name_key(name)
            if _LISTED_ADJUSTMENT.fullmatch(key):
                labels.add(key)
    for clause in meaning.split(";"):
        matches = list(_LITERAL_LABEL.finditer(clause))
        for match in matches:
            label = next(part for part in match.groups() if part is not None)
            # Evidence is local to the label so a documented shipping fee does not classify
            # any other adjustment in the same column description.
            direct = _LISTED_ADJUSTMENT.fullmatch(fold(label))
            explanation = (len(matches) == 1 and _ORDER_ADJUSTMENT.search(
                fold(clause[:match.start()] + " " + clause[match.end():])))
            if direct or explanation:
                labels.add(_name_key(label))
    return frozenset(labels)


def has_documented_order_adjustments(readme: str) -> bool:
    return any(_adjustment_labels(section, section.fields) for section in parse_sections(readme))


def _amounts_check_out(table: Table, section: Section) -> bool:
    fields = section.fields
    if not {"unit_price", "quantity", "amount"} <= set(fields):
        return False  # nothing to check the guessed amount against
    checked = exact = at_most = 0
    adjustments = _adjustment_labels(section, fields)
    for _, raw in _records(table, section.columns):
        if adjustments and _name_key(raw.get(fields["product_name"])) in adjustments:
            continue
        try:
            price = parse_amount(raw.get(fields["unit_price"]))
            quantity = parse_quantity(raw.get(fields["quantity"]))
            amount = parse_amount(raw.get(fields["amount"]))
            discount = (Decimal(0) if "discount" not in fields
                        or _blank(raw.get(fields["discount"]))
                        else parse_amount(raw.get(fields["discount"])))
        except (TypeError, ValueError, ArithmeticError):
            continue
        checked += 1
        exact += abs(price * quantity - discount - amount) <= 1
        at_most += amount <= price * quantity - discount + 1
        if checked >= _ARITHMETIC_ROWS:
            break
    return (checked >= 20 and exact >= _EXACT_SHARE * checked
            and at_most >= _AT_MOST_SHARE * checked)


def sale_cost_column(readme: str, table: Table, header: set[str] | None = None) -> str | None:
    """Find a unique column explicitly documented as the cost of one selling unit."""
    candidates = set()
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
            candidates.add(column or name)
    if candidates:
        candidates.intersection_update(header if header is not None else _header_names(table))
    return next(iter(candidates)) if len(candidates) == 1 else None


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
            key = (_name_key(item.group(1)), _name_key(item.group(2)))
            value = (factor, _name_key(item.group(4)))
            if strict and key in conversions and conversions[key] != value:
                raise ValueError("Conflicting README conversion factors")
            conversions[key] = value
    return Conventions(conversions, listed, returns, tuple(evidence))


def _name_key(value: Any) -> str:
    return re.sub(r"\s+", " ", fold(str(value))).strip()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _summary_row(row: dict[str, Any], keys: Iterable[str]) -> bool:
    """A total or note row of a list: nothing in ``keys`` besides the order id and date, an
    order id that is blank or a total label ("Tổng 01/05", "Cộng"), and a date that is blank
    or a total label too ("Cộng tháng"); or, with no order id, a row with no product and no
    money at all (a note written in the date column)."""
    keys = list(keys)
    if any(not _blank(row.get(key)) for key in keys if key not in ("order_id", "date")):
        return False
    order = row.get("order_id")
    if not (_blank(order) or is_total_label(order)):
        return False
    day = row.get("date")
    if "date" not in keys or _blank(day) or is_total_label(day):
        return True
    return _blank(order) and all(_blank(row.get(name))
                                 for name in ("product_id", "product_name", "amount"))


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


# Hand-written day cells: "Thứ 2, 03/08", "T2 3/8", "CN - 3/8", "3/8 (T2)", "ngày 3".
_WEEKDAY = r"(?:thu (?:hai|ba|tu|nam|sau|bay|[2-7])|t[2-7]|cn|chu nhat)"
_DAY_PREFIX = re.compile(rf"^(?:{_WEEKDAY}\s*[,:-]?\s*(?:ngay\s+)?|ngay\s+)(?=\d)")
_DAY_SUFFIX = re.compile(rf"\s*(?:\(\s*{_WEEKDAY}\s*\)|[,-]\s*{_WEEKDAY})$")
_TWO_DIGIT_YEAR = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2})")


def parse_day(value: Any, sheet: str = "") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 20000 < value < 80000:
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = str(value).strip()
    # Lower case without accents, punctuation kept (fold drops "-" and brackets).
    plain = "".join(c for c in unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
                    if not unicodedata.combining(c))
    if _DAY_PREFIX.search(plain) or _DAY_SUFFIX.search(plain):
        text = _DAY_SUFFIX.sub("", _DAY_PREFIX.sub("", plain)).strip()
    two = _TWO_DIGIT_YEAR.fullmatch(text)
    if two:
        year = 2000 + int(two.group(3))
        named = re.search(r"(?<!\d)(20\d{2})(?!\d)", sheet)
        if named and int(named.group(1)) != year:
            raise ValueError("Two-digit year disagrees with the sheet")
        return date(year, int(two.group(2)), int(two.group(1)))
    if re.fullmatch(r"\d{5}", text) and 20000 < int(text) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(text))
    short = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})", text)
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", sheet)
    if short and year:
        return date(int(year.group(1)), int(short.group(2)), int(short.group(1)))
    # A day number alone on a monthly sheet ("T7-2025", "Tháng 07/2025").
    month = re.search(r"(?<!\d)(\d{1,2})\s*[-/._ ]\s*(20\d{2})(?!\d)", sheet)
    if re.fullmatch(r"\d{1,2}(?:\.0)?", text) and month:
        return date(int(month.group(2)), int(month.group(1)), int(float(text)))
    dotted = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", text)
    if dotted:
        return date(int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1)))
    return parse_date(text, sheet)


# ---------------------------------------------------------------- tables

def _customer_identity_absence(
    catalog: Catalog, roles: dict[str, _Role], headers: dict[tuple[Path, str], set[str]],
) -> list[str]:
    """Prove absence only for a catalog fully described by known non-customer columns."""
    non_customer = {
        "order_id", "date", "status", "product_id", "product_name", "name",
        "quantity", "quantity_base", "unit", "base_unit", "amount", "unit_price",
        "product_group", "unit_cost_at_sale",
    }
    product_metadata = {
        "bảo hành", "ghi chú", "loại hàng hóa", "mã hh liên quan", "mã vạch ean",
        "mô tả sản phẩm", "trọng lượng", "tồn lớn nhất", "tồn nhỏ nhất", "vị trí",
        "mẫu ghi chú", "thương hiệu", "vị trí kệ", "được bán trực tiếp",
        "định mức tồn tối thiểu", "định mức tồn tối đa",
    }
    covered = set()
    selected = [pair for entry in roles.values() for pair in entry.tables]
    for section in parse_sections(catalog.readme):
        for meaning in section.columns.values():
            if (meaning_field(meaning) not in non_customer
                    and fold(meaning) not in {"gia ban hien tai", "gia von"}):
                return []
        tables = [table for documented, table in selected if documented == section]
        if not tables:
            return []
        fields = section.fields.keys()
        product_list = (
            "product_id" in fields and bool(fields & {"name", "product_name"})
            and not fields & {"order_id", "amount", "date", "quantity"}
        )
        for table in tables:
            key = (table.path, table.name)
            if key not in headers:
                return []
            header = headers[key]
            extras = header - section.columns.keys()
            if product_list:
                extras = {name for name in extras if name.casefold() not in product_metadata}
            if extras or section.columns.keys() - header:
                return []
            covered.add(key)
    if covered != {(table.path, table.name) for table in catalog.tables}:
        return []
    return sorted(table.key for table in catalog.tables)


def _matching_tables(catalog: Catalog, section: Section) -> list[Table]:
    files = {t.path for t in catalog.tables if matches_source(section.file, t.path.name)}
    if len(files) > 1 and not is_family(section.file):
        raise ReadmeSalesError("AMBIGUOUS_SOURCE", "Several files match one README source",
                               source=section.label)
    matches = [t for t in catalog.tables
               if t.path in files and (section.sheet is None or t.name == section.sheet)]
    if section.sheet is None and len(matches) > 1:
        documented_columns = set(section.columns)
        compatible = [table for table, header in _table_headers(matches)
                      if documented_columns <= header]
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
        yield from iter_data_rows(table)
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
            if is_repeated_header(header, values):
                continue
            if any(v is not None and (not isinstance(v, str) or v.strip()) for v in values):
                yield ordinal, {name: value for name, value in zip(header, values) if name}
    finally:
        book.close()


@dataclass
class _Role:
    sections: list[Section]
    tables: list[tuple[Section, Table]]


_OPTIONAL_ROLES = frozenset({"customers"})


def _catalogue_with_stock(section: Section, fields: set[str]) -> bool:
    """A product catalogue can also carry a stock balance column."""
    return (
        {"product_id", "base_unit", "product_group"} <= fields
        and bool(fields & {"name", "product_name"})
        and not fields & {"order_id", "quantity", "amount", "date", "status", "big_unit", "factor"}
        and all(fold(meaning).startswith("ton kho") for meaning in section.columns.values()
                if meaning_field(meaning) == "_other")
    )


def _classify(sections: list[Section], catalog: Catalog) -> dict[str, _Role]:
    roles: dict[str, _Role] = {}
    for section in sections:
        fields = set(section.fields)
        if _service_sheet(section, catalog.readme):
            role = "service_orders"
        elif "_other" in fields:
            if not _catalogue_with_stock(section, fields):
                continue
            role = "products"
        elif {"big_unit", "factor"} <= fields:
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
        elif ({"product_name", "product_group"} <= fields
              and not fields & {"product_id", "order_id", "quantity", "amount", "date"}):
            role = "products"  # a catalogue without codes: joined by unambiguous name
        else:
            continue
        if role == "service_orders":
            # Keep all declared sheets so a missing header on one month cannot silently
            # shorten the data. Non-financial columns do not establish the grain.
            tables = [table for table in catalog.tables
                      if matches_source(section.file, table.path.name)
                      and section.sheet in (None, table.name)]
            if not is_family(section.file) and len({table.path for table in tables}) > 1:
                raise ReadmeSalesError("AMBIGUOUS_SOURCE", "Several files match one ticket ledger")
        else:
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
        signatures = {frozenset(_service_fields(s) if role == "service_orders" else s.fields)
                      for s in entry.sections}
        if len(signatures) != 1:
            raise ReadmeSalesError("AMBIGUOUS_ROLE", "Files of one role disagree on fields",
                                   role=role, sources=[s.label for s in entry.sections])
    return roles


_BASE_PRICE_MEANINGS = {
    "don gia theo don vi co ban", "don gia don vi co ban",
    "don gia tinh theo don vi co ban", "don gia cho mot don vi co ban",
    "don gia mot don vi co ban", "don gia da quy doi ve don vi co ban",
}


def _role_rows(
    entry: _Role, headers: dict[tuple[Path, str], set[str]] | None = None,
    with_file: bool = False, *, sale_cost_readme: str = "", service: bool = False,
) -> Iterator[tuple[str, int, dict[str, Any]]]:
    """``(sheet, ordinal, row)``; ``with_file`` adds the source file name as ``_file``."""
    for section, table in entry.tables:
        fields = _service_fields(section) if service else section.fields
        if sale_cost_readme and "unit_cost_at_sale" not in fields:
            cost = sale_cost_column(sale_cost_readme, table)
            if cost and cost not in section.columns:
                fields["unit_cost_at_sale"] = cost
        key = (table.path, table.name)
        price_column = fields.get("unit_price", "")
        price_is_base = fold(section.columns.get(price_column, "")) in _BASE_PRICE_MEANINGS
        notes = _note_labels(fold(section.columns.get(fields.get("status_note", ""), "")))
        adjustments = _adjustment_labels(section, fields)
        needed = ([column for field, column in fields.items() if field in
                   {"date", "service_goods", "order_goods", "status", "status_note", "time", "staff_id"}]
                  if service else section.columns)
        for ordinal, raw in _records(table, needed):
            if headers is not None and key not in headers:
                headers[key] = {name for name in raw if not re.fullmatch(r"_empty_\d+", name)}
            row = {key: raw.get(column) for key, column in fields.items()}
            if price_is_base:
                row["_unit_price_is_base"] = True
            if notes:
                row["_note_labels"] = notes
            if adjustments and _name_key(row.get("product_name")) in adjustments:
                row["_order_adjustment"] = True
            # Rulebook §8 compares *every* cell, not only the mapped ones.
            row["_cells"] = tuple(sorted((str(k), repr(v)) for k, v in raw.items()))
            if with_file:
                row["_file"] = table.path.name
            yield table.name, ordinal, row


# ---------------------------------------------------------------- rules

@dataclass(frozen=True)
class _Rules:
    status: Any
    blank_cells_inherit: str | None
    duplicate_rows_once: str | None
    whole_order_returns: str | None
    service_fee: str | None
    order_goods: str | None
    evidence: tuple[str, ...]


def _rules(rulebook: str) -> _Rules:
    try:
        status = status_policy(rulebook)
    except StatusError as exc:
        raise ReadmeSalesError("RULE_CAPABILITY_MISSING", str(exc)) from exc
    blank = rule_line(rulebook, "o de trong o cac dong sau cua cung mot nhom dong",
                       "giong o do o dong dau nhom")
    duplicates = rule_line(rulebook, "hai dong cua cung mot don giong nhau o moi o",
                            "tinh mot lan")
    returns = rule_line(rulebook, "don tinh vao doanh thu", "ca don")
    fee = rule_line(rulebook, "mat hang", "phi phuc vu", "khong xep hang")
    order_goods = rule_line(rulebook, "doanh thu mot don", "tong thanh tien cac dong hang",
                            "khong tru giam gia tren ca don", "khong gom phi giao hang")
    evidence = tuple(x for x in (*status.evidence, blank, duplicates, returns, fee) if x)
    return _Rules(status, blank, duplicates, returns, fee, order_goods, evidence)


# ---------------------------------------------------------------- loading

def _check_read_amounts(roles: dict[str, "_Role"]) -> None:
    """A line amount documented in other words than "thành tiền sau giảm" (read from its
    head, e.g. "thành tiền của dòng") must check out as price x quantity - discount."""
    if "lines" not in roles:
        return
    for section, table in roles["lines"].tables:
        column = section.fields.get("amount")
        if section.origin != "readme" or column is None:
            continue
        meaning = fold(re.sub(r"\s*\(.*\)\s*\.?\s*$", "", section.columns[column]))
        if meaning.strip() in _MEANINGS:
            continue
        if not _amounts_check_out(table, section):
            raise ReadmeSalesError("AMOUNT_UNVERIFIED",
                                   "A line amount read from other words does not check out")


def load_readme_sales(catalog: Catalog, rulebook: str
                      ) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    """Load sales of one business from its README-described lists.

    Returns ``(sales, evidence, ranking_eligible)``.
    """
    sections = parse_sections(catalog.readme)
    notes = {column: meaning for column, meaning in convention_columns(catalog.readme).items()
             if meaning_field(meaning) == "status_note"}
    for column, meaning in notes.items():
        owners = [i for i, section in enumerate(sections) if column in section.columns]
        if len(owners) == 1:
            i = owners[0]
            section = sections[i]
            # Clear ownership and no more specific mapping in the column meaning itself.
            if fold(section.columns[column]) in _NOTE_HEADS:
                sections[i] = replace(section, columns={**section.columns, column: meaning})
    sections += profile_sections(catalog, sections)
    header_groups = None
    if not any({"order_id", "amount"} <= set(s.fields) or "order_goods" in s.fields
               for s in sections):
        for section in header_sections(catalog, sections):
            if section.origin == _HEADER_GROUPS:
                header_groups = section
            else:
                sections.append(section)
    if not sections:
        raise ReadmeSalesError("README_COLUMNS_MISSING", "README has no column descriptions")
    roles = _classify(sections, catalog)
    _check_read_amounts(roles)
    rules = _rules(rulebook)
    conventions = parse_conventions(catalog.readme)
    if "service_orders" in roles:
        if set(roles) & {"lines", "orders"}:
            raise ReadmeSalesError("AMBIGUOUS_ROLE", "Ticket ledger coexists with a sales list")
        return _from_service_orders(catalog, roles["service_orders"], rules)
    if "lines" in roles:
        sales, evidence, eligible = _from_lines(catalog, roles, rules, conventions)
        if header_groups is not None:
            sales, matched = _enrich_header_groups(catalog, header_groups, sales)
            if matched:
                evidence["product_groups_from_headers"] = {
                    "source": header_groups.label, "matched_rows": matched,
                }
        return sales, evidence, eligible
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
        if _summary_row(row, ("order_id", "date", "status")):
            counts["order_summary_rows_skipped"] += 1
            continue
        key = str(row["order_id"]).strip()
        if key in orders:
            # An order row repeated identically in every cell is an export duplicate when
            # the rulebook says so; any other repeat is a conflict.
            if rules.duplicate_rows_once and orders[key]["_cells"] == row["_cells"]:
                counts["order_duplicates_removed"] += 1
                continue
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Order list repeats an order id",
                                   role="orders")
        orders[key] = {**row, "_sheet": sheet}
    for row in orders.values():
        row.pop("_cells")
    counts["orders"] = len(orders)
    return orders, counts


def _products_index(
    roles: dict[str, _Role], headers: dict[tuple[Path, str], set[str]] | None = None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    if "products" not in roles:
        return by_id, by_name
    ambiguous: set[str] = set()
    coded = all("product_id" in section.fields for section in roles["products"].sections)
    for _sheet, _ordinal, row in _role_rows(roles["products"], headers):
        if _blank(row.get("product_id")) and coded:
            continue
        key = None if not coded else str(row["product_id"]).strip()
        if key is not None and key in by_id:
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Product list repeats a code",
                                   role="products")
        name = row.get("name", row.get("product_name"))
        record = {"id": key, "name": None if _blank(name) else str(name).strip(),
                  "base_unit": row.get("base_unit"), "group": row.get("product_group")}
        if key is not None:
            by_id[key] = record
        if record["name"]:
            name_key = _name_key(record["name"])
            if name_key in by_name:
                ambiguous.add(name_key)
            by_name[name_key] = record
    for name_key in ambiguous:
        by_name.pop(name_key, None)
    return by_id, by_name


def _enrich_header_groups(catalog: Catalog, section: Section,
                          sales: list[Sale]) -> tuple[list[Sale], int]:
    """A header-only catalogue can supply missing groups while preserving sales identities."""
    try:
        tables = _matching_tables(catalog, section)
        if len(tables) != 1:
            return sales, 0
        groups = _Role([section], [(section, tables[0])])
        by_id, by_name = _products_index({"products": groups})
    except (ReadmeSalesError, OSError):
        return sales, 0
    enriched = []
    matched = 0
    for sale in sales:
        if sale.product_group:
            enriched.append(sale)
            continue
        if sale.product_id and not sale.product_id_is_name:
            product = by_id.get(sale.product_id)
        else:
            product = by_name.get(_name_key(sale.product_name))
        group = _text(product["group"]) if product else None
        enriched.append(replace(sale, product_group=group) if group else sale)
        matched += group is not None
    return enriched, matched


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


def enrich_sales_from_readme_products(catalog: Catalog, sales: list[Sale]
                                      ) -> tuple[list[Sale], dict[str, int]]:
    """Attach documented product unit/group context to an otherwise valid flat export.

    This does not alter the flat adapter's row grain, amounts, statuses, identifiers or
    quantities.  A product is enriched only through a unique README-described product
    parent, joined by code when the sale has one and otherwise by an unambiguous name.
    """
    sections = parse_sections(catalog.readme)
    sections += profile_sections(catalog, sections)
    roles = _classify(sections, catalog)
    if "products" not in roles:
        return sales, {"matched_by_code": 0, "matched_by_name": 0, "unmatched": len(sales)}
    by_id, by_name = _products_index(roles)
    counts = Counter()
    enriched: list[Sale] = []
    for sale in sales:
        if sale.product_id and not sale.product_id_is_name:
            product = by_id.get(sale.product_id)
            counts["matched_by_code" if product is not None else "unmatched"] += 1
        elif sale.product_name:
            product = by_name.get(_name_key(sale.product_name))
            counts["matched_by_name" if product is not None else "unmatched"] += 1
        else:
            counts["unmatched"] += 1
        if product is None:
            enriched.append(sale)
            continue
        base_unit = (_name_key(product["base_unit"])
                     if not _blank(product.get("base_unit")) else sale.base_unit)
        enriched.append(replace(
            sale,
            base_unit=sale.base_unit or base_unit,
            product_group=sale.product_group or _text(product.get("group")),
        ))
    return enriched, dict(counts)


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
        if (not matches_source(section.file, orders.path.name)
                or section.sheet not in (None, orders.name)):
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
    headers: dict[tuple[Path, str], set[str]] | None = None
    if set(roles) <= {"lines", "products"} and not line_fields & {"customer_id", "customer_name"}:
        headers = {}
    products_by_id, products_by_name = _products_index(roles, headers)
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
    # Rows as records get "<sheet>#<row>"; a list kept in several files (one per month)
    # repeats sheet names, so its records are qualified by file.
    several_files = len({table.path for _, table in roles["lines"].tables}) > 1
    count_keys = [k for k in ("quantity", "quantity_base") if k in line_fields]
    for sheet, ordinal, row in _role_rows(
        roles["lines"], headers, with_file=True, sale_cost_readme=catalog.readme,
    ):
        source_file = row.pop("_file")
        counts["line_rows_raw"] += 1
        if _summary_row(row, order_keys + count_keys):
            # Subtotal, daily total or note rows ("Cộng", "Tổng ngày 01/03", "Ghi chú: ...")
            # carry no order and no quantity: they are not sales lines.
            counts["summary_rows_skipped"] += 1
            continue
        cells = row.pop("_cells")
        if row.get("_order_adjustment"):
            if not (rules.order_goods and rules.service_fee):
                raise ReadmeSalesError("RULE_CAPABILITY_MISSING",
                                       "Order adjustments need both the revenue and item rules")
            if not has_order_ids:
                raise ReadmeSalesError("MAPPING_INCOMPLETE",
                                       "Order adjustments need a documented order key")
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
            fingerprint = (key, sheet, cells)
            if fingerprint in seen and rules.duplicate_rows_once:
                counts["duplicates_removed"] += 1
                continue
            seen.add(fingerprint)
        else:
            key = f"{source_file}::{sheet}#{ordinal}" if several_files else f"{sheet}#{ordinal}"
            counts["rows_as_records"] += 1
        lines.append({**row, "_order": key, "_sheet": sheet})
    seen.clear()
    first_values.clear()
    if "status" not in line_fields and "status_note" in line_fields:
        _states_from_notes(lines)
        line_fields = line_fields | {"status"}
    merchandise = [line for line in lines if not line.get("_order_adjustment")]
    names_resolved = _expand_abbreviations(catalog, roles["lines"], merchandise)
    if names_resolved is not None:
        line_fields = line_fields | {"product_group"}

    status_counts: Counter[str] = Counter()
    order_state: dict[str, dict[str, Any]] = {}
    orphan_lines = 0
    labels = ({line.get("status") for line in lines} if "status" in line_fields
              else {row.get("status") for row in orders.values()})
    policy = by_elimination(rules.status, labels)
    for line in lines:
        key = line["_order"]
        parent = orders.get(key) if orders else None
        if orders and parent is None:
            orphan_lines += 1
            continue
        state = order_state.setdefault(key, {"days": set(), "statuses": set(), "times": set(),
                                             "negative": False, "returned": False})
        raw_date = line.get("date") if "date" in line_fields else (parent or {}).get("date")
        raw_status = line.get("status") if "status" in line_fields else (parent or {}).get("status")
        ambiguous = line.get("_state_ambiguous", False)
        if (_blank(raw_status) and not ambiguous) or _blank(raw_date):
            raise ReadmeSalesError("MISSING_VALUE", "Order date or status is missing",
                                   order_source="lines" if parent is None else "orders")
        try:
            status = AMBIGUOUS_STATE if ambiguous else policy.resolve(raw_status)
        except StatusError as exc:
            raise ReadmeSalesError("UNKNOWN_STATUS", str(exc)) from exc
        sheet = line["_sheet"] if "date" in line_fields else (parent or {}).get("_sheet", "")
        try:
            day = parse_day(raw_date, sheet)
        except (TypeError, ValueError) as exc:
            raise ReadmeSalesError("DATE_INVALID", "Order date could not be parsed") from exc
        line["_status"], line["_day"] = status, day
        quantity = None
        if not line.get("_order_adjustment") and not _blank(line.get("quantity")):
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
        if AMBIGUOUS_STATE in statuses:
            statuses = {AMBIGUOUS_STATE}
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
    # Rulebook §8 "Tên gọi": names of one thing are one thing. Without product codes, every
    # spelling of a name that differs only in case, spacing or accents ("Bia Tiger Lon",
    # "Bia  Tiger lon") is one product, identified by its most frequent spelling.
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    if not products_by_id:
        for line in merchandise:
            if _blank(line.get("product_id")) and not _blank(line.get("product_name")):
                name = str(line["product_name"]).strip()
                spellings[_name_key(name)][name] += 1
    canonical = {key: counts.most_common(1)[0][0] for key, counts in spellings.items()}
    merged_spellings = sum(len(counts) - 1 for counts in spellings.values())
    merchandise_orders = {line["_order"] for line in merchandise}
    adjustment_orders_kept = set()
    for line in lines:
        key = line["_order"]
        if line.get("_order_adjustment"):
            counts["order_adjustment_rows_excluded"] += 1
            if key not in merchandise_orders and key not in adjustment_orders_kept:
                adjustment_orders_kept.add(key)
                amount_by_order[key] = Decimal(0)
                context = _line_context(line, orders.get(key, {}), line_fields, customer_groups)
                sales.append(Sale(key, final_day[key], final_status[key], Decimal(0),
                                  None, None, None, minute=final_time[key], **context))
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
            # no product codes in this shop: the name identifies (one id per spelling group)
            product_id = canonical.get(_name_key(product_name), product_name)
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
        context = _line_context(line, orders.get(key, {}), line_fields, customer_groups)
        sales.append(Sale(key, final_day[key], final_status[key], amount, product_id,
                          product_name, quantity_base, minute=final_time[key],
                          quantity_sold=line["_quantity"],
                          unit_price_base=_line_price_per_base(line, quantity_base),
                          unit_cost_at_sale=optional_sale_cost(line.get("unit_cost_at_sale")),
                          product_id_is_name=product_id_is_name,
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
        "name_spellings_merged": merged_spellings,
        "amount_reconciliation": totals,
        "order_ids": "from_file" if has_order_ids else "one_record_per_row",
        "rules": list(rules.evidence),
        "readme_conventions": list(conventions.evidence),
        "status_by_elimination": dict(policy.eliminated),
    }
    if counts["order_adjustment_rows_excluded"]:
        evidence["rules"].append(rules.order_goods)
    if headers is not None:
        evidence["tables_without_customer_identity"] = _customer_identity_absence(
            catalog, roles, headers)
    if fee_rows and rules.service_fee is None:
        eligible = set()
    if names_resolved is False or (any(s.origin == "header" for s in roles["lines"].sections)
                                   and "product_id" not in line_fields):
        # Header-guessed columns with product names only: no catalogue says which spellings
        # are one product or breaks ties by code. Product rankings, counts and filters abstain.
        evidence["product_identity"] = "names_unverified"
    return sales, evidence, frozenset(eligible)


def _line_context(line: dict[str, Any], parent: dict[str, Any], line_fields: set[str],
                  customer_groups: dict[str, dict[str, str | None]]) -> dict[str, str | None]:
    context = {name: _text(line.get(name) if name in line_fields else parent.get(name))
               for name in ("customer_id", "customer_name", "customer_group", "channel",
                            "staff_id", "payment_method")}
    listed = customer_groups.get(context["customer_id"] or "")
    if listed:
        context["customer_group"] = context["customer_group"] or listed["group"]
        context["customer_name"] = context["customer_name"] or listed["name"]
    return context


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


def _line_price_per_base(line: dict[str, Any], quantity_base: Decimal | None) -> Decimal | None:
    raw = line.get("unit_price")
    if _blank(raw):
        return None
    try:
        price = parse_amount(raw)
    except ValueError:
        return None
    if line.get("_unit_price_is_base"):
        return price if price.is_finite() and price >= 0 else None
    return price_per_base_unit(price, line["_quantity"], quantity_base)


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


def _from_service_orders(catalog: Catalog, role: _Role, rules: _Rules
                         ) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    """A service register with an explicit one-ticket-per-row grain and no product facts."""
    if not rules.order_goods:
        raise ReadmeSalesError("RULE_CAPABILITY_MISSING", "Service tickets need the revenue rule")
    fields = _service_fields(role.sections[0])
    amount_field = "order_goods" if "order_goods" in fields else "service_goods"
    readme = fold(catalog.readme)
    plain = False
    for section in role.sections:
        amount_meaning = fold(section.columns[_service_fields(section)[amount_field]])
        gross = ("truoc giam gia" in amount_meaning or "khong tru giam gia" in amount_meaning)
        net_of_fees = ("khong gom phi giao" in amount_meaning
                       and "khong gom thue" in amount_meaning)
        # The README names no discount, fee or tax anywhere: the ticket total is the takings.
        plain = (_TICKET_TOTAL.fullmatch(amount_meaning.strip()) is not None
                 and not _PRICE_ADJUSTMENT.search(readme))
        if not (gross and net_of_fees or plain):
            raise ReadmeSalesError("AMOUNT_UNVERIFIED",
                                   "Ticket totals must document discount, tax and fee treatment")
    written_status = "status" in fields
    documented_notes = "status_note" in fields
    # One register with no state column and no state named anywhere in the README: every
    # ticket is a done job, except a zero ticket with a note (a free return visit, a quote):
    # whether it is a ticket is unknown, so it may change a count but never a total.
    stateless = (plain and len(role.sections) == 1 and not written_status
                 and not documented_notes and not _TICKET_STATE.search(readme))
    if not (written_status or documented_notes or stateless or
            (len(role.sections) == 1 and _ONLY_COMPLETE.search(readme))):
        raise ReadmeSalesError("MISSING_VALUE", "Ticket status or completion is undocumented")
    rows = []
    for sheet, ordinal, row in _role_rows(role, with_file=True, service=True):
        if _blank(row.get("date")) and not _blank(row.get(amount_field)):
            raise ReadmeSalesError("MISSING_VALUE", "Ticket amount without a date")
        if is_total_label(row.get("date")) or (
                _blank(row.get("date")) and _blank(row.get(amount_field))):
            continue
        # A note written across the date column ("Ghi chú: ..."), with no amount.
        if (isinstance(row.get("date"), str) and not re.search(r"\d", row["date"])
                and _blank(row.get(amount_field))):
            continue
        key = f"{row['_file']}::{sheet}#{ordinal}"
        rows.append((key, sheet, {**row, "_order": key}))
    if not written_status and documented_notes:
        _states_from_notes([row for _, _, row in rows])
    policy = by_elimination(rules.status, {r.get("status") for _, _, r in rows})
    sales = []
    statuses = Counter()
    for key, sheet, row in rows:
        part = "status"
        try:
            label = row.get("status") if written_status or documented_notes else "hoan thanh"
            status = AMBIGUOUS_STATE if row.get("_state_ambiguous") else policy.resolve(label)
            part = "date"
            day = parse_day(row.get("date"), sheet)
            part = "amount"
            amount = parse_amount(row.get(amount_field))
            if stateless and not amount and not _blank(row.get("ticket_note")):
                status = AMBIGUOUS_STATE
        except (TypeError, ValueError, StatusError) as exc:
            raise ReadmeSalesError("VALUE_INVALID", f"Ticket {part} unreadable") from exc
        sales.append(Sale(key, day, status, amount, None, None, None,
                          minute=parse_minute(row.get("time")), staff_id=_text(row.get("staff_id"))))
        statuses[status] += 1
    sales, staff_evidence = _ticket_workers(catalog, sales)
    evidence = {
        "adapter_version": ADAPTER_VERSION,
        "sources": {"service_orders": [s.label for s in role.sections]},
        "tables": sorted({table.key for _, table in role.tables}),
        "sales_rows": len(sales),
        "status_counts": dict(statuses),
        "order_ids": "one_record_per_row",
        "grain": "service_tickets",
        "rules": [*rules.evidence, rules.order_goods],
        **staff_evidence,
    }
    return sales, evidence, frozenset()


def _ticket_workers(catalog: Catalog, sales: list[Sale]) -> tuple[list[Sale], dict[str, Any]]:
    """Name each ticket's worker as the README's worker list does. Spellings that cannot be
    joined to exactly one listed worker leave every worker question unanswered."""
    from datamate.ingestion.service_staff import match_worker, spelling_key, worker_list

    written = {s.staff_id for s in sales if s.staff_id}
    if not written:
        return sales, {}
    workers = worker_list(catalog)
    if workers is None:
        keys = [spelling_key(name) for name in written]
        # Without a list, two spellings of one person would split that person's tickets.
        merged = len(set(keys)) != len(keys)
        return sales, ({"staff_unresolved": len(keys) - len(set(keys))} if merged else {})
    joined = {name: match_worker(name, workers) for name in written}
    unresolved = sum(worker is None for worker in joined.values())
    if unresolved:
        return sales, {"staff_unresolved": unresolved, "staff_list": len(workers)}
    sales = [replace(s, staff_id=joined[s.staff_id].name) if s.staff_id else s for s in sales]
    return sales, {"staff_list": len(workers), "staff_spellings": len(written),
                   "commission_percent": {w.name: str(w.commission_percent) for w in workers
                                          if w.commission_percent is not None}}


def _from_orders(roles: dict[str, _Role], rules: _Rules
                 ) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    """Shops that only keep an invoice list: one Sale per order, no product."""
    sales: list[Sale] = []
    status_counts: Counter[str] = Counter()
    seen: dict[str, tuple] = {}
    skipped = duplicates = 0
    rows = list(_role_rows(roles["orders"]))
    policy = by_elimination(rules.status, {row.get("status") for _, _, row in rows
                                           if not _summary_row(row, ("order_id", "date", "status"))})
    for sheet, _ordinal, row in rows:
        if _summary_row(row, ("order_id", "date", "status")):
            skipped += 1  # total / note rows of the invoice list
            continue
        if _blank(row.get("order_id")):
            raise ReadmeSalesError("MISSING_KEY", "Invoice without order id")
        key = str(row["order_id"]).strip()
        if key in seen:
            if rules.duplicate_rows_once and seen[key] == row["_cells"]:
                duplicates += 1  # export duplicate (rulebook): counted once
                continue
            raise ReadmeSalesError("DUPLICATE_PARENT_KEY", "Invoice list repeats an order id")
        seen[key] = row["_cells"]
        try:
            status = policy.resolve(row.get("status"))
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
        "duplicates_removed": duplicates,
        "status_by_elimination": dict(policy.eliminated),
        "order_ids": "from_file",
        "grain": "one row per order; amount is the order's goods total (README)",
        "rules": list(rules.evidence),
    }
    return sales, evidence, frozenset()
