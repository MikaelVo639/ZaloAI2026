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


def normalize_question(value: object) -> str:
    """Fold a question for grammar matching: Telex leftovers, abbreviations, date spellings.

    Only for planning. Ingestion and answer matching keep using `fold`/`normalize_answer`,
    and names quoted in a question must still be read from the original text.
    """
    # fold() drops "%", which would turn "bao nhiêu % đơn bị huỷ" into a count question.
    tokens = [_untelex(token) for token in fold(str(value).replace("%", " phần trăm ")).split()]
    text = " ".join(ABBREVIATIONS.get(token, token) for token in tokens)
    text = re.sub(r"\bbua (nay|qua)\b", r"hom \1", text)
    text = re.sub(r"\bth (truoc|roi|nay|lien truoc)\b", r"thang \1", text)  # "th trước"
    # "tháng 8 năm 2025" / "quý 2 năm 2026" -> "tháng 8/2025" / "quý 2/2026"
    text = re.sub(r"\b(thang|quy) (\d{1,2}) nam (\d{4})\b", r"\1 \2/\3", text)
    return text


def normalize_answer(value: str) -> str:
    # Answer matching preserves Vietnamese diacritics (unlike question/schema search).
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                            for c in value).split())
