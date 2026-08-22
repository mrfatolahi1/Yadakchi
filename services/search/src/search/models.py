from __future__ import annotations

import uuid
from typing import ClassVar

from django.db import models


class ProductState(models.Model):
    product_uid = models.UUIDField(primary_key=True)
    payload = models.JSONField(default=dict)
    part_type = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    event_occurred_at = models.DateTimeField()
    source_updated_at = models.DateTimeField(null=True, blank=True)
    index_applied_at = models.DateTimeField(null=True, blank=True)
    embedding = models.JSONField(default=list)
    embedding_text_hash = models.CharField(max_length=64, blank=True, default="")
    trace_id = models.CharField(max_length=255, blank=True, default="")


class ProductPartNumber(models.Model):
    product = models.ForeignKey(
        ProductState, on_delete=models.CASCADE, related_name="base_part_numbers"
    )
    code = models.CharField(max_length=128, db_index=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=("product", "code"), name="search_unique_product_part_number"
            )
        ]


class SynonymDecision(models.Model):
    request_uid = models.CharField(max_length=255, primary_key=True)
    token = models.CharField(max_length=255, blank=True, default="")
    part_type = models.CharField(max_length=128, blank=True, default="", db_index=True)
    decision = models.CharField(max_length=32, blank=True, default="")
    active = models.BooleanField(default=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    event_occurred_at = models.DateTimeField()
    index_applied_at = models.DateTimeField(null=True, blank=True)
    trace_id = models.CharField(max_length=255, blank=True, default="")


class VehicleState(models.Model):
    vehicle_slug = models.CharField(max_length=128, primary_key=True)
    payload = models.JSONField(default=dict)
    is_published = models.BooleanField(default=False, db_index=True)
    event_occurred_at = models.DateTimeField()
    trace_id = models.CharField(max_length=255, blank=True, default="")


class CrossReference(models.Model):
    pair_key = models.CharField(max_length=260, primary_key=True)
    code_a = models.CharField(max_length=128, blank=True, default="", db_index=True)
    code_b = models.CharField(max_length=128, blank=True, default="", db_index=True)
    confidence = models.FloatField(default=0.0)
    provenance = models.CharField(max_length=32, blank=True, default="")
    active = models.BooleanField(default=True)
    event_occurred_at = models.DateTimeField()
    index_applied_at = models.DateTimeField(null=True, blank=True)
    trace_id = models.CharField(max_length=255, blank=True, default="")


class QueryLog(models.Model):
    query_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    normalized_text = models.TextField(db_index=True)
    vehicle_slug = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    filters = models.JSONField(default=dict)
    result_count = models.PositiveIntegerField(default=0, db_index=True)
    result_product_uids = models.JSONField(default=list)
    clicked_position = models.PositiveIntegerField(null=True, blank=True)
    clicked_product_uid = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    clicked_at = models.DateTimeField(null=True, blank=True)


class ReindexRun(models.Model):
    run_uid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consumer_group = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=16, default="running", db_index=True)
    processed_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
