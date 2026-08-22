from __future__ import annotations

from dataclasses import asdict, dataclass


def _escape_filter_value(value: str) -> str:
    return value.replace("`", "\\`")


@dataclass(frozen=True, slots=True)
class SearchFilters:
    brand: str | None = None
    part_type: str | None = None
    authenticity: str | None = None
    min_price_toman: int | None = None
    max_price_toman: int | None = None
    has_image: bool | None = None

    def as_log_dict(self) -> dict[str, str | int | bool]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def build_filter_by(filters: SearchFilters, vehicle_slug: str | None) -> str | None:
    clauses: list[str] = []
    if vehicle_slug:
        clauses.append(f"vehicle_incompatible:!=`{_escape_filter_value(vehicle_slug)}`")
    if filters.brand:
        clauses.append(f"brand:=`{_escape_filter_value(filters.brand)}`")
    if filters.part_type:
        clauses.append(f"part_type:=`{_escape_filter_value(filters.part_type)}`")
    if filters.authenticity:
        clauses.append(f"authenticity_dominant:=`{_escape_filter_value(filters.authenticity)}`")
    if filters.min_price_toman is not None:
        clauses.append(f"min_price_toman:>={filters.min_price_toman}")
    if filters.max_price_toman is not None:
        clauses.append(f"min_price_toman:<={filters.max_price_toman}")
    if filters.has_image is not None:
        clauses.append(f"has_image:={str(filters.has_image).lower()}")
    return " && ".join(clauses) or None


def document_matches(
    document: dict[str, object], filters: SearchFilters, vehicle_slug: str | None
) -> bool:
    incompatible = document.get("vehicle_incompatible", [])
    if vehicle_slug and isinstance(incompatible, list) and vehicle_slug in incompatible:
        return False
    if filters.brand and document.get("brand") != filters.brand:
        return False
    if filters.part_type and document.get("part_type") != filters.part_type:
        return False
    if filters.authenticity and document.get("authenticity_dominant") != filters.authenticity:
        return False
    price = document.get("min_price_toman")
    if filters.min_price_toman is not None and (
        not isinstance(price, int) or price < filters.min_price_toman
    ):
        return False
    if filters.max_price_toman is not None and (
        not isinstance(price, int) or price > filters.max_price_toman
    ):
        return False
    if filters.has_image is not None and document.get("has_image") is not filters.has_image:
        return False
    return True
