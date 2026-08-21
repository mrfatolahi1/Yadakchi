from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Source(models.Model):
    class Kind(models.TextChoices):
        HTML = "html", "HTML"
        FEED = "feed", "Feed"

    key = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    base_url = models.URLField(max_length=500)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.HTML)
    adapter_key = models.SlugField(max_length=64)
    priority = models.PositiveSmallIntegerField(default=0)
    politeness_delay_ms = models.PositiveIntegerField(default=2000)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-priority", "key")

    def __str__(self) -> str:
        return self.name


class ArchivedDocument(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="archives")
    url = models.TextField()
    url_hash = models.CharField(max_length=64, db_index=True)
    http_status = models.PositiveSmallIntegerField()
    fetched_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField()
    seen_count = models.PositiveIntegerField(default=1)
    archive_uri = models.TextField()
    page_hash = models.CharField(max_length=64, db_index=True)
    error = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=("source", "url_hash", "-fetched_at"))]
        constraints = [
            models.UniqueConstraint(
                fields=("source", "url_hash", "page_hash"), name="unique_archived_page_per_url"
            )
        ]

    def __str__(self) -> str:
        return f"{self.source.key}:{self.page_hash[:12]}"


class Observation(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="observations")
    archive_document = models.ForeignKey(
        ArchivedDocument, on_delete=models.PROTECT, related_name="observations"
    )
    external_key = models.TextField()
    offer_uid = models.CharField(max_length=32, db_index=True)
    url = models.TextField()
    url_hash = models.CharField(max_length=64, db_index=True)
    raw_title = models.TextField()
    raw_price_text = models.TextField(null=True, blank=True)
    raw_stock_text = models.TextField(null=True, blank=True)
    image_url = models.TextField(null=True, blank=True)
    raw_fragment = models.TextField()
    fragment_hash = models.CharField(max_length=64, db_index=True)
    observed_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    trace_id = models.CharField(max_length=64)
    emitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("source", "external_key", "-observed_at"))]

    def __str__(self) -> str:
        return f"{self.source.key}:{self.external_key}"


class AdapterHealth(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="health_windows")
    window = models.DateTimeField(db_index=True)
    attempted = models.PositiveIntegerField()
    parsed_ok = models.PositiveIntegerField()
    parse_rate = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        validators=(MinValueValidator(0), MaxValueValidator(1)),
    )
    baseline_rate = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        null=True,
        validators=(MinValueValidator(0), MaxValueValidator(1)),
    )
    alerted = models.BooleanField(default=False)

    class Meta:
        ordering = ("-window",)

    def __str__(self) -> str:
        return f"{self.source.key}:{self.window.isoformat()}"


class CrawlCursor(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="crawl_cursors")
    tier = models.CharField(max_length=64)
    position = models.TextField(default="0")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("source", "tier"), name="unique_source_tier_cursor")
        ]

    def __str__(self) -> str:
        return f"{self.source.key}:{self.tier}@{self.position}"


class ClickSignal(models.Model):
    offer_uid = models.CharField(max_length=32, primary_key=True)
    count_7d = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.offer_uid}:{self.count_7d}"


class ConsumedClick(models.Model):
    click_id = models.CharField(max_length=200, primary_key=True)
    event_id = models.UUIDField(db_index=True)
    offer_uid = models.CharField(max_length=32, db_index=True)
    occurred_at = models.DateTimeField(db_index=True)
    matched = models.BooleanField(default=False)
    consumed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.click_id


class OutboxEvent(models.Model):
    event_id = models.UUIDField(unique=True)
    dedupe_key = models.CharField(max_length=300, unique=True)
    topic = models.CharField(max_length=200)
    key = models.TextField()
    body = models.JSONField()
    observation = models.OneToOneField(
        Observation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="outbox_event",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.topic}:{self.event_id}"
