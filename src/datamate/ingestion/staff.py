"""Reviewed staff directories used to resolve a displayed name to a seller code."""

from __future__ import annotations

from dataclasses import dataclass

from datamate.contracts import Catalog, Table
from datamate.ingestion.catalog import iter_rows
from datamate.ingestion.readme_sales import _matching_tables, parse_sections
from datamate.semantics.text import fold


class StaffSourceError(ValueError):
    """The staff directory exists but cannot be joined without guessing."""


@dataclass(frozen=True)
class StaffDirectory:
    names_by_code: dict[str, str]
    codes_by_unique_name: dict[str, str]
    ambiguous_names: tuple[str, ...]
    source: str


def _pos_staff_table(catalog: Catalog) -> Table | None:
    text = fold(catalog.readme)
    if not ("pos" in text.split() or "phan mem ban hang" in text):
        return None
    matches = [table for table in catalog.tables
               if {"MaNhanVien", "TenGoi"} <= set(table.columns)]
    if len(matches) > 1:
        raise StaffSourceError("Several POS tables look like staff directories")
    return matches[0] if matches else None


def load_staff_directory(catalog: Catalog) -> StaffDirectory:
    """Load a structurally reviewed POS staff list.

    Codes remain the primary identity.  A name is exposed as an alias only when it names
    exactly one code; repeated names are reported but deliberately omitted from the alias map.
    """
    table = _pos_staff_table(catalog)
    if table is None:
        return StaffDirectory({}, {}, (), "")

    names_by_code: dict[str, str] = {}
    codes_by_name: dict[str, set[str]] = {}
    for row in iter_rows(table):
        code_value, name_value = row.get("MaNhanVien"), row.get("TenGoi")
        if code_value is None or name_value is None:
            raise StaffSourceError("Staff code or name is blank")
        code, name = str(code_value).strip(), str(name_value).strip()
        if not code or not name:
            raise StaffSourceError("Staff code or name is blank")
        previous = names_by_code.get(code)
        if previous is not None and previous != name:
            raise StaffSourceError("One staff code has conflicting names")
        names_by_code[code] = name
        codes_by_name.setdefault(fold(name), set()).add(code)

    ambiguous = tuple(sorted(name for name, codes in codes_by_name.items() if len(codes) > 1))
    unique = {name: next(iter(codes)) for name, codes in codes_by_name.items()
              if len(codes) == 1}
    return StaffDirectory(names_by_code, unique, ambiguous, table.key)


def load_repair_staff(catalog: Catalog) -> StaffDirectory:
    """Map names in a repair ledger to the documented roster where they are unique."""
    sections = parse_sections(catalog.readme)
    if not any("tho lam phieu ghi ten goi" in fold(meaning)
               for section in sections for meaning in section.columns.values()):
        return StaffDirectory({}, {}, (), "")
    sources = [(column, table) for section in sections
               for column, meaning in section.columns.items() if fold(meaning) == "ho ten tho"
               for table in _matching_tables(catalog, section)]
    if len(sources) != 1:
        raise StaffSourceError("Repair ledger requires one documented staff roster")
    column, table = sources[0]
    names = [str(row[column]).strip() for row in iter_rows(table) if row.get(column)]
    if not names or len({fold(name) for name in names}) != len(names):
        raise StaffSourceError("Repair roster does not uniquely identify each mechanic")
    aliases: dict[str, set[str]] = {}
    for name in names:
        full = fold(name)
        given = full.split()[-1]
        for alias in {full, given, f"a {given}", f"anh {given}"}:
            aliases.setdefault(alias, set()).add(name)
    unique = {alias: next(iter(owners)) for alias, owners in aliases.items() if len(owners) == 1}
    ambiguous = tuple(sorted(alias for alias, owners in aliases.items() if len(owners) > 1))
    return StaffDirectory({name: name for name in names}, unique, ambiguous, table.key)
