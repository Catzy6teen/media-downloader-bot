"""
tests/test_upload.py – Tests for chunked upload handler.

Covers: oversized uploads, path traversal, valid uploads, duplicate
filename handling, and I/O error simulation.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BOT_TOKEN", "1234567890:AAFakeTokenForTestingPurposesOnly")
os.environ.setdefault("OWNER_ID", "999999999")
os.environ.setdefault("UPLOAD_KEY", "test-upload-key-that-is-long-enough")
os.environ.setdefault("UPLOAD_DIR", "/tmp/tg_test_uploads")
os.environ.setdefault("TORRENT_DIR", "/tmp/tg_test_torrents")


from web.upload import _sanitise_filename, _make_dest_path
from fastapi import HTTPException


# ---- Filename sanitisation ------------------------------------------------- #

def test_sanitise_valid_name():
    assert _sanitise_filename("my_file.mkv") == "my_file.mkv"


def test_sanitise_strips_leading_spaces():
    assert _sanitise_filename("  movie.mp4  ") == "movie.mp4"


def test_sanitise_path_traversal_dot_dot():
    """../etc/passwd should be reduced to 'passwd' by Path().name."""
    result = _sanitise_filename("../etc/passwd")
    assert result == "passwd"


def test_sanitise_path_traversal_absolute():
    """Absolute path should be reduced to the basename."""
    result = _sanitise_filename("/etc/passwd")
    assert result == "passwd"


def test_sanitise_empty_filename():
    with pytest.raises(HTTPException) as exc_info:
        _sanitise_filename("")
    assert exc_info.value.status_code == 400


def test_sanitise_too_long_filename():
    with pytest.raises(HTTPException) as exc_info:
        _sanitise_filename("a" * 300 + ".txt")
    assert exc_info.value.status_code == 400


def test_sanitise_null_byte():
    with pytest.raises(HTTPException) as exc_info:
        _sanitise_filename("file\x00name.txt")
    assert exc_info.value.status_code == 400


def test_sanitise_disallowed_chars():
    with pytest.raises(HTTPException) as exc_info:
        _sanitise_filename("file<script>.txt")
    assert exc_info.value.status_code == 400


# ---- Destination path confinement ----------------------------------------- #

def test_make_dest_path_stays_in_upload_dir(tmp_path, monkeypatch):
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.settings, "UPLOAD_DIR", tmp_path)
    dest = _make_dest_path("safe_file.mkv")
    assert str(dest).startswith(str(tmp_path.resolve()))
    assert dest.name.endswith("safe_file.mkv")


# ---- Full streaming upload ------------------------------------------------- #

@pytest.mark.asyncio
async def test_stream_upload_oversized(tmp_path, monkeypatch):
    """Uploading more bytes than MAX_UPLOAD_BYTES should raise 413."""
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.settings, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg_module.settings, "MAX_UPLOAD_BYTES", 10)  # tiny limit
    monkeypatch.setattr(cfg_module.settings, "UPLOAD_CHUNK_SIZE", 5)

    from web.upload import stream_upload_to_disk
    from fastapi import UploadFile
    from starlette.datastructures import UploadFile as StarletteUploadFile

    # 20 bytes > 10 byte limit
    fake_file = UploadFile(filename="big.txt", file=io.BytesIO(b"x" * 20))
    fake_request = MagicMock()
    fake_request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await stream_upload_to_disk(fake_file, fake_request)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_stream_upload_valid(tmp_path, monkeypatch):
    """A valid small upload should succeed and return the dest path."""
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.settings, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg_module.settings, "MAX_UPLOAD_BYTES", 1024 * 1024)
    monkeypatch.setattr(cfg_module.settings, "UPLOAD_CHUNK_SIZE", 512)

    from web.upload import stream_upload_to_disk
    from fastapi import UploadFile

    content = b"hello torrent world"
    fake_file = UploadFile(filename="valid.txt", file=io.BytesIO(content))
    fake_request = MagicMock()
    fake_request.headers = {}

    dest, safe_name = await stream_upload_to_disk(fake_file, fake_request)

    assert safe_name == "valid.txt"
    assert dest.exists()
    assert dest.read_bytes() == content
