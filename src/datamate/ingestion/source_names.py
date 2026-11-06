"""Match documented source names without renaming organizer files.

ZIP entries without the UTF-8 flag may be extracted as CP437 text even when the
filename bytes were UTF-8. Only a reversible, valid decoding is accepted. Callers
must reject collisions rather than merge two sources with the same lookup key.
"""

import unicodedata


def filename_key(name: str) -> str:
    basename = unicodedata.normalize("NFC", name.replace("\\", "/").rsplit("/", 1)[-1])
    try:
        basename = basename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return unicodedata.normalize("NFC", basename)
