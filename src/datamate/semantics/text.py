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
    tinh ton tu tuan ty vay ve xem xet
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
    "nhieeu": "nhieu",
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
_CALLING = re.compile(r"^(?:(?:(?:e|em|a|anh|chi|ad|admin|ban|be|shop) )?oi |(?:ui|ua|e{2,}) )+")
# Month/year with a two-digit year: "12/25", "th 3/26". The data years 2020-2039 are all
# above 12, so "1/26" cannot be a day and a month.
_SHORT_YEAR = re.compile(r"(?<![\d/])(?:\b(?:thang|thg|th|t) ?)?(\d{1,2})/([23]\d)\b(?![\d/])")


def _untranspose(token: str) -> str:
    if len(token) < 4 or not token.isalpha() or token in GRAMMAR_WORDS:
        return token
    fixed = {token[:i] + token[i + 1] + token[i] + token[i + 2:] for i in range(len(token) - 1)
             if token[i] != token[i + 1]} & _TRANSPOSABLE
    return fixed.pop() if len(fixed) == 1 else token


# Misspellings told apart by their diacritics, which folding would lose. "tiềng"/"tền" are
# not words, so they are "tiền" (never "tiếng" = hour or "tên" = name); "nhân diên" is
# "nhân viên" (not "điện"), "xố đơn" is "số đơn", "mí đơn" is "mấy đơn".
_MARKED_SLIPS = (
    (r"(?<!\w)(tiềng|tền)(?!\w)", "tiền"),
    (r"(?<!\w)(tiền) báng(?!\w)", r"\1 bán"),
    (r"(?<!\w)(nhân) diên(?!\w)", r"\1 viên"),
    (r"(?<!\w)xố (đơn)(?!\w)", r"số \1"),
    (r"(?<!\w)mí (đơn)(?!\w)", r"mấy \1"),
)


def _marked_slips(raw: str) -> str:
    raw = unicodedata.normalize("NFC", raw)
    for pattern, replacement in _MARKED_SLIPS:
        raw = re.sub(pattern, replacement, raw, flags=re.IGNORECASE)
    return raw


def normalize_question(value: object, *, product_names: Iterable[str] | None = None,
                       as_of_year: int | None = None) -> str:
    """Fold a question for grammar matching: Telex leftovers, abbreviations, date spellings.

    Only for planning. Ingestion and answer matching keep using `fold`/`normalize_answer`,
    and names quoted in a question must still be read from the original text.
    """
    # fold() drops "%", which would turn "bao nhiêu % đơn bị huỷ" into a count question.
    raw = str(value).replace("%", " phần trăm ")
    raw = _marked_slips(raw)
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
    text = re.sub(r"\bla chi$", "la gi", text)  # central "là chi" = "là gì", closing a question
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
    text = re.sub(r"\b(?:thg|th)(?= nay\b)", "thang", text)
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
# Read with their marks: folded, "hỉ", "gồi" (= "rồi"), "zạ", "hả" would be "hi", "goi",
# "za", "ha", and "goi" may be the unit "gói".
_ENDING_WORDS = r"(?:thế|hè|rứa|nghe|nghen|za|hỉ|gồi|nhoa|zạ|dzạ|zị|z|hả|luôn)"
_COLLOQUIAL_ENDING = re.compile(rf"\b({_ENDING_WORDS}(?: {_ENDING_WORDS})*)\W*$")
# Asking to be told, at the start only: "cho em hỏi", "anh xem giúp em", "nhờ bạn tính
# giúp", "hỏi nhanh xíu", "mình muốn biết", "alo". The rest is parsed as usual.
_PRONOUN = r"(?:em|e|minh|toi|tui|anh|chi|ban)"
_LEAD_IN = re.compile(
    rf"^(?:alo|(?:{_PRONOUN} )?(?:cho|cko)(?: {_PRONOUN})? (?:hoi|xin|biet|bit|xem|coi)"
    rf"|(?:{_PRONOUN} )?(?:xem|coi|check|chec) (?:giup|jup|dup)(?: {_PRONOUN})?"
    rf"|nho(?: {_PRONOUN})? (?:xem|coi|check|tinh)(?: (?:giup|jup|dup))?(?: {_PRONOUN})?"
    rf"|hoi nhanh|(?:minh|em|toi|tui) (?:muon|mun) (?:biet|bit|hoi)|ne(?: {_PRONOUN})? hoi)"
    r"(?: (?:xiu|chut|nhanh|nha|nhe|voi))* ")
