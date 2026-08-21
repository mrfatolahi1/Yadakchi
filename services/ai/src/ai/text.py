"""Persian text handling for the deterministic stub.

This is *not* the system's Persian normalization — that belongs to `enricher`,
which owns it and does far more. What lives here is the minimum a fake model
needs to behave like a real one on real titles: fold the four ways Iranian
sellers write the same letter, turn ۱۲۳ and ١٢٣ into 123, and get the phone
numbers and prices out of the way before anything looks for a part number.

A phone number in the title is the single most common trap in this data set:
`09121234567` is eleven digits that a naive part-number regex loves.
"""

from __future__ import annotations

import re
from typing import Final

#: Persian (U+06F0-U+06F9) and Arabic-Indic (U+0660-U+0669) digits to ASCII.
_DIGIT_MAP: Final[dict[int, str]] = {
    **{0x06F0 + i: str(i) for i in range(10)},
    **{0x0660 + i: str(i) for i in range(10)},
}

#: Letter shapes that mean the same thing to a reader and not to a computer.
_LETTER_MAP: Final[dict[int, str]] = {
    ord("ك"): "ک",
    ord("ي"): "ی",
    ord("ى"): "ی",
    ord("ة"): "ه",
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("ٱ"): "ا",
    ord("ؤ"): "و",
    ord("ـ"): "",  # tatweel
    **dict.fromkeys(range(1611, 1619), ""),  # harakat
    ord("‌"): " ",  # ZWNJ
    ord("‍"): " ",
    ord("‎"): " ",
    ord("‏"): " ",
    ord("﻿"): "",
}

#: Iranian phone numbers are eleven digits: 09xx xxx xxxx for a mobile,
#: 0xx + 8 or 0xxx + 7 for a landline. Anchoring on that length matters —
#: a looser rule eats ten-digit part numbers such as Bosch's 0242235666.
_PHONE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?<!\d)(?:\+?98|0)9\d{2}[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)0(?:\d{2}[\s\-.]?\d{8}|\d{3}[\s\-.]?\d{7})(?!\d)"),
)

#: A number introduced by «کد» or «شماره فنی» is a part number, whatever it
#: looks like. The marker wins over every pattern below.
_CODE_MARKER_BEFORE: Final[re.Pattern[str]] = re.compile(
    r"(?:کد\s*فنی|کدفنی|شماره\s*فنی|پارت\s*نامبر|کد|code|part\s*(?:no|number)|pn)\s*:?\s*$",
    re.IGNORECASE,
)

_PRICE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)"),
    re.compile(r"(?<!\d)\d[\d,.]*\s*(?:هزار|میلیون)?\s*(?:تومان|تومن|ریال)"),
    re.compile(r"قیمت\s*:?\s*\d[\d,.]*"),
)

_PUNCT = re.compile(r"[^0-9A-Za-z؀-ۿ\-./_+]+")
_SPACES = re.compile(r"\s+")


def normalize_digits(text: str) -> str:
    """۱۲۳ and ١٢٣ become 123. Sellers mix all three in one title."""
    return text.translate(_DIGIT_MAP)


def normalize(text: str) -> str:
    """Fold letters and digits, drop diacritics, collapse whitespace."""
    folded = normalize_digits(text.translate(_LETTER_MAP))
    return _SPACES.sub(" ", folded).strip()


def _blank_unless_marked(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    """Blank every match, except one the seller labelled as a code."""

    def replace(match: re.Match[str]) -> str:
        if _CODE_MARKER_BEFORE.search(text[: match.start()]):
            return match.group(0)
        return " "

    for pattern in patterns:
        text = pattern.sub(replace, text)
    return _SPACES.sub(" ", text).strip()


def strip_phone_numbers(text: str) -> str:
    """Remove seller phone numbers so they cannot be read as a part number."""
    return _blank_unless_marked(text, _PHONE_PATTERNS)


def strip_prices(text: str) -> str:
    """Remove prices for the same reason: `450000 تومان` is not a part code."""
    return _blank_unless_marked(text, _PRICE_PATTERNS)


def clean_for_part_number(text: str) -> str:
    """Normalized text with the two number-shaped distractions removed."""
    return strip_prices(strip_phone_numbers(normalize(text)))


def tokenize(text: str) -> list[str]:
    """Words of a normalized title, keeping `-`, `.`, `/` inside part codes."""
    return [tok for tok in _PUNCT.sub(" ", text).split() if tok]


def content_tokens(text: str) -> list[str]:
    """Tokens worth comparing: prices, phones, stop-words and noise removed."""
    cleaned = clean_for_part_number(text)
    return [tok for tok in tokenize(cleaned) if len(tok) > 1 and tok not in _STOPWORDS]


#: Words that appear in every second listing and separate nothing.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "و",
        "کد",
        "شماره",
        "با",
        "برای",
        "از",
        "در",
        "به",
        "عدد",
        "قیمت",
        "تومان",
        "تومن",
        "ریال",
        "فروش",
        "ارسال",
        "رایگان",
        "گارانتی",
        "تماس",
        "بگیرید",
        "موجود",
        "کالا",
        "خرید",
        "اینترنتی",
        "ویژه",
        "تخفیف",
        "هزار",
        "میلیون",
        "دار",
        "های",
        "یک",
        "جدید",
    }
)
