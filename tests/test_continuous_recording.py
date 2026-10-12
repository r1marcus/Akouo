"""Phase 4 Wave 1 — REC-07, REC-08, REC-09, REC-10, REC-13."""
import asyncio
import queue
import pathlib

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _drain_queues():
    """Prevent cross-test queue pollution."""
    import sys
    sys.path.insert(0, "audio-service")
    try:
        from recorder import _rec_q, _str_q
        while not _rec_q.empty():
            _rec_q.get_nowait()
        while not _str_q.empty():
            _str_q.get_nowait()
    except ImportError:
        pass
    yield
    try:
        from recorder import _rec_q, _str_q
        while not _rec_q.empty():
            _rec_q.get_nowait()
        while not _str_q.empty():
            _str_q.get_nowait()
    except ImportError:
        pass


def test_continuous_start(mock_audio_device_4ch, tmp_recordings):
    """REC-07: start_session() returns status=recording with WAV path on disk."""
    import sys
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder
    import config as cfg_module
    cfg_module.config.sessions_dir = tmp_recordings / "sessions"

    rec = ContinuousRecorder()
    result = rec.start_session(service_type="Gottesdienst", output_dir=str(tmp_recordings))
    try:
        assert result["status"] == "recording"
        assert result["wav_path"].endswith(".wav")
        assert pathlib.Path(result["wav_path"]).exists()
    finally:
        # cleanup: stop stream
        rec._is_recording = False
        if rec._stream:
            rec._stream.stop()
            rec._stream.close()
        if rec._wav_file:
            rec._wav_file.close()


@pytest.mark.asyncio
async def test_wav_grows_on_disk(mock_audio_device_4ch, tmp_recordings):
    """REC-07: WAV file size grows as audio callback feeds chunks (no in-memory buffer)."""
    import sys
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder, _rec_q

    import config as cfg_module
    cfg_module.config.sessions_dir = tmp_recordings / "sessions"

    rec = ContinuousRecorder()
    result = rec.start_session(service_type="Gottesdienst")

    assert result["status"] == "recording"
    wav_path = pathlib.Path(result["wav_path"])

    # Record initial size (header only)
    size_before = wav_path.stat().st_size

    # Simulate 5 callback chunks via _rec_q
    chunk = (np.zeros((1024, 2), dtype=np.int16)).tobytes()
    for _ in range(5):
        _rec_q.put_nowait(chunk)

    # Run drain coroutine for a short time
    drain_task = asyncio.create_task(rec._drain_rec_queue())
    await asyncio.sleep(0.1)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    size_after = wav_path.stat().st_size
    assert size_after > size_before, f"WAV file should grow; before={size_before}, after={size_after}"

    # Cleanup
    rec._is_recording = False
    if rec._stream:
        rec._stream.stop()
        rec._stream.close()
    if rec._wav_file:
        rec._wav_file.close()


def test_add_marker(mock_audio_device_4ch, tmp_recordings):
    """REC-08: place_marker('Predigt') returns dict with offset_seconds."""
    import sys
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder

    import config as cfg_module
    cfg_module.config.sessions_dir = tmp_recordings / "sessions"

    rec = ContinuousRecorder()
    rec.start_session(service_type="Gottesdienst")
    try:
        result = rec.place_marker("Predigt")
        assert result["status"] == "ok"
        assert "marker" in result
        assert result["marker"]["type"] == "Predigt"
        assert isinstance(result["marker"]["offset_seconds"], float)
        assert result["marker"]["offset_seconds"] >= 0.0
        assert "wall_time" in result["marker"]
    finally:
        rec._is_recording = False
        if rec._stream:
            rec._stream.stop()
            rec._stream.close()
        if rec._wav_file:
            rec._wav_file.close()


