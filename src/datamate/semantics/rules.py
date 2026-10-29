from dataclasses import dataclass

from datamate.semantics.text import fold


@dataclass(frozen=True)
class Rules:
    revenue: bool
    month_to_date: bool
    monday_week: bool
    quantity_ranking: bool
    source_rows: tuple[str, ...]


def read_rules(rulebook: str) -> Rules:
    """Recognize a small, explicit subset; new rule wording requires an adapter.

    No cached training rulebook, identifier lookup or model assumption is used.
    Unrecognized revenue semantics disable the deterministic baseline.
    """
    rows = {}
    originals = {}
    for line in rulebook.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            rows[fold(cells[0])] = fold(cells[1])
            originals[fold(cells[0])] = line.strip()
    validity = rows.get("don tinh vao doanh thu", "")
    amount = rows.get("doanh thu mot don", "")
    month = rows.get("thang nay", "")
    week = rows.get("tuan", "")
    ranking = rows.get("ban chay", "")
    return Rules(
        revenue=all(token in validity for token in ("khong huy", "khong tra", "ca don"))
        and all(token in amount for token in (
            "thanh tien cac dong", "khong tru", "khong cong thue vat", "khong gom phi giao hang"
        )),
        month_to_date="ngay 1" in month and "hom nay" in month,
        monday_week="thu hai" in week and "chu nhat" in week,
        quantity_ranking="so luong quy ve don vi co ban" in ranking,
        source_rows=tuple(originals[key] for key in (
            "don tinh vao doanh thu", "doanh thu mot don", "thang nay", "tuan", "ban chay"
        ) if key in originals),
    )
