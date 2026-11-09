"""Grounded order/detail/product adapter with join and arithmetic evidence."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import iter_rows
from datamate.ingestion.inventory import _sha256
from datamate.ingestion.readme_sales import joined_customer_sources
from datamate.ingestion.sales import Sale, parse_date, parse_minute
from datamate.semantics.text import fold

_ADAPTER_VERSION = "joined_sales.v1"


class JoinedSalesError(ValueError):
    """Typed ingestion failure whose diagnostics are safe to share."""

    def __init__(self, code: str, message: str, diagnostics: Mapping[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.diagnostics = {"code": code, **dict(diagnostics or {})}


@dataclass(frozen=True)
class SourceMapping:
    source: str
    fields: Mapping[str, str]
    key: tuple[str, ...]
    grain: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class NumericPolicy:
    decimal_separator: str = ","
    thousands_separator: str = "."
    amount_suffix_multipliers: Mapping[str, Decimal] = field(default_factory=dict)
    currency_suffixes: tuple[str, ...] = ()
    amount_decimal_separator: str | None = None
    amount_thousands_separator: str | None = None
    amount_suffix_decimal_separator: str | None = None
    amount_suffix_thousands_separator: str | None = None


@dataclass(frozen=True)
class MappingSpec:
    version: str
    orders: SourceMapping
    details: SourceMapping
    products: SourceMapping
    numeric_policy: NumericPolicy
    evidence: tuple[str, ...]
    integral_line_key: bool = False


@dataclass(frozen=True)
class RuleCapabilities:
    version: str
    status_mapping: Mapping[str, str]
    whole_order_returns: bool
    quantity_base_authoritative: bool
    line_amount_after_discount: bool
    deduplicate_identical_detail_rows: bool
    service_fee_names: tuple[str, ...]
    service_fee_counts_as_revenue: bool
    service_fee_ranking_eligible: bool
    evidence: tuple[str, ...]


def _fail(code: str, message: str, **diagnostics: Any) -> None:
    raise JoinedSalesError(code, message, diagnostics)


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _identifier(value: Any, *, field_name: str, source: str,
                row_ordinal: int, allow_integral_numeric: bool = False) -> str:
    if _missing(value):
        _fail(
            "MISSING_KEY", "Required identifier is missing",
            source=source, field=field_name, row_ordinal=row_ordinal,
        )
    if allow_integral_numeric and not isinstance(value, bool) \
            and isinstance(value, (int, float, Decimal)):
        numeric = Decimal(str(value))
        if numeric.is_finite() and numeric == numeric.to_integral_value():
            return format(numeric, ".0f")
    if not isinstance(value, str):
        _fail(
            "IDENTIFIER_POLICY_REQUIRED", "Numeric identifiers require an explicit policy",
            source=source, field=field_name, row_ordinal=row_ordinal,
        )
    return value


def _parse_localized_decimal(text: str, decimal_separator: str,
                             thousands_separator: str) -> Decimal:
    if (not decimal_separator or not thousands_separator
            or decimal_separator == thousands_separator):
        raise ValueError("Invalid numeric policy")
    sign = ""
    if text[:1] in {"+", "-"}:
        sign, text = text[0], text[1:]
    if not text:
        raise ValueError("Missing digits")
    if text.count(decimal_separator) > 1:
        raise ValueError("Repeated decimal separator")
    if decimal_separator in text:
        integer, fraction = text.rsplit(decimal_separator, 1)
        if not fraction.isdigit() or thousands_separator in fraction:
            raise ValueError("Invalid decimal fraction")
    else:
        integer, fraction = text, None
    groups = integer.split(thousands_separator)
    if len(groups) > 1:
        if not (groups[0].isdigit() and 1 <= len(groups[0]) <= 3
                and all(group.isdigit() and len(group) == 3 for group in groups[1:])):
            raise ValueError("Invalid thousands grouping")
        integer = "".join(groups)
    elif not integer.isdigit():
        raise ValueError("Invalid integer digits")
    normalized = sign + integer
    if fraction is not None:
        normalized += "." + fraction
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("Non-finite decimal")
    return result


def _parse_decimal(value: Any, policy: NumericPolicy, *, allow_amount_suffix: bool,
                   allow_currency_suffix: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing or boolean numeric value")
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Non-finite decimal")
        return result
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise ValueError("Empty numeric value")
    matched_currency = None
    folded = text.casefold()
    for suffix in sorted(policy.currency_suffixes, key=len, reverse=True):
        if folded.endswith(suffix.casefold()):
            matched_currency = suffix
            break
    if matched_currency is not None:
        if not allow_currency_suffix:
            raise ValueError("Currency suffix is not permitted for this field")
        text = text[:-len(matched_currency)]
        folded = text.casefold()
    multiplier = Decimal(1)
    matched_suffix = None
    for suffix in sorted(policy.amount_suffix_multipliers, key=len, reverse=True):
        if folded.endswith(suffix.casefold()):
            matched_suffix = suffix
            break
    if matched_suffix is not None:
        if not allow_amount_suffix:
            raise ValueError("Numeric suffix is only permitted for line amount")
        text = text[:-len(matched_suffix)]
        multiplier = policy.amount_suffix_multipliers[matched_suffix]
        decimal_separator = (
            policy.amount_suffix_decimal_separator or policy.decimal_separator
        )
        thousands_separator = (
            policy.amount_suffix_thousands_separator or policy.thousands_separator
        )
    elif re.search(r"[^0-9+\-.,]", text):
        raise ValueError("Unsupported numeric suffix")
    else:
        decimal_separator = (
            policy.amount_decimal_separator or policy.decimal_separator
            if allow_amount_suffix else policy.decimal_separator
        )
        thousands_separator = (
            policy.amount_thousands_separator or policy.thousands_separator
            if allow_amount_suffix else policy.thousands_separator
        )
    return _parse_localized_decimal(text, decimal_separator, thousands_separator) * multiplier


def _select_table(catalog: Catalog, source_mapping: SourceMapping,
                  required_fields: set[str]) -> Table:
    missing_fields = sorted(required_fields - set(source_mapping.fields))
    if missing_fields:
        _fail(
            "MAPPING_INCOMPLETE", "Mapping is missing required semantic fields",
            source=source_mapping.source, missing_fields=missing_fields,
        )
    if any(key not in source_mapping.fields for key in source_mapping.key):
        _fail(
            "MAPPING_INCOMPLETE", "Mapping key references an unmapped semantic field",
            source=source_mapping.source,
        )
    normalized_source = source_mapping.source.replace("\\", "/").lstrip("/")
    source_path, separator, source_table = normalized_source.rpartition("::")
    if not separator:
        source_path, source_table = normalized_source, ""
    matches = []
    for table in catalog.tables:
        path = table.path.as_posix()
        path_matches = path == source_path or path.endswith("/" + source_path)
        if path_matches and (not source_table or table.name == source_table):
            matches.append(table)
    if len(matches) != 1:
        _fail(
            "SOURCE_SELECTION_ERROR", "Declared source must match exactly one catalog table",
            source=source_mapping.source, match_count=len(matches),
        )
    table = matches[0]
    required_columns = set(source_mapping.fields.values())
    missing_columns = sorted(required_columns - set(table.columns))
    if missing_columns:
        _fail(
            "SCHEMA_MISMATCH", "Declared source is missing mapped columns",
            source=source_mapping.source, missing_columns=missing_columns,
        )
    return table


def _readme_evidence(catalog: Catalog, *tokens: str) -> str:
    for line in catalog.readme.splitlines():
        normalized = fold(line)
        if all(token in normalized for token in tokens):
            return line.strip()
    _fail(
        "MAPPING_EVIDENCE_MISSING",
        "The current business README does not document a required schema meaning",
        required_tokens=list(tokens),
    )


def _single_structural_table(catalog: Catalog, role: str,
                             required_columns: set[str]) -> Table:
    matches = [
        table for table in catalog.tables
        if table.path.suffix.lower() == ".xlsx" and required_columns <= set(table.columns)
    ]
    if len(matches) != 1:
        _fail(
            "MAPPING_AMBIGUOUS" if matches else "SCHEMA_MISMATCH",
            "Verified XLSX schema roles must each match exactly one table",
            role=role, match_count=len(matches),
        )
    return matches[0]


def mapping_for_xlsx_order_detail_product(catalog: Catalog) -> MappingSpec:
    """Build the reviewed XLSX O/D/P mapping from structure and local README evidence.

    This is intentionally one schema policy, not a general workbook guesser. The caller
    still supplies capabilities from the current split's rulebook to ``load_joined_sales``.
    """
    orders = _single_structural_table(catalog, "orders", {
        "Mã hóa đơn", "Thời gian", "Tổng tiền hàng", "Trạng thái",
    })
    details = _single_structural_table(catalog, "details", {
        "Mã hóa đơn", "Mã hàng", "Tên hàng", "Số lượng", "Đơn giá", "Giảm giá",
        "Thành tiền", "Số lượng quy đổi", "Đơn vị tính", "STT",
    })
    products = _single_structural_table(catalog, "products", {
        "Mã hàng", "Tên hàng", "Nhóm hàng(3 Cấp)", "ĐVT",
    })
    order_evidence = _readme_evidence(catalog, "ma hoa don", "ma don")
    amount_evidence = _readme_evidence(
        catalog, "thanh tien", "thanh tien sau giam",
    )
    line_key_evidence = _readme_evidence(catalog, "stt", "so thu tu dong")
    quantity_evidence = _readme_evidence(
        catalog, "so luong quy doi", "don vi co ban",
    )
    product_evidence = _readme_evidence(catalog, "dvt", "don vi co ban")
    # Optional: order time for the rulebook shifts, only when the README documents it.
    time_evidence = next((line.strip() for line in catalog.readme.splitlines()
                          if "thoi gian tao" in fold(line) and "thoi diem dat don" in fold(line)),
                         None)
    order_fields = {"order_id": "Mã hóa đơn", "date": "Thời gian",
                    "status": "Trạng thái", "total_due": "Tổng tiền hàng"}
    if time_evidence and "Thời gian tạo" in orders.columns:
        order_fields["time"] = "Thời gian tạo"
    staff_evidence = next((line.strip() for line in catalog.readme.splitlines()
                           if "nguoi ban" in fold(line) and "ma nhan vien ban" in fold(line)), None)
    if staff_evidence and "Người bán" in orders.columns:
        order_fields["staff_id"] = "Người bán"

    def source(table: Table) -> str:
        return f"{table.path.name}::{table.name}"

    mapping_evidence = (
        order_evidence, amount_evidence, line_key_evidence, quantity_evidence,
        product_evidence,
        "money fields use schema-scoped comma thousands and dot decimals",
    )
    return MappingSpec(
        version="xlsx_order_detail_product.v1",
        orders=SourceMapping(
            source=source(orders),
            fields=order_fields,
            key=("order_id",), grain="one row per invoice",
            evidence=(order_evidence,) + ((time_evidence,) if "time" in order_fields else ())
            + ((staff_evidence,) if "staff_id" in order_fields else ()),
        ),
        details=SourceMapping(
            source=source(details),
            fields={
                "order_id": "Mã hóa đơn", "line_no": "STT",
                "product_id": "Mã hàng", "amount": "Thành tiền",
                "quantity": "Số lượng", "quantity_base": "Số lượng quy đổi",
                "unit": "Đơn vị tính", "unit_price": "Đơn giá",
                "discount": "Giảm giá",
            },
            key=("order_id", "line_no"), grain="one row per invoice line",
            evidence=(amount_evidence, line_key_evidence, quantity_evidence),
        ),
        products=SourceMapping(
            source=source(products),
            fields={
                "product_id": "Mã hàng", "product_name": "Tên hàng",
                "base_unit": "ĐVT", "category": "Nhóm hàng(3 Cấp)",
            },
            key=("product_id",), grain="one row per product",
            evidence=(product_evidence,),
        ),
        numeric_policy=NumericPolicy(decimal_separator=".", thousands_separator=","),
        evidence=mapping_evidence,
        integral_line_key=True,
    )


def _mapping_payload(mapping: MappingSpec) -> dict[str, Any]:
    def source_payload(value: SourceMapping) -> dict[str, Any]:
        return {
            "source": value.source,
            "fields": dict(sorted(value.fields.items())),
            "key": list(value.key),
            "grain": value.grain,
            "evidence": list(value.evidence),
        }

    return {
        "version": mapping.version,
        "orders": source_payload(mapping.orders),
        "details": source_payload(mapping.details),
        "products": source_payload(mapping.products),
        "numeric_policy": {
            "decimal_separator": mapping.numeric_policy.decimal_separator,
            "thousands_separator": mapping.numeric_policy.thousands_separator,
            "amount_suffix_multipliers": {
                key: str(value)
                for key, value in sorted(mapping.numeric_policy.amount_suffix_multipliers.items())
            },
            "currency_suffixes": list(mapping.numeric_policy.currency_suffixes),
            "amount_suffix_decimal_separator": (
                mapping.numeric_policy.amount_suffix_decimal_separator
            ),
            "amount_suffix_thousands_separator": (
                mapping.numeric_policy.amount_suffix_thousands_separator
            ),
            "amount_decimal_separator": mapping.numeric_policy.amount_decimal_separator,
            "amount_thousands_separator": mapping.numeric_policy.amount_thousands_separator,
        },
        "integral_line_key": mapping.integral_line_key,
        "evidence": list(mapping.evidence),
    }


def _rules_payload(rules: RuleCapabilities) -> dict[str, Any]:
    return {
        "version": rules.version,
        "status_mapping": dict(sorted(rules.status_mapping.items())),
        "whole_order_returns": rules.whole_order_returns,
        "quantity_base_authoritative": rules.quantity_base_authoritative,
        "line_amount_after_discount": rules.line_amount_after_discount,
        "deduplicate_identical_detail_rows": rules.deduplicate_identical_detail_rows,
        "service_fee_names": list(rules.service_fee_names),
        "service_fee_counts_as_revenue": rules.service_fee_counts_as_revenue,
        "service_fee_ranking_eligible": rules.service_fee_ranking_eligible,
        "evidence": list(rules.evidence),
    }


def _validate_capabilities(mapping: MappingSpec, rules: RuleCapabilities) -> None:
    if not mapping.evidence or not rules.evidence:
        _fail("EVIDENCE_REQUIRED", "Mapping and rule capabilities require source citations")
    if not rules.quantity_base_authoritative:
        _fail("UNIT_POLICY_REQUIRED", "Adapter requires an authoritative quantity_base field")
    if not rules.line_amount_after_discount:
        _fail("AMOUNT_POLICY_REQUIRED", "Adapter requires line amount after line discount")
    allowed = {"valid", "cancelled", "returned"}
    invalid = sorted(set(rules.status_mapping.values()) - allowed)
    if invalid:
        _fail("RULE_CONFLICT", "Status mapping contains unsupported canonical states",
              invalid_states=invalid)
    normalized = [fold(value) for value in rules.status_mapping]
    if len(set(normalized)) != len(normalized):
        _fail("RULE_CONFLICT", "Status mapping is ambiguous after normalization")


def _status_lookup(rules: RuleCapabilities) -> dict[str, str]:
    return {fold(raw): canonical for raw, canonical in rules.status_mapping.items()}


def _row_fingerprint(row: Mapping[str, Any], columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{type(row.get(column)).__name__}:{row.get(column)!r}" for column in columns)


def load_joined_sales(catalog: Catalog, *, mapping: MappingSpec,
                      rule_capabilities: RuleCapabilities) -> tuple[list[Sale], dict[str, Any]]:
    """Load one declared O/D/P schema without changing the shared flat adapter."""
    _validate_capabilities(mapping, rule_capabilities)
    orders_table = _select_table(
        catalog, mapping.orders,
        {"order_id", "date", "status", "total_due"},
    )
    details_table = _select_table(
        catalog, mapping.details,
        {"order_id", "line_no", "product_id", "amount", "quantity",
         "quantity_base", "unit", "unit_price", "discount"},
    )
    products_table = _select_table(
        catalog, mapping.products,
        {"product_id", "product_name", "base_unit", "category"},
    )
    selected = [orders_table, details_table, products_table]
    before_hashes = {table.path: _sha256(table.path) for table in selected}
    status_lookup = _status_lookup(rule_capabilities)
    diagnostics: list[dict[str, Any]] = []
    customer_fields, customers, customer_error = joined_customer_sources(catalog, orders_table)
    if customer_error:
        diagnostics.append({"code": customer_error, "severity": "warning", "role": "customers"})

    orders: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    header_total_parse_failures = 0
    for row_ordinal, raw in enumerate(iter_rows(orders_table), 1):
        order_id = _identifier(
            raw.get(mapping.orders.fields["order_id"]), field_name="order_id",
            source=mapping.orders.source, row_ordinal=row_ordinal,
        )
        if order_id in orders:
            _fail(
                "DUPLICATE_PARENT_KEY", "Order parent key is duplicated",
                source=mapping.orders.source, key_fields=list(mapping.orders.key),
                duplicate_count=1,
            )
        raw_status = raw.get(mapping.orders.fields["status"])
        canonical_status = status_lookup.get(fold(str(raw_status))) if not _missing(raw_status) else None
        if canonical_status is None:
            _fail(
                "UNKNOWN_STATUS", "Order status is not covered by split rule capabilities",
                source=mapping.orders.source, row_ordinal=row_ordinal,
            )
        if canonical_status == "returned" and not rule_capabilities.whole_order_returns:
            _fail(
                "RULE_CAPABILITY_MISSING", "Returned order requires whole-order return semantics",
                source=mapping.orders.source, row_ordinal=row_ordinal,
            )
        try:
            day = parse_date(raw.get(mapping.orders.fields["date"]), orders_table.name)
        except (TypeError, ValueError) as exc:
            raise JoinedSalesError(
                "DATE_INVALID", "Order date could not be parsed",
                {"source": mapping.orders.source, "row_ordinal": row_ordinal},
            ) from exc
        total_due = None
        try:
            total_due = _parse_decimal(
                raw.get(mapping.orders.fields["total_due"]), mapping.numeric_policy,
                allow_amount_suffix=False, allow_currency_suffix=True,
            )
        except ValueError:
            header_total_parse_failures += 1
        customer = {key: str(raw[column]).strip() for key, column in customer_fields.items()
                    if not _missing(raw.get(column))}
        customer_id = customer.get("customer_id")
        customer_name = None
        customer_group = None
        if customer_id or "customer_id" not in customer_fields:
            listed = customers.get(customer_id or "", {})
            customer_name = customer.get("customer_name") or listed.get("name")
            order_group = customer.get("customer_group")
            listed_group = listed.get("group")
            if not (order_group and listed_group and fold(order_group) != fold(listed_group)):
                customer_group = order_group or listed_group
        orders[order_id] = {
            "day": day,
            "minute": (parse_minute(raw.get(mapping.orders.fields["time"]))
                       if "time" in mapping.orders.fields else None),
            "staff_id": (None if "staff_id" not in mapping.orders.fields
                         or _missing(raw.get(mapping.orders.fields["staff_id"]))
                         else str(raw.get(mapping.orders.fields["staff_id"])).strip()),
            "status": canonical_status,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_group": customer_group,
            "total_due": total_due,
            "row_ordinal": row_ordinal,
            "payment_method": (
                raw.get(mapping.orders.fields["payment_method"])
                if "payment_method" in mapping.orders.fields else None
            ),
        }
        status_counts[canonical_status] += 1

    products: dict[str, dict[str, Any]] = {}
    for row_ordinal, raw in enumerate(iter_rows(products_table), 1):
        product_id = _identifier(
            raw.get(mapping.products.fields["product_id"]), field_name="product_id",
            source=mapping.products.source, row_ordinal=row_ordinal,
        )
        if product_id in products:
            _fail(
                "DUPLICATE_PARENT_KEY", "Product parent key is duplicated",
                source=mapping.products.source, key_fields=list(mapping.products.key),
                duplicate_count=1,
            )
        products[product_id] = {
            "name": raw.get(mapping.products.fields["product_name"]),
            "base_unit": raw.get(mapping.products.fields["base_unit"]),
            "category": raw.get(mapping.products.fields["category"]),
            "row_ordinal": row_ordinal,
        }

    raw_detail_rows = 0
    duplicate_detail_rows = 0
    missing_detail_keys = 0
    orphan_order_rows = 0
    orphan_product_rows = 0
    seen_line_keys: dict[tuple[str, str], tuple[str, ...]] = {}
    skipped_ordinals: set[int] = set()
    detail_order_ids: set[str] = set()
    for row_ordinal, raw in enumerate(iter_rows(details_table), 1):
        raw_detail_rows += 1
        try:
            order_id = _identifier(
                raw.get(mapping.details.fields["order_id"]), field_name="order_id",
                source=mapping.details.source, row_ordinal=row_ordinal,
            )
            line_no = _identifier(
                raw.get(mapping.details.fields["line_no"]), field_name="line_no",
                source=mapping.details.source, row_ordinal=row_ordinal,
                allow_integral_numeric=mapping.integral_line_key,
            )
            product_id = _identifier(
                raw.get(mapping.details.fields["product_id"]), field_name="product_id",
                source=mapping.details.source, row_ordinal=row_ordinal,
            )
        except JoinedSalesError:
            missing_detail_keys += 1
            continue
        line_key = (order_id, line_no)
        fingerprint = _row_fingerprint(raw, details_table.columns)
        if line_key in seen_line_keys:
            if (rule_capabilities.deduplicate_identical_detail_rows
                    and seen_line_keys[line_key] == fingerprint):
                duplicate_detail_rows += 1
                skipped_ordinals.add(row_ordinal)
                continue
            _fail(
                "DUPLICATE_DETAIL_KEY", "Compound detail grain key is duplicated",
                source=mapping.details.source,
                key_fields=list(mapping.details.key), duplicate_count=1,
            )
        seen_line_keys[line_key] = fingerprint
        detail_order_ids.add(order_id)
        if order_id not in orders:
            orphan_order_rows += 1
        if product_id not in products:
            orphan_product_rows += 1
    if missing_detail_keys:
        _fail(
            "MISSING_KEY", "Detail rows contain missing grain or join keys",
            source=mapping.details.source, missing_key_rows=missing_detail_keys,
        )
    if orphan_order_rows or orphan_product_rows:
        _fail(
            "ORPHAN_FK", "Detail rows contain orphan references",
            source=mapping.details.source, orphan_order_rows=orphan_order_rows,
            orphan_product_rows=orphan_product_rows,
        )

    sales: list[Sale] = []
    lineage: list[dict[str, Any]] = []
    amount_parsed = 0
    amount_rejected = 0
    numeric_rejected = 0
    formula_matches = 0
    formula_mismatches = 0
    service_fee_rows = 0
    line_amounts_by_order: dict[str, Decimal] = {}
    service_names = {fold(value) for value in rule_capabilities.service_fee_names}
    for row_ordinal, raw in enumerate(iter_rows(details_table), 1):
        if row_ordinal in skipped_ordinals:
            continue
        order_id = str(raw[mapping.details.fields["order_id"]])
        line_no = str(raw[mapping.details.fields["line_no"]])
        product_id = str(raw[mapping.details.fields["product_id"]])
        try:
            amount = _parse_decimal(
                raw.get(mapping.details.fields["amount"]), mapping.numeric_policy,
                allow_amount_suffix=True, allow_currency_suffix=True,
            )
            amount_parsed += 1
        except ValueError:
            amount_rejected += 1
            continue
        try:
            quantity = _parse_decimal(
                raw.get(mapping.details.fields["quantity"]), mapping.numeric_policy,
                allow_amount_suffix=False,
            )
            quantity_base = _parse_decimal(
                raw.get(mapping.details.fields["quantity_base"]), mapping.numeric_policy,
                allow_amount_suffix=False,
            )
            unit_price = _parse_decimal(
                raw.get(mapping.details.fields["unit_price"]), mapping.numeric_policy,
                allow_amount_suffix=False,
            )
            discount = _parse_decimal(
                raw.get(mapping.details.fields["discount"]), mapping.numeric_policy,
                allow_amount_suffix=False,
            )
        except ValueError:
            numeric_rejected += 1
            continue
        expected_amount = quantity * unit_price - discount
        if amount == expected_amount:
            formula_matches += 1
        else:
            formula_mismatches += 1
        order = orders[order_id]
        product = products[product_id]
        product_name = None if _missing(product["name"]) else str(product["name"])
        is_service_fee = product_name is not None and fold(product_name) in service_names
        if is_service_fee:
            service_fee_rows += 1
        sale_index = len(sales)
        sales.append(Sale(
            order_id=order_id,
            day=order["day"],
            status=order["status"],
            amount=amount,
            product_id=product_id,
            product_name=product_name,
            quantity_base=quantity_base,
            minute=order["minute"],
            staff_id=order["staff_id"],
            customer_id=order["customer_id"],
            customer_name=order["customer_name"],
            customer_group=order["customer_group"],
        ))
        line_amounts_by_order[order_id] = line_amounts_by_order.get(order_id, Decimal(0)) + amount
        lineage.append({
            "sale_index": sale_index,
            "line_key": [order_id, line_no],
            "detail": {"source": mapping.details.source, "row_ordinal": row_ordinal},
            "order": {"source": mapping.orders.source,
                      "row_ordinal": order["row_ordinal"]},
            "product": {"source": mapping.products.source,
                        "row_ordinal": product["row_ordinal"]},
            "status": order["status"],
            "is_service_fee": is_service_fee,
            "counts_as_revenue": (
                rule_capabilities.service_fee_counts_as_revenue if is_service_fee else True
            ),
            "ranking_eligible": (
                rule_capabilities.service_fee_ranking_eligible if is_service_fee else True
            ),
            "quantity_base_authoritative": True,
            "base_unit": product["base_unit"],
            "category": product["category"],
        })
    if amount_rejected or numeric_rejected:
        _fail(
            "NUMERIC_AMBIGUOUS", "One or more required detail numbers could not be parsed",
            source=mapping.details.source, amount_parsed=amount_parsed,
            amount_rejected=amount_rejected, other_numeric_rejected=numeric_rejected,
        )
    if formula_mismatches:
        diagnostics.append({
            "code": "AMOUNT_RECONCILIATION_MISMATCH",
            "severity": "warning",
            "count": formula_mismatches,
            "message": "Line amount differs from quantity times unit price minus discount",
        })

    comparable_orders = 0
    header_total_differences = 0
    for order_id, line_total in line_amounts_by_order.items():
        total_due = orders[order_id]["total_due"]
        if total_due is None:
            continue
        comparable_orders += 1
        if total_due != line_total:
            header_total_differences += 1
    joined_rows = len(sales)
    selected_detail_rows = raw_detail_rows - duplicate_detail_rows
    join_expansion = joined_rows - selected_detail_rows
    if join_expansion != 0:
        _fail(
            "JOIN_EXPANSION", "Joined row count differs from selected detail grain",
            selected_detail_rows=selected_detail_rows, joined_rows=joined_rows,
            join_expansion=join_expansion,
        )
    after_hashes = {table.path: _sha256(table.path) for table in selected}
    if before_hashes != after_hashes:
        _fail("CHECKSUM_CHANGED", "A source changed while the adapter was reading it")

    sources = []
    for table, source_mapping in zip(
            selected, (mapping.orders, mapping.details, mapping.products)):
        sources.append({
            "source": source_mapping.source,
            "table": table.name,
            "sha256": before_hashes[table.path],
        })
    evidence = {
        "adapter_version": _ADAPTER_VERSION,
        "mapping": _mapping_payload(mapping),
        "rules": _rules_payload(rule_capabilities),
        "sources": sources,
        "counts": {
            "order_rows": len(orders),
            "detail_rows_raw": raw_detail_rows,
            "detail_rows_selected": selected_detail_rows,
            "product_rows": len(products),
            "sales_rows": len(sales),
            "duplicates_removed": duplicate_detail_rows,
            "orders_without_details": len(set(orders) - detail_order_ids),
        },
        "join": {
            "orphan_order_rows": 0,
            "orphan_product_rows": 0,
            "duplicate_order_keys": 0,
            "duplicate_product_keys": 0,
            "join_expansion": join_expansion,
            "detail_to_order_cardinality": "many-to-one",
            "detail_to_product_cardinality": "many-to-one",
        },
        "parsing": {
            "amount_parsed": amount_parsed,
            "amount_rejected": amount_rejected,
            "other_numeric_rejected": numeric_rejected,
            "header_total_parse_failures": header_total_parse_failures,
        },
        "amount_reconciliation": {
            "formula": "quantity * unit_price - discount",
            "matches": formula_matches,
            "mismatches": formula_mismatches,
            "header_total_comparable_orders": comparable_orders,
            "header_total_differs_from_line_sum": header_total_differences,
            "header_total_is_not_line_revenue": True,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "service_fee": {
            "rows": service_fee_rows,
            "counts_as_revenue": rule_capabilities.service_fee_counts_as_revenue,
            "ranking_eligible": rule_capabilities.service_fee_ranking_eligible,
        },
        "lineage": lineage,
        "diagnostics": diagnostics,
    }
    if customer_fields:
        evidence["customer_order_columns"] = customer_fields
    return sales, evidence
