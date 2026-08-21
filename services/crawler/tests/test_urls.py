import pytest
from django.test import Client


@pytest.mark.django_db
def test_health_and_metrics_endpoints_are_available(client: Client) -> None:
    health = client.get("/healthz")
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert metrics.status_code == 200
    assert metrics["Content-Type"].startswith("text/plain")
    assert b"python_info" in metrics.content
