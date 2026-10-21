"""
bot/downloader.py – Ultra-fast, lightweight downloader for social media media.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yt_dlp

logger = logging.getLogger(__name__)

DOWNLOAD_TMP_DIR = Path("data/downloads")
DOWNLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)


def _sync_get_formats(url: str) -> Optional[Dict[str, Any]]:
    """Ultra-fast metadata extraction without full video stream scraping."""
    ydl_opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 5,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            title = info.get("title", "Social Video")
            duration = info.get("duration", 0)
            uploader = info.get("uploader") or info.get("channel") or "Creator"
            formats = info.get("formats", [])

            # Extract distinct available heights
            heights = set()
            for f in formats:
                h = f.get("height")
                vcodec = f.get("vcodec")
                if h and vcodec and vcodec != "none":
                    heights.add(int(h))

            # Default fallback standard resolutions if flat extract returned limited info
            if not heights:
                heights = {1080, 720, 480}

            return {
                "title": title,
                "duration": duration,
                "uploader": uploader,
                "heights": sorted(list(heights), reverse=True),
            }
    except Exception as exc:
        logger.error("Fast extract failed: %s", exc)
        return None


async def get_video_info(url: str) -> Optional[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_get_formats, url)


def _sync_download_custom(url: str, format_choice: str, out_template: str) -> Tuple[Optional[Path], Optional[str], bool]:
    is_audio = False

    common_opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # Multi-thread connections for maximum download speed
        "concurrent_fragment_downloads": 8,
        "buffersize": 1024 * 1024,
        "socket_timeout": 10,
    }

    if format_choice == "mp3":
        is_audio = True
        ydl_opts = {
            **common_opts,
            "outtmpl": out_template,
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
        }
    elif format_choice == "lossless":
        is_audio = True
        ydl_opts = {
            **common_opts,
            "outtmpl": out_template,
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "flac",
            }],
        }
    else:
        height = format_choice
        ydl_opts = {
            **common_opts,
            "outtmpl": out_template,
            # Best fast container matching
            "format": f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
            "merge_output_format": "mp4",
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, "Failed to download", is_audio

            title = info.get("title", "Media")
            filename = ydl.prepare_filename(info)
            path = Path(filename)

            if is_audio:
                ext = ".mp3" if format_choice == "mp3" else ".flac"
                candidate = path.with_suffix(ext)
                if candidate.exists():
                    return candidate, title, is_audio

            if not path.exists():
                for ext in (".mp4", ".mkv", ".webm", ".mp3", ".flac"):
                    candidate = path.with_suffix(ext)
                    if candidate.exists():
                        return candidate, title, is_audio

            return path, title, is_audio
    except Exception as exc:
        logger.error("Download error: %s", exc)
        return None, str(exc), is_audio


async def download_selected_format(url: str, format_choice: str) -> Tuple[Optional[Path], Optional[str], bool]:
    job_id = uuid.uuid4().hex[:8]
    out_template = str(DOWNLOAD_TMP_DIR / f"{job_id}_%(title).50s.%(ext)s")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_download_custom, url, format_choice, out_template)
