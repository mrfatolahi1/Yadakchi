from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html

from billing.models import (
    ClickEvent,
    CpcRate,
    OutboxEvent,
    ProcessedEvent,
    Seller,
    SuspicionRule,
    WalletTransaction,
)
from billing.wallet import adjust_wallet


class ManualAdjustmentForm(forms.Form):
    amount_toman = forms.IntegerField(help_text="Positive credits; negative debits.")
    reference = forms.CharField(max_length=240)

    def clean_amount_toman(self) -> int:
        amount = int(self.cleaned_data["amount_toman"])
        if amount == 0:
            raise forms.ValidationError("Adjustment cannot be zero.")
        return amount


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "seller_key",
        "name",
        "is_panel",
        "wallet_balance_toman",
        "panel_offers_active",
        "is_deleted",
        "wallet_adjustment_link",
    )
    search_fields = ("seller_key", "name", "domain")
    readonly_fields = ("wallet_balance_toman", "billing_state_version")

    @admin.display(description="Wallet")
    def wallet_adjustment_link(self, seller: Seller) -> str:
        url = reverse("admin:billing_seller_adjust_wallet", args=[seller.pk])
        return format_html('<a href="{}">Adjust wallet</a>', url)

    def get_urls(self) -> list[URLPattern]:
        custom = [
            path(
                "<path:object_id>/adjust-wallet/",
                self.admin_site.admin_view(self.adjust_wallet_view),
                name="billing_seller_adjust_wallet",
            )
        ]
        return custom + super().get_urls()

    def adjust_wallet_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        seller = self.get_object(request, object_id)
        if seller is None:
            raise Http404
        form = ManualAdjustmentForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                adjustment = adjust_wallet(
                    seller_key=seller.seller_key,
                    amount_toman=form.cleaned_data["amount_toman"],
                    reference=f"manual:{form.cleaned_data['reference']}",
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    f"Wallet adjusted; balance is {adjustment.balance_after_toman} toman.",
                )
                return redirect("admin:billing_seller_change", object_id)
        context: dict[str, Any] = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": seller,
            "form": form,
            "title": f"Adjust wallet: {seller.seller_key}",
        }
        return TemplateResponse(
            request,
            "admin/billing/seller/adjust_wallet.html",
            context,
        )


@admin.register(CpcRate)
class CpcRateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "name",
        "min_price_toman",
        "max_price_toman",
        "cost_toman",
        "active",
        "effective_from",
    )
    list_filter = ("active",)

    def get_readonly_fields(
        self, request: HttpRequest, obj: CpcRate | None = None
    ) -> tuple[str, ...]:
        del request
        if obj is None:
            return ()
        # Existing bands are historical versions; close them and create a new row instead.
        return (
            "name",
            "min_price_toman",
            "max_price_toman",
            "cost_toman",
            "effective_from",
        )


@admin.register(SuspicionRule)
class SuspicionRuleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "enabled", "threshold", "window_seconds", "updated_at")


@admin.register(ClickEvent)
class ClickEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "click_id",
        "seller_key",
        "cost_toman",
        "is_suspicious",
        "occurred_at",
    )
    search_fields = ("click_id", "seller_key", "offer_uid")
    list_filter = ("is_suspicious", "is_panel_offer")
    readonly_fields = [field.name for field in ClickEvent._meta.fields]

    def has_add_permission(self, _request: object) -> bool:
        return False


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "transaction_id",
        "seller",
        "kind",
        "amount_toman",
        "balance_after_toman",
        "occurred_at",
    )
    search_fields = ("transaction_id", "seller__seller_key", "reference")
    readonly_fields = [field.name for field in WalletTransaction._meta.fields]

    def has_add_permission(self, _request: object) -> bool:
        return False


admin.site.register(ProcessedEvent)
admin.site.register(OutboxEvent)
