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
    # Chat spellings: "mik", "kua", "jum", "đh" (đơn hàng).
    "mik": "minh", "mk": "minh", "kua": "cua", "dh": "don hang",
    # "giùm", "dùm", "jum" = "giúp".
    "gium": "giup", "dum": "giup", "jum": "giup",
    # Chat spellings of grammar words: "trog", "dza". ("nhứt", "ko" only in context below:
    # "Nhựt" is a person's name.)
    "trog": "trong", "dza": "vay",
    # Every grammar pattern that reads "dthu" also reads "doanh thu".
    "dthu": "doanh thu",
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
    r"(?:\s+(?:vay|day|dzay|zay|zi|z|v|a|ak|ah|ha|nhi|nha|nhe|nhen|hen|ne|di|the nhi|roi"
    r"|cam on(?: nhieu)?|thanks?(?: you)?|tks|thx))+$")
# Calling someone at the start of a question: "em ơi", "ad ơi", "ủa".
_CALLING = re.compile(r"^(?:(?:(?:e|em|a|anh|chi|ad|admin|ban|be|shop) )?oi |(?:ui|ua|e{2,}) )+")
# Greetings and "cho … hỏi" opening a question: "alo", "hello shop", "dạ cho em hỏi".
# "dạ", "chào", "hi" only before whom they address: "Đá xay", "Cháo gà" are product names
# and go through this normalisation too.
_GREETING = re.compile(
    r"^(?:(?:alo|a lo|hello|xin chao)(?: (?:shop|ad|admin|em|anh|chi|ban))? "
    r"|(?:hi|chao|da) (?:(?:shop|ad|admin|em|anh|chi|ban) |(?=cho )))+")
