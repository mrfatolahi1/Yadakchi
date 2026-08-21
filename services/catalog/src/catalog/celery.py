"""Celery application. Celery is for work *inside* this service only —
scheduled recomputation and partition maintenance. Kafka consumers are
long-running management commands, never Celery tasks.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "catalog.settings")

app = Celery("catalog")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["catalog"])

app.conf.beat_schedule = {
    # The debounce flush: material changes accumulate, this emits them once.
    "flush-pending-products": {
        "task": "catalog.flush_pending_products",
        "schedule": 15.0,
    },
    # A seller provisioned from a new offer, or edited in the admin, reaches
    # billing and ops on this beat rather than waiting for the nightly trust run.
    "flush-pending-sellers": {
        "task": "catalog.flush_pending_sellers",
        "schedule": 60.0,
    },
    # Trust is recomputed on a schedule, per SPEC.md part four.
    "recompute-trust": {
        "task": "catalog.recompute_trust",
        "schedule": crontab(minute=0, hour=2),
    },
    "make-partitions": {
        "task": "catalog.make_partitions",
        "schedule": crontab(minute=30, hour=3, day_of_month=25),
    },
    "prune-processed-events": {
        "task": "catalog.prune_processed_events",
        "schedule": crontab(minute=15, hour=4),
    },
}
