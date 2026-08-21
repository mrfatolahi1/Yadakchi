import gzip
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import boto3
from botocore.exceptions import ClientError
from django.db import transaction
from django.db.models import F

from crawler.metrics import ARCHIVE_DEDUPLICATIONS
from crawler.models import ArchivedDocument, Source


class ObjectStore(Protocol):
    bucket: str

    def ensure_bucket(self) -> None: ...

    def exists(self, key: str) -> bool: ...

    def put(self, key: str, body: bytes, page_hash: str) -> None: ...

    def get(self, key: str) -> bytes: ...


class S3ObjectStore:
    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                    raise

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def put(self, key: str, body: bytes, page_hash: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/octet-stream",
            ContentEncoding="gzip",
            Metadata={"sha256": page_hash},
        )

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return bytes(response["Body"].read())


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    document: ArchivedDocument
    object_created: bool


class ArchiveService:
    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    @transaction.atomic
    def archive(
        self,
        source: Source,
        url: str,
        raw: bytes,
        http_status: int,
        fetched_at: datetime,
        error: str = "",
    ) -> ArchiveResult:
        page_hash = hashlib.sha256(raw).hexdigest()
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        latest = (
            ArchivedDocument.objects.select_for_update()
            .filter(source=source, url_hash=url_hash)
            .order_by("-fetched_at", "-id")
            .first()
        )
        if latest is not None and latest.page_hash == page_hash:
            ArchivedDocument.objects.filter(pk=latest.pk).update(
                last_seen_at=fetched_at, seen_count=F("seen_count") + 1
            )
            latest.refresh_from_db(fields=("last_seen_at", "seen_count"))
            ARCHIVE_DEDUPLICATIONS.labels(source=source.key).inc()
            return ArchiveResult(latest, False)

        key = f"{source.key}/{fetched_at:%Y}/{fetched_at:%m}/{fetched_at:%d}/{page_hash}.gz"
        object_created = not self.store.exists(key)
        if object_created:
            self.store.put(key, gzip.compress(raw, mtime=0), page_hash)
        else:
            ARCHIVE_DEDUPLICATIONS.labels(source=source.key).inc()

        archive_uri = f"s3://{self.store.bucket}/{key}"
        document, created = ArchivedDocument.objects.get_or_create(
            source=source,
            url_hash=url_hash,
            page_hash=page_hash,
            defaults={
                "url": url,
                "http_status": http_status,
                "fetched_at": fetched_at,
                "last_seen_at": fetched_at,
                "archive_uri": archive_uri,
                "error": error,
            },
        )
        if not created:
            ArchivedDocument.objects.filter(pk=document.pk).update(
                last_seen_at=fetched_at, seen_count=F("seen_count") + 1
            )
            document.refresh_from_db(fields=("last_seen_at", "seen_count"))
        return ArchiveResult(document, object_created)


def object_key_from_uri(uri: str, bucket: str) -> str:
    prefix = f"s3://{bucket}/"
    if not uri.startswith(prefix):
        raise ValueError(f"Archive URI is not in bucket {bucket!r}")
    return uri.removeprefix(prefix)
