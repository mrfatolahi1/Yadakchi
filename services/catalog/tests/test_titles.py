"""Acceptance criterion 2 — product titles never carry promotional junk.

SPEC.md calls this the most common regression in this service, and the reason
is worth restating: a promotional token in the title goes straight into the
HTML <title> tag, and a domain full of "ارسال رایگان" titles loses ranking
across every page, not just the one.
"""

from __future__ import annotations

import pytest

from catalog.titles import contains_promotional, is_sane_title, strip_promotional

#: The seeded junk-title set. Every entry is a real shape seen on Iranian
#: parts marketplaces: shipping offers, authenticity marketing, discounts,
#: phone numbers, channel handles and decoration.
JUNK_TITLES = [
    "لنت ترمز جلو پژو 206 عظام - ارسال رایگان",
    "فیلتر روغن پراید سرکان [تخفیف ویژه]",
    "🔥 دیسک ترمز سمند EF7 ✅ 100% اورجینال 🔥",
    "شمع پژو 405 - بهترین قیمت - www.yadakshop.ir",
    "کمک فنر جلو تیبا ۲ ۲۰٪ تخفیف",
    "واشر سرسیلندر پژو ۴۰۵ ۱۰۰ درصد اورجینال، تلگرام @yadakshop",
    "روغن موتور بهران ۴ لیتری، تماس: 021-88776655",
    "لنت عقب پراید 09121234567 تماس بگیرید",
    "سیبک فرمان پژو 206 - ضمانت بازگشت وجه - خرید اقساطی",
    "طلق چراغ سمند ارسال فوری به سراسر کشور",
    "دسته موتور پژو ۲۰۶ | حراج ویژه | فقط امروز",
    "کلاچ کامل پراید ★★★ فروش ویژه ★★★",
    "Brake Pad Peugeot 206 - FREE SHIPPING",
    "فیلتر هوا پژو 405 info@yadak.com",
    "طبق پژو 206 مشاوره رایگان واتساپ",
]

#: Titles that must survive untouched. Every one contains something a naive
#: stripper would eat: bare digits, a 10-digit part number, Persian digits,
#: ZWNJ, an authenticity word that carries real meaning.
CLEAN_TITLES = [
    "لنت ترمز جلو پژو 206 تیپ 5 عظام",
    "فیلتر روغن پراید ۱۳۱ سرکان",
    "لنت ترمز اصل بوش کد 0451103318",
    "چراغ جلو راست پژو 206",
    "واشر سرسیلندر سمند EF7 اصل",
    "دیسک‌ ترمز جلو پژو 405",
    "روغن گیربکس ای‌تی‌اف 4 لیتری",
]


@pytest.mark.parametrize("title", JUNK_TITLES)
def test_junk_titles_are_detected(title: str) -> None:
    assert contains_promotional(title), f"missed promotional content in {title!r}"


@pytest.mark.parametrize("title", JUNK_TITLES)
def test_stripping_leaves_no_promotional_content(title: str) -> None:
    cleaned = strip_promotional(title)
    assert not contains_promotional(cleaned), f"{title!r} still promotional as {cleaned!r}"


@pytest.mark.parametrize("title", CLEAN_TITLES)
def test_clean_titles_are_left_alone(title: str) -> None:
    assert not contains_promotional(title)
    assert strip_promotional(title) == title


def test_part_numbers_are_not_mistaken_for_phone_numbers() -> None:
    """A 10-digit Bosch code starts with an Iranian area code. Stripping it
    would destroy the single most useful field on the page."""
    title = "لنت ترمز اصل بوش کد 0451103318"
    assert strip_promotional(title) == title


def test_persian_digits_and_zwnj_survive() -> None:
    """Removal runs on the original text, so Persian typography is intact."""
    title = "فیلتر روغن پراید ۱۳۱ سرکان"
    assert strip_promotional(title) == title
    assert "‌" in strip_promotional("دیسک‌ترمز پژو 405 ارسال رایگان")


def test_stripping_is_idempotent() -> None:
    for title in JUNK_TITLES:
        once = strip_promotional(title)
        assert strip_promotional(once) == once


def test_an_entirely_promotional_title_becomes_empty() -> None:
    """The caller — not this module — decides what to do with nothing left."""
    assert strip_promotional("ارسال رایگان") == ""


def test_title_band() -> None:
    assert is_sane_title("لنت ترمز جلو پژو 206", (12, 90))
    assert not is_sane_title("لنت", (12, 90))
    assert not is_sane_title("ل" * 200, (12, 90))
