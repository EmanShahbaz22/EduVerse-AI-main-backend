"""
file_storage_service.py — Local Disk Storage for Teacher Reference Files

Saves uploaded reference files to local disk so they are never lost when
ChromaDB is wiped or needs reprocessing. Files are organised by tenant and
course for easy inspection.

Directory layout:
    ./uploaded_references/
        {tenant_id}/
            {course_id}/
                {uuid}.{ext}   ← Newton's Laws book, slides, etc.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

# Base directory for all uploaded reference files.
# Sits inside the backend project root — never committed (add to .gitignore).
BASE_UPLOAD_DIR = Path("./uploaded_references")


def save_file_to_disk(
    file_bytes: bytes,
    original_filename: str,
    tenant_id: str,
    course_id: str,
) -> dict:
    """
    Save an uploaded reference file to local disk, organised by tenant/course.

    Args:
        file_bytes:        Raw bytes of the uploaded file.
        original_filename: Original filename from the browser (e.g. "newton.pdf").
        tenant_id:         Tenant identifier (school / organisation).
        course_id:         Course identifier.

    Returns:
        {
            "file_path":         str  — absolute path written to disk,
            "file_key":          str  — relative key: tenant/course/uuid.ext,
            "original_filename": str,
            "file_size_mb":      float,
        }
    """
    # Create directory structure: ./uploaded_references/{tenant_id}/{course_id}/
    upload_dir = BASE_UPLOAD_DIR / tenant_id / course_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename to prevent collisions between teachers who upload
    # files with identical names.
    extension = original_filename.split(".")[-1].lower()
    unique_filename = f"{uuid4()}.{extension}"
    file_path = upload_dir / unique_filename

    with open(file_path, "wb") as fh:
        fh.write(file_bytes)

    file_size_mb = round(len(file_bytes) / (1024 * 1024), 2)
    file_key = f"{tenant_id}/{course_id}/{unique_filename}"

    logger.info(
        "Saved reference file to disk: %s (%.2f MB) → %s",
        original_filename,
        file_size_mb,
        file_path,
    )

    return {
        "file_path": str(file_path),
        "file_key": file_key,
        "original_filename": original_filename,
        "file_size_mb": file_size_mb,
    }


def delete_file_from_disk(file_path: str) -> bool:
    """
    Delete a reference file from disk when the teacher removes an upload.

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        True if deleted or file was already absent; False on unexpected error.
    """
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            logger.info("Deleted reference file from disk: %s", file_path)
        else:
            logger.info("Delete requested but file not found on disk: %s", file_path)
        return True
    except Exception as exc:
        logger.error("Failed to delete file from disk: %s — %s", file_path, exc)
        return False


def get_file_bytes_from_disk(file_path: str) -> bytes:
    """
    Read a reference file back from disk for reprocessing (e.g. after ChromaDB wipe).

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        Raw file bytes.

    Raises:
        FileNotFoundError: If the file does not exist on disk.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Reference file not found on disk: {file_path}")
    with open(path, "rb") as fh:
        return fh.read()
