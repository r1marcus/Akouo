"""Schnittkanten-Automatik: findet den echten Beginn/Schluss eines Tracks."""
import pathlib
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, "audio_service")


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


def _write_flicker_wav(path: pathlib.Path, rate=48000):
    """Session 37 nachgestellt: stumm, dann ein leises Intro, das in 100-ms-Schritten
    zwischen −53 und −40 dBFS springt, dann volles Singen."""
    def ton(seconds, dbfs):
        n = int(seconds * rate)
        return np.sin(2 * np.pi * 220 * np.arange(n) / rate) * 10 ** (dbfs / 20) * 32767

    teile = [np.zeros(int(10 * rate))]
    for i in range(60):                       # 10,0–16,0 s: Intro, 70/30 laut/leise
        teile.append(ton(0.1, -53 if i % 10 in (3, 7, 9) else -40))
    teile.append(ton(14, -20))                # 16–30 s: voll
    mono = np.concatenate(teile).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.repeat(mono, 2).tobytes())


def test_flackerndes_intro_zaehlt_als_anfang(tmp_path):
    """Knopf erst beim vollen Einsatz (16 s, real 6 s nach dem Intro) — der Anfang wird
    trotzdem auf das Intro bei 10 s gezogen, obwohl es nicht lückenlos über der Schwelle
    liegt."""
    from boundaries import refine
    wav = tmp_path / "t.wav"
    _write_flicker_wav(wav)

    result = refine(wav, 19.0, 30.0)

    assert result["start_seconds"] == pytest.approx(10.0, abs=0.5)


def test_rueckwaerts_weiter_als_vorwaerts(tmp_path):
    """Zehn Sekunden zu spät gedrückt liegt jenseits des Suchfensters nach vorn, aber
    innerhalb des weiteren nach hinten."""
    from boundaries import refine
    wav = tmp_path / "t.wav"
    _write_wav(wav, [(10, 0), (30, 0.3), (6, 0)])    # Ton: 10,0 bis 40,0 s

    # Ende knapp dahinter, damit das Fenster genug Stille für den Grundpegel sieht
    assert refine(wav, 19.0, 21.0)["start_seconds"] == pytest.approx(10.0, abs=0.5)
    # vorwärts bleibt es bei acht Sekunden: zehn zu früh wird nicht gefunden
    _write_wav(wav, [(20, 0), (20, 0.3), (6, 0)])    # Ton: 20,0 bis 40,0 s
    assert refine(wav, 10.0, 40.0)["start_delta"] == 0.0