# The shop: "quán", "tiệm" and chat "wán" as written. Unaccented, "quan" / "tiem" / "wan"
# are the shop only in a shop position and when no catalogue name uses the word
# ("quần", "tiêm", "bình quân" fold the same way).
_SHOP_MARKED = re.compile(r"(?<!\w)(?:quán|tiệm|wán|sốp)(?!\w)")
_SHOP_PLAIN = re.compile(
    r"(?<!\bbinh )(?<!\bthuoc )\b(?:quan|tiem|wan|sop)\b"
    rf"(?= (?:ban|co|duoc|dat|bi|len|chot|thu|nhap|la|da|huy|minh|tong|{_ASK_COUNT})\b|$)"
    r"|(?:(?<=\bdoanh thu )|(?<=\bdthu )|(?<=\bca )|(?<=\bcua ))(?:quan|tiem|wan|sop)\b")


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


def _shop_plain(text: str, raw: str, names: tuple[str, ...]) -> str:
    if not names:
        return text
    occupied = {word for name in names for word in fold(name).split()}
    occupied.update(fold(word) for word in re.findall(r"\b(?:quần|tiêm)\b", raw))
    text = _SHOP_PLAIN.sub(lambda match: match[0] if match[0] in occupied else "cua hang", text)
    return re.sub(r"\bcua hang (?:minh|nha minh|em|toi|ta)\b", "cua hang", text)


def _prior_year_reference(text: str, as_of_year: int | None) -> str:
    def replace(match: re.Match[str]) -> str:
        months = re.findall(r"\bthang (\d{1,2})/(\d{4})\b", text[:match.start()])
        if len(months) == 1 and tuple(map(int, months[0])) == (int(match[2]), as_of_year):
            return match[1] + "cung thang nam truoc"
        return match[0]

    return re.sub(r"\b(so (?:voi|vs) )thang (\d{1,2}) nam (?:roi|ngoai)\b", replace, text)


def _closing_wording(text: str, raw: str) -> str:
    text = _CLOSING.sub("", text)
    if re.search(r"\b(?:doanh thu|dthu)\b", text):
        text = re.sub(r"(?<=\bcua hang) luon$", "", text)
    if re.search(r"\b(?:rồi|roi) ta\W*$", raw):
        return re.sub(r"\broi ta$", "", text).rstrip()
    if re.search(r"\b(?:rồi|rooif) đó\W*$", raw):
        return re.sub(r"\broi do$", "", text).rstrip()
    ending = _COLLOQUIAL_ENDING.search(raw)
    if not ending:
        return text
    suffix = re.escape(fold(ending[1]))
    slot = r"(?:gi|j|nao|phan tram|don(?: hang)?|tien|roi|khong|ko|chua|huy)"
    if re.search(r"\b(?:doanh thu|dthu|tien|don|phan tram)\b", text):
        slot = rf"(?:{slot}|{_ASK_COUNT}|nh(?:at|ut)|doanh thu|dthu|cua hang|\d{{1,2}}/\d{{4}})"
    return re.sub(rf"\b({slot}) {suffix}$", r"\1", text)


