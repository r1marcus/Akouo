"""Tests for audio-service POST /api/uploads/nas-push endpoint (EXP-03 helper).

Pattern mirrors test_segment_export.py audio_client fixture setup.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ─── Fixture: load audio-service/main.py without shadowing api/main.py ───────

@pytest.fixture(scope="module")
def audio_client(tmp_path_factory):
    """TestClient for audio-service main.py with hardware mocked."""
    import os

    audio_service_dir = os.path.join(os.path.dirname(__file__), "..", "audio-service")
    main_path = os.path.join(audio_service_dir, "main.py")

    mock_stream_obj = MagicMock()
    mock_stream_obj.start = MagicMock()
    mock_stream_obj.stop = MagicMock()
    mock_stream_obj.close = MagicMock()

    with patch("sounddevice.query_devices") as mock_query, \
         patch("sounddevice.InputStream") as mock_stream:
        mock_query.return_value = [
            {"name": "Focusrite USB Audio", "max_input_channels": 4, "max_output_channels": 0},
        ]
        mock_stream.return_value = mock_stream_obj

        # Force-reload audio-service modules so api/main.py doesn't shadow
        for mod_name in list(sys.modules.keys()):
            if mod_name in ("main", "recorder", "streamer", "segment_exporter", "nas_client",
                            "audio_service_main_naspush"):
                del sys.modules[mod_name]

        spec = importlib.util.spec_from_file_location("audio_service_main_naspush", main_path)
        audio_main = importlib.util.module_from_spec(spec)
        sys.modules["audio_service_main_naspush"] = audio_main
        spec.loader.exec_module(audio_main)

        from fastapi.testclient import TestClient
        client = TestClient(audio_main.app, raise_server_exceptions=False)
        yield client, audio_main, tmp_path_factory


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_nas_push_404_when_local_path_missing(audio_client):
    """404 when local_path does not exist on disk."""
    client, _, _ = audio_client
    resp = client.post("/api/uploads/nas-push", json={
        "local_path": "/nonexistent/path/file.mp3",
        "remote_subpath": "uploads/2026/04/file.mp3",
    })
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
    assert "local_path not found" in resp.json()["detail"]


def test_nas_push_422_when_remote_subpath_contains_dotdot(audio_client, tmp_path):
    """422 when remote_subpath contains '..' (path traversal guard)."""
    client, _, _ = audio_client
    real_file = tmp_path / "test.mp3"
    real_file.write_bytes(b"\xff\xfb\x90\x00")

    resp = client.post("/api/uploads/nas-push", json={
        "local_path": str(real_file),
        "remote_subpath": "../etc/passwd",
    })
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    assert ".." in resp.json()["detail"]


def test_nas_push_422_when_remote_subpath_empty(audio_client, tmp_path):
    """422 when remote_subpath is empty string."""
    client, _, _ = audio_client
    real_file = tmp_path / "test2.mp3"
    real_file.write_bytes(b"\xff\xfb\x90\x00")

    resp = client.post("/api/uploads/nas-push", json={
        "local_path": str(real_file),
        "remote_subpath": "",
    })
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


def test_nas_push_skipped_when_nas_not_configured(audio_client, monkeypatch, tmp_path):
    """Returns status='skipped' when nas_server is empty string."""
    client, audio_main, _ = audio_client
    real_file = tmp_path / "test3.mp3"
    real_file.write_bytes(b"\xff\xfb\x90\x00")

    monkeypatch.setattr(audio_main.config, "nas_server", "")

    resp = client.post("/api/uploads/nas-push", json={
        "local_path": str(real_file),
        "remote_subpath": "uploads/2026/04/test3.mp3",
    })
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "skipped"
    assert body["nas_path"] is None
    assert body["uploaded_at"] is None


def test_nas_push_success_path(audio_client, monkeypatch, tmp_path):
    """Happy path: status='uploaded', uploaded_at is ISO string, nas_path returned."""
    client, audio_main, _ = audio_client
    real_file = tmp_path / "test4.mp3"
    real_file.write_bytes(b"\xff\xfb\x90\x00")

    monkeypatch.setattr(audio_main.config, "nas_server", "srv.local")
    monkeypatch.setattr(audio_main.config, "nas_password", "secret")

    now = datetime(2026, 4, 29, 10, 0, 0)

    def fake_upload(local_path, remote_subpath):
        return {
            "status": "uploaded",
            "error": None,
            "nas_path": f"\\\\srv.local\\Audio\\GD\\{remote_subpath}",
            "uploaded_at": now,
        }

    monkeypatch.setattr(audio_main.nas_client, "upload_file", fake_upload)

    resp = client.post("/api/uploads/nas-push", json={
        "local_path": str(real_file),
        "remote_subpath": "uploads/2026/04/test4.mp3",
    })
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "uploaded"
    assert body["uploaded_at"] == now.isoformat()
    assert body["nas_path"] is not None
    assert body["error"] is None