_ASKING = re.compile(r"^(?:cho (?:em|e|minh|toi|tui|chi|anh|con) hoi )+")
# Month/year with a two-digit year: "12/25", "th 3/26". The data years 2020-2039 are all
# above 12, so "1/26" cannot be a day and a month.
_SHORT_YEAR = re.compile(r"(?<![\d/])(?:\b(?:thang|thg|th|t) ?)?(\d{1,2})/([23]\d)\b(?![\d/])")


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
    # fold() also drops "≥": "doanh thu ≥ 580.000 đồng", "mua ≥ 4 đơn" are the rulebook's
    # "từ N trở lên" (gồm N). "≤", "<", ">" stay unread, so those questions abstain.
    raw = re.sub(r"(?:≥|>=|⩾)\s*(\d[\d.,]*)\s*((?:đồng|dong|đ|vnđ|vnd|"
                 r"(?:đơn|don|đh|dh|bill|order)(?: (?:hàng|hang))?(?: (?:khác|khac) nhau)?)(?!\w))?",
                 lambda m: f" từ {m.group(1)} {m.group(2) or ''} trở lên ", raw, flags=re.I)
    # fold() also drops "-": "15-3-2026" / "3-2026" are the same dates as "15/3/2026" / "3/2026".
    # The shop as the subject or owner, read with accents ("quán" is never "quần", "quận"):
    # "quán bán được…", "doanh thu quán mình…", "doanh thu cả quán". Unaccented "quan"
    # stays: it may be a product ("quần").
    raw = re.sub(r"(?<!\w)(?:[Cc]ả )?(?:[Qq]uán|[Tt]iệm)(?= (?:bán|có|nhập|thu|đạt|nhận|chốt)\b)",
                 "cửa hàng", raw)
    raw = re.sub(r"(?<!\w)(?:doanh thu|dthu) (?:của )?cả (?:quán|tiệm|shop|cửa hàng)"
                 r"(?: (?:mình|nhà mình|em|tôi|ta))?(?!\w)", "tổng doanh thu", raw)
    # "doanh thu tiệm mình được bao nhiêu": the shop owns the revenue, "được" stays with it.
    raw = re.sub(r"(?<!\w)((?:[Dd]oanh thu|dthu) )(?:của )?(?:quán|tiệm|shop|bên)"
                 r"(?: (?:mình|nhà mình|em|tôi|ta))?(?= (?:được|đươc|đc|dc|duoc)(?!\w))",
                 r"\1của cửa hàng", raw)
    # A month moved after the question ("…, trong tháng 3/2026?") reads as the leading period.
    raw = re.sub(r"^(.+?),\s*trong\s+(tháng\s+\d{1,2}/\d{4})"
                 r"((?:\s+(?:nhỉ|nhé|nha|vậy|hả|ạ|dậy|z|ha|á|nhá))?\s*[?.!]*)$", r"\2 \1\3", raw,
                 flags=re.I)
    # The shop as an object or subject: "thiếu tiền quán", "chi tiền cho quán", "quán còn …".
    raw = re.sub(r"(?<!\w)(tiền|cho) (?:quán|tiệm)(?!\w)", r"\1 cửa hàng", raw)
    raw = re.sub(r"(?<!\w)(?:[Qq]uán|[Tt]iệm)(?= còn(?!\w))", "cửa hàng", raw)
    raw = re.sub(r"(?<!\w)((?:[Dd]oanh thu|dthu) )(?:của )?(?:quán|tiệm|shop|bên)"
                 r"(?: (?:mình|nhà mình|em|tôi|ta))?(?!\w)"
                 # "doanh thu (của quán) được" must not become "thu được" (debt collected).
                 r"(?! (?:được|đươc|đc|dc|duoc|thu)(?!\w))", r"\1", raw)
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
    # Same measure, other words: "(số) doanh thu ghi nhận", "bình quân" (not of purchases),
    # "tính cho một đơn",
    # "lên hay xuống", southern "nhứt".
    text = re.sub(r"\b(?:so )?doanh thu ghi nhan\b", "doanh thu", text)
    if not re.search(r"\bnhap\b", text):  # "giá nhập bình quân" is its own measure (§5)
        text = re.sub(r"\bbinh quan\b", "trung binh", text)
    text = re.sub(r"\btinh cho (mot|moi) don\b", r"\1 don", text)
    text = re.sub(r"\blen hay xuong\b", "tang hay giam", text)
    # "nhứt" after a measure word ("nhiều đơn hàng nhứt"); "Nhựt" alone is a name.
    text = re.sub(r"\b((?:nhieu|it|cao|thap|chay|lon|nho|hot|dat|re|manh|tot|gioi|kem|lau|gan|moi|dong)"
                  r"(?: \w+){0,2}) nhut\b", r"\1 nhat", text)
    # "tuần rồi" = "tuần trước", also when "rồi" would close the question.
    text = re.sub(r"\btuan roi\b", "tuan truoc", text)
    text = _SHORT_YEAR.sub(lambda m: f"thang {int(m.group(1))}/20{m.group(2)}"
                           if 1 <= int(m.group(1)) <= 12 else m.group(), text)
    text = re.sub(r"\bthg(?= \d)", "thang", text)  # "thg 3 năm 2026" before the line below
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
    text = re.sub(r"\btiem(?= (?:ban|co|dat|nhan|len|thu|nhap|bo ra)\b)", "cua hang", text)
    # Owing the shop: "nợ quán", "công nợ với tiệm" ("quán" alone may be "quần" elsewhere).
    text = re.sub(r"\b(no|voi) (?:quan|tiem)\b", r"\1 cua hang", text)
    # "bên mình" is the shop speaking of itself.
    text = re.sub(r"\bben (?:minh|em|toi)\b", "cua hang", text)
    # The shop between the revenue word and "trung bình" adds nothing: "doanh thu (của) cửa
    # hàng trung bình một đơn". Elsewhere it stays: "doanh thu được" would read "thu được"
    # (money collected from debtors).
    text = re.sub(r"\b(doanh thu|dthu) (?:cua )?cua hang (?=trung binh|binh quan)", r"\1 ",
                  text)
    # Before what is sold the shop adds nothing either: "doanh thu cửa hàng thuốc kê đơn".
    text = re.sub(r"\b(doanh thu) (?:cua )?cua hang (?=thuoc|hang|nhom|mat hang|san pham|mon"
                  r"|khach|nhan vien|ca|cac don|don|thang|quy|nam|tuan|ngay|hom|tet|tu|trong"
                  r"|la\b|chiem|bang)", r"\1 ", text)
    # "bình quân một đơn" = "trung bình một đơn" (order value only: "giá nhập bình quân" is
    # the quantity-weighted purchase price, its own grammar).
    text = re.sub(r"\bbinh quan(?= (?:tren )?(?:mot|moi|1) (?:don|bill|order|hoa don)\b)"
                  r"|(?<=\bgia tri )binh quan\b", "trung binh", text)
    # "cao nhứt", "nhiều nhứt": southern "nhứt" after a comparison; "… ko" = "… không".
    text = re.sub(r"\b(cao|nhieu|it|lon|thap|chay|nho|re|dat|tot|kem|manh|nhanh|tien|don|lan"
                  r"|luong|gan|loi|han|nhiu|som|muon|lau) nhut\b",
                  r"\1 nhat", text)
    text = re.sub(r"\bko(?= (?:co|duoc|phai)\b)|\bko$", "khong", text)
    text = _standard_wording(text)
    for _ in range(3):  # greetings, calls and "cho em hỏi" may follow one another
        text = _ASKING.sub("", _CALLING.sub("", _GREETING.sub("", text)))
    text = _CLOSING.sub("", text)
    return re.sub(r"\bla chi$", "la gi", text)  # "là chi z" -> "là chi" -> "là gì"


