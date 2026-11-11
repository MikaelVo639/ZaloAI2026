"""Purchase receipts (phiếu nhập) of one business, from the lists its README describes.

Two documented shapes: a receipt list with the total of each receipt ("tổng tiền phiếu
nhập", optional "trạng thái phiếu nhập"), or a purchase book whose lines carry "tiền nhập
của cả dòng" and are summed per receipt. A KiotViet receipt export without a README
section is read with the meanings KiotViet-export READMEs give its columns. Anything
undocumented, inconsistent or with an unknown status raises ``PurchaseSourceError``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog, Table
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    Section,
    _matching_tables,
    _records,
    _table_headers,
    parse_amount,
    parse_day,
    parse_quantity,
    parse_sections,
)
from datamate.semantics.text import fold

_FIELDS = {
    "ma phieu nhap": "receipt_id",
    "ngay nhap": "day",
    "tong tien phieu nhap": "total",
    "trang thai phieu nhap": "status",
    "tien nhap cua ca dong": "line_amount",
    "nha cung cap": "supplier_name",
    "ma nha cung cap": "supplier_code",
    # Lines of a receipt (giá nhập bình quân, rulebook §5).
    "ma hang": "product_id",
    "ten hang": "product_name",
    "don vi tinh": "unit",
    "don vi nhap": "unit",
    "so luong": "quantity",
    "so luong nhap": "quantity",
    "gia nhap mot don vi": "unit_price",
}
_LINE_FIELDS = frozenset({"receipt_id", "unit", "quantity", "unit_price"})
_KIOTVIET_RECEIPTS = {
    "Mã nhập hàng": "mã phiếu nhập", "Thời gian": "ngày nhập", "Mã NCC": "mã nhà cung cấp",
    "Cần trả NCC": "tổng tiền phiếu nhập", "Trạng thái": "trạng thái phiếu nhập",
}
# KiotViet receipt lines, as KiotViet-export READMEs describe "DanhSachChiTietNhapHang".
_KIOTVIET_LINES = {
    "Mã nhập hàng": "mã phiếu nhập", "Mã hàng": "mã hàng", "ĐVT": "đơn vị tính",
    "Số lượng": "số lượng", "Đơn giá": "giá nhập một đơn vị",
}
_SQLITE_RECEIPTS = {
    "MaPhieuNhap": "mã phiếu nhập", "NgayNhapKho": "ngày nhập",
    "TongGiaTriNhap": "tổng tiền phiếu nhập", "TinhTrang": "trạng thái phiếu nhập",
    "MaNhaCungCap": "mã nhà cung cấp",
}
_SQLITE_LINES = {
    "MaPhieuNhap": "mã phiếu nhập", "MaHangHoa": "mã hàng",
    "DonViTinh": "đơn vị nhập", "SL": "số lượng nhập", "DonGiaNhap": "giá nhập một đơn vị",
}
_SQLITE_RECEIPT_EVIDENCE = "phieu nhap tonggiatrinhap la tong tien cua ca phieu nhap"
_NOTEBOOK_LINES = {
    "Số phiếu": "mã phiếu nhập", "Ngày nhập": "ngày nhập",
    "Product": "tên hàng", "ĐVT nhập": "đơn vị nhập", "SL nhập": "số lượng nhập",
    "Giá nhập": "giá nhập một đơn vị", "Thành tiền": "tiền nhập của cả dòng",
    "Nhà cung cấp": "nhà cung cấp",
}
_DONE = frozenset({"da nhap hang", "da nhap", "hoan thanh", "received"})


class PurchaseSourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    day: date
    total: Decimal
    supplier: str | None  # supplier name, or code when the source only records a code
    cancelled: bool


@dataclass(frozen=True)
class PurchaseBook:
    receipts: tuple[Receipt, ...]
    supplier_names: bool  # True when ``supplier`` is a name, False for codes / unknown
    source: str


def _field(meaning: str) -> str | None:
    text = fold(re.sub(r"\([^)]*\)\s*$", "", meaning)).strip()
    return _FIELDS.get(text)


def _purchase_headers(catalog: Catalog, documented: set[str],
                      signature: dict[str, str]) -> list[tuple[Table, set[str]]]:
    """Use catalog headers first; preview titles only in a plausible purchase workbook."""
    return _table_headers(
        table for table in catalog.tables
        if table.path.name not in documented and (
            signature.keys() <= set(table.columns)
            or "nhap" in fold(f"{table.path.stem} {table.name}")
        )
    )


def _profile(table: Table, meanings: dict[str, str],
             origin: str) -> tuple[Section, dict[str, str]]:
    section = Section(table.path.name, table.name, meanings, origin=origin)
    return section, {_FIELDS[fold(meaning)]: column for column, meaning in meanings.items()}


def _sqlite_profiles(catalog: Catalog, table_name: str,
                     meanings: dict[str, str]) -> list[tuple[Section, dict[str, str]]]:
    return [_profile(table, meanings, "purchase_sqlite_pos.v1")
            for table in catalog.tables
            if table.path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
            and table.name == table_name and meanings.keys() <= set(table.columns)]


def _sections(catalog: Catalog) -> list[tuple[Section, dict[str, str]]]:
    found = []
    sections = parse_sections(catalog.readme)
    for section in sections:
        fields = {}
        for column, meaning in section.columns.items():
            field = _field(meaning)
            if field and field not in fields:
                fields[field] = column
        if {"receipt_id", "day"} <= set(fields) and ({"total", "line_amount"} & set(fields)):
            found.append((section, fields))
    if found:
        return found
    documented = {section.file for section in sections}
    text = fold(catalog.readme)
    if _SQLITE_RECEIPT_EVIDENCE in text:
        found.extend(_sqlite_profiles(catalog, "phieu_nhap", _SQLITE_RECEIPTS))
    for table, header in _purchase_headers(catalog, documented, _KIOTVIET_RECEIPTS):
        if _KIOTVIET_RECEIPTS.keys() <= header:
            found.append(_profile(table, _KIOTVIET_RECEIPTS, "purchase_kiotviet.v1"))
        elif (_NOTEBOOK_LINES.keys() <= header and table.path.stem.startswith("SoTay_")
              and "thanh tien la tien nhap cua ca dong" in text):
            found.append(_profile(table, _NOTEBOOK_LINES, "purchase_notebook.v1"))
    return found


def _cancelled(label: object) -> bool:
    text = fold(str(label or ""))
    if "huy" in text.split() or text in {"cancelled", "canceled"}:
        return True
    if text in _DONE:
        return False
    raise PurchaseSourceError("status", "Unknown purchase receipt status")


def _sqlite_supplier_names(catalog: Catalog, source: Section) -> dict[str, str]:
    tables = [table for table in catalog.tables if table.path.name == source.file
              and table.name == "nha_cung_cap" and {"MaNhaCungCap", "TenGoi"} <= set(table.columns)]
    if len(tables) != 1:
        return {}
    names: dict[str, str] = {}
    for _, row in _records(tables[0], ("MaNhaCungCap", "TenGoi")):
        code, name = (str(row.get(column) or "").strip() for column in ("MaNhaCungCap", "TenGoi"))
        if not code or not name or code in names:
            return {}
        names[code] = name
    return names if len(set(names.values())) == len(names) else {}


def load_purchases(catalog: Catalog) -> PurchaseBook:
    sections = _sections(catalog)
    headers = [(s, f) for s, f in sections if "total" in f]
    lines = [(s, f) for s, f in sections if "line_amount" in f and "total" not in f]
    chosen = headers or lines
    if not chosen:
        raise PurchaseSourceError("absent", "README describes no purchase receipts")
    if len(chosen) != 1:
        raise PurchaseSourceError("source", "Several purchase lists of one shape")
    section, fields = chosen[0]
    supplier_field = ("supplier_name" if "supplier_name" in fields
                      else "supplier_code" if "supplier_code" in fields else None)
    try:
        tables = _matching_tables(catalog, section)
        if not tables:
            raise PurchaseSourceError("source", "README purchase list is not present")
        rows = [row for table in tables
                for _ordinal, row in _records(table, section.columns)]
    except ReadmeSalesError as exc:
        raise PurchaseSourceError("source", str(exc)) from exc
    receipts: dict[str, dict] = {}
    totals: dict[str, Decimal] = defaultdict(Decimal)
    try:
        for row in rows:
            raw_id = row.get(fields["receipt_id"])
            if raw_id in (None, "") or not str(raw_id).strip():
                continue  # total / note rows
            key = str(raw_id).strip()
            day = parse_day(row.get(fields["day"]))
            supplier = row.get(fields[supplier_field]) if supplier_field else None
            supplier = None if supplier in (None, "") else str(supplier).strip()
            cancelled = _cancelled(row.get(fields["status"])) if "status" in fields else False
            record = {"day": day, "supplier": supplier, "cancelled": cancelled}
            if headers:
                if key in receipts:
                    raise PurchaseSourceError("key", "Receipt list repeats a receipt id")
                totals[key] = parse_amount(row.get(fields["total"]))
            else:
                if key in receipts and receipts[key] != record:
                    raise PurchaseSourceError("key", "Lines of one receipt disagree")
                totals[key] += parse_amount(row.get(fields["line_amount"]))
            receipts[key] = record
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PurchaseSourceError):
            raise
        raise PurchaseSourceError("value", f"Purchase value unreadable: {exc}") from exc
    if not receipts:
        raise PurchaseSourceError("absent", "Purchase list is empty")
    supplier_names = (_sqlite_supplier_names(catalog, section)
                      if section.origin == "purchase_sqlite_pos.v1" else {})
    return PurchaseBook(
        tuple(Receipt(key, r["day"], totals[key],
                      supplier_names.get(r["supplier"]) if supplier_names else r["supplier"],
                      r["cancelled"])
              for key, r in receipts.items()),
        supplier_field == "supplier_name" or bool(supplier_names), section.label)


@dataclass(frozen=True)
class PurchaseLine:
    receipt_id: str
    day: date
    product_id: str | None
    product_name: str | None
    unit: str  # đơn vị nhập of the line
    quantity: Decimal
    unit_price: Decimal  # giá nhập một đơn vị nhập
    cancelled: bool


@dataclass(frozen=True)
class PurchaseLines:
    lines: tuple[PurchaseLine, ...]
    source: str
    convention: str


def _price_convention(readme: str) -> tuple[str, str]:
    """"**Nhập hàng**: **<cột>** là giá nhập một đơn vị, theo đơn vị nhập của dòng"."""
    for line in readme.splitlines():
        heading, separator, description = line.partition(":")
        if not separator or fold(heading) != "nhap hang":
            continue
        match = re.match(r"\s*(?:\*\*([^*]+)\*\*|`([^`]+)`)", description)
        if match and fold(description[match.end():]).startswith(
                "la gia nhap mot don vi theo don vi nhap cua dong"):
            return (match.group(1) or match.group(2)).strip(), line.strip()
    raise PurchaseSourceError("absent", "README states no purchase unit price convention")


def _line_sections(catalog: Catalog, price: str) -> list[tuple[Section, dict[str, str]]]:
    found = []
    sections = parse_sections(catalog.readme)
    for section in sections:
        fields: dict[str, str] = {}
        for column, meaning in section.columns.items():
            field = _field(meaning)
            if field and field not in fields:
                fields[field] = column
        if (_LINE_FIELDS <= set(fields) and fields["unit_price"] == price
                and {"product_id", "product_name"} & set(fields)):
            found.append((section, fields))
    if found:
        return found
    documented = {section.file for section in sections}
    if price == "chi_tiet_nhap.DonGiaNhap" and _SQLITE_RECEIPT_EVIDENCE in fold(catalog.readme):
        return _sqlite_profiles(catalog, "chi_tiet_nhap", _SQLITE_LINES)
    if price == "Đơn giá":
        for table, header in _purchase_headers(catalog, documented, _KIOTVIET_LINES):
            if _KIOTVIET_LINES.keys() <= header:
                found.append(_profile(table, _KIOTVIET_LINES, "purchase_kiotviet.v1"))
    elif price == "Giá nhập" and "thanh tien la tien nhap cua ca dong" in fold(catalog.readme):
        for table, header in _purchase_headers(catalog, documented, _NOTEBOOK_LINES):
            if _NOTEBOOK_LINES.keys() <= header and table.path.stem.startswith("SoTay_"):
                found.append(_profile(table, _NOTEBOOK_LINES, "purchase_notebook.v1"))
    return found


def load_purchase_lines(catalog: Catalog) -> PurchaseLines:
    """Receipt lines with product, purchase unit, quantity and unit price.

    A line list without dates takes the day and status of its receipt from the receipt
    list (``load_purchases``); a purchase book carries them on every line.
    """
    price, convention = _price_convention(catalog.readme)
    sections = _line_sections(catalog, price)
    if not sections:
        raise PurchaseSourceError("absent", "README describes no purchase lines")
    if len(sections) != 1:
        raise PurchaseSourceError("source", "Several purchase line lists")
    section, fields = sections[0]
    receipts: dict[str, Receipt] = {}
    if "day" not in fields:
        receipts = {r.receipt_id: r for r in load_purchases(catalog).receipts}
    try:
        tables = _matching_tables(catalog, section)
        if not tables:
            raise PurchaseSourceError("source", "README purchase line list is not present")
        rows = [row for table in tables for _ordinal, row in _records(table, section.columns)]
    except ReadmeSalesError as exc:
        raise PurchaseSourceError("source", str(exc)) from exc
    lines: list[PurchaseLine] = []
    try:
        for row in rows:
            raw_id = row.get(fields["receipt_id"])
            if raw_id in (None, "") or not str(raw_id).strip():
                continue  # total / note rows
            key = str(raw_id).strip()
            if receipts:
                receipt = receipts.get(key)
                if receipt is None:
                    raise PurchaseSourceError("key", "Purchase line of an unknown receipt")
                day, cancelled = receipt.day, receipt.cancelled
            else:
                day = parse_day(row.get(fields["day"]))
                cancelled = _cancelled(row.get(fields["status"])) if "status" in fields else False
            code = row.get(fields.get("product_id", ""))
            name = row.get(fields.get("product_name", ""))
            unit = row.get(fields["unit"])
            if (code in (None, "") and name in (None, "")) or unit in (None, ""):
                raise PurchaseSourceError("value", "Purchase line without product or unit")
            quantity = parse_quantity(row.get(fields["quantity"]))
            unit_price = parse_amount(row.get(fields["unit_price"]))
            if quantity <= 0 or unit_price < 0:
                raise PurchaseSourceError("value", "Purchase line quantity or price out of range")
            lines.append(PurchaseLine(
                key, day, None if code in (None, "") else str(code).strip(),
                None if name in (None, "") else str(name).strip(), str(unit).strip(),
                quantity, unit_price, cancelled))
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PurchaseSourceError):
            raise
        raise PurchaseSourceError("value", f"Purchase line unreadable: {exc}") from exc
    if not lines:
        raise PurchaseSourceError("absent", "Purchase line list is empty")
    return PurchaseLines(tuple(lines), section.label, convention)
