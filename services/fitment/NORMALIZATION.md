# 13 — Persian text normalization and part numbers (shared)

**This file is distributed into `enricher`, `fitment` and `search`.** All three
normalize Persian text, none may import another's code, and all three must agree
exactly. `enricher` normalizes titles into `title_normalized`; `catalog` passes
that through untouched; `search` normalizes the *query*. If the two sides differ
by one rule, a query stops matching text derived from the same words — the
classic silent search bug, which produces no error and no empty-result signal,
just quietly worse recall.

Prose is not enough to guarantee that. **`normalization-vectors.json` sits beside
this file in your folder and is the actual contract**: a table of input → expected
output. Your test suite must run every vector and match byte-for-byte. If your
implementation disagrees with a vector, your implementation is wrong; if you
believe the vector is wrong, stop and raise it rather than editing your copy,
which CI compares against the source.

---

## `normalize_text(s) -> str`

Ordered. Each step sees the output of the one before, and the order matters:
digit folding after NFKC, ZWNJ handling after whitespace unification.

**1. Unicode NFKC.** Folds Arabic presentation forms (`ﻛ`, `ﻼ`), full-width Latin
and compatibility digits into their canonical shapes.

**2. Character unification.** Exact, single-character replacements:

| From | To | Note |
|---|---|---|
| `ي` U+064A | `ی` U+06CC | Arabic yeh → Persian yeh |
| `ى` U+0649 | `ی` U+06CC | alef maksura → Persian yeh |
| `ك` U+0643 | `ک` U+06A9 | Arabic kaf → Persian kaf |
| `ة` U+0629 | `ه` U+0647 | teh marbuta |
| `أ` U+0623, `إ` U+0625, `ٱ` U+0671 | `ا` U+0627 | hamza carriers |
| `ـ` U+0640 | *(removed)* | tatweel / kashida |

**`آ` U+0622 is preserved.** It is a distinct Persian letter, not a decoration;
folding it to `ا` merges words that differ in meaning.

**3. Digits to Latin.** Persian `۰`–`۹` U+06F0–U+06F9 and Arabic-Indic `٠`–`٩`
U+0660–U+0669 → `0`–`9`. Latin digits are left alone. Digits are folded in **both**
the index text and the query, so `۲۰۶` and `206` are the same token.

**4. Strip diacritics.** Remove U+064B–U+0652 (harakat), U+0653–U+0655, and U+0670
(superscript alef).

**5. Punctuation unification.**

| From | To |
|---|---|
| `،` U+060C | `,` |
| `؛` U+061B | `;` |
| `؟` U+061F | `?` |
| `٪` U+066A | `%` |
| `٫` U+066B | `.` (decimal separator) |
| `٬` U+066C | *(removed — thousands separator)* |
| U+2010–U+2015, U+2212 | `-` |
| `«` `»` `“` `”` | `"` |
| `‘` `’` | `'` |

**6. Strip decorative symbols.** Remove every character in Unicode general
category `So` or `Sk`, plus the emoji planes (U+1F000–U+1FAFF) and U+2600–U+27BF.
This is what removes `⭐`, `✅`, `🔥` and the star runs sellers pad titles with.
It does **not** remove `%`, `+`, `-`, `/` or `#`, which carry meaning in part
numbers and quantities.

**7. Zero-width characters.** In this order:

1. Remove U+200B (ZWSP), U+200D (ZWJ) and U+FEFF everywhere.
2. Collapse runs of U+200C (ZWNJ) to a single one.
3. Remove a ZWNJ that is adjacent to whitespace or at either end of the string —
   it is decorative there and carries nothing.
4. **Keep every remaining ZWNJ.** A ZWNJ between two letters is نیم‌فاصله and is
   semantic: `می‌رود` is not `میرود`.

**8. Whitespace.** U+00A0, U+2000–U+200A, U+202F, U+205F, U+3000 and tab/newline
→ a single space `U+0020`; collapse runs; strip leading and trailing.

### What `normalize_text` does NOT do

**It never strips promotional tokens.** `strip_promotional` is a separate
function that only `enricher` applies, only to titles, on its way to
`title_normalized`. Applying it to a search query would delete words the user
deliberately typed — someone searching `اورجینال` means it.

**It never lowercases.** Latin case is preserved here; part-number
canonicalization uppercases separately, and search may casefold its own index
and query provided it does so on both sides.

### Extra folding inside a service

You may fold further for matching — dropping ZWNJ entirely, casefolding, removing
`ه` endings — **provided you apply the identical fold to both sides of every
comparison**, and provided it happens *after* `normalize_text`. Never bake it
into `normalize_text` itself: that output is on the wire in `title_normalized`
and other services depend on it being exactly this.

---

## `normalize_part_number(s) -> str`

The canonical form the wire carries. `offers.enriched.part_number` and
`products.changed.part_numbers[]` are always in it, and the published schemas
enforce `^[A-Z0-9]+$`.

1. `normalize_text(s)` — folds digits, removes tatweel and zero-widths.
2. Uppercase, ASCII only.
3. **Remove every character that is not `A`–`Z` or `0`–`9`.** Hyphens, dots,
   spaces, slashes and underscores all go.
4. The result must match `^[A-Z0-9]+$`. An empty result means there was no part
   number.

So `0K30E-14-302`, `0k30e 14 302` and `۰K30E14302` all canonicalize to
`0K30E14302`, and an exact lookup finds all three.

## `looks_like_part_number(s) -> bool`

Shape only, evaluated on the **canonical** form:

```
^(?=.*[0-9])[A-Z0-9]{5,20}$
```

— five to twenty characters, `A`–`Z` and `0`–`9` only, and **at least one digit**,
which is what stops a word like `GENUINE` qualifying.

### The threshold differs by caller, deliberately

Shape alone is weak evidence in a title and strong evidence in a query, so the
two callers apply it differently. This is not an inconsistency:

- **`enricher`, extracting from a title.** Shape is necessary but not sufficient.
  A run of digits in a title is more often a year, a price, a quantity or a
  vehicle model than a part number. Accept a **pure-digit** token only when it is
  at least 6 characters **and** corroborated — an adjacent code keyword (`کد`,
  `کد فنی`, `شماره فنی`, `پارت نامبر`, `OE`, `OEM`, `P/N`) or a known
  brand-prefix pattern. A token mixing letters and digits needs no corroboration.
  This is what "a stray 6-digit number is usually not a part number" means: not
  that six digits is too short, but that six digits alone is not evidence.
- **`search`, matching a query.** If the **entire normalized query** satisfies the
  shape, treat it as a part number and run the exact fast path first. A user who
  types `425438` and nothing else has told you what they mean, and there is no
  surrounding text to mislead you. Corroboration is not required and must not be.

`425438` is a real Peugeot front-pad number and appears in the fixtures. It is
extracted from `لنت ترمز جلو پژو 206 عظام کد 425438` because `کد` corroborates
it, and it is honoured as a query on its own.

---

## Acceptance

Every service holding this file must ship a test that runs all of
`normalization-vectors.json` and asserts exact equality, for both
`normalize_text` and `normalize_part_number`. The file is distributed by
`make sync-specs`; CI compares your copy byte-for-byte against
`platform/text/normalization-vectors.json`, so do not edit it locally.
