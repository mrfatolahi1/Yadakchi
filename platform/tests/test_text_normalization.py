"""The shared Persian normalization contract.

Three services normalize Persian text — enricher on the way into
title_normalized, fitment when resolving vehicle hints, search on every query —
and none may import another's code. Prose cannot make three independent
implementations agree; a table of input and expected output can.

platform/text/normalization-vectors.json is that table, distributed into those
three services by `make sync-specs`. This file holds a reference implementation
of the rules in docs/specs/13-TEXT-NORMALIZATION.md and proves every vector is
reachable from them, so a service agent that disagrees with a vector knows the
fault is in its own code and not in an unachievable expectation.

The reference implementation is deliberately literal, one function per numbered
rule, so it reads as the spec does rather than as fast code.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VECTORS = REPO_ROOT / "platform" / "text" / "normalization-vectors.json"
SPEC = REPO_ROOT / "docs" / "specs" / "13-TEXT-NORMALIZATION.md"

# Rule 2 — character unification.
UNIFY = {
    "ي": "ی",  # ARABIC YEH        -> PERSIAN YEH
    "ى": "ی",  # ALEF MAKSURA      -> PERSIAN YEH
    "ك": "ک",  # ARABIC KAF        -> PERSIAN KEHEH
    "ة": "ه",  # TEH MARBUTA       -> HEH
    "أ": "ا",  # ALEF WITH HAMZA ABOVE
    "إ": "ا",  # ALEF WITH HAMZA BELOW
    "ٱ": "ا",  # ALEF WASLA
    "ـ": "",  # TATWEEL — removed
}

# Rule 3 — digits. ARABIC-INDIC and EXTENDED ARABIC-INDIC both fold to Latin.
DIGITS = {chr(0x0660 + n): str(n) for n in range(10)} | {chr(0x06F0 + n): str(n) for n in range(10)}

# Rule 4 — diacritics.
DIACRITICS = {chr(c) for c in [*range(0x064B, 0x0656), 0x0670]}

# Rule 5 — punctuation.
PUNCTUATION = {
    "،": ",",
    "؛": ";",
    "؟": "?",
    "٪": "%",
    "٫": ".",
    "٬": "",
    "−": "-",
    "«": '"',
    "»": '"',
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
} | {chr(c): "-" for c in range(0x2010, 0x2016)}

# Rule 8 — whitespace.
SPACES = {chr(c) for c in [0x00A0, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]}

ZWNJ = "‌"
PART_NUMBER_SHAPE = re.compile(r"^(?=.*[0-9])[A-Z0-9]{5,20}$")


def _is_decorative(ch: str) -> bool:
    """Rule 6 — emoji and decorative symbols, but never a meaningful sign."""
    if unicodedata.category(ch) in {"So", "Sk"}:
        return True
    code = ord(ch)
    return 0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF


def normalize_text(value: str) -> str:
    """docs/specs/13-TEXT-NORMALIZATION.md, rules 1-8, in order."""
    text = unicodedata.normalize("NFKC", value)  # 1
    text = "".join(UNIFY.get(ch, ch) for ch in text)  # 2
    text = "".join(DIGITS.get(ch, ch) for ch in text)  # 3
    text = "".join(ch for ch in text if ch not in DIACRITICS)  # 4
    text = "".join(PUNCTUATION.get(ch, ch) for ch in text)  # 5
    text = "".join("" if _is_decorative(ch) else ch for ch in text)  # 6

    # 7 — zero-width characters.
    text = text.translate({0x200B: None, 0x200D: None, 0xFEFF: None})
    while ZWNJ * 2 in text:
        text = text.replace(ZWNJ * 2, ZWNJ)
    kept: list[str] = []
    for i, ch in enumerate(text):
        if ch != ZWNJ:
            kept.append(ch)
            continue
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        if before == "" or after == "" or before.isspace() or after.isspace():
            continue  # decorative at a boundary or beside a space
        kept.append(ch)  # نیم‌فاصله between letters is semantic
    text = "".join(kept)

    # 8 — whitespace.
    text = "".join(" " if ch in SPACES or ch in "\t\r\n" else ch for ch in text)
    return re.sub(r" +", " ", text).strip()


def normalize_part_number(value: str) -> str:
    """docs/specs/13-TEXT-NORMALIZATION.md, part-number canonicalization."""
    text = normalize_text(value).upper()
    return "".join(ch for ch in text if ch.isascii() and (ch.isdigit() or "A" <= ch <= "Z"))


def looks_like_part_number(value: str) -> bool:
    return PART_NUMBER_SHAPE.fullmatch(normalize_part_number(value)) is not None


def load_vectors() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return loaded


# ---------------------------------------------------------------------------
# The vectors must be reachable from the rules as written.
# ---------------------------------------------------------------------------
def test_vector_file_exists_and_is_structured() -> None:
    data = load_vectors()
    assert data["normalize_text"], "no normalize_text vectors"
    assert data["normalize_part_number"], "no part-number vectors"
    assert data["looks_like_part_number"], "no shape vectors"


@pytest.mark.parametrize("case", load_vectors()["normalize_text"], ids=lambda c: c["name"])
def test_normalize_text_vectors(case: dict[str, str]) -> None:
    assert normalize_text(case["input"]) == case["expected"], case["name"]


@pytest.mark.parametrize("case", load_vectors()["normalize_part_number"], ids=lambda c: c["name"])
def test_normalize_part_number_vectors(case: dict[str, str]) -> None:
    assert normalize_part_number(case["input"]) == case["expected"], case["name"]


@pytest.mark.parametrize("case", load_vectors()["looks_like_part_number"], ids=lambda c: c["name"])
def test_looks_like_part_number_vectors(case: dict[str, Any]) -> None:
    assert looks_like_part_number(case["input"]) is case["expected"], case["name"]


# ---------------------------------------------------------------------------
# Properties the wire format depends on.
# ---------------------------------------------------------------------------
def test_normalization_is_idempotent() -> None:
    """search normalizes a query; enricher already normalized the text it is
    matched against. Running the function twice must change nothing, or the two
    sides drift the moment anything is normalized more than once."""
    for case in load_vectors()["normalize_text"]:
        once = normalize_text(case["input"])
        assert normalize_text(once) == once, case["name"]


def test_canonical_part_numbers_satisfy_the_published_schema() -> None:
    """The wire pattern is ^[A-Z0-9]+$ — canonicalization has to produce it."""
    pattern = re.compile(r"^[A-Z0-9]+$")
    for case in load_vectors()["normalize_part_number"]:
        result = case["expected"]
        if result:
            assert pattern.fullmatch(result), case["name"]


def test_zwnj_is_kept_between_letters_and_dropped_at_edges() -> None:
    """The rule most likely to be implemented differently by three agents."""
    assert normalize_text("نیم‌فاصله") == "نیم‌فاصله"
    assert normalize_text("‌لنت‌") == "لنت"
    assert normalize_text("لنت ‌ ترمز") == "لنت ترمز"
    assert normalize_text("می‌‌رود") == "می‌رود"


def test_promotional_tokens_survive_normalize_text() -> None:
    """Stripping them is enricher's separate step, never part of this function.
    A query for 'اورجینال' means it."""
    assert "اورجینال" in normalize_text("لنت اورجینال")
    assert "گارانتی" in normalize_text("لنت گارانتی ۶ ماهه")


def test_the_spec_and_the_vectors_ship_together() -> None:
    assert SPEC.is_file()
    assert VECTORS.is_file()
    text = SPEC.read_text(encoding="utf-8")
    assert "normalization-vectors.json" in text
    assert PART_NUMBER_SHAPE.pattern in text.replace("\n", "")