# ---------------------------------------------------------------- standard wordings

# The rewrite model restates a question in words close to, but not the same as, the
# standard restatements its prompt teaches (`EXAMPLES` in agent/rewrite.py). Collected from
# the model's restatements of TRAIN questions (LOCAL PROXY model benchmark): each rule maps
# one recurring wording onto the standard one, with the same measure, period and condition.
_P = (r"(?:thang \d{1,2}/\d{4}|quy \d/\d{4}|nam \d{4}|tuan (?:truoc|nay)|thang (?:nay|truoc)"
      r"|hom (?:nay|qua)|ngay \d{1,2}/\d{1,2}/\d{4}|\d+ ngay gan nhat)")
_D = r"\d{1,2}/\d{1,2}/\d{4}"
_ORDER = r"(?:don hang|don|hoa don)"
# "kèm số tiền còn nợ (từng khách)", "mỗi người còn nợ bao nhiêu": the amounts and nothing
# more ("… và hạn trả" asks a column the table does not have).
_DEBT_AMOUNT = (r"(?: la ai)?(?: va| kem(?: theo)?| cung(?: voi)?| moi (?:nguoi|khach))? (?:so tien |so )?"
                r"(?:(?:ho )?con no|no)(?: (?:cua )?(?:tung khach|ho|tung nguoi))?"
                r"(?: (?:la )?(?:bao nhieu(?: tien)?|gi|nhu the nao))?$")
