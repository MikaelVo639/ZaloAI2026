"""Experimental model rewrite: restate a question the grammar cannot plan, then plan it.

The model never plans, computes or answers. It only restates an everyday question in the
standard wording of ``EXAMPLES``. The deterministic grammar then plans the restatement under
the current split's rulebook, and the existing executor computes the answer. A rewrite is
used only when it keeps every number, catalogue name and meaning-bearing word of the
question, and when every independent rewrite leads to the same Plan.

``EXAMPLES`` are written by the team for this module. They are not train, public or private
questions; each standard wording is checked against the grammar by
``scripts/check_rewrite_examples.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import Any

from datamate.agent import code_answer
from datamate.agent.baseline import _execution_failure
from datamate.agent.code_answer import (
    CODE_EXAMPLES,
    CODE_MAX_NEW_TOKENS,
    CODE_REPAIRS,
    CODE_TIMEOUT_SECONDS,
    build_code_messages,
    extract_code,
    implausible,
    normalize_result,
    repair_messages,
)
from datamate.agent.local_plan import LocalPlanAgent
from datamate.agent.rule_spec_model import model_specs
from datamate.contracts import BusinessContext, Candidate, Plan, Question
from datamate.semantics.planner import unread_words
from datamate.semantics.rule_spec import enabled_specs
from datamate.semantics.text import fold, normalize_question

REWRITE_CONFIDENCE = 0.5  # fixed diagnostic score, not a calibrated probability
UNSURE = "KHONG_RO"
MAX_REWRITE_CHARACTERS = 300

# (everyday wording, standard wording). Synthetic: invented names, codes, dates and amounts.
EXAMPLES: tuple[tuple[str, str], ...] = (
    ("dthu th2/2026 dc bn vay", "Doanh thu tháng 2/2026 là bao nhiêu?"),
    ("hnay quán bán đc nhiêu tiền rồi ta", "Doanh thu hôm nay là bao nhiêu?"),
    ("cho e hoi tuan truoc tiem thu ve tong bao nhieu", "Doanh thu tuần trước là bao nhiêu?"),
    ("quý 4/2025 shop kiếm được bao nhiêu doanh thu thế", "Doanh thu quý 4/2025 là bao nhiêu?"),
    ("tu 01/02/2026 toi 10/02/2026 ban dc bao nhieu tien",
     "Doanh thu từ ngày 01/02/2026 đến ngày 10/02/2026 là bao nhiêu?"),
    ("Bánh quy bơ tháng 2/2026 thu về nhiêu tiền nhỉ",
     "Doanh thu tháng 2/2026 của Bánh quy bơ là bao nhiêu?"),
    ("nv mã NV007 th2/2026 bán đc tổng bn tiền",
     "Nhân viên mã NV007 tháng 2/2026 bán được tổng cộng bao nhiêu tiền?"),
    ("dthu th2/2026 so vs th1/2026 tang giam bnhiu %",
     "Doanh thu tháng 2/2026 thay đổi bao nhiêu % so với tháng 1/2026?"),
    ("thang nay ban so voi thang truoc thay doi may phan tram",
     "Doanh thu tháng này thay đổi bao nhiêu % so với tháng trước?"),
    ("tết năm nay so vs tết năm trc doanh thu thay đổi mấy %",
     "Doanh thu dịp Tết năm nay thay đổi bao nhiêu % so với dịp Tết năm ngoái?"),
    ("th2/2026 quán chốt đc mấy đơn", "Tháng 2/2026 cửa hàng có bao nhiêu đơn hàng?"),
    ("hom nay co may don roi", "Hôm nay cửa hàng có bao nhiêu đơn?"),
    ("bình quân mỗi đơn th2/2026 được bao nhiêu tiền",
     "Giá trị trung bình một đơn tháng 2/2026 là bao nhiêu?"),
    ("thang 2/2026 bi huy bao nhieu don vay", "Số đơn bị huỷ trong tháng 2/2026 là bao nhiêu?"),
    ("th2/2026 đơn huỷ chiếm mấy phần trăm", "Tỉ lệ huỷ đơn tháng 2/2026 là bao nhiêu %?"),
    ("thang 2/2026 hom nao ban dc nhieu tien nhat",
     "Ngày nào trong tháng 2/2026 có doanh thu cao nhất?"),
    ("top 3 món bán chạy th2/2026 là gì",
     "3 mặt hàng bán chạy nhất tháng 2/2026 là những mặt hàng nào?"),
    ("thang 2/2026 mon nao ban dat nhat", "Mặt hàng nào bán chạy nhất tháng 2/2026?"),
    ("5 khach chi nhieu tien nhat th2/2026 la ai",
     "5 khách mua nhiều tiền nhất tháng 2/2026 là ai?"),
    ("th2/2026 có bao nhiêu đơn từ 500k trở lên",
     "Tháng 2/2026 có bao nhiêu đơn có doanh thu từ 500.000 đồng trở lên?"),
    ("Kẹo gừng hộp 10 gói th2/2026 bán ra mấy hộp",
     "Tháng 2/2026 Kẹo gừng hộp 10 gói bán được tổng cộng bao nhiêu hộp?"),
    ("gia nhap tb 1 hop Keo gung hop 10 goi trong th2/2026",
     "Tháng 2/2026 giá nhập bình quân một hộp Kẹo gừng hộp 10 gói là bao nhiêu?"),
    ("ngày 05/02/2026 bán Kẹo gừng hộp 10 gói giá mấy 1 hộp",
     "Giá bán một hộp Kẹo gừng hộp 10 gói ngày 05/02/2026 là bao nhiêu?"),
    ("th2/2026 nhap hang het tong bnhiu tien",
     "Tháng 2/2026 tổng giá trị các phiếu nhập hàng là bao nhiêu?"),
    ("xem giup th2/2026 lay hang cua ncc nao nhieu tien nhat",
     "Tháng 2/2026 cửa hàng nhập nhiều tiền hàng nhất từ nhà cung cấp nào?"),
    ("kho con bao nhieu goi Keo gung hop 10 goi cuoi ngay hnay",
     "Tồn kho Kẹo gừng hộp 10 gói cuối ngày hôm nay là bao nhiêu gói?"),
    ("hết hôm nay Kẹo gừng hộp 10 gói còn mấy hộp nguyên với mấy gói lẻ",
     "Cuối ngày hôm nay Kẹo gừng hộp 10 gói còn mấy hộp chẵn, mấy gói lẻ?"),
    ("den het 10/03/2026 khach no tong cong bao nhieu",
     "Tính đến hết ngày 10/03/2026, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?"),
    ("tới hết ngày 10/03/2026 còn mấy người nợ tiền quán",
     "Tính đến hết ngày 10/03/2026, có bao nhiêu khách còn nợ cửa hàng?"),
    ("th2/2026 thu no dc bao nhieu", "Tháng 2/2026 cửa hàng thu được bao nhiêu tiền khách trả nợ?"),
    ("th2/2026 kh Lê Thị Mai nợ thêm nhiêu, trả đc nhiêu",
     "Tháng 2/2026 khách Lê Thị Mai ghi nợ thêm bao nhiêu và trả nợ được bao nhiêu? Ghi hai số"),
    ("đến hết 10/03/2026 top 3 người nợ nhiều nhất, mỗi người nợ bao nhiêu",
     "Tính đến hết ngày 10/03/2026, 3 khách nào đang nợ nhiều nhất, mỗi người còn nợ bao nhiêu?"),
    ("th2/2026 quan loi gop dc bnhiu", "Tháng 2/2026 lãi gộp của cửa hàng là bao nhiêu?"),
    ("thang 2/2026 ban mon nao loi nhat", "Hàng nào có tổng lãi gộp cao nhất tháng 2/2026?"),
    ("trừ hết chi phí th2/2026 còn lời ròng bao nhiêu", "Lợi nhuận ròng tháng 2/2026 là bao nhiêu?"),
    ("thang 2/2026 nv nao ban gioi nhat", "Nhân viên bán giỏi nhất tháng 2/2026 là ai?"),
    ("th2/2026 chở hàng cho khách mấy chuyến",
     "Tháng 2/2026 cửa hàng giao bao nhiêu đợt hàng cho khách?"),
    ("tu 01/02/2026 den het 07/02/2026 may mon khong ai mua",
     "Từ ngày 01/02/2026 đến hết ngày 07/02/2026, có mấy mặt hàng ế hẳn, không ai mua cái nào?"),
    ("dthu 3 thang 11/2025 12/2025 1/2026 co tang hoai ko",
     "Doanh thu có tăng liên tục qua 3 tháng 11/2025, 12/2025, 1/2026 không?"),
    ("dthu th12/2025 voi th1/2026 moi thang bao nhieu",
     "Doanh thu tháng 12/2025 và tháng 1/2026 lần lượt là bao nhiêu?"),
    # Groups, customer groups, shifts, thresholds, best period (invented group names).
    ("dthu nhom Do an vat th2/2026 dc bn",
     "Doanh thu nhóm Đồ ăn vặt tháng 2/2026 là bao nhiêu?"),
    ("th2/2026 nhóm đồ ăn vặt chiếm mấy % dthu",
     "Doanh thu nhóm hàng Đồ ăn vặt chiếm bao nhiêu phần trăm tổng doanh thu tháng 2/2026?"),
    ("khach nhom dai ly th2/2026 mua het bao nhieu tien",
     "Doanh thu của khách nhóm Đại lý tháng 2/2026 là bao nhiêu?"),
    ("khách nhóm đại lý th2/2026 bình quân mỗi đơn mua bn",
     "Tháng 2/2026 khách nhóm Đại lý mua trung bình bao nhiêu tiền hàng mỗi đơn?"),
    ("khach nhom dai ly chiem may % dthu th2/2026",
     "Khách nhóm Đại lý chiếm bao nhiêu phần trăm doanh thu tháng 2/2026?"),
    ("trong 3 thang 11/2025 12/2025 1/2026 thang nao ban dc nhieu tien nhat",
     "Tháng nào trong 3 tháng 11/2025, 12/2025, 1/2026 có doanh thu cao nhất?"),
    ("th2/2026 bao nhieu khach mua tu 3 don tro len",
     "Tháng 2/2026 có bao nhiêu khách mua từ 3 đơn trở lên?"),
    ("th2/2026 mấy ngày bán đc từ 5tr trở lên",
     "Tháng 2/2026 có bao nhiêu ngày doanh thu từ 5.000.000 đồng trở lên?"),
    ("th2/2026 có mấy món nằm trong từ 50 đơn khác nhau trở lên",
     "Tháng 2/2026 có bao nhiêu mặt hàng có mặt ở từ 50 đơn hàng khác nhau trở lên?"),
    ("ca chieu th2/2026 ban dc bn tien", "Doanh thu ca chiều tháng 2/2026 là bao nhiêu?"),
    ("top 5 khach dat nhieu don nhat th2/2026", "5 khách mua nhiều đơn nhất tháng 2/2026 là ai?"),
    # Inventory, expiry and debt snapshots.
    ("cuối ngày 15/03/2026 tiền hàng nằm trong kho tính theo giá vốn bn",
     "Tiền hàng nằm trong kho vào cuối ngày 15/03/2026 theo giá vốn là bao nhiêu?"),
    ("hết ngày 12/03/2026 có mấy mặt hàng đang hết hàng vậy",
     "Cuối ngày 12/03/2026, cửa hàng có bao nhiêu mặt hàng đang hết hàng?"),
    ("theo tồn kho ngày 15/03/2026 lô Trà mây tía gần hết hạn nhất có hạn dùng ngày nào",
     "Theo tồn kho ngày 15/03/2026, lô Trà mây tía gần hết hạn nhất có hạn dùng là ngày nào?"),
    ("theo tồn kho ngày 15/03/2026 có mấy lô còn tồn sẽ hết hạn từ nay đến hết ngày 04/04/2026",
     "Theo tồn kho ngày 15/03/2026, có bao nhiêu lô hàng còn tồn sẽ hết hạn từ nay đến hết ngày 04/04/2026?"),
    ("còn mấy mặt hàng có lô tồn sắp hết hạn trong 20 ngày tới vậy",
     "Có bao nhiêu mặt hàng còn lô tồn sắp hết hạn trong 20 ngày tới?"),
    ("sp nào còn hàng hạn dùng từ 15/03/2026 tới hết 04/04/2026",
     "Sản phẩm nào đang còn hàng có hạn dùng từ ngày 15/03/2026 đến hết ngày 04/04/2026?"),
    ("tiền vốn nằm ở các lô còn tồn hạn dùng từ 15/03/2026 tới hết 04/04/2026 bn",
     "Tiền vốn đang nằm ở các lô còn tồn có hạn dùng từ ngày 15/03/2026 đến hết ngày 04/04/2026 là bao nhiêu?"),
    ("hết ngày 12/03/2026 khách còn nợ bao nhiêu chia tuổi nợ 0–7 ngày, 8–15 ngày và trên 15 ngày",
     "Tính đến hết ngày 12/03/2026, khách còn nợ bao nhiêu theo nhóm tuổi nợ 0–7 ngày, 8–15 ngày và trên 15 ngày?"),
    ("khách còn nợ quá 19 ngày hết ngày 12/03/2026 tổng cộng bn",
     "Tính đến hết ngày 12/03/2026, khách còn nợ quá 19 ngày tổng cộng bao nhiêu tiền?"),
    # Sales filters and defined denominators.
    ("th2/2026 các đơn trả bằng chuyển khoản dthu bn",
     "Doanh thu các đơn trả bằng chuyển khoản tháng 2/2026 là bao nhiêu?"),
    ("tiền mặt tháng 2/2026 chiếm mấy % tổng dthu",
     "Doanh thu các đơn trả bằng tiền mặt tháng 2/2026 chiếm bao nhiêu % tổng doanh thu?"),
    ("th2/2026 cho dthu đơn trả tiền mặt với dthu đơn trả cod mỗi khoản bn",
     "Tháng 2/2026, doanh thu các đơn trả bằng tiền mặt là bao nhiêu, doanh thu các đơn trả bằng COD là bao nhiêu?"),
    ("qua điện thoại tháng 2/2026 bán được bn doanh thu",
     "Doanh thu các đơn bán qua điện thoại tháng 2/2026 là bao nhiêu?"),
    ("th2/2026 đơn bán tại quầy chiếm mấy phần trăm tổng doanh thu",
     "Doanh thu các đơn bán tại quầy tháng 2/2026 chiếm bao nhiêu % tổng doanh thu?"),
    ("th2/2026 xin số dthu bán tại quầy rồi qua điện thoại, mỗi khoản bn",
     "Tháng 2/2026, doanh thu các đơn bán tại quầy là bao nhiêu, doanh thu các đơn bán qua điện thoại là bao nhiêu?"),
    ("tháng này thuốc có hoạt chất Kẽm gluconat bán tổng bn tiền",
     "Thuốc có hoạt chất Kẽm gluconat tháng này bán được tổng cộng bao nhiêu tiền?"),
    ("th2/2026 thuốc kê đơn bán được mấy đồng tất cả",
     "Thuốc kê đơn tháng 2/2026 bán được tổng cộng bao nhiêu tiền?"),
    ("th2/2026 thuốc không kê đơn chiếm mấy % doanh thu nhà thuốc",
     "Thuốc không kê đơn chiếm bao nhiêu % doanh thu của nhà thuốc tháng 2/2026?"),
    ("th2/2026 trong số khách đã mua, khách mua ít nhất 4 đơn chiếm bao nhiêu phần trăm",
     "Tháng 2/2026, trong số khách đã mua, khách mua ít nhất 4 đơn chiếm bao nhiêu phần trăm?"),
    ("ca sáng tuần này có mấy đơn vậy", "Tuần này ca sáng có bao nhiêu đơn hàng?"),
    ("nhân viên mã NV036 tuần trước bán mấy đơn",
     "Tuần trước các đơn do nhân viên mã NV036 bán có bao nhiêu đơn?"),
    ("th2/2026 dthu ca chiều so với ca sáng chênh mấy %",
     "Tháng 2/2026 doanh thu ca chiều so với ca sáng chênh bao nhiêu phần trăm?"),
    ("hôm qua bán mấy gói tính mọi mặt hàng theo gói",
     "Số gói bán được hôm qua là bao nhiêu, cộng mọi mặt hàng tính theo gói?"),
    ("th2/2026 bán mấy ly size L tính mọi loại đồ uống",
     "Trong tháng 2/2026 số ly size L bán được là bao nhiêu, tính mọi loại đồ uống?"),
    ("Trà mây tía lên 38.000đ ngày 08/02/2026, so 14 ngày kể từ 08/02/2026 với 14 ngày trước đó số ly bán thay đổi mấy %",
     "Từ ngày 08/02/2026, Trà mây tía tăng giá lên 38.000 đồng. So 14 ngày kể từ ngày 08/02/2026 với 14 ngày ngay trước đó, số ly bán ra thay đổi bao nhiêu phần trăm?"),
    ("ca chiều tuần này số đơn chênh ca sáng mấy %",
     "Tuần này số đơn ca chiều chênh ca sáng bao nhiêu phần trăm?"),
    ("th2/2026 ai mua hang nhieu tien nhat vay",
     "Tháng 2/2026, khách nào mua nhiều tiền nhất?"),
)

SYSTEM_PROMPT = f"""You restate one Vietnamese question from a shop owner in the standard
wording shown by the examples, so that a fixed parser can read it.
- Keep every number, date, amount, code and period word (hôm nay, tháng này, tuần trước,
  năm nay, năm ngoái...) exactly. Never turn a relative period into a date.
