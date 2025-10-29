from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings


class MediaStorageError(RuntimeError):
    """Raised when media persistence fails."""


@dataclass
class MediaStorageResult:
    storage_path: str
    storage_filename: str
    storage_backend: str
    extra: dict[str, Any] = field(default_factory=dict)


def sanitize_filename(value: str | None) -> str:
    if not value:
        return "attachment"
    sanitized = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_", ".", " "})
    sanitized = sanitized.strip().replace(" ", "_")
    return sanitized or "attachment"


def _infer_extension(original_name: str | None, mimetype: str | None) -> str:
    if mimetype:
        guessed = mimetypes.guess_extension(mimetype, strict=False)
        if guessed:
            return guessed
    if original_name:
        suffix = Path(original_name).suffix
        if suffix:
            return suffix
    return ""


class BaseMediaStorage:
    backend_name: str = "local"

    def persist(
        self,
        *,
        content: bytes,
        content_hash: str,
        mimetype: str | None,
        original_name: str | None,
    ) -> MediaStorageResult:
        raise NotImplementedError


class LocalMediaStorage(BaseMediaStorage):
    backend_name = "local"

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def persist(
        self,
        *,
        content: bytes,
        content_hash: str,
        mimetype: str | None,
        original_name: str | None,
    ) -> MediaStorageResult:
        extension = _infer_extension(original_name, mimetype)
        subdir = self.root_dir / content_hash[:2]
        subdir.mkdir(parents=True, exist_ok=True)
        filename = f"{content_hash}{extension}"
        target = subdir / filename
        if not target.exists():
            temp = target.with_suffix(target.suffix + ".tmp")
            try:
                temp.write_bytes(content)
                temp.replace(target)
            except OSError as exc:
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass
                raise MediaStorageError(f"failed to persist media to {target}") from exc
        return MediaStorageResult(
            storage_path=target.as_posix(),
            storage_filename=filename,
            storage_backend=self.backend_name,
            extra={"local_dir": subdir.as_posix()},
        )


class S3MediaStorage(BaseMediaStorage):
    backend_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        timeout_seconds: float,
        region: str | None,
        endpoint_url: str | None,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MediaStorageError(
                "S3 storage selected but boto3 is not installed; install pricebot[storage] or set STORAGE backend to local"
            ) from exc

        normalized_prefix = prefix.strip("/")
        if normalized_prefix:
            normalized_prefix = normalized_prefix + "/"
        self.bucket = bucket
        self.prefix = normalized_prefix
        timeout = max(1.0, float(timeout_seconds))
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=BotoConfig(
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )

    def persist(
        self,
        *,
        content: bytes,
        content_hash: str,
        mimetype: str | None,
        original_name: str | None,
    ) -> MediaStorageResult:
        extension = _infer_extension(original_name, mimetype)
        key = f"{self.prefix}{content_hash[:2]}/{content_hash}{extension}"
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": content,
        }
        if mimetype:
            kwargs["ContentType"] = mimetype
        try:
            self.client.put_object(**kwargs)
        except Exception as exc:  # pragma: no cover - depends on runtime environment
            raise MediaStorageError(f"failed to upload media to s3://{self.bucket}/{key}") from exc
        return MediaStorageResult(
            storage_path=f"s3://{self.bucket}/{key}",
            storage_filename=key,
            storage_backend=self.backend_name,
            extra={"bucket": self.bucket},
        )


class GCSMediaStorage(BaseMediaStorage):
    backend_name = "gcs"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        timeout_seconds: float,
    ) -> None:
        try:
            from google.cloud import storage as gcs_storage
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MediaStorageError(
                "GCS storage selected but google-cloud-storage is not installed; install pricebot[storage] or set STORAGE backend to local"
            ) from exc
        self._gcs_storage = gcs_storage
        self.client = gcs_storage.Client()
        self.bucket = self.client.bucket(bucket)
        normalized_prefix = prefix.strip("/")
        if normalized_prefix:
            normalized_prefix = normalized_prefix + "/"
        self.prefix = normalized_prefix
        self.timeout = max(1.0, float(timeout_seconds))

    def persist(
        self,
        *,
        content: bytes,
        content_hash: str,
        mimetype: str | None,
        original_name: str | None,
    ) -> MediaStorageResult:
        extension = _infer_extension(original_name, mimetype)
        key = f"{self.prefix}{content_hash[:2]}/{content_hash}{extension}"
        blob = self.bucket.blob(key)
        try:
            blob.upload_from_string(
                content,
                content_type=mimetype or "application/octet-stream",
                timeout=self.timeout,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime environment
            raise MediaStorageError(f"failed to upload media to gs://{self.bucket.name}/{key}") from exc
        return MediaStorageResult(
            storage_path=f"gs://{self.bucket.name}/{key}",
            storage_filename=key,
            storage_backend=self.backend_name,
            extra={"bucket": self.bucket.name},
        )


_storage_lock = Lock()
_storage_instance: BaseMediaStorage | None = None


def _build_storage() -> BaseMediaStorage:
    backend = settings.whatsapp_media_storage_backend
    timeout = max(1.0, float(settings.whatsapp_media_storage_timeout_seconds))
    if backend == "local":
        root = settings.ingestion_storage_dir / "whatsapp_media"
        return LocalMediaStorage(root)
    if backend == "s3":
        bucket = settings.whatsapp_media_s3_bucket
        if not bucket:
            raise MediaStorageError("whatsapp_media_s3_bucket is required when using the s3 storage backend")
        return S3MediaStorage(
            bucket=bucket,
            prefix=settings.whatsapp_media_s3_prefix or "",
            timeout_seconds=timeout,
            region=settings.whatsapp_media_s3_region,
            endpoint_url=settings.whatsapp_media_s3_endpoint_url,
        )
    if backend == "gcs":
        bucket = settings.whatsapp_media_gcs_bucket
        if not bucket:
            raise MediaStorageError("whatsapp_media_gcs_bucket is required when using the gcs storage backend")
        return GCSMediaStorage(
            bucket=bucket,
            prefix=settings.whatsapp_media_gcs_prefix or "",
            timeout_seconds=timeout,
        )
    raise MediaStorageError(f"unsupported media storage backend: {backend}")


def get_media_storage() -> BaseMediaStorage:
    global _storage_instance
    storage = _storage_instance
    if storage is not None:
        return storage
    with _storage_lock:
        storage = _storage_instance
        if storage is not None:
            return storage
        storage = _build_storage()
        _storage_instance = storage
        return storage
