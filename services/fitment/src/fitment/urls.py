from __future__ import annotations

from django.contrib import admin
from django.urls import path

from fitment.metrics import metrics_view

urlpatterns = [path("admin/", admin.site.urls), path("metrics", metrics_view)]
