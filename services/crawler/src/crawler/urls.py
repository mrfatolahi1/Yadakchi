from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.http.request import HttpRequest
from django.urls import path
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def healthcheck(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def metrics(_: HttpRequest) -> HttpResponse:
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthcheck),
    path("metrics", metrics),
]
