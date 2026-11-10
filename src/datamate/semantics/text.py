import re
import unicodedata
from collections.abc import Iterable


def fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value).lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9/]+", " ", text).split())


# Words the question grammar uses. A Telex-typed token (e.g. "toongr", "dduwowcj") is only
# rewritten when its decoded form is one of these words, so product/customer names that
# merely look like Telex ("max", "gas", "plus") are never altered.
GRAMMAR_WORDS = frozenset("""
    a bao ban bi bill binh biet bn bnhiu bua cao chay cho co cong cua cuoi da dat dau dem den do
    hot ngay nhom chiem gop
    don dong duoc gan gi gia gium giup ha hang het hien hoi hom huy khach la le luong mang
    mat may minh moi mon mot nam nao nay nhat nhieu nhiu nhung pham phan quan qua quy roi
    san so tai tam thai thang theo thu ti tien toi tong tram trang tren trong truoc trung
    tu tuan ty vay ve xem xet
    cung doi giam hay hon kem ke ky liet lien ngoai tang top truong voi vs
    con huong khong luon nguoi no phai sau tuc tung xu
    sang chieu tra bot chiu them thieu nha vao
    len nhi thuoc
    bang dien thoai
""".split())
# Whole-token abbreviations and misspellings seen in everyday questions.
ABBREVIATIONS = {
    "hnay": "hom nay", "hqua": "hom qua", "dc": "duoc", "sp": "san pham", "trc": "truoc",
    "revenue": "doanh thu", "total": "tong cong",
    # Chat spellings: "mik", "kua", "jum", "đh" (đơn hàng).
    "mik": "minh", "mk": "minh", "kua": "cua", "dh": "don hang",
    # "giùm", "dùm", "jum" = "giúp".
    "gium": "giup", "dum": "giup", "jum": "giup",
}
_TONE_KEYS = "sfrxj"


def _untelex(token: str) -> str:
    """Decode Telex keystrokes left in a folded token: dd, aa/ee/oo, aw/ow/uw, tone key."""
    if "/" in token and not any(c.isdigit() for c in token):
        return "/".join(_untelex(part) for part in token.split("/"))  # "tawng/giamr"
    if len(token) < 3 or not token.isalpha():
        return token
    decoded = re.sub(r"(?<=[aou])w", "", re.sub(r"([aeo])\1", r"\1", token.replace("dd", "d")))
    if decoded[-1] in _TONE_KEYS and len(decoded) > 1:
        decoded = decoded[:-1]
    return decoded if decoded in GRAMMAR_WORDS else token


# Long grammar words whose adjacent letters are often swapped when typing fast ("nheiu",
# "nhta", "thnag"). None of their swapped forms is a Vietnamese syllable or a filler word,
# so a product name is never turned into a word the grammar would drop.
_TRANSPOSABLE = frozenset("""
    nhieu nhat doanh thang tong trung binh khach duoc truoc giam tang hang ngay chay
""".split())
# Closing particles that never carry a condition; removed only at the end of a question.
# Not "do", "luon", "rua", "ta": at the end they may be part of a name ("áo đỏ", "lươn",
# "nước rửa") or a unit ("tạ").
_CLOSING = re.compile(
    r"(?:\s+(?:vay|day|dzay|zay|zi|z|v|a|ak|ah|ha|nhi|nha|nhe|nhen|hen|ne|di|the nhi|roi))+$")
# Calling someone at the start of a question: "em ơi", "ad ơi", "ủa".
_CALLING = re.compile(r"^(?:(?:(?:e|em|a|anh|chi|ad|admin|ban|be|shop) )?oi |(?:ui|ua) )+")
# Month/year with a two-digit year: "12/25", "th 3/26". The data years 2020-2039 are all
# above 12, so "1/26" cannot be a day and a month.
_SHORT_YEAR = re.compile(r"(?<![\d/])(?:\b(?:thang|thg|th|t) ?)?(\d{1,2})/([23]\d)\b(?![\d/])")


