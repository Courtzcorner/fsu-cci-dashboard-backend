"""
File storage abstraction for profile photo uploads.

`StorageService.save()` validates and persists an uploaded file, returning
a public URL to store on the profile record - callers never see or store
a raw filesystem path.

Only a "local" provider is implemented (uploads/ directory, served via a
StaticFiles mount). Swap in an object-storage provider (S3, GCS, etc.)
later by adding a branch in `get_storage_service()` that implements the
same `save()` interface - selection is controlled by `STORAGE_PROVIDER`
so no route code needs to change.
"""
import uuid

from fastapi import HTTPException, UploadFile, status

from app.config import get_settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class LocalStorageService:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_dir = settings.uploads_dir_full_path
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.max_bytes = settings.max_upload_size_mb * 1024 * 1024

    def save_profile_photo(self, owner_id: str, file: UploadFile, contents: bytes) -> str:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported image type '{file.content_type}'. Allowed: jpeg, png, webp.",
            )
        if len(contents) > self.max_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image exceeds the {self.max_bytes // (1024 * 1024)}MB size limit.",
            )
        if len(contents) == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

        extension = ALLOWED_CONTENT_TYPES[file.content_type]
        # Filename is entirely generated server-side (owner id + random
        # uuid) - the original filename is never used for the path, so it
        # can't be used for path traversal or collisions.
        safe_filename = f"{owner_id}-{uuid.uuid4().hex}{extension}"

        owner_dir = self.base_dir / "profile-photos"
        owner_dir.mkdir(parents=True, exist_ok=True)
        destination = owner_dir / safe_filename
        destination.write_bytes(contents)

        return f"{self.public_base_url}/uploads/profile-photos/{safe_filename}"


def get_storage_service():
    settings = get_settings()
    if settings.storage_provider == "local":
        return LocalStorageService()
    raise RuntimeError(
        f"Unsupported STORAGE_PROVIDER '{settings.storage_provider}'. "
        "Only 'local' is implemented; add a new provider in app/services/storage_service.py."
    )
