#!/bin/sh
set -eu

run_once() {
    TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    TMP_DUMP="/tmp/onechapter-${TIMESTAMP}.dump"
    echo "[db-backup] Creating PostgreSQL dump ${TMP_DUMP}..."
    pg_dump -Fc -f "${TMP_DUMP}"

    uv run --no-dev python - "${TMP_DUMP}" "${TIMESTAMP}" <<'PY'
import os
import sys
import boto3

dump_path = sys.argv[1]
timestamp = sys.argv[2]
bucket = os.environ["ONECHAPTER_S3_BUCKET"].strip()
region = os.environ.get("AWS_REGION", "eu-north-1")

s3 = boto3.client("s3", region_name=region)
dated_key = f"backups/postgres/onechapter-{timestamp}.dump"
latest_key = "backups/postgres/latest.dump"

with open(dump_path, "rb") as fh:
    data = fh.read()

for key in (dated_key, latest_key):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ServerSideEncryption="AES256",
    )
print(f"[db-backup] Uploaded s3://{bucket}/{dated_key} and {latest_key}")
PY
    rm -f "${TMP_DUMP}"
}

if [ "${1:-}" = "--loop" ]; then
    INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
    while true; do
        run_once
        sleep "${INTERVAL}"
    done
else
    run_once
fi
