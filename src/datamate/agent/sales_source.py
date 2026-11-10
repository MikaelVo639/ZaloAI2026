"""Choose and run the sales adapter for one business (worker side).

Adapters are tried from the most to the least specific; the first schema that matches
is used and a schema that matches but fails its checks raises (no silent fallback past a
detected joined schema):

1. verified order/detail/product CSV signature -> joined adapter;
2. verified XLSX order/detail/product export with README evidence (DATA-02 mapping)
   -> joined adapter;
3. documented sales lines with an authoritative unit conversion or product catalogue
   -> README adapter;
4. flat sales table whose headers match the known aliases -> flat adapter;
5. other lists described column by column in the business README -> README adapter.

Joined and README capabilities must be stated by the rulebook of the split being answered.
"""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from datamate.contracts import Catalog
from datamate.ingestion.catalog import iter_rows
from datamate.ingestion.joined_sales import (
    JoinedSalesError,
    MappingSpec,
    NumericPolicy,
    RuleCapabilities,
    SourceMapping,
    load_joined_sales,
    mapping_for_xlsx_order_detail_product,
)
from datamate.ingestion.readme_sales import ReadmeSalesError, load_readme_sales, parse_sections
from datamate.ingestion.sales import Sale, load_sales
from datamate.ingestion.status import StatusError, status_policy
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


def _joined_mapping(catalog: Catalog) -> MappingSpec | None:
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


_XLSX_NOT_THIS_SCHEMA = {"SCHEMA_MISMATCH", "MAPPING_AMBIGUOUS", "MAPPING_EVIDENCE_MISSING"}


def _xlsx_mapping(catalog: Catalog) -> MappingSpec | None:
    """DATA-02 XLSX order/detail/product mapping, or None when the business is not that export.

    The mapping needs the three verified tables *and* README lines documenting them; a
    business without that evidence is left to the README adapter.
    """
    try:
        return mapping_for_xlsx_order_detail_product(catalog)
    except JoinedSalesError as exc:
        if exc.code in _XLSX_NOT_THIS_SCHEMA:
            return None
        raise


def _xlsx_rules(rulebook: str, catalog: Catalog, mapping: MappingSpec) -> RuleCapabilities:
    """Joined capabilities with the status labels of this export read through the split's
    four order states; an unknown label raises instead of being guessed."""
    rules = _joined_rules(rulebook)
    try:
        policy = status_policy(rulebook)
    except StatusError as exc:
        raise JoinedSalesError("RULE_CAPABILITY_MISSING", str(exc)) from exc
    table = next(t for t in catalog.tables
                 if f"{t.path.name}::{t.name}" == mapping.orders.source)
    column = mapping.orders.fields["status"]
    labels = {str(row[column]) for row in iter_rows(table) if row.get(column) not in (None, "")}
    mapping_by_label = {}
    for label in sorted(labels):
        try:
            mapping_by_label[label] = policy.resolve(label)
        except StatusError as exc:
            raise JoinedSalesError("UNKNOWN_STATUS", str(exc)) from exc
    return replace(
        rules, version="joined_rules.current_split.xlsx.v1",
        status_mapping=mapping_by_label, evidence=(*rules.evidence, *policy.evidence),
    )


def _joined_result(catalog: Catalog, mapping: MappingSpec, rules: RuleCapabilities
                   ) -> tuple[list[Sale], dict[str, Any], frozenset[int]]:
    sales, evidence = load_joined_sales(
        catalog, mapping=mapping, rule_capabilities=rules,
    )
    ranking_eligible = {
        item["sale_index"] for item in evidence["lineage"] if item["ranking_eligible"]
    }
    source = {
        **_compact_joined_evidence(evidence), "adapter": "joined_sales",
        # Same summary keys as the flat adapter, used by the calculation evidence.
        "tables": [item["table"] for item in evidence["sources"]],
        "sales_rows": evidence["counts"]["sales_rows"],
    }
    return sales, source, frozenset(ranking_eligible)


def _needs_documented_context(readme: str) -> bool:
    fields_by_section = [set(section.fields) for section in parse_sections(readme)]
    line_fields = {"order_id", "date", "status", "amount", "quantity", "unit"}
    product_fields = {"product_id", "product_name"}
    conversion_fields = {"big_unit", "factor", "base_unit"}
    catalogue_fields = {"product_id", "base_unit", "product_group"}
    lines = [fields for fields in fields_by_section
             if line_fields <= fields and fields & product_fields]
    conversions = any(conversion_fields <= fields and fields & product_fields
                      for fields in fields_by_section)
    products = any(catalogue_fields <= fields and fields & {"name", "product_name"}
                   and not fields & {"order_id", "quantity"} for fields in fields_by_section)
    return (conversions and any("quantity_base" not in fields for fields in lines)
            or products and any("quantity_base" in fields for fields in lines))


def load_business_sales(
    catalog: Catalog, rulebook: str,
) -> tuple[list[Sale], dict[str, Any], frozenset[int] | None]:
    """Load one business with the adapter its schema calls for.

    Returns ``(sales, source_evidence, ranking_eligible)``. ``ranking_eligible`` is the
    set of sale indices that may enter a product ranking, or ``None`` when every row
    may (flat schema). A partial or ambiguous joined schema raises instead of falling
    back to another adapter.
    """
    mapping = _joined_mapping(catalog)
    if mapping is not None:
        return _joined_result(catalog, mapping, _joined_rules(rulebook))
    mapping = _xlsx_mapping(catalog)
    if mapping is not None:
        sales, source, eligible = _joined_result(
            catalog, mapping, _xlsx_rules(rulebook, catalog, mapping))
        return sales, {**source, "adapter": "joined_sales_xlsx"}, eligible
    context_error = None
    if _needs_documented_context(catalog.readme):
        # The flat reader drops the companion conversion or product catalogue.
        # Use the documented source first and retain the flat fallback on a typed failure.
        try:
            sales, evidence, eligible = load_readme_sales(catalog, rulebook)
        except (ValueError, OSError) as exc:
            context_error = exc
        else:
            return sales, {**evidence, "adapter": "readme_sales"}, eligible
    try:
        sales, evidence = load_sales(catalog)
        return sales, {**evidence, "adapter": "flat_sales"}, None
    except ValueError as flat_error:
        if context_error is not None:
            raise ValueError(f"{flat_error}; README adapter: {context_error}") from context_error
        try:
            sales, evidence, eligible = load_readme_sales(catalog, rulebook)
        except ReadmeSalesError as readme_error:
            raise ValueError(f"{flat_error}; README adapter: {readme_error}") from readme_error
        return sales, {**evidence, "adapter": "readme_sales",
                       "flat_adapter_error": str(flat_error)}, eligible
