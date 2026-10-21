"""
web/upload.py – Chunked streaming upload handler.

Streams multipart/form-data body to disk in UPLOAD_CHUNK_SIZE increments
without ever buffering the entire file in RAM.  Enforces MAX_UPLOAD_BYTES
mid-stream so the server aborts before writing excess data.

Security controls implemented here:
  - Filename sanitisation (no path traversal, no null bytes, length limit)
  - Mid-stream size enforcement
  - UUID-prefixed destination to avoid collisions
  - Dest path confinement to UPLOAD_DIR (resolve check)
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException, Request, UploadFile, status

from core.config import settings
from core.jobs import JobRecord, get_job_manager

logger = logging.getLogger(__name__)

# Allowed characters in filenames (alphanumeric, dash, underscore, dot, space)
_SAFE_FILENAME_RE = re.compile(r"^[\w\-. ]+$")
_MAX_FILENAME_LEN = 200


def _sanitise_filename(raw: str) -> str:
    """
    Return a safe filename derived from *raw*.

    Raises HTTPException 400 for obviously malicious or unsupported names.
    """
    name = raw.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Filename must not be empty")
    if len(name) > _MAX_FILENAME_LEN:
        raise HTTPException(status_code=400, detail="Filename too long (max 200 chars)")
    # Strip directory components — first line of path traversal defence
    name = Path(name).name
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if "\x00" in name:
        raise HTTPException(status_code=400, detail="Null bytes in filename")
    if not _SAFE_FILENAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Filename contains disallowed characters (use letters, digits, -, _, ., space)",
        )
    return name


def _make_dest_path(filename: str) -> Path:
    """
    Return a collision-free destination path inside UPLOAD_DIR.

    The path is verified to remain within UPLOAD_DIR (confinement check).
    """
    prefix = uuid.uuid4().hex[:8]
    dest = (settings.UPLOAD_DIR / f"{prefix}_{filename}").resolve()
    # Confinement check: resolved path must be inside UPLOAD_DIR
    upload_dir_resolved = settings.UPLOAD_DIR.resolve()
    if not str(dest).startswith(str(upload_dir_resolved)):
        raise HTTPException(status_code=400, detail="Invalid destination path")
    return dest


async def stream_upload_to_disk(
    file: UploadFile,
    request: Request,
) -> Tuple[Path, str]:
    """
    Stream *file* to disk and return (dest_path, sanitised_filename).

    Raises:
        HTTPException 400 – bad filename
        HTTPException 413 – upload exceeds MAX_UPLOAD_BYTES
        HTTPException 500 – I/O error during write
    """
    # 1. Validate filename
    raw_name = file.filename or ""
    safe_name = _sanitise_filename(raw_name)

    # 2. Early Content-Length check (advisory; clients may omit it)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            cl = int(content_length)
            if cl > settings.MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_BYTES} bytes",
                )
        except ValueError:
            pass  # Ignore malformed Content-Length

    dest = _make_dest_path(safe_name)
    written = 0

    logger.info("Starting upload", extra={"filename": safe_name, "dest": dest.name})

    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(settings.UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                # Mid-stream size enforcement
                if written > settings.MAX_UPLOAD_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Upload exceeds maximum allowed size; transfer aborted",
                    )
                fh.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        logger.error("I/O error during upload", extra={"error": type(exc).__name__})
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server-side I/O error during upload",
        )

    logger.info(
        "Upload complete",
        extra={"filename": safe_name, "bytes": written},
    )
    return dest, safe_name


async def handle_upload(file: UploadFile, request: Request) -> JobRecord:
    """
    Orchestrate upload → job submission.  Returns the created JobRecord.
    """
    dest, safe_name = await stream_upload_to_disk(file, request)
    manager = get_job_manager()
    record = await manager.submit(filename=safe_name, source_path=dest)
    logger.info("Job created", extra={"job_id": record.job_id, "filename": safe_name})
    return record
