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
from collections import Counter
from collections.abc import Iterable
from typing import Any

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

# normalize_question writes "500.000" as "500 000".
_NUMBER_SEPARATOR = re.compile(r"(?<=\d)[ .,](?=\d{3}(?!\d))")
_THOUSANDS = re.compile(r"\b(\d+)\s*(?:k|nghin|ngan)\b")
_MILLIONS = re.compile(r"\b(\d+)\s*(?:tr|trieu|cu)\b")

# Words whose presence changes the meaning. Present in the question => present in the
# restatement. "both" groups must also not appear in a restatement whose question lacks them.
_GUARDS: tuple[tuple[str, str, bool], ...] = (
    ("negation", r"\b(?:khong|ko|chua|chang)\b", True),
    ("cancel", r"\bhuy\b", True),
    ("return", r"\btra (?:hang|lai)\b|\bhoan\b", True),
    ("percent", r"%|\bphan tram\b", True),
    # "tháng 4/2025" -> "quý 4/2025" keeps every number but asks another period.
    ("quarter", r"\bquy\b|\bq ?[1-4]\b", True),
    ("compare", r"\bso (?:voi|vs)\b|\bso\b(?= (?:thang|tuan|nam|quy|ngay|tet|ky|cung))", False),
    ("average", r"\b(?:trung binh|binh quan|tb)\b", False),
    ("extreme", r"\bnhat\b|\btop\b", False),
    ("payment", r"\b(?:tien mat|chuyen khoan|cod|quet the)\b", False),
    ("staff", r"\b(?:nhan vien|nv)\b", False),
    ("customer", r"\b(?:khach|kh)\b", False),
    ("supplier", r"\b(?:nha cung cap|ncc)\b", False),
    ("purchase", r"\bnhap\b", False),
    ("stock", r"\bton\b|\bkho\b", False),
    ("debt", r"\bno\b", False),
    ("group", r"\bnhom\b", False),
    ("profit", r"\b(?:loi|lai)\b", False),
)


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_question(text)).strip()


def numbers(text: str) -> Counter[int]:
    """Every number of the question, with thousand separators and k / triệu expanded."""
    plain = _NUMBER_SEPARATOR.sub("", _plain(text))
    plain = _THOUSANDS.sub(lambda m: str(int(m[1]) * 1000), plain)
    plain = _MILLIONS.sub(lambda m: str(int(m[1]) * 1_000_000), plain)
    return Counter(int(value) for value in re.findall(r"\d+", plain))


def _same_numbers(question: str, rewrite: str) -> bool:
    """Same numbers; a digit 1 may stand for the word "một" on the other side."""
    asked, restated = numbers(question), numbers(rewrite)
    ones_asked, ones_restated = asked.pop(1, 0), restated.pop(1, 0)
    word_asked = len(re.findall(r"\bmot\b", _plain(question)))
    word_restated = len(re.findall(r"\bmot\b", _plain(rewrite)))
    return (asked == restated and ones_asked <= ones_restated + word_restated
            and ones_restated <= ones_asked + word_asked)


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


def spell_months(question: str) -> str:
    """The question with every month written "tháng M/YYYY" (or "tháng M")."""
    def month(m: re.Match[str], year: str = "") -> str:
        number = int(m.group(1))
        return f"tháng {number}{year}" if 1 <= number <= 12 else m.group()

    text = _MONTH_YEAR.sub(lambda m: month(m, f"/{m.group(2)}"), question)
    text = _MONTH_SHORT_YEAR.sub(lambda m: month(m, f"/20{m.group(2)}"), text)
    text = _BARE_MONTH_YEAR.sub(lambda m: month(m, f"/{m.group(2)}"), text)
    return _MONTH_ALONE.sub(month, text)


