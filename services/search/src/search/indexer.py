from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict, deque
from datetime import datetime
from typing import Any

from django.db import transaction

from search.ai_client import EmbeddingClient
from search.events import (
    CrossReferenceEvent,
    ProductEvent,
    ProductPayload,
    ReviewEvent,
    VehicleEvent,
)
from search.models import (
    CrossReference,
    ProductPartNumber,
    ProductState,
    SynonymDecision,
    VehicleState,
)
from search.synonyms import approved_synonyms
from search.text import normalize_part_number, normalize_text
from search.typesense_client import SearchIndex

logger = logging.getLogger(__name__)


def _timestamp(value: datetime) -> int:
    return int(value.timestamp())


def _cross_reference_graph() -> dict[str, set[str]]:
    graph: defaultdict[str, set[str]] = defaultdict(set)
    for reference in CrossReference.objects.filter(active=True).only("code_a", "code_b"):
        code_a = normalize_part_number(reference.code_a)
        code_b = normalize_part_number(reference.code_b)
        if not code_a or not code_b:
            continue
        graph[code_a].add(code_b)
        graph[code_b].add(code_a)
    return graph


def _connected_codes(seed_codes: set[str]) -> set[str]:
    graph = _cross_reference_graph()
    visited = set(seed_codes)
    queue = deque(seed_codes)
    while queue:
        code = queue.popleft()
        for adjacent in graph.get(code, set()):
            if adjacent not in visited:
                visited.add(adjacent)
                queue.append(adjacent)
    return visited


def _base_codes(payload: ProductPayload) -> set[str]:
    return {
        normalized
        for code in [*payload.part_numbers, *payload.crossref_codes]
        if (normalized := normalize_part_number(code))
    }


def _embedding_text(payload: ProductPayload) -> str:
    variants = sorted(
        {
            offer.title_normalized
            for offer in payload.offers
            if offer.title_normalized and offer.title_normalized != payload.title
        }
    )
    values = [payload.title, *variants]
    if payload.brand:
        values.append(payload.brand)
    if payload.part_type:
        values.append(payload.part_type)
    return " | ".join(values)


def _document(
    state: ProductState,
    payload: ProductPayload,
    embedding_client: EmbeddingClient,
) -> tuple[dict[str, Any], list[float], str]:
    variants = sorted(
        {
            offer.title_normalized
            for offer in payload.offers
            if offer.title_normalized and offer.title_normalized != payload.title
        }
    )
    synonyms = approved_synonyms(payload.part_type).get(payload.part_type or "", [])
    part_numbers = sorted(_connected_codes(_base_codes(payload)))
    text_for_embedding = _embedding_text(payload)
    embedding_hash = hashlib.sha256(text_for_embedding.encode()).hexdigest()
    stored_embedding = [float(value) for value in state.embedding]
    if state.embedding_text_hash == embedding_hash and len(stored_embedding) == 384:
        embedding = stored_embedding
    else:
        embedding = embedding_client.embed([text_for_embedding])[0]
    observed_times = [
        offer.price_observed_at
        for offer in payload.offers
        if offer.price_observed_at is not None and offer.stock_status == "in_stock"
    ]
    price_freshness = max(observed_times) if observed_times else None
    best_trust = max((offer.trust_score for offer in payload.offers), default=0.0)
    body: dict[str, Any] = {
        "product_uid": str(payload.product_uid),
        "title": payload.title,
        "title_variants": variants,
        "part_type_synonyms": synonyms,
        "part_numbers": part_numbers,
        "vehicle_compatible": sorted(set(payload.vehicles_compatible)),
        "vehicle_incompatible": sorted(set(payload.vehicles_incompatible)),
        "authenticity_dominant": payload.authenticity_dominant,
        "offer_count": payload.offer_count,
        "has_image": payload.image_url is not None,
        "embedding": embedding,
        "updated_at": _timestamp(payload.updated_at),
        "price_freshness": _timestamp(price_freshness) if price_freshness else 0,
        "best_seller_trust": best_trust,
        "payload_json": json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
    }
    if payload.brand is not None:
        body["brand"] = payload.brand
    if payload.part_type is not None:
        body["part_type"] = payload.part_type
    if payload.min_price_toman is not None:
        body["min_price_toman"] = payload.min_price_toman
    return body, embedding, embedding_hash


