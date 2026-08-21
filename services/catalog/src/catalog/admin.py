"""Admin — the place a human corrects what the machine got wrong.

Every override exposed here is **sticky**: a rebuild, and a full reprocess,
must never erase a human decision. That is principle 4 of the brief, and it
is why the override fields are separate columns rather than edits to the
computed ones.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone

from catalog.models import (
    ClickCounter,
    CrossRefReadModel,
    FitmentReadModel,
    OfferReadModel,
    PriceHistory,
    Product,
    ProductOffer,
    ProductSlug,
    Seller,
    VehicleReadModel,
)
from catalog.rebuild import rebuild_product
from catalog.tasks import emit_product


class ProductOfferInline(admin.TabularInline):
    model = ProductOffer
    extra = 0
    fields = (
        "rank_position",
        "offer_uid",
        "seller_key",
        "price_toman",
        "stock_status",
        "authenticity_claim",
        "trust_score",
        "is_cheapest",
        "is_active",
    )
    readonly_fields = fields
    ordering = ("rank_position",)
    can_delete = False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "offer_count",
        "min_price_toman",
        "authenticity_dominant",
        "is_published",
        "is_retired",
        "updated_at",
    )
    list_filter = ("is_published", "authenticity_dominant", "part_type")
    search_fields = ("title", "slug", "product_uid", "part_numbers")
    readonly_fields = (
        "product_uid",
        "slug",
        "title",
        "offer_count",
        "min_price_toman",
        "max_price_toman",
        "median_price_toman",
        "authenticity_dominant",
        "representative_offer_uid",
        "representative_reason",
        "part_numbers",
        "crossref_codes",
        "vehicles_compatible",
        "vehicles_incompatible",
        "vehicles_unknown",
        "price_series",
        "is_published",
        "unpublished_reasons",
        "successor_product_uid",
        "successor_slug",
        "document",
        "related",
        "document_hash",
        "last_emitted_hash",
        "last_emitted_at",
        "dirty_since",
        "click_count",
        "created_at",
        "updated_at",
    )
    fields = ("title_override", "image_url_override", "representative_override", *readonly_fields)
    inlines = (ProductOfferInline,)
    actions = ("rebuild_selected", "rebuild_and_emit_selected")

    @admin.action(description="Rebuild the selected products")
    def rebuild_selected(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        now = timezone.now()
        changed = sum(
            1
            for uid in queryset.values_list("product_uid", flat=True)
            if (result := rebuild_product(uid, now)) and result.changed
        )
        self.message_user(request, f"rebuilt {queryset.count()}, changed {changed}")

    @admin.action(description="Rebuild and publish immediately")
    def rebuild_and_emit_selected(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        now = timezone.now()
        emitted = 0
        for uid in list(queryset.values_list("product_uid", flat=True)):
            result = rebuild_product(uid, now)
            if result and emit_product(result.product, now):
                emitted += 1
        self.message_user(request, f"emitted {emitted} products.changed event(s)")


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = (
        "seller_key",
        "name",
        "domain",
        "tier",
        "tier_override",
        "trust_score",
        "price_accuracy",
        "stock_accuracy",
        "is_panel",
    )
    list_filter = ("tier", "is_panel")
    search_fields = ("seller_key", "name", "domain")
    readonly_fields = (
        "seller_key",
        "tier",
        "trust_score",
        "price_accuracy",
        "stock_accuracy",
        "price_observations",
        "price_hits",
        "stock_observations",
        "stock_hits",
        "first_seen_at",
        "updated_at",
        "last_emitted_hash",
    )
    fields = (
        "name",
        "domain",
        "source_key",
        "is_panel",
        "tier_override",
        "domain_age_days",
        "contact_completeness",
        "has_trust_badge",
        *readonly_fields,
    )


@admin.register(ProductSlug)
class ProductSlugAdmin(admin.ModelAdmin):
    list_display = ("slug", "product", "is_current", "created_at")
    list_filter = ("is_current",)
    search_fields = ("slug",)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # Deleting a slug is deleting a URL. Never from here.
        return False


class ReadModelAdmin(admin.ModelAdmin):
    """Read models are projections of other services' events. They are
    visible for debugging and never editable: the way to change one is to fix
    the producer and replay."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(OfferReadModel)
class OfferReadModelAdmin(ReadModelAdmin):
    list_display = (
        "offer_uid",
        "seller_key",
        "title_normalized",
        "price_toman",
        "stock_status",
        "is_active",
        "last_seen_at",
    )
    list_filter = ("stock_status", "is_active", "authenticity_claim")
    search_fields = ("offer_uid", "seller_key", "title_normalized", "part_number")


@admin.register(FitmentReadModel)
class FitmentReadModelAdmin(ReadModelAdmin):
    list_display = ("offer_uid", "computed_at")
    search_fields = ("offer_uid",)


@admin.register(VehicleReadModel)
class VehicleReadModelAdmin(ReadModelAdmin):
    list_display = ("vehicle_slug", "display_name_fa", "is_published", "is_deleted")
    list_filter = ("is_published", "is_deleted")
    search_fields = ("vehicle_slug", "display_name_fa")


@admin.register(CrossRefReadModel)
class CrossRefReadModelAdmin(ReadModelAdmin):
    list_display = ("pair_key", "confidence", "provenance", "is_deleted")
    search_fields = ("code_a", "code_b")


@admin.register(PriceHistory)
class PriceHistoryAdmin(ReadModelAdmin):
    list_display = ("offer_uid", "observed_at", "price_toman", "stock_status")
    search_fields = ("offer_uid",)


@admin.register(ClickCounter)
class ClickCounterAdmin(ReadModelAdmin):
    list_display = ("product_uid", "offer_uid", "seller_key", "clicks", "last_click_at")
    search_fields = ("product_uid", "offer_uid", "seller_key")
