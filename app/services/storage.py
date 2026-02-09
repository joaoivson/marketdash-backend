"""
S3-compatible Object Storage (Supabase Storage, MinIO, AWS S3).
Used by the jobs pipeline for presigned uploads and chunk storage.
"""
import logging
from io import BytesIO
from typing import BinaryIO, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    """Lazy boto3 client; returns None if storage is not configured."""
    if not all([settings.S3_BUCKET, settings.S3_ENDPOINT, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY]):
        return None
    try:
        import boto3
        from botocore.config import Config
        config = Config(signature_version="s3v4", region_name=settings.S3_REGION or "us-east-1")
        return boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=config,
        )
    except ImportError:
        logger.warning("boto3 not installed; storage operations disabled")
        return None


def is_storage_configured() -> bool:
    """Return True if S3 bucket and credentials are set."""
    return bool(
        settings.S3_BUCKET
        and settings.S3_ENDPOINT
        and settings.S3_ACCESS_KEY
        and settings.S3_SECRET_KEY
    )


def create_presigned_put(
    bucket: str,
    key: str,
    expires_in: int = 3600,
    content_type: Optional[str] = "text/csv",
) -> Optional[str]:
    """
    Generate a presigned URL for PUT (upload). Client should PUT the file body to this URL.
    """
    client = _get_client()
    if not client:
        return None
    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ContentType": content_type or "application/octet-stream",
            },
            ExpiresIn=expires_in,
        )
        return url
    except Exception as e:
        logger.error(f"Failed to generate presigned PUT: {e}")
        return None


def upload_file_obj(
    bucket: str,
    key: str,
    file_like: BinaryIO,
    content_type: Optional[str] = "text/csv",
) -> bool:
    """Upload from a file-like object (e.g. UploadFile)."""
    client = _get_client()
    if not client:
        return False
    try:
        client.upload_fileobj(
            file_like,
            bucket,
            key,
            ExtraArgs={"ContentType": content_type or "application/octet-stream"},
        )
        return True
    except Exception as e:
        logger.error(f"Upload failed for {bucket}/{key}: {e}")
        return False


def download_file(bucket: str, key: str) -> Optional[bytes]:
    """Download object to bytes. Returns None on failure."""
    client = _get_client()
    if not client:
        return None
    try:
        buf = BytesIO()
        client.download_fileobj(bucket, key, buf)
        return buf.getvalue()
    except Exception as e:
        logger.error(f"Download failed for {bucket}/{key}: {e}")
        return None


def object_exists(bucket: str, key: str) -> bool:
    """Check if object exists (HEAD)."""
    client = _get_client()
    if not client:
        return False
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def delete_object(bucket: str, key: str) -> bool:
    """Delete object. Returns True on success."""
    client = _get_client()
    if not client:
        return False
    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        logger.error(f"Delete failed for {bucket}/{key}: {e}")
        return False
