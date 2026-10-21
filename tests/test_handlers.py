"""
tests/test_handlers.py – Tests for Telegram command handlers.

Uses mocked Update and Context objects so no real Telegram bot token
or network access is required.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BOT_TOKEN", "1234567890:AAFakeTokenForTestingPurposesOnly")
os.environ.setdefault("OWNER_ID", "999999999")
os.environ.setdefault("UPLOAD_KEY", "test-upload-key-that-is-long-enough")
os.environ.setdefault("UPLOAD_DIR", "/tmp/tg_test_uploads")
os.environ.setdefault("TORRENT_DIR", "/tmp/tg_test_torrents")


def _make_update(user_id: int) -> MagicMock:
    """Return a minimal mocked telegram.Update."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context() -> MagicMock:
    return MagicMock()


# ---- /start ---------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_start_owner_receives_reply():
    """Owner sends /start → bot replies."""
    from bot.handlers import cmd_start

    update = _make_update(user_id=999999999)  # matches OWNER_ID
    ctx = _make_context()

    await cmd_start(update, ctx)

    update.message.reply_text.assert_called_once()
    # Verify the reply contains key info (not the token)
    call_args = update.message.reply_text.call_args[0][0]
    assert "Bot" in call_args
    assert "1234567890" not in call_args  # token must not appear


@pytest.mark.asyncio
async def test_start_stranger_silently_ignored():
    """Non-owner sends /start → bot does NOT reply."""
    from bot.handlers import cmd_start

    update = _make_update(user_id=111111111)  # different from OWNER_ID
    ctx = _make_context()

    await cmd_start(update, ctx)

    update.message.reply_text.assert_not_called()


# ---- /status --------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_status_empty_jobs():
    """Owner sends /status with no jobs → 'No jobs found' message."""
    from core.jobs import init_job_manager
    import core.jobs as _jobs_module
    mgr = init_job_manager(2)
    _jobs_module.job_manager = mgr

    from bot.handlers import cmd_status

    update = _make_update(user_id=999999999)
    ctx = _make_context()

    await cmd_status(update, ctx)

    update.message.reply_text.assert_called_once()
    assert "No jobs" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_status_stranger_silently_ignored():
    """Non-owner sends /status → no reply."""
    from bot.handlers import cmd_status

    update = _make_update(user_id=111111111)
    ctx = _make_context()

    await cmd_status(update, ctx)

    update.message.reply_text.assert_not_called()
