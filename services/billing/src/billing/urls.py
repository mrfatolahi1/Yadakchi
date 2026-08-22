from __future__ import annotations

from django.contrib import admin
from django.urls import path

from billing.api import api
from billing.redirect_view import health_view, redirect_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", health_view, name="health"),
    path("go/<str:token>", redirect_view, name="redirect"),
    path("", api.urls),
]
