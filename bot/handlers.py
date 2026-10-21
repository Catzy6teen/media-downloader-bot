"""
bot/handlers.py – Telegram handlers with ultra-clean modern UI and fast processing.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import re
import uuid
from typing import Callable, Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.downloader import download_selected_format, get_video_info
from core.cleanup import safe_delete
from core.config import settings

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
)

_PENDING_URLS: Dict[str, str] = {}


def owner_only(handler: Callable) -> Callable:
    """Decorator: silently ignore updates from non-owner users."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or user.id != settings.OWNER_ID:
            if user:
                logger.warning("Unauthorised access attempt: %s", user.id)
            return
        return await handler(update, context)

    return wrapper


@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with clean welcome UI."""
    welcome_text = (
        "⚡️ *ALL-IN-ONE MEDIA DOWNLOADER*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 *Fast • High Quality • No Limits*\n\n"
        "Send any video or music link from:\n"
        "• YouTube • Instagram • Facebook\n"
        "• Twitter / X • TikTok • Reddit\n\n"
        "💎 *Supported Options:*\n"
        "🎬 4K • 2K • 1080p • 720p Video\n"
        "🎵 320kbps MP3 • Lossless FLAC\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👉 *Just paste a link below to start!*"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    await update.message.reply_text("🟢 *System Online & Operating at Turbo Speed*", parse_mode="Markdown")


@owner_only
async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analyze link with ultra-fast UX."""
    text = update.message.text or update.message.caption or ""
    urls = URL_REGEX.findall(text)
    if not urls:
        return

    url = urls[0]
    status_msg = await update.message.reply_text("⚡️ *Fetching media details...*", parse_mode="Markdown")

    info = await get_video_info(url)
    if not info:
        await status_msg.edit_text("❌ *Could not fetch media. Please check if the link is valid and public.*", parse_mode="Markdown")
        return

    title = info["title"]
    heights = info["heights"]
    uploader = info.get("uploader", "Creator")
    duration = info.get("duration", 0)
    mins, secs = divmod(duration, 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    token = uuid.uuid4().hex[:10]
    _PENDING_URLS[token] = url

    keyboard = []
    video_row = []

    label_map = {
        2160: "4K (2160p)",
        1440: "2K (1440p)",
        1080: "1080p FHD",
        720: "720p HD",
        480: "480p SD",
        360: "360p",
    }

    for h in heights:
        if h in label_map or h >= 360:
            name = label_map.get(h, f"{h}p")
            video_row.append(InlineKeyboardButton(f"🎬 {name}", callback_data=f"dl:{token}:{h}"))
            if len(video_row) == 2:
                keyboard.append(video_row)
                video_row = []

    if video_row:
        keyboard.append(video_row)

    # Clean Audio Section
    keyboard.append([
        InlineKeyboardButton("🎵 MP3 320kbps", callback_data=f"dl:{token}:mp3"),
        InlineKeyboardButton("🎧 FLAC Lossless", callback_data=f"dl:{token}:lossless"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    ui_card = (
        f"🎬 *{title[:85]}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Author:* `{uploader[:30]}`\n"
        f"⏱ *Duration:* `{time_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 *Select Download Quality:*"
    )

    menu_msg = await status_msg.edit_text(
        ui_card,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )

    # 5 minute timeout for buttons
    async def _auto_expire():
        await asyncio.sleep(300)
        try:
            await menu_msg.edit_reply_markup(reply_markup=None)
            _PENDING_URLS.pop(token, None)
        except Exception:
            pass

    asyncio.create_task(_auto_expire())


@owner_only
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle click with instant feedback and streaming delivery."""
    query = update.callback_query
    await query.answer(text="🚀 Starting turbo download...", show_alert=False)

    data = query.data or ""
    if not data.startswith("dl:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        return

    _, token, choice = parts
    url = _PENDING_URLS.get(token)
    if not url:
        await query.message.reply_text("⚠️ *Session expired. Please send the link again.*", parse_mode="Markdown")
        return

    tag = choice.upper() if choice in ("mp3", "lossless") else f"{choice}p"
    progress = await query.message.reply_text(f"⏳ *Downloading {tag} in high speed...*", parse_mode="Markdown")

    file_path, title_or_err, is_audio = await download_selected_format(url, choice)

    if not file_path or not file_path.exists():
        err = title_or_err or "Unknown error"
        await progress.edit_text(f"❌ *Download Failed:*\n`{err[:150]}`", parse_mode="Markdown")
        return

    try:
        await progress.edit_text("📤 *Sending to your chat...*", parse_mode="Markdown")
        with file_path.open("rb") as fh:
            if is_audio:
                await update.effective_chat.send_audio(
                    audio=fh,
                    title=title_or_err,
                    caption=f"🎵 *{title_or_err}*\n💎 *Quality:* `{tag}`",
                    parse_mode="Markdown",
                    read_timeout=300.0,
                    write_timeout=300.0,
                )
            else:
                await update.effective_chat.send_video(
                    video=fh,
                    caption=f"🎥 *{title_or_err}*\n💎 *Resolution:* `{tag}`",
                    parse_mode="Markdown",
                    supports_streaming=True,
                    read_timeout=300.0,
                    write_timeout=300.0,
                )
        await progress.delete()
    except Exception as exc:
        logger.error("Send error: %s", exc)
        try:
            with file_path.open("rb") as fh:
                await update.effective_chat.send_document(
                    document=fh,
                    filename=file_path.name,
                    caption=f"📁 *{title_or_err}*",
                    parse_mode="Markdown",
                    read_timeout=300.0,
                    write_timeout=300.0,
                )
            await progress.delete()
        except Exception:
            size_mb = file_path.stat().st_size / (1024 * 1024)
            direct_link = f"http://localhost:{settings.PORT}/media/download/{file_path.name}"
            await progress.edit_text(
                f"⚠️ *File ({size_mb:.1f} MB) is larger than Telegram's 50MB Bot limit!*\n\n"
                f"📥 *Direct High-Speed Download:*\n{direct_link}",
                parse_mode="Markdown",
            )
    finally:
        safe_delete(file_path)


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
