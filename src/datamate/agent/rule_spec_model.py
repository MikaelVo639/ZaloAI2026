"""Model-filled rule specs for ranking rows the deterministic reader leaves off.

Run once per split. For each named ranking row whose ``parse_row`` result is off, the model
fills the fixed JSON twice, from two prompts that differ in the order of the vocabularies
and examples. The spec is enabled only when both outputs parse, are identical, and pass
``ground`` (every value supported by the row's words). Everything is recorded for run.json.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from datamate.semantics.rule_spec import (
    CHI_TIEU,
    CHON,
    DOI_TUONG,
    DON_HOP_LE,
    KIEU_DAP_AN,
    RuleSpec,
    SpecResult,
    ground,
    normalize_rulebook,
    spec_from_json,
)

# Synthetic rows written for this prompt (not organizer text) with their specs.
EXAMPLES = (
    ("Khách chịu chi nhất", "giá trị trung bình một đơn cao nhất, chỉ xét khách có từ 4 đơn",
     {"doi_tuong": "khach", "chi_tieu": "gia_tri_tb_don", "chon": "lon_nhat",
      "dieu_kien": {"so_don_toi_thieu": 4}}),
    ("Nhân viên siêng nhất", "nhân viên có số ngày có đặt đơn nhiều nhất",
     {"doi_tuong": "nhan_vien", "chi_tieu": "so_ngay_co_mua", "chon": "lon_nhat",
      "dieu_kien": {}}),
    ("Ngày bán đắt nhất", "ngày có doanh thu cao nhất trong kỳ",
     {"doi_tuong": "ngay", "chi_tieu": "doanh_thu", "chon": "lon_nhat", "dieu_kien": {}}),
)
_FIELDS = ("ten", "cach_goi", "doi_tuong", "chi_tieu", "chon", "don_hop_le", "dieu_kien",
           "mau_so", "kieu_dap_an", "nguon")


def _example_json(name: str, definition: str, values: dict) -> dict:
    return {"ten": "", "cach_goi": [name.lower()], "don_hop_le": "luat_chung", "mau_so": "",
            "kieu_dap_an": "string", "nguon": "", **values}


def build_spec_messages(name: str, definition: str, variant: int) -> list[dict[str, str]]:
    vocab = [("doi_tuong", DOI_TUONG), ("chi_tieu", CHI_TIEU), ("chon", CHON),
             ("don_hop_le", DON_HOP_LE), ("kieu_dap_an", KIEU_DAP_AN)]
    examples = list(EXAMPLES)
    if variant:
        vocab, examples = vocab[::-1], examples[::-1]
    lines = [f"- {field}: one of {', '.join(values)}" for field, values in vocab]
    system = (
        "Convert ONE Vietnamese rulebook row into a JSON object with exactly these keys: "
        f"{', '.join(_FIELDS)}. Use only these values:\n" + "\n".join(lines) + "\n"
        "- dieu_kien: object; allowed keys so_don_toi_thieu (int, 'từ N đơn'), so_don_hon "
        "(int, 'trên N đơn'), tinh_ngay_khong_don (bool). Only numbers written in the row.\n"
        "- don_hop_le: luat_chung unless the row itself says which orders to drop.\n"
        "Copy nothing from other rows. If the row needs anything outside these values, "
        "output exactly: KHONG_RO")
    messages = [{"role": "system", "content": system}]
    for ex_name, ex_definition, values in examples:
        messages.append({"role": "user", "content": f"| {ex_name} | {ex_definition} |"})
        messages.append({"role": "assistant", "content": json.dumps(
            _example_json(ex_name, ex_definition, values), ensure_ascii=False)})
    messages.append({"role": "user", "content": f"| {name} | {definition} |"})
    return messages


def parse_spec_output(text: str, line: str) -> RuleSpec | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match or "KHONG_RO" in text:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["nguon"] = line
    return spec_from_json(data, line)


def model_specs(rulebook: str, generate: Callable[[list[dict[str, str]]], str | None]
                ) -> tuple[list[SpecResult], list[dict[str, Any]]]:
    """Deterministic results, with off ranking rows retried through ``generate``.

    ``generate(messages)`` returns the model text or ``None`` on failure. Returns the final
    results and the audit records (prompts' outputs and the decision) for run.json.
    """
    results, audit = [], []
    for result in normalize_rulebook(rulebook):
        if result.spec is not None:
            results.append(result)
            continue
        cells = [c.strip() for c in result.nguon.strip().strip("|").split("|")]
        outputs = [generate(build_spec_messages(cells[0], cells[1], variant))
                   for variant in (0, 1)]
        specs = [parse_spec_output(text or "", result.nguon) for text in outputs]
        record: dict[str, Any] = {"nguon": result.nguon, "outputs": outputs,
                                  "deterministic_reasons": list(result.reasons)}
        if None in specs or specs[0].to_json() != specs[1].to_json():
            record["decision"] = "off: outputs missing or different"
            results.append(result)
        else:
            problems = ground(specs[0])
            record["decision"] = "on" if not problems else f"off: {problems}"
            results.append(SpecResult(result.nguon, None if problems else specs[0],
                                      tuple(problems)))
        audit.append(record)
    return results, audit
