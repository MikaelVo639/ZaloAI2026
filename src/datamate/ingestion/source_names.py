"""Match documented source names without renaming organizer files.

ZIP entries without the UTF-8 flag may be extracted as CP437 text even when the
filename bytes were UTF-8. Only a reversible, valid decoding is accepted. Callers
must reject collisions rather than merge two sources with the same lookup key.

A README may name a family of files with placeholders (``Ban_T<tháng>_<năm>.xlsx``: one
file per month, same columns). A placeholder for a date part matches digits only, so a
family never swallows a differently named file of the same prefix.
"""

import re
import unicodedata

from datamate.semantics.text import fold

_DATE_PARTS = frozenset("thang nam ngay quy ky tuan month year day quarter week mm yyyy yy dd"
                        .split())


def filename_key(name: str) -> str:
    basename = unicodedata.normalize("NFC", name.replace("\\", "/").rsplit("/", 1)[-1])
    try:
        basename = basename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return unicodedata.normalize("NFC", basename)


def source_pattern(name: str) -> re.Pattern[str] | None:
    """Regex for a README file name with ``<…>`` placeholders; ``None`` for a plain name."""
    parts = re.split(r"(<[^<>/]+>)", filename_key(name))
    if len(parts) == 1:
        return None
    regex = ""
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            words = set(fold(part[1:-1]).split())
            regex += r"\d{1,4}" if words & _DATE_PARTS else r"[^/]+?"
        else:
            regex += re.escape(part)
    return re.compile(regex)


def is_family(name: str) -> bool:
    return source_pattern(name) is not None


def matches_source(documented: str, filename: str) -> bool:
    """Whether ``filename`` is the README source ``documented`` (or one of its family)."""
    pattern = source_pattern(documented)
    key = filename_key(filename)
    return key == filename_key(documented) if pattern is None else bool(pattern.fullmatch(key))
