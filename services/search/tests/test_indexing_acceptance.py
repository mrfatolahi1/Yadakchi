from __future__ import annotations

from copy import deepcopy
from io import StringIO
from uuid import UUID

import pytest
from django.core.management import call_command

from search.indexer import (
    handle_cross_reference_event,
    handle_product_event,
    handle_review_event,
)
from search.models import ProductState, QueryLog
from search.synonyms import approved_synonyms
from tests.factories import later, product_event, product_payload, review_event
from tests.fakes import FakeEmbeddings, FakeIndex


@pytest.mark.django_db
def test_unapproved_candidate_synonym_never_reaches_expansion() -> None:
    index = FakeIndex()

    handle_review_event(review_event(decision="reject"), "synonym-1", index, FakeEmbeddings())

    assert approved_synonyms() == {}
    assert index.synonyms == {}


@pytest.mark.django_db
def test_approved_synonym_is_denormalized_into_affected_documents() -> None:
    index = FakeIndex()
    embeddings = FakeEmbeddings()
    payload = product_payload(title="بلبرینگ چرخ", part_type="wheel_bearing")
    handle_product_event(product_event(payload), str(payload["product_uid"]), index, embeddings)

    handle_review_event(review_event(decision="approve"), "synonym-1", index, embeddings)

    document = index.documents[str(payload["product_uid"])]
    assert document["part_type_synonyms"] == ["توپی چرخ"]
    assert index.synonyms["wheel_bearing"] == ["توپی چرخ"]


@pytest.mark.django_db
def test_full_product_replay_reproduces_incremental_index_exactly() -> None:
    first_uid = UUID("00000000-0000-4000-8000-000000000001")
    second_uid = UUID("00000000-0000-4000-8000-000000000002")
    first = product_payload(first_uid, title="لنت ترمز نسخه اول", part_numbers=["425438"])
    second = product_payload(second_uid, title="فیلتر روغن", part_numbers=["SF7710"])
    updated = product_payload(
        first_uid,
        title="لنت ترمز نسخه نهایی",
        part_numbers=["425438"],
        updated_at=later(2),
    )
    events = [
        product_event(first),
        product_event(second, occurred_at=later(1)),
        product_event(updated, occurred_at=later(2)),
    ]
    index = FakeIndex()
    embeddings = FakeEmbeddings()
    for event in events:
        handle_product_event(event, str(event["payload"]["product_uid"]), index, embeddings)
    incremental = deepcopy(index.documents)

    ProductState.objects.all().delete()
    index.reset_collection()
    for event in events:
        handle_product_event(event, str(event["payload"]["product_uid"]), index, embeddings)

    assert index.documents == incremental


@pytest.mark.django_db
def test_unpublishing_removes_product_within_same_consume_cycle() -> None:
    index = FakeIndex()
    embeddings = FakeEmbeddings()
    payload = product_payload()
    uid = str(payload["product_uid"])
    handle_product_event(product_event(payload), uid, index, embeddings)
    unpublished = deepcopy(payload)
    unpublished["is_published"] = False
    unpublished["updated_at"] = later(1).isoformat().replace("+00:00", "Z")

    handle_product_event(product_event(unpublished, occurred_at=later(1)), uid, index, embeddings)

    assert uid not in index.documents
    assert ProductState.objects.get(product_uid=uid).is_published is False


@pytest.mark.django_db
def test_duplicate_product_delivery_yields_one_document_and_one_upsert() -> None:
    index = FakeIndex()
    embeddings = FakeEmbeddings()
    payload = product_payload()
    event = product_event(payload)
    uid = str(payload["product_uid"])

    handle_product_event(event, uid, index, embeddings)
    handle_product_event(event, uid, index, embeddings)

    assert list(index.documents) == [uid]
    assert index.upsert_count == 1
    assert ProductState.objects.filter(product_uid=uid).count() == 1


@pytest.mark.django_db
def test_first_seen_product_tombstone_is_durable_and_idempotent() -> None:
    index = FakeIndex()
    uid = "00000000-0000-4000-8000-000000000099"
    tombstone = product_event(None)

    handle_product_event(tombstone, uid, index, FakeEmbeddings())
    handle_product_event(tombstone, uid, index, FakeEmbeddings())

    state = ProductState.objects.get(product_uid=uid)
    assert state.is_published is False
    assert state.payload == {}
    assert state.index_applied_at == state.event_occurred_at
    assert index.delete_count == 1


@pytest.mark.django_db
def test_failed_index_write_replays_pending_product_event() -> None:
    class FailOnceIndex(FakeIndex):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def upsert(self, document: dict[str, object]) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("Typesense unavailable")
            super().upsert(document)

    index = FailOnceIndex()
    payload = product_payload()
    event = product_event(payload)
    uid = str(payload["product_uid"])

    with pytest.raises(RuntimeError, match="Typesense unavailable"):
        handle_product_event(event, uid, index, FakeEmbeddings())
    assert ProductState.objects.get(product_uid=uid).index_applied_at is None

    handle_product_event(event, uid, index, FakeEmbeddings())

    assert uid in index.documents
    assert ProductState.objects.get(product_uid=uid).index_applied_at is not None


@pytest.mark.django_db
def test_cross_reference_tombstone_uses_canonical_pair_identity() -> None:
    index = FakeIndex()
    embeddings = FakeEmbeddings()
    payload = product_payload(part_numbers=["0K30E14302"])
    uid = str(payload["product_uid"])
    handle_product_event(product_event(payload), uid, index, embeddings)
    live = {
        "event_id": "00000000-0000-4000-8000-000000000010",
        "event_type": "crossrefs.changed",
        "version": 1,
        "occurred_at": "2026-08-20T12:00:01Z",
        "producer": "fitment",
        "trace_id": "test-trace",
        "payload": {
            "code_a": "0K30E-14-302",
            "code_b": "ALT-123",
            "confidence": 1.0,
            "provenance": "human",
            "updated_at": "2026-08-20T12:00:01Z",
        },
    }
    handle_cross_reference_event(live, "0K30E-14-302|ALT-123", index, embeddings)
    assert "ALT123" in index.documents[uid]["part_numbers"]
    tombstone = {**live, "occurred_at": "2026-08-20T12:00:02Z", "payload": None}

    handle_cross_reference_event(tombstone, "0K30E-14-302|ALT-123", index, embeddings)

    assert index.documents[uid]["part_numbers"] == ["0K30E14302"]


@pytest.mark.django_db
def test_older_product_event_cannot_overwrite_newer_state() -> None:
    index = FakeIndex()
    embeddings = FakeEmbeddings()
    payload = product_payload(title="نسخه قدیمی")
    uid = str(payload["product_uid"])
    newer = product_payload(
        UUID(uid), title="نسخه جدید", part_numbers=payload["part_numbers"], updated_at=later(2)
    )
    handle_product_event(product_event(newer, occurred_at=later(2)), uid, index, embeddings)
    handle_product_event(product_event(payload), uid, index, embeddings)
    assert index.documents[uid]["title"] == "نسخه جدید"


@pytest.mark.django_db
def test_zero_result_queries_are_logged_and_weekly_report_has_rows() -> None:
    QueryLog.objects.create(
        normalized_text="قطعه ناموجود",
        vehicle_slug="peugeot-206-type-5",
        filters={"brand": "missing"},
        result_count=0,
    )
    output = StringIO()

    call_command("zero_result_report", stdout=output)

    report = output.getvalue()
    assert "normalized_text" in report
    assert "قطعه ناموجود" in report
