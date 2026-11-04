import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from datamate.contracts import BusinessContext, Candidate, Question
from datamate.ingestion.joined_sales import (
    JoinedSalesError,
    MappingSpec,
    NumericPolicy,
    RuleCapabilities,
    SourceMapping,
    load_joined_sales,
)
from datamate.ingestion.sales import load_sales
from datamate.semantics.planner import plan_question
from datamate.semantics.rules import read_rules
from datamate.semantics.text import fold


_JOIN_COLUMNS = {
    "orders": {
        "Hình thức thanh toán", "Tình trạng đơn", "Số phiếu", "Phải thu", "Ngày bán",
    },
    "details": {
        "Tiền bán", "TT dòng", "S.L", "Mã hàng bán", "Quy ra ĐV lẻ", "Số CT",
        "Giảm giá", "Giá 1 đv", "ĐV tính",
    },
    "products": {"Mã HH", "Tên sản phẩm", "Đơn vị tính", "Nhom hang"},
}

_JOIN_ANCHORS = {
    "orders": {"Số phiếu", "Tình trạng đơn"},
    "details": {"Số CT", "TT dòng", "Mã hàng bán"},
    "products": {"Mã HH", "Tên sản phẩm"},
}


def _rule_rows(rulebook: str) -> list[tuple[str, str, str]]:
    rows = []
    for line in rulebook.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            rows.append((fold(cells[0]), fold(cells[1]), line.strip()))
    return rows


def _required_rule(rows: list[tuple[str, str, str]], key: str,
                   *tokens: str) -> str:
    for row_key, value, original in rows:
        if row_key == key and all(token in value for token in tokens):
            return original
    raise JoinedSalesError(
        "RULE_CAPABILITY_MISSING",
        "Joined schema requires an explicit capability in the current split rulebook",
        {"rule_key": key, "required_tokens": list(tokens)},
    )


def _joined_rules(rulebook: str) -> RuleCapabilities:
    rows = _rule_rows(rulebook)
    validity = _required_rule(
        rows, "don tinh vao doanh thu", "khong huy", "khong tra", "ca don",
        "dang giao van tinh",
    )
    amount = _required_rule(
        rows, "doanh thu mot don", "thanh tien cac dong", "da tru giam gia",
    )
    ranking = _required_rule(rows, "ban chay", "so luong quy ve don vi co ban")
    product = _required_rule(
        rows, "mat hang", "phi phuc vu", "khong xep hang", "van tinh vao doanh thu",
    )
    duplicates = _required_rule(
        rows, "hai dong cua cung mot don giong nhau o moi o", "tinh mot lan",
    )
    statuses = _required_rule(
        rows, "trang thai don", "hoan thanh", "huy", "tra hang", "dang giao",
    )
    return RuleCapabilities(
        version="joined_rules.current_split.v1",
        status_mapping={
            "completed": "valid", "cancelled": "cancelled",
            "returned": "returned", "shipping": "valid",
        },
        whole_order_returns=True,
        quantity_base_authoritative=True,
        line_amount_after_discount=True,
        deduplicate_identical_detail_rows=True,
        service_fee_names=("Phí phục vụ",),
        service_fee_counts_as_revenue=True,
        service_fee_ranking_eligible=False,
        evidence=(validity, amount, ranking, product, duplicates, statuses),
    )


def _joined_mapping(catalog) -> MappingSpec | None:
    candidates = {
        role: [table for table in catalog.tables if required <= set(table.columns)]
        for role, required in _JOIN_COLUMNS.items()
    }
    detected = {
        role: [table for table in catalog.tables if anchors <= set(table.columns)]
        for role, anchors in _JOIN_ANCHORS.items()
    }
    if not any(detected.values()):
        return None
    if any(len(candidates[role]) != 1 for role in _JOIN_COLUMNS):
        raise JoinedSalesError(
            "AMBIGUOUS_JOINED_MAPPING",
            "Joined schema roles must each match exactly one source",
            {"candidate_counts": {role: len(values)
                                  for role, values in sorted(candidates.items())}},
        )
    orders, details, products = (
        candidates["orders"][0], candidates["details"][0], candidates["products"][0]
    )
    structural_evidence = (
        "orders source matched the verified order column signature",
        "details source matched the verified detail column signature",
        "products source matched the verified product column signature",
    )
    return MappingSpec(
        version="verified_odp_columns.v1",
        orders=SourceMapping(
            source=orders.path.name,
            fields={
                "order_id": "Số phiếu", "date": "Ngày bán",
                "status": "Tình trạng đơn", "total_due": "Phải thu",
                "payment_method": "Hình thức thanh toán",
            },
            key=("order_id",), grain="one row per order",
            evidence=(structural_evidence[0],),
        ),
        details=SourceMapping(
            source=details.path.name,
            fields={
                "order_id": "Số CT", "line_no": "TT dòng",
                "product_id": "Mã hàng bán", "amount": "Tiền bán",
                "quantity": "S.L", "quantity_base": "Quy ra ĐV lẻ",
                "unit": "ĐV tính", "unit_price": "Giá 1 đv", "discount": "Giảm giá",
            },
            key=("order_id", "line_no"), grain="one row per order line",
            evidence=(structural_evidence[1],),
        ),
        products=SourceMapping(
            source=products.path.name,
            fields={
                "product_id": "Mã HH", "product_name": "Tên sản phẩm",
                "base_unit": "Đơn vị tính", "category": "Nhom hang",
            },
            key=("product_id",), grain="one row per product",
            evidence=(structural_evidence[2],),
        ),
        numeric_policy=NumericPolicy(
            decimal_separator=",", thousands_separator=".",
            amount_suffix_multipliers={"tr": Decimal("1000000")},
            currency_suffixes=("đ",),
            amount_suffix_decimal_separator=",", amount_suffix_thousands_separator=".",
        ),
        evidence=structural_evidence,
    )


