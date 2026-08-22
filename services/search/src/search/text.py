from __future__ import annotations

import re
import unicodedata

_CHARACTER_TRANSLATION = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ة": "ه",
        "أ": "ا",
        "إ": "ا",
        "ٱ": "ا",
        "ـ": "",
    }
)
_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "،": ",",
        "؛": ";",
        "؟": "?",
        "٪": "%",
        "٫": ".",
        "٬": "",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)
_DIACRITIC_RE = re.compile("[\u064b-\u0652\u0653-\u0655\u0670]")
_REMOVED_ZERO_WIDTH = {"\u200b", "\u200d", "\ufeff"}
_ZWNJ = "\u200c"
_PART_NUMBER_RE = re.compile(r"^(?=.*[0-9])[A-Z0-9]{5,20}$")
_PART_NUMBER_INPUT_RE = re.compile(r"^[A-Za-z0-9\s._/+\-#]+$")


def _is_decorative(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) in {"So", "Sk"}
        or 0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
    )


def _normalize_zero_width(text: str) -> str:
    without_removed = "".join(char for char in text if char not in _REMOVED_ZERO_WIDTH)
    collapsed = re.sub(f"{_ZWNJ}+", _ZWNJ, without_removed)
    output: list[str] = []
    for index, char in enumerate(collapsed):
        if char != _ZWNJ:
            output.append(char)
            continue
        previous = collapsed[index - 1] if index > 0 else ""
        following = collapsed[index + 1] if index + 1 < len(collapsed) else ""
        if not previous or not following or previous.isspace() or following.isspace():
            continue
        output.append(char)
    return "".join(output)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(_CHARACTER_TRANSLATION)
    text = text.translate(_DIGIT_TRANSLATION)
    text = _DIACRITIC_RE.sub("", text)
    text = text.translate(_PUNCTUATION_TRANSLATION)
    text = "".join(char for char in text if not _is_decorative(char))
    text = _normalize_zero_width(text)
    return " ".join(text.split())


def normalize_part_number(value: str) -> str:
    normalized = normalize_text(value).upper()
    return re.sub(r"[^A-Z0-9]", "", normalized)


def looks_like_part_number(value: str) -> bool:
    normalized = normalize_text(value)
    if not _PART_NUMBER_INPUT_RE.fullmatch(normalized):
        return False
    return _PART_NUMBER_RE.fullmatch(normalize_part_number(normalized)) is not None
