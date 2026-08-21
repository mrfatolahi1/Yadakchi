from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest

from fitment.coverage import compute_coverage
from fitment.models import CrossRef, RiskyPartFamily, Vehicle


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("slug", "display_name_fa", "brand", "model", "trim", "is_published")
    list_filter = ("brand", "model", "is_published")
    search_fields = ("slug", "display_name_fa", "aliases")
    readonly_fields = ("updated_at",)

    def save_model(self, request: HttpRequest, obj: Vehicle, form: object, change: bool) -> None:
        del form, change
        if obj.is_published and obj.pk:
            result = compute_coverage(obj)
            if not result.publishable:
                obj.is_published = False
                self.message_user(
                    request,
                    "Publication refused: fitment coverage is below the launch gate.",
                    level=messages.WARNING,
                )
        obj.save()


@admin.register(CrossRef)
class CrossRefAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code_a", "code_b", "brand_a", "brand_b", "confidence", "provenance")
    list_filter = ("provenance", "brand_a", "brand_b")
    search_fields = ("code_a", "code_b")
    readonly_fields = ("updated_at",)


@admin.register(RiskyPartFamily)
class RiskyPartFamilyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("part_type", "required_granularity")
    search_fields = ("part_type", "note_fa")
