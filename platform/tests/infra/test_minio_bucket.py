"""The raw archive belongs to crawler alone. Acceptance criterion 6.

Raw pages are the one thing in this system that cannot be rebuilt, so the
bucket exists, the crawler can write to it, and nobody else has a key.
"""

from __future__ import annotations

import io
import uuid

import pytest
from conftest import requires_port
from minio import Minio
from minio.error import S3Error

pytestmark = pytest.mark.infra


@pytest.fixture(scope="module")
def endpoint(env: dict[str, str]) -> str:
    port = int(env.get("MINIO_API_PORT", "9000"))
    requires_port("127.0.0.1", port, "minio")
    return f"127.0.0.1:{port}"


@pytest.fixture(scope="module")
def bucket(env: dict[str, str]) -> str:
    return env.get("RAW_ARCHIVE_BUCKET", "raw-archive")


@pytest.fixture(scope="module")
def root_client(endpoint: str, env: dict[str, str]) -> Minio:
    return Minio(
        endpoint,
        access_key=env["MINIO_ROOT_USER"],
        secret_key=env["MINIO_ROOT_PASSWORD"],
        secure=False,
    )


@pytest.fixture(scope="module")
def crawler_client(endpoint: str, env: dict[str, str]) -> Minio:
    return Minio(
        endpoint,
        access_key=env["CRAWLER_S3_ACCESS_KEY"],
        secret_key=env["CRAWLER_S3_SECRET_KEY"],
        secure=False,
    )


def test_bucket_exists(root_client: Minio, bucket: str) -> None:
    assert root_client.bucket_exists(bucket)


def test_crawler_can_archive_and_read_back(
    crawler_client: Minio, root_client: Minio, bucket: str
) -> None:
    key = f"test/{uuid.uuid4().hex}.html"
    body = "<html>لنت ترمز جلو پژو ۲۰۶</html>".encode()
    crawler_client.put_object(bucket, key, io.BytesIO(body), length=len(body))
    try:
        response = crawler_client.get_object(bucket, key)
        assert response.read() == body
    finally:
        response.close()
        response.release_conn()
        root_client.remove_object(bucket, key)


def test_the_archive_is_append_only(crawler_client: Minio, root_client: Minio, bucket: str) -> None:
    """Raw data is never discarded — so crawler holds no delete permission.

    Everything downstream is rebuilt from these snapshots; a bug in the crawler
    must not be able to destroy the one thing we cannot re-fetch.
    """
    key = f"test/{uuid.uuid4().hex}.html"
    body = b"<html>archived</html>"
    crawler_client.put_object(bucket, key, io.BytesIO(body), length=len(body))
    try:
        with pytest.raises(S3Error) as excinfo:
            crawler_client.remove_object(bucket, key)
        assert excinfo.value.code == "AccessDenied"
    finally:
        root_client.remove_object(bucket, key)


def test_crawler_cannot_create_other_buckets(crawler_client: Minio) -> None:
    """The policy is scoped to raw-archive; nothing else is reachable."""
    with pytest.raises(S3Error) as excinfo:
        crawler_client.make_bucket(f"not-mine-{uuid.uuid4().hex[:8]}")
    assert excinfo.value.code in {"AccessDenied", "AllAccessDisabled"}


def test_wrong_credentials_are_refused(endpoint: str, bucket: str) -> None:
    # A deliberately invalid key: no service other than crawler has one.
    impostor = Minio(
        endpoint,
        access_key="matcher",
        secret_key="matcher-has-no-key",  # noqa: S106
        secure=False,
    )
    with pytest.raises(S3Error) as excinfo:
        impostor.stat_object(bucket, "anything")
    assert excinfo.value.code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied"}


def test_bucket_is_not_public(endpoint: str, bucket: str) -> None:
    import requests

    response = requests.get(f"http://{endpoint}/{bucket}/", timeout=10)
    assert response.status_code in {401, 403}