def test_marker_type_validation(mock_audio_device_4ch, tmp_recordings, monkeypatch):
    """REC-09: eine unbekannte Beitragsart wird abgelehnt und nicht eingetragen.

    Geprueft wird gegen die Liste, die die API geschoben hat (PUT /api/config/erkennung).
    Vorher hielt der Audio-Dienst eine eigene Kopie — die wich von der API ab und war
    genau deshalb eine Fehlerquelle.
    """
    import sys
    sys.path.insert(0, "audio-service")
    import recorder as recorder_modul
    from recorder import ContinuousRecorder

    import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "sessions_dir", tmp_recordings / "sessions")
    monkeypatch.setattr(recorder_modul, "BEITRAGSARTEN", ["Predigt", "Chorlied"])

    rec = ContinuousRecorder()
    rec.start_session(service_type="Gottesdienst")
    try:
        result = rec.place_marker("Unbekannt")
        assert result["status"] == "error"
        assert len(rec._markers) == 0

        # Was auf der Liste steht, geht durch.
        assert rec.place_marker("Predigt")["status"] != "error"
        assert len(rec._markers) == 1
    finally:
        rec._is_recording = False
        if rec._stream:
            rec._stream.stop()
            rec._stream.close()
        if rec._wav_file:
            rec._wav_file.close()


def test_marker_ohne_geschobene_liste_wird_nicht_geprueft(mock_audio_device_4ch, tmp_recordings, monkeypatch):
    """Solange die API nichts geschoben hat, prueft der Audio-Dienst nicht.

    Das ist Absicht: die API ist Besitzerin der Liste und prueft selbst. Wuerde der
    Dienst hier mit leerer Liste alles ablehnen, waere er nach einem Neustart vor der
    API unbenutzbar — und genau dann laeuft moeglicherweise eine Aufnahme.
    """
    import sys
    sys.path.insert(0, "audio-service")
    import recorder as recorder_modul
    from recorder import ContinuousRecorder

    import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "sessions_dir", tmp_recordings / "sessions")
    monkeypatch.setattr(recorder_modul, "BEITRAGSARTEN", [])

    rec = ContinuousRecorder()
    rec.start_session(service_type="Gottesdienst")
    try:
        assert rec.place_marker("Posaunenchor")["status"] != "error"
        assert len(rec._markers) == 1
    finally:
        rec._is_recording = False
        if rec._stream:
            rec._stream.stop()
            rec._stream.close()
        if rec._wav_file:
            rec._wav_file.close()


