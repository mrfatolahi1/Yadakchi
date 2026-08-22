from __future__ import annotations

from django.urls import path

from search.api import api

urlpatterns = [
    path("", api.urls),
]
