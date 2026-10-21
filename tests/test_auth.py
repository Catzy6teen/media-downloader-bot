"""
tests/test_auth.py – Tests for X-Upload-Key authentication.

Uses FastAPI TestClient to exercise the /upload and /status endpoints
without starting a real server or requiring Telegram credentials.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Patch env before any app import so Settings initialises correctly
os.environ.setdefault("BOT_TOKEN", "1234567890:AAFakeTokenForTestingPurposesOnly")
os.environ.setdefault("OWNER_ID", "999999999")
os.environ.setdefault("UPLOAD_KEY", "test-upload-key-that-is-long-enough")
os.environ.setdefault("UPLOAD_DIR", "/tmp/tg_test_uploads")
os.environ.setdefault("TORRENT_DIR", "/tmp/tg_test_torrents")


@pytest.fixture(scope="module")
def client():
    # Import after env is set
    from core.jobs import init_job_manager
    import core.jobs as _jobs_module
    mgr = init_job_manager(2)
    _jobs_module.job_manager = mgr

    from web.app import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---- /health (no auth) ----------------------------------------------------- #

def test_health_no_auth(client):
    """Health endpoint should return 200 without any auth header."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---- /upload auth ---------------------------------------------------------- #

def test_upload_missing_key(client, tmp_path):
    """Missing X-Upload-Key should return 401."""
    resp = client.post(
        "/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 401


def test_upload_wrong_key(client, tmp_path):
    """Wrong X-Upload-Key should return 401."""
    resp = client.post(
        "/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers={"X-Upload-Key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_upload_correct_key(client, tmp_path, monkeypatch):
    """
    Correct key with a small file should return 202 and a job_id.
    We monkeypatch UPLOAD_DIR to a real temp dir so the file write succeeds.
    """
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.settings, "UPLOAD_DIR", tmp_path)

    resp = client.post(
        "/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        headers={"X-Upload-Key": "test-upload-key-that-is-long-enough"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["filename"] == "hello.txt"


# ---- /status auth ---------------------------------------------------------- #

def test_status_missing_key(client):
    """Missing auth on /status should return 401."""
    resp = client.get("/status/nonexistent-job-id")
    assert resp.status_code == 401


def test_status_not_found(client):
    """Valid auth but unknown job_id should return 404."""
    resp = client.get(
        "/status/00000000-0000-0000-0000-000000000000",
        headers={"X-Upload-Key": "test-upload-key-that-is-long-enough"},
    )
    assert resp.status_code == 404
