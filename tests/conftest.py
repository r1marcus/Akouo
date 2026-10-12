import pytest
import tempfile
import pathlib
from unittest.mock import MagicMock, patch


@pytest.fixture
def tmp_recordings(tmp_path):
    """Temporary recordings directory mirroring ~/Recordings/Tontechnik/ structure."""
    recordings_dir = tmp_path / "Recordings" / "Tontechnik"
    recordings_dir.mkdir(parents=True)
    return recordings_dir


@pytest.fixture
def mock_audio_device():
    """Mock sounddevice to avoid hardware dependency in unit tests."""
    mock_device = MagicMock()
    mock_device.__enter__ = MagicMock(return_value=mock_device)
    mock_device.__exit__ = MagicMock(return_value=False)
    with patch("sounddevice.query_devices") as mock_query, \
         patch("sounddevice.RawInputStream") as mock_stream:
        mock_query.return_value = [
            {"name": "Focusrite USB Audio", "max_input_channels": 2, "max_output_channels": 0},
            {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
        ]
        mock_stream.return_value = mock_device
        yield {"query": mock_query, "stream": mock_stream, "device": mock_device}


@pytest.fixture
def mock_audio_device_4ch():
    """Mock sounddevice for 4-channel Focusrite (Phase 4 ContinuousRecorder)."""
    mock_stream_obj = MagicMock()
    mock_stream_obj.start = MagicMock()
    mock_stream_obj.stop = MagicMock()
    mock_stream_obj.close = MagicMock()
    with patch("sounddevice.query_devices") as mock_query, \
         patch("sounddevice.InputStream") as mock_stream:
        mock_query.return_value = [
            {"name": "Focusrite USB Audio", "max_input_channels": 4, "max_output_channels": 0},
            {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
        ]
        mock_stream.return_value = mock_stream_obj
        yield {"query": mock_query, "stream": mock_stream, "device": mock_stream_obj}
