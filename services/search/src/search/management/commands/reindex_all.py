from __future__ import annotations

import time
import uuid
from argparse import ArgumentParser
from typing import Any

from confluent_kafka import Consumer, KafkaError
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from search.indexer import handle_product_event
from search.kafka import apply_then_commit
from search.models import ProductState, ReindexRun
from search.services import get_embedding_client, get_index
from search.synonyms import approved_synonyms


class Command(BaseCommand):
    help = "Rebuild Typesense by replaying the compacted products topic from its beginning."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--new",
            action="store_true",
            help="Start a fresh rebuild instead of resuming the current run.",
        )
        parser.add_argument(
            "--idle-seconds",
            type=float,
            default=10.0,
            help="Mark the replay complete after this many seconds without a message.",
        )

    def handle(self, *args: object, **options: Any) -> None:
        del args
        index = get_index()
        embeddings = get_embedding_client()
        run = ReindexRun.objects.filter(status="running").order_by("started_at").first()
        if options["new"] or run is None:
            index.reset_collection()
            for part_type, tokens in approved_synonyms().items():
                index.upsert_synonym(part_type, tokens)
            with transaction.atomic():
                ProductState.objects.all().delete()
                if run is not None:
                    run.status = "superseded"
                    run.completed_at = timezone.now()
                    run.save(update_fields=("status", "completed_at"))
                run = ReindexRun.objects.create(consumer_group=f"search-reindex-{uuid.uuid4()}")
        assert run is not None
        consumer = Consumer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "group.id": run.consumer_group,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "enable.partition.eof": True,
            }
        )
        consumer.subscribe(["yadakchi.products.changed.v1"])
        idle_started = time.monotonic()
        rate = settings.SEARCH_REINDEX_RATE_PER_SECOND
        delay = 1.0 / rate if rate > 0 else 0.0
        try:
            while time.monotonic() - idle_started < float(options["idle_seconds"]):
                message = consumer.poll(1.0)
                if message is None:
                    continue
                error = message.error()
                if error:
                    if error.code() == KafkaError._PARTITION_EOF:
                        continue
                    raise RuntimeError(str(message.error()))
                apply_then_commit(
                    consumer,
                    message,
                    lambda body, key: handle_product_event(body, key, index, embeddings),
                )
                ReindexRun.objects.filter(run_uid=run.run_uid).update(
                    processed_count=run.processed_count + 1
                )
                run.processed_count += 1
                idle_started = time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        finally:
            consumer.close()
        run.status = "complete"
        run.completed_at = timezone.now()
        run.save(update_fields=("status", "completed_at", "processed_count"))
        self.stdout.write(
            self.style.SUCCESS(f"reindex complete: {run.processed_count} product events")
        )
