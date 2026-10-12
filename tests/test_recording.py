import pytest
import pathlib
from unittest.mock import MagicMock, patch


def test_audio_capture(tmp_recordings, mock_audio_device):
    """FND-01: sounddevice captures audio from the configured input device."""
    from recorder import Recorder
    recorder = Recorder()
    device_idx = recorder._find_input_device()
    assert device_idx is not None


def test_start_recording(tmp_recordings, mock_audio_device):
    """REC-01: start() opens audio stream and returns status=recording."""
    from recorder import Recorder
    recorder = Recorder()
    result = recorder.start(service_type="Gottesdienst", output_dir=str(tmp_recordings))
    assert result["status"] == "recording"
    assert recorder.is_recording is True


def test_stop_recording(tmp_recordings, mock_audio_device):
    """REC-02: stop() encodes PCM to MP3 and returns path to finished file."""
    from recorder import Recorder
    recorder = Recorder()
    recorder.start(service_type="Gottesdienst", output_dir=str(tmp_recordings))
    result = recorder.stop()
    assert result["status"] == "saved"
    assert result["path"].endswith(".mp3")
    assert pathlib.Path(result["path"]).exists()


def test_separate_tracks(tmp_recordings, mock_audio_device):
    """REC-03: each start/stop cycle produces a distinct file; no merging."""
    from recorder import Recorder
    recorder = Recorder()
    recorder.start(service_type="Gottesdienst", output_dir=str(tmp_recordings))
    r1 = recorder.stop()
    recorder.start(service_type="Gottesdienst", output_dir=str(tmp_recordings))
    r2 = recorder.stop()
    assert r1["path"] != r2["path"]


def test_auto_naming(tmp_recordings, mock_audio_device):
    """REC-04: filename matches YYYY-MM-DD_ServiceType_NNN.mp3 pattern."""
    import re
    from recorder import Recorder
    recorder = Recorder()
    recorder.start(service_type="Gottesdienst", output_dir=str(tmp_recordings))
    result = recorder.stop()
    filename = pathlib.Path(result["path"]).name
    assert re.match(r"^\d{4}-\d{2}-\d{2}_Gottesdienst_\d{3}\.mp3$", filename), \
        f"Filename '{filename}' does not match expected pattern"
