"""Title hygiene.

`enricher` already strips promotional tokens on its way to
``title_normalized``, and that is the field we use — never ``raw_title``.
This module is the second lock on the same door, because a promotional token
that reaches a product title reaches the HTML ``<title>`` tag and damages SEO
across the whole domain. SPEC.md calls this the most common regression in
this service, so the guard is explicit, defensive, and tested against a
seeded junk-title set.

Two rules shape everything below:

* **Detection is permissive, removal is conservative.** Matching runs against
  a normalised copy (Arabic letter forms unified, ZWNJ folded, Persian digits
  read as digits) so junk is found however it was typed. Removal runs against
  the *original* string, so the Persian we publish keeps its own digits and
  its ZWNJ — those are meaningful, not noise.
* **Only unambiguous marketing goes.** Authenticity words such as «اصل» stay:
  on a price-comparison page they carry real meaning.
"""

from __future__ import annotations

import re

#: Any digit a seller might have typed.
_D = r"[0-9۰-۹٠-٩]"

#: Arabic letter forms that sometimes survive into a title, mapped to the
#: Persian letters they should be. Detection only — never applied to output.
_LETTER_MAP = str.maketrans(
    {
        "ك": "ک",  # ك -> ک
        "ي": "ی",  # ي -> ی
        "ى": "ی",  # ى -> ی
        "ة": "ه",  # ة -> ه
        "‌": " ",  # ZWNJ
        "‎": "",
        "‏": "",
        "﻿": "",
    }
)
_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹"
                           "٠١٢٣٤٥٦٧٨٩",
                           "01234567890123456789")  # fmt: skip

#: Letters with more than one spelling in the wild, for phrase matching.
_LETTER_CLASS = {
    "ک": "[کك]",  # ک ك
    "ی": "[یيى]",  # ی ي ى
    "ه": "[هة]",  # ه ة
}