_STANDARD: tuple[tuple[str, str], ...] = (
    # Same words, other spellings.
    (r"\btuan qua\b", "tuan truoc"),
    (r"\bthang hien tai\b", "thang nay"),
    (r"\b(\d+) ngay gan day\b", r"\1 ngay gan nhat"),
    (r"\bhet ngay hom nay\b", "het hom nay"),
    (r"\btang hoac giam\b", "tang hay giam"),
    (r"\bhan su dung\b", "han dung"),
    (r"\bden het (" + _D + r")\b", r"den het ngay \1"),
    (r"\bnv ban gioi nhat\b", "nhan vien ban gioi nhat"),
    # Revenue "(được) ghi nhận (qua cửa hàng)", "của cửa hàng" before "là".
    (r"\b(doanh thu\b.*?) (?:duoc )?ghi nhan(?: qua cua hang)?\b", r"\1"),
    (r"\b(doanh thu\b.*?) cua cua hang(?= la\b)", r"\1"),
    # Order value: "doanh thu tháng X (có giá trị) trung bình mỗi đơn hàng",
    # "doanh thu / giá trị trung bình của một đơn hàng trong tháng X".
    (r"\bdoanh thu (" + _P + r") (?:co )?(?:gia tri )?trung binh (?:tren )?(?:moi|mot) "
     + _ORDER + r"\b", r"gia tri trung binh mot don \1"),
    (r"\bdoanh thu (" + _P + r") trung binh bao nhieu tien(?: hang)? (?:tren )?(?:moi|mot) "
     + _ORDER + r"\b", r"gia tri trung binh mot don \1 la bao nhieu"),
    (r"\b(?:doanh thu|gia tri) trung binh (?:cua |tren )?(?:moi|mot) " + _ORDER
     + r" (?:trong )?(" + _P + r")\b", r"gia tri trung binh mot don \1"),
    (r"\bgia tri trung binh (?:moi|mot) don (" + _P + r") mang ve bao nhieu doanh thu\b",
     r"gia tri trung binh mot don \1 la bao nhieu"),
    # Best day.
    (r"\bngay nao trong (" + _P + r") (?:cua hang )?(?:ban duoc )?doanh thu (?:dat )?cao nhat\b",
     r"ngay nao trong \1 co doanh thu cao nhat"),
    (r"\bngay nao trong (" + _P + r") hang hoa ban duoc nhieu tien nhat\b",
     r"ngay nao trong \1 co doanh thu cao nhat"),
    # Rankings: supplier, staff, customers, products, gross profit.
    (r"\b(" + _P + r") nha cung cap nao (?:nhap hang voi|co) tong gia tri (?:nhap hang )?"
     r"(?:cao|lon) nhat\b", r"\1 cua hang nhap nhieu tien hang nhat tu nha cung cap nao"),
    (r"\bnhan vien (?:ban hang )?(?:nao )?co doanh thu cao nhat\b", "nhan vien ban gioi nhat"),
    # Not "mua nhiều lần nhất" without "tính theo số đơn": the shop's rule decides.
    (r"\bkhach(?: hang)? (?:nao )?(?:da )?(?:mua (?:hang )?(?:tong tien cao nhat|nhieu tien nhat)"
     r"|chi tieu nhieu nhat)\b", "khach mua nhieu tien nhat"),
    (r"\bhang hoa nao ban chay nhat\b", "mat hang nao ban chay nhat"),
    (r"\btop (\d+) san pham ban chay nhat\b", r"\1 mat hang ban chay nhat"),
    (r"\b(?:mat )?hang nao co (?:tong )?loi nhuan (cao|thap) nhat\b",
     r"hang nao co tong lai gop \1 nhat"),
    # Purchases, stock value, deliveries.
    (r"\bcua hang bo ra tong cong bao nhieu tien nhap hang\b",
     "tong gia tri cac phieu nhap hang la bao nhieu"),
    (r"\btong gia tri hang nhap vao (" + _P + r")\b", r"\1 tong gia tri cac phieu nhap hang"),
    (r"\b(?:tinh toan theo gia von )?(?:tong )?(?:tien hang nam trong kho|tien hang ton kho"
     r"|so hang (?:con )?(?:trong kho|ton kho)) vao (?:cuoi |het )?ngay (" + _D
     + r")(?: (?:tinh )?theo gia von)?\b", r"tien hang nam trong kho vao cuoi ngay \1 theo gia von"),
    (r"\bcua hang (?:da )?giao hang tong cong bao nhieu dot\b",
     "cua hang giao bao nhieu dot hang cho khach"),
    (r"\bso luong dot giao hang trong (" + _P + r") la bao nhieu\b",
     r"\1 cua hang giao bao nhieu dot hang cho khach"),
    # Unsold items over a span of days.
    (r"\b(?:trong )?(?:khoang thoi gian |giai doan )?tu (?:ngay )?(" + _D + r") den (?:het )?(?:ngay )?("
     + _D + r") (?:cua hang )?(?:co )?(?:tong cong )?(?:bao nhieu|may) mat hang (?:bi )?e\b(?: han)?",
     r"tu ngay \1 den het ngay \2 co may mat hang e han"),
    # Revenue comparisons and shares.
    (r"\bdoanh thu (" + _P + r") so voi (" + _P + r") (?:tang hay giam|thay doi) bao nhieu "
     r"phan tram\b", r"doanh thu \1 thay doi bao nhieu phan tram so voi \2"),
    (r"\bdoanh thu ca chieu (" + _P + r") (?:thay doi|tang hay giam) bao nhieu phan tram so voi "
     r"ca sang\b", r"\1 doanh thu ca chieu so voi ca sang chenh bao nhieu phan tram"),
    (r"\bdoanh thu (" + _P + r") ca chieu (?:thay doi|tang hay giam) bao nhieu phan tram so voi "
     r"ca sang\b", r"\1 doanh thu ca chieu so voi ca sang chenh bao nhieu phan tram"),
    (r"\bdoanh thu ca chieu (" + _P + r") so voi ca sang (?:tang hay giam|thay doi) bao nhieu "
     r"phan tram\b", r"\1 doanh thu ca chieu so voi ca sang chenh bao nhieu phan tram"),
    (r"\b(?:doanh thu (?:ban |tu viec ban )?)?thuoc (khong )?ke don (?:ban duoc )?chiem bao nhieu "
     r"phan tram (?:tong )?doanh thu(?: cua (?:nha thuoc|cua hang)| tong the)?(?: trong)? (" + _P + r")\b",
     r"thuoc \1ke don chiem bao nhieu phan tram doanh thu cua nha thuoc \2"),
    (r"\bdoanh thu (?:ban |tu viec ban )?thuoc (khong )?ke don (?:trong )?(" + _P
     + r") chiem bao nhieu phan tram tong doanh thu\b",
     r"thuoc \1ke don chiem bao nhieu phan tram doanh thu cua nha thuoc \2"),
    (r"\bdoanh thu (?:nha thuoc )?(?:ban |tu viec ban )?thuoc (khong )?ke don (?:trong )?(" + _P
     + r")(?: la bao nhieu)?$", r"thuoc \1ke don \2 ban duoc tong cong bao nhieu tien"),
    (r"\bdoanh thu hang hoa khong ke don (?:trong )?(" + _P + r")(?: la bao nhieu)?$",
     r"thuoc khong ke don \1 ban duoc tong cong bao nhieu tien"),
    (r"\bthuoc (khong )?ke don nha thuoc\b", r"thuoc \1ke don"),
    (r"^(.+?) ban duoc bao nhieu tien trong (" + _P + r")$",
     r"\1 \2 ban duoc tong cong bao nhieu tien"),
    (r"^doanh thu tu (thang \d{1,2}/\d{4}) den (thang \d{1,2}/\d{4}) co (?:luon cao hon thang "
     r"truoc|tang len moi thang|tang lien tuc) khong$",
     r"tu \1 den \2 doanh thu thang sau co luon cao hon thang truoc khong"),
    # Net profit after every cost.
    (r"^lai rong (" + _P + r") sau khi tru het chi phi la bao nhieu$",
     r"loi nhuan rong \1 la bao nhieu"),
    (r"^(" + _P + r") con lai rong bao nhieu sau khi tru het chi phi$",
     r"loi nhuan rong \1 la bao nhieu"),
    # Counts with a threshold, orders by shift.
    (r"\bco bao nhieu mat hang duoc mua tu (\d+) don(?: hang)? khac nhau tro len\b",
     r"co bao nhieu mat hang co mat o tu \1 don hang khac nhau tro len"),
    (r"\bhay dem so khach da mua tu (\d+) don tro len\b", r"co bao nhieu khach mua tu \1 don tro len"),
    (r"\bco bao nhieu don hang co gia tri tu ([\d.]+ dong) tro len\b",
     r"co bao nhieu don co doanh thu tu \1 tro len"),
    (r"\bso don (ca (?:sang|chieu|toi)) co bao nhieu " + _ORDER + r"\b", r"\1 co bao nhieu don hang"),
    (r"\bco bao nhieu hoa don bi huy\b", "co bao nhieu don bi huy"),
    # Cups of one size over every drink.
    (r"^(" + _P + r") (?:tong so luong ly size (\w+) ban duoc la bao nhieu|tong cong cua hang ban "
     r"duoc bao nhieu ly size (\w+)) (?:tinh|cong) tat ca cac (?:mat hang|mon)$",
     r"trong \1 so ly size \2\3 ban duoc la bao nhieu tinh moi loai do uong"),
    # Stock: "kiểm tra số lượng tồn kho X ..." is "tồn kho X ...".
    (r"^kiem tra so luong ton kho\b", "ton kho"),
    (r"^(?:tinh )?den het ngay (" + _D + r") (cua hang co bao nhieu mat hang dang het hang)$",
     r"cuoi ngay \1 \2"),
    # Debts: top debtors, age buckets, debts older than N days, money collected.
    (r"^(?:tinh )?den het ngay (" + _D + r") (?:liet ke )?(\d+) khach(?: hang)? (?:nao )?(?:dang )?"
     r"(?:co cong no lon nhat|no nhieu nhat)" + _DEBT_AMOUNT,
     r"tinh den het ngay \1 \2 khach nao dang no nhieu nhat moi nguoi con no bao nhieu"),
    (r"^(?:top |liet ke |danh sach |bang )?(\d+) khach(?: hang)? (?:co cong no lon nhat|no nhieu nhat)"
     r" (?:tinh )?den het ngay (" + _D + r")" + _DEBT_AMOUNT,
     r"tinh den het ngay \2 \1 khach nao dang no nhieu nhat moi nguoi con no bao nhieu"),
    (r"^(?:tinh )?den het ngay (" + _D + r") (?:so tien con no (?:duoc )?phan loai theo tuoi no"
     r"|phan tuoi no con lai thanh 3 nhom) 0 7 ngay 8 15 ngay(?: va)? tren 15 ngay"
     r"(?: (?:la bao nhieu|moi nhom (?:co tong tien )?(?:la )?bao nhieu(?: tien)?))?$",
     r"tinh den het ngay \1 khach con no bao nhieu theo nhom tuoi no 0 7 ngay 8 15 ngay va tren 15 ngay"),
    (r"^\b(doanh thu (?:cua hang )?)?(" + _P + r") tu (?:viec )?khach tra no la bao nhieu$",
     r"\2 cua hang thu duoc bao nhieu tien khach tra no"),
)
_STANDARD_RULES = tuple((re.compile(pattern), repl) for pattern, repl in _STANDARD)


