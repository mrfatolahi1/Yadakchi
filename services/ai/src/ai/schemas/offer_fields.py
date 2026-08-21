"""`offer_fields` — the structured shape `enricher` asks the model for.

These are exactly the fields listed in the spec, and no more. `enricher` owns
what they *mean* (it resolves brands, mints `offer_uid`, decides its cascade);
this service only guarantees the shape and that nothing is invented.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AuthenticityClaim(StrEnum):
    """The seller's stated grade. Prices differ ~4x across these, so a wrong
    label is worse than no label — hence `null` when the title does not say."""

    GENUINE = "genuine"
    OEM = "oem"
    AFTERMARKET = "aftermarket"
    USED = "used"
    REFURBISHED = "refurbished"


class OfferFields(BaseModel):
    """One listing title, taken apart.

    `extra="forbid"`: a key we did not ask for means the model is improvising,
    which is exactly what the repair retry exists to correct.
    """

    model_config = ConfigDict(extra="forbid")

    brand: str | None = Field(
        default=None,
        description="Manufacturer or seller-stated brand, as written in the title.",
        examples=["ایساکو", "بوش", "سایپا یدک"],
    )
    part_number: str | None = Field(
        default=None,
        description="Manufacturer or OEM code exactly as printed. Never a phone number or a price.",
        examples=["1109AY", "9678385480"],
    )
    part_type: str | None = Field(
        default=None,
        description="What the part is, in Persian.",
        examples=["لنت ترمز جلو", "فیلتر روغن"],
    )
    authenticity_claim: AuthenticityClaim | None = Field(
        default=None,
        description="Grade the seller claims. Null when the title does not say.",
    )
    pack_quantity: int | None = Field(
        default=None,
        ge=1,
        description="Units in the package. Null unless the title states it.",
        examples=[2, 4],
    )
    vehicle_hints: list[str] | None = Field(
        default=None,
        description="Vehicle mentions as written; `fitment` resolves them to slugs.",
        examples=[["پژو 206 تیپ 5"], ["پراید", "تیبا"]],
    )
