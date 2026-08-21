"""Ten Iranian spare-part listing titles, and what a good answer looks like.

These are written the way sellers actually write them — Persian and Latin
digits in one line, a phone number in the title, a price where a part number
would be, a brand written in Persian, and one listing with no brand at all.
Acceptance criterion 7 measures `/v1/extract` against them.

They are deliberately *not* the titles used as few-shot examples in
`prompts/extract_offer_fields.txt`: a fixture that also appears in the prompt
measures copying, not extraction.
"""

from __future__ import annotations

from typing import NamedTuple


class TitleCase(NamedTuple):
    title: str
    brand: str | None
    part_number: str | None
    note: str


#: brand + part number expectations. `None` means the title states neither.
TITLES: tuple[TitleCase, ...] = (
    TitleCase(
        "فیلتر روغن پژو ۲۰۶ تیپ ۵ اصلی ایساکو کد 1109AY",
        "ایساکو",
        "1109AY",
        "genuine part with an OEM code, Persian digits in the model",
    ),
    TitleCase(
        "لنت ترمز جلو پراید ۱۱۱ برند تخت جمشید کد TJ-3411",
        "تخت جمشید",
        "TJ-3411",
        "aftermarket brand, code with a separator",
    ),
    TitleCase(
        "کویل دوبل پژو 405 کروز شماره فنی 3705010 اصلی",
        "کروز",
        "3705010",
        "code introduced by «شماره فنی»",
    ),
    TitleCase(
        "دیسک و صفحه کلاچ سمند EF7 والئو اصلی کد 826761",
        "والئو",
        "826761",
        "trim code EF7 must not be read as a part number",
    ),
    TitleCase(
        "تسمه تایم پژو ۲۰۶ تیپ ۲ گیتس کد 5559XS قیمت ۹۵۰,۰۰۰ تومان",
        "گیتس",
        "5559XS",
        "mixed Persian/Latin digits, and a price next to the code",
    ),
    TitleCase(
        "کمک فنر جلو پژو 405 کایابا اصل ژاپن کد KYB-334262 تماس 09121234567",
        "کایابا",
        "KYB-334262",
        "phone number in the title — the classic trap",
    ),
    TitleCase(
        "واشر سرسیلندر پراید و تیبا سایپا یدک کد 11114101",
        "سایپا یدک",
        "11114101",
        "multi-vehicle listing; «سایپا یدک» beats «سایپا»",
    ),
    TitleCase(
        "شمع موتور بوش آلمان کد 0242235666 مناسب پژو 206 و 405",
        "بوش",
        "0242235666",
        "ten-digit code that a loose phone regex would eat",
    ),
    TitleCase(
        "سنسور اکسیژن دنسو کد 234-4260 مناسب سمند ال ایکس",
        "دنسو",
        "234-4260",
        "hyphenated numeric code",
    ),
    TitleCase(
        "فیلتر هوا پژو پارس متفرقه",
        None,
        None,
        "no brand and no code — nulls, never a guess",
    ),
)


class JudgePair(NamedTuple):
    a: str
    b: str
    is_same: bool
    note: str


#: Different brands are never the same part — the first rule of the prompt.
DIFFERENT_BRAND_PAIRS: tuple[JudgePair, ...] = (
    JudgePair(
        "لنت ترمز جلو پراید اصلی سایپا یدک",
        "لنت ترمز جلو پراید برند تخت جمشید",
        False,
        "same part type and vehicle, two brands",
    ),
    JudgePair(
        "فیلتر روغن پژو 206 ایساکو اصلی",
        "فیلتر روغن پژو 206 بوش اصلی",
        False,
        "identical wording apart from the brand",
    ),
    JudgePair(
        "کمک فنر جلو پژو 405 کایابا",
        "کمک فنر جلو پژو 405 والئو",
        False,
        "two suppliers of the same design",
    ),
    JudgePair(
        "تسمه تایم پژو 206 تیپ 5 گیتس",
        "تسمه تایم پژو 206 تیپ 5 دلفی",
        False,
        "same vehicle and trim, different brand",
    ),
    JudgePair(
        "شمع موتور بوش آلمان",
        "شمع موتور دنسو ژاپن",
        False,
        "different brand and different country of origin",
    ),
    JudgePair(
        "دیسک و صفحه کلاچ سمند EF7 والئو",
        "دیسک و صفحه کلاچ سمند EF7 عظام",
        False,
        "different brand on an otherwise identical listing",
    ),
)

#: Pairs a judge should accept, so the fixture proves it is not simply
#: answering "no" to everything.
SAME_PART_PAIRS: tuple[JudgePair, ...] = (
    JudgePair(
        "فیلتر روغن پژو 206 تیپ 5 اصلی ایساکو کد 1109AY",
        "فیلتر روغن 206 اصل ایساکو 1109AY",
        True,
        "same brand, same code, different wording",
    ),
    JudgePair(
        "لنت ترمز جلو پراید اصلی سایپا یدک",
        "لنت ترمز جلو پراید سایپا یدک اصلی موجود",
        True,
        "same brand and grade, one has marketing noise",
    ),
)

#: Rules 2 and 3: a different grade or a different pack is a different product.
DIFFERENT_PRODUCT_PAIRS: tuple[JudgePair, ...] = (
    JudgePair(
        "دیسک و صفحه کلاچ سمند EF7 والئو اصلی",
        "دیسک و صفحه کلاچ سمند EF7 والئو طرح",
        False,
        "rule 2 — genuine against aftermarket",
    ),
    JudgePair(
        "سیبک فرمان پراید عظام بسته 2 عددی",
        "سیبک فرمان پراید عظام بسته 4 عددی",
        False,
        "rule 3 — different pack quantity",
    ),
)
