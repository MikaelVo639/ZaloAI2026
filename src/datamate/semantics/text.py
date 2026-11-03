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
    a bao ban bill biet bn bnhiu bua chay cho co cong cua cuoi dau den do don dong duoc
    gan gi gium giup ha hang het hien hoi hom khach la luong mat may minh moi mon nam nao
    nay nhat nhieu nhiu nhung pham qua quy roi san so tai thang thu tien toi tong trong
    truoc tu tuan vay ve xem
""".split())
# Whole-token abbreviations and misspellings seen in everyday questions.
ABBREVIATIONS = {
    "hnay": "hom nay", "hqua": "hom qua", "dc": "duoc", "sp": "san pham",
    "revenue": "doanh thu", "total": "tong cong",
}
_TONE_KEYS = "sfrxj"


def _untelex(token: str) -> str:
    """Decode Telex keystrokes left in a folded token: dd, aa/ee/oo, aw/ow/uw, tone key."""
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
    tokens = [_untelex(token) for token in fold(value).split()]
    text = " ".join(ABBREVIATIONS.get(token, token) for token in tokens)
    text = re.sub(r"\bbua (nay|qua)\b", r"hom \1", text)
    # "tháng 8 năm 2025" / "quý 2 năm 2026" -> "tháng 8/2025" / "quý 2/2026"
    text = re.sub(r"\b(thang|quy) (\d{1,2}) nam (\d{4})\b", r"\1 \2/\3", text)
    return text


def normalize_answer(value: str) -> str:
    # Answer matching preserves Vietnamese diacritics (unlike question/schema search).
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                            for c in value).split())