- Copy product, customer, staff and supplier names exactly as written.
- Fix spelling, missing accents, abbreviations and word order of ordinary words only.
- Never add, drop or change a condition, filter, comparison, unit or requested output.
- Do not answer the question.
If no example has the same kind of question, or you are unsure, output exactly {UNSURE}.
Output one line: the restated question only."""

# Experimental: abstain only when the meaning cannot be kept. The grammar, the safety checks
# and the agreement of both votes still decide whether a restatement is answered.
LENIENT_PROMPT = SYSTEM_PROMPT.replace(
    f"If no example has the same kind of question, or you are unsure, output exactly {UNSURE}.",
    "Restate every question about the shop's own data in the closest example wording, even "
    "when no example has exactly the same kind of question.\n"
    f"Output exactly {UNSURE} only when that is impossible without adding, dropping or "
    "changing a condition, filter, period or requested output.")
assert LENIENT_PROMPT != SYSTEM_PROMPT

# normalize_question writes "500.000" as "500 000".
_NUMBER_SEPARATOR = re.compile(r"(?<=\d)[ .,](?=\d{3}(?!\d))")
# Amounts: "500k", "1k5", "1tr5", "1,5 triệu", "2 triệu rưỡi", "3 trăm nghìn", "2 tỷ".
_SCALES = {"k": 1000, "nghin": 1000, "ngan": 1000, "tr": 1_000_000, "trieu": 1_000_000,
           "cu": 1_000_000, "ty": 1_000_000_000}
_GLUED_AMOUNT = re.compile(r"\b(\d+)(k|tr)(\d{1,3})\b")
_HUNDRED_THOUSANDS = re.compile(r"\b(\d+) tram (?:nghin|ngan|k)\b")
_AMOUNT = re.compile(r"\b(\d+)(?: (\d{1,2}))? ?(k|nghin|ngan|tr|trieu|cu|ty)\b( ruoi\b)?")
_ROMAN_QUARTER = re.compile(r"\bquy (iv|iii|ii|i)\b")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def _amount(m: re.Match[str]) -> str:
    scale = _SCALES[m[3]]
    whole = int(m[1]) * scale
    if m[2]:  # "1,5 triệu" folds to "1 5 trieu"
        whole += int(m[2]) * scale // 10 ** len(m[2])
    if m[4]:  # "rưỡi" = and a half
        whole += scale // 2
    return str(whole)


def _glued(m: re.Match[str]) -> str:
    scale = _SCALES[m[2]]
    return str(int(m[1]) * scale + int(m[3]) * scale // 10 ** len(m[3]))


# Words whose presence changes the meaning. Present in the question => present in the
# restatement. "both" groups must also not appear in a restatement whose question lacks them.
# The last item is removed before the check: phrases that share the folded word but not the
# meaning ("số ngày" is not "so với", "còn lại" is not "lãi", "ít nhất 3 đơn" is a threshold).
_GUARDS: tuple[tuple[str, str, bool, str], ...] = (
    ("negation", r"\b(?:khong|ko|chua|chang)\b", True, ""),
    ("cancel", r"\bhuy\b", True, ""),
    ("return", r"\btra (?:hang|lai)\b|\bhoan\b", True,
     r"\bhoan (?:thanh|tat|toan|chinh)\b"),
    ("percent", r"%|\bphan tram\b", True, ""),
    # "tháng 4/2025" -> "quý 4/2025" keeps every number but asks another period.
    ("quarter", r"\bquy\b|\bq ?[1-4]\b", True,
     r"\bquy (?:doi|ra|cach|dinh|tac|uoc|trinh|khach)\b"),
    ("compare", r"\bso (?:voi|vs)\b|\bso\b(?= (?:thang|tuan|nam|quy|ngay|tet|ky|cung))", False, ""),
    ("average", r"\b(?:trung binh|binh quan|tb)\b", False, ""),
    ("extreme", r"\bnhat\b|\btop\b", False,
     r"\b(?:it nhat|toi thieu)(?= \d)|\bduy nhat\b|\bcap nhat\b"
     r"|\b(?:\d+|hai|ba|bon|nam|sau|bay|tam|chin|muoi) (?:ngay|tuan|thang|nam|don|lan)"
     r" (?:gan|gan day) nhat\b"),
    ("payment", r"\b(?:tien mat|chuyen khoan|cod|quet the)\b", False, ""),
    ("staff", r"\b(?:nhan vien|nv)\b", False, ""),
    ("customer", r"\b(?:khach|kh)\b", False, ""),
    ("supplier", r"\b(?:nha cung cap|ncc)\b", False, ""),
    ("purchase", r"\bnhap\b", False, ""),
    ("stock", r"\bton\b|\bkho\b", False, ""),
    ("debt", r"\bno\b", False, ""),
    ("group", r"\bnhom\b", False, ""),
    ("profit", r"\b(?:loi|lai)\b", False,
     r"\b(?:con|tra|hoan|doi|ban|mua|nhap|xem|tinh|kiem|quay|ghi|giu|de|gui|lam|cap) lai\b"
     r"|\blai (?:lan|nua|tu dau)\b|\btra loi\b|\b(?:hang|bi|san pham|mon) loi\b"),
)
# Accented words that fold onto a guard word with another meaning ("số" -> "so", "nó" ->
# "no", "lại" -> "lai", "khô" -> "kho"). Read with accents only; an unaccented question
# keeps the cautious folded reading.
_HOMOGRAPHS = re.compile(r"(?<!\w)(?:số|nó|lỗi|tốn|khó|khô|quỹ)(?!\w)")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_question(text)).strip()


def _guard_text(text: str, names: Iterable[str]) -> str:
    """Folded text for the meaning guards, without catalogue names (checked on their own)
    and without accented homographs of guard words."""
    plain = f" {_plain(_HOMOGRAPHS.sub(' ', unicodedata.normalize('NFC', text).lower()))} "
    for key in sorted({_plain(name) for name in names}, key=len, reverse=True):
        if len(key) >= 4:
            plain = plain.replace(f" {key} ", "  ")
    return plain


def numbers(text: str) -> Counter[int]:
    """Every number of the question, with thousand separators, amounts and quarters expanded."""
    plain = _NUMBER_SEPARATOR.sub("", _plain(text))
    plain = _ROMAN_QUARTER.sub(lambda m: f"quy {_ROMAN[m[1]]}", plain)
    plain = _GLUED_AMOUNT.sub(_glued, plain)
    plain = _HUNDRED_THOUSANDS.sub(lambda m: str(int(m[1]) * 100_000), plain)
    plain = _AMOUNT.sub(_amount, plain)
    return Counter(int(value) for value in re.findall(r"\d+", plain))


# Small counts the model may write as a digit or as a word ("3 tháng" / "ba tháng"). Read
# with accents, so "sau" (after), "tạm", "muối" are never counts; "năm" (year) and "tư"
# only before a counted noun or as a month/quarter.
_COUNTED = (r"(?= (?:ngày|tháng|tuần|quý|đơn|khách|mặt|sản|món|lần|nhân|nhà|lô|hộp|gói|chai"
            r"|ly|người|loại|ca|kỳ|đợt|phiếu|hoá|hóa)\b)")
_NUMBER_WORDS = {
    1: r"một|mot", 2: r"hai", 3: r"ba", 4: r"bốn|bon|(?<=quý )tư|(?<=tháng )tư",
    5: r"năm" + _COUNTED, 6: r"sáu", 7: r"bảy|bẩy|bay", 8: r"tám", 9: r"chín|chin",
    10: r"mười",
}


def _number_words(text: str, value: int) -> int:
    lowered = unicodedata.normalize("NFC", text).lower()
    return len(re.findall(rf"(?<!\w)(?:{_NUMBER_WORDS[value]})(?!\w)", lowered))


def _same_numbers(question: str, rewrite: str) -> bool:
    """Same numbers; a digit 1-10 may stand for its word on the other side ("một", "ba")."""
    asked, restated = numbers(question), numbers(rewrite)
    for value in _NUMBER_WORDS:
        digits_asked, digits_restated = asked.pop(value, 0), restated.pop(value, 0)
        if not (digits_asked <= digits_restated + _number_words(rewrite, value)
                and digits_restated <= digits_asked + _number_words(question, value)):
            return False
    return asked == restated


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", fold(text))) + " "


# Month spellings the model misreads as quarters or years ("th4/2026" -> "quý IV",
# "10/2025" -> "năm 2025"). Written out before the model sees the question; the safety
# checks compare against this same text, so no number is added or lost.
_MONTH_YEAR = re.compile(r"\b(?:[Tt]h(?:g|áng|ang)?|[Tt])\s?(\d{1,2})\s*[/.-]\s*(\d{4})\b")
_MONTH_SHORT_YEAR = re.compile(r"\b(?:[Tt]h(?:g|áng|ang)?|[Tt])\s?(\d{1,2})/([23]\d)\b(?![\d/])")
_BARE_MONTH_YEAR = re.compile(
    r"(?<![\d/.-])(?<!tháng )(?<!Tháng )(?<!ngày )(?<!quý )(\d{1,2})\s*[/-]\s*(20\d{2})(?![\d/])")
_MONTH_ALONE = re.compile(r"\b[Tt]h(?:g)?\s?(\d{1,2})\b(?![\d/.-])")


# "tháng giêng", "tháng mười một", "quý IV", "quý tư": the model and the checks see digits.
# Not "tháng năm" ("tháng, năm") nor unaccented "thang tu" ("tháng từ ...").
_MONTH_WORDS = {"gieng": 1, "hai": 2, "ba": 3, "tu": 4, "bon": 4, "sau": 6, "bay": 7,
                "tam": 8, "chin": 9, "muoi": 10, "muoi mot": 11, "muoi hai": 12, "chap": 12}
_MONTH_WORD = re.compile(
    r"(?<!\w)([Tt]háng) (giêng|hai|ba|tư|bốn|sáu|bảy|bẩy|tám|chín|mười một|mười hai|mười"
    r"|chạp)(?!\w)(?! (?:tháng|ngày|tuần|lần)\b)")
_QUARTER_WORDS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "một": 1, "hai": 2, "ba": 3, "bốn": 4,
                  "tư": 4}
_QUARTER_WORD = re.compile(r"(?<!\w)([Qq]uý) (IV|III|II|I|iv|iii|ii|i|một|hai|ba|bốn|tư)(?!\w)")


def spell_months(question: str) -> str:
    """The question with every month written "tháng M/YYYY" (or "tháng M") and every
    quarter "quý N"."""
    def month(m: re.Match[str], year: str = "") -> str:
        number = int(m.group(1))
        return f"tháng {number}{year}" if 1 <= number <= 12 else m.group()

    text = _MONTH_WORD.sub(lambda m: f"{m[1]} {_MONTH_WORDS[fold(m[2])]}", question)
    text = _QUARTER_WORD.sub(lambda m: f"{m[1]} {_QUARTER_WORDS[m[2].lower()]}", text)
    text = _MONTH_YEAR.sub(lambda m: month(m, f"/{m.group(2)}"), text)
    text = _MONTH_SHORT_YEAR.sub(lambda m: month(m, f"/20{m.group(2)}"), text)
    text = _BARE_MONTH_YEAR.sub(lambda m: month(m, f"/{m.group(2)}"), text)
    return _MONTH_ALONE.sub(month, text)


def check_rewrite(question: str, rewrite: str, names: Iterable[str] = ()) -> str | None:
    """Reason the restatement is unsafe, or None. Never repairs, only rejects."""
    if not rewrite or len(rewrite) > MAX_REWRITE_CHARACTERS or "\n" in rewrite:
        return "Rewrite is empty, too long or not one line"
    if not _same_numbers(question, rewrite):
        return "Rewrite changed the numbers of the question"
    names = list(names)
    asked, restated = _words(question), _words(rewrite)
    for name in names:
        key = _words(name)
        if len(key.strip()) >= 4 and (key in asked) != (key in restated):
            return f"Rewrite added or dropped the name {name!r}"
    plain_question, plain_rewrite = _guard_text(question, names), _guard_text(rewrite, names)
    for concept, pattern, both, ignore in _GUARDS:
        before_text = re.sub(ignore, " ", plain_question) if ignore else plain_question
        after_text = re.sub(ignore, " ", plain_rewrite) if ignore else plain_rewrite
        before = re.search(pattern, before_text) is not None
        after = re.search(pattern, after_text) is not None
        if (before and not after) or (both and after and not before):
            return f"Rewrite changed the '{concept}' meaning"
    return None


def _trigrams(text: str) -> Counter[str]:
    plain = f"  {_plain(text)}  "
    return Counter(plain[i:i + 3] for i in range(len(plain) - 2))


def _similarity(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(count * b[key] for key, count in a.items())
    norm = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


_EXAMPLE_GRAMS = tuple((_trigrams(raw), _trigrams(standard)) for raw, standard in EXAMPLES)


def select_examples(question: str, k: int = 6) -> list[tuple[str, str]]:
    """The k examples closest to the question by character trigrams (ties: list order)."""
    grams = _trigrams(question)
    scored = sorted(range(len(EXAMPLES)), key=lambda i: (
        -max(_similarity(grams, _EXAMPLE_GRAMS[i][0]), _similarity(grams, _EXAMPLE_GRAMS[i][1])), i))
    return [EXAMPLES[i] for i in scored[:k]]


def build_rewrite_messages(question: str, examples: list[tuple[str, str]],
                           rule_names: Iterable[str] = (), *,
                           lenient: bool = False) -> list[dict[str, str]]:
    """Prompt; ``rule_names`` are the split's enabled named rankings, offered as standard
    wordings ("<tên luật> tháng 2/2026 là ai?") for questions with the same meaning."""
    shots = "\n".join(f"Câu hỏi: {raw}\nViết lại: {standard}" for raw, standard in examples)
    system = LENIENT_PROMPT if lenient else SYSTEM_PROMPT
    names = [name for name in rule_names if name]
    if names:
        system += ("\nThis shop's rulebook also names these rankings; when the question asks "
                   "for exactly one of them, restate it as '<name> <period> là ai/là gì/là ngày "
                   "nào?' keeping the name exactly: " + "; ".join(names))
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"{shots}\nCâu hỏi: {question}\nViết lại:"}]


def _retry_messages(messages: list[dict[str, str]], rewrite: str,
                    remaining: str | None) -> list[dict[str, str]]:
    if remaining:
        words = " ".join(remaining.split()[:12])
        feedback = f"Bộ đọc chưa hiểu các từ: «{words}»."
    else:
        feedback = "Bộ đọc chưa lập được phép tính và kỳ thời gian từ câu viết lại."
    return [*messages, {"role": "assistant", "content": rewrite},
            {"role": "user", "content": (
                f"{feedback} Hãy viết lại câu hỏi gốc đúng một dòng theo cách viết "
                f"trong các ví dụ, giữ nguyên nghĩa. Nếu chưa chắc, trả {UNSURE}.")}]


def _rejected_messages(messages: list[dict[str, str]], rewrite: str,
                       rejected: str) -> list[dict[str, str]]:
    return [*messages, {"role": "assistant", "content": rewrite},
            {"role": "user", "content": (
                f"Câu viết lại bị từ chối ({rejected}). Hãy viết lại câu hỏi gốc đúng một "
                "dòng, giữ nguyên mọi số, ngày, kỳ, tên và mọi điều kiện của câu gốc. "
                f"Nếu chưa chắc, trả {UNSURE}.")}]



SPEC_MAX_NEW_TOKENS = 320  # one rule spec JSON


def parse_rewrite(text: str) -> str | None:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return None
    # A model that echoes "Câu hỏi: …" before "Viết lại: …" restates on the later line.
    labelled = [line for line in lines if re.match(r"(?:viết lại|viet lai)\s*:", line, re.I)]
    line = labelled[0] if labelled else lines[0]
    if re.match(r"(?:câu hỏi|cau hoi)\s*:", line, re.IGNORECASE):
        return None
    line = re.sub(r"^(?:viết lại|viet lai)\s*:\s*", "", line, flags=re.IGNORECASE)
    line = line.strip().strip("`\"'“”").strip()
    if not line or UNSURE in line:
        return None
    return line


# Rewrite failures after which the model calculation is tried (never model or data failures).
_CODE_AFTER = ("Rewrite model was unsure", "Grammar cannot plan the rewrite",
               "Rewrites lead to different plans", "Rewrite changed", "Rewrite added or dropped",
               "Rewrite is empty")


class RewriteAgent(LocalPlanAgent):
    """Grammar first; on failure, plan model restatements with the same grammar."""

    def __init__(self, model_path: str, *, votes: int = 2, examples: int = 6,
                 rewrite_retries: int = 1, lenient: bool = False,
                 retry_rejected: bool = False, code_fallback: bool = False,
                 code_only: bool = False, **kwargs: Any) -> None:
        super().__init__(model_path, **kwargs)
        if any(type(flag) is not bool for flag in (lenient, retry_rejected, code_fallback, code_only)):
            raise ValueError("lenient, retry_rejected, code_fallback and code_only must be true or false")
        self.lenient, self.retry_rejected = lenient, retry_rejected
        self.code_fallback, self.code_only = code_fallback, code_only
        if type(votes) is not int or not 1 <= votes <= 3:
            raise ValueError("votes must be 1, 2 or 3 (two of three must agree)")
        if type(examples) is not int or not 1 <= examples <= len(EXAMPLES):
            raise ValueError("examples must be between 1 and the number of examples")
        if type(rewrite_retries) is not int or rewrite_retries not in (0, 1):
            raise ValueError("rewrite_retries must be 0 or 1")
        self.votes, self.examples = votes, examples
        self.rewrite_retries = rewrite_retries
        self.rule_spec_audit: list[dict[str, Any]] = []

    def _rule_specs(self, rulebook: str) -> tuple:
        """Deterministic specs plus model-filled ones for ranking rows the reader leaves
        off, once per rulebook. Without the model: the deterministic specs only."""
        cache = self.__dict__.setdefault("_spec_cache", {})
        if rulebook in cache:
            return cache[rulebook]
        if self._ensure_model() is not None:
            cache[rulebook] = enabled_specs(rulebook)
            return cache[rulebook]

        def generate(messages: list[dict[str, str]]) -> str | None:
            result = self.planner_worker.run(
                self.generate, self.model_timeout, messages=messages,
                max_input_tokens=self.max_input_tokens, max_new_tokens=SPEC_MAX_NEW_TOKENS)
            return result.value.get("text", "") if result.ok else None

        results, audit = model_specs(rulebook, generate)
        self.rule_spec_audit.append({
            "rulebook_sha256": hashlib.sha256(rulebook.encode()).hexdigest(), "rows": audit})
        cache[rulebook] = tuple(r.spec for r in results if r.spec is not None)
        return cache[rulebook]

    def _vote(self, vote: int, messages: list[dict[str, str]], question: Question,
              context: BusinessContext, asked: str, names: list[str], product_names,
              vocabulary, evidence: dict[str, Any]) -> Plan | str | Candidate:
        """One restatement (with its retry): a Plan, the reason it failed, or a Candidate
        when the model itself failed."""
        for retry in range(self.rewrite_retries + 1):
            attempt: dict[str, Any] = {"prompt_sha256": hashlib.sha256(
                json.dumps(messages, ensure_ascii=False).encode()).hexdigest()}
            if retry or vote > 2:
                attempt.update(vote=vote, retry=retry)
            evidence["rewrites"].append(attempt)
            generated = self.planner_worker.run(
                self.generate, self.model_timeout, messages=messages,
                max_input_tokens=self.max_input_tokens, max_new_tokens=self.max_new_tokens)
            attempt["execution"] = generated.execution()
            if not generated.ok:
                if generated.timed_out or not self.planner_worker.alive:
                    self._model_error = Candidate(reason="Rewrite model stopped after worker failure",
                                                  evidence=dict(evidence))
                return Candidate(reason=f"Rewrite failed: {generated.error}", evidence=evidence)
            attempt["model_output"] = generated.value
            rewrite = parse_rewrite(generated.value.get("text", ""))
            attempt["rewrite"] = rewrite
            if rewrite is None:
                return "Rewrite model was unsure"
            rejected = check_rewrite(asked, rewrite, names)
            if rejected:
                attempt["rejected"] = rejected
                if not self.retry_rejected or retry == self.rewrite_retries:
                    return rejected
                attempt.update(vote=vote, retry=retry)
                messages = _rejected_messages(messages, rewrite, rejected)
                continue
            restated = Question(question.id, question.business_id, rewrite, question.as_of_date)
            plan = self._plan(restated, context)
            if plan is not None:
                return plan
            if retry == self.rewrite_retries:
                return "Grammar cannot plan the rewrite"
            remaining = unread_words(restated, context.cache["rules"], product_names, vocabulary)
            attempt.update(vote=vote, retry=retry, unread_words=remaining)
            messages = _retry_messages(messages, rewrite, remaining)
        return "Grammar cannot plan the rewrite"

    # Worker tasks of the model calculation (module-level for the spawn worker).
    run_code = staticmethod(code_answer.run_code)
    code_profile = staticmethod(code_answer.profile)

    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        if self.code_only:  # diagnostic: the model calculation alone
            return self._ensure_model() or self._code_answer(question, context)
        plan = self._plan(question, context)
        if plan is not None:
            return self._answer_plan(plan, context, as_of_date=question.as_of_date)
        unrecorded = self._unrecorded(question, context)
        if unrecorded is not None:
            return unrecorded
        unavailable = self._ensure_model()
        if unavailable is not None:
            return unavailable
        candidate = self._rewrite_answer(question, context)
        if (self.code_fallback and candidate.type == "abstain"
                and candidate.reason.startswith(_CODE_AFTER)):
            computed = self._code_answer(question, context)
            if computed.type != "abstain":
                computed.evidence["rewrites"] = candidate.evidence.get("rewrites", [])
                return computed
            candidate.evidence["code_attempt"] = computed.evidence
            candidate.reason = f"{candidate.reason}; code: {computed.reason}"
        return candidate

    def _code_answer(self, question: Question, context: BusinessContext) -> Candidate:
        """Two independent model-written calculations that must return the same answer."""
        failure = self._prepare(context)
        if failure is not None:
            return failure
        today = question.as_of_date
        shape = self.worker.run(self.code_profile, self.question_timeout, today=today,
                                catalog=context.catalog, calendar=context.split.calendar_path)
        if not shape.ok:
            return _execution_failure("Code profile", shape, {})
        evidence: dict[str, Any] = {"model": self._model_info, "path": "code",
                                    "planner_confidence": REWRITE_CONFIDENCE,
                                    "code_votes": []}
        results = []
        for vote, examples in enumerate((list(CODE_EXAMPLES), list(CODE_EXAMPLES)[::-1]), 1):
            messages = build_code_messages(question.question, today, shape.value,
                                           context.split.rulebook, examples)
            for repair in range(CODE_REPAIRS + 1):
                attempt: dict[str, Any] = {"vote": vote, "repair": repair,
                                           "prompt_sha256": hashlib.sha256(json.dumps(
                                               messages, ensure_ascii=False).encode()).hexdigest()}
                evidence["code_votes"].append(attempt)
                generated = self.planner_worker.run(
                    self.generate, CODE_TIMEOUT_SECONDS, messages=messages,
                    max_input_tokens=self.max_input_tokens, max_new_tokens=CODE_MAX_NEW_TOKENS)
                attempt["execution"] = generated.execution()
                if not generated.ok:
                    if generated.timed_out or not self.planner_worker.alive:
                        self._model_error = Candidate(
                            reason="Rewrite model stopped after worker failure",
                            evidence=dict(evidence))
                    return Candidate(reason=f"Code model failed: {generated.error}",
                                     evidence=evidence)
                attempt["model_output"] = generated.value
                code = extract_code(generated.value.get("text", ""))
                attempt["code"] = code
                if code is None:
                    return Candidate(reason="Model wrote no answer function", evidence=evidence)
                ran = self.worker.run(self.run_code, self.question_timeout, code=code,
                                      today=today, catalog=context.catalog,
                                      calendar=context.split.calendar_path)
                attempt["run"] = ran.execution()
                # A raised error or a refused/unusable result may be repaired once; a
                # timeout or a deliberate None is final.
                if not ran.ok:
                    error = f"Model code failed: {ran.error}"[:300]
                    if ran.timed_out or repair == CODE_REPAIRS:
                        return Candidate(reason=error, evidence=evidence)
                    messages = repair_messages(messages, code, ran.error)
                    continue
                if "refused" in ran.value:
                    result: tuple[str, Any] | str = ran.value["refused"]
                else:
                    attempt["result"] = ran.value["value"]
                    result = normalize_result(ran.value["value"])
                if isinstance(result, str):
                    if result == "Code returned no answer" or repair == CODE_REPAIRS:
                        return Candidate(reason=result, evidence=evidence)
                    messages = repair_messages(messages, code, result)
                    continue
                # A type the question rules out, a zero or an unasked negative is final:
                # the code answered another question, and a repair would only guess.
                doubt = implausible(question.question, *result)
                if doubt is not None:
                    return Candidate(reason=doubt, evidence=evidence)
                results.append(result)
                break
        if results[0] != results[1]:
            return Candidate(reason="Model calculations disagree", evidence=evidence)
        kind, value = results[0]
        evidence.update(code=next(a["code"] for a in evidence["code_votes"] if "result" in a
                                  and normalize_result(a["result"]) == results[0]), result=value)
        return Candidate(type=kind, answer=value, confidence=REWRITE_CONFIDENCE,
                         reason="Model calculation", evidence=evidence)

    def _rewrite_answer(self, question: Question, context: BusinessContext) -> Candidate:
        product_names = self._product_names(context)
        vocabulary = self._vocabulary(context)
        names = [*product_names, *(label for labels in vocabulary.values() for label in labels)]
        asked = spell_months(question.question)  # what the model reads and is checked on
        chosen = select_examples(asked, self.examples)
        rule_names = [spec.nguon.strip().strip("|").split("|")[0].strip()
                      for spec in self._rule_specs(context.split.rulebook)]
        # Independent restatements: the same examples, most similar first, then last; a
        # third vote (votes=3) interleaves them and is asked only when the first two differ.
        orders = [chosen, chosen[::-1], chosen[1::2] + chosen[::2]][:self.votes]
        prompts = [build_rewrite_messages(asked, order, rule_names, lenient=self.lenient)
                   for order in orders]
        evidence: dict[str, Any] = {"model": self._model_info,
                                    "planner_confidence": REWRITE_CONFIDENCE,
                                    "rewrites": []}
        plans: list[Plan] = []
        failures: list[str] = []
        for vote, messages in enumerate(prompts, 1):
            outcome = self._vote(vote, messages, question, context, asked, names,
                                 product_names, vocabulary, evidence)
            if isinstance(outcome, Candidate):
                return outcome  # the model itself failed
            if isinstance(outcome, str):
                failures.append(outcome)
                if self.votes < 3:
                    return Candidate(reason=outcome, evidence=evidence)
            else:
                plans.append(outcome)
            if self.votes == 3 and vote == 2:
                if len(plans) == 2 and plans[0] == plans[1]:
                    break  # agreed: no third vote
                if not plans:
                    return Candidate(reason=failures[0], evidence=evidence)
        if self.votes == 3:
            agreed = [plan for plan in plans if plans.count(plan) >= 2]
            if not agreed:
                reason = failures[0] if failures and len(plans) < 2 else (
                    "Rewrites lead to different plans")
                return Candidate(reason=reason, evidence=evidence)
            plans = [agreed[0]]
        elif any(plan != plans[0] for plan in plans[1:]):
            return Candidate(reason="Rewrites lead to different plans", evidence=evidence)
        candidate = self._answer_plan(plans[0], context, as_of_date=question.as_of_date)
        candidate.evidence = {**evidence, **candidate.evidence}
        if candidate.type == "unanswerable":
            # Only the deterministic path may conclude that data is missing.
            return Candidate(reason="Rewrite path never concludes unanswerable",
                             evidence=candidate.evidence)
        candidate.confidence = min(candidate.confidence, REWRITE_CONFIDENCE)
        return candidate
