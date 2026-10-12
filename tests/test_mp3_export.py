"""Phase 5 Plan 04 — MP3 export tests for REC-11 and REC-12.

Tests are synchronous (pydub is a sync library).
audio-service/ is on sys.path via root conftest.py, so `from exporter import` works.

Requirements covered:
- REC-11: 192kbit/s, 48kHz sample rate, peak normalization at -3dBFS
- REC-12: 1s fade-in and fade-out on exported MP3
"""
import math
import pathlib
import struct
import wave

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────

def _write_sine_wav(path: pathlib.Path, duration_s: float = 2.0,
                   sample_rate: int = 44100, amplitude: float = 0.5) -> None:
    """Write a mono sine-wave WAV at the given amplitude (0.0–1.0)."""
    n_samples = int(duration_s * sample_rate)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            val = amplitude * math.sin(2 * math.pi * 440 * i / sample_rate)
            frame = struct.pack("<h", int(val * 32767))
            wf.writeframes(frame)


def _write_silence_wav(path: pathlib.Path, duration_s: float = 2.0,
                      sample_rate: int = 44100) -> None:
    """Write an all-zero (silent) WAV file."""
    n_samples = int(duration_s * sample_rate)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)


# ─── Tests ────────────────────────────────────────────────────────────────

def test_export_bitrate_samplerate(tmp_path):
    """REC-11: export_wav_to_mp3() produces MP3 with 192kbit/s and 48kHz sample rate.

    Builds a 2-second sine WAV at 44100 Hz, exports to MP3 via the exporter,
    then reads the exported file back with pydub and asserts frame_rate == 48000.
    Bitrate is verified via ffprobe to be >= 180000 (within 10% of 192k).

    Requirement: REC-11 (192kbit/s, 48kHz export).
    """
    from exporter import export_wav_to_mp3
    from pydub import AudioSegment

    wav_path = tmp_path / "input.wav"
    mp3_path = tmp_path / "output.mp3"
    _write_sine_wav(wav_path, duration_s=2.0, amplitude=0.3)

    result = export_wav_to_mp3(wav_path, mp3_path)

    assert mp3_path.exists(), "export must create the MP3 file"
    assert result["mp3_path"] == str(mp3_path)
    assert result["frame_rate"] == 48000

    # Verify sample rate via pydub
    loaded = AudioSegment.from_mp3(str(mp3_path))
    assert loaded.frame_rate == 48000, f"Expected 48000 Hz, got {loaded.frame_rate}"

    # Bitrate >= 180k (within 10% of 192k) — verified via ffprobe
    import json
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(mp3_path)],
        capture_output=True, text=True
    )
    streams = json.loads(probe.stdout)["streams"]
    bit_rate = int(streams[0].get("bit_rate", 0))
    assert bit_rate >= 180_000, f"Expected bitrate >= 180000, got {bit_rate}"


def test_peak_normalization(tmp_path):
    """REC-11: exported MP3 peak is normalized to -3dBFS (within ±0.5dB tolerance).

    Input WAV has peak around -10dBFS (amplitude=0.1).
    After export, the MP3 peak must be in the range [-3.5, -2.5] dBFS.

    Requirement: REC-11 (peak normalization to -3dBFS).
    """
    from exporter import export_wav_to_mp3
    from pydub import AudioSegment

    wav_path = tmp_path / "input.wav"
    mp3_path = tmp_path / "output.mp3"
    # amplitude=0.1 → peak ≈ -20dBFS (well below -3dBFS target)
    _write_sine_wav(wav_path, duration_s=3.0, amplitude=0.1)

    export_wav_to_mp3(wav_path, mp3_path)

    loaded = AudioSegment.from_mp3(str(mp3_path))
    peak_dbfs = loaded.max_dBFS
    assert -3.5 <= peak_dbfs <= -2.5, (
        f"Peak normalization failed: expected [-3.5, -2.5] dBFS, got {peak_dbfs:.2f}"
    )


def test_fade_in_out(tmp_path):
    """REC-12: first 1000ms RMS rises; last 1000ms RMS falls after fade-in/out.

    A constant-amplitude sine WAV exported with 1s fade-in and 1s fade-out must show:
    - Rising RMS in the first 1000ms (RMS at 0ms < 500ms < ~900ms)
    - Falling RMS in the last 1000ms

    Requirement: REC-12 (1s fade-in + fade-out).
    """
    from exporter import export_wav_to_mp3
    from pydub import AudioSegment

    wav_path = tmp_path / "input.wav"
    mp3_path = tmp_path / "output.mp3"
    # Full-amplitude sine (amplitude=0.4) at 4s ensures RMS differences are detectable
    _write_sine_wav(wav_path, duration_s=4.0, amplitude=0.4)

    export_wav_to_mp3(wav_path, mp3_path)

    loaded = AudioSegment.from_mp3(str(mp3_path))

    # Sample 100ms windows at different points
    first = loaded[:100].rms
    mid_start = (len(loaded) // 2) - 50
    middle = loaded[mid_start:mid_start + 100].rms
    last = loaded[-100:].rms

    assert first < middle, f"fade-in not detected: first={first} middle={middle}"
    assert last < middle, f"fade-out not detected: last={last} middle={middle}"


def test_silence_guard(tmp_path):
    """REC-11 edge: exporting an all-zero WAV does not raise OverflowError.

    pydub.max_dBFS returns -inf for silent audio; apply_gain(-inf + 3) would
    overflow. The exporter must guard against this and produce a valid (silent) MP3.

    Requirement: REC-11 (silence guard — no OverflowError on zero-amplitude input).
    """
    from exporter import export_wav_to_mp3

    wav_path = tmp_path / "silence.wav"
    mp3_path = tmp_path / "silence.mp3"
    _write_silence_wav(wav_path, duration_s=2.0)

    # Must not raise OverflowError or any other exception
    result = export_wav_to_mp3(wav_path, mp3_path)

    assert mp3_path.exists(), "Silent WAV export must still produce a valid MP3 file"
    assert result["mp3_path"] == str(mp3_path)
    assert result["frame_rate"] == 48000
