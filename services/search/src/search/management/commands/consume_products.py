from __future__ import annotations

import logging

from confluent_kafka import Consumer, KafkaError
from django.conf import settings
from django.core.management.base import BaseCommand

from search.indexer import handle_product_event
from search.kafka import apply_then_commit
from search.services import get_embedding_client, get_index

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume the compacted products.changed topic and maintain the Typesense index."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        index = get_index()
        embeddings = get_embedding_client()
        index.ensure_collection()
        consumer = Consumer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "group.id": "search-products-v1",
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe(["yadakchi.products.changed.v1"])
        try:
            while True:
                message = consumer.poll(1.0)
                if message is None:
                    continue
                error = message.error()
                if error:
                    if error.code() != KafkaError._PARTITION_EOF:
                        logger.error(
                            "Kafka consumer error", extra={"event_type": "products.changed"}
                        )
                    continue
                apply_then_commit(
                    consumer,
                    message,
                    lambda body, key: handle_product_event(body, key, index, embeddings),
                )
        finally:
            consumer.close()
