"""Django models for the catalog service.

Three families live here:

* **Owned state** — ``Product``, ``ProductOffer``, ``ProductSlug``, ``Seller``,
  ``PriceHistory``, ``ClickCounter``. This service is the source of truth.
* **Read models** — ``OfferReadModel``, ``FitmentReadModel``,
  ``VehicleReadModel``, ``CrossRefReadModel``. Local projections of other
  services' events. Never authoritative, always rebuildable from Kafka.
* **Plumbing** — ``ProcessedEvent``, the exact-duplicate guard every consumer
  writes through.

Money is integer tomans everywhere. Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class AuthenticityClaim(models.TextChoices):
    GENUINE = "genuine", "genuine"
    OEM = "oem", "oem"
    AFTERMARKET = "aftermarket", "aftermarket"
    USED = "used", "used"
    REFURBISHED = "refurbished", "refurbished"
    UNKNOWN = "unknown", "unknown"


class StockStatus(models.TextChoices):
    IN_STOCK = "in_stock", "in stock"
    OUT_OF_STOCK = "out_of_stock", "out of stock"
    UNKNOWN = "unknown", "unknown"


class FitmentStatus(models.TextChoices):
    COMPATIBLE = "compatible", "compatible"
    INCOMPATIBLE = "incompatible", "incompatible"
    UNKNOWN = "unknown", "unknown"


class Provenance(models.TextChoices):
    RULE = "rule", "rule"
    MODEL = "model", "model"
    HUMAN = "human", "human"
    CATALOG = "catalog", "catalog"
    CONSENSUS = "consensus", "consensus"


class SellerTier(models.TextChoices):
    NEW = "new", "new"
    STANDARD = "standard", "standard"
    TRUSTED = "trusted", "trusted"
    SUSPENDED = "suspended", "suspended"


# =========================================================== owned: sellers
class Seller(models.Model):
    """Seller identity and trust.

    catalog owns seller identity outright — no inbound topic carries a
    seller's display name, domain or panel membership, so those are this
    service's own data, seeded from what offers reveal and corrected by a
    human in the admin. Human edits are sticky: a rebuild never overwrites
    a field whose ``*_is_override`` flag is set.
    """

    seller_key = models.CharField(primary_key=True, max_length=64)
    #: Seeded from seller_key at provisioning and thereafter only ever
    #: changed by a human in the admin — nothing recomputes it, so a human
    #: edit is sticky by construction.
    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, blank=True)
    source_key = models.CharField(max_length=64, null=True, blank=True)
    is_panel = models.BooleanField(default=False)

    tier = models.CharField(max_length=16, choices=SellerTier.choices, default=SellerTier.NEW)
    tier_override = models.CharField(
        max_length=16,
        choices=SellerTier.choices,
        null=True,
        blank=True,
        help_text="Human decision. Wins over the computed tier, permanently.",
    )
    trust_score = models.FloatField(default=0.0)

    # Observed in our own crawl history — the signals a seller cannot fake.
    price_observations = models.IntegerField(default=0)
    price_hits = models.IntegerField(default=0)
    stock_observations = models.IntegerField(default=0)
    stock_hits = models.IntegerField(default=0)
    price_accuracy = models.FloatField(null=True, blank=True)
    stock_accuracy = models.FloatField(null=True, blank=True)

    # Carried by nothing on the wire; catalog-owned, admin-editable, and
    # neutral (contributing the prior) while unknown.
    domain_age_days = models.IntegerField(null=True, blank=True)
    contact_completeness = models.FloatField(null=True, blank=True)
    has_trust_badge = models.BooleanField(null=True, blank=True)

    first_seen_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    last_emitted_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["seller_key"]
        indexes = [models.Index(fields=["tier"])]

    def __str__(self) -> str:
        return f"{self.seller_key} ({self.tier})"

    @property
    def effective_tier(self) -> str:
        """Human decisions are sticky (brief, principle 4)."""
        return self.tier_override or self.tier

    @property
    def is_new_seller(self) -> bool:
        return self.effective_tier == SellerTier.NEW


# ========================================================== owned: products
class Product(models.Model):
    """A canonical product: one match cluster, made presentable.

    ``product_uid`` **is** matcher's ``cluster_uid``, adopted unchanged and
    never reassigned. A product is never deleted — it is retired and points
    at its successor.
    """

    product_uid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True)

    title = models.CharField(max_length=255)
    title_override = models.CharField(
        max_length=255, null=True, blank=True, help_text="Human title. Sticky across rebuilds."
    )
    brand = models.CharField(max_length=128, null=True, blank=True)
    part_type = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    authenticity_dominant = models.CharField(
        max_length=16, choices=AuthenticityClaim.choices, default=AuthenticityClaim.UNKNOWN
    )
    image_url = models.URLField(max_length=1024, null=True, blank=True)
    image_url_override = models.URLField(max_length=1024, null=True, blank=True)

    # Representative provenance, so re-election is explainable and deterministic.
    representative_offer_uid = models.CharField(max_length=32, null=True, blank=True)
    representative_override = models.CharField(
        max_length=32, null=True, blank=True, help_text="Human pick. Sticky across rebuilds."
    )
    representative_reason = models.JSONField(default=dict, blank=True)

    # Real Postgres arrays, not JSON: `related` searches them with `overlap`
    # and that needs a GIN index over an array column.
    part_numbers = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    crossref_codes = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    vehicles_compatible = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    vehicles_incompatible = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    vehicles_unknown = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    risky_family_note_fa = models.TextField(null=True, blank=True)

    offer_count = models.IntegerField(default=0)
    min_price_toman = models.BigIntegerField(null=True, blank=True)
    max_price_toman = models.BigIntegerField(null=True, blank=True)
    median_price_toman = models.BigIntegerField(null=True, blank=True)
    price_series = models.JSONField(default=list, blank=True)

    is_published = models.BooleanField(default=False, db_index=True)
    unpublished_reasons = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    successor_product_uid = models.UUIDField(null=True, blank=True)
    #: Denormalised so a redirect costs no extra query.
    successor_slug = models.SlugField(max_length=255, null=True, blank=True, allow_unicode=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    #: The exact products.changed payload. The read API serves this verbatim,
    #: which is what keeps GET /v1/products/{slug} to a bounded query count
    #: and keeps the API and the event from ever disagreeing.
    document = models.JSONField(default=dict, blank=True)
    #: Fingerprint of `document` with `updated_at` excluded. Two rebuilds of
    #: unchanged inputs produce the same fingerprint, which is what keeps a
    #: rebuild from bumping timestamps and re-emitting for nothing.
    document_hash = models.CharField(max_length=64, blank=True, default="")
    #: Related-part cards for the read API. Deliberately not on the event:
    #: products.changed.v1 has no field for them and a published payload
    #: shape is not changed without a version bump.
    related = models.JSONField(default=list, blank=True)
    #: Per-seller display extras (tier, "new seller" badge) for the read API,
    #: for the same reason: the event's offer object does not declare them.
    seller_badges = models.JSONField(default=dict, blank=True)

    click_count = models.BigIntegerField(default=0)

    # Debounced emission bookkeeping.
    dirty_since = models.DateTimeField(null=True, blank=True, db_index=True)
    last_emitted_hash = models.CharField(max_length=64, blank=True, default="")
    last_emitted_at = models.DateTimeField(null=True, blank=True)

    cluster_change_reason = models.CharField(max_length=64, blank=True, default="")
    cluster_computed_at = models.DateTimeField(null=True, blank=True)
    #: Envelope occurred_at of the newest clusters.changed applied here.
    source_occurred_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["part_type", "is_published"]),
            models.Index(fields=["successor_product_uid"]),
            GinIndex(fields=["crossref_codes"], name="idx_product_crossrefs"),
            GinIndex(fields=["part_numbers"], name="idx_product_partnumbers"),
            GinIndex(fields=["vehicles_compatible"], name="idx_product_vehicles"),
        ]

    def __str__(self) -> str:
        return f"{self.title} <{self.product_uid}>"

    @property
    def is_retired(self) -> bool:
        return self.successor_product_uid is not None


class ProductSlug(models.Model):
    """Every slug a product has ever had. Rows are never deleted: an old slug
    that stops resolving is a dead URL, and dead URLs cost rankings.
    """

    slug = models.SlugField(primary_key=True, max_length=255, allow_unicode=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="slugs")
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["product", "is_current"])]

    def __str__(self) -> str:
        return self.slug


class ProductOffer(models.Model):
    """One cluster member, denormalised for display and ranked.

    Membership (confidence, provenance) comes from matcher; everything else is
    denormalised from the offer read model at rebuild time so the ranked list
    can be served without joining.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="offers")
    offer_uid = models.CharField(max_length=32, db_index=True)

    membership_confidence = models.FloatField(default=0.0)
    membership_provenance = models.CharField(
        max_length=16, choices=Provenance.choices, default=Provenance.RULE
    )

    seller_key = models.CharField(max_length=64)
    seller_name = models.CharField(max_length=255)
    price_toman = models.BigIntegerField(null=True, blank=True)
    stock_status = models.CharField(
        max_length=16, choices=StockStatus.choices, default=StockStatus.UNKNOWN
    )
    authenticity_claim = models.CharField(
        max_length=16, choices=AuthenticityClaim.choices, default=AuthenticityClaim.UNKNOWN
    )
    trust_score = models.FloatField(default=0.0)
    rank_position = models.IntegerField(default=0)
    rank_score = models.FloatField(default=0.0)
    url = models.URLField(max_length=1024)
    is_cheapest = models.BooleanField(default=False)
    #: An inactive listing has vanished from the source and leaves the list.
    #: Out of stock is *not* inactive: it stays, labelled.
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["rank_position", "offer_uid"]
        constraints = [
            models.UniqueConstraint(fields=["product", "offer_uid"], name="uniq_product_offer")
        ]
        indexes = [models.Index(fields=["seller_key"])]

    def __str__(self) -> str:
        return f"{self.offer_uid}@{self.seller_key}"