#: Marketing phrases. Matched longest-first so a phrase is removed whole
#: rather than leaving a fragment behind.
_PROMO_PHRASES = (
    # shipping
    "ارسال رایگان", "ارسال فوری", "ارسال سریع", "ارسال به سراسر کشور",
    "ارسال به سراسر ایران", "ارسال یک روزه", "پست رایگان", "ارسال رایگان به سراسر ایران",
    # authenticity *marketing* — the claim itself lives in authenticity_claim
    "صد در صد اصل", "تضمین اصالت کالا", "تضمین اصالت", "ضمانت اصالت",
    "اصالت کالا تضمینی", "گارانتی اصالت", "درصد اورجینال", "اورجینال اصل",
    # price and discount
    "تخفیف ویژه", "فروش ویژه", "حراج ویژه", "قیمت استثنایی", "بهترین قیمت",
    "ارزان ترین قیمت", "ارزانترین قیمت", "قیمت باورنکردنی", "پیشنهاد شگفت انگیز",
    "شگفت انگیز", "جشنواره فروش", "آفر ویژه", "حراج", "تخفیف",
    # guarantees and payment
    "ضمانت بازگشت وجه", "خرید اقساطی", "پرداخت در محل", "اقساطی",
    # contact and call to action
    "جهت خرید تماس بگیرید", "تماس بگیرید", "تماس با ما", "شماره تماس",
    "مشاوره رایگان", "سفارش تلفنی", "خرید اینترنتی", "تماس",
    # availability noise
    "موجود در انبار", "فقط امروز", "تعداد محدود", "آخرین موجودی",
    # channels
    "تلگرام", "واتساپ", "واتس اپ", "اینستاگرام",
    # latin
    "free shipping", "best price", "special offer", "discount", "on sale",
)  # fmt: skip


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Match a phrase however it was spaced: ZWNJ, several spaces, or none,
    and with either spelling of the ambiguous letters."""
    words = []
    for word in phrase.split():
        words.append("".join(_LETTER_CLASS.get(ch, re.escape(ch)) for ch in word))
    return re.compile(r"[\s‌]*".join(words), re.IGNORECASE)


_PHRASE_MATCHERS: tuple[re.Pattern[str], ...] = tuple(
    _phrase_pattern(p) for p in sorted(_PROMO_PHRASES, key=len, reverse=True)
)

_PROMO_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Iranian mobile: 11 digits starting 09. A bare 10-digit part number such
    # as 0451103318 deliberately does not match — separators are required for
    # the landline form below for exactly the same reason.
    re.compile(rf"(?<!{_D})0\s*9{_D}{{9}}(?!{_D})"),
    re.compile(rf"\+?\s*98[\s-]?9{_D}{{2}}[\s-]?{_D}{{3}}[\s-]?{_D}{{4}}(?!{_D})"),
    re.compile(rf"(?<!{_D})0{_D}{{2,3}}[\s-]{_D}{{7,8}}(?!{_D})"),
    # URLs, handles, emails, bare domains.
    re.compile(r"https?://\S+"),
    re.compile(r"\bwww\.\S+"),
    re.compile(r"\S+@\S+\.\S+"),
    re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}"),
    re.compile(r"\b[\w-]+\.(?:ir|com|net|org|co)\b", re.IGNORECASE),
    # Composite claims first, so the generic percent rule below cannot eat
    # half of one and leave the other half stranded in the title.
    re.compile(rf"{_D}{{2,3}}\s*(?:درصد|%|٪)\s*(?:اورجینال|اصل|original|genuine)", re.IGNORECASE),
    re.compile(r"\b100\s*%?\s*(?:original|genuine)\b", re.IGNORECASE),
    re.compile(rf"{_D}{{1,3}}\s*درصد\s*(?:تخفیف)?"),
    # Percentage claims, either order and either percent sign.
    re.compile(rf"{_D}{{1,3}}\s*[%٪]"),
    re.compile(rf"[%٪]\s*{_D}{{1,3}}"),
)

#: Decoration carries no information in a page title.
_DECORATION = re.compile(
    "["
    "\U0001f000-\U0001faff"  # emoji planes
    "☀-➿"  # misc symbols and dingbats
    "⬀-⯿"
    "️★☆✔❤◆●•"
    "]+"
)
_BRACKETED_EMPTY = re.compile(r"[\[\(\{«][\s\-–—_.,؛;:]*[\]\)\}»]")
_MULTI_PUNCT = re.compile(r"[!؟?]{2,}")
_DANGLING_SEP = re.compile(r"(?:\s*[\-–—|/\\،,;:]\s*){2,}")
_SEP_BEFORE_END = re.compile(r"[\s\-–—_|/\\،,.;:!؟?«»\[\]\(\)\{\}]+$")
_SEP_AFTER_START = re.compile(r"^[\s\-–—_|/\\،,.;:!؟?«»\[\]\(\)\{\}]+")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def _detection_form(text: str) -> str:
    """A normalised copy used for matching only. Never published."""
    return _MULTI_SPACE.sub(" ", text.translate(_LETTER_MAP).translate(_DIGIT_MAP)).strip()


def contains_promotional(title: str) -> bool:
    """True when a title carries marketing copy, contact details or decoration.

    This is the acceptance oracle for "product titles never contain
    promotional tokens or phone numbers".
    """
    if _DECORATION.search(title):
        return True
    probe = _detection_form(title)
    if any(pattern.search(probe) for pattern in _PROMO_PATTERNS):
        return True
    return any(matcher.search(probe) for matcher in _PHRASE_MATCHERS)


def _tidy(text: str) -> str:
    text = _MULTI_PUNCT.sub("", text)
    for _ in range(3):  # removing a phrase can expose an empty bracket pair
        collapsed = _BRACKETED_EMPTY.sub(" ", text)
        if collapsed == text:
            break
        text = collapsed
    text = _DANGLING_SEP.sub(" - ", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _SEP_AFTER_START.sub("", text)
    text = _SEP_BEFORE_END.sub("", text)
    return _MULTI_SPACE.sub(" ", text).strip()


def strip_promotional(title: str) -> str:
    """Remove marketing copy, contact details and decoration from a title.

    Removal runs on the original text, so Persian digits and ZWNJ survive
    intact. May return an empty string when the title was *entirely* junk —
    the caller decides what to do then, because a product must never end up
    with an empty title.
    """
    if not title:
        return ""

    text = title
    # Patterns before phrases: "۱۰۰ درصد اورجینال" must go as one unit, not
    # lose its tail to a phrase and leave a stray "۱۰۰" in the title.
    for pattern in _PROMO_PATTERNS:
        text = pattern.sub(" ", text)
    for matcher in _PHRASE_MATCHERS:
        text = matcher.sub(" ", text)
    text = _DECORATION.sub(" ", text)
    return _tidy(text)


def is_sane_title(title: str, band: tuple[int, int]) -> bool:
    """Is the title inside the configured length band?"""
    low, high = band
    return low <= len(title.strip()) <= high
