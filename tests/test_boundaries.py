"""Schnittkanten-Automatik: findet den echten Beginn/Schluss eines Tracks."""
import pathlib
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, "audio-service")


def _write_wav(path: pathlib.Path, blocks, rate=48000):
    """blocks: Liste aus (Sekunden, Amplitude 0..1)."""
    parts = []
    for seconds, amplitude in blocks:
        n = int(seconds * rate)
        if amplitude <= 0:
            parts.append(np.zeros(n, dtype=np.int16))
        else:
            tone = np.sin(2 * np.pi * 220 * np.arange(n) / rate) * amplitude * 32767
            parts.append(tone.astype(np.int16))
    mono = np.concatenate(parts)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.repeat(mono, 2).tobytes())


def test_late_start_and_early_stop_are_corrected(tmp_path):
    """Knopf 4 s zu spät und 4 s zu früh gedrückt — beides wird auf den echten Ton gezogen."""
    from boundaries import refine
    wav = tmp_path / "t.wav"
    _write_wav(wav, [(6, 0), (20, 0.3), (6, 0)])   # echter Ton: 6.0 bis 26.0 s

    result = refine(wav, 10.0, 22.0)

    assert result["start_seconds"] == pytest.approx(6.0, abs=0.5)
    assert result["end_seconds"] == pytest.approx(26.0, abs=0.6)
    assert result["start_delta"] < -3      # nach vorne gezogen
    assert result["end_delta"] > 3         # nach hinten verlängert


def test_continuous_sound_is_left_alone(tmp_path):
    """Ohne echte Stille davor/danach darf nichts verschoben werden (durchgehende Rede)."""
    from boundaries import refine
    wav = tmp_path / "t.wav"
    _write_wav(wav, [(40, 0.3)])

    result = refine(wav, 10.0, 30.0)

    assert result["start_delta"] == 0.0
    assert result["end_delta"] == 0.0


def test_short_pauses_do_not_count_as_silence(tmp_path):
    """Satzpausen von 0,5 s sind keine Trackgrenze."""
    from boundaries import refine
    wav = tmp_path / "t.wav"
    _write_wav(wav, [(10, 0.3), (0.5, 0), (10, 0.3), (0.5, 0), (10, 0.3)])

    result = refine(wav, 12.0, 25.0)

    assert result["start_delta"] == 0.0
    assert result["end_delta"] == 0.0
