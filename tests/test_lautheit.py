"""Lautheit statt Spitze: zwei verschieden laute Segmente kommen gleich laut heraus.

Echte FFmpeg-Läufe — gemessen wird mit demselben loudnorm-Filter, der normalisiert.
"""
import json
import subprocess
import sys

import numpy as np
from pydub import AudioSegment

sys.path.insert(0, "audio-service")

import laufzeit  # noqa: E402
from exporter import export_wav_to_mp3  # noqa: E402


def _ton(pfad, pegel: float, sekunden: float = 8.0, rate: int = 48000):
    t = np.arange(int(sekunden * rate)) / rate
    # Sprachähnlich: Ton mit langsamer Hüllkurve, damit loudnorm etwas zu messen hat
    welle = pegel * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t))
    daten = (np.stack([welle, welle], axis=1) * 32767).astype(np.int16)
    AudioSegment(daten.tobytes(), frame_rate=rate, sample_width=2, channels=2).export(str(pfad), format="wav")


def _lufs(pfad) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(pfad), "-af",
                        "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])["input_i"])


def test_leise_und_laut_klingen_danach_gleich(tmp_path):
    for name, pegel in (("leise", 0.02), ("laut", 0.5)):
        _ton(tmp_path / f"{name}.wav", pegel)
        export_wav_to_mp3(tmp_path / f"{name}.wav", tmp_path / f"{name}.mp3", fade_ms=200, lufs=-16.0)
    leise, laut = _lufs(tmp_path / "leise.mp3"), _lufs(tmp_path / "laut.mp3")
    assert abs(leise - laut) < 1.0
    assert abs(leise + 16.0) < 1.5


def test_ohne_lautheit_bleibt_es_bei_der_spitze(tmp_path):
    _ton(tmp_path / "leise.wav", 0.02)
    export_wav_to_mp3(tmp_path / "leise.wav", tmp_path / "leise.mp3", fade_ms=200)
    assert AudioSegment.from_mp3(str(tmp_path / "leise.mp3")).max_dBFS > -4.0


def test_umschalten_ueber_die_einstellungen(monkeypatch):
    monkeypatch.setitem(laufzeit.EXPORT, "normalisierung", "Spitzenpegel")
    assert laufzeit.ziel_lufs() is None
    laufzeit.export_setzen({"export_normalisierung": "Lautheit", "export_lufs": -18.0})
    assert laufzeit.ziel_lufs() == -18.0
    laufzeit.export_setzen({"export_normalisierung": "Spitzenpegel"})
    assert laufzeit.ziel_lufs() is None


def test_gesamtaufnahme_als_mp3_neben_der_wav(tmp_path):
    from exporter import gesamt_mp3
    wav = tmp_path / "aufnahme.wav"
    _ton(wav, 0.3, sekunden=3)
    mp3 = gesamt_mp3(wav)
    assert mp3 == tmp_path / "aufnahme.mp3" and mp3.stat().st_size > 10_000
    assert wav.exists()                                   # die WAV bleibt
    assert not (tmp_path / "aufnahme.mp3.neu").exists()
