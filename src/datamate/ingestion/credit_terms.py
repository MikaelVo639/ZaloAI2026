"""README-backed due dates for credit sales, kept separate from payment events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from datamate.contracts import Catalog
from datamate.ingestion.readme_sales import _matching_tables, _records, parse_day, parse_sections
from datamate.semantics.text import fold


class CreditTermsError(ValueError):
    """A due-date source is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class CreditTerm:
    order_id: str
    due_date: date
    term_days: int | None
    source: str


def _field(meaning: str) -> str | None:
    text = fold(meaning).strip().removesuffix(".")
    if text in {"ma don", "ma don hang"}:
        return "order_id"
    if text == "han thanh toan":
        return "due_date"
    if text == "so ngay duoc no":
        return "term_days"
    return None


def _term_days(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise CreditTermsError("Credit term is not an integer number of days") from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise CreditTermsError("Credit term is not a non-negative integer")
    return int(number)


def load_credit_terms(catalog: Catalog) -> tuple[tuple[CreditTerm, ...], dict[str, Any]]:
    """Load a documented order/due-date list, rejecting missing or duplicate identities."""
    sources = []
    for section in parse_sections(catalog.readme):
        fields = {semantic: column for column, meaning in section.columns.items()
                  if (semantic := _field(meaning)) is not None}
        if fields.keys() >= {"order_id", "due_date"}:
            sources.append((section, fields))
        elif fields.keys() & {"due_date", "term_days"}:
            raise CreditTermsError(f"Incomplete credit-terms source: {section.label}")
    if not sources:
        raise CreditTermsError("README documents no complete credit-terms source")

    records: dict[str, CreditTerm] = {}
    labels: list[str] = []
    for section, fields in sources:
        tables = _matching_tables(catalog, section)
        if not tables:
            raise CreditTermsError(f"Credit-terms source is missing: {section.label}")
        labels.append(section.label)
        for table in tables:
            for ordinal, raw in _records(table, section.columns):
                values = {field: raw.get(column) for field, column in fields.items()}
                if all(value is None or (isinstance(value, str) and not value.strip())
                       for value in values.values()):
                    continue
                order_value, due_value = values.get("order_id"), values.get("due_date")
                if order_value is None or due_value is None:
                    raise CreditTermsError("Credit order id or due date is blank")
                order_id = str(order_value).strip()
                if not order_id:
                    raise CreditTermsError("Credit order id is blank")
                try:
                    due_date = parse_day(due_value, table.name)
                except (TypeError, ValueError) as exc:
                    raise CreditTermsError("Credit due date is invalid") from exc
                record = CreditTerm(order_id, due_date, _term_days(values.get("term_days")),
                                    f"{table.key}#{ordinal}")
                if order_id in records:
                    raise CreditTermsError("Credit-terms source repeats an order id")
                records[order_id] = record

    result = tuple(records[key] for key in sorted(records))
    return result, {"adapter_version": "readme_credit_terms.v1",
                    "sources": labels, "records": len(result)}
