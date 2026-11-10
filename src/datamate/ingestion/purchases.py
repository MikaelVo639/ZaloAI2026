"""Purchase receipts (phiếu nhập) of one business, from the lists its README describes.

Receipt totals ("tổng tiền phiếu nhập") may appear once per receipt or repeat on documented
detail rows. Purchase books carrying "tiền nhập của cả dòng" are summed per receipt.
A KiotViet receipt export without a README
section is read with the meanings KiotViet-export READMEs give its columns. Anything
undocumented, inconsistent or with an unknown status raises ``PurchaseSourceError``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.readme_sales import (
    ReadmeSalesError,
    Section,
    _header_names,
    _matching_tables,
    _records,
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
    "so thu tu dong trong phieu nhap": "line_id",
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
_DONE = frozenset({"da nhap hang", "da nhap", "hoan thanh"})
_POS_RECEIPTS = {
    "MaPhieuNhap": "mã phiếu nhập", "NgayNhapKho": "ngày nhập",
    "MaNhaCungCap": "mã nhà cung cấp", "TinhTrang": "trạng thái phiếu nhập",
    "TongGiaTriNhap": "tổng tiền phiếu nhập",
}
_POS_LINES = {
    "MaPhieuNhap": "mã phiếu nhập", "MaHangHoa": "mã hàng", "DonViTinh": "đơn vị nhập",
    "SL": "số lượng nhập", "DonGiaNhap": "giá nhập một đơn vị",
}
_PURCHASE_NOTEBOOK = {
    "Số phiếu": "mã phiếu nhập", "Ngày nhập": "ngày nhập", "Product": "tên hàng",
    "Nhà cung cấp": "nhà cung cấp", "Thành tiền": "tiền nhập của cả dòng",
    "ĐVT nhập": "đơn vị nhập", "SL nhập": "số lượng nhập", "Giá nhập": "giá nhập một đơn vị",
}


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
    supplier_code: str | None = None


@dataclass(frozen=True)
class PurchaseBook:
    receipts: tuple[Receipt, ...]
    supplier_names: bool  # True when ``supplier`` is a name, False for codes / unknown
    source: str
    supplier_codes: bool = False


def _field(meaning: str) -> str | None:
    text = fold(re.sub(r"\([^)]*\)\s*$", "", meaning)).strip()
    return _FIELDS.get(text)


def _sections(catalog: Catalog) -> list[tuple[Section, dict[str, str]]]:
    found = []
    for section in parse_sections(catalog.readme):
        fields = {}
        for column, meaning in section.columns.items():
            field = _field(meaning)
            if field and field not in fields:
                fields[field] = column
        if {"receipt_id", "day"} <= set(fields) and ({"total", "line_amount"} & set(fields)):
            found.append((section, fields))
    if found:
        return found
    for table in catalog.tables:
        if (table.path.name == "SoTay_NhapHang.xlsx"
                and "`SoTay_NhapHang.xlsx`" in catalog.readme
                and "thanh tien la tien nhap cua ca dong" in fold(catalog.readme)):
            section = Section(table.path.name, table.name, dict(_PURCHASE_NOTEBOOK))
            found.append((section, {_field(m): c for c, m in section.columns.items()}))
        if (table.name == "phieu_nhap" and set(_POS_RECEIPTS) <= _header_names(table)
                and "phieu_nhap.tonggiatrinhap" in catalog.readme.lower()):
            section = Section(table.path.name, table.name, dict(_POS_RECEIPTS))
            found.append((section, {_field(m): c for c, m in section.columns.items()}))
    if found:
        return found
    documented = {s.file for s in parse_sections(catalog.readme)}
    for table in catalog.tables:
        if table.path.name not in documented and set(_KIOTVIET_RECEIPTS) <= _header_names(table):
            section = Section(table.path.name, table.name, dict(_KIOTVIET_RECEIPTS))
            found.append((section, {_field(m): c for c, m in _KIOTVIET_RECEIPTS.items()}))
    return found


def _cancelled(label: object) -> bool:
    text = fold(str(label or ""))
    if "huy" in text.split():
        return True
    if text in _DONE:
        return False
    raise PurchaseSourceError("status", "Unknown purchase receipt status")


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
            code = row.get(fields.get("supplier_code", ""))
            code = None if code in (None, "") else str(code).strip()
            status = row.get(fields["status"]) if "status" in fields else None
            if section.columns == _POS_RECEIPTS:
                status = {"received": "đã nhập hàng", "cancelled": "đã hủy"}.get(status, status)
            cancelled = _cancelled(status) if "status" in fields else False
            record = {"day": day, "supplier": supplier, "cancelled": cancelled, "code": code}
            if headers:
                amount = parse_amount(row.get(fields["total"]))
                line_id = row.get(fields.get("line_id", ""))
                if "line_id" in fields and (line_id is None or not str(line_id).strip()):
                    raise PurchaseSourceError("key", "Receipt detail is missing its line number")
                if key in receipts:
                    if "line_id" not in fields or receipts[key] != record or totals[key] != amount:
                        raise PurchaseSourceError("key", "Receipt headers repeat or disagree")
                totals[key] = amount
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
    return PurchaseBook(
        tuple(Receipt(key, r["day"], totals[key], r["supplier"], r["cancelled"], r["code"])
              for key, r in receipts.items()),
        supplier_field == "supplier_name", section.label, "supplier_code" in fields)


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
    product_codes: bool = False


def _price_convention(readme: str) -> tuple[str, str]:
    """"**Nhập hàng**: **<cột>** là giá nhập một đơn vị, theo đơn vị nhập của dòng"."""
    for line in readme.splitlines():
        text = fold(line)
        if ("chi_tiet_nhap.dongianhap" in line.lower()
                and "la gia nhap mot don vi theo don vi nhap cua dong" in text):
            return "DonGiaNhap", line.strip()
        if "nhap hang" in text and "la gia nhap mot don vi theo don vi nhap cua dong" in text:
            names = re.findall(r"\*\*([^*]+)\*\*", line)
            if len(names) >= 2 and fold(names[0]) == "nhap hang":
                return names[1].strip(), line.strip()
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
    if price == "Giá nhập":
        return [(s, f) for s, f in _sections(catalog)
                if s.file == "SoTay_NhapHang.xlsx" and _LINE_FIELDS <= f.keys()]
    if price == "DonGiaNhap":
        for table in catalog.tables:
            if table.name == "chi_tiet_nhap" and set(_POS_LINES) <= _header_names(table):
                section = Section(table.path.name, table.name, dict(_POS_LINES))
                found.append((section, {_field(m): c for c, m in section.columns.items()}))
        return found
    documented = {s.file for s in sections}
    if price != "Đơn giá":
        return found
    for table in catalog.tables:
        if table.path.name not in documented and set(_KIOTVIET_LINES) <= _header_names(table):
            section = Section(table.path.name, table.name, dict(_KIOTVIET_LINES))
            found.append((section, {_field(m): c for c, m in _KIOTVIET_LINES.items()}))
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
    return PurchaseLines(tuple(lines), section.label, convention, "product_id" in fields)
