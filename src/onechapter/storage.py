from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path

from onechapter import config

log = logging.getLogger(__name__)

MAX_SAFE_FILENAME_LEN = 200


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(filename: str) -> str:
    raw = (filename or "book").replace("\x00", "")
    leaf = re.split(r"[/\\]+", raw)[-1].strip()
    leaf = "".join(ch for ch in leaf if ord(ch) >= 32)
    leaf = leaf.lstrip(". ").strip()
    if not leaf or leaf in {".", ".."}:
        return "book"
    p = Path(leaf)
    suffix = p.suffix[:20]
    stem = p.stem or "book"
    max_stem = max(1, MAX_SAFE_FILENAME_LEN - len(suffix))
    return f"{stem[:max_stem]}{suffix}"


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
    create_temp: bool = False,
) -> tuple[str, str, Path | None]:
    """Persist validated book bytes to S3 (or local uploads/) after extraction/splitting succeeds.

    When ONECHAPTER_S3_BUCKET is not set (local dev/tests), atomically writes a durable local
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
        if not create_temp:
            return digest, key, None
        suffix = Path(safe_name).suffix or ".book"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{digest[:12]}-",
            suffix=suffix,
            dir=config.DATA_DIR / "tmp",
        )
        with open(fd, "wb") as handle:
            handle.write(data)
        return digest, key, Path(tmp_name)

    local_dest = config.UPLOAD_DIR / key
    parent = local_dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(prefix=".orig-", dir=parent)
    tmp_path = Path(tmp_path_str)
    try:
        with open(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, local_dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        try:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
        raise
    return digest, key, local_dest


def delete_original(s3_key: str, *, s3_client=None) -> None:
    """Best-effort cleanup of a newly written original if surrounding transaction rolls back."""
    if not s3_key:
        return
    bucket = config.s3_bucket()
    if bucket:
        client = s3_client or _s3_client()
        if hasattr(client, "delete_object"):
            try:
                client.delete_object(Bucket=bucket, Key=s3_key)
            except Exception:
                log.warning("Failed to clean up S3 object %s after rollback", s3_key, exc_info=True)
        return

    local_dest = config.UPLOAD_DIR / s3_key
    try:
        if local_dest.exists():
            local_dest.unlink(missing_ok=True)
        parent = local_dest.parent
        originals_root = (config.UPLOAD_DIR / "originals").resolve()
        if (
            parent.exists()
            and parent.resolve() != originals_root
            and parent.resolve().is_relative_to(originals_root)
            and not any(parent.iterdir())
        ):
            parent.rmdir()
    except OSError:
        log.warning("Failed to clean up local upload %s after rollback", s3_key, exc_info=True)


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
        try:
            with open(fd, "wb") as handle:
                resp = client.get_object(Bucket=bucket, Key=s3_key)
                handle.write(resp["Body"].read())
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
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
