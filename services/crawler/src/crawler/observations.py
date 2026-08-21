import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import transaction

from crawler.adapters.base import ListingStub
from crawler.events import ListingsObservedEvent, ListingsObservedPayload
from crawler.models import ArchivedDocument, Observation, OutboxEvent, Source
from crawler.producer import LISTINGS_TOPIC, validate_event


def offer_uid(source_key: str, external_key: str) -> str:
    value = f"{source_key}:{external_key}".encode()
    return hashlib.sha256(value).hexdigest()[:32]


def cap_fragment(fragment: str, max_bytes: int | None = None) -> str:
    limit = max_bytes if max_bytes is not None else settings.RAW_FRAGMENT_MAX_BYTES
    encoded = fragment.encode("utf-8")
    if len(encoded) <= limit:
        return fragment
    return encoded[:limit].decode("utf-8", errors="ignore")


@dataclass(frozen=True, slots=True)
class ObservationResult:
    observation: Observation
    created: bool


@transaction.atomic
def observe_listing(
    source: Source,
    archive: ArchivedDocument,
    stub: ListingStub,
    observed_at: datetime,
) -> ObservationResult:
    raw_fragment = cap_fragment(stub.raw_fragment)
    fragment_hash = hashlib.sha256(raw_fragment.encode("utf-8")).hexdigest()
    url_hash = hashlib.sha256(stub.url.encode("utf-8")).hexdigest()
    latest = (
        Observation.objects.select_for_update()
        .filter(source=source, external_key=stub.external_key)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is not None and latest.fragment_hash == fragment_hash:
        Observation.objects.filter(pk=latest.pk).update(last_seen_at=observed_at)
        latest.last_seen_at = observed_at
        return ObservationResult(latest, False)

    trace_id = uuid.uuid4().hex
    observation = Observation.objects.create(
        source=source,
        archive_document=archive,
        external_key=stub.external_key,
        offer_uid=offer_uid(source.key, stub.external_key),
        url=stub.url,
        url_hash=url_hash,
        raw_title=stub.raw_title,
        raw_price_text=stub.raw_price_text,
        raw_stock_text=stub.raw_stock_text,
        image_url=stub.image_url,
        raw_fragment=raw_fragment,
        fragment_hash=fragment_hash,
        observed_at=observed_at,
        last_seen_at=observed_at,
        trace_id=trace_id,
    )
    event_id = uuid.uuid4()
    event = ListingsObservedEvent(
        event_id=event_id,
        occurred_at=observed_at,
        trace_id=trace_id,
        payload=ListingsObservedPayload(
            source_key=source.key,
            external_key=stub.external_key,
            url=stub.url,
            raw_title=stub.raw_title,
            raw_price_text=stub.raw_price_text,
            raw_stock_text=stub.raw_stock_text,
            image_url=stub.image_url,
            raw_fragment=raw_fragment,
            archive_uri=archive.archive_uri,
            fragment_hash=fragment_hash,
            observed_at=observed_at,
        ),
    ).model_dump(mode="json")
    validate_event(LISTINGS_TOPIC, event)
    OutboxEvent.objects.create(
        event_id=event_id,
        dedupe_key=f"listing-observation:{observation.pk}",
        topic=LISTINGS_TOPIC,
        key=f"{source.key}:{stub.external_key}",
        body=event,
        observation=observation,
    )
    return ObservationResult(observation, True)


def event_from_observation(observation: Observation) -> dict[str, object]:
    event = ListingsObservedEvent(
        event_id=uuid.uuid4(),
        occurred_at=observation.observed_at,
        trace_id=observation.trace_id,
        payload=ListingsObservedPayload(
            source_key=observation.source.key,
            external_key=observation.external_key,
            url=observation.url,
            raw_title=observation.raw_title,
            raw_price_text=observation.raw_price_text,
            raw_stock_text=observation.raw_stock_text,
            image_url=observation.image_url,
            raw_fragment=observation.raw_fragment,
            archive_uri=observation.archive_document.archive_uri,
            fragment_hash=observation.fragment_hash,
            observed_at=observation.observed_at,
        ),
    ).model_dump(mode="json")
    validate_event(LISTINGS_TOPIC, event)
    return event