def _untranspose(token: str) -> str:
    if len(token) < 4 or not token.isalpha() or token in GRAMMAR_WORDS:
        return token
    fixed = {token[:i] + token[i + 1] + token[i] + token[i + 2:] for i in range(len(token) - 1)
             if token[i] != token[i + 1]} & _TRANSPOSABLE
    return fixed.pop() if len(fixed) == 1 else token


def normalize_question(value: object, *, product_names: Iterable[str] | None = None,
                       as_of_year: int | None = None) -> str:
    """Fold a question for grammar matching: Telex leftovers, abbreviations, date spellings.

    Only for planning. Ingestion and answer matching keep using `fold`/`normalize_answer`,
    and names quoted in a question must still be read from the original text.
    """
    # fold() drops "%", which would turn "bao nhiêu % đơn bị huỷ" into a count question.
    raw = str(value).replace("%", " phần trăm ")
    # fold() also drops "≥": "doanh thu ≥ 580.000 đồng", "mua ≥ 4 đơn" are the rulebook's
    # "từ N trở lên" (gồm N). "≤", "<", ">" stay unread, so those questions abstain.
    raw = re.sub(r"(?:≥|>=|⩾)\s*(\d[\d.,]*)\s*((?:đồng|dong|đ|vnđ|vnd|"
                 r"(?:đơn|don|bill|order)(?: (?:hàng|hang))?(?: (?:khác|khac) nhau)?)(?!\w))?",
                 lambda m: f" từ {m.group(1)} {m.group(2) or ''} trở lên ", raw, flags=re.I)
    # fold() also drops "-": "15-3-2026" / "3-2026" are the same dates as "15/3/2026" / "3/2026".
    raw = re.sub(r"(?<![\d/-])(\d{1,2})-(\d{1,2})-(\d{4})(?![\d/-])", r"\1/\2/\3", raw)
    raw = re.sub(r"(?<![\d/-])(\d{1,2})-(\d{4})(?![\d/-])", r"\1/\2", raw)
    tokens = [_untranspose(_untelex(token)) for token in fold(raw).split()]
    text = " ".join(ABBREVIATIONS.get(token, token) for token in tokens)
    # Central "ni" = "này": "năm ni", "tháng ni", "bữa ni".
    text = re.sub(r"\b(nam|thang|tuan|quy|hom|bua) ni\b", r"\1 nay", text)
    text = re.sub(r"\bbua (nay|qua)\b", r"hom \1", text)
    text = re.sub(r"\bth (truoc|roi|nay|lien truoc)\b", r"thang \1", text)  # "th trước"
    text = re.sub(r"\b(thang|tuan) vua (?:roi|qua)\b", r"\1 truoc", text)
    text = re.sub(r"\bthang roi\b", "thang truoc", text)
    # "tuần rồi" = "tuần trước", also when "rồi" would close the question.
    text = re.sub(r"\btuan roi\b", "tuan truoc", text)
    text = _SHORT_YEAR.sub(lambda m: f"thang {int(m.group(1))}/20{m.group(2)}"
                           if 1 <= int(m.group(1)) <= 12 else m.group(), text)
    # "tháng 8 năm 2025" / "quý 2 năm 2026" -> "tháng 8/2025" / "quý 2/2026"
    text = re.sub(r"\b(thang|quy) (\d{1,2}) nam (\d{4})\b", r"\1 \2/\3", text)
    # Month and quarter abbreviations: "T3/2025", "th3/2025", "thg 3/2025", "Q2/2026".
    text = re.sub(r"\b(?:thg|th|t) ?(\d{1,2})/(\d{4})\b", r"thang \1/\2", text)
    text = re.sub(r"\bq ?([1-4])/(\d{4})\b", r"quy \1/\2", text)
    text = re.sub(r"\bthg\b", "thang", text)
    # A bare "3/2026" (no day before it) is a month: "3/2026 tổng doanh thu". Not inside a
    # list such as "tháng 1, 2 và 3/2026", which the month grammar reads as a whole.
    text = re.sub(r"(?<![\d/])(?<!thang )(?<!quy )(?<!ngay )(?<!\d )(?<!va )"
                  r"\b(\d{1,2})/(\d{4})\b", r"thang \1/\2", text)
    # Same meaning, other words: "lên hay xuống" = "tăng hay giảm", "đem về" = "mang về".
    text = re.sub(r"\b(?:len hay xuong|xuong hay len|giam hay tang)\b", "tang hay giam", text)
    text = re.sub(r"\blen/xuong\b", "tang/giam", text)
    text = re.sub(r"\b(?:dem|mang) (?:ve|de)\b", "mang ve", text)
    # "của quán / của tiệm / quán mình" = "của cửa hàng"; a bare "quan" may be "quần".
    text = re.sub(r"\bcua (?:quan|tiem)\b", "cua cua hang", text)
    text = re.sub(r"\b(?:cua hang|quan|tiem|shop) (?:minh|nha minh|em|toi|ta)\b", "cua hang",
                  text)
    # "tiệm" as the subject: "hôm nay tiệm bán được…".
    text = re.sub(r"\btiem(?= (?:ban|co|dat|nhan|len|thu)\b)", "cua hang", text)
    original = _CLOSING.sub("", _CALLING.sub("", text))
    if product_names is None:
        return original
    return _everyday_wording(text, original, str(value), tuple(product_names), as_of_year)


