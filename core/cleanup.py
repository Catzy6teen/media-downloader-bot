"""
core/cleanup.py – Safe deletion of temporary files and directories.

All functions log their actions but never raise; callers should not
worry about cleanup errors cascading into job failures.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def safe_delete(path: Optional[Path]) -> None:
    """
    Delete *path* (file or directory) without raising on failure.

    Logs the outcome at DEBUG level on success, WARNING on error.
    """
    if path is None:
        return
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            logger.debug("Deleted directory", extra={"name": path.name})
        elif path.is_file():
            path.unlink(missing_ok=True)
            logger.debug("Deleted file", extra={"name": path.name})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to delete path",
            extra={"name": path.name, "error": type(exc).__name__},
        )


def safe_delete_upload(source_path: Path) -> None:
    """
    Delete the uploaded source file after the torrent has been delivered.

    This is called after successful Telegram delivery so that large source
    files do not accumulate on disk indefinitely.

    IMPORTANT: The .torrent file in TORRENT_DIR is kept permanently so the
    owner can re-send it without re-running mktorrent.
    """
    logger.info("Cleaning up upload", extra={"name": source_path.name})
    safe_delete(source_path)
