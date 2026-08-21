from __future__ import annotations

from django.contrib import admin

from fitment.models import CrossRef, RiskyPartFamily, Vehicle


def test_required_reference_models_are_registered_in_admin() -> None:
    assert admin.site.is_registered(Vehicle)
    assert admin.site.is_registered(CrossRef)
    assert admin.site.is_registered(RiskyPartFamily)