# Read amounts before folding so a decimal, a sign, or punctuation cannot become a
# different amount. Grouped đồng amounts need at least one thousands separator.
_AMOUNT_BOUNDARY = r"(?<![\w.,/+\-−])(?<![\d.,+\-−] )"
_GROUPED_DONG = re.compile(rf"{_AMOUNT_BOUNDARY}(\d{{1,3}}(?:[. ]\d{{3}})+) ?[đd]\b", re.I)
_MILLIONS = re.compile(rf"{_AMOUNT_BOUNDARY}(\d+) (?:triệu|trieu)(?: (?:đồng|dong|đ|d))?\b"
                       r"(?! +\d)", re.I)
# Parse shorthand in a lower threshold before folding punctuation or diacritics.
_MONEY_THRESHOLD = re.compile(
    r"\b(?:từ|tu) +(\d+)(k| +củ|tr[0-9]) +(?=(?:trở|tro) (?:lên|len|lne)\b)"
)

_ASK_COUNT = r"(?:bao nhieu|bnhiu|bn|nhieu|nhiu|may)"
_STORE_SUBJECT = re.compile(r"\bquan(?= (?:ban|co|duoc|bi|chot|len|thu|dat|da)\b)")
_COMPARISON_CUE = r"(?:tang hay giam|tang giam|thay doi|tang truong|hon hay kem)"
_COLLOQUIAL_ENDING = re.compile(r"\b((?:thế|hè|rứa|nghe|nghen|za)"
                                  r"(?: (?:thế|hè|rứa|nghe|nghen|za))*)\W*$")


def _threshold_dong(match: re.Match[str]) -> str:
    digits, suffix = match.groups()
    if suffix == "k":
        return f"tu {digits}000 dong "
    if suffix.startswith("tr"):
        return f"tu {digits}{suffix[-1]}00000 dong "
    return f"tu {digits}000000 dong "


def _store_subject(text: str, raw: str, names: tuple[str, ...]) -> str:
    if not _STORE_SUBJECT.search(text):
        return text
    spellings = {word for word in re.findall(r"\b\w+\b", raw) if fold(word) == "quan"}
    allowed = {"quán", "quan"} if names else {"quán"}
    if not spellings or spellings - allowed or any("quan" in fold(name).split() for name in names):
        return text
    return _STORE_SUBJECT.sub("cua hang", text)


