from __future__ import annotations

import logging

from confluent_kafka import Consumer, KafkaError
from django.conf import settings
from django.core.management.base import BaseCommand

from search.indexer import (
    handle_cross_reference_event,
    handle_review_event,
    handle_vehicle_event,
)
from search.kafka import apply_then_commit
from search.services import get_embedding_client, get_index

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume vehicles, cross-references and approved synonym decisions."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        index = get_index()
        embeddings = get_embedding_client()
        index.ensure_collection()
        topics = [
            "yadakchi.vehicles.changed.v1",
            "yadakchi.crossrefs.changed.v1",
            "yadakchi.review.decided.v1",
        ]
        consumer = Consumer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "group.id": "search-reference-v1",
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe(topics)

        def dispatch(body: dict[str, object], key: str | None) -> object:
            event_type = body.get("event_type")
            if event_type == "vehicles.changed":
                return handle_vehicle_event(body, key)
            if event_type == "crossrefs.changed":
                return handle_cross_reference_event(body, key, index, embeddings)
            if event_type == "review.decided":
                return handle_review_event(body, key, index, embeddings)
            raise ValueError(f"unsupported reference event_type: {event_type}")

        try:
            while True:
                message = consumer.poll(1.0)
                if message is None:
                    continue
                error = message.error()
                if error:
                    if error.code() != KafkaError._PARTITION_EOF:
                        logger.error("Kafka reference consumer error")
                    continue
                apply_then_commit(consumer, message, dispatch)
        finally:
            consumer.close()