class PriceHistory(models.Model):
    """Append-only price observations, monthly-partitioned by ``observed_at``.

    A row is written on an *actual change* only, never once per event. The
    physical table is created by a RunSQL migration as a partitioned parent;
    ``make_partitions`` keeps months ahead of the clock.
    """

    id = models.BigAutoField(primary_key=True)
    offer_uid = models.CharField(max_length=32)
    product_uid = models.UUIDField(null=True, blank=True)
    observed_at = models.DateTimeField()
    price_toman = models.BigIntegerField(null=True, blank=True)
    stock_status = models.CharField(
        max_length=16, choices=StockStatus.choices, default=StockStatus.UNKNOWN
    )

    class Meta:
        constraints = [
            # Includes the partition key, as Postgres requires, and is what
            # makes a replayed event a no-op instead of a duplicate row.
            models.UniqueConstraint(
                fields=["offer_uid", "observed_at"], name="uniq_pricehistory_offer_observed"
            )
        ]
        indexes = [
            models.Index(fields=["offer_uid", "observed_at"]),
            models.Index(fields=["product_uid", "observed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.offer_uid}@{self.observed_at.isoformat()}"


class ClickCounter(models.Model):
    """Traffic-derived priority only.

    clicks.recorded is explicitly *not* financial truth, and it is explicitly
    not an input to ranking: paid placement is rejected. These counts order
    related products and give ops a popularity signal, nothing more.
    """

    product_uid = models.UUIDField()
    offer_uid = models.CharField(max_length=32)
    seller_key = models.CharField(max_length=64, db_index=True)
    clicks = models.BigIntegerField(default=0)
    suspicious_clicks = models.BigIntegerField(default=0)
    last_click_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product_uid", "offer_uid"], name="uniq_clickcounter_product_offer"
            )
        ]
        indexes = [models.Index(fields=["product_uid"])]

    def __str__(self) -> str:
        return f"{self.product_uid}/{self.offer_uid}: {self.clicks}"


# ============================================================== read models
class OfferReadModel(models.Model):
    """Local projection of yadakchi.offers.enriched.v1.

    Every field mirrors the consumed contract exactly. Nothing is invented
    here: a field absent from the schema is absent from this table.
    """

    offer_uid = models.CharField(primary_key=True, max_length=32)
    source_key = models.CharField(max_length=64)
    external_key = models.CharField(max_length=512)
    seller_key = models.CharField(max_length=64, db_index=True)
    url = models.URLField(max_length=1024)

    raw_title = models.TextField()
    title_normalized = models.TextField()
    brand = models.CharField(max_length=128, null=True, blank=True)
    part_number = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    part_type = models.CharField(max_length=128, null=True, blank=True)

    authenticity_claim = models.CharField(
        max_length=16, choices=AuthenticityClaim.choices, default=AuthenticityClaim.UNKNOWN
    )
    pack_quantity = models.IntegerField(default=1)
    price_toman = models.BigIntegerField(null=True, blank=True)
    stock_status = models.CharField(
        max_length=16, choices=StockStatus.choices, default=StockStatus.UNKNOWN
    )
    image_url = models.URLField(max_length=1024, null=True, blank=True)

    vehicle_hints = models.JSONField(default=list, blank=True)
    vehicle_hints_excluded = models.JSONField(default=list, blank=True)
    overbroad_claim = models.BooleanField(default=False)
    confidences = models.JSONField(default=dict, blank=True)
    extraction_provenance = models.JSONField(default=dict, blank=True)
    normalizer_version = models.CharField(max_length=64, default="")

    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)

    #: Envelope occurred_at of the newest event applied to this row. Out-of-
    #: order and stale redeliveries are rejected against it.
    source_occurred_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.offer_uid