def _prior_year_reference(text: str, as_of_year: int | None) -> str:
    def replace(match: re.Match[str]) -> str:
        months = re.findall(r"\bthang (\d{1,2})/(\d{4})\b", text[:match.start()])
        if len(months) == 1 and tuple(map(int, months[0])) == (int(match[2]), as_of_year):
            return match[1] + "cung thang nam truoc"
        return match[0]

    return re.sub(r"\b(so (?:voi|vs) )thang (\d{1,2}) nam (?:roi|ngoai)\b", replace, text)


def _closing_wording(text: str, raw: str) -> str:
    text = _CLOSING.sub("", text)
    if re.search(r"\b(?:rồi|roi) ta\W*$", raw):
        return re.sub(r"\broi ta$", "", text).rstrip()
    ending = _COLLOQUIAL_ENDING.search(raw)
    if not ending:
        return text
    suffix = re.escape(fold(ending[1]))
    slot = r"(?:gi|j|nao|phan tram|don(?: hang)?|tien|roi|khong|ko|chua)"
    if re.search(r"\b(?:doanh thu|dthu|tien|don|phan tram)\b", text):
        slot = rf"(?:{slot}|{_ASK_COUNT}|nh(?:at|ut))"
    return re.sub(rf"\b({slot}) {suffix}$", r"\1", text)


def _everyday_wording(text: str, original: str, value: str, names: tuple[str, ...],
                      as_of_year: int | None) -> str:
    raw = unicodedata.normalize("NFC", value).casefold()
    amounts = _GROUPED_DONG.sub(lambda m: m[1].replace(".", " ") + " dong", raw)
    amounts = _MILLIONS.sub(lambda m: f"{int(m[1]):,}".replace(",", " ") + " 000 000 dong",
                            amounts)
    amounts = _MONEY_THRESHOLD.sub(_threshold_dong, amounts)
    if amounts != raw:
        text = normalize_question(amounts)
    text = _store_subject(text, raw, names)
    text = re.sub(r"\btiem(?= (?:duoc|bi|chot|da)\b)", "cua hang", text)
    text = re.sub(rf"\b(?:chot|len) duoc(?= {_ASK_COUNT} don\b)", "ban duoc", text)
    if re.search(rf"\b{_COMPARISON_CUE}\b", text) and not re.search(r"\bsố\b", raw):
        text = re.sub(r"(\b(?:thang|quy) \d{1,2}/\d{4}|\bca (?:sang|chieu)|\bphan tram)"
                      r" so (?=(?:thang|ca|cung|t|th)\b)", r"\1 so voi ", text)
    text = re.sub(rf"\btong (?={_ASK_COUNT}\b)", "", text)
    text = re.sub(r"\btrog\b|^chong(?= (?:thang|quy|tuan|nam|ngay)\b)", "trong", text)
    text = re.sub(r"\btoi (?:chu|gio)\b(?=$| (?:ban|co|da|doanh thu|thu|tong)\b)",
                  "den hom nay", text)
    text = re.sub(r"\bdat doanh thu\b", "co doanh thu", text)
    text = re.sub(rf"\bdoanh thu dat(?= {_ASK_COUNT}\b)", "doanh thu", text)
    text = re.sub(r"^(?:hoi (?=(?:thang|quy|ngay|nam|tuan|hom)\b)"
                  r"|ca (?=(?:thang|quy|nam|tuan)\b))", "", text)
    if re.match(r"^\s*cả ngày hôm\b", raw):
        text = re.sub(r"^ca (?=ngay hom\b)", "", text)
    text = _prior_year_reference(text, as_of_year)
    text = _CALLING.sub("", _closing_wording(text, raw))
    if text != original:
        # The existing entity normalizer still matches names. Keep their spelling and
        # occurrence counts when rewriting the surrounding question.
        for name in names:
            key = normalize_question(name)
            if key and f" {original} ".count(f" {key} ") != f" {text} ".count(f" {key} "):
                return original
    return text


def normalize_answer(value: str) -> str:
    # Answer matching preserves Vietnamese diacritics (unlike question/schema search).
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                            for c in value).split())
