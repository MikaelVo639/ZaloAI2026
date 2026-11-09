"""Read ingredient and prescription flags from a README documented drug catalog."""

import re
from dataclasses import dataclass

from datamate.contracts import Catalog
from datamate.ingestion.readme_sales import (
    _header_names,
    _matching_tables,
    _records,
    meaning_field,
    parse_sections,
)
from datamate.semantics.text import fold


@dataclass(frozen=True)
class Drug:
    ingredient: str
    prescription: bool


@dataclass(frozen=True)
class DrugCatalog:
    products: dict[str, Drug]
    source: str
    convention: str


def ingredient_key(value: str) -> str:
    # Preserve the component boundaries and order. A combination is one exact label.
    return " + ".join(fold(part) for part in value.split("+"))


def _prescription(value: object) -> bool:
    if isinstance(value, (bool, int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().casefold() in ("true", "false", "1", "0"):
        return value.strip().casefold() in ("true", "1")
    raise ValueError("Drug catalog has an invalid prescription flag")


def load_drugs(catalog: Catalog) -> DrugCatalog:
    lines = [line.strip() for line in catalog.readme.splitlines()
             if (label := re.match(r"^\s*-\s+\*\*([^*]+)\*\*\s*:", line))
             and fold(label[1]) == "thuoc"]
    if len(lines) != 1:
        raise ValueError("README does not define one drug catalog convention")
    convention = lines[0]
    text = fold(convention)
    if not all(token in text for token in ("dung true/1 la thuoc ke don",
                                           "hoat chat ghi khong kem ham luong",
                                           "nhieu chat noi bang")) or not re.search(
                                               r"""['"]\s*\+\s*['"]""", convention):
        raise ValueError("README does not define the prescription flag and ingredient format")

    options = []
    explicit = re.findall(r"`([^`.]+)\.([^`.]+)`", convention)
    if len(explicit) == 1:
        name, rx = explicit[0]
        for table in catalog.tables:
            if (table.name == name and table.path.suffix.lower() in (".sqlite", ".sqlite3", ".db")
                    and {"MaHangHoa", "ThanhPhanChinh", rx} <= set(table.columns)):
                options.append((table, "MaHangHoa", "ThanhPhanChinh", rx))
    elif not explicit:
        bold = re.findall(r"\*\*([^*]+)\*\*", convention)
        if len(bold) != 2:
            raise ValueError("README does not name the prescription column")
        rx = bold[1]
        for section in parse_sections(catalog.readme):
            code = [col for col, desc in section.columns.items() if meaning_field(desc) == "product_id"]
            ingredient = [col for col, desc in section.columns.items()
                          if fold(desc).startswith("hoat chat")]
            if rx in section.columns and len(code) == len(ingredient) == 1:
                for table in _matching_tables(catalog, section):
                    options.append((table, code[0], ingredient[0], rx))
        if not options and "phan mem ban hang" in fold(catalog.readme):
            # A sales-software export with the prescription column named by the
            # convention, and the full drug catalog header signature.
            for table in catalog.tables:
                if (table.path.name.startswith("BanHang_")
                        and f"`{table.path.name}`" in catalog.readme
                        and {"Mã HH", "Hoạt chất", rx} <= _header_names(table)):
                    options.append((table, "Mã HH", "Hoạt chất", rx))
    if len(options) != 1:
        raise ValueError("Drug catalog is missing or ambiguous")
    table, code, ingredient, rx = options[0]
    products = {}
    for _, row in _records(table, {code, ingredient, rx}):
        key = str(row[code]).strip() if row.get(code) is not None else ""
        if not key or key in products:
            raise ValueError("Drug catalog has a blank or repeated product code")
        raw = row.get(ingredient)
        if raw is not None and not isinstance(raw, str):
            raise ValueError("Drug catalog has a non-text ingredient")
        label = (raw or "").strip()
        if label and any(not fold(part) for part in label.split("+")):
            raise ValueError("Drug catalog has an empty ingredient component")
        products[key] = Drug(label, _prescription(row.get(rx)))
    if not products:
        raise ValueError("Drug catalog has no records")
    return DrugCatalog(products, table.key, convention)
