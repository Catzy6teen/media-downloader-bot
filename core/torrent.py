"""
core/torrent.py – Wrapper around the mktorrent CLI.

Runs mktorrent as a subprocess (never shell=True) and returns a structured
result.  Stderr/stdout are captured without leaking secrets or internal paths.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TorrentResult:
    success: bool
    torrent_path: Optional[Path] = None
    error_message: Optional[str] = None


async def create_torrent(
    source_path: Path,
    output_path: Path,
    trackers: List[str],
    piece_size_kib: int = 0,
    mktorrent_bin: str = "mktorrent",
) -> TorrentResult:
    """
    Run `mktorrent` to generate a .torrent file from *source_path*.

    Args:
        source_path:     File or directory to include in the torrent.
        output_path:     Destination path for the generated .torrent file.
        trackers:        List of announce URL strings.
        piece_size_kib:  Piece size in KiB (0 = auto).
        mktorrent_bin:   Path to the mktorrent executable.

    Returns:
        TorrentResult with success flag and path or error message.

    NOTE: This function only *creates* the .torrent metadata file.
          It does NOT start seeding.  To make the torrent publicly
          downloadable you must run a seeding client (e.g. qBittorrent)
          pointing at the source file with a reachable tracker or DHT.
    """
    if not source_path.exists():
        return TorrentResult(
            success=False,
            error_message=f"Source path does not exist: {source_path.name}",
        )

    # Build argument list — never use shell=True
    cmd: List[str] = [mktorrent_bin]

    for url in trackers:
        cmd.extend(["-a", url])

    if piece_size_kib > 0:
        cmd.extend(["-l", str(piece_size_kib)])

    cmd.extend(["-o", str(output_path)])
    cmd.append(str(source_path))

    logger.debug(
        "Running mktorrent",
        extra={"args_count": len(cmd), "output": output_path.name},
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
    except FileNotFoundError:
        return TorrentResult(
            success=False,
            error_message="mktorrent binary not found. Install it with: sudo apt install mktorrent",
        )
    except OSError as exc:
        return TorrentResult(
            success=False,
            error_message=f"Failed to launch mktorrent: {type(exc).__name__}",
        )

    # Decode output — strip internal paths from logs
    stderr_text = stderr_bytes.decode(errors="replace").strip()
    stdout_text = stdout_bytes.decode(errors="replace").strip()

    if proc.returncode != 0:
        # Only surface the last two lines of stderr to avoid leaking paths
        safe_stderr = "\n".join(stderr_text.splitlines()[-2:])
        logger.error(
            "mktorrent exited with non-zero code",
            extra={"returncode": proc.returncode, "stderr_tail": safe_stderr},
        )
        return TorrentResult(
            success=False,
            error_message=f"mktorrent failed (exit {proc.returncode}): {safe_stderr[:200]}",
        )

    if not output_path.exists():
        return TorrentResult(
            success=False,
            error_message="mktorrent exited successfully but .torrent file was not created",
        )

    logger.info(
        "Torrent created successfully",
        extra={"torrent_file": output_path.name, "size_bytes": output_path.stat().st_size},
    )
    return TorrentResult(success=True, torrent_path=output_path)
