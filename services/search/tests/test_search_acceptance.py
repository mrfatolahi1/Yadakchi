from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from search.query_builder import SearchFilters
from search.search_service import QueryService
from tests.factories import product_payload
from tests.fakes import FakeEmbeddings, FakeIndex


def make_document(
    *,
    product_uid: UUID | None = None,
    title: str,
    part_numbers: list[str] | None = None,
    compatible: list[str] | None = None,
    incompatible: list[str] | None = None,
    brand: str | None = "isaco",
    part_type: str | None = "brake_pad",
    authenticity: str = "oem",
    min_price: int = 900_000,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    uid = product_uid or uuid4()
    payload = product_payload(
        uid,
        title=title,
        part_numbers=part_numbers,
        compatible=compatible,
        incompatible=incompatible,
        brand=brand,
        part_type=part_type,
        authenticity=authenticity,
        min_price=min_price,
    )
    return {
        "product_uid": str(uid),
        "title": title,
        "title_variants": [],
        "brand": brand,
        "part_type": part_type,
        "part_type_synonyms": [],
        "part_numbers": part_numbers or ["425438"],
        "vehicle_compatible": compatible or [],
        "vehicle_incompatible": incompatible or [],
        "authenticity_dominant": authenticity,
        "min_price_toman": min_price,
        "offer_count": 1,
        "has_image": True,
        "embedding": embedding or [0.0] * 384,
        "updated_at": 1,
        "price_freshness": 0,
        "best_seller_trust": 0.8,
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }


def test_part_number_fast_path_keeps_exact_match_first() -> None:
    index = FakeIndex()
    exact = make_document(title="لنت ترمز پژو", part_numbers=["425438"])
    fuzzy = make_document(title="لنت 425438 طرح مشابه", part_numbers=["999999"])
    index.upsert(exact)
    index.upsert(fuzzy)
    index.forced_lexical = [str(fuzzy["product_uid"]), str(exact["product_uid"])]
    index.forced_vector = [str(fuzzy["product_uid"]), str(exact["product_uid"])]
    service = QueryService(index, FakeEmbeddings(), result_floor=0)

    result = service.search("۴۲۵۴۳۸", SearchFilters(), None, 1, 20)

    assert result.hits[0]["product_uid"] == exact["product_uid"]
    assert result.hits[0]["exact_part_number_match"] is True
    assert result.hits[1]["exact_part_number_match"] is False


def test_vector_channel_connects_colloquial_query_to_technical_title() -> None:
    index = FakeIndex()
    semantic_vector = [1.0] + [0.0] * 383
    technical = make_document(
        title="بلبرینگ چرخ جلو پژو 405",
        part_numbers=["VKBA529"],
        part_type="wheel_bearing",
        embedding=semantic_vector,
    )
    unrelated = make_document(
        title="فیلتر روغن پراید",
        part_numbers=["SF7710"],
        part_type="oil_filter",
        embedding=[0.0, 1.0] + [0.0] * 382,
    )
    index.upsert(technical)
    index.upsert(unrelated)
    embeddings = FakeEmbeddings({"توپی چرخ جلو": semantic_vector})
    service = QueryService(index, embeddings, result_floor=0)

    result = service.search("توپی چرخ جلو", SearchFilters(), None, 1, 20)

    assert technical["product_uid"] == result.hits[0]["product_uid"]
    assert technical["part_type_synonyms"] == []


def test_vehicle_filter_excludes_only_confirmed_incompatible() -> None:
    vehicle = "peugeot-206-type-5"
    index = FakeIndex()
    fits = make_document(title="قطعه سازگار", compatible=[vehicle])
    unknown = make_document(title="قطعه نامشخص")
    incompatible = make_document(title="قطعه ناسازگار", incompatible=[vehicle])
    for document in (fits, unknown, incompatible):
        index.upsert(document)
    service = QueryService(index, FakeEmbeddings(), result_floor=0)

    result = service.search("قطعه", SearchFilters(), vehicle, 1, 20)

    statuses = {hit["product_uid"]: hit["fitment_status"] for hit in result.hits}
    assert incompatible["product_uid"] not in statuses
    assert statuses[fits["product_uid"]] == "fits"
    assert statuses[unknown["product_uid"]] == "unverified"


def test_below_floor_reruns_without_vehicle_filter_and_flags_fallback() -> None:
    vehicle = "peugeot-206-type-5"
    index = FakeIndex()
    visible = make_document(title="قطعه قابل نمایش")
    hidden = make_document(title="قطعه ناسازگار", incompatible=[vehicle])
    index.upsert(visible)
    index.upsert(hidden)
    service = QueryService(index, FakeEmbeddings(), result_floor=5)

    result = service.search("قطعه", SearchFilters(), vehicle, 1, 20)

    assert result.fallback_applied is True
    assert result.total == 2
    assert {hit["product_uid"] for hit in result.hits} == {
        visible["product_uid"],
        hidden["product_uid"],
    }


def test_facet_counts_are_correct_on_seeded_candidates() -> None:
    index = FakeIndex()
    documents = [
        make_document(
            title="قطعه اول",
            compatible=["peugeot-206-type-5"],
            brand="isaco",
            part_type="brake_pad",
            authenticity="oem",
            min_price=800_000,
        ),
        make_document(
            title="قطعه دوم",
            compatible=["peugeot-206-type-5"],
            brand="isaco",
            part_type="oil_filter",
            authenticity="genuine",
            min_price=2_000_000,
        ),
        make_document(
            title="قطعه سوم",
            compatible=["saipa-pride-131"],
            brand="sarkan",
            part_type="oil_filter",
            authenticity="aftermarket",
            min_price=7_000_000,
        ),
    ]
    for document in documents:
        index.upsert(document)
    service = QueryService(index, FakeEmbeddings(), result_floor=0)

    result = service.search("قطعه", SearchFilters(), None, 1, 20)

    assert {bucket["value"]: bucket["count"] for bucket in result.facets["brands"]} == {
        "isaco": 2,
        "sarkan": 1,
    }
    assert {bucket["value"]: bucket["count"] for bucket in result.facets["part_types"]} == {
        "oil_filter": 2,
        "brake_pad": 1,
    }
    assert {bucket["value"]: bucket["count"] for bucket in result.facets["price_ranges"]} == {
        "1m_to_5m": 1,
        "5m_and_over": 1,
        "under_1m": 1,
    }


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (SearchFilters(brand="isaco"), 1),
        (SearchFilters(min_price_toman=1_000_000), 0),
        (SearchFilters(has_image=True), 1),
    ],
)
def test_first_class_filters(filters: SearchFilters, expected: int) -> None:
    index = FakeIndex()
    index.upsert(make_document(title="قطعه فیلترپذیر"))
    service = QueryService(index, FakeEmbeddings(), result_floor=0)
    assert service.search("قطعه", filters, None, 1, 20).total == expected
