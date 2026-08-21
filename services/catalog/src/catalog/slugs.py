"""Slugs: Persian, URL-safe, and stable enough to keep rankings.

``product_uid`` is the identity and never changes. The slug is derived from
the title and *may* change when the title improves, so every slug a product
has ever carried is kept and keeps resolving — a dead URL is a lost ranking.

The trailing suffix is a short hash of ``product_uid``. It is deterministic,
which is what lets a rebuild produce byte-identical output, and it keeps two
products with the same title from colliding on one URL.
"""

from __future__ import annotations

import hashlib
import re
import uuid

SLUG_SUFFIX_LENGTH = 6
MAX_SLUG_BODY = 80

#: Persian digits read better as Latin ones in a URL.
_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹"
                           "٠١٢٣٤٥٦٧٨٩",
                           "01234567890123456789")  # fmt: skip

_LETTER_MAP = str.maketrans({"ك": "ک", "ي": "ی", "ى": "ی", "ة": "ه"})

#: Persian letters, ASCII alphanumerics and digits survive. Everything else,
#: including ZWNJ and every kind of punctuation, becomes a separator.
_KEEP = re.compile(r"[^a-z0-9ء-غف-يپچژکگی]+")
_DASHES = re.compile(r"-{2,}")


def identity_suffix(product_uid: uuid.UUID | str, length: int = SLUG_SUFFIX_LENGTH) -> str:
    """A short, stable, well-distributed suffix for this identity."""
    digest = hashlib.sha256(str(product_uid).encode("utf-8")).hexdigest()
    return digest[:length]


def slug_body(title: str) -> str:
    """The readable part of a slug: the title, made URL-safe, Persian intact."""
    text = title.strip().translate(_LETTER_MAP).translate(_DIGIT_MAP).lower()
    text = _KEEP.sub("-", text)
    text = _DASHES.sub("-", text).strip("-")
    if len(text) > MAX_SLUG_BODY:
        # Cut on a separator so the slug never ends mid-word.
        text = text[:MAX_SLUG_BODY].rsplit("-", 1)[0].strip("-")
    return text


def build_slug(title: str, product_uid: uuid.UUID | str) -> str:
    """``{title}-{suffix}``, or just the suffix when the title has no
    URL-safe characters at all. Never empty."""
    body = slug_body(title)
    suffix = identity_suffix(product_uid)
    return f"{body}-{suffix}" if body else suffix