# Typing slips in grammar words: a dropped/swapped letter or two words typed without a space.
# A slip is only fixed when none of its words occurs in a catalogue name of the shop.
_SLIPS = (
    (r"\bbna\b", "ban"),                            # "bná"
    (r"\bban roa\b", "ban ra"),                     # "bán roa"
    (r"\bdoanhthu\b", "doanh thu"),
    (r"\bban(nhieu|nhiu)\b", r"ban \1"),            # "bánnhiều"
    (r"\bnhieu(don|tien)\b", r"nhieu \1"),          # "nhiềuđơn"
    (r"\btrung bih\b", "trung binh"),
    (r"\bbin quan\b", "binh quan"),                 # "bìn quân"
    (r"\btog cog\b", "tong cong"),                  # "tổg cộg"
    (r"\bth(\d{1,2})\b(?!/)", r"thang \1"),         # "Th3" without a year
)
# "tháng ba" = "tháng 3". Not "tháng năm" (also "year") or "tháng sau" (also "next month").
_SPOKEN_MONTHS = {"mot": 1, "hai": 2, "ba": 3, "tu": 4, "bay": 7, "tam": 8,
                  "chin": 9, "muoi": 10, "muoi mot": 11, "muoi hai": 12}


def _typing_slips(text: str, occupied: set[str]) -> str:
    for pattern, replacement in _SLIPS:
        text = re.sub(pattern, lambda m: m[0] if (set(m[0].split()) - GRAMMAR_WORDS) & occupied
                      else m.expand(replacement), text)
    spoken = "|".join(sorted(_SPOKEN_MONTHS, key=len, reverse=True))
    text = re.sub(rf"\bthang ({spoken})\b(?! ?[\d/])",
                  lambda m: f"thang {_SPOKEN_MONTHS[m[1]]}", text)
    # "mua nhiều lần nhất, đếm theo số đơn" names the measure: most orders.
    text = re.sub(r"\bmua nhieu lan nh(at|ut)(?: nhi)? dem theo so don(?: ay| nhe| nha)?$",
                  r"mua nhieu don nh\1", text)
    # An unaccented closing "the" (= "thế") after the amount of an average order; in other
    # questions "bao nhiêu the" may still be the unit "thẻ".
    if re.search(r"\b(?:trung binh|binh quan)\b.*\b(?:mot|moi|1) don\b", text):
        text = re.sub(rf"\b({_ASK_COUNT}) the$", r"\1", text)
    # "mấy o đờ" = "mấy order" (already read after "một"/"mỗi").
    text = re.sub(rf"\b({_ASK_COUNT}) o do\b", r"\1 order", text)
    text = re.sub(rf"\b(?:chot|len) duoc(?= (?:tong cong )?{_ASK_COUNT} order\b)", "ban duoc",
                  text)
    # Word order: "bán tổng cộng được", "bán tháng 7/2025 được", "bán chạy nhất là hàng nào".
    text = re.sub(r"\bban tong cong duoc\b", "ban duoc tong cong", text)
    text = re.sub(r"\bban (thang \d{1,2}/\d{4}|quy [1-4]/\d{4}|(?:thang|tuan) (?:nay|truoc)"
                  r"|hom (?:nay|qua)) duoc\b", r"\1 ban duoc", text)
    text = re.sub(r"\bban chay nh(at|ut) la (mat hang|san pham|mon|hang) nao$",
                  r"\2 nao ban chay nh\1", text)
    return text