def _standard_wording(text: str) -> str:
    for pattern, repl in _STANDARD_RULES:
        if "theo gia von" in repl and not re.search(r"\bgia von\b", text):
            continue  # stock at cost only when the question says "giá vốn"
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_answer(value: str) -> str:
    # Answer matching preserves Vietnamese diacritics (unlike question/schema search).
    value = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " "
                            for c in value).split())


# ---------------------------------------------------------------- openings

_CUT = re.compile(r"[.!:]\s+")
# Words that carry a question or a condition. An opening holding any of them is part of the
# question, never small talk ("So doanh thu … tháng này: mỗi bên bao nhiêu?"). Read with
# accents when the opening has them: "gom số liệu" is not "gồm", "bản tổng hợp" not "bán".
_KEPT_WORDS = (
    "bao nhiêu|bn|bnhiu|nhiêu|nhiều|ít|nào|mấy|không|ko|gì|chưa|hay|liệt kê|so sánh|so với"
    "|doanh thu|dthu|lời|lãi|nợ|tồn|nhập|bán|mua|trả|thu|chi|giá|tiền|đơn|khách|nhóm|kênh"
    "|mặt hàng|sản phẩm|món|thuốc|ca|ngày|hôm|tuần|tháng|quý|năm|tết|riêng|trừ|loại|gồm"
    "|kể cả|tính|ngoài|online|offline|trực tiếp|tại quầy|chuyển khoản|tiền mặt|thẻ|ví")
