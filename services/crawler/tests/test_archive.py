import gzip
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from crawler.archive import ArchiveService, object_key_from_uri
from crawler.models import ArchivedDocument, Source


class MemoryObjectStore:
    bucket = "raw-archive"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_count = 0

    def ensure_bucket(self) -> None:
        return None

    def exists(self, key: str) -> bool:
        return key in self.objects

    def put(self, key: str, body: bytes, page_hash: str) -> None:
        del page_hash
        self.put_count += 1
        self.objects[key] = body

    def get(self, key: str) -> bytes:
        return self.objects[key]


@pytest.mark.django_db
def test_archive_round_trip_hash_matches_recorded_page_hash(source: Source) -> None:
    store = MemoryObjectStore()
    service = ArchiveService(store)
    raw = b"\x00seller-page\xff\nraw"

    result = service.archive(
        source,
        "https://seller.example/page/1",
        raw,
        200,
        datetime(2026, 8, 21, 8, tzinfo=UTC),
    )

    key = object_key_from_uri(result.document.archive_uri, store.bucket)
    restored = gzip.decompress(store.get(key))
    assert restored == raw
    assert hashlib.sha256(restored).hexdigest() == result.document.page_hash


@pytest.mark.django_db
def test_unchanged_page_reuses_archive_object(source: Source) -> None:
    store = MemoryObjectStore()
    service = ArchiveService(store)
    first_at = datetime(2026, 8, 21, 8, tzinfo=UTC)

    first = service.archive(source, "https://seller.example/page/1", b"same", 200, first_at)
    second = service.archive(
        source,
        "https://seller.example/page/1",
        b"same",
        200,
        first_at + timedelta(days=1),
    )

    assert first.object_created is True
    assert second.object_created is False
    assert first.document.pk == second.document.pk
    assert store.put_count == 1
    assert ArchivedDocument.objects.count() == 1
    second.document.refresh_from_db()
    assert second.document.seen_count == 2