def index_product_state(
    state: ProductState, index: SearchIndex, embedding_client: EmbeddingClient
) -> None:
    with transaction.atomic():
        current = ProductState.objects.select_for_update().get(product_uid=state.product_uid)
        if not current.is_published or not current.payload:
            index.delete(str(current.product_uid))
            current.index_applied_at = current.event_occurred_at
            current.save(update_fields=("index_applied_at",))
            return
        payload = ProductPayload.model_validate(current.payload)
        document, embedding, embedding_hash = _document(current, payload, embedding_client)
        index.upsert(document)
        current.index_applied_at = current.event_occurred_at
        current.embedding = embedding
        current.embedding_text_hash = embedding_hash
        current.save(update_fields=("index_applied_at", "embedding", "embedding_text_hash"))


def handle_product_event(
    body: dict[str, Any],
    message_key: str | None,
    index: SearchIndex,
    embedding_client: EmbeddingClient,
) -> bool:
    event = ProductEvent.model_validate(body)
    product_uid = event.payload.product_uid if event.payload is not None else message_key
    if product_uid is None:
        raise ValueError("a product tombstone requires the Kafka product_uid key")
    payload_json = event.payload.model_dump(mode="json") if event.payload is not None else {}
    with transaction.atomic():
        current = ProductState.objects.select_for_update().filter(product_uid=product_uid).first()
        is_new = current is None
        if current is not None and current.event_occurred_at > event.occurred_at:
            return False
        if (
            current is not None
            and current.event_occurred_at == event.occurred_at
            and current.index_applied_at == event.occurred_at
        ):
            return False
        if current is None:
            current = ProductState(product_uid=product_uid, event_occurred_at=event.occurred_at)
        if (
            is_new
            or current.event_occurred_at != event.occurred_at
            or current.payload != payload_json
        ):
            current.payload = payload_json
            current.part_type = event.payload.part_type if event.payload is not None else None
            current.is_published = bool(event.payload and event.payload.is_published)
            current.event_occurred_at = event.occurred_at
            current.source_updated_at = event.payload.updated_at if event.payload else None
            current.index_applied_at = None
            current.trace_id = event.trace_id
            current.save()
            current.base_part_numbers.all().delete()
            if event.payload is not None:
                ProductPartNumber.objects.bulk_create(
                    [
                        ProductPartNumber(product=current, code=code)
                        for code in sorted(_base_codes(event.payload))
                    ],
                    ignore_conflicts=True,
                )
    state = ProductState.objects.get(product_uid=product_uid)
    index_product_state(state, index, embedding_client)
    logger.info(
        "product event applied",
        extra={
            "trace_id": event.trace_id,
            "event_type": event.event_type,
            "product_uid": str(product_uid),
        },
    )
    return True


def _reindex_part_types(
    part_types: set[str], index: SearchIndex, embedding_client: EmbeddingClient
) -> None:
    for part_type in sorted(value for value in part_types if value):
        tokens = approved_synonyms(part_type).get(part_type, [])
        if tokens:
            index.upsert_synonym(part_type, tokens)
        else:
            index.delete_synonym(part_type)
        for state in ProductState.objects.filter(is_published=True, part_type=part_type).iterator():
            index_product_state(state, index, embedding_client)


