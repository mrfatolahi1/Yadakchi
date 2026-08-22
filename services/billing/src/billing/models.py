from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Seller(models.Model):
    class Tier(models.TextChoices):
        NEW = "new", "New"
        STANDARD = "standard", "Standard"
        TRUSTED = "trusted", "Trusted"
        SUSPENDED = "suspended", "Suspended"

    seller_key = models.CharField(primary_key=True, max_length=128)
    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=255)
    source_key = models.CharField(max_length=128, null=True, blank=True)
    is_panel = models.BooleanField(default=False)
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.NEW)
    trust_score = models.DecimalField(max_digits=7, decimal_places=6, default=0)
    price_accuracy = models.DecimalField(max_digits=7, decimal_places=6, null=True, blank=True)
    stock_accuracy = models.DecimalField(max_digits=7, decimal_places=6, null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    wallet_balance_toman = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    panel_offers_active = models.BooleanField(default=True)
    billing_state_version = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["seller_key"]

    def __str__(self) -> str:
        return f"{self.seller_key} ({self.name})"

    @property
    def effective_trust_score(self) -> float:
        score = float(self.trust_score)
        if self.is_panel:
            return score
        return min(score, settings.NON_PANEL_TRUST_CAP)


class CpcRate(models.Model):
    name = models.CharField(max_length=128)
    min_price_toman = models.BigIntegerField(validators=[MinValueValidator(0)])
    max_price_toman = models.BigIntegerField(null=True, blank=True)
    cost_toman = models.BigIntegerField(validators=[MinValueValidator(0)])
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["min_price_toman", "effective_from"]
        constraints = [
            models.CheckConstraint(
                condition=Q(max_price_toman__isnull=True)
                | Q(max_price_toman__gt=models.F("min_price_toman")),
                name="cpc_rate_valid_band",
            )
        ]

    def __str__(self) -> str:
        upper = self.max_price_toman if self.max_price_toman is not None else "unbounded"
        return f"{self.name}: {self.min_price_toman}-{upper} => {self.cost_toman}"


class SuspicionRule(models.Model):
    code = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True)
    threshold = models.PositiveIntegerField(default=1)
    window_seconds = models.PositiveIntegerField(default=300)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.code


class ClickEvent(models.Model):
    click_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_uid = models.UUIDField()
    offer_uid = models.CharField(max_length=32)
    seller_key = models.CharField(max_length=128, db_index=True)
    price_toman = models.BigIntegerField(null=True, blank=True)
    is_panel_offer = models.BooleanField()
    cost_toman = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    is_suspicious = models.BooleanField(default=False, db_index=True)
    fraud_reasons = models.JSONField(default=list)
    ip_hash = models.CharField(max_length=64)
    user_agent_hash = models.CharField(max_length=64)
    fingerprint_hash = models.CharField(max_length=64)
    rate = models.ForeignKey(CpcRate, null=True, blank=True, on_delete=models.SET_NULL)
    occurred_at = models.DateTimeField(db_index=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["seller_key", "occurred_at"])]

    def __str__(self) -> str:
        return str(self.click_id)


class WalletTransaction(models.Model):
    class Kind(models.TextChoices):
        CHARGE = "charge", "Charge"
        TOPUP = "topup", "Top-up"
        MANUAL = "manual", "Manual adjustment"

    transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller = models.ForeignKey(Seller, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount_toman = models.BigIntegerField()
    balance_after_toman = models.BigIntegerField(validators=[MinValueValidator(0)])
    click = models.OneToOneField(
        ClickEvent,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wallet_transaction",
    )
    reference = models.CharField(max_length=255, null=True, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reference"],
                condition=Q(reference__isnull=False),
                name="wallet_transaction_unique_reference",
            ),
            models.CheckConstraint(
                condition=~Q(amount_toman=0), name="wallet_transaction_nonzero_amount"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.seller_id}: {self.kind} {self.amount_toman}"


class ProcessedEvent(models.Model):
    event_id = models.UUIDField(primary_key=True)
    topic = models.CharField(max_length=255)
    natural_key = models.CharField(max_length=255)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self) -> str:
        return f"{self.topic}:{self.event_id}"


class OutboxEvent(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=255)
    message_key = models.CharField(max_length=255)
    natural_key = models.CharField(max_length=255)
    body = models.JSONField()
    published_at = models.DateTimeField(null=True, blank=True)
    publish_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "natural_key"], name="outbox_topic_natural_key_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.topic}:{self.natural_key}"
