"""
web/app.py – FastAPI application: routes, middleware, lifespan.

Routes:
  POST /upload  – authenticated file upload (streams to disk)
  GET  /health  – health check (no auth required)
  GET  /status/{job_id} – query job state (authenticated)

Security layers applied:
  - Auth dependency (verify_api_key) on upload and status routes
  - In-memory rate limiting dependency on /upload
  - Global 413 / 500 exception handlers that never leak internals
  - Content-Length pre-check in upload handler
"""
from __future__ import annotations

import collections
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.jobs import get_job_manager
from web.auth import verify_api_key
from web.upload import handle_upload

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Simple in-memory sliding-window rate limiter                                #
# --------------------------------------------------------------------------- #
_request_records: Dict[str, collections.deque] = collections.defaultdict(collections.deque)

async def check_rate_limit(request: Request) -> None:
    """Enforce per-IP rate limits without decorator conflicts on UploadFile."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = _request_records[client_ip]

    # Purge timestamps older than 60 seconds
    while timestamps and now - timestamps[0] > 60:
        timestamps.popleft()

    if len(timestamps) >= settings.RATE_LIMIT_RPM:
        logger.warning("Rate limit exceeded", extra={"client_ip": client_ip})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )

    timestamps.append(now)


# --------------------------------------------------------------------------- #
#  Lifespan                                                                    #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI server starting up")
    yield
    logger.info("FastAPI server shutting down")


# --------------------------------------------------------------------------- #
#  Application factory                                                         #
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    app = FastAPI(
        title="Torrent Creator Bot – Upload API",
        version="1.0.0",
        docs_url="/docs" if settings.ENV == "development" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ #
    #  Global exception handlers                                           #
    # ------------------------------------------------------------------ #
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception in request",
            extra={"error": type(exc).__name__, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )

    # ------------------------------------------------------------------ #
    #  Routes                                                              #
    # ------------------------------------------------------------------ #
    @app.get("/", response_class=HTMLResponse, tags=["web"])
    async def index() -> str:
        """Simple, beautiful upload interface for browser uploads."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Torrent Creator - Upload File</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); width: 100%; max-width: 480px; }
        h1 { margin-top: 0; font-size: 1.5rem; color: #38bdf8; text-align: center; }
        p { color: #94a3b8; font-size: 0.9rem; text-align: center; margin-bottom: 2rem; }
        .form-group { margin-bottom: 1.5rem; }
        label { display: block; margin-bottom: 0.5rem; font-size: 0.85rem; color: #cbd5e1; }
        input[type="text"], input[type="file"] { width: 100%; box-sizing: border-box; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid #334155; background: #0f172a; color: #fff; font-size: 0.95rem; }
        button { width: 100%; padding: 0.85rem; border-radius: 0.5rem; border: none; background: #0284c7; color: white; font-weight: bold; font-size: 1rem; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #0369a1; }
        button:disabled { background: #475569; cursor: not-allowed; }
        .progress-bar-container { display: none; width: 100%; background: #334155; border-radius: 0.5rem; height: 1.2rem; overflow: hidden; margin-top: 1.5rem; }
        .progress-bar { width: 0%; height: 100%; background: #38bdf8; transition: width 0.2s; text-align: center; font-size: 0.75rem; line-height: 1.2rem; color: #000; font-weight: bold; }
        .status { margin-top: 1rem; font-size: 0.9rem; text-align: center; word-break: break-word; }
        .status.success { color: #4ade80; }
        .status.error { color: #f87171; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🧲 Torrent Creator</h1>
        <p>Upload a file to generate a .torrent file directly to Telegram.</p>
        
        <form id="uploadForm">
            <div class="form-group">
                <label for="uploadKey">Upload Key:</label>
                <input type="text" id="uploadKey" required placeholder="Enter X-Upload-Key" />
            </div>
            
            <div class="form-group">
                <label for="fileInput">Select File (10–50 GB supported):</label>
                <input type="file" id="fileInput" required />
            </div>
            
            <button type="submit" id="submitBtn">Upload & Generate Torrent</button>
        </form>

        <div class="progress-bar-container" id="progressContainer">
            <div class="progress-bar" id="progressBar">0%</div>
        </div>

        <div class="status" id="statusMessage"></div>
    </div>

    <script>
        const form = document.getElementById('uploadForm');
        const uploadKeyInput = document.getElementById('uploadKey');
        const fileInput = document.getElementById('fileInput');
        const submitBtn = document.getElementById('submitBtn');
        const progressContainer = document.getElementById('progressContainer');
        const progressBar = document.getElementById('progressBar');
        const statusMsg = document.getElementById('statusMessage');

        // Restore saved key from localStorage if present
        const savedKey = localStorage.getItem('torrent_upload_key');
        if (savedKey) uploadKeyInput.value = savedKey;

        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const file = fileInput.files[0];
            const key = uploadKeyInput.value.trim();
            if (!file || !key) return;

            localStorage.setItem('torrent_upload_key', key);

            submitBtn.disabled = true;
            progressContainer.style.display = 'block';
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            statusMsg.className = 'status';
            statusMsg.textContent = 'Uploading file...';

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/upload', true);
            xhr.setRequestHeader('X-Upload-Key', key);

            xhr.upload.onprogress = (event) => {
                if (event.lengthComputable) {
                    const percent = Math.round((event.loaded / event.total) * 100);
                    progressBar.style.width = percent + '%';
                    progressBar.textContent = percent + '%';
                }
            };

            xhr.onload = () => {
                submitBtn.disabled = false;
                if (xhr.status === 202) {
                    try {
                        const res = JSON.parse(xhr.responseText);
                        statusMsg.className = 'status success';
                        statusMsg.textContent = '✅ File uploaded! Generating torrent and sending to your Telegram bot...';
                    } catch {
                        statusMsg.className = 'status success';
                        statusMsg.textContent = '✅ Upload accepted!';
                    }
                } else {
                    statusMsg.className = 'status error';
                    statusMsg.textContent = '❌ Upload failed: ' + xhr.responseText;
                }
            };

            xhr.onerror = () => {
                submitBtn.disabled = false;
                statusMsg.className = 'status error';
                statusMsg.textContent = '❌ Connection error occurred during upload.';
            };

            const formData = new FormData();
            formData.append('file', file);
            xhr.send(formData);
        });
    </script>
</body>
</html>"""

    @app.get("/health", tags=["ops"])
    async def health() -> Dict[str, Any]:
        """Health check – returns 200 OK with status information."""
        manager = get_job_manager()
        jobs = await manager.list_all()
        return {
            "status": "ok",
            "timestamp": time.time(),
            "jobs_total": len(jobs),
        }

    @app.post(
        "/upload",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(verify_api_key), Depends(check_rate_limit)],
        tags=["upload"],
    )
    async def upload_file(
        request: Request,
        file: UploadFile = File(...),
    ) -> Dict[str, Any]:
        """
        Upload a large file for torrent creation.

        Authentication: X-Upload-Key header required.
        Rate limit: configurable via RATE_LIMIT_RPM env var.

        Returns 202 Accepted with the job_id to track progress via /status/{job_id}.
        """
        record = await handle_upload(file, request)
        return {
            "status": "accepted",
            "job_id": record.job_id,
            "filename": record.filename,
            "message": "File received. Torrent creation will begin shortly.",
        }

    @app.get(
        "/status/{job_id}",
        dependencies=[Depends(verify_api_key)],
        tags=["upload"],
    )
    async def job_status(job_id: str) -> Dict[str, Any]:
        """Return the current state of a torrent-creation job."""
        manager = get_job_manager()
        record = await manager.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return record.to_dict()

    @app.get("/media/download/{filename}", tags=["media"])
    async def download_large_media(filename: str):
        """Direct download link for files exceeding Telegram's 50MB limit."""
        from bot.downloader import DOWNLOAD_TMP_DIR
        file_path = (DOWNLOAD_TMP_DIR / filename).resolve()
        if not file_path.exists() or not str(file_path).startswith(str(DOWNLOAD_TMP_DIR.resolve())):
            raise HTTPException(status_code=404, detail="File not found or expired")
        return FileResponse(file_path, filename=filename)

    return app


# Module-level app instance used by uvicorn
app = create_app()

