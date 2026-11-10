#!/bin/sh
set -eu

S3_KEY="${1:-backups/postgres/latest.dump}"
TMP_DUMP="/tmp/restore.dump"

echo "[db-restore] Downloading s3://${ONECHAPTER_S3_BUCKET}/${S3_KEY}..."
uv run --no-dev python - "${S3_KEY}" "${TMP_DUMP}" <<'PY'
import os
import sys
import boto3

s3_key = sys.argv[1]
dest_path = sys.argv[2]
bucket = os.environ["ONECHAPTER_S3_BUCKET"].strip()
region = os.environ.get("AWS_REGION", "eu-north-1")

s3 = boto3.client("s3", region_name=region)
resp = s3.get_object(Bucket=bucket, Key=s3_key)
with open(dest_path, "wb") as fh:
    fh.write(resp["Body"].read())
PY

echo "[db-restore] Restoring into PostgreSQL..."
pg_restore --clean --if-exists --no-owner --no-privileges -d "${PGDATABASE:-onechapter}" "${TMP_DUMP}"
rm -f "${TMP_DUMP}"
echo "[db-restore] Restore complete."
