"""Structured descriptions of the CURRENT split's ranking rules (rule specs).

Done once per split. Each table row of the rulebook is split into its name and definition
and, when it defines a ranking ("<đối tượng> … nhất"), turned into a ``RuleSpec`` whose
fields only take values from fixed vocabularies. ``ground`` then checks that every value
has support in the words of the row itself (a number must be written in the row, a count
of days needs the word "ngày", and so on). A spec with any unsupported, ambiguous or
uncomputable part is disabled with its reasons; questions that need it abstain.

``parse_row`` is the deterministic reader. A model may fill the same JSON later
(docs/AGENT_02.md); its output goes through the same ``ground`` check and must agree across
two prompts. No definition is ever taken from another split.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from datamate.semantics.rules import canonical, read_rules
from datamate.semantics.text import fold

SPEC_VERSION = "rule_spec.v1"

DOI_TUONG = ("khach", "mat_hang", "ngay", "khung_gio", "nhan_vien", "nha_cung_cap", "don")
CHI_TIEU = ("so_don", "so_ngay_co_mua", "doanh_thu", "gia_tri_tb_don", "so_don_huy",
            "so_phieu_nhap", "so_luong_quy_doi")
CHON = ("lon_nhat", "nho_nhat", "tong", "ti_le")
KIEU_DAP_AN = ("number", "string", "list")
DON_HOP_LE = ("luat_chung", "bo_huy_tra", "bo_huy", "chi_huy", "moi_don")


@dataclass(frozen=True)
class RuleSpec:
    ten: str                                   # rule code, from the row name
    cach_goi: tuple[str, ...]                  # row name + "cùng nghĩa với …" phrases (folded)
    doi_tuong: str
    chi_tieu: str
    chon: str
    don_hop_le: str = "luat_chung"
    dieu_kien: dict = field(default_factory=dict)  # so_don_toi_thieu, tinh_ngay_khong_don, …
    mau_so: str = ""                           # only for chon == "ti_le"
    kieu_dap_an: str = "string"
    nguon: str = ""                            # the rulebook line, verbatim

    def to_json(self) -> dict:
        data = asdict(self)
        data["cach_goi"] = list(self.cach_goi)
        return data


@dataclass(frozen=True)
class SpecResult:
    """One rulebook row: its spec when enabled, else the reasons it is off."""
    nguon: str
    spec: RuleSpec | None
    reasons: tuple[str, ...] = ()


# ---------------------------------------------------------------- vocabulary

def _text(value: str) -> str:
    """Folded canonical words; section references ("§1", "(§5)") are dropped."""
    value = re.sub(r"\(?§\s*\d+\)?", " ", value)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", canonical(fold(value))).split())


_SUBJECTS = (  # (đối tượng, pattern) as named in a row name
    ("nha_cung_cap", r"\bnha cung cap\b|\bncc\b"),
    ("nhan_vien", r"\bnhan vien\b|\btho\b"),
    ("khung_gio", r"\bkhung gio\b|\bgio cao diem\b|\bgio\b"),
    ("khach", r"\bkhach\b"),
    ("mat_hang", r"\bmat hang\b|\bsan pham\b|\bmon\b"),
    ("ngay", r"\bngay\b"),
    ("don", r"\bdon\b"),
)
# The subject a definition opens with ("khách có …", "ngày có …", "người có …").
_LEAD = re.compile(r"^(?:la |nhung |cac )?(nha cung cap|ncc|nhan vien|tho|khung gio|gio|khach"
                   r"|mat hang|san pham|mon|ngay|nguoi|don)\b")
_LEAD_SUBJECT = {"nha cung cap": "nha_cung_cap", "ncc": "nha_cung_cap", "nhan vien": "nhan_vien",
                 "tho": "nhan_vien", "khung gio": "khung_gio", "gio": "khung_gio", "khach": "khach",
                 "mat hang": "mat_hang", "san pham": "mat_hang", "mon": "mat_hang",
                 "ngay": "ngay", "don": "don", "nguoi": ""}
_PERSONS = {"khach", "nhan_vien"}
_MEASURES = (  # (chỉ tiêu, pattern); more specific first; direction words are not part of them
    ("so_ngay_co_mua", r"\bso ngay khac nhau\b|\b(?:so )?ngay (?:khac nhau )?(?:co )?"
                       r"(?:mua|den|ghe|dat|lam viec|lam|ban|di lam)(?: hang| don)?"
                       r"(?: khac nhau)?\b"),
    ("gia_tri_tb_don", r"\b(?:gia tri )?(?:trung binh|binh quan) (?:mot |moi )?don\b"),
    ("so_don_huy", r"\bso don (?:bi )?huy\b|(?<=\bnhieu )don (?:bi )?huy\b"
                   r"|(?<=\bit )don (?:bi )?huy\b|\bhuy nhieu don\b"),
    ("so_phieu_nhap", r"\bso phieu nhap\b|(?<=\bnhieu )phieu nhap\b|(?<=\bit )phieu nhap\b"),
    ("so_luong_quy_doi", r"\bso luong quy (?:ve|doi)\b|\bso luong (?:da )?doi ra\b"),
    ("doanh_thu", r"\bdoanh thu\b"),
    ("so_don", r"\bso don\b|(?<=\bnhieu )don\b|(?<=\bit )don\b"),
)
_UP, _DOWN = r"\b(?:cao|nhieu|lon)\b", r"\b(?:thap|it|nho)\b"
# A row name "<…> nhất" with no direction in the definition means the most of its measure,
# unless the name says the opposite.
_NEGATIVE_NAME = re.compile(r"\b(?:it|thap|nho|kem|vang|e|cuoi|xau|lo)\b|\bcham\b(?! chi)")
# A clause "không theo / không xét theo / không dựa trên X": X is named only to exclude it.
_EXCLUDED = re.compile(r"^(?:va |nhung )?khong (?:xet |phai )?(?:theo|dua tren)\b")
_NOT_RANKING = re.compile(r"\bgan nhat\b|\bmoi nhat\b|\bsom nhat\b|\bmuon nhat\b")
_ONLY = r"(?:chi (?:xet|tinh|lay) (?:[a-z]+ ){0,3}?)?(?:co )?"
_MIN_ORDERS = re.compile(rf"\b{_ONLY}(?:tu|it nhat|toi thieu|co tu|co it nhat) (\d+) don"
                         r"(?: tro len)?\b")
_STRICT_ORDERS = re.compile(rf"\b{_ONLY}(?:tren|hon|qua) (\d+) don\b")
_EMPTY_DAYS_IN = re.compile(r"\b(?:ke|tinh) ca (?:ngay|gio|khung gio) khong (?:co )?don\b")
_EMPTY_DAYS_OUT = re.compile(r"\b(?:khong tinh|bo) (?:ngay|gio|khung gio) khong (?:co )?don\b")
_VALIDITY = (("chi_huy", r"\bchi (?:tinh |xet )?don huy\b"),
             ("moi_don", r"\bmoi (?:trang thai|don)\b"),
             ("bo_huy_tra", r"\bbo (?:don )?huy(?: va)? (?:don )?tra(?: hang)?\b"
                            r"|\bkhong huy(?: va)? khong tra(?: hang)?\b"),
             ("bo_huy", r"\bbo (?:don |phieu )?huy\b|\bkhong tinh (?:don |phieu )?huy\b"))
# Words that carry meaning in a definition. Any left after the reader took what it
# understood turns the rule off (a dropped "huỷ", "trả", "30 ngày", "lãi", "kể cả" …).
_MEANING = re.compile(
    r"\b(?:\d+|huy|tra|ngay|lai|loi|luot|lan|phieu|bao hanh|ke ca|khong|tru|chi|rieng|ngoai"
    r"|trung binh|binh quan|doanh thu|so luong|gio|thang|tuan|quy|nam|phan tram|ti le|gia"
    r"|tien|nhap|giao|no|doi|nhom|kenh|thanh toan|cod|chuyen khoan|tong|moi|cao|thap|nhieu"
    r"|it|lon|nho)\b")
_SAME = re.compile(r"\b(?:cung nghia|con goi la|goi la|tuc la)\b")


def table_rows(rulebook: str) -> list[tuple[str, str, str]]:
    """``(name, definition, line)`` of every data row of every rulebook table."""
    rows = []
    for line in rulebook.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.fullmatch(r"\|[\s:|-]+\|", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and cells[1]:
            rows.append((cells[0], cells[1], stripped))
    return rows


def _one(patterns: tuple[tuple[str, str], ...], text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in patterns if re.search(pattern, text))


def _names(name: str, definition: str) -> tuple[str, ...]:
    """The row name split on "," / "/" plus phrases written as "cùng nghĩa với **…**"."""
    names = [part for part in re.split(r"[,/]| hoặc ", name) if part.strip()]
    for sentence in re.split(r"[.;]\s", definition):
        if _SAME.search(_text(sentence)):  # "**A**, **B** cùng nghĩa với **C**"
            names += re.findall(r"\*\*([^*]+)\*\*", sentence)
    return tuple(dict.fromkeys(_text(item) for item in names if _text(item)))


def is_ranking_row(name: str) -> bool:
    """A named ranking: the row name ends with "nhất" (khách sộp nhất, giờ cao điểm nhất)
    or names a peak ("cao điểm"). Other rows are definitions this reader leaves alone."""
    text = _NOT_RANKING.sub(" ", _text(name))
    return bool(re.search(r"\bnhat\b|\bcao diem\b|\bchinh\b|\btrung thanh\b", text))


def _take(pattern: str | re.Pattern, text: str) -> tuple[re.Match | None, str]:
    """First match of ``pattern`` and the text with that match blanked out."""
    match = re.search(pattern, text)
    return match, (text if match is None else text[:match.start()] + " " + text[match.end():])


def _subject(key: str, clauses: list[str]) -> tuple[str | None, list[str], str]:
    """Đối tượng from the definition's opening subject; the name only arbitrates."""
    named = set(_one(_SUBJECTS, key)) - {"don"}
    if "khung_gio" in named:
        named.discard("ngay")
    lead = _LEAD.match(clauses[0]) if clauses else None
    if lead:
        clauses = [clauses[0][lead.end():], *clauses[1:]]
        subject = _LEAD_SUBJECT[lead.group(1)]
        if subject == "":  # "người có …": the name says which person
            people = named & _PERSONS
            if len(people) != 1:
                return None, clauses, f"definition says 'người' and the name gives {sorted(named)}"
            return people.pop(), clauses, ""
        if named and subject not in named:
            return None, clauses, f"name {sorted(named)} and definition '{lead.group(1)}' differ"
        return subject, clauses, ""
    if len(named) == 1:
        return next(iter(named)), clauses, ""
    return None, clauses, f"no subject in the definition and the name gives {sorted(named)}"


