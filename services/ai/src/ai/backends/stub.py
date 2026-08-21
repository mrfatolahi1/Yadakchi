"""The stub backend: a deterministic model that needs no network at all.

`AI_BACKEND=stub` is the default, not a test convenience. Ten services share
one test suite, none of them has network access in CI, and every one of them
that touches text goes through this service — so if the stub produced noise,
nine other agents would be blocked on a model endpoint they do not have.

That sets the bar: the stub has to be *right* on real Persian titles, not just
well-shaped. What follows is a small rule engine over a spare-parts lexicon.
It is fixture-grade, not a business rule: `enricher` owns the real rules-based
cascade and never sees this code. What it guarantees is that
`/v1/extract` and `/v1/judge` answer usefully with the network unplugged, and
that they answer identically every time — the same title always yields the same
bytes, which is what makes the cache and the golden dataset testable.

The stub speaks the same wire shape as a real model: it returns a JSON *string*
that the service then strips, parses and validates. The parse-and-repair path
is therefore exercised offline too.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Final

from ai.backends.base import Completion, CompletionRequest, ModelBackend
from ai.text import clean_for_part_number, content_tokens, normalize, tokenize

#: Bumped when the heuristics change; it is part of the cache key, so an
#: improvement to the stub never serves yesterday's answers.
STUB_MODEL_ID: Final[str] = "stub-1"

# --------------------------------------------------------------------- lexicons

#: canonical form -> the ways sellers write it. Longest alias wins, so
#: «سایپا یدک» beats «سایپا».
BRANDS: Final[dict[str, tuple[str, ...]]] = {
    "ایساکو": ("ایساکو", "isaco"),
    "سایپا یدک": ("سایپا یدک", "saipa yadak"),
    "سایپا": ("سایپا", "saipa"),
    "ایران خودرو": ("ایران خودرو", "ikco"),
    "کروز": ("کروز", "crouse"),
    "عظام": ("عظام", "azam"),
    "محرکه": ("محرکه", "moharekeh"),
    "امکو": ("امکو", "emco"),
    "نیکو": ("نیکو", "niko"),
    "مبنا": ("مبنا",),
    "تخت جمشید": ("تخت جمشید", "takhte jamshid"),
    "بوش": ("بوش", "bosch"),
    "والئو": ("والئو", "والیو", "valeo"),
    "دلفی": ("دلفی", "delphi"),
    "کایابا": ("کایابا", "کایاب", "kayaba", "kyb"),
    "گیتس": ("گیتس", "gates"),
    "دنسو": ("دنسو", "denso"),
    "ان جی کی": ("ان جی کی", "ngk"),
    "فدرال": ("فدرال", "federal"),
    "بهمن دیزل": ("بهمن دیزل",),
    "دینا پارت": ("دینا پارت", "دیناپارت"),
    "پارس تجهیز": ("پارس تجهیز",),
    "لنت پارس": ("لنت پارس",),
    "سهند": ("سهند",),
    "زیمنس": ("زیمنس", "siemens"),
    "مانن": ("مانن", "mann"),
    "پارت لاستیک": ("پارت لاستیک",),
    "کاسپین": ("کاسپین", "caspian"),
    "تی بی ای": ("تی بی ای", "tba"),
}

#: Part types, matched longest-first so «لنت ترمز جلو» beats «لنت ترمز».
PART_TYPES: Final[tuple[str, ...]] = (
    "دیسک و صفحه کلاچ",
    "سیبک طبق پایین",
    "لنت ترمز جلو",
    "لنت ترمز عقب",
    "کمک فنر جلو",
    "کمک فنر عقب",
    "واشر سرسیلندر",
    "سنسور اکسیژن",
    "میل موج گیر",
    "بلبرینگ چرخ",
    "سیبک فرمان",
    "دیسک ترمز",
    "کاسه چرخ",
    "کاسه نمد",
    "فیلتر روغن",
    "فیلتر هوا",
    "فیلتر کابین",
    "فیلتر بنزین",
    "صافی بنزین",
    "پمپ بنزین",
    "واتر پمپ",
    "تسمه تایم",
    "تسمه دینام",
    "دسته موتور",
    "چراغ جلو",
    "چراغ عقب",
    "آینه بغل",
    "وایر شمع",
    "طبق چرخ",
    "رادیاتور",
    "ترموستات",
    "کویل دوبل",
    "لنت ترمز",
    "کمک فنر",
    "دیاق سپر",
    "شمع موتور",
    "سرسیلندر",
    "دلکو",
    "کویل",
    "سیبک",
    "طبق",
    "شمع",
    "دیسک",
)

_AUTHENTICITY_TOKENS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("refurbished", ("بازسازی", "بازسازی شده", "تعمیری", "refurbished")),
    ("used", ("استوک", "کارکرده", "دست دوم", "دستدوم", "used", "stock")),
    ("oem", ("oem", "شرکتی", "کارخانه ای", "کارخانهای")),
    ("genuine", ("اصلی", "اصل", "ژنوین", "اورجینال", "genuine", "original")),
    ("aftermarket", ("متفرقه", "طرح", "بازار", "تایوانی", "چینی", "کپی", "aftermarket")),
)

#: Numbers that are vehicles, not part numbers. The most common way a naive
#: extractor invents a part number.
VEHICLE_NUMBERS: Final[frozenset[str]] = frozenset(
    {
        "206",
        "207",
        "405",
        "301",
        "2008",
        "111",
        "131",
        "132",
        "141",
        "151",
        "90",
        "ef7",
        "tu5",
        "xu7",
        "l90",
        "gli",
        "elx",
        "lx",
        "sd",
    }
)

_VEHICLE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:پژو\s*)?(?:206|207)\s*(?:اس\s*دی|sd)?\s*(?:تیپ|type)\s*\d+", re.IGNORECASE),
    re.compile(r"پژو\s*(?:206|207|405|301|2008)\s*(?:اس\s*دی|sd)?", re.IGNORECASE),
    re.compile(r"پژو\s*پارس(?:\s*(?:elx|lx|tu5))?", re.IGNORECASE),
    re.compile(r"سمند\s*(?:ef7|lx|ال\s*ایکس|سورن(?:\s*پلاس)?)?", re.IGNORECASE),
    re.compile(r"پراید\s*(?:111|131|132|141|151|صبا|هاچبک)?"),
    re.compile(r"سایپا\s*(?:111|131|132|141|151)"),
    re.compile(r"تیبا\s*(?:2|۲)?"),
    re.compile(r"(?:ال\s*90|l\s*90|لوگان)", re.IGNORECASE),
    re.compile(r"(?:ساینا|کوییک|دنا(?:\s*پلاس)?|رانا(?:\s*پلاس)?|شاهین|آریو)"),
    re.compile(r"(?<!\S)(?:206|405)\s*(?:تیپ|type)\s*\d+", re.IGNORECASE),
    re.compile(r"(?<!\S)(?:206|405)(?!\S)"),
)

_PACK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:بسته|پک|ست|کارتن)\s*(\d{1,2})\s*(?:عددی|عدد|تایی|تائی)"),
    re.compile(r"(\d{1,2})\s*(?:عددی|تایی|تائی)(?!\s*متر)"),
    re.compile(r"(?:بسته|پک|ست)\s*(\d{1,2})\s*(?:تای|عد)"),
)

_CODE_MARKERS: Final[frozenset[str]] = frozenset(
    {"کد", "کدفنی", "شماره", "فنی", "پارت", "نامبر", "code", "part", "number", "no", "pn"}
)

_CODE_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-./_]*$")
_YEAR = re.compile(r"^(?:1[3-4]\d{2}|19\d{2}|20\d{2})$")


# ------------------------------------------------------------------ extraction


def _find_brand(norm: str) -> tuple[str | None, float]:
    """Longest alias wins; a brand word followed by a vehicle number is not one."""
    haystack = f" {norm.lower()} "
    best: tuple[str, str] | None = None
    for canonical, aliases in BRANDS.items():
        for alias in aliases:
            needle = f" {alias.lower()} "
            index = haystack.find(needle)
            if index < 0:
                continue
            tail = haystack[index + len(needle) - 1 :].split()
            if tail and tail[0].strip(".,") in VEHICLE_NUMBERS:
                continue  # «سایپا 131» is a car, not the brand سایپا
            if best is None or len(alias) > len(best[1]):
                best = (canonical, alias)
    if best is None:
        return None, 0.0
    # A brand introduced by «برند» or «مارک» is stated, not inferred.
    stated = re.search(rf"(?:برند|مارک)\s*:?\s*{re.escape(best[1])}", norm, re.IGNORECASE)
    return best[0], 0.95 if stated else 0.9


def _find_part_type(norm: str) -> tuple[str | None, float]:
    for candidate in PART_TYPES:
        if candidate in norm:
            return candidate, 0.9
    return None, 0.0


def _find_authenticity(norm: str) -> tuple[str | None, float]:
    words = set(tokenize(norm))
    lowered = f" {norm.lower()} "
    for grade, markers in _AUTHENTICITY_TOKENS:
        for marker in markers:
            if " " in marker:
                if f" {marker} " in lowered:
                    return grade, 0.85
            elif marker in words or marker.lower() in {w.lower() for w in words}:
                return grade, 0.85
    return None, 0.0


def _find_pack_quantity(norm: str) -> tuple[int | None, float]:
    for pattern in _PACK_PATTERNS:
        match = pattern.search(norm)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 24:
                return value, 0.9
    if "جفت" in norm:
        return 2, 0.85
    if re.search(r"(?<!\S)تکی(?!\S)", norm):
        return 1, 0.8
    return None, 0.0


def _find_vehicle_hints(norm: str) -> tuple[list[str] | None, float]:
    hints: list[str] = []
    for pattern in _VEHICLE_PATTERNS:
        for match in pattern.finditer(norm):
            hint = re.sub(r"\s+", " ", match.group(0)).strip()
            if not hint:
                continue
            # Keep the most specific mention: «پژو 206 تیپ 5» absorbs «پژو 206».
            if any(hint in existing for existing in hints):
                continue
            hints = [existing for existing in hints if existing not in hint]
            hints.append(hint)
    if not hints:
        return None, 0.0
    return hints, 0.85


def _code_score(token: str, previous: list[str]) -> int:
    """How much a token looks like a part number, higher is better."""
    if not _CODE_SHAPE.match(token):
        return -1
    stripped = token.strip("-./_")
    if not any(char.isdigit() for char in stripped):
        return -1
    if stripped.lower() in VEHICLE_NUMBERS or _YEAR.match(stripped):
        return -1
    has_alpha = any(char.isalpha() for char in stripped)
    compact = re.sub(r"[^A-Za-z0-9]", "", stripped)
    if len(compact) < 4 or (len(compact) < 5 and not has_alpha):
        return -1
    if len(set(re.sub(r"\D", "", compact))) <= 1 and not has_alpha:
        return -1  # 0000000 is a placeholder, not a code

    score = 0
    if any(word.lower() in _CODE_MARKERS for word in previous[-2:]):
        score += 4
    if has_alpha:
        score += 2
    score += min(len(compact), 12) // 3
    return score


def _find_part_number(text: str) -> tuple[str | None, float]:
    """Best code-shaped token, once phone numbers and prices are gone."""
    cleaned = clean_for_part_number(text)
    tokens = tokenize(cleaned)
    best: tuple[int, int, str] | None = None
    for index, token in enumerate(tokens):
        candidate = token.strip("-./_,")
        score = _code_score(candidate, tokens[:index])
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, index, candidate)
    if best is None:
        return None, 0.0
    marked = any(word.lower() in _CODE_MARKERS for word in tokens[max(0, best[1] - 2) : best[1]])
    return best[2], 0.95 if marked else 0.85


def extract_offer_fields(text: str, hint: str | None = None) -> dict[str, Any]:
    """The stub's answer for `schema_name=offer_fields`.

    Returns the {"fields": ..., "confidences": ...} envelope the real prompt
    asks a model for, so both paths are validated identically.
    """
    subject = f"{text} {hint}" if hint else text
    norm = normalize(subject)

    brand, brand_confidence = _find_brand(norm)
    part_number, part_number_confidence = _find_part_number(subject)
    part_type, part_type_confidence = _find_part_type(norm)
    authenticity, authenticity_confidence = _find_authenticity(norm)
    pack_quantity, pack_confidence = _find_pack_quantity(norm)
    vehicle_hints, vehicle_confidence = _find_vehicle_hints(norm)

    return {
        "fields": {
            "brand": brand,
            "part_number": part_number,
            "part_type": part_type,
            "authenticity_claim": authenticity,
            "pack_quantity": pack_quantity,
            "vehicle_hints": vehicle_hints,
        },
        "confidences": {
            "brand": brand_confidence,
            "part_number": part_number_confidence,
            "part_type": part_type_confidence,
            "authenticity_claim": authenticity_confidence,
            "pack_quantity": pack_confidence,
            "vehicle_hints": vehicle_confidence,
        },
    }


# --------------------------------------------------------------------- judging

_GRADE_FA: Final[dict[str, str]] = {
    "genuine": "اصلی",
    "oem": "شرکتی",
    "aftermarket": "متفرقه",
    "used": "استوک",
    "refurbished": "بازسازی‌شده",
}


def _jaccard(left: list[str], right: list[str]) -> float:
    first, second = set(left), set(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def judge_same_part(a: str, b: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the four rules from the prompt, in the prompt's order of authority."""
    left = extract_offer_fields(a)["fields"]
    right = extract_offer_fields(b)["fields"]
    overlap = _jaccard(content_tokens(a), content_tokens(b))

    # Rule 1 — different brands are never the same part.
    if left["brand"] and right["brand"] and left["brand"] != right["brand"]:
        return {
            "is_same": False,
            "confidence": 0.95,
            "reason_fa": (
                f"برند دو آگهی متفاوت است ({left['brand']} در برابر {right['brand']})؛ "
                "برندهای متفاوت هرگز یک قطعه نیستند."
            ),
        }

    # Rule 2 — a different authenticity grade is a different product.
    if (
        left["authenticity_claim"]
        and right["authenticity_claim"]
        and left["authenticity_claim"] != right["authenticity_claim"]
    ):
        first = _GRADE_FA[left["authenticity_claim"]]
        second = _GRADE_FA[right["authenticity_claim"]]
        return {
            "is_same": False,
            "confidence": 0.9,
            "reason_fa": (
                f"درجه اصالت متفاوت است ({first} در برابر {second})؛ "
                "دو درجه اصالت از یک طراحی، دو محصول جداگانه‌اند."
            ),
        }

    # Rule 3 — a different pack quantity is a different product.
    if (
        left["pack_quantity"]
        and right["pack_quantity"]
        and left["pack_quantity"] != right["pack_quantity"]
    ):
        return {
            "is_same": False,
            "confidence": 0.88,
            "reason_fa": (
                f"تعداد بسته‌بندی متفاوت است ({left['pack_quantity']} در برابر "
                f"{right['pack_quantity']} عدد) و بسته‌های متفاوت، محصولات متفاوتی هستند."
            ),
        }

    left_code, right_code = left["part_number"], right["part_number"]
    if left_code and right_code:
        if left_code.lower() == right_code.lower():
            return {
                "is_same": True,
                "confidence": 0.95,
                "reason_fa": (
                    f"شماره فنی هر دو یکسان است ({left_code}) و برند و درجه اصالت ناسازگار نیست."
                ),
            }
        return {
            "is_same": False,
            "confidence": 0.9,
            "reason_fa": (
                f"شماره فنی متفاوت است ({left_code} در برابر {right_code})؛ "
                "دو شماره فنی یعنی دو قطعه."
            ),
        }

    # Rule 4 — vehicle applicability is a strong signal, never decisive alone.
    left_vehicles = set(left["vehicle_hints"] or [])
    right_vehicles = set(right["vehicle_hints"] or [])
    vehicles_conflict = bool(
        left_vehicles and right_vehicles and not (left_vehicles & right_vehicles)
    )
    same_type = bool(left["part_type"] and left["part_type"] == right["part_type"])

    if vehicles_conflict and overlap < 0.75:
        return {
            "is_same": False,
            "confidence": 0.7,
            "reason_fa": (
                "خودروهای اعلام‌شده متفاوت‌اند و شباهت متن دو آگهی هم کافی نیست؛ "
                "احتمالاً دو قطعه متفاوت‌اند."
            ),
        }

    if same_type and overlap >= 0.6:
        return {
            "is_same": True,
            "confidence": round(min(0.6 + overlap / 3, 0.85), 2),
            "reason_fa": (
                f"نوع قطعه یکسان است ({left['part_type']}) و متن دو آگهی به میزان زیادی "
                "هم‌پوشانی دارد؛ تعارضی در برند، اصالت یا بسته‌بندی دیده نمی‌شود."
            ),
        }

    return {
        "is_same": False,
        "confidence": round(max(0.55, 0.9 - overlap), 2),
        "reason_fa": (
            "شواهد کافی برای یکی بودن دو آگهی وجود ندارد؛ شماره فنی مشترکی اعلام نشده و "
            "شباهت متن کم است."
        ),
    }


# --------------------------------------------------------------------- backend


class StubBackend(ModelBackend):
    """Deterministic, offline, and the default. Same input, same bytes."""

    name = "stub"

    @property
    def model_id(self) -> str:
        return STUB_MODEL_ID

    async def complete(self, request: CompletionRequest) -> Completion:
        started = time.perf_counter()
        payload = request.payload
        if request.op == "extract":
            answer: dict[str, Any] = extract_offer_fields(
                str(payload.get("text", "")),
                payload.get("hint"),
            )
        elif request.op == "judge":
            answer = judge_same_part(
                str(payload.get("a", "")),
                str(payload.get("b", "")),
                payload.get("context"),
            )
        else:  # pragma: no cover - guarded by the router
            raise ValueError(f"stub backend cannot serve operation {request.op!r}")

        text = json.dumps(answer, ensure_ascii=False)
        # Token counts are estimated so the tokens metric is exercised offline
        # too; four characters per token is the usual rule of thumb.
        return Completion(
            text=text,
            model=STUB_MODEL_ID,
            prompt_tokens=len(request.system + request.user) // 4,
            completion_tokens=len(text) // 4,
            duration_seconds=time.perf_counter() - started,
        )

    async def reachable(self) -> bool:
        return True
