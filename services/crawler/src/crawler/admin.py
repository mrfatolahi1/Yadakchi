from django.contrib import admin

from crawler.models import (
    AdapterHealth,
    ArchivedDocument,
    ClickSignal,
    ConsumedClick,
    CrawlCursor,
    Observation,
    OutboxEvent,
    Source,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("key", "name", "kind", "adapter_key", "priority", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("key", "name", "base_url")


@admin.register(ArchivedDocument)
class ArchivedDocumentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("source", "http_status", "fetched_at", "page_hash", "seen_count")
    list_filter = ("source", "http_status")
    search_fields = ("url", "url_hash", "page_hash", "archive_uri")
    readonly_fields = ("page_hash", "archive_uri", "fetched_at", "last_seen_at", "seen_count")


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("source", "external_key", "observed_at", "fragment_hash", "emitted_at")
    list_filter = ("source",)
    search_fields = ("external_key", "offer_uid", "url", "raw_title", "fragment_hash")
    readonly_fields = ("offer_uid", "fragment_hash", "trace_id", "emitted_at")


@admin.register(AdapterHealth)
class AdapterHealthAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("source", "window", "attempted", "parsed_ok", "parse_rate", "alerted")
    list_filter = ("source", "alerted")


@admin.register(CrawlCursor)
class CrawlCursorAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("source", "tier", "position", "updated_at")
    list_filter = ("tier",)


@admin.register(ClickSignal)
class ClickSignalAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("offer_uid", "count_7d", "updated_at")
    search_fields = ("offer_uid",)


@admin.register(ConsumedClick)
class ConsumedClickAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("click_id", "offer_uid", "occurred_at", "matched", "consumed_at")
    list_filter = ("matched",)
    search_fields = ("click_id", "offer_uid", "event_id")
    readonly_fields = ("click_id", "event_id", "offer_uid", "occurred_at", "matched", "consumed_at")


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("topic", "event_id", "created_at", "sent_at", "attempts")
    list_filter = ("topic", "sent_at")
    search_fields = ("event_id", "dedupe_key", "key")
    readonly_fields = ("event_id", "dedupe_key", "topic", "key", "body", "created_at")