def test_channel_deinterleave(mock_audio_device_4ch, tmp_recordings, monkeypatch):
    """REC-10: rec PCM (ch1+2) != stream PCM (ch3+4) from same indata."""
    import sys
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder, _rec_q, _str_q, stream_enabled

    import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "sessions_dir", tmp_recordings / "sessions")
    # Getrennte Paare ausdruecklich setzen: der Default ist zweimal 1,2, damit das
    # System auch mit einem Stereo-Eingang laeuft. Geprueft wird hier das Trennen.
    monkeypatch.setattr(cfg_module.config, "record_channels", [1, 2])
    monkeypatch.setattr(cfg_module.config, "stream_channels", [3, 4])

    rec = ContinuousRecorder()
    rec._is_recording = True  # allow callback without full start
    rec._paused = False

    N = 1024
    # Build 4-channel indata with distinct values per channel pair
    indata = np.zeros((N, 4), dtype=np.float32)
    indata[:, 0] = 0.1   # ch1
    indata[:, 1] = 0.2   # ch2
    indata[:, 2] = 0.5   # ch3
    indata[:, 3] = 0.6   # ch4

    stream_enabled.set()  # StreamAdapter sets this while it consumes _str_q
    try:
        rec._audio_callback(indata, N, None, None)
    finally:
        stream_enabled.clear()

    rec_bytes = _rec_q.get_nowait()
    str_bytes = _str_q.get_nowait()

    # rec bytes = ch1+2, str bytes = ch3+4 — they must differ
    assert rec_bytes != str_bytes

    # Verify rec_bytes corresponds to ch1+2
    expected_rec = (np.clip(indata[:, :2], -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    assert rec_bytes == expected_rec

    # Verify str_bytes corresponds to ch3+4
    expected_str = (np.clip(indata[:, 2:4], -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    assert str_bytes == expected_str


def test_meter_live_without_recording(tmp_recordings):
    """Level meter is fed whenever the input is open; idle input queues no WAV or stream chunks."""
    import sys
    from unittest.mock import MagicMock
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder, _rec_q, _str_q

    rec = ContinuousRecorder()
    rec._stream = MagicMock()  # input open, not recording, not streaming
    assert rec.get_rms_level() == 0.0  # no callback yet

    indata = np.full((1024, 4), 0.5, dtype=np.float32)
    rec._audio_callback(indata, 1024, None, None)

    assert rec.get_rms_level() == pytest.approx(0.5, abs=1e-3)
    assert rec.input_available()
    assert _rec_q.empty(), "no WAV chunks while idle"
    assert _str_q.empty(), "no stream chunks without a StreamAdapter consumer"


def test_input_device_search_follows_configured_names(tmp_recordings):
    """Device search walks config.device_names in order — Dante first, Focusrite as fallback."""
    import sys
    from unittest.mock import patch
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder
    import config as cfg_module

    devices = [
        {"name": "Focusrite USB Audio", "max_input_channels": 4, "max_output_channels": 0},
        {"name": "Dante Virtual Soundcard", "max_input_channels": 64, "max_output_channels": 64},
        {"name": "Mac Studio-Lautsprecher", "max_input_channels": 0, "max_output_channels": 2},
    ]
    rec = ContinuousRecorder()
    original = cfg_module.config.device_names
    try:
        with patch("sounddevice.query_devices", return_value=devices):
            cfg_module.config.device_names = ["Dante", "Focusrite"]
            assert rec._find_input_device() == 1, "Dante wins even though Focusrite is listed first"
            cfg_module.config.device_names = ["Focusrite"]
            assert rec._find_input_device() == 0
            cfg_module.config.device_names = ["Behringer"]
            assert rec._find_input_device() is None
    finally:
        cfg_module.config.device_names = original


@pytest.mark.asyncio
async def test_stop_keeps_input_open(mock_audio_device_4ch, tmp_recordings):
    """Stopping a session keeps the input stream open (meter stays live) and the next session reuses it."""
    import sys
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder

    import config as cfg_module
    cfg_module.config.sessions_dir = tmp_recordings / "sessions"

    rec = ContinuousRecorder()
    assert rec.start_session(service_type="Gottesdienst")["status"] == "recording"
    stream = rec._stream
    try:
        result = await rec.stop_session()
        assert result["status"] == "stopped"
        assert rec._stream is stream
        stream.close.assert_not_called()

        assert rec.start_session(service_type="Gottesdienst")["status"] == "recording"
        assert mock_audio_device_4ch["stream"].call_count == 1, "second session must reuse the open input"
    finally:
        rec._is_recording = False
        if rec._drain_task:
            rec._drain_task.cancel()
            await asyncio.sleep(0)
        if rec._wav_file:
            rec._wav_file.close()
        rec.close_input()


@pytest.mark.asyncio
async def test_pause_resume(mock_audio_device_4ch, tmp_recordings):
    """REC-13: pause stops WAV writes, resume records gap with start+end offsets."""
    import sys
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder, _rec_q

    import config as cfg_module
    cfg_module.config.sessions_dir = tmp_recordings / "sessions"

    rec = ContinuousRecorder()
    result = rec.start_session(service_type="Gottesdienst")
    assert result["status"] == "recording"
    wav_path = pathlib.Path(result["wav_path"])

    # Add a few chunks and drain to establish baseline size
    chunk = (np.zeros((1024, 2), dtype=np.int16)).tobytes()
    for _ in range(3):
        _rec_q.put_nowait(chunk)
    drain_task = asyncio.create_task(rec._drain_rec_queue())
    await asyncio.sleep(0.1)
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass

    size_before_pause = wav_path.stat().st_size
    assert size_before_pause > 44, "WAV should have data before pause"

    # Pause
    pause_result = rec.pause()
    assert pause_result["status"] == "paused"
    assert "paused_at_offset" in pause_result

    # Feed 100 chunks while paused — wav should not grow
    for _ in range(100):
        _rec_q.put_nowait(chunk)

    drain_task2 = asyncio.create_task(rec._drain_rec_queue())
    await asyncio.sleep(0.15)
    drain_task2.cancel()
    try:
        await drain_task2
    except asyncio.CancelledError:
        pass

    size_during_pause = wav_path.stat().st_size
    assert size_during_pause == size_before_pause, "WAV must not grow during pause"

    # Resume
    resume_result = rec.resume()
    assert resume_result["status"] == "recording"
    assert "resumed_at_offset" in resume_result
    assert "gap_seconds" in resume_result

    # Gap must be recorded with start and end offsets
    assert len(rec._gaps) == 1
    gap = rec._gaps[0]
    assert gap["start_offset"] is not None
    assert gap["end_offset"] is not None
    assert gap["end_offset"] > gap["start_offset"]

    # Feed more chunks after resume — wav should grow
    for _ in range(5):
        _rec_q.put_nowait(chunk)

    drain_task3 = asyncio.create_task(rec._drain_rec_queue())
    await asyncio.sleep(0.1)
    drain_task3.cancel()
    try:
        await drain_task3
    except asyncio.CancelledError:
        pass

    size_after_resume = wav_path.stat().st_size
    assert size_after_resume > size_during_pause, "WAV should grow after resume"

    # Cleanup
    rec._is_recording = False
    if rec._stream:
        rec._stream.stop()
        rec._stream.close()
    if rec._wav_file:
        rec._wav_file.close()


def test_session_gets_own_folder(mock_audio_device_4ch, tmp_recordings):
    """Every session gets its own folder <Datum>_<Typ>_<NNN>/ holding aufnahme.wav."""
    import sys
    from datetime import datetime
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder

    import config as cfg_module
    cfg_module.config.sessions_dir = tmp_recordings / "sessions"

    date_str = datetime.now().strftime("%Y-%m-%d")
    paths = []
    for _ in range(2):
        rec = ContinuousRecorder()
        result = rec.start_session(service_type="Gebetsstunde")
        try:
            assert result["status"] == "recording"
            paths.append(pathlib.Path(result["wav_path"]))
        finally:
            rec._is_recording = False
            if rec._wav_file:
                rec._wav_file.close()
            rec.close_input()

    first, second = paths
    assert first.name == "aufnahme.wav"
    assert first.parent.name == f"{date_str}_Gebetsstunde_001"
    assert first.parent.parent == cfg_module.config.sessions_dir
    assert first.exists()
    # Counter keeps running per day and service type
    assert second.parent.name == f"{date_str}_Gebetsstunde_002"


def test_session_counter_respects_old_flat_recordings(tmp_recordings, mock_audio_device_4ch):
    """Old flat WAVs in sessions/ still count — a number is never handed out twice."""
    import sys
    from datetime import datetime
    sys.path.insert(0, "audio-service")
    from recorder import ContinuousRecorder

    import config as cfg_module
    sessions = tmp_recordings / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    cfg_module.config.sessions_dir = sessions

    date_str = datetime.now().strftime("%Y-%m-%d")
    (sessions / f"{date_str}_Bibelstunde_001.wav").write_bytes(b"")

    rec = ContinuousRecorder()
    result = rec.start_session(service_type="Bibelstunde")
    try:
        assert pathlib.Path(result["wav_path"]).parent.name == f"{date_str}_Bibelstunde_002"
    finally:
        rec._is_recording = False
        if rec._wav_file:
            rec._wav_file.close()
        rec.close_input()


def test_export_dir_follows_layout(tmp_recordings):
    """Export target: exports/ inside the session folder — old flat WAVs keep the shared folder."""
    import sys
    sys.path.insert(0, "audio-service")
    import config as cfg_module

    session_folder = tmp_recordings / "sessions" / "2026-09-18_Gebetsstunde_003"
    cfg_module.config.exports_dir = tmp_recordings / "exports"

    assert cfg_module.export_dir(session_folder / "aufnahme.wav") == session_folder / "exports"
    assert cfg_module.session_dir(session_folder / "aufnahme.wav") == session_folder

    flat = tmp_recordings / "sessions" / "2026-04-29_Gottesdienst_001.wav"
    assert cfg_module.export_dir(flat) == tmp_recordings / "exports"
    assert cfg_module.session_dir(flat) is None


def test_transcript_paths_follow_layout(tmp_recordings):
    """Transcripts: transcript.txt/.jsonl in the session folder, old names for flat WAVs."""
    import sys
    sys.path.insert(0, "audio-service")
    from transcriber import LiveTranscriber

    session_folder = tmp_recordings / "sessions" / "2026-09-18_Gebetsstunde_003"
    tr = LiveTranscriber()
    tr._wav_path = session_folder / "aufnahme.wav"
    assert tr._transcript_path("txt") == session_folder / "transcript.txt"
    assert tr._transcript_path("jsonl") == session_folder / "transcript.jsonl"

    flat = tmp_recordings / "sessions" / "2026-04-29_Gottesdienst_001.wav"
    tr._wav_path = flat
    assert tr._transcript_path("txt") == flat.with_suffix(".txt")
    assert tr._transcript_path("jsonl") == flat.with_suffix(".transcript.jsonl")
