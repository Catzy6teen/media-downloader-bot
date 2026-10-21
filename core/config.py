"""
core/config.py – Application configuration loaded from environment variables.

All secrets are read from .env (never hardcoded). The application crashes fast
with a clear error if required variables are missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    BOT_TOKEN: str
    OWNER_ID: int

    # ── Web upload ───────────────────────────────────────────────────────────
    UPLOAD_KEY: str          # secret passed in X-Upload-Key header
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── Storage ───────────────────────────────────────────────────────────────
    UPLOAD_DIR: Path = Path("data/uploads")
    TORRENT_DIR: Path = Path("data/torrents")

    # ── Torrent creation ─────────────────────────────────────────────────────
    TRACKER_URLS: List[str] = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.tracker.cl:1337/announce",
    ]
    # Piece size in KiB (0 = mktorrent auto-select; suitable for large files)
    TORRENT_PIECE_SIZE_KIB: int = 0
    MKTORRENT_BIN: str = "mktorrent"

    # ── Limits ────────────────────────────────────────────────────────────────
    # Maximum upload size in bytes (default: 60 GiB)
    MAX_UPLOAD_BYTES: int = 60 * 1024 * 1024 * 1024
    # Chunk size for streaming reads (1 MiB)
    UPLOAD_CHUNK_SIZE: int = 1 * 1024 * 1024
    # Maximum concurrent torrent-creation jobs
    MAX_CONCURRENT_JOBS: int = 2
    # Rate limit for upload endpoint (requests per minute per IP)
    RATE_LIMIT_RPM: int = 10
    # Request read timeout seconds (for uvicorn; informational here)
    REQUEST_TIMEOUT_SECS: int = 3600

    # ── Misc ──────────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    ENV: str = "production"  # set to "development" to enable debug output

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("BOT_TOKEN", mode="before")
    @classmethod
    def _bot_token_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("BOT_TOKEN must not be empty")
        return v.strip()

    @field_validator("UPLOAD_KEY", mode="before")
    @classmethod
    def _upload_key_not_empty(cls, v: str) -> str:
        if not v or len(v.strip()) < 16:
            raise ValueError("UPLOAD_KEY must be at least 16 characters")
        return v.strip()

    @field_validator("OWNER_ID", mode="before")
    @classmethod
    def _owner_id_positive(cls, v) -> int:
        v = int(v)
        if v <= 0:
            raise ValueError("OWNER_ID must be a positive integer")
        return v

    @model_validator(mode="after")
    def _create_dirs(self) -> "Settings":
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.TORRENT_DIR.mkdir(parents=True, exist_ok=True)
        return self


# Module-level singleton — imported everywhere as `from core.config import settings`
settings = Settings()
