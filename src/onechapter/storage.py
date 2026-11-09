from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path

from onechapter import config

log = logging.getLogger(__name__)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(filename: str) -> str:
    name = Path(filename or "book").name.strip()
    return name or "book"


def build_original_key(sha256: str, filename: str) -> str:
    return f"originals/{sha256}/{safe_filename(filename)}"


def build_reflection_audio_key(reader_id: int, reflection_uuid: str, ext: str = "ogg") -> str:
    clean_ext = ext.lstrip(".") or "ogg"
    return f"media/reflections/{reader_id}/{reflection_uuid}.{clean_ext}"


def _s3_client():
    import boto3

    return boto3.client("s3", region_name=config.AWS_REGION)


def store_original(
    data: bytes,
    filename: str,
    *,
    s3_client=None,
) -> tuple[str, str, Path]:
    """Store uploaded book bytes in S3 (when configured) and return (sha256, s3_key, local_temp_path).

    When ONECHAPTER_S3_BUCKET is not set (local dev/tests), stores a durable local
    copy under DATA_DIR/uploads/originals/{sha256}/{filename} using the identical key layout.
    """
    config.ensure_dirs()
    digest = sha256_bytes(data)
    safe_name = safe_filename(filename)
    key = build_original_key(digest, safe_name)
    bucket = config.s3_bucket()

    if bucket:
        client = s3_client or _s3_client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ServerSideEncryption="AES256",
        )
        suffix = Path(safe_name).suffix or ".book"
        fd, tmp_name = tempfile.mkstemp(prefix=f"{digest[:12]}-", suffix=suffix, dir=config.DATA_DIR / "tmp")
        with open(fd, "wb") as handle:
            handle.write(data)
        return digest, key, Path(tmp_name)

    local_dest = config.UPLOAD_DIR / key
    local_dest.parent.mkdir(parents=True, exist_ok=True)
    local_dest.write_bytes(data)
    return digest, key, local_dest


def fetch_original_to_temp(s3_key: str, *, s3_client=None) -> tuple[Path, bool]:
    """Download or locate an original book file by s3_key.

    Returns (path, should_cleanup).
    """
    config.ensure_dirs()
    bucket = config.s3_bucket()
    safe_name = safe_filename(s3_key)
    suffix = Path(safe_name).suffix or ".book"

    if bucket:
        client = s3_client or _s3_client()
        fd, tmp_name = tempfile.mkstemp(prefix="reprocess-", suffix=suffix, dir=config.DATA_DIR / "tmp")
        tmp_path = Path(tmp_name)
        with open(fd, "wb") as handle:
            resp = client.get_object(Bucket=bucket, Key=s3_key)
            handle.write(resp["Body"].read())
        return tmp_path, True

    candidate = config.UPLOAD_DIR / s3_key
    if candidate.exists():
        return candidate, False
    fallback = config.UPLOAD_DIR / safe_name
    if fallback.exists():
        return fallback, False
    raise FileNotFoundError(f"Original file not found for key: {s3_key}")


def presign_reflection_audio_get(
    s3_key: str,
    *,
    expires_in: int = 900,
    s3_client=None,
) -> str | None:
    if not s3_key:
        return None
    bucket = config.s3_bucket()
    if not bucket:
        return None
    client = s3_client or _s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expires_in,
    )
