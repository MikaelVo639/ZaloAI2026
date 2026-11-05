"""Small deterministic schema descriptions for bounded Agent/model context."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from datamate.contracts import Catalog, Table
from datamate.ingestion.joined_sales import MappingSpec, SourceMapping


def _source(table: Table) -> str:
    return f"{table.path.name}::{table.name}"


def _matches(table: Table, source_mapping: SourceMapping) -> bool:
    path, separator, sheet = source_mapping.source.replace("\\", "/").rpartition("::")
    if not separator:
        path, sheet = source_mapping.source, ""
    return table.path.name == path and (not sheet or sheet == "*" or table.name == sheet)


def _physical_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (float, Decimal)):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    return "text"


def _redacted_sample(value: Any) -> str:
    kind = _physical_type(value)
    if kind == "null":
        return "<null>"
    if kind == "text":
        return f"<text:length={len(str(value))}>"
    return f"<{kind}>"


def _column_descriptor(table: Table, column: str,
                       max_sample_values: int) -> dict[str, Any]:
    observed = [row.get(column) for row in table.sample]
    types = sorted({_physical_type(value) for value in observed if value is not None})
    samples = []
    for value in observed:
        redacted = _redacted_sample(value)
        if redacted not in samples:
            samples.append(redacted)
        if len(samples) == max_sample_values:
            break
    return {
        "name": column,
        "physical_types_observed": types,
        "observed_nullable": None if not observed else any(
            value is None or (isinstance(value, str) and not value.strip())
            for value in observed
        ),
        "sample_shapes": samples,
        "observation_scope": "catalog_sample",
    }


def _role_payload(role: str, source_mapping: SourceMapping) -> dict[str, Any]:
    units = {}
    if "quantity_base" in source_mapping.fields:
        units["quantity_base"] = "product base unit"
    for field in ("amount", "total_due", "unit_price", "discount"):
        if field in source_mapping.fields:
            units[field] = "source currency"
    return {
        "role": role,
        "grain": source_mapping.grain,
        "key": list(source_mapping.key),
        "semantic_fields": dict(sorted(source_mapping.fields.items())),
        "units": units,
    }


def describe_catalog(
    catalog: Catalog,
    *,
    mapping: MappingSpec | None = None,
    diagnostics: Iterable[str] = (),
    max_sample_values: int = 2,
) -> dict[str, Any]:
    """Return JSON-safe schema metadata without checkout paths or raw cell values."""
    if not 0 <= max_sample_values <= 5:
        raise ValueError("max_sample_values must be between 0 and 5")
    roles = [] if mapping is None else [
        ("orders", mapping.orders),
        ("details", mapping.details),
        ("products", mapping.products),
    ]
    tables = []
    for table in sorted(catalog.tables, key=lambda item: (item.path.name, item.name)):
        table_roles = [
            _role_payload(role, source_mapping)
            for role, source_mapping in roles if _matches(table, source_mapping)
        ]
        tables.append({
            "source": _source(table),
            "sheet_or_table": table.name,
            "roles": table_roles,
            "columns": [
                _column_descriptor(table, column, max_sample_values)
                for column in table.columns
            ],
        })
    joins = []
    if mapping is not None:
        joins = [
            {
                "from": "details.order_id", "to": "orders.order_id",
                "cardinality": "many-to-one", "validated_by": "joined adapter",
            },
            {
                "from": "details.product_id", "to": "products.product_id",
                "cardinality": "many-to-one", "validated_by": "joined adapter",
            },
        ]
    limitations = [
        "physical types and nullability describe the bounded catalog sample only",
        "sample values are redacted to type and text length",
    ]
    if mapping is None:
        limitations.append("grain, keys and joins are undeclared")
    return {
        "schema_version": "schema_descriptor.v1",
        "mapping_version": mapping.version if mapping is not None else None,
        "tables": tables,
        "joins": joins,
        "diagnostics": sorted(set(diagnostics)),
        "limitations": limitations,
    }