def parse_row(name: str, definition: str, line: str) -> SpecResult | None:
    """Deterministic spec of one row; ``None`` when the row is not a named ranking.

    Every part the reader understands is taken out of the definition; a meaning word left
    over (``_MEANING``) means part of the rule was not understood, so the rule is off.
    """
    if not is_ranking_row(name):
        return None
    key = _text(name)
    # Sentences giving other names ("**A** cùng nghĩa với B") are used by ``cach_goi``.
    meaning = " . ".join(s for s in re.split(r"[.;]\s", definition) if not _SAME.search(_text(s)))
    clauses = [c for c in (_text(part) for part in re.split(r"[,.;:()]", meaning)) if c]
    reasons: list[str] = []
    conditions: dict = {}
    subject, clauses, why = _subject(key, clauses)
    if why:
        reasons.append(why)
    rest_clauses, excluded = [], []
    for clause in clauses:
        (excluded if _EXCLUDED.match(clause) else rest_clauses).append(clause)
    rest = " ; ".join(rest_clauses)
    for bound, pattern in (("so_don_toi_thieu", _MIN_ORDERS), ("so_don_hon", _STRICT_ORDERS)):
        values = [int(m.group(1)) for m in pattern.finditer(rest)]
        rest = pattern.sub(" ", rest)
        if len(values) > 1:
            reasons.append(f"several values for {bound}")
        elif values:
            conditions[bound] = values[0]
    if {"so_don_toi_thieu", "so_don_hon"} <= set(conditions):
        reasons.append("both an inclusive and a strict order minimum")
    empty_in, rest = _take(_EMPTY_DAYS_IN, rest)
    empty_out, rest = _take(_EMPTY_DAYS_OUT, rest)
    if empty_in and empty_out:
        reasons.append("contradictory empty-day wording")
    elif empty_in or empty_out:
        conditions["tinh_ngay_khong_don"] = bool(empty_in)
    validity = "luat_chung"
    for value, pattern in _VALIDITY:
        match, rest = _take(pattern, rest)
        if match:
            validity = value
            break
    measures = []
    for value, pattern in _MEASURES:
        match, rest = _take(pattern, rest)
        if match:
            measures.append(value)
    if len(measures) != 1:
        reasons.append(f"chỉ tiêu: {measures or 'none'}")
    for clause in excluded:  # "không theo số đơn" must not name the chosen measure
        if measures and re.search(dict(_MEASURES)[measures[0]], clause):
            reasons.append(f"the definition excludes its own measure: {clause!r}")
    picks = set()
    if re.search(r"\bnhat\b", rest):
        picks = {"lon_nhat"} if re.search(_UP, rest) else set()
        picks |= {"nho_nhat"} if re.search(_DOWN, rest) else set()
        rest = re.sub(rf"{_UP}|{_DOWN}|\bnhat\b", " ", rest)
    if not picks and re.search(r"\bnhat\b|\bcao diem\b", key):
        picks = {"nho_nhat"} if _NEGATIVE_NAME.search(key) else {"lon_nhat"}
    if len(picks) != 1:
        reasons.append(f"chọn: {sorted(picks) or 'none'}")
    left = sorted(set(_MEANING.findall(rest)))
    if left:
        reasons.append(f"words not understood: {left}")
    if reasons:
        return SpecResult(line, None, tuple(reasons))
    spec = RuleSpec(
        ten=re.sub(r"\s+", "_", key), cach_goi=_names(name, definition),
        doi_tuong=subject, chi_tieu=measures[0], chon=picks.pop(), don_hop_le=validity,
        dieu_kien=conditions, kieu_dap_an="string", nguon=line)
    problems = ground(spec)
    return SpecResult(line, None if problems else spec, tuple(problems))


