from __future__ import annotations

from uuid import uuid4

from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from fitment.models import CrossRef, Vehicle
from fitment.producer import (
    queue_crossref_changed,
    queue_crossref_tombstone,
    queue_vehicle_changed,
    queue_vehicle_tombstone,
)


def new_trace_id() -> str:
    return uuid4().hex


@receiver(post_save, sender=Vehicle)
def emit_vehicle_change(sender: type[Vehicle], instance: Vehicle, **kwargs: object) -> None:
    del sender, kwargs
    queue_vehicle_changed(instance, trace_id=new_trace_id())


@receiver(pre_delete, sender=Vehicle)
def remember_vehicle_key(sender: type[Vehicle], instance: Vehicle, **kwargs: object) -> None:
    del sender, kwargs
    instance._deleted_slug = instance.slug  # type: ignore[attr-defined]


@receiver(post_delete, sender=Vehicle)
def emit_vehicle_delete(sender: type[Vehicle], instance: Vehicle, **kwargs: object) -> None:
    del sender, kwargs
    queue_vehicle_tombstone(instance.slug, trace_id=new_trace_id())


@receiver(post_save, sender=CrossRef)
def emit_crossref_change(sender: type[CrossRef], instance: CrossRef, **kwargs: object) -> None:
    del sender, kwargs
    queue_crossref_changed(instance, trace_id=new_trace_id())


@receiver(post_delete, sender=CrossRef)
def emit_crossref_delete(sender: type[CrossRef], instance: CrossRef, **kwargs: object) -> None:
    del sender, kwargs
    queue_crossref_tombstone(instance.code_a, instance.code_b, trace_id=new_trace_id())
