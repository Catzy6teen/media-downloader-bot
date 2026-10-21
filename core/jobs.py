"""
core/jobs.py – Async-safe job queue for torrent-creation tasks.

Provides:
  - JobState enum
  - JobRecord dataclass
  - JobManager singleton with concurrency cap
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class JobRecord:
    job_id: str
    filename: str
    source_path: Path
    torrent_path: Optional[Path] = None
    state: JobState = JobState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error": self.error,
        }


class JobManager:
    """
    Thread-safe (asyncio) job registry with a bounded worker semaphore.
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def submit(self, filename: str, source_path: Path) -> JobRecord:
        """Create a new job and enqueue it.  Returns the created record."""
        async with self._lock:
            job_id = str(uuid.uuid4())
            record = JobRecord(
                job_id=job_id,
                filename=filename,
                source_path=source_path,
            )
            self._jobs[job_id] = record
            logger.info("Job submitted", extra={"job_id": job_id, "filename": filename})
        await self._queue.put(job_id)
        return record

    async def get(self, job_id: str) -> Optional[JobRecord]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_all(self) -> List[JobRecord]:
        async with self._lock:
            return sorted(self._jobs.values(), key=lambda r: r.created_at, reverse=True)

    async def _update(self, job_id: str, **kwargs) -> None:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record:
                for k, v in kwargs.items():
                    setattr(record, k, v)
                record.updated_at = datetime.now(timezone.utc)

    async def mark_running(self, job_id: str) -> None:
        await self._update(job_id, state=JobState.RUNNING)
        logger.info("Job running", extra={"job_id": job_id})

    async def mark_done(self, job_id: str, torrent_path: Path) -> None:
        await self._update(job_id, state=JobState.DONE, torrent_path=torrent_path)
        logger.info("Job done", extra={"job_id": job_id})

    async def mark_failed(self, job_id: str, error: str) -> None:
        # Sanitise error to avoid leaking internal paths in Telegram messages
        safe_error = error[:300] if error else "unknown error"
        await self._update(job_id, state=JobState.FAILED, error=safe_error)
        logger.error("Job failed", extra={"job_id": job_id, "error": safe_error})

    async def next_job_id(self) -> str:
        """Block until a job is available in the queue and return its ID."""
        return await self._queue.get()

    def acquire_slot(self):
        """Return the semaphore context manager for concurrency control."""
        return self._semaphore


# Module-level singleton, initialised in main.py with settings.MAX_CONCURRENT_JOBS
job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    if job_manager is None:
        raise RuntimeError("JobManager not initialised — call init_job_manager() first")
    return job_manager


def init_job_manager(max_concurrent: int) -> JobManager:
    global job_manager
    job_manager = JobManager(max_concurrent=max_concurrent)
    return job_manager
