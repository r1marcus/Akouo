"""Phase 7 Plan 02 — segment_exporter tests and segment-export endpoint tests.

Tests are split into two sections:
1. Unit tests for export_segment() from segment_exporter.py (TDD RED/GREEN cycle)
2. Integration tests for POST /api/session/segment-export against audio-service main.py
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from pydub import AudioSegment


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_silent_wav(path: pathlib.Path, duration_ms: int = 10_000, frame_rate: int = 48_000) -> None:
    """Write a stereo silent WAV at frame_rate for the given duration."""
    audio = (
        AudioSegment.silent(duration=duration_ms, frame_rate=frame_rate)
        .set_channels(2)
    )
    audio.export(str(path), format="wav")


# ─── Section 1: Unit tests for export_segment() ───────────────────────────────

def test_export_segment_duration(tmp_path):
    """Test 1: slice [2.0, 7.0) of a 10s WAV produces MP3 with duration_ms ≈ 5000 (±50ms)."""
    from segment_exporter import export_segment

    wav = tmp_path / "session.wav"
    mp3 = tmp_path / "segment.mp3"
    _make_silent_wav(wav, duration_ms=10_000)

    result = export_segment(wav, start_seconds=2.0, end_seconds=7.0, mp3_path=mp3)

    assert abs(result["duration_ms"] - 5000) <= 50, (
        f"Expected ~5000ms, got {result['duration_ms']}"
    )


def test_export_segment_bad_bounds_start_ge_end(tmp_path):
    """Test 2: start >= end raises ValueError containing 'start must be less than end'."""
    from segment_exporter import export_segment

    wav = tmp_path / "session.wav"
    mp3 = tmp_path / "segment.mp3"
    _make_silent_wav(wav, duration_ms=10_000)

    with pytest.raises(ValueError, match="start must be less than end"):
        export_segment(wav, start_seconds=5.0, end_seconds=5.0, mp3_path=mp3)

    with pytest.raises(ValueError, match="start must be less than end"):
        export_segment(wav, start_seconds=7.0, end_seconds=3.0, mp3_path=mp3)


def test_export_segment_end_exceeds_wav(tmp_path):
    """Test 3: end > WAV length raises ValueError containing 'end exceeds'."""
    from segment_exporter import export_segment

    wav = tmp_path / "session.wav"
    mp3 = tmp_path / "segment.mp3"
    _make_silent_wav(wav, duration_ms=10_000)

    with pytest.raises(ValueError, match="end exceeds"):
        export_segment(wav, start_seconds=0.0, end_seconds=15.0, mp3_path=mp3)


def test_export_segment_schneidet_bei_knapper_ueberschreitung(tmp_path):
    """Ein Ende knapp hinter dem Dateiende ist kein Fehler.

    Ein Track, der beim Stoppen noch lief, bekommt seine Grenze aus der Uhr
    (gestoppt minus gestartet). Der Schreiber hört Sekundenbruchteile früher auf —
    gemessen 0,7 s. Dann wird genommen, was da ist, statt die Datei zu verweigern.
    """
    from segment_exporter import export_segment

    wav = tmp_path / "session.wav"
    mp3 = tmp_path / "segment.mp3"
    _make_silent_wav(wav, duration_ms=10_000)

    ergebnis = export_segment(wav, start_seconds=5.0, end_seconds=10.7, mp3_path=mp3)
    assert mp3.exists()
    assert ergebnis["duration_ms"] <= 5_000 + 100   # bis zum Dateiende, nicht darüber


def test_export_segment_creates_mp3_and_parent(tmp_path):
    """Test 4: output MP3 file exists at returned mp3_path, parent dir auto-created."""
    from segment_exporter import export_segment

    wav = tmp_path / "session.wav"
    mp3 = tmp_path / "subdir" / "nested" / "segment.mp3"
    _make_silent_wav(wav, duration_ms=10_000)

    result = export_segment(wav, start_seconds=1.0, end_seconds=4.0, mp3_path=mp3)

    assert mp3.exists(), "MP3 output must exist after export_segment"
    assert pathlib.Path(result["mp3_path"]) == mp3


def test_export_segment_dict_shape(tmp_path):
    """Test 5: returned dict has keys mp3_path, duration_ms, frame_rate (same shape as export_wav_to_mp3)."""
    from segment_exporter import export_segment

    wav = tmp_path / "session.wav"
    mp3 = tmp_path / "segment.mp3"
    _make_silent_wav(wav, duration_ms=10_000)

    result = export_segment(wav, start_seconds=0.0, end_seconds=5.0, mp3_path=mp3)

    assert set(result.keys()) == {"mp3_path", "duration_ms", "frame_rate"}, (
        f"Unexpected dict keys: {set(result.keys())}"
    )
    assert result["frame_rate"] == 48000
    assert isinstance(result["duration_ms"], int)
    assert isinstance(result["mp3_path"], str)


# ─── Section 2: Integration tests for POST /api/session/segment-export ────────
# Uses FastAPI TestClient against audio-service main.py.
# Patch sounddevice to avoid hardware dependency.

@pytest.fixture(scope="module")
def audio_client(tmp_path_factory):
    """TestClient for audio-service main.py with hardware mocked.

    Uses importlib to load audio-service/main.py explicitly, since api/main.py
    is also on sys.path and would otherwise shadow it.
    """
    import importlib.util
    import os
    from unittest.mock import MagicMock, patch

    audio_service_dir = os.path.join(os.path.dirname(__file__), "..", "audio-service")
    main_path = os.path.join(audio_service_dir, "main.py")

    # Aufnahmeordner auf ein Wegwerf-Verzeichnis umbiegen, BEVOR config geladen wird.
    # Ohne das fiel config.export_dir() auf den echten ~/Recordings/.../exports zurueck
    # (die Test-WAV heisst session.wav, nicht aufnahme.wav, also gibt session_dir()
    # None zurueck) — und die Tests schrieben test.mp3 in den Produktionsordner.
    tmp_recordings = tmp_path_factory.mktemp("recordings")
    vorher_env = os.environ.get("RECORDINGS_DIR")
    os.environ["RECORDINGS_DIR"] = str(tmp_recordings)
    sys.modules.pop("config", None)

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

        # Force-load audio-service/main.py by file path so api/main.py doesn't shadow it
        for mod_name in list(sys.modules.keys()):
            if mod_name in ("main", "recorder", "streamer", "segment_exporter", "nas_client"):
                del sys.modules[mod_name]

        spec = importlib.util.spec_from_file_location("audio_service_main", main_path)
        audio_main = importlib.util.module_from_spec(spec)
        sys.modules["audio_service_main"] = audio_main
        spec.loader.exec_module(audio_main)

        from fastapi.testclient import TestClient
        client = TestClient(audio_main.app, raise_server_exceptions=False)
        try:
            yield client, audio_main, tmp_path_factory
        finally:
            if vorher_env is None:
                os.environ.pop("RECORDINGS_DIR", None)
            else:
                os.environ["RECORDINGS_DIR"] = vorher_env
            # config NICHT aus sys.modules entfernen: recorder haelt eine Referenz auf
            # dieses config-Objekt. Ein frisches Modul danach wuerde bedeuten, dass
            # spaetere Tests ein anderes config patchen als recorder liest.


def test_endpoint_happy_path(audio_client, tmp_path):
    """Happy path: 200 with export.duration_ms near 5000 and nas.status == 'skipped'."""
    client, audio_main, tmp_path_factory = audio_client

    # Create a real WAV file
    wav = tmp_path / "session.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 2.0,
        "end_seconds": 7.0,
        "mp3_filename": "test_export.mp3",
    })

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "export" in body
    assert "nas" in body
    assert body["nas"]["status"] == "skipped"
    assert abs(body["export"]["duration_ms"] - 5000) <= 100


def test_endpoint_bad_bounds_422(audio_client, tmp_path):
    """422 when start_seconds >= end_seconds."""
    client, audio_main, _ = audio_client

    wav = tmp_path / "session2.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 5.0,
        "end_seconds": 3.0,
        "mp3_filename": "out.mp3",
    })

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


def test_endpoint_bad_filename_422(audio_client, tmp_path):
    """422 when mp3_filename has no .mp3 extension."""
    client, audio_main, _ = audio_client

    wav = tmp_path / "session3.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 0.0,
        "end_seconds": 5.0,
        "mp3_filename": "no_extension",
    })

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


def test_endpoint_traversal_in_remote_subpath(audio_client, monkeypatch, tmp_path):
    """Traversal in remote_subpath: nas.status == 'failed', nas.error mentions '..', upload_file NOT called."""
    client, audio_main, _ = audio_client

    wav = tmp_path / "session4.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    # Monkeypatch config and nas_client.upload_file
    monkeypatch.setattr(audio_main.config, "nas_server", "srv")
    monkeypatch.setattr(audio_main.config, "nas_password", "x")

    sentinel_called = []

    def fake_upload(local_path, remote_subpath):
        sentinel_called.append((local_path, remote_subpath))
        return {"status": "uploaded", "error": None, "nas_path": "\\\\srv\\share\\x", "uploaded_at": None}

    monkeypatch.setattr(audio_main.nas_client, "upload_file", fake_upload)

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 0.0,
        "end_seconds": 5.0,
        "mp3_filename": "test.mp3",
        "remote_subpath": "../etc/passwd",
    })

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["nas"]["status"] == "failed", f"Expected failed, got {body['nas']}"
    assert ".." in body["nas"]["error"]
    assert len(sentinel_called) == 0, "upload_file should NOT have been called for traversal path"


def test_endpoint_nas_skipped_when_no_server(audio_client, monkeypatch, tmp_path):
    """NAS skipped when nas_server is empty, even with remote_subpath provided."""
    client, audio_main, _ = audio_client

    wav = tmp_path / "session5.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    monkeypatch.setattr(audio_main.config, "nas_server", "")

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 0.0,
        "end_seconds": 5.0,
        "mp3_filename": "test2.mp3",
        "remote_subpath": "2026/04/test2.mp3",
    })

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["nas"]["status"] == "skipped"


def test_endpoint_exports_into_session_folder(audio_client, tmp_path):
    """New layout: a WAV inside its own session folder exports to <session>/exports/."""
    client, audio_main, _ = audio_client

    session_folder = tmp_path / "sessions" / "2026-09-18_Gebetsstunde_003"
    session_folder.mkdir(parents=True)
    wav = session_folder / "aufnahme.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 0.0,
        "end_seconds": 3.0,
        "mp3_filename": "2026-09-18_Gebetsstunde_01_Predigt.mp3",
    })

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    mp3_path = pathlib.Path(resp.json()["mp3_path"])
    assert mp3_path.parent == session_folder / "exports"
    assert mp3_path.exists()


def test_endpoint_legacy_flat_wav_uses_shared_exports(audio_client, monkeypatch, tmp_path):
    """Backwards compatible: a flat WAV from before the session folders exports to exports_dir."""
    client, audio_main, _ = audio_client

    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True)
    wav = sessions / "2026-04-29_Gottesdienst_001.wav"
    _make_silent_wav(wav, duration_ms=10_000)

    shared_exports = tmp_path / "exports"
    monkeypatch.setattr(audio_main.config, "exports_dir", shared_exports)

    resp = client.post("/api/session/segment-export", json={
        "wav_path": str(wav),
        "start_seconds": 0.0,
        "end_seconds": 3.0,
        "mp3_filename": "2026-04-29_Gottesdienst_01_Predigt.mp3",
    })

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    mp3_path = pathlib.Path(resp.json()["mp3_path"])
    assert mp3_path.parent == shared_exports
    assert mp3_path.exists()
    assert not (sessions / "exports").exists(), "flat WAV must not grow a sibling exports folder"
