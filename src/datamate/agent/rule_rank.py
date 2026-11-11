"""One executor for every enabled rule spec (semantics/rule_spec.py).

``rank`` groups the period's rows by the spec's đối tượng, measures each group by its chỉ
tiêu, keeps the groups that meet its điều kiện and orders them by its chọn. Ties follow the
rulebook's "bằng nhau" row (code ascending, else name); without that row, or for days,
a tie at the cut abstains instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from datamate.ingestion.purchases import Receipt
from datamate.ingestion.sales import Sale
from datamate.semantics.rule_spec import RuleSpec


class RankError(ValueError):
    """The spec cannot be computed on this data (no rows, a tie without a rule, …)."""


@dataclass
class _Group:
    name: str
    code: str | None
    orders: set = field(default_factory=set)
    days: set = field(default_factory=set)
    revenue: Decimal = Decimal(0)
    quantity: Decimal = Decimal(0)
    cancelled: set = field(default_factory=set)


_VALID_STATUS = {
    "luat_chung": {"valid"}, "bo_huy_tra": {"valid"}, "bo_huy": {"valid", "returned"},
    "chi_huy": {"cancelled"}, "moi_don": {"valid", "returned", "cancelled"},
}


def _customer(sale: Sale, has_codes: bool) -> tuple[str, str, str | None] | None:
    from datamate.agent.compute import _customer_name_key
    if has_codes:
        if not sale.customer_id:
            return None
        return sale.customer_id, sale.customer_name or sale.customer_id, sale.customer_id
    key = _customer_name_key(sale.customer_name)
    return None if key is None else (key, sale.customer_name or key, None)


def _key(sale: Sale, subject: str, has_codes: bool) -> tuple[object, str, str | None] | None:
    if subject == "khach":
        return _customer(sale, has_codes)
    if subject == "mat_hang":
        if not sale.product_id:
            return None
        return sale.product_id, sale.product_name or sale.product_id, sale.product_id
    if subject == "nhan_vien":
        return (sale.staff_id, sale.staff_id, sale.staff_id) if sale.staff_id else None
    if subject == "ngay":
        return sale.day, sale.day.isoformat(), None
    raise RankError(f"{subject} is not grouped from sales")


def _measure(group: _Group, measure: str) -> Decimal:
    if measure == "so_don":
        return Decimal(len(group.orders))
    if measure == "so_ngay_co_mua":
        return Decimal(len(group.days))
    if measure == "doanh_thu":
        return group.revenue
    if measure == "gia_tri_tb_don":
        if not group.orders:
            raise RankError("average order value of a group without orders")
        return group.revenue / len(group.orders)
    if measure == "so_don_huy":
        return Decimal(len(group.cancelled))
    if measure == "so_luong_quy_doi":
        return group.quantity
    raise RankError(f"{measure} is not measured from sales")


def sales_groups(spec: RuleSpec, sales: list[Sale], start: date, end: date
                 ) -> dict[object, _Group]:
    """Groups of the period's sales rows under the spec's validity."""
    period = [s for s in sales if start <= s.day <= end]
    statuses = _VALID_STATUS[spec.don_hop_le]
    has_codes = any(s.customer_id for s in sales)
    groups: dict[object, _Group] = {}
    for sale in period:
        key = _key(sale, spec.doi_tuong, has_codes)
        if key is None:
            continue
        identity, name, code = key
        group = groups.setdefault(identity, _Group(name, code))
        if sale.status == "cancelled":
            group.cancelled.add(sale.order_id)
        if spec.chi_tieu == "so_don_huy" or sale.status not in statuses:
            continue
        group.orders.add(sale.order_id)
        group.days.add(sale.day)
        group.revenue += sale.amount
        if spec.chi_tieu == "so_luong_quy_doi":
            if sale.quantity_base is None:
                raise RankError("a sold quantity has no base-unit quantity")
            group.quantity += sale.quantity_base
    if spec.chi_tieu != "so_don_huy":
        groups = {k: g for k, g in groups.items() if g.orders}
    if spec.doi_tuong == "ngay" and spec.dieu_kien.get("tinh_ngay_khong_don") and sales:
        first, last = min(s.day for s in sales), max(s.day for s in sales)
        day = max(start, first)
        while day <= min(end, last):
            groups.setdefault(day, _Group(day.isoformat(), None))
            day += timedelta(days=1)
    return groups


def supplier_groups(spec: RuleSpec, receipts: tuple[Receipt, ...], start: date, end: date
                    ) -> dict[object, _Group]:
    groups: dict[object, _Group] = {}
    for receipt in receipts:
        if not start <= receipt.day <= end or receipt.supplier is None:
            continue
        if receipt.cancelled and spec.don_hop_le != "moi_don":
            continue
        group = groups.setdefault(receipt.supplier, _Group(receipt.supplier, receipt.supplier))
        group.orders.add(receipt.receipt_id)
    return groups


def rank(spec: RuleSpec, groups: dict[object, _Group], top_k: int, ties_by_code: bool
         ) -> tuple[list[str], list[dict]]:
    """Names of the first ``top_k`` groups and the full ranking as evidence."""
    minimum = spec.dieu_kien.get("so_don_toi_thieu")
    if minimum is not None:
        groups = {k: g for k, g in groups.items() if len(g.orders) >= minimum}
    above = spec.dieu_kien.get("so_don_hon")
    if above is not None:
        groups = {k: g for k, g in groups.items() if len(g.orders) > above}
    measure = "so_don" if spec.chi_tieu == "so_phieu_nhap" else spec.chi_tieu
    rows = [(_measure(group, measure), group) for group in groups.values()]
    if not rows:
        raise RankError("no group meets the rule in the period")
    sign = -1 if spec.chon == "lon_nhat" else 1
    rows.sort(key=lambda row: (sign * row[0], (row[1].code or row[1].name).casefold()))
    if len(rows) > top_k and rows[top_k - 1][0] == rows[top_k][0]:
        if not ties_by_code or spec.doi_tuong == "ngay":
            raise RankError("tie at the cut and no tie rule applies")
    evidence = [{"name": g.name, "code": g.code, "value": str(value),
                 "orders": len(g.orders)} for value, g in rows]
    return [g.name for _, g in rows[:top_k]], evidence
