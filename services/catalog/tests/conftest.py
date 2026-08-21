"""Shared fixtures.

Tests run against a real Postgres because this service depends on things
SQLite cannot imitate: array columns with `overlap`, JSONB, and a
range-partitioned table.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any, Protocol

import pytest

from catalog import producer
from catalog.events import Envelope
from catalog.models import Seller

NOW = dt.datetime(2026, 8, 19, 7, 0, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _memory_transport() -> Iterator[producer.MemoryTransport]:
    """Every test publishes into memory, never into a broker."""
    transport = producer.MemoryTransport()
    producer.set_transport(transport)
    yield transport
    producer.set_transport(None)


@pytest.fixture
def transport(_memory_transport: producer.MemoryTransport) -> producer.MemoryTransport:
    return _memory_transport


@pytest.fixture
def now() -> dt.datetime:
    return NOW


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_envelope(event_type: str, payload: dict[str, Any] | None, **overrides: Any) -> Envelope:
    """A well-formed envelope for any consumed topic."""
    producers = {
        "offers.enriched": "enricher",
        "clusters.changed": "matcher",
        "offers.fitted": "fitment",
        "vehicles.changed": "fitment",
        "crossrefs.changed": "fitment",
        "clicks.recorded": "billing",
    }
    body: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "occurred_at": iso(overrides.pop("occurred_at", NOW)),
        "producer": producers[event_type],
        "trace_id": overrides.pop("trace_id", "testtrace0000001"),
        "payload": payload,
    }
    body.update(overrides)
    return Envelope.model_validate(body)


def offer_uid(seed: str) -> str:
    """A well-formed offer_uid: 32 lowercase hex characters."""
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def offer_payload(seed: str, **overrides: Any) -> dict[str, Any]:
    """An offers.enriched payload with sane defaults."""
    payload: dict[str, Any] = {
        "offer_uid": offer_uid(seed),
        "source_key": "yadakmarket",
        "external_key": seed,
        "seller_key": "yadakyar",
        "url": f"https://yadakmarket.com/p-{seed}",
        "raw_title": "لنت ترمز جلو پژو 206 تیپ 5 عظام - ارسال رایگان",
        "title_normalized": "لنت ترمز جلو پژو 206 تیپ 5 عظام",
        "brand": "ezam",
        "part_number": "425438",
        "part_type": "brake_pad_front",
        "authenticity_claim": "genuine",
        "pack_quantity": 1,
        "price_toman": 2450000,
        "stock_status": "in_stock",
        "image_url": "https://cdn.yadakmarket.com/media/1/a.jpg",
        "vehicle_hints": ["پژو 206"],
        "vehicle_hints_excluded": [],
        "overbroad_claim": False,
        "confidences": {"brand": 0.9},
        "extraction_provenance": {"brand": "rule"},
        "normalizer_version": "1.0.0",
        "first_seen_at": iso(NOW - dt.timedelta(days=10)),
        "last_seen_at": iso(NOW),
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def cluster_payload(
    cluster_uid: uuid.UUID, member_seeds: list[str], **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cluster_uid": str(cluster_uid),
        "members": [
            {"offer_uid": offer_uid(seed), "confidence": 0.95, "provenance": "model"}
            for seed in member_seeds
        ],
        "change_reason": "created",
        "predecessor_uids": [],
        "successor_uid": None,
        "computed_at": iso(NOW),
    }
    payload.update(overrides)
    return payload


def fitment_payload(seed: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "offer_uid": offer_uid(seed),
        "fitments": [
            {
                "vehicle_slug": "peugeot-206-type-5",
                "status": "compatible",
                "confidence": 0.9,
                "provenance": "rule",
                "evidence": {"agreeing_sellers": 3},
            }
        ],
        "crossref_codes": ["425235"],
        "risky_family": None,
        "computed_at": iso(NOW),
    }
    payload.update(overrides)
    return payload


def vehicle_payload(slug: str = "peugeot-206-type-5", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vehicle_slug": slug,
        "brand": "Peugeot",
        "model": "206",
        "trim": "Type 5",
        "year_from": 2005,
        "year_to": None,
        "engine_code": "TU5JP4",
        "display_name_fa": "پژو ۲۰۶ تیپ ۵",
        "aliases": ["206 تیپ 5", "پژو 206"],
        "is_published": True,
        "updated_at": iso(NOW),
    }
    payload.update(overrides)
    return payload


class SellerFactory(Protocol):
    def __call__(
        self,
        seller_key: str,
        *,
        price_hits: int = ...,
        price_observations: int = ...,
        stock_hits: int = ...,
        stock_observations: int = ...,
        is_panel: bool = ...,
        tier: str = ...,
        trust_score: float | None = ...,
        name: str | None = ...,
    ) -> Seller: ...


@pytest.fixture
def seller_factory(db: None) -> SellerFactory:
    """Sellers with an explicit observation history, so trust is predictable."""

    def _make(
        seller_key: str,
        *,
        price_hits: int = 0,
        price_observations: int = 0,
        stock_hits: int = 0,
        stock_observations: int = 0,
        is_panel: bool = False,
        tier: str = "new",
        trust_score: float | None = None,
        name: str | None = None,
    ) -> Seller:
        seller = Seller.objects.create(
            seller_key=seller_key,
            name=name or seller_key,
            domain=f"{seller_key}.ir",
            source_key=seller_key,
            is_panel=is_panel,
            tier=tier,
            price_hits=price_hits,
            price_observations=price_observations,
            stock_hits=stock_hits,
            stock_observations=stock_observations,
            first_seen_at=NOW,
            updated_at=NOW,
        )
        if trust_score is None:
            from catalog import trust as trust_module

            trust_module.apply_trust(seller, NOW)
        else:
            seller.trust_score = trust_score
        seller.save()
        return seller

    return _make


@pytest.fixture
def contracts_dir() -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "contracts"


@pytest.fixture
def published_schema(contracts_dir: Any) -> Any:
    import json

    def _load(topic: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(
            (contracts_dir / "published" / f"{topic}.json").read_text()
        )
        return loaded

    return _load


class Pipeline:
    """Feeds events through the real consumer handlers.

    Everything below the Kafka client is exercised: parsing, the duplicate
    guard, the staleness guard, the read models, the rebuild and the
    debounced emission. Only the broker itself is absent.
    """

    def __init__(self) -> None:
        from catalog.consumers import handlers

        self._handlers = handlers
        self.topic_for = {
            "offers.enriched": "yadakchi.offers.enriched.v1",
            "clusters.changed": "yadakchi.clusters.changed.v1",
            "offers.fitted": "yadakchi.offers.fitted.v1",
            "vehicles.changed": "yadakchi.vehicles.changed.v1",
            "crossrefs.changed": "yadakchi.crossrefs.changed.v1",
            "clicks.recorded": "yadakchi.clicks.recorded.v1",
        }

    def feed(
        self,
        event_type: str,
        payload: dict[str, Any] | None,
        *,
        key: str | None = None,
        envelope: Envelope | None = None,
        **overrides: Any,
    ) -> bool:
        """Deliver one event. Returns whether it was applied."""
        message = envelope or make_envelope(event_type, payload, **overrides)
        handler = self._handlers.HANDLERS[event_type]
        return bool(handler(message, self.topic_for[event_type], key))

    def deliver_twice(
        self, event_type: str, payload: dict[str, Any] | None, *, key: str | None = None
    ) -> tuple[bool, bool]:
        """Deliver the *same* message twice, as an at-least-once broker will.

        The second delivery must be a no-op.
        """
        message = make_envelope(event_type, payload)
        topic = self.topic_for[event_type]
        handler = self._handlers.HANDLERS[event_type]
        return bool(handler(message, topic, key)), bool(handler(message, topic, key))


@pytest.fixture
def pipeline(db: None) -> Pipeline:
    return Pipeline()


@pytest.fixture
def seeded_vehicles(pipeline: Pipeline) -> None:
    """The four phase-one vehicles, so fitment verdicts have somewhere to land."""
    for slug in (
        "peugeot-206-type-5",
        "peugeot-206-type-6",
        "peugeot-405-glx",
        "pride-131",
    ):
        pipeline.feed("vehicles.changed", vehicle_payload(slug), key=slug)
