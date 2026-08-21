from __future__ import annotations

import re
import unicodedata

CHARACTER_MAP = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "ٱ": "ا",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "\u200c": " ",
        "\u200d": " ",
        "\ufeff": " ",
    }
)
SEPARATOR_RE = re.compile(r"[^0-9a-zA-Z\u0600-\u06ff]+")
SPACE_RE = re.compile(r"\s+")


def normalize_persian(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).translate(CHARACTER_MAP).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = SEPARATOR_RE.sub(" ", normalized)
    return SPACE_RE.sub(" ", normalized).strip()


def contains_normalized(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?:^|\s){re.escape(needle)}(?:$|\s)", haystack) is not None


def normalize_part_number(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    return normalized or None
