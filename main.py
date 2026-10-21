"""
main.py – Entry point for the Telegram Torrent Creator Bot.

Runs two concurrent tasks on a single asyncio event loop:
  1. Uvicorn (FastAPI upload server)
  2. python-telegram-bot polling

A background worker coroutine drains the job queue, runs mktorrent,
delivers the .torrent to the owner, and cleans up upload files.

Signals: SIGINT / SIGTERM trigger graceful shutdown (stops polling,
waits for running jobs, stops uvicorn).
"""
from __future__ import annotations

import asyncio
import logging
import logging.config
import signal
import sys
from pathlib import Path

import uvicorn
from telegram.ext import Application, ApplicationBuilder

from bot.handlers import register_handlers
from bot.notifier import notify_done, notify_failed
from core.cleanup import safe_delete_upload
from core.config import settings
from core.jobs import JobState, init_job_manager
from core.torrent import create_torrent
from web.app import create_app

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

# --------------------------------------------------------------------------- #
#  Logging                                                                     #
# --------------------------------------------------------------------------- #
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "%(asctime)s %(levelname)-8s %(name)s %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%SZ",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": settings.LOG_LEVEL.upper(),
    },
    # Suppress noisy third-party loggers in production
    "loggers": {
        "httpx": {"level": "WARNING"},
        "telegram": {"level": "INFO"},
        "uvicorn.access": {"level": "WARNING"},
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

# Module-level secrets filter: prevent BOT_TOKEN from appearing in logs
class _SecretFilter(logging.Filter):
    """Strip known secrets from log records."""
    _secrets = [settings.BOT_TOKEN, settings.UPLOAD_KEY]

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        for secret in self._secrets:
            if secret in str(record.getMessage()):
                record.msg = record.msg.replace(secret, "[REDACTED]")
        return True


for _handler in logging.root.handlers:
    _handler.addFilter(_SecretFilter())


# --------------------------------------------------------------------------- #
#  Background worker                                                           #
# --------------------------------------------------------------------------- #
async def worker(app_instance: Application) -> None:
    """
    Drain the job queue, create torrents, notify the owner, clean up uploads.
    """
    manager = init_job_manager(settings.MAX_CONCURRENT_JOBS)
    bot = app_instance.bot
    logger.info("Worker started", extra={"max_concurrent": settings.MAX_CONCURRENT_JOBS})

    while True:
        job_id = await manager.next_job_id()
        # Acquire concurrency slot before launching
        asyncio.ensure_future(_process_job(manager, bot, job_id))


async def _process_job(manager, bot, job_id: str) -> None:
    """Process a single job inside the concurrency semaphore."""
    async with manager.acquire_slot():
        record = await manager.get(job_id)
        if record is None:
            logger.error("Job not found", extra={"job_id": job_id})
            return

        await manager.mark_running(job_id)
        logger.info(
            "Processing job",
            extra={"job_id": job_id, "filename": record.filename},
        )

        # Build output torrent path
        torrent_filename = Path(record.filename).stem + ".torrent"
        output_path = settings.TORRENT_DIR / torrent_filename
        # Avoid overwriting existing torrent with same name
        counter = 0
        while output_path.exists():
            counter += 1
            output_path = settings.TORRENT_DIR / f"{Path(record.filename).stem}_{counter}.torrent"

        result = await create_torrent(
            source_path=record.source_path,
            output_path=output_path,
            trackers=settings.TRACKER_URLS,
            piece_size_kib=settings.TORRENT_PIECE_SIZE_KIB,
            mktorrent_bin=settings.MKTORRENT_BIN,
        )

        if result.success and result.torrent_path:
            await manager.mark_done(job_id, result.torrent_path)
            await notify_done(
                bot=bot,
                job_id=job_id,
                filename=record.filename,
                torrent_path=result.torrent_path,
            )
            # Clean up source upload after successful delivery
            safe_delete_upload(record.source_path)
        else:
            error_msg = result.error_message or "Unknown torrent creation error"
            await manager.mark_failed(job_id, error_msg)
            await notify_failed(
                bot=bot,
                job_id=job_id,
                filename=record.filename,
                error=error_msg,
            )
            # Still clean up the source to avoid disk accumulation
            safe_delete_upload(record.source_path)


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
async def main() -> None:
    # Build Telegram Application with generous timeouts for uploading large videos
    tg_app = (
        ApplicationBuilder()
        .token(settings.BOT_TOKEN)
        .connect_timeout(60.0)
        .read_timeout(300.0)
        .write_timeout(300.0)
        .build()
    )
    register_handlers(tg_app)

    # Build Uvicorn server
    fastapi_app = create_app()
    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
        timeout_keep_alive=settings.REQUEST_TIMEOUT_SECS,
        access_log=settings.ENV == "development",
    )
    server = uvicorn.Server(uvicorn_config)

    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("Shutdown signal received", extra={"signal": sig})
        shutdown_event.set()

    # Register signal handlers (works on Linux; on Windows SIGTERM is unavailable)
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s, None))
    except NotImplementedError:
        # Windows: fall back to default SIGINT (Ctrl-C) handling
        signal.signal(signal.SIGINT, _signal_handler)

    logger.info(
        "Starting bot and upload server",
        extra={"host": settings.HOST, "port": settings.PORT},
    )

    # Initialise the job manager before the worker reads from it
    manager = init_job_manager(settings.MAX_CONCURRENT_JOBS)
    # Monkey-patch the manager into core.jobs module so imports work
    import core.jobs as _jobs_module
    _jobs_module.job_manager = manager

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram polling started")

        # Run Uvicorn and the background worker concurrently
        try:
            await asyncio.gather(
                server.serve(),
                worker(tg_app),
                shutdown_event.wait(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping Telegram polling")
            await tg_app.updater.stop()
            await tg_app.stop()
            server.should_exit = True
            logger.info("Graceful shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
