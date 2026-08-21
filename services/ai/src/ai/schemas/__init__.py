"""The schema registry.

A caller selects an output shape by **name**. It may never pass a prompt, a
schema or a fragment of either: prompt versioning and the cache key both depend
on the prompt being fixed here, and a caller-supplied prompt would silently
break both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel

from ai.schemas.offer_fields import AuthenticityClaim, OfferFields


@dataclass(frozen=True)
class RegisteredSchema:
    """One extractable shape: a name, a model, and the prompt that fills it."""

    name: str
    model: type[BaseModel]
    prompt_file: str
    description: str

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(self.model.model_fields)


OFFER_FIELDS = RegisteredSchema(
    name="offer_fields",
    model=OfferFields,
    prompt_file="extract_offer_fields.txt",
    description="Brand, part number, part type, authenticity, pack size and vehicle hints.",
)

SCHEMA_REGISTRY: Final[dict[str, RegisteredSchema]] = {OFFER_FIELDS.name: OFFER_FIELDS}


def get_schema(name: str) -> RegisteredSchema | None:
    return SCHEMA_REGISTRY.get(name)


def schema_names() -> tuple[str, ...]:
    return tuple(SCHEMA_REGISTRY)


__all__ = [
    "OFFER_FIELDS",
    "SCHEMA_REGISTRY",
    "AuthenticityClaim",
    "OfferFields",
    "RegisteredSchema",
    "get_schema",
    "schema_names",
]