def handle_review_event(
    body: dict[str, Any],
    message_key: str | None,
    index: SearchIndex,
    embedding_client: EmbeddingClient,
) -> bool:
    event = ReviewEvent.model_validate(body)
    if event.payload is not None and event.payload.kind != "synonym_candidate":
        return False
    request_uid = event.payload.request_uid if event.payload is not None else message_key
    if request_uid is None:
        raise ValueError("a review tombstone requires the Kafka request_uid key")
    with transaction.atomic():
        current = (
            SynonymDecision.objects.select_for_update().filter(request_uid=request_uid).first()
        )
        if current is not None and current.event_occurred_at > event.occurred_at:
            return False
        if (
            current is not None
            and current.event_occurred_at == event.occurred_at
            and current.index_applied_at == event.occurred_at
        ):
            return False
        old_part_type = current.part_type if current else ""
        if event.payload is None:
            if current is None:
                current = SynonymDecision(
                    request_uid=request_uid, event_occurred_at=event.occurred_at
                )
            current.active = False
        else:
            token = event.payload.subject.get("token")
            part_type = event.payload.subject.get("part_type")
            if not isinstance(token, str) or not isinstance(part_type, str):
                raise ValueError("a synonym decision subject requires string token and part_type")
            if current is None:
                current = SynonymDecision(
                    request_uid=request_uid, event_occurred_at=event.occurred_at
                )
            current.token = normalize_text(token)
            current.part_type = part_type
            current.decision = event.payload.decision
            current.decided_at = event.payload.decided_at
            current.active = True
        current.event_occurred_at = event.occurred_at
        current.index_applied_at = None
        current.trace_id = event.trace_id
        current.save()
        affected = {old_part_type, current.part_type}
    _reindex_part_types(affected, index, embedding_client)
    SynonymDecision.objects.filter(request_uid=request_uid).update(
        index_applied_at=event.occurred_at
    )
    return True


def _affected_product_states(codes: set[str]) -> list[ProductState]:
    if not codes:
        return []
    product_uids = ProductPartNumber.objects.filter(code__in=codes).values_list(
        "product_id", flat=True
    )
    return list(ProductState.objects.filter(product_uid__in=product_uids, is_published=True))


def handle_cross_reference_event(
    body: dict[str, Any],
    message_key: str | None,
    index: SearchIndex,
    embedding_client: EmbeddingClient,
) -> bool:
    event = CrossReferenceEvent.model_validate(body)
    if event.payload is not None:
        code_a = normalize_part_number(event.payload.code_a)
        code_b = normalize_part_number(event.payload.code_b)
        code_a, code_b = sorted((code_a, code_b))
        pair_key = f"{code_a}|{code_b}"
    else:
        raw_codes = message_key.split("|", maxsplit=1) if message_key else []
        if len(raw_codes) == 2:
            code_a, code_b = sorted(normalize_part_number(code) for code in raw_codes)
            pair_key = f"{code_a}|{code_b}"
        else:
            pair_key = ""
            code_a = ""
            code_b = ""
    if not pair_key:
        raise ValueError("a cross-reference tombstone requires the Kafka pair key")
    with transaction.atomic():
        current = CrossReference.objects.select_for_update().filter(pair_key=pair_key).first()
        if current is not None and current.event_occurred_at > event.occurred_at:
            return False
        if (
            current is not None
            and current.event_occurred_at == event.occurred_at
            and current.index_applied_at == event.occurred_at
        ):
            return False
        if current is None:
            current = CrossReference(pair_key=pair_key, event_occurred_at=event.occurred_at)
        if event.payload is None:
            current.active = False
        else:
            current.code_a = code_a
            current.code_b = code_b
            current.confidence = event.payload.confidence
            current.provenance = event.payload.provenance
            current.active = True
        current.event_occurred_at = event.occurred_at
        current.index_applied_at = None
        current.trace_id = event.trace_id
        current.save()
        affected_seeds = {current.code_a, current.code_b}
    affected_codes = _connected_codes({code for code in affected_seeds if code})
    for state in _affected_product_states(affected_codes):
        index_product_state(state, index, embedding_client)
    CrossReference.objects.filter(pair_key=pair_key).update(index_applied_at=event.occurred_at)
    return True


def handle_vehicle_event(body: dict[str, Any], message_key: str | None) -> bool:
    event = VehicleEvent.model_validate(body)
    vehicle_slug = event.payload.vehicle_slug if event.payload is not None else message_key
    if vehicle_slug is None:
        raise ValueError("a vehicle tombstone requires the Kafka vehicle_slug key")
    with transaction.atomic():
        current = VehicleState.objects.select_for_update().filter(vehicle_slug=vehicle_slug).first()
        if current is not None and current.event_occurred_at >= event.occurred_at:
            return False
        VehicleState.objects.update_or_create(
            vehicle_slug=vehicle_slug,
            defaults={
                "payload": event.payload.model_dump(mode="json") if event.payload else {},
                "is_published": bool(event.payload and event.payload.is_published),
                "event_occurred_at": event.occurred_at,
                "trace_id": event.trace_id,
            },
        )
    return True