def check_rewrite(question: str, rewrite: str, names: Iterable[str] = ()) -> str | None:
    """Reason the restatement is unsafe, or None. Never repairs, only rejects."""
    if not rewrite or len(rewrite) > MAX_REWRITE_CHARACTERS or "\n" in rewrite:
        return "Rewrite is empty, too long or not one line"
    if not _same_numbers(question, rewrite):
        return "Rewrite changed the numbers of the question"
    asked, restated = _words(question), _words(rewrite)
    for name in names:
        key = _words(name)
        if len(key.strip()) >= 4 and (key in asked) != (key in restated):
            return f"Rewrite added or dropped the name {name!r}"
    plain_question, plain_rewrite = _plain(question), _plain(rewrite)
    for concept, pattern, both in _GUARDS:
        before = re.search(pattern, plain_question) is not None
        after = re.search(pattern, plain_rewrite) is not None
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
                           rule_names: Iterable[str] = ()) -> list[dict[str, str]]:
    """Prompt; ``rule_names`` are the split's enabled named rankings, offered as standard
    wordings ("<tên luật> tháng 2/2026 là ai?") for questions with the same meaning."""
    shots = "\n".join(f"Câu hỏi: {raw}\nViết lại: {standard}" for raw, standard in examples)
    system = SYSTEM_PROMPT
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



SPEC_MAX_NEW_TOKENS = 320  # one rule spec JSON


def parse_rewrite(text: str) -> str | None:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return None
    line = re.sub(r"^(?:viết lại|viet lai)\s*:\s*", "", lines[0], flags=re.IGNORECASE)
    line = line.strip().strip("`\"'“”").strip()
    if not line or UNSURE in line:
        return None
    return line


class RewriteAgent(LocalPlanAgent):
    """Grammar first; on failure, plan model restatements with the same grammar."""

    def __init__(self, model_path: str, *, votes: int = 2, examples: int = 6,
                 rewrite_retries: int = 1, **kwargs: Any) -> None:
        super().__init__(model_path, **kwargs)
        if type(votes) is not int or not 1 <= votes <= 2:
            raise ValueError("votes must be 1 or 2")
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

    def answer(self, question: Question, context: BusinessContext) -> Candidate:
        plan = self._plan(question, context)
        if plan is not None:
            return self._answer_plan(plan, context, as_of_date=question.as_of_date)
        unrecorded = self._unrecorded(question, context)
        if unrecorded is not None:
            return unrecorded
        unavailable = self._ensure_model()
        if unavailable is not None:
            return unavailable
        product_names = self._product_names(context)
        vocabulary = self._vocabulary(context)
        names = [*product_names, *(label for labels in vocabulary.values() for label in labels)]
        asked = spell_months(question.question)  # what the model reads and is checked on
        chosen = select_examples(asked, self.examples)
        rule_names = [spec.nguon.strip().strip("|").split("|")[0].strip()
                      for spec in self._rule_specs(context.split.rulebook)]
        # Independent restatements: the same examples, most similar first, then last.
        prompts = [build_rewrite_messages(asked, chosen, rule_names),
                   build_rewrite_messages(asked, chosen[::-1], rule_names)
                   ][:self.votes]
        evidence: dict[str, Any] = {"model": self._model_info,
                                    "planner_confidence": REWRITE_CONFIDENCE,
                                    "rewrites": []}
        plans: list[Plan] = []
        for vote, messages in enumerate(prompts, 1):
            for retry in range(self.rewrite_retries + 1):
                attempt: dict[str, Any] = {"prompt_sha256": hashlib.sha256(
                    json.dumps(messages, ensure_ascii=False).encode()).hexdigest()}
                if retry:
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
                    return Candidate(reason="Rewrite model was unsure", evidence=evidence)
                rejected = check_rewrite(asked, rewrite, names)
                if rejected:
                    attempt["rejected"] = rejected
                    return Candidate(reason=rejected, evidence=evidence)
                restated = Question(question.id, question.business_id, rewrite, question.as_of_date)
                plan = self._plan(restated, context)
                if plan is not None:
                    plans.append(plan)
                    break
                if retry == self.rewrite_retries:
                    return Candidate(reason="Grammar cannot plan the rewrite", evidence=evidence)
                remaining = unread_words(restated, context.cache["rules"], product_names, vocabulary)
                attempt.update(vote=vote, retry=retry, unread_words=remaining)
                messages = _retry_messages(messages, rewrite, remaining)
        if any(plan != plans[0] for plan in plans[1:]):
            return Candidate(reason="Rewrites lead to different plans", evidence=evidence)
        candidate = self._answer_plan(plans[0], context, as_of_date=question.as_of_date)
        candidate.evidence = {**evidence, **candidate.evidence}
        if candidate.type == "unanswerable":
            # Only the deterministic path may conclude that data is missing.
            return Candidate(reason="Rewrite path never concludes unanswerable",
                             evidence=candidate.evidence)
        candidate.confidence = min(candidate.confidence, REWRITE_CONFIDENCE)
        return candidate
