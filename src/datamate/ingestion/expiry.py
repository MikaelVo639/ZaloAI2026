"""Remaining lots at the end of the documented data day."""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from datamate.contracts import Catalog
from datamate.ingestion.debt import _coverage_day, parse_day
from datamate.ingestion.readme_sales import (
    _matching_tables,
    _records,
    parse_amount,
    parse_quantity,
    parse_sections,
)
from datamate.semantics.text import fold


@dataclass(frozen=True)
class Lot:
    product: str
    expires: date | None
    remaining: Decimal
    unit_cost: Decimal | None = None


_DOCUMENTED_FIELDS = {
    "so lo": "lot", "ma hang": "product", "ma don": "order",
    "han su dung": "expires", "han dung": "expires",
    "so luong nhap don vi co ban": "received",
    "so luong ban lay tu lo don vi co ban": "sold",
    "so luong khach tra lai don vi co ban": "returned",
    "ten": "name", "ten hang": "name", "don vi co ban": "base_unit",
}
_SQLITE = {
    "receipts": ("lo_hang", {"lot": "MaLo", "product": "MaHangHoa",
                              "expires": "HanSuDung", "received": "SLNhapQuyDoi"}),
    "sales": ("chi_tiet_don_lo", {"order": "MaHoaDon", "lot": "MaLo",
                                   "sold": "SLQuyDoi", "returned": "SLTraQuyDoi"}),
    "products": ("san_pham", {"product": "MaHangHoa", "name": "TenGoi",
                                "base_unit": "DonViCoSo"}),
}
_EXCEL = {
    "receipts": ("BanHang_LoHang.xlsx", {"lot": "Lô", "product": "Mã món",
                                         "expires": "HSD", "received": "SL nhập (ĐVT cơ bản)"}),
    "sales": ("BanHang_BanTheoLo.xlsx", {"order": "Mã hóa đơn", "lot": "Lô",
                                         "sold": "SL quy ra lẻ", "returned": "SL trả (ĐVT cơ bản)"}),
    "products": ("BanHang_HangHoa.xlsx", {"product": "ma_sp", "name": "Món",
                                          "base_unit": "Đơn vị"}),
}


def _lot_tables(catalog: Catalog) -> dict:
    groups = {key: [] for key in _SQLITE}
    for section in parse_sections(catalog.readme):
        fields = {}
        for column, meaning in section.columns.items():
            key = _DOCUMENTED_FIELDS.get(fold(meaning.split(";", 1)[0]))
            if key in fields:
                raise ValueError("Ambiguous lot column in README")
            if key:
                fields[key] = column
        for role, required in _SQLITE.items():
            if required[1].keys() <= fields.keys():
                tables = _matching_tables(catalog, section)
                if not tables:
                    raise ValueError("Missing documented lot table")
                groups[role].extend((table, fields) for table in tables)
    if all(groups.values()):
        return groups
    for profile in (_SQLITE, _EXCEL):
        matched = {}
        for role, (source, fields) in profile.items():
            if f"`{source}`" not in catalog.readme:
                break
            tables = [table for table in catalog.tables
                      if source in (table.name, table.path.name)
                      and set(fields.values()) <= set(table.columns)]
            if len(tables) != 1:
                break
            matched[role] = [(tables[0], fields)]
        if len(matched) == len(profile):
            return matched
    raise ValueError("The README and export do not identify all three lot lists")


def _text(value: object) -> str:
    if value is None or not str(value).strip():
        raise ValueError("Lot list has a blank key or product name")
    return str(value).strip()


def _values(group):
    for table, fields in group:
        for _, row in _records(table, fields.values()):
            yield {key: row[column] for key, column in fields.items()}


def _lot_costs(catalog: Catalog, lots: dict[str, Lot], receipts: dict[str, str]
               ) -> tuple[dict[str, Decimal], str | None]:
    if not any("chi tiet nhap dongianhap la gia nhap mot don vi theo don vi nhap" in fold(line)
               for line in catalog.readme.splitlines()):
        return {}, None
    fields = ("MaLo", "MaHangHoa", "MaPhieuNhap", "SL", "DonGiaNhap")
    tables = [t for t in catalog.tables if t.name == "chi_tiet_nhap"
              and set(fields) <= set(t.columns)]
    if len(tables) != 1:
        return {}, None
    costs = {}
    table = tables[0]
    for _, row in _records(table, fields):
        lot = row["MaLo"]
        if lot not in lots:
            continue
        received = lots[lot]
        quantity = parse_quantity(row["SL"])
        if (lot in costs or quantity <= 0 or received.remaining <= 0
                or row["MaHangHoa"] != received.product or row["MaPhieuNhap"] != receipts[lot]):
            raise ValueError("Lot purchase line cannot be joined uniquely")
        costs[lot] = parse_amount(row["DonGiaNhap"]) * quantity / received.remaining
    return costs, table.key


def load_lots(catalog: Catalog, day: date, *, with_cost: bool = False
              ) -> tuple[tuple[Lot, ...], dict[str, str], list[str]]:
    if _coverage_day(catalog.readme) != day:
        raise ValueError("Lot ledger without dated movements supports only the last data day")
    convention = ("moi dong nhap la mot lo", "so lo khong trung",
                  "hang khach tra ve lai lo da ban")
    if not any(all(part in fold(line) for part in convention)
               for line in catalog.readme.splitlines()):
        raise ValueError("README does not document lot identity and returns")
    groups = _lot_tables(catalog)
    sources = [table.key for group in groups.values() for table, _ in group]
    lots: dict[str, Lot] = {}
    receipts: dict[str, str] = {}
    if with_cost and groups["receipts"][0][0].name == "lo_hang":
        table, fields = groups["receipts"][0]
        groups["receipts"] = [(table, {**fields, "receipt": "MaPhieuNhap"})]
    for row in _values(groups["receipts"]):
        lot = _text(row["lot"])
        if lot in lots:
            raise ValueError("Repeated receipt lot code")
        expires = row["expires"]
        quantity = parse_quantity(row["received"])
        if quantity < 0:
            raise ValueError("Negative receipt quantity")
        lots[lot] = Lot(_text(row["product"]),
                        parse_day(expires) if expires not in (None, "") else None, quantity)
        if "receipt" in row:
            receipts[lot] = _text(row["receipt"])
    costs, cost_source = _lot_costs(catalog, lots, receipts) if receipts else ({}, None)
    if with_cost and not cost_source:
        raise ValueError("No documented purchase-price source for lots")
    if cost_source:
        sources.append(cost_source)
    for row in _values(groups["sales"]):
        _text(row["order"])
        lot = _text(row["lot"])
        sold, returned = parse_quantity(row["sold"]), parse_quantity(row["returned"])
        if lot not in lots or not 0 <= returned <= sold:
            raise ValueError("Invalid sale or return against a lot")
        remaining = lots[lot].remaining - sold + returned
        if remaining < 0:
            raise ValueError("Lot sales exceed the received quantity")
        lots[lot] = replace(lots[lot], remaining=remaining)
    names = {}
    for row in _values(groups["products"]):
        key, name = _text(row["product"]), _text(row["name"])
        if key in names:
            raise ValueError("Repeated product code in lot catalogue")
        names[key] = name
    if any(lot.product not in names for lot in lots.values()):
        raise ValueError("Lot references an unknown catalogue product")
    return tuple(replace(row, unit_cost=costs.get(key)) for key, row in lots.items()), names, sources
