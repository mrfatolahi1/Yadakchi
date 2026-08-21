import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

from crawler.events import ReviewRequestedEvent, ReviewRequestedPayload
from crawler.metrics import ADAPTER_HEALTH_ALERTS
from crawler.models import AdapterHealth, OutboxEvent, Source
from crawler.producer import REVIEW_TOPIC, validate_event


@transaction.atomic
def record_adapter_health(
    source: Source,
    attempted: int,
    parsed_ok: int,
    window: datetime | None = None,
) -> AdapterHealth:
    measured_at = window or timezone.now()
    parse_rate = Decimal(parsed_ok) / Decimal(attempted) if attempted else Decimal(0)
    baseline_value = AdapterHealth.objects.filter(
        source=source,
        window__gte=measured_at - timedelta(days=7),
        window__lt=measured_at,
        attempted__gt=0,
    ).aggregate(value=Avg("parse_rate"))["value"]
    baseline = Decimal(baseline_value) if baseline_value is not None else None
    should_alert = baseline is not None and parse_rate < baseline * Decimal("0.80")

    health = AdapterHealth.objects.create(
        source=source,
        window=measured_at,
        attempted=attempted,
        parsed_ok=parsed_ok,
        parse_rate=parse_rate,
        baseline_rate=baseline,
        alerted=should_alert,
    )
    if not should_alert:
        return health
    assert baseline is not None

    request_uid = f"adapter-broken:{source.key}:{measured_at.date().isoformat()}"
    event_id = uuid.uuid4()
    trace_id = uuid.uuid4().hex
    event = ReviewRequestedEvent(
        event_id=event_id,
        occurred_at=measured_at,
        trace_id=trace_id,
        payload=ReviewRequestedPayload(
            request_uid=request_uid,
            kind="adapter_broken",
            priority=source.priority,
            subject={"source_key": source.key},
            evidence={
                "source_key": source.key,
                "source_name": source.name,
                "base_url": source.base_url,
                "adapter_key": source.adapter_key,
                "attempted": attempted,
                "parsed_ok": parsed_ok,
                "parse_rate": float(parse_rate),
                "baseline_rate": float(baseline),
                "window": measured_at.isoformat().replace("+00:00", "Z"),
            },
            requested_at=measured_at,
        ),
    ).model_dump(mode="json")
    validate_event(REVIEW_TOPIC, event)
    _, created = OutboxEvent.objects.get_or_create(
        dedupe_key=request_uid,
        defaults={
            "event_id": event_id,
            "topic": REVIEW_TOPIC,
            "key": request_uid,
            "body": event,
        },
    )
    if created:
        ADAPTER_HEALTH_ALERTS.labels(source=source.key).inc()
    return health