def _compact_joined_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in evidence.items() if key != "lineage"}


def _load_business_sales(context: BusinessContext):
    mapping = _joined_mapping(context.catalog)
    if mapping is None:
        sales, evidence = load_sales(context.catalog)
        return sales, {**evidence, "adapter": "flat_sales"}, None
    rules = _joined_rules(context.split.rulebook)
    sales, evidence = load_joined_sales(
        context.catalog, mapping=mapping, rule_capabilities=rules,
    )
    ranking_eligible = {
        item["sale_index"] for item in evidence["lineage"] if item["ranking_eligible"]
    }
    return sales, {**_compact_joined_evidence(evidence), "adapter": "joined_sales"}, \
        ranking_eligible


class AbstainAgent:
    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        return Candidate(reason="Explicit abstain backend")


class BaselineAgent:
    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        if "rules" not in context.cache:
            context.cache["rules"] = read_rules(context.split.rulebook)
        plan = plan_question(question, context.cache["rules"])
        if plan is None:
            return Candidate(reason="Question or split rules outside baseline grammar")
        if "sales" not in context.cache:
            # Cache adapter failures as well: do not rescan a bad workbook per question.
            try:
                context.cache["sales"] = _load_business_sales(context)
            except (ValueError, OSError) as exc:
                context.cache["sales"] = exc
        cached = context.cache["sales"]
        if isinstance(cached, Exception):
            return Candidate(reason=str(cached))
        sales, source, ranking_eligible = cached
        evidence = {**source, "intent": plan.intent, "start": plan.start.isoformat(),
                    "end": plan.end.isoformat(), "rules": plan.evidence}
        if not sales or plan.start > plan.end:
            return Candidate(reason="Empty data or future-only period", evidence=evidence)
        days = [s.day for s in sales]
        if plan.end < min(days) or plan.start > max(days):
            # Absence of sales alone does not establish the business's coverage.
            coverage = re.search(r"Khoảng dữ liệu:\s*(\d{4}-\d{2}-\d{2})\s+đến\s+"
                                 r"(\d{4}-\d{2}-\d{2})", context.catalog.readme)
            if coverage:
                first, last = map(date.fromisoformat, coverage.groups())
                if plan.end < first or plan.start > last:
                    evidence["coverage_source"] = coverage.group()
                    return Candidate(type="unanswerable", confidence=1.0,
                                     reason="Kỳ hỏi nằm hoàn toàn ngoài khoảng dữ liệu ghi trong README",
                                     evidence=evidence)
            return Candidate(reason="No sales in period; business coverage is not established",
                             evidence=evidence)
        selected = [(index, sale) for index, sale in enumerate(sales)
                    if plan.start <= sale.day <= plan.end and sale.status == "valid"]
        evidence["selected_rows"] = len(selected)
        if plan.intent == "revenue":
            value = sum((sale.amount for _, sale in selected), Decimal(0)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP)
            evidence["calculation"] = "sum(line_amount) on valid whole orders; round VND half up"
            return Candidate("number", int(value), 1.0, "", evidence)
        if plan.intent == "order_count":
            evidence["calculation"] = "count distinct order_id on valid whole orders"
            return Candidate("number", len({sale.order_id for _, sale in selected}),
                             1.0, "", evidence)
        ranked_rows = [(index, sale) for index, sale in selected
                       if ranking_eligible is None or index in ranking_eligible]
        evidence["ranking_rows"] = len(ranked_rows)
        if not ranked_rows or any(sale.quantity_base is None or not sale.product_id
                                  or not sale.product_name for _, sale in ranked_rows):
            return Candidate(reason="Ranking needs explicit base quantities and product ids/names",
                             evidence=evidence)
        quantities: dict[str, Decimal] = defaultdict(Decimal)
        names = {}
        for _, sale in ranked_rows:
            quantities[sale.product_id] += sale.quantity_base
            names.setdefault(sale.product_id, sale.product_name)
        ranked = sorted(quantities, key=lambda key: (-quantities[key], key))[:plan.top_k]
        answer = [names[key] for key in ranked]
        evidence["calculation"] = "sum(quantity_base) by product_id, descending; ties by id"
        return Candidate("string" if plan.top_k == 1 else "list",
                         answer[0] if plan.top_k == 1 else answer, 1.0, "", evidence)
