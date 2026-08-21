from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from fitment.coverage import compute_all_coverage


def metrics_view(request: HttpRequest) -> HttpResponse:
    del request
    compute_all_coverage()
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