# ---------------------------------------------------------------- grounding

_SUPPORT = {  # value -> words one of which must be in the row
    "khach": ("khach",), "mat_hang": ("mat hang", "san pham", "mon", "hang"),
    "ngay": ("ngay",), "khung_gio": ("gio",), "nhan_vien": ("nhan vien", "tho"),
    "nha_cung_cap": ("nha cung cap", "ncc"), "don": ("don",),
    "so_don": ("don",), "so_ngay_co_mua": ("ngay",), "doanh_thu": ("doanh thu",),
    "gia_tri_tb_don": ("trung binh", "binh quan"), "so_don_huy": ("huy",),
    "so_phieu_nhap": ("phieu",), "so_luong_quy_doi": ("so luong",),
    "lon_nhat": ("nhat", "cao diem"), "nho_nhat": ("nhat",),
    "tong": ("tong",), "ti_le": ("ti le", "phan tram"),
    "bo_huy_tra": ("huy",), "bo_huy": ("huy",), "chi_huy": ("huy",), "moi_don": ("moi",),
}
# Computations the executor implements (agent/rule_rank.py); others stay off.
SUPPORTED = {
    "khach": {"so_don", "so_ngay_co_mua", "doanh_thu", "gia_tri_tb_don", "so_don_huy"},
    "mat_hang": {"so_don", "doanh_thu", "so_luong_quy_doi"},
    "ngay": {"so_don", "doanh_thu"},
    "khung_gio": set(),  # no answer format for an hour slot in the rulebook yet
    "nhan_vien": {"so_don", "doanh_thu", "so_ngay_co_mua"},
    "nha_cung_cap": {"so_phieu_nhap"},
    "don": set(),
}


