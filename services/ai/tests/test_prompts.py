"""Prompts are versioned artefacts, not strings someone edits in passing.

`PROMPT_VERSION` goes into every cache key, so changing a prompt without
bumping it means thirty days of answers produced by a prompt that no longer
exists. The fingerprint test below is what makes that impossible to do by
accident.
"""

from __future__ import annotations

import re

from ai.prompts import (
    JUDGE_PROMPT_FILE,
    PROMPT_FINGERPRINT,
    PROMPT_VERSION,
    compute_fingerprint,
    load_prompt,
    prompt_files,
    render,
    system_prompt,
)
from ai.schemas import OFFER_FIELDS


def test_changing_a_prompt_requires_bumping_the_version() -> None:
    assert compute_fingerprint() == PROMPT_FINGERPRINT, (
        "a prompt or a registered schema changed. Bump PROMPT_VERSION (that "
        "invalidates the cache, which is the point) and run `make prompt-fingerprint`."
    )


def test_the_three_prompts_exist() -> None:
    assert prompt_files() == [
        "extract_offer_fields.txt",
        "judge_same_part.txt",
        "system.txt",
    ]


def test_the_version_is_not_empty() -> None:
    assert PROMPT_VERSION.strip()


def test_the_system_prompt_forbids_invention_and_markdown() -> None:
    text = system_prompt()
    assert "JSON" in text
    assert "Never invent" in text
    assert "no markdown fences" in text.lower()


def test_the_extraction_prompt_has_at_least_six_persian_examples() -> None:
    text = load_prompt(OFFER_FIELDS.prompt_file)
    examples = re.findall(r"^TEXT: (.+)$", text, flags=re.MULTILINE)
    # The last one is the placeholder for the real input.
    examples = [line for line in examples if "{{TEXT}}" not in line]

    assert len(examples) >= 6, examples
    for example in examples:
        assert any("؀" <= char <= "ۿ" for char in example), example


def test_the_extraction_prompt_covers_every_required_case() -> None:
    text = load_prompt(OFFER_FIELDS.prompt_file)
    examples = [
        line
        for line in re.findall(r"^TEXT: (.+)$", text, flags=re.MULTILINE)
        if "{{TEXT}}" not in line
    ]
    joined = "\n".join(examples)

    assert "اصلی" in joined and "1109AY" in joined, "a genuine part with an OEM code"
    assert "متفرقه" in joined or "طرح" in joined, "an aftermarket part"
    assert " و " in joined, "a multi-vehicle listing"
    assert re.search(r"09\d{9}", joined), "a listing with a phone number in the title"
    assert re.search(r"[۰-۹]", joined), "one with mixed Persian and Latin digits"
    assert any(
        brand not in example for example in examples for brand in ("ایساکو",) if "سیبک" in example
    ), "one with no brand"


def test_every_field_of_the_registered_schema_is_named_in_the_prompt() -> None:
    text = load_prompt(OFFER_FIELDS.prompt_file)
    for field in OFFER_FIELDS.field_names:
        assert field in text


def test_the_judge_prompt_states_the_four_rules() -> None:
    flat = re.sub(r"\s+", " ", load_prompt(JUDGE_PROMPT_FILE))

    assert "Different brands are NEVER the same part" in flat
    assert "Different authenticity grades of the same design ARE different products" in flat
    assert "Different pack quantities are different products" in flat
    assert "Different vehicle applicability is a strong negative signal, but not decisive" in flat
    assert "reason_fa" in flat


def test_placeholders_are_filled_without_touching_the_json_braces() -> None:
    filled = render('A: {{A}} / B: {{B}} {"kept": true}', a="یک", b="دو")
    assert filled == 'A: یک / B: دو {"kept": true}'
