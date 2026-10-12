"""Phase 4 Wave 1 — STR-04, STR-05."""
import asyncio
import queue
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_mock_process():
    """Build a mock asyncio.subprocess.Process with stdin pipe."""
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_stdin.close = MagicMock()

    mock_proc = MagicMock()
    mock_proc.stdin = mock_stdin
    mock_proc.returncode = None
    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)
    return mock_proc


@pytest.mark.asyncio
async def test_stream_start(mock_audio_device_4ch):
    """STR-04: StreamAdapter.start(target) launches FFmpeg subprocess with stdin=PIPE."""
    import sys
    sys.path.insert(0, "audio-service")
    from streamer import StreamAdapter

    mock_proc = _make_mock_process()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
        adapter = StreamAdapter()
        result = await adapter.start("rtmp://example/live")

        assert result["status"] == "streaming"

        # Verify FFmpeg was called with stdin=PIPE
        call_kwargs = mock_exec.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("stdin") == asyncio.subprocess.PIPE
        assert call_kwargs.kwargs.get("stderr") == asyncio.subprocess.DEVNULL

        # Verify pipe:0 input was specified in the command args
        cmd_args = call_kwargs.args
        assert "pipe:0" in cmd_args

        # Cleanup
        adapter._is_streaming = False
        if adapter._drain_task:
            adapter._drain_task.cancel()
            try:
                await adapter._drain_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_stream_channel_source(mock_audio_device_4ch, monkeypatch):
    """STR-05: Stream PCM bytes derived from indata[:, 2:4], not indata[:, :2]."""
    import sys
    sys.path.insert(0, "audio-service")
    import recorder
    from recorder import ContinuousRecorder, _str_q

    import config as cfg_module
    # Getrennte Paare ausdruecklich setzen (Default ist zweimal 1,2 fuer Stereo-Eingaenge).
    monkeypatch.setattr(cfg_module.config, "record_channels", [1, 2])
    monkeypatch.setattr(cfg_module.config, "stream_channels", [3, 4])

    # Build a 4-channel indata with distinct values in each channel pair
    N = 1024
    indata = np.zeros((N, 4), dtype=np.float32)
    indata[:, 0] = 0.1   # ch1
    indata[:, 1] = 0.2   # ch2
    indata[:, 2] = 0.5   # ch3
    indata[:, 3] = 0.6   # ch4

    # Invoke the audio callback directly (simulating PortAudio)
    rec = ContinuousRecorder()
    rec._is_recording = True
    rec._paused = False
    recorder.stream_enabled.set()  # StreamAdapter sets this while it consumes _str_q
    try:
        rec._audio_callback(indata, N, None, None)
    finally:
        recorder.stream_enabled.clear()

    # Expected ch3+4 bytes
    expected_str_bytes = (np.clip(indata[:, 2:4], -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    # Expected ch1+2 bytes (must differ)
    expected_rec_bytes = (np.clip(indata[:, :2], -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    # Pull from _str_q — these are what StreamAdapter would consume
    str_bytes = _str_q.get_nowait()

    assert str_bytes == expected_str_bytes, "StreamAdapter should receive ch3+4 bytes"
    assert str_bytes != expected_rec_bytes, "Stream bytes must differ from recording bytes"