_KEPT_ACCENTED = re.compile(rf"(?<!\w)(?:{_KEPT_WORDS})(?!\w)")
_KEPT_FOLDED = re.compile(
    rf"(?<!\w)(?:{'|'.join(sorted({fold(word) for word in _KEPT_WORDS.split('|')}))})(?!\w)")


def _kept_opening(opening: str) -> bool:
    lowered = opening.lower()
    if lowered != fold(lowered):  # typed with accents
        return bool(_KEPT_ACCENTED.search(lowered))
    return bool(_KEPT_FOLDED.search(lowered))


def opening_cuts(question: str, names: tuple[str, ...] = ()) -> list[str]:
    """The question without its leading small talk, shortest cut first.

    An opening is the text before a ". ", "! " or ": " that has no digit, no question
    word, no measure, period or filter word and no catalogue name: "Mình đang gom số liệu
    cho báo cáo nội bộ. Nhờ tra giúp câu này: …". Only for planning a question the grammar
    cannot read whole; a cut that still does not plan is ignored."""
    cuts = []
    for match in _CUT.finditer(question):
        opening, rest = question[:match.start()], question[match.end():].strip()
        folded = fold(opening)
        if re.search(r"\d", opening) or _kept_opening(opening):
            break
        if any(len(name) >= 4 and f" {name} " in f" {folded} " for name in names):
            break
        if len(rest.split()) >= 3:
            cuts.append(rest)
    return cuts
