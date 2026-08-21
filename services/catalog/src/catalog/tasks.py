"""Celery tasks: scheduled recomputation and the debounced emission flush.

Celery lives *inside* this service. Kafka consumers are long-running
management commands, never tasks — a consumer that can be retried by a broker
is a consumer whose offsets no longer mean anything.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog import producer, trust
from catalog.events import iso_utc
from catalog.models import ProcessedEvent, Product, Seller
from catalog.partitions import ensure_partitions
from catalog.rebuild import rebuild_product

logger = logging.getLogger("catalog.tasks")


def seller_payload(seller: Seller) -> dict[str, object]:
    """The ``sellers.changed.v1`` payload for one seller."""
    return {
        "seller_key": seller.seller_key,
        "name": seller.name,
        "domain": seller.domain,
        "source_key": seller.source_key,
        "is_panel": seller.is_panel,
        "tier": seller.effective_tier,
        "trust_score": seller.trust_score,
        "price_accuracy": seller.price_accuracy,
        "stock_accuracy": seller.stock_accuracy,
        "updated_at": iso_utc(seller.updated_at),
    }


@shared_task(name="catalog.flush_pending_products")
def flush_pending_products(now: dt.datetime | None = None) -> int:
    """Emit ``products.changed`` for every product whose debounce has expired.

    This is the whole of acceptance criterion 9: five material changes inside
    a minute leave one dirty marker, and this task turns it into one event.
    """
    moment = now or timezone.now()
    cutoff = moment - dt.timedelta(seconds=settings.PRODUCT_EMIT_DEBOUNCE_SECONDS)
    due = Product.objects.filter(dirty_since__isnull=False, dirty_since__lte=cutoff)

    emitted = 0
    for product in due.iterator(chunk_size=200):
        if emit_product(product, moment):
            emitted += 1
    if emitted:
        producer.flush()
    return emitted


def emit_product(product: Product, now: dt.datetime, *, trace_id: str | None = None) -> bool:
    """Publish one product if its payload has actually moved.

    Clearing ``dirty_since`` even when nothing is emitted matters: a rebuild
    that reverted an earlier change should close the window, not leave the
    product permanently marked.
    """
    with transaction.atomic():
        locked = Product.objects.select_for_update().get(product_uid=product.product_uid)
        if locked.document_hash == locked.last_emitted_hash:
            locked.dirty_since = None
            locked.save(update_fields=["dirty_since"])
            return False

        producer.publish_product(
            locked.document,
            trace_id=trace_id or producer.new_trace_id(),
            now=now,
        )
        locked.last_emitted_hash = locked.document_hash
        locked.last_emitted_at = now
        locked.dirty_since = None
        locked.save(update_fields=["last_emitted_hash", "last_emitted_at", "dirty_since"])
        return True


def emit_seller(seller: Seller, now: dt.datetime, *, trace_id: str | None = None) -> bool:
    """Publish one seller if anything a consumer would notice changed."""
    from catalog.rebuild import document_hash

    payload = seller_payload(seller)
    digest = document_hash(payload)
    if digest == seller.last_emitted_hash:
        return False
    producer.publish_seller(payload, trace_id=trace_id or producer.new_trace_id(), now=now)
    seller.last_emitted_hash = digest
    Seller.objects.filter(seller_key=seller.seller_key).update(last_emitted_hash=digest)
    return True


@shared_task(name="catalog.flush_pending_sellers")
def flush_pending_sellers(now: dt.datetime | None = None) -> int:
    """Publish any seller whose payload has moved since we last published it.

    Without this, a seller provisioned from a fresh offer would not reach
    `billing` or `ops` until the nightly trust recomputation — and billing
    keeps its own seller read model from this topic and never calls us.

    Comparing hashes in Python rather than in SQL is deliberate: sellers
    number in the hundreds, not the millions, and a stored hash column would
    have to be kept in step with every field a human can edit in the admin.
    """
    moment = now or timezone.now()
    emitted = 0
    for seller in Seller.objects.all().iterator(chunk_size=500):
        if emit_seller(seller, moment):
            emitted += 1
    if emitted:
        producer.flush()
    return emitted


@shared_task(name="catalog.recompute_trust")
def recompute_trust(now: dt.datetime | None = None) -> int:
    """Rescore every seller, emit the ones that moved, and rebuild the
    products they appear on so the ranked lists follow the new scores."""
    moment = now or timezone.now()
    changed_sellers: list[str] = []

    for seller in Seller.objects.all().iterator(chunk_size=200):
        if trust.apply_trust(seller, moment):
            seller.save(
                update_fields=[
                    "trust_score",
                    "tier",
                    "price_accuracy",
                    "stock_accuracy",
                    "updated_at",
                ]
            )
            emit_seller(seller, moment)
            changed_sellers.append(seller.seller_key)

    if changed_sellers:
        affected = (
            Product.objects.filter(offers__seller_key__in=changed_sellers)
            .values_list("product_uid", flat=True)
            .distinct()
        )
        for product_uid in list(affected):
            rebuild_product(product_uid, moment)
        producer.flush()

    logger.info("trust recomputed", extra={"sellers_changed": len(changed_sellers)})
    return len(changed_sellers)


@shared_task(name="catalog.rebuild_product")
def rebuild_product_task(product_uid: str, now: dt.datetime | None = None) -> bool:
    result = rebuild_product(uuid.UUID(product_uid), now or timezone.now())
    return bool(result and result.changed)


@shared_task(name="catalog.make_partitions")
def make_partitions(months_ahead: int | None = None) -> list[str]:
    return ensure_partitions(months_ahead or settings.PARTITION_MONTHS_AHEAD)


@shared_task(name="catalog.prune_processed_events")
def prune_processed_events(now: dt.datetime | None = None) -> int:
    """Keep the duplicate-guard table bounded.

    Safe to prune, because it is not the only guard: every read model also
    refuses an event older than the one it already applied, so a redelivery
    after pruning is still a no-op.
    """
    moment = now or timezone.now()
    cutoff = moment - dt.timedelta(days=settings.PROCESSED_EVENT_RETENTION_DAYS)
    deleted, _ = ProcessedEvent.objects.filter(processed_at__lt=cutoff).delete()
    return int(deleted)
