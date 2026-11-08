"""Anfang eines Beitrags: zwei Schwellen statt einer.

Nachgestellt ist Session 37 (8. November, Sonntagabend): ein Chorlied beginnt mit einem
leisen Intro nach stummem Kanal, die Stille-Schwelle schlägt erst acht Sekunden später an.
Gearbeitet wird auf gestellten Hüllkurven, ohne Aufnahme.
"""
import sys

import pytest

sys.path.insert(0, "audio_service")

import automode  # noqa: E402
import classifier  # noqa: E402

B = 0.25          # Blocklänge der Hüllkurve, wie classifier.BLOCK_MS


def _strecke(sekunden: float, *werte: int) -> list:
    """`sekunden` lang die Werte im Wechsel — so flattert ein leises Intro."""
    n = int(round(sekunden / B))
    return [werte[i % len(werte)] for i in range(n)]


def _session_37() -> list:
    """Raum, ein erstes Lied, stummer Kanal, leises Intro, volles Singen, Ende."""
    return (_strecke(100, -40)               # Raum vor dem Gottesdienst
            + _strecke(535, -20)             # 100–635 s: erstes Lied
            + _strecke(42.5, -60)            # 635–677,5 s: Kanal stumm
            + _strecke(5.75, -44, -38)       # 677,5–683,25 s: Intro, unter der Schwelle
            + _strecke(3.75, -33, -31)       # 683,25–687 s: hier schlägt sie an
            + _strecke(60, -25)              # 687–747 s: voll gesungen
            + _strecke(12, -60))             # danach stumm


def _modus(pegel: list) -> automode.AutoModus:
    m = automode.AutoModus(recorder=None)
    m._pegel = list(pegel)
    m._block_s = B
    return m


def test_ohne_vorziehen_laege_der_anfang_im_lied(monkeypatch):
    """Gegenprobe: die alte, einfache Schwelle trifft erst 683,25 s."""
    monkeypatch.setattr(classifier, "VORLAUF_MAX_S", 0.0)
    grenzen = _modus(_session_37())._grenzen_aus_pegel()
    assert grenzen[1][0] == pytest.approx(683.25)


def test_live_anfang_an_den_leisen_einsatz():
    grenzen = _modus(_session_37())._grenzen_aus_pegel()
    assert len(grenzen) == 2
    assert grenzen[0] == (100.0, 635.0)
    assert grenzen[1][0] == pytest.approx(677.5)
    assert grenzen[1][1] == pytest.approx(747.0)


def test_nachlauf_findet_denselben_anfang(tmp_path, monkeypatch):
    pegel = _session_37()
    monkeypatch.setattr(classifier, "envelope", lambda *a, **k: (pegel, 0.0, B))
    monkeypatch.setattr(classifier, "_klassenspur", lambda *a: [])
    grenzen = classifier.finde_beitraege(tmp_path / "egal.wav")
    assert grenzen[1][0] == pytest.approx(677.5)


def test_digitale_stille_ist_harte_grenze():
    """Ein einzelner Block stumm, und davor wäre wieder Ton — da singt niemand durch."""
    pegel = [-58] * 120 + [-45] * 20 + [-60] + [-45] * 20 + [-30] * 40
    assert classifier.start_vorziehen(pegel, 161, 0, B) == 141


def test_nie_in_den_beitrag_davor():
    pegel = [-20] * 100 + [-58] * 60 + [-45] * 30 + [-30] * 40
    assert classifier.start_vorziehen(pegel, 190, 170, B) == 170


def test_hoechstens_fuenfzehn_sekunden():
    pegel = [-58] * 300 + [-45] * 80 + [-30] * 40
    neu = classifier.start_vorziehen(pegel, 380, 0, B)
    assert neu == 380 - int(classifier.VORLAUF_MAX_S / B)


def test_raum_rauschen_zieht_nicht_vor():
    """Liegt davor nur der Raum, bleibt der Anfang, wo er ist: das Rauschen setzt den
    örtlichen Grundpegel, und nichts davor steht 6 dB darüber."""
    pegel = [-48, -50, -47, -49] * 50 + [-25] * 40
    assert classifier.start_vorziehen(pegel, 200, 0, B) == 200


def test_abgeschlossener_beitrag_bleibt_grenze():
    """Live: ein fertiger Beitrag davor ist fest, auch wenn der Pegel ihn heute anders
    schneiden würde — sonst überlappte der neue ihn und würde mit ihm verwechselt."""
    m = _modus(_session_37())
    m.beitraege = [automode.Beitrag(nr=1, start_s=100.0, ende_s=680.0)]
    grenzen = m._grenzen_aus_pegel()
    assert grenzen[1][0] == pytest.approx(680.0)


def test_offener_beitrag_wandert_nach_vorn(monkeypatch):
    """Erst die alte Schwelle, dann die neue: der laufende Beitrag wird korrigiert, nicht
    verdoppelt."""
    m = _modus(_session_37())
    monkeypatch.setattr(m, "_einschaetzen", lambda b, bis=None: None)
    monkeypatch.setattr(m, "_ansage_auswerten", lambda b: None)
    m._grenzen_uebernehmen([(100.0, 635.0), (683.25, None)])
    m._grenzen_uebernehmen(m._grenzen_aus_pegel())
    assert [(b.start_s, b.ende_s) for b in m.beitraege] == [(100.0, 635.0), (677.5, 747.0)]
