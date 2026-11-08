"""Stereo-Korrelation als Stimme für Sprache: Rednerpult mittig, Chor verteilt.

Gestellte Aufnahmen in temporären Verzeichnissen; nie ~/Recordings.
"""
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, "audio_service")

import classifier  # noqa: E402

RATE = 16000


def _wav(pfad, links, rechts=None):
    kanaele = 1 if rechts is None else 2
    daten = links if rechts is None else np.column_stack([links, rechts]).ravel()
    with wave.open(str(pfad), "wb") as wf:
        wf.setnchannels(kanaele)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes((np.clip(daten, -1, 1) * 32767).astype(np.int16).tobytes())


def _rauschen(sekunden, seed):
    return np.random.default_rng(seed).normal(0, 0.1, int(sekunden * RATE))


def test_mittig_ist_korrelation_eins(tmp_path):
    s = _rauschen(3, 1)
    _wav(tmp_path / "a.wav", s, s)
    _, _, k = classifier._lade(tmp_path / "a.wav", 0, 3)
    assert k == pytest.approx(1.0, abs=1e-3)


def test_verteilt_ist_korrelation_niedrig(tmp_path):
    gemeinsam = _rauschen(3, 1)
    _wav(tmp_path / "a.wav", gemeinsam + _rauschen(3, 2), gemeinsam + _rauschen(3, 3))
    _, _, k = classifier._lade(tmp_path / "a.wav", 0, 3)
    assert 0.3 < k < 0.7          # wie ein Chorlied in Session 36


def test_mono_und_stummer_kanal_sagen_nichts(tmp_path):
    _wav(tmp_path / "mono.wav", _rauschen(3, 1))
    assert classifier._lade(tmp_path / "mono.wav", 0, 3)[2] is None
    _wav(tmp_path / "stumm.wav", _rauschen(3, 1), np.zeros(3 * RATE))
    assert classifier._lade(tmp_path / "stumm.wav", 0, 3)[2] is None


def _merkmale(stimmen: int, korrelation):
    """Merkmale, von denen genau `stimmen` für Sprache sprechen."""
    namen = ["flachheit", "schwerpunkt", "zcr_streuung", "leise_anteil", "fluss_streuung"]
    f = {n: classifier.SCHWELLEN[n] * (2 if i < stimmen else 0.5) for i, n in enumerate(namen)}
    f["korrelation"] = korrelation
    return f


@pytest.mark.parametrize("klang, korrelation, erwartet", [
    (2, 0.999, 3),     # Grenzfall, Rednerpult: kippt zu Sprache
    (3, 0.60, 2),      # Grenzfall, Chor verteilt: kippt zu Musik
    (3, 0.85, 3),      # dazwischen (Kinderlied über ein Mikrofon): keine Stimme
    (3, None, 3),      # Mono: keine Stimme
    (0, 0.999, 1),     # klare Musik bleibt Musik — kein Veto
    (5, 0.50, 4),      # klare Sprache bleibt Sprache
    (5, 0.999, 5),     # Skala bleibt 0..5
    (0, 0.10, 0),
])
def test_stereo_ist_eine_stimme_kein_veto(klang, korrelation, erwartet):
    assert classifier._stimmen_fuer_sprache(_merkmale(klang, korrelation)) == erwartet


def test_alte_merkmale_ohne_korrelation_gehen_weiter():
    f = _merkmale(3, None)
    del f["korrelation"]
    assert classifier._stimmen_fuer_sprache(f) == 3
