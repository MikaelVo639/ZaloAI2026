import re
import unicodedata


def fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value).lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9/]+", " ", text).split())


# Words the question grammar uses. A Telex-typed token (e.g. "toongr", "dduwowcj") is only
# rewritten when its decoded form is one of these words, so product/customer names that
# merely look like Telex ("max", "gas", "plus") are never altered.
GRAMMAR_WORDS = frozenset("""
    a bao ban bi bill binh biet bn bnhiu bua cao chay cho co cong cua cuoi da dat dau dem den do
    hot ngay
    don dong duoc gan gi gia gium giup ha hang het hien hoi hom huy khach la le luong mang
    mat may minh moi mon mot nam nao nay nhat nhieu nhiu nhung pham phan quan qua quy roi
    san so tai tam thai thang theo thu ti tien toi tong tram trang tren trong truoc trung
    tu tuan ty vay ve xem xet
    cung doi giam hay hon kem ke ky liet lien ngoai tang top truong voi vs
    con huong khong luon nguoi no phai sau tuc tung xu
""".split())
# Whole-token abbreviations and misspellings seen in everyday questions.
ABBREVIATIONS = {
    "hnay": "hom nay", "hqua": "hom qua", "dc": "duoc", "sp": "san pham", "trc": "truoc",
    "revenue": "doanh thu", "total": "tong cong",
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
_CLOSING = re.compile(
    r"(?:\s+(?:vay|day|dzay|zay|z|v|a|ak|ah|ha|nhi|nha|nhe|nhen|hen|ne|ta|di|the nhi))+$")


def _untranspose(token: str) -> str:
    if len(token) < 4 or not token.isalpha() or token in GRAMMAR_WORDS:
        return token
    fixed = {token[:i] + token[i + 1] + token[i] + token[i + 2:] for i in range(len(token) - 1)
             if token[i] != token[i + 1]} & _TRANSPOSABLE
    return fixed.pop() if len(fixed) == 1 else token


def normalize_question(value: object) -> str:
    """Fold a question for grammar matching: Telex leftovers, abbreviations, date spellings.

    Only for planning. Ingestion and answer matching keep using `fold`/`normalize_answer`,
    and names quoted in a question must still be read from the original text.
    """
    # fold() drops "%", which would turn "bao nhiêu % đơn bị huỷ" into a count question.
    raw = str(value).replace("%", " phần trăm ")
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
    return _CLOSING.sub("", text)


def normalize_answer(value: str) -> str:
    # Answer matching preserves Vietnamese diacritics (unlike question/schema search).
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                            for c in value).split())