class FitmentReadModel(models.Model):
    """Local projection of yadakchi.offers.fitted.v1."""

    offer_uid = models.CharField(primary_key=True, max_length=32)
    fitments = models.JSONField(default=list, blank=True)
    crossref_codes = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    risky_family = models.JSONField(null=True, blank=True)
    computed_at = models.DateTimeField()
    source_occurred_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.offer_uid


class VehicleReadModel(models.Model):
    """Local projection of yadakchi.vehicles.changed.v1 (compacted topic)."""

    vehicle_slug = models.CharField(primary_key=True, max_length=128)
    brand = models.CharField(max_length=128, default="")
    model = models.CharField(max_length=128, default="")
    trim = models.CharField(max_length=128, null=True, blank=True)
    year_from = models.IntegerField(null=True, blank=True)
    year_to = models.IntegerField(null=True, blank=True)
    engine_code = models.CharField(max_length=64, null=True, blank=True)
    display_name_fa = models.CharField(max_length=255, default="")
    aliases = models.JSONField(default=list, blank=True)
    is_published = models.BooleanField(default=False)
    updated_at = models.DateTimeField(null=True, blank=True)
    #: Tombstone (payload: null). The row stays so history stays explicable.
    is_deleted = models.BooleanField(default=False)
    source_occurred_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.vehicle_slug


