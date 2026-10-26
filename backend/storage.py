"""Leaf-image storage: Cloudinary in production, local disk in development.

The rule the rest of the app relies on: **storing an image never fails the request
because a cloud provider is misconfigured or down.** If Cloudinary is configured it is
used; if it is missing or errors, the bytes go to ``backend/uploads/`` and the response
says which happened. That keeps a demo working when a credential is wrong, while never
pretending an image was uploaded to a service it never reached.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import settings

logger = logging.getLogger("cropguard.storage")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass
class StoredImage:
    url: str
    backend: str
    public_id: Optional[str] = None
    warning: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "backend": self.backend,
            "public_id": self.public_id,
            "warning": self.warning,
        }


class StorageError(RuntimeError):
    """Both Cloudinary and local storage failed."""


def resolve_extension(filename: Optional[str], content_type: Optional[str]) -> str:
    """Pick a safe extension from the filename, then the declared content type."""
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in ALLOWED_EXTENSIONS:
            return ".jpg" if suffix == ".jpeg" else suffix
    if content_type:
        mapped = ALLOWED_CONTENT_TYPES.get(content_type.split(";")[0].strip().lower())
        if mapped:
            return mapped
    return ".jpg"


def _store_local(raw: bytes, extension: str) -> StoredImage:
    uploads: Path = settings.uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{extension}"
    (uploads / name).write_bytes(raw)
    return StoredImage(url=f"/uploads/{name}", backend="local")


def _store_cloudinary(raw: bytes, extension: str, folder: str) -> StoredImage:
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    public_id = f"{folder}/{uuid.uuid4().hex}"
    result = cloudinary.uploader.upload(
        raw,
        public_id=public_id,
        resource_type="image",
        folder=None,
        overwrite=False,
        # Keep the stored image small: the model only ever sees 224x224, and a
        # 12 MP phone photo has no business being kept at full size.
        transformation=[{"width": 1024, "crop": "limit"}, {"quality": "auto"}],
    )
    return StoredImage(
        url=result.get("secure_url") or result.get("url", ""),
        backend="cloudinary",
        public_id=result.get("public_id"),
    )


def _store_postgres(raw: bytes, extension: str, content_type: Optional[str]) -> StoredImage:
    """Keep the bytes in the database and return an unguessable URL.

    No extra credentials are needed: a deployment that already has DATABASE_URL gets
    durable images. This is what makes images survive a redeploy on a host whose disk is
    ephemeral.
    """
    import hashlib  # noqa: PLC0415 - only needed on this path
    import secrets  # noqa: PLC0415

    from database import SessionLocal  # noqa: PLC0415 - avoids a circular import
    from models import ReportImage  # noqa: PLC0415

    token = secrets.token_urlsafe(24)
    resolved_type = (content_type or "image/jpeg").split(";")[0].strip().lower()
    if resolved_type not in ALLOWED_CONTENT_TYPES:
        resolved_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[extension]

    with SessionLocal() as session:
        session.add(
            ReportImage(
                token=token,
                content_type=resolved_type,
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                data=raw,
            )
        )
        session.commit()

    return StoredImage(url=f"/api/images/{token}", backend="postgres", public_id=token)


def database_storage_available() -> bool:
    """True when the configured database can hold image bytes."""
    from database import engine  # noqa: PLC0415

    return engine.dialect.name != "sqlite" or not settings.force_local_storage


def store_image(
    raw: bytes,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    folder: str = "cropguard",
) -> StoredImage:
    """Store leaf-image bytes and return a URL the frontend can render."""
    if not raw:
        raise StorageError("No image bytes to store.")

    extension = resolve_extension(filename, content_type)

    if settings.cloudinary_enabled:
        try:
            return _store_cloudinary(raw, extension, folder)
        except Exception as error:  # network, auth, quota — never fatal
            logger.warning("Cloudinary upload failed, falling back to local disk: %s", error)
            stored = _store_local(raw, extension)
            stored.warning = (
                "Cloudinary upload failed; the image was saved to local storage instead. "
                "On an ephemeral host this file will not survive a redeploy."
            )
            return stored

    # Prefer the database over local disk: on an ephemeral host, files written to disk
    # disappear on the next deploy, while rows do not.
    if database_storage_available():
        try:
            return _store_postgres(raw, extension, content_type)
        except Exception as error:  # noqa: BLE001 - never fail the request over storage
            logger.warning("database image storage failed, falling back to disk: %s", error)
            stored = _store_local(raw, extension)
            stored.warning = (
                "The image could not be stored in the database and was written to local "
                "disk instead. On an ephemeral host this file will not survive a redeploy."
            )
            return stored

    stored = _store_local(raw, extension)
    stored.warning = (
        "Images are stored on local disk because no durable store is configured. "
        "On an ephemeral host they will not survive a redeploy."
    )
    return stored


def storage_status() -> dict:
    """Non-secret summary for /health."""
    uploads = settings.uploads_dir
    writable = _is_writable(uploads)
    if settings.cloudinary_enabled:
        backend = "cloudinary"
    elif database_storage_available():
        backend = "postgres"
    else:
        backend = "local"
    return {
        "backend": backend,
        # `writable` is what /health publishes; the path itself is not exposed.
        "writable": writable,
        "uploads_dir_writable": writable,
    }


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
