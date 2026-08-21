from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models


class FitmentStatus(models.TextChoices):
    COMPATIBLE = "compatible", "Compatible"
    INCOMPATIBLE = "incompatible", "Incompatible"
    UNKNOWN = "unknown", "Unknown"


class Provenance(models.TextChoices):
    RULE = "rule", "Rule"
    MODEL = "model", "Model"
    HUMAN = "human", "Human"
    CATALOG = "catalog", "Catalog"
    CONSENSUS = "consensus", "Consensus"


class Vehicle(models.Model):
    slug = models.SlugField(primary_key=True, max_length=100)
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    trim = models.CharField(max_length=100, null=True, blank=True)
    year_from = models.PositiveSmallIntegerField(null=True, blank=True)
    year_to = models.PositiveSmallIntegerField(null=True, blank=True)
    engine_code = models.CharField(max_length=50, null=True, blank=True)
    display_name_fa = models.CharField(max_length=200)
    aliases = models.JSONField(default=list)
    is_published = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["brand", "model", "trim", "slug"]

    def __str__(self) -> str:
        return self.display_name_fa

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in self.aliases
        ):
            raise ValidationError({"aliases": "Aliases must be a list of non-empty strings."})
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValidationError({"year_to": "year_to must not precede year_from."})


class OfferReadModel(models.Model):
    offer_uid = models.CharField(primary_key=True, max_length=32)
    source_key = models.CharField(max_length=100)
    external_key = models.TextField()
    seller_key = models.CharField(max_length=100)
    url = models.URLField(max_length=1000)
    raw_title = models.TextField()
    title_normalized = models.TextField()
    brand = models.CharField(max_length=100, null=True, blank=True)
    part_number = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    part_type = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    price_toman = models.BigIntegerField(null=True, blank=True)
    vehicle_hints = models.JSONField(default=list)
    vehicle_hints_excluded = models.JSONField(default=list)
    overbroad_claim = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    group_key = models.CharField(max_length=300, db_index=True)
    trace_id = models.CharField(max_length=200)
    source_occurred_at = models.DateTimeField()
    payload_hash = models.CharField(max_length=64)
    output_hash = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["offer_uid"]

    def __str__(self) -> str:
        return f"{self.seller_key}: {self.title_normalized}"


class PartFitment(models.Model):
    offer = models.ForeignKey(
        OfferReadModel,
        db_column="offer_uid",
        on_delete=models.CASCADE,
        related_name="fitments",
        to_field="offer_uid",
    )
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="part_fitments")
    status = models.CharField(max_length=20, choices=FitmentStatus.choices)
    confidence = models.FloatField()
    provenance = models.CharField(max_length=20, choices=Provenance.choices)
    evidence = models.JSONField(default=dict)
    computed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["offer", "vehicle"], name="fitment_offer_vehicle_unique"
            )
        ]
        ordering = ["offer_id", "vehicle_id"]

    def __str__(self) -> str:
        return f"{self.offer_id} / {self.vehicle_id}: {self.status}"


class CrossRef(models.Model):
    code_a = models.CharField(max_length=100)
    code_b = models.CharField(max_length=100)
    brand_a = models.CharField(max_length=100, null=True, blank=True)
    brand_b = models.CharField(max_length=100, null=True, blank=True)
    confidence = models.FloatField()
    provenance = models.CharField(max_length=20, choices=Provenance.choices)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["code_a", "code_b"], name="crossref_pair_unique")
        ]
        ordering = ["code_a", "code_b"]

    def __str__(self) -> str:
        return f"{self.code_a} ↔ {self.code_b}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        self.code_a = self.code_a.strip().upper()
        self.code_b = self.code_b.strip().upper()
        if self.code_a == self.code_b:
            raise ValidationError("A code cannot cross-reference itself.")
        if self.code_b < self.code_a:
            self.code_a, self.code_b = self.code_b, self.code_a
            self.brand_a, self.brand_b = self.brand_b, self.brand_a
        if not 0 <= self.confidence <= 1:
            raise ValidationError({"confidence": "Confidence must be between zero and one."})


class RiskyPartFamily(models.Model):
    part_type = models.CharField(primary_key=True, max_length=100)
    required_granularity = models.CharField(max_length=50)
    note_fa = models.TextField()

    class Meta:
        ordering = ["part_type"]
        verbose_name_plural = "risky part families"

    def __str__(self) -> str:
        return self.part_type


class HumanCorrection(models.Model):
    request_uid = models.CharField(unique=True, max_length=200)
    part_number = models.CharField(max_length=100)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="human_corrections")
    status = models.CharField(max_length=20, choices=FitmentStatus.choices)
    actor = models.CharField(max_length=200)
    reason = models.TextField(null=True, blank=True)
    decided_at = models.DateTimeField()
    trace_id = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["part_number", "vehicle"], name="human_part_number_vehicle_unique"
            )
        ]
        ordering = ["part_number", "vehicle_id"]

    def __str__(self) -> str:
        return f"{self.part_number} / {self.vehicle_id}: {self.status}"


class ReviewRequestState(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        SKIPPED = "skipped", "Skipped"
        SETTLED = "settled", "Settled"

    part_number = models.CharField(max_length=100)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="review_states")
    request_uid = models.CharField(max_length=200, unique=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    attempt = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["part_number", "vehicle"], name="review_part_number_vehicle_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.part_number} / {self.vehicle_id}: {self.state}"


class ProcessedEvent(models.Model):
    event_id = models.UUIDField(primary_key=True)
    topic = models.CharField(max_length=200)
    natural_key = models.CharField(max_length=300)
    occurred_at = models.DateTimeField()
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["processed_at"]

    def __str__(self) -> str:
        return f"{self.topic}: {self.event_id}"


class DeadLetterEvent(models.Model):
    event_id = models.CharField(primary_key=True, max_length=200)
    topic = models.CharField(max_length=200)
    reason = models.TextField()
    message = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.topic}: {self.event_id}"


class OutboxEvent(models.Model):
    event_id = models.UUIDField(primary_key=True, editable=False)
    topic = models.CharField(max_length=200, db_index=True)
    message_key = models.CharField(max_length=300)
    envelope = models.JSONField()
    dedupe_key = models.CharField(max_length=500, unique=True)
    trace_id = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    publish_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.topic}: {self.message_key}"
