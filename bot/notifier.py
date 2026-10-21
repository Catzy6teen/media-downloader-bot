"""
bot/notifier.py – Send torrent results back to the owner via Telegram.

Never includes raw exception messages, internal file paths, or secrets in
messages sent to Telegram. Error summaries are sanitised before delivery.
"""
from __future__ import annotations

import logging
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from core.config import settings

logger = logging.getLogger(__name__)

_MAX_CAPTION = 1024  # Telegram caption limit


async def notify_done(
    bot: Bot,
    job_id: str,
    filename: str,
    torrent_path: Path,
) -> None:
    """
    Send the .torrent file to the owner's chat.

    Falls back to a text notification if the file send fails.
    """
    caption = (
        f"✅ Torrent created!\n"
        f"File: `{filename[:200]}`\n"
        f"Job: `{job_id}`\n\n"
        f"⚠️ Remember: this torrent is *not* seeding yet.\n"
        f"Start your seeding client to make it downloadable."
    )

    try:
        with torrent_path.open("rb") as fh:
            await bot.send_document(
                chat_id=settings.OWNER_ID,
                document=fh,
                filename=torrent_path.name,
                caption=caption[:_MAX_CAPTION],
                parse_mode="Markdown",
            )
        logger.info("Torrent sent to owner", extra={"job_id": job_id})
    except TelegramError as exc:
        logger.error(
            "Failed to send torrent document",
            extra={"job_id": job_id, "error": type(exc).__name__},
        )
        # Fallback text notification
        try:
            await bot.send_message(
                chat_id=settings.OWNER_ID,
                text=(
                    f"✅ Torrent ready for `{filename[:100]}`\n"
                    f"(Could not send the file automatically. "
                    f"Find it at: `{torrent_path.name}` in TORRENT\_DIR)"
                ),
                parse_mode="Markdown",
            )
        except TelegramError:
            logger.critical("Could not notify owner at all", extra={"job_id": job_id})


async def notify_failed(
    bot: Bot,
    job_id: str,
    filename: str,
    error: str,
) -> None:
    """
    Send a sanitised failure notification to the owner.

    The raw error string is truncated; internal paths are not included.
    """
    safe_error = error[:200] if error else "Unknown error"
    try:
        await bot.send_message(
            chat_id=settings.OWNER_ID,
            text=(
                f"❌ Torrent creation *failed*\n"
                f"File: `{filename[:100]}`\n"
                f"Job: `{job_id}`\n"
                f"Reason: {safe_error}"
            ),
            parse_mode="Markdown",
        )
        logger.info("Failure notification sent", extra={"job_id": job_id})
    except TelegramError as exc:
        logger.error(
            "Failed to send failure notification",
            extra={"job_id": job_id, "error": type(exc).__name__},
        )
