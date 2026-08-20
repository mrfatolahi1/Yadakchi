#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# Provision the raw archive bucket and the ONE identity allowed to touch it.
#
# `crawler` owns raw storage. Everything downstream rebuilds from Kafka events,
# never from MinIO, so no other service gets credentials here. Idempotent:
# every step tolerates already existing.
#
# Runs as a short-lived init container against the minio service (see
# platform/docker-compose.infra.yml). Requires: MINIO_ROOT_USER,
# MINIO_ROOT_PASSWORD, CRAWLER_S3_ACCESS_KEY, CRAWLER_S3_SECRET_KEY.
# ---------------------------------------------------------------------------
set -eu

ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
BUCKET="${RAW_ARCHIVE_BUCKET:-raw-archive}"
POLICY_NAME="raw-archive-rw"
POLICY_FILE="/tmp/${POLICY_NAME}.json"

echo "==> waiting for MinIO at $ENDPOINT"
until mc alias set local "$ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  sleep 1
done

echo "==> bucket $BUCKET"
mc mb --ignore-existing "local/$BUCKET"
# Raw pages are the one thing we can never re-fetch. Versioning is the cheapest
# insurance against an accidental overwrite by a buggy crawler run.
mc version enable "local/$BUCKET" || true
mc anonymous set none "local/$BUCKET"

# Read and write, deliberately NO DeleteObject: raw data is never discarded,
# and a bug in the crawler must not be able to destroy the one store we cannot
# rebuild. Deletions are a root-credential, human-decision operation.
echo "==> policy $POLICY_NAME (scoped to $BUCKET only)"
cat > "$POLICY_FILE" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::$BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"],
      "Resource": ["arn:aws:s3:::$BUCKET/*"]
    }
  ]
}
JSON
mc admin policy create local "$POLICY_NAME" "$POLICY_FILE" 2>/dev/null || \
  mc admin policy add local "$POLICY_NAME" "$POLICY_FILE" 2>/dev/null || true

echo "==> user for crawler"
mc admin user add local "$CRAWLER_S3_ACCESS_KEY" "$CRAWLER_S3_SECRET_KEY" 2>/dev/null || true
mc admin policy attach local "$POLICY_NAME" --user "$CRAWLER_S3_ACCESS_KEY" 2>/dev/null || \
  mc admin policy set local "$POLICY_NAME" "user=$CRAWLER_S3_ACCESS_KEY" 2>/dev/null || true

echo "==> done: $BUCKET reachable only by root and $CRAWLER_S3_ACCESS_KEY"
