from __future__ import annotations

import json
import logging
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message
from django.conf import settings
from django.core.management.base import BaseCommand

from billing.consumer import SELLERS_CHANGED_TOPIC, process_seller_event

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume the replay-safe sellers.changed local read model"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        consumer = Consumer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "group.id": settings.KAFKA_CONSUMER_GROUP,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe([SELLERS_CHANGED_TOPIC])
        try:
            while True:
                message: Message | None = consumer.poll(1.0)
                if message is None:
                    if options["once"]:
                        return
                    continue
                error = message.error()
                if error is not None:
                    if error.code() == KafkaError._PARTITION_EOF:
                        if options["once"]:
                            return
                        continue
                    raise KafkaException(error)
                self._process_message(consumer, message)
                if options["once"]:
                    return
        finally:
            consumer.close()

    def _process_message(self, consumer: Consumer, message: Message) -> None:
        raw_key = message.key()
        if raw_key is None:
            raise ValueError("sellers.changed requires a Kafka key")
        raw_value = message.value()
        if raw_value is None:
            raise ValueError("sellers.changed requires a message body")
        body = json.loads(raw_value)
        changed = process_seller_event(body, message_key=raw_key.decode())
        # The offset moves only after the transaction above commits durably.
        consumer.commit(message=message, asynchronous=False)
        logger.info(
            "seller event consumed",
            extra={
                "event": "seller_event_consumed",
                "seller_key": raw_key.decode(),
                "changed": changed,
            },
        )
