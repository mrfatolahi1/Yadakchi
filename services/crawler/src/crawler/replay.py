import time
from datetime import datetime

from crawler.models import CrawlCursor, Observation, Source
from crawler.observations import event_from_observation
from crawler.producer import LISTINGS_TOPIC, EventPublisher


def replay_observations(
    source: Source,
    publisher: EventPublisher,
    since: datetime | None = None,
    until: datetime | None = None,
    rate_per_second: float = 50.0,
    reset: bool = False,
) -> int:
    tier = "replay-listings-v2"
    cursor, _ = CrawlCursor.objects.get_or_create(source=source, tier=tier)
    if reset:
        cursor.position = "0"
        cursor.save(update_fields=("position", "updated_at"))
    try:
        position = int(cursor.position)
    except ValueError:
        position = 0

    observations = Observation.objects.select_related("source", "archive_document").filter(
        source=source, pk__gt=position
    )
    if since is not None:
        observations = observations.filter(observed_at__gte=since)
    if until is not None:
        observations = observations.filter(observed_at__lte=until)

    delay = 1 / rate_per_second if rate_per_second > 0 else 0
    emitted = 0
    for observation in observations.order_by("pk").iterator(chunk_size=500):
        event = event_from_observation(observation)
        publisher.publish(
            LISTINGS_TOPIC,
            f"{source.key}:{observation.external_key}",
            event,
        )
        cursor.position = str(observation.pk)
        cursor.save(update_fields=("position", "updated_at"))
        emitted += 1
        if delay:
            time.sleep(delay)
    return emitted
