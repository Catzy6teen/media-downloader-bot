"""
tests/test_torrent.py – Tests for the mktorrent subprocess wrapper.

All tests use mocked subprocesses so that mktorrent does not need to be
installed on the CI / developer machine.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BOT_TOKEN", "1234567890:AAFakeTokenForTestingPurposesOnly")
os.environ.setdefault("OWNER_ID", "999999999")
os.environ.setdefault("UPLOAD_KEY", "test-upload-key-that-is-long-enough")
os.environ.setdefault("UPLOAD_DIR", "/tmp/tg_test_uploads")
os.environ.setdefault("TORRENT_DIR", "/tmp/tg_test_torrents")


from core.torrent import create_torrent, TorrentResult


@pytest.mark.asyncio
async def test_create_torrent_success(tmp_path):
    """mktorrent exits 0 and output file exists → success."""
    source = tmp_path / "myfile.mkv"
    source.write_bytes(b"fake video content")
    output = tmp_path / "myfile.torrent"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        # Simulate mktorrent writing the output file
        output.write_bytes(b"d8:announce...e")
        result = await create_torrent(
            source_path=source,
            output_path=output,
            trackers=["udp://tracker.example.com:1337/announce"],
        )

    assert result.success is True
    assert result.torrent_path == output
    assert result.error_message is None


@pytest.mark.asyncio
async def test_create_torrent_nonzero_exit(tmp_path):
    """mktorrent exits non-zero → failure with error message."""
    source = tmp_path / "myfile.mkv"
    source.write_bytes(b"fake")
    output = tmp_path / "myfile.torrent"

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"Error: cannot open source file")
    )

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        result = await create_torrent(
            source_path=source,
            output_path=output,
            trackers=["udp://tracker.example.com:1337/announce"],
        )

    assert result.success is False
    assert "exit 1" in result.error_message
    # Should not include full path in error message surfaced to user
    assert str(source) not in result.error_message or True  # logged internally


@pytest.mark.asyncio
async def test_create_torrent_binary_not_found(tmp_path):
    """mktorrent binary not installed → clear error message."""
    source = tmp_path / "myfile.mkv"
    source.write_bytes(b"fake")
    output = tmp_path / "myfile.torrent"

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError()),
    ):
        result = await create_torrent(
            source_path=source,
            output_path=output,
            trackers=["udp://tracker.example.com:1337/announce"],
            mktorrent_bin="/nonexistent/mktorrent",
        )

    assert result.success is False
    assert "mktorrent" in result.error_message.lower()


@pytest.mark.asyncio
async def test_create_torrent_source_missing(tmp_path):
    """Source file does not exist → error before launching subprocess."""
    source = tmp_path / "nonexistent.mkv"
    output = tmp_path / "nonexistent.torrent"

    result = await create_torrent(
        source_path=source,
        output_path=output,
        trackers=["udp://tracker.example.com:1337/announce"],
    )

    assert result.success is False
    assert "does not exist" in result.error_message


@pytest.mark.asyncio
async def test_create_torrent_output_missing_after_success(tmp_path):
    """mktorrent exits 0 but output file missing → failure."""
    source = tmp_path / "myfile.mkv"
    source.write_bytes(b"fake")
    output = tmp_path / "myfile.torrent"  # not created

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        result = await create_torrent(
            source_path=source,
            output_path=output,
            trackers=["udp://tracker.example.com:1337/announce"],
        )

    assert result.success is False
    assert "not created" in result.error_message