def _everyday_wording(text: str, original: str, value: str, names: tuple[str, ...],
                      as_of_year: int | None) -> str:
    raw = unicodedata.normalize("NFC", value).casefold()
    amounts = re.sub(rf"\bkó(?= {_ASK_COUNT} (?:đơn|don)\b)", "có", raw)
    amounts = _GROUPED_DONG.sub(lambda m: m[1].replace(".", " ") + " dong", amounts)
    amounts = _MILLIONS.sub(lambda m: f"{int(m[1]):,}".replace(",", " ") + " 000 000 dong",
                            amounts)
    amounts = _MONEY_THRESHOLD.sub(_threshold_dong, amounts)
    occupied = {word for name in names for word in fold(name).split()}
    amounts = _SHOP_MARKED.sub(
        lambda match: match[0] if fold(match[0]) in occupied else "cửa hàng", amounts)
    if amounts != raw:
        text = normalize_question(amounts)
    text = _store_subject(text, raw, names)
    text = _shop_plain(text, raw, names)
    text = re.sub(r"\b(?:doanh thu|dthu) bq(?= (?:moi|mot|1) don\b)",
                  "doanh thu binh quan", text)
    text = re.sub(r"\b(binh quan|trung binh) chen(?= (?:moi|mot|1) don\b)", r"\1 tren", text)
    text = re.sub(r"\b(moi|mot|1) o do\b", r"\1 order", text)
    text = re.sub(rf"\b((?:doanh thu|dthu)(?: cua cua hang)?) ra(?= {_ASK_COUNT}\b)",
                  r"\1", text)
    text = re.sub(r"\btiem(?= (?:duoc|bi|chot|da)\b)", "cua hang", text)
    text = re.sub(rf"\b(?:chot|len) duoc(?= (?:tong cong )?{_ASK_COUNT} don\b)", "ban duoc", text)
    if re.search(rf"\b{_COMPARISON_CUE}\b", text) and not re.search(r"\bsố\b", raw):
        text = re.sub(r"(\b(?:thang|quy) \d{1,2}/\d{4}|\bca (?:sang|chieu)|\bphan tram)"
                      r" so (?=(?:thang|ca|cung|t|th)\b)", r"\1 so voi ", text)
    text = re.sub(rf"\btong (?={_ASK_COUNT}\b)", "", text)
    # "tính tất cả bao nhiêu / doanh thu", "thu về / mang về bao nhiêu doanh thu".
    text = re.sub(rf"\b(?:tinh )?tat ca (?=(?:{_ASK_COUNT}|doanh thu|dthu)\b)", "", text)
    text = re.sub(rf"\b(?:thu duoc|(?:thu|mang) ve) (?=(?:{_ASK_COUNT}) (?:doanh thu|dthu)\b)",
                  "", text)
    text = re.sub(r"\btrog\b|^chong(?= (?:thang|quy|tuan|nam|ngay)\b)", "trong", text)
    text = re.sub(r"\btoi (?:chu|gio)\b(?=$| (?:ban|co|da|doanh thu|thu|tong)\b)",
                  "den hom nay", text)
    text = re.sub(r"\bdat doanh thu\b", "co doanh thu", text)
    text = re.sub(rf"\bdoanh thu dat(?= {_ASK_COUNT}\b)", "doanh thu", text)
    text = re.sub(r"^(?:hoi (?=(?:thang|quy|ngay|nam|tuan|hom)\b)"
                  r"|ca (?=(?:thang|quy|nam|tuan)\b))", "", text)
    if re.match(r"^\s*cả ngày hôm\b", raw):
        text = re.sub(r"^ca (?=ngay hom\b)", "", text)
    if re.search(r"\bmí\s*(?:%|phần trăm)", raw):
        text = re.sub(r"\bmi (?=phan tram\b)", "bao nhieu ", text)
    text = re.sub(rf"\btang dam (?={_ASK_COUNT} phan tram\b)", "tang giam ", text)
    text = re.sub(r"\bxo voi (?=(?:cung ky |cung )?thang\b)", "so voi ", text)
    text = re.sub(r"\bcaonhat\b", "cao nhat", text)
    text = _typing_slips(text, occupied)
    text = _prior_year_reference(text, as_of_year)
    text = _CALLING.sub("", _closing_wording(text, raw))
    while True:
        shorter = _CALLING.sub("", _LEAD_IN.sub("", text))
        if shorter == text:
            break
        text = shorter
    if text != original:
        # The existing entity normalizer still matches names. Keep their spelling and
        # occurrence counts when rewriting the surrounding question.
        words = normalize_answer(value).split()
        for name in names:
            key = normalize_question(name)
            if key and f" {original} ".count(f" {key} ") != f" {text} ".count(f" {key} "):
                width = len(key.split())
                occurrences = [" ".join(words[i:i + width]) for i in range(len(words) - width + 1)
                               if fold(" ".join(words[i:i + width])) == key]
                if (fold(name) != key or not occurrences
                        or any(term == normalize_answer(name) or term == fold(term)
                               for term in occurrences)):
                    return original
    return text


def normalize_answer(value: str) -> str:
    # Answer matching preserves Vietnamese diacritics (unlike question/schema search).
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                            for c in value).split())
