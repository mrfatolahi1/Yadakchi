from datetime import UTC, datetime, timedelta

import pytest

from crawler.health import record_adapter_health
from crawler.models import OutboxEvent, Source
from crawler.producer import REVIEW_TOPIC, flush_outbox
from tests.conftest import RecordingPublisher


@pytest.mark.django_db
def test_parse_rate_below_eighty_percent_of_baseline_emits_adapter_broken(
    source: Source, publisher: RecordingPublisher
) -> None:
    measured_at = datetime(2026, 8, 21, 9, tzinfo=UTC)
    baseline = record_adapter_health(source, 100, 100, measured_at - timedelta(days=1))
    broken = record_adapter_health(source, 100, 79, measured_at)
    repeated = record_adapter_health(source, 100, 70, measured_at + timedelta(minutes=5))
    flush_outbox(publisher)

    assert baseline.alerted is False
    assert broken.alerted is True
    assert repeated.alerted is True
    assert OutboxEvent.objects.filter(topic=REVIEW_TOPIC).count() == 1
    assert len(publisher.messages) == 1
    topic, key, event = publisher.messages[0]
    assert topic == REVIEW_TOPIC
    assert key == "adapter-broken:test-source:2026-08-21"
    assert event["payload"]["kind"] == "adapter_broken"
    assert event["payload"]["subject"] == {"source_key": "test-source"}
    assert event["payload"]["evidence"]["parsed_ok"] == 79