class CrossRefReadModel(models.Model):
    """Local projection of yadakchi.crossrefs.changed.v1 (compacted topic)."""

    pair_key = models.CharField(primary_key=True, max_length=257)
    code_a = models.CharField(max_length=128, db_index=True)
    code_b = models.CharField(max_length=128, db_index=True)
    brand_a = models.CharField(max_length=128, null=True, blank=True)
    brand_b = models.CharField(max_length=128, null=True, blank=True)
    confidence = models.FloatField(default=0.0)
    provenance = models.CharField(
        max_length=16, choices=Provenance.choices, default=Provenance.RULE
    )
    updated_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    source_occurred_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.pair_key


# ================================================================= plumbing
class ProcessedEvent(models.Model):
    """The exact-duplicate guard.

    Kafka is at-least-once, so the same ``event_id`` can arrive twice. The
    unique constraint turns the second delivery into a no-op. Stale and
    out-of-order deliveries are a different problem, solved separately by
    comparing ``occurred_at`` against each read model's own
    ``source_occurred_at``.
    """

    topic = models.CharField(max_length=128)
    event_id = models.UUIDField()
    entity_key = models.CharField(max_length=255, blank=True, default="")
    occurred_at = models.DateTimeField()
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["topic", "event_id"], name="uniq_processed_event")
        ]
        indexes = [models.Index(fields=["processed_at"])]

    def __str__(self) -> str:
        return f"{self.topic}/{self.event_id}"