def _words(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def ground(spec: RuleSpec) -> list[str]:
    """Every problem with ``spec``: values outside the vocabularies, values without
    support in the row's words, numbers not written in the row, computations not built."""
    problems = []
    for name, value, allowed in (("doi_tuong", spec.doi_tuong, DOI_TUONG),
                                 ("chi_tieu", spec.chi_tieu, CHI_TIEU),
                                 ("chon", spec.chon, CHON),
                                 ("don_hop_le", spec.don_hop_le, DON_HOP_LE),
                                 ("kieu_dap_an", spec.kieu_dap_an, KIEU_DAP_AN)):
        if value not in allowed:
            problems.append(f"{name}={value!r} outside the vocabulary")
    cells = [cell.strip() for cell in spec.nguon.strip().strip("|").split("|")]
    text = _text(" ".join(cells))
    if not spec.nguon or len(cells) < 2:
        problems.append("nguon is not a rulebook row")
    for value in (spec.doi_tuong, spec.chi_tieu, spec.chon,
                  *(() if spec.don_hop_le == "luat_chung" else (spec.don_hop_le,))):
        if value in _SUPPORT and not any(_words(text, word) for word in _SUPPORT[value]):
            problems.append(f"{value!r} has no support in the row")
    numbers = set(re.findall(r"\d+", text))
    for name, value in spec.dieu_kien.items():
        if name in ("so_don_toi_thieu", "so_don_hon"):
            if (not isinstance(value, int) or isinstance(value, bool)
                    or str(value) not in numbers or not _words(text, "don")):
                problems.append(f"{name}={value!r} is not written in the row")
        elif name == "tinh_ngay_khong_don":
            if not isinstance(value, bool) or "khong" not in text.split():
                problems.append("tinh_ngay_khong_don has no support in the row")
        else:
            problems.append(f"unknown condition {name!r}")
    used = {str(v) for k, v in spec.dieu_kien.items() if k in ("so_don_toi_thieu", "so_don_hon")}
    if numbers - used:
        problems.append(f"numbers {sorted(numbers - used)} in the row are not understood")
    if spec.chon == "ti_le" and not spec.mau_so:
        problems.append("ti_le without its denominator")
    if spec.chon in ("tong", "ti_le"):
        problems.append(f"chon={spec.chon!r} is not computed yet")
    if spec.chi_tieu not in SUPPORTED.get(spec.doi_tuong, set()):
        problems.append(f"{spec.chi_tieu} per {spec.doi_tuong} is not computed yet")
    if (spec.doi_tuong in ("ngay", "khung_gio") and spec.chon == "nho_nhat"
            and "tinh_ngay_khong_don" not in spec.dieu_kien):
        problems.append("nho_nhat over days needs the row to say whether empty days count")
    if not spec.cach_goi:
        problems.append("no name to recognise in questions")
    # Only the definition must be fully used; the name is how questions call the rule.
    definition = " ".join(s for s in re.split(r"[.;]\s", cells[1] if len(cells) >= 2 else "")
                          if not _SAME.search(_text(s)))
    unexplained = sorted(set(_CONTENT.findall(_text(definition))) - _explained(spec))
    if unexplained:
        problems.append(f"words of the row no field explains: {unexplained}")
    return problems


# Content words of a definition and the spec fields that can explain them. Structural words
# ("không", "kể cả", "chỉ", directions) are checked by parse_row, which sees the clauses.
_CONTENT = re.compile(
    r"\b(?:huy|tra|ngay|lai|loi|luot|lan|phieu|bao hanh|tru|rieng|ngoai|trung binh"
    r"|binh quan|doanh thu|so luong|gio|thang|tuan|quy|nam|phan tram|ti le|gia|tien|nhap"
    r"|giao|no|doi|nhom|kenh|thanh toan|cod|chuyen khoan|tong)\b")


def _explained(spec: RuleSpec) -> set[str]:
    words = set()
    if spec.chi_tieu == "so_don_huy" or spec.don_hop_le in ("bo_huy", "bo_huy_tra", "chi_huy"):
        words.add("huy")
    if spec.don_hop_le == "bo_huy_tra":
        words.add("tra")
    if (spec.doi_tuong == "ngay" or spec.chi_tieu == "so_ngay_co_mua"
            or "tinh_ngay_khong_don" in spec.dieu_kien):
        words.add("ngay")
    if spec.chi_tieu == "so_phieu_nhap" or spec.doi_tuong == "nha_cung_cap":
        words |= {"phieu", "nhap"}
    if spec.chi_tieu == "doanh_thu":
        words.add("doanh thu")
    if spec.chi_tieu == "gia_tri_tb_don":
        words |= {"trung binh", "binh quan", "gia"}
    if spec.chi_tieu == "so_luong_quy_doi":
        words.add("so luong")
    if spec.doi_tuong == "khung_gio":
        words.add("gio")
    return words


def spec_from_json(data: dict, line: str) -> RuleSpec | None:
    """A model-filled spec; ``None`` unless it has exactly the fixed fields."""
    expected = set(RuleSpec.__dataclass_fields__)
    if not isinstance(data, dict) or set(data) != expected:
        return None
    try:
        spec = RuleSpec(**{**data, "cach_goi": tuple(data["cach_goi"]), "nguon": line})
    except TypeError:
        return None
    return spec


def handled_rows(rulebook: str) -> set[str]:
    """Rows the hand-written reader (rules.read_rules) already recognises; never re-read."""
    rules = read_rules(rulebook)
    return ({line.strip() for _, line in rules.rule_rows}
            | {line.strip() for line in rules.source_rows})


def normalize_rulebook(rulebook: str) -> list[SpecResult]:
    """Specs of every named ranking row of the split not already handled by read_rules,
    enabled or off with reasons."""
    results = []
    handled = handled_rows(rulebook)
    for name, definition, line in table_rows(rulebook):
        if line.strip() in handled:
            continue
        result = parse_row(name, definition, line)
        if result is not None:
            results.append(result)
    return results


def enabled_specs(rulebook: str) -> tuple[RuleSpec, ...]:
    return tuple(r.spec for r in normalize_rulebook(rulebook) if r.spec is not None)


def report(rulebook: str) -> str:
    """JSON of every spec (enabled or off) for run.json and review."""
    return json.dumps({"version": SPEC_VERSION, "rules": [
        {"nguon": r.nguon, "enabled": r.spec is not None,
         "spec": r.spec.to_json() if r.spec else None, "reasons": list(r.reasons)}
        for r in normalize_rulebook(rulebook)]}, ensure_ascii=False, indent=2)
