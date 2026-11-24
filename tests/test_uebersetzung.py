"""Übersetzer-Strecke: Ausgangsmischung im Duplex-Callback, Abgriff vom Stream-Bus und der
Lebenslauf der beiden Richtungen.

Kein Test öffnet ein Gerät oder das Netz. ffmpeg und srt-live-transmit werden durch kleine
Python-Skripte ersetzt, die über echte Pipes laufen: Das Zusammenstecken der Prozesse, das
Mitlesen von stderr und das Neuverbinden werden also wirklich ausgeführt.
"""
import importlib.util
import logging
import os
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, "audio_service")

GERAETE = [{"name": "Dante Virtual Soundcard", "max_input_channels": 64, "max_output_channels": 64}]


# ─── Ausgang: Rückspiel und Übersetzung in einem Duplex-Stream ─────────────

@pytest.fixture
def cfg(monkeypatch):
    import recorder
    c = recorder.config
    monkeypatch.setattr(c, "record_channels", [1, 2])
    monkeypatch.setattr(c, "stream_channels", [3, 4])
    monkeypatch.setattr(c, "output_channels", [])
    monkeypatch.setattr(c, "uebersetzung_kanaele", [])
    monkeypatch.setattr(c, "device_names", ["Dante"])
    monkeypatch.setattr(c, "sample_rate", 48000)
    monkeypatch.setattr(c, "blocksize", 1024)
    return c


def _oeffnen(geraete=GERAETE):
    from recorder import ContinuousRecorder
    with patch("sounddevice.query_devices") as q, \
         patch("sounddevice.InputStream") as ein, \
         patch("sounddevice.Stream") as duplex:
        q.side_effect = lambda device=None: geraete if device is None else geraete[device]
        rec = ContinuousRecorder()
        assert rec.ensure_input()
    return rec, ein, duplex


def _ausgang(rec, kanaele: int, frames: int = 1024) -> np.ndarray:
    indata = np.zeros((frames, 4), dtype=np.float32)
    outdata = np.full((frames, kanaele), 9.0, dtype=np.float32)   # Müll, muss weg
    rec._duplex_callback(indata, outdata, frames, None, None)
    return outdata


def _mono(wert: float, frames: int = 1024) -> np.ndarray:
    return np.full(frames, wert, dtype=np.float32)


def test_nur_uebersetzung_oeffnet_den_duplex(cfg):
    cfg.uebersetzung_kanaele = [63, 64]
    assert cfg.continuous_output_channels == 64
    rec, ein, duplex = _oeffnen()
    ein.assert_not_called()
    assert duplex.call_args.kwargs["channels"][1] == 64
    assert rec.uebersetzung_ausgang_bereit()
    # Der Duplex ist nur wegen der Übersetzung offen — Rückspiel bleibt aus
    assert not rec.rueckspiel_bereit()
    assert not rec.rueckspiel_einreihen(np.zeros((10, 2), dtype=np.float32))


def test_nur_rueckspiel_bleibt_wie_es_war(cfg):
    cfg.output_channels = [3, 4]
    rec, _, duplex = _oeffnen()
    assert duplex.call_args.kwargs["channels"][1] == 4
    assert rec.rueckspiel_bereit()
    assert not rec.uebersetzung_ausgang_bereit()
    assert not rec.uebersetzung_einreihen(_mono(0.5))
    rec.rueckspiel_einreihen(np.full((1024, 2), 0.25, dtype=np.float32))
    out = _ausgang(rec, 4)
    assert np.allclose(out[:, 2:], 0.25)
    assert np.allclose(out[:, :2], 0.0)


def test_ohne_beide_listen_nur_eingang(cfg):
    rec, ein, duplex = _oeffnen()
    ein.assert_called_once()
    duplex.assert_not_called()
    assert not rec.uebersetzung_ausgang_bereit()


def test_maximum_ueber_beide_listen(cfg):
    cfg.output_channels = [61, 62]
    cfg.uebersetzung_kanaele = [10]
    assert cfg.continuous_output_channels == 62
    cfg.uebersetzung_kanaele = [63, 64]
    assert cfg.continuous_output_channels == 64


def test_mono_auf_beide_uebersetzungskanaele(cfg):
    cfg.uebersetzung_kanaele = [63, 64]
    rec, _, _ = _oeffnen()
    rec.uebersetzung_einreihen(_mono(0.3))
    rec.uebersetzung_einreihen(_mono(0.3))
    out = _ausgang(rec, 64)
    assert np.allclose(out[:, 62:64], 0.3)
    assert np.allclose(out[:, :62], 0.0)


def test_beide_quellen_in_eigenen_kanaelen(cfg):
    cfg.output_channels = [1, 2]
    cfg.uebersetzung_kanaele = [3]
    rec, _, _ = _oeffnen()
    rec.rueckspiel_einreihen(np.full((1024, 2), 0.2, dtype=np.float32))
    rec.uebersetzung_einreihen(_mono(-0.4))
    rec.uebersetzung_einreihen(_mono(-0.4))
    out = _ausgang(rec, 3)
    assert np.allclose(out[:, :2], 0.2)
    assert np.allclose(out[:, 2], -0.4)


def test_ueberschneidung_wird_addiert_und_begrenzt(cfg):
    cfg.output_channels = [1, 2]
    cfg.uebersetzung_kanaele = [2]
    rec, _, _ = _oeffnen()
    rec.rueckspiel_einreihen(np.full((1024, 2), 0.7, dtype=np.float32))
    rec.uebersetzung_einreihen(_mono(0.6))
    rec.uebersetzung_einreihen(_mono(0.6))
    out = _ausgang(rec, 2)
    assert np.allclose(out[:, 0], 0.7)
    assert np.allclose(out[:, 1], 1.0)      # 1,3 → begrenzt


def test_ueberlauf_verwirft_die_aeltesten_bloecke(cfg):
    import recorder
    cfg.uebersetzung_kanaele = [1]
    rec, _, _ = _oeffnen()
    for i in range(20):
        assert rec.uebersetzung_einreihen(_mono(i / 100))
    assert rec._uebs_aus_q.qsize() == recorder.UEBERSETZUNG_AUSGANG_MAX_BLOECKE
    out = _ausgang(rec, 1)
    erster = 20 - recorder.UEBERSETZUNG_AUSGANG_MAX_BLOECKE
    assert np.allclose(out[:, 0], erster / 100)     # die Neuesten bleiben, keine Verzögerung


def test_vorlauf_nach_leerlauf(cfg):
    """Ein einzelner Block nach Leerlauf wird noch nicht gespielt — erst mit Vorlauf."""
    cfg.uebersetzung_kanaele = [1]
    rec, _, _ = _oeffnen()
    rec.uebersetzung_einreihen(_mono(0.5))
    assert np.allclose(_ausgang(rec, 1), 0.0)
    rec.uebersetzung_einreihen(_mono(0.5))
    assert np.allclose(_ausgang(rec, 1), 0.5)
    assert np.allclose(_ausgang(rec, 1), 0.5)
    # Jetzt leer: Stille, und wieder auf Vorlauf warten
    assert np.allclose(_ausgang(rec, 1), 0.0)
    rec.uebersetzung_einreihen(_mono(0.5))
    assert np.allclose(_ausgang(rec, 1), 0.0)


def test_rueckspiel_stopp_laesst_uebersetzung_weiterlaufen(cfg):
    cfg.output_channels = [1]
    cfg.uebersetzung_kanaele = [2]
    rec, _, _ = _oeffnen()
    rec.rueckspiel_einreihen(np.full((1024, 1), 0.2, dtype=np.float32))
    rec.uebersetzung_einreihen(_mono(0.4))
    rec.uebersetzung_einreihen(_mono(0.4))
    rec.rueckspiel_stoppen()
    out = _ausgang(rec, 2)
    assert np.allclose(out[:, 0], 0.0)
    assert np.allclose(out[:, 1], 0.4)


def test_geraet_ohne_genug_ausgaenge(cfg):
    cfg.uebersetzung_kanaele = [63, 64]
    klein = [{"name": "Dante Virtual Soundcard", "max_input_channels": 64, "max_output_channels": 2}]
    rec, ein, duplex = _oeffnen(klein)
    ein.assert_called_once()
    duplex.assert_not_called()
    assert not rec.uebersetzung_ausgang_bereit()


def test_kanaele_ohne_doppelte():
    import config as config_modul
    with patch.dict(os.environ, {"UEBERSETZUNG_KANAELE": "63, 64,63"}):
        assert config_modul.Config().uebersetzung_kanaele == [63, 64]


# ─── Abgriff vom Stream-Bus ────────────────────────────────────────────────

def _leeren(q):
    while not q.empty():
        q.get_nowait()


def test_callback_gibt_stream_paar_an_die_uebersetzung(cfg):
    import recorder
    from recorder import ContinuousRecorder
    rec = ContinuousRecorder()
    _leeren(recorder._uebs_q)
    indata = np.zeros((256, 4), dtype=np.float32)
    indata[:, 3] = 0.5
    recorder.uebersetzung_enabled.set()
    try:
        rec._audio_callback(indata, 256, None, None)
    finally:
        recorder.uebersetzung_enabled.clear()
    pcm = np.frombuffer(recorder._uebs_q.get_nowait(), dtype=np.int16).reshape(-1, 2)
    assert pcm[0, 0] == 0
    assert pcm[0, 1] == int(0.5 * 32767)


def test_callback_fuellt_uebersetzung_nur_bei_bedarf(cfg):
    import recorder
    from recorder import ContinuousRecorder
    _leeren(recorder._uebs_q)
    ContinuousRecorder()._audio_callback(np.zeros((64, 4), dtype=np.float32), 64, None, None)
    assert recorder._uebs_q.empty()


def test_volle_queue_bremst_den_callback_nicht(cfg):
    import recorder
    from recorder import ContinuousRecorder
    rec = ContinuousRecorder()
    recorder.uebersetzung_enabled.set()
    try:
        for _ in range(recorder.UEBERSETZUNG_EINGANG_MAX_BLOECKE + 10):
            rec._audio_callback(np.zeros((64, 4), dtype=np.float32), 64, None, None)
    finally:
        recorder.uebersetzung_enabled.clear()
    assert recorder._uebs_q.full()
    _leeren(recorder._uebs_q)


# ─── Befehle und Zugangsdaten ──────────────────────────────────────────────

PASSPHRASE = "pass/phrase+geheim"
MAC_PASS = "mac:pw&x"


@pytest.fixture
def ue(monkeypatch):
    import uebersetzung
    c = uebersetzung.config
    monkeypatch.setattr(c, "uebersetzung_server", "medien.example.org")
    monkeypatch.setattr(c, "uebersetzung_srt_port", 8890)
    monkeypatch.setattr(c, "uebersetzung_srt_passphrase", PASSPHRASE)
    monkeypatch.setattr(c, "uebersetzung_mac_user", "mac")
    monkeypatch.setattr(c, "uebersetzung_mac_pass", MAC_PASS)
    monkeypatch.setattr(c, "uebersetzung_kanaele", [63, 64])
    monkeypatch.setattr(c, "stream_channels", [3, 4])
    monkeypatch.setattr(c, "sample_rate", 48000)
    monkeypatch.setattr(c, "blocksize", 1024)
    return uebersetzung


def test_befehle_wie_im_durchstich(ue):
    ffmpeg, srt = ue.befehle_hin()
    assert ffmpeg[0] == "ffmpeg" and "libopus" in ffmpeg and ffmpeg[-1] == "pipe:1"
    assert ffmpeg[ffmpeg.index("-ac") + 1] == "2"        # Eingang: das Stream-Paar
    assert "mpegts" in ffmpeg
    assert "-chunk:1316" in srt and "-a:no" in srt and srt[-2] == "file://con"
    assert srt[-1].startswith("srt://medien.example.org:8890?")
    assert "streamid=publish%3Ade%3Amac%3A" in srt[-1]
    srt2, ffmpeg2 = ue.befehle_rueck()
    assert "streamid=read%3Aru%3Amac%3A" in srt2[-2] and srt2[-1] == "file://con"
    assert ffmpeg2[ffmpeg2.index("-ar") + 1] == "48000" and ffmpeg2[-2:] == ["s16le", "pipe:1"]


def test_ohne_nutzer_nur_modus_und_pfad(ue, monkeypatch):
    monkeypatch.setattr(ue.config, "uebersetzung_mac_user", "")
    assert "streamid=publish%3Ade&" in ue.srt_url("publish", "de")


def test_maskiere_entfernt_zugangsdaten_roh_und_kodiert(ue):
    url = ue.srt_url("read", "ru")
    text = ue.maskiere(f"Media path: '{url}' --> 'file://con' {PASSPHRASE} {MAC_PASS}")
    for geheim in (PASSPHRASE, MAC_PASS):
        assert geheim not in text
        assert ue.quote(geheim, safe="") not in text
    assert "medien.example.org" in text


# ─── Lebenslauf mit falschem ffmpeg und srt-live-transmit ──────────────────

FALSCHES_FFMPEG = """#!{py}
import os, signal, sys
if os.environ.get("FALSCH_STUR"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)     # beendet sich nicht freiwillig
while True:
    daten = os.read(0, 65536)
    if not daten:
        break
    os.write(1, daten)
"""

FALSCHES_SRT = """#!{py}
import os, select, sys, time
ordner = os.environ["FALSCH_ORDNER"]
lesen = sys.argv[-1] == "file://con"
with open(os.path.join(ordner, "starts.log"), "a") as f:
    f.write(("ru" if lesen else "de") + "\\n")
# Wie das echte: die ganze URL samt Zugangsdaten auf stderr
sys.stderr.write("Media path: " + " ".join(sys.argv[1:]) + "\\n")
sys.stderr.flush()
if lesen:
    if not os.path.exists(os.path.join(ordner, "ru_sendet")):
        time.sleep(0.05)
        sys.exit(0)                       # abgelehnt: niemand sendet auf „ru“
    sys.stderr.write("SRT source connected\\n"); sys.stderr.flush()
    block = (8000).to_bytes(2, "little", signed=True) * 1024
    ende = time.monotonic() + float(open(os.path.join(ordner, "ru_sendet")).read() or 30)
    while time.monotonic() < ende:
        os.write(1, block)
        time.sleep(0.02)
    sys.exit(0)
else:
    sys.stderr.write("SRT target connected\\n"); sys.stderr.flush()
    kurz = os.path.exists(os.path.join(ordner, "de_bricht_ab"))
    ende = time.monotonic() + 0.3
    with open(os.path.join(ordner, "de.bin"), "ab") as f:
        while True:
            if kurz and time.monotonic() > ende:
                sys.exit(1)
            if not select.select([0], [], [], 0.05)[0]:
                continue
            daten = os.read(0, 65536)
            if not daten:
                break
            f.write(daten); f.flush()
"""


class _Recorder:
    """Steht für ContinuousRecorder: nimmt Blöcke an, hat einen Ausgang."""

    def __init__(self, bereit=True):
        self.bloecke = []
        self.bereit = bereit

    def uebersetzung_ausgang_bereit(self):
        return self.bereit

    def uebersetzung_einreihen(self, block):
        self.bloecke.append(block)
        return self.bereit

    def uebersetzung_ausgang_leeren(self):
        pass


@pytest.fixture
def falsch(ue, tmp_path, monkeypatch):
    for name, inhalt in (("ffmpeg", FALSCHES_FFMPEG), ("srt-live-transmit", FALSCHES_SRT)):
        pfad = tmp_path / name
        pfad.write_text(inhalt.format(py=sys.executable))
        pfad.chmod(pfad.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FALSCH_ORDNER", str(tmp_path))
    monkeypatch.setattr(ue, "FFMPEG", str(tmp_path / "ffmpeg"))
    monkeypatch.setattr(ue.config, "srt_bin", str(tmp_path / "srt-live-transmit"))
    monkeypatch.setattr(ue, "NEUVERBINDEN_PAUSEN", (0.05,))
    monkeypatch.setattr(ue, "WARTEN_PAUSE", 0.1)
    monkeypatch.setattr(ue, "AUFPASSER_TAKT_S", 0.1)
    return tmp_path


def _warte(bedingung, sekunden=5.0) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return False


def _starts(ordner: Path, richtung: str) -> int:
    try:
        return (ordner / "starts.log").read_text().split().count(richtung)
    except FileNotFoundError:
        return 0


def test_ohne_server_nicht_eingerichtet(ue, monkeypatch):
    monkeypatch.setattr(ue.config, "uebersetzung_server", "")
    u = ue.Uebersetzung(_Recorder())
    assert u.status() == {"status": "nicht_eingerichtet", "configured": False}
    assert u.starten()["status"] == "nicht_eingerichtet"
    assert not u.aktiv()


def test_ohne_passphrase_nicht_eingerichtet(ue, monkeypatch):
    monkeypatch.setattr(ue.config, "uebersetzung_srt_passphrase", "")
    assert ue.Uebersetzung(_Recorder()).status()["status"] == "nicht_eingerichtet"


def test_fehlendes_ffmpeg_wird_gemeldet(ue, monkeypatch):
    monkeypatch.setattr(ue, "FFMPEG", "/gibt/es/nicht/ffmpeg")
    u = ue.Uebersetzung(_Recorder())
    st = u.starten()
    assert st["status"] == "fehler" and st["grund"] == "ffmpeg_fehlt"
    assert not u.aktiv()


def test_sendet_deutsch_und_stoppt_sauber(falsch, ue):
    import recorder
    u = ue.Uebersetzung(_Recorder())
    st = u.starten()
    try:
        assert st["aktiv"] and recorder.uebersetzung_enabled.is_set()
        assert _warte(lambda: u.status()["sendet_deutsch"])
        assert u.status()["status"] == "laeuft"
        for _ in range(5):
            recorder._uebs_q.put(b"\x01\x00" * 2048)
        assert _warte(lambda: (falsch / "de.bin").exists() and (falsch / "de.bin").stat().st_size >= 5 * 4096)
    finally:
        st = u.stoppen()
    assert st["status"] == "aus" and not st["aktiv"]
    assert not recorder.uebersetzung_enabled.is_set()
    assert u._lauf is None


def test_ohne_uebersetzer_wird_still_neu_verbunden(falsch, ue, caplog):
    rec = _Recorder()
    u = ue.Uebersetzung(rec)
    with caplog.at_level(logging.INFO, logger=ue.__name__):
        u.starten()
        try:
            assert _warte(lambda: _starts(falsch, "ru") >= 3)
            st = u.status()
            assert st["uebersetzer_verbunden"] is False and st["pegel_ru_db"] is None
        finally:
            u.stoppen()
    assert rec.bloecke == []
    # Warten auf den Übersetzer ist der Normalzustand: keine Zeile je Versuch
    assert not [r for r in caplog.records if "Russisch" in r.getMessage()]


def test_uebersetzer_kommt_und_geht(falsch, ue):
    rec = _Recorder()
    u = ue.Uebersetzung(rec)
    u.starten()
    try:
        assert _warte(lambda: _starts(falsch, "ru") >= 1)
        (falsch / "ru_sendet").write_text("0.6")
        assert _warte(lambda: u.status()["uebersetzer_verbunden"])
        st = u.status()
        assert st["pegel_ru_db"] == pytest.approx(20 * np.log10(8000 / 32768), abs=0.5)
        assert rec.bloecke and len(rec.bloecke[0]) == 1024
        assert np.allclose(rec.bloecke[0], 8000 / 32768)
        # Nach 0,6 s hört der Übersetzer auf; es wird von selbst wieder angeklopft
        (falsch / "ru_sendet").unlink()
        vorher = _starts(falsch, "ru")
        assert _warte(lambda: _starts(falsch, "ru") > vorher + 1)
        assert _warte(lambda: not u.status()["uebersetzer_verbunden"])
    finally:
        u.stoppen()


def test_abbruch_auf_de_wird_neu_verbunden(falsch, ue):
    (falsch / "de_bricht_ab").write_text("")
    u = ue.Uebersetzung(_Recorder())
    u.starten()
    try:
        assert _warte(lambda: _starts(falsch, "de") >= 3)
        assert u.aktiv()
    finally:
        u.stoppen()


def test_zugangsdaten_erscheinen_nicht_in_meldungen(falsch, ue):
    u = ue.Uebersetzung(_Recorder())
    u.starten()
    try:
        assert _warte(lambda: u._lauf.hin is not None and u._lauf.hin.meldungen)
        meldungen = " ".join(u._lauf.hin.meldungen)
    finally:
        u.stoppen()
    assert "Media path" in meldungen
    for geheim in (PASSPHRASE, MAC_PASS):
        assert geheim not in meldungen and ue.quote(geheim, safe="") not in meldungen


def test_ohne_dante_ausgang_wird_trotzdem_gesendet(falsch, ue, monkeypatch):
    monkeypatch.setattr(ue.config, "uebersetzung_kanaele", [])
    u = ue.Uebersetzung(_Recorder(bereit=False))
    u.starten()
    try:
        assert _warte(lambda: u.status()["sendet_deutsch"])
        st = u.status()
        assert st["dante_ausgabe"] is False and st["dante_grund"] == "keine_kanaele"
    finally:
        u.stoppen()


def _faden_der_strecke() -> list:
    import threading
    return [t for t in threading.enumerate() if t.is_alive()
            and t.name in ("uebersetzung-de", "uebersetzung-ru", "uebersetzung-aufpasser")]


def _prozesse(ordner: Path) -> str:
    import subprocess
    return subprocess.run(["pgrep", "-f", str(ordner)], capture_output=True, text=True).stdout.strip()


def test_paralleler_doppelstart_ergibt_genau_einen_lauf(falsch, ue):
    import threading
    u = ue.Uebersetzung(_Recorder())
    sperre = threading.Barrier(8)

    def drueck():
        sperre.wait()
        u.starten()
    faeden = [threading.Thread(target=drueck) for _ in range(8)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    try:
        assert len(_faden_der_strecke()) == 3
        assert _warte(lambda: _starts(falsch, "de") >= 1)
        time.sleep(0.3)
        assert _starts(falsch, "de") == 1          # nicht acht Sender auf „de“
    finally:
        u.stoppen()
    assert _faden_der_strecke() == []
    assert _warte(lambda: _prozesse(falsch) == "")


def test_start_und_stopp_durcheinander_hinterlassen_nichts(falsch, ue):
    import random
    import threading
    u = ue.Uebersetzung(_Recorder())
    zufall = random.Random(7)

    def wild():
        for _ in range(5):
            (u.starten if zufall.random() < 0.5 else u.stoppen)()
    faeden = [threading.Thread(target=wild) for _ in range(6)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    if u.aktiv():
        assert len(_faden_der_strecke()) == 3
    u.stoppen()
    assert not u.aktiv()
    assert _warte(lambda: _faden_der_strecke() == [])
    assert _warte(lambda: _prozesse(falsch) == "")


def test_stoppen_hat_eine_frist(falsch, ue, monkeypatch):
    monkeypatch.setenv("FALSCH_STUR", "1")
    u = ue.Uebersetzung(_Recorder())
    u.starten()
    assert _warte(lambda: u.status()["sendet_deutsch"])
    t0 = time.monotonic()
    u.stoppen(frist=1.0)
    assert time.monotonic() - t0 < 4.0
    assert _warte(lambda: _prozesse(falsch) == "")


class _GeschlosseneKette:
    class _Ausgang:
        def read(self, n):
            raise ValueError("read of closed file")
    ausgang = _Ausgang()


def test_lesen_nach_fremdem_schliessen_endet_ruhig(ue):
    u = ue.Uebersetzung(_Recorder())
    u._lesen(ue._Lauf(), _GeschlosseneKette())      # wirft nicht


def test_schleife_ueberlebt_unerwarteten_fehler(ue, monkeypatch, caplog):
    monkeypatch.setattr(ue, "NEUVERBINDEN_PAUSEN", (0.01,))
    aufrufe = []

    def schleife(lauf):
        aufrufe.append(1)
        if len(aufrufe) < 4:
            raise RuntimeError("kaputt")
    with caplog.at_level(logging.WARNING, logger=ue.__name__):
        ue._robust("test", schleife)(ue._Lauf())
    assert len(aufrufe) == 4
    assert len([r for r in caplog.records if "kaputt" in r.getMessage()]) == 1   # keine Flut


def test_verbunden_nicht_bei_disconnected(ue):
    assert ue.VERBUNDEN_MUSTER.search("SRT target connected")
    assert ue.VERBUNDEN_MUSTER.search("SRT source connected")
    assert not ue.VERBUNDEN_MUSTER.search("SRT target disconnected")
    assert not ue.VERBUNDEN_MUSTER.search("not connected")


def test_kurzer_nutzername_zerhackt_keine_meldung(ue):
    assert ue.maskiere("macht nichts, connected") == "macht nichts, connected"


# ─── Duplex scheitert: Aufnahme hat Vorrang ────────────────────────────────

def test_duplex_scheitert_faellt_auf_eingang_zurueck(cfg):
    import sounddevice as sd
    cfg.uebersetzung_kanaele = [63, 64]
    from recorder import ContinuousRecorder
    with patch("sounddevice.query_devices") as q, \
         patch("sounddevice.InputStream") as ein, \
         patch("sounddevice.Stream", side_effect=sd.PortAudioError("kaputt")) as duplex:
        q.side_effect = lambda device=None: GERAETE if device is None else GERAETE[device]
        rec = ContinuousRecorder()
        assert rec.ensure_input()
        ein.assert_called_once()
        assert not rec.uebersetzung_ausgang_bereit()
        assert rec.ausgang_fehler() == "duplex_fehlgeschlagen"
        # Eingang verloren und wieder geöffnet: kein neuer Duplex-Versuch
        rec.close_input()
        assert rec.ensure_input()
        assert duplex.call_count == 1
        assert ein.call_count == 2


def test_duplex_start_scheitert_schliesst_und_faellt_zurueck(cfg):
    from unittest.mock import MagicMock
    cfg.output_channels = [61, 62]
    from recorder import ContinuousRecorder
    kaputt = MagicMock()
    kaputt.start.side_effect = OSError("start")
    with patch("sounddevice.query_devices") as q, \
         patch("sounddevice.InputStream") as ein, \
         patch("sounddevice.Stream", return_value=kaputt):
        q.side_effect = lambda device=None: GERAETE if device is None else GERAETE[device]
        rec = ContinuousRecorder()
        assert rec.ensure_input()
    kaputt.close.assert_called_once()
    ein.assert_called_once()
    assert not rec.rueckspiel_bereit()


def test_leeren_verwirft_auch_den_angefangenen_block(cfg):
    cfg.uebersetzung_kanaele = [1]
    rec, _, _ = _oeffnen()
    rec.uebersetzung_einreihen(_mono(0.5))
    rec.uebersetzung_einreihen(_mono(0.5))
    assert np.allclose(_ausgang(rec, 1, frames=512), 0.5)    # halber Block bleibt liegen
    rec.uebersetzung_ausgang_leeren()
    assert np.allclose(_ausgang(rec, 1, frames=512), 0.0)
    assert rec._uebs_rest is None


# ─── Einstellungen ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("roh, erwartet", [
    ("63,64", [63, 64]), ("63;64", [63, 64]), ("63 64", [63, 64]), (" 63 , 64 ", [63, 64]),
    ("", []), ("63,x", []), ("0", []), ("63,,64", [63, 64]),
])
def test_kanaele_tolerant(roh, erwartet, caplog):
    import config as config_modul
    with patch.dict(os.environ, {"UEBERSETZUNG_KANAELE": roh}):
        assert config_modul.Config().uebersetzung_kanaele == erwartet


def test_port_tolerant():
    import config as config_modul
    with patch.dict(os.environ, {"UEBERSETZUNG_SRT_PORT": "acht"}):
        assert config_modul.Config().uebersetzung_srt_port == 8890
    with patch.dict(os.environ, {"UEBERSETZUNG_SRT_PORT": "9000"}):
        assert config_modul.Config().uebersetzung_srt_port == 9000


def test_kanaele_auf_der_einstellungsseite():
    import dienst
    assert dienst.pruefe({"UEBERSETZUNG_KANAELE": "63,64"}) == []
    assert dienst.pruefe({"UEBERSETZUNG_KANAELE": ""}) == []
    assert dienst.pruefe({"UEBERSETZUNG_KANAELE": "63,x"})


def test_beenden_sichert_erst_die_aufnahme():
    """Beim Entladen zuerst den WAV-Kopf schreiben, dann die Übersetzung stoppen."""
    quelle = (Path(__file__).resolve().parent.parent / "audio_service" / "main.py").read_text()
    lebenslauf = quelle[quelle.index("async def lifespan"):quelle.index("app = FastAPI(")]
    assert lebenslauf.index("emergency_close()") < lebenslauf.index("uebersetzung.stoppen")
    assert lebenslauf.index("close_input()") < lebenslauf.index("uebersetzung.stoppen")
    assert "wait_for" in lebenslauf


# ─── Endpunkte des Audio-Dienstes ──────────────────────────────────────────

@pytest.fixture(scope="module")
def audio_main():
    """audio_service/main.py über den Dateipfad laden — api/main.py heißt auch main."""
    if "audio_service_main" in sys.modules:
        return sys.modules["audio_service_main"]
    pfad = Path(__file__).resolve().parent.parent / "audio_service" / "main.py"
    spec = importlib.util.spec_from_file_location("audio_service_main", pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["audio_service_main"] = modul
    spec.loader.exec_module(modul)
    return modul


class _FalscheStrecke:
    def __init__(self, eingerichtet=True, start=None):
        self._eingerichtet = eingerichtet
        self._start = start or {"status": "verbindet", "aktiv": True}
        self.gestoppt = False

    def eingerichtet(self):
        return self._eingerichtet

    def starten(self):
        return self._start

    def stoppen(self):
        self.gestoppt = True
        return {"status": "aus", "aktiv": False}

    def status(self):
        return {"status": "laeuft", "sendet_deutsch": True}


def _client(audio_main):
    from fastapi.testclient import TestClient
    return TestClient(audio_main.app)     # ohne „with“: keine Lifespan, kein Gerät


def test_endpunkt_start_ohne_einrichtung_409(audio_main, monkeypatch):
    monkeypatch.setattr(audio_main, "uebersetzung", _FalscheStrecke(eingerichtet=False))
    r = _client(audio_main).post("/api/uebersetzung/start")
    assert r.status_code == 409
    assert r.json()["detail"] == "Übersetzung ist nicht eingerichtet"


def test_endpunkt_start_meldet_fehlendes_programm(audio_main, monkeypatch):
    strecke = _FalscheStrecke(start={"status": "fehler", "grund": "srt_fehlt"})
    monkeypatch.setattr(audio_main, "uebersetzung", strecke)
    r = _client(audio_main).post("/api/uebersetzung/start")
    assert r.status_code == 409
    assert r.json()["detail"] == "srt-live-transmit ist nicht installiert"


def test_endpunkte_start_status_stop(audio_main, monkeypatch):
    strecke = _FalscheStrecke()
    monkeypatch.setattr(audio_main, "uebersetzung", strecke)
    c = _client(audio_main)
    assert c.post("/api/uebersetzung/start").json()["status"] == "verbindet"
    assert c.get("/api/uebersetzung/status").json()["sendet_deutsch"] is True
    assert c.post("/api/uebersetzung/stop").json()["status"] == "aus"
    assert strecke.gestoppt


# ─── Duplex ohne Callbacks (Prüfrunde 2) ───────────────────────────────────

def _monitor_einmal(rec):
    """Einen Durchgang von monitor_input() ausführen, danach abbrechen."""
    import asyncio
    import recorder

    async def lauf():
        aufgabe = asyncio.create_task(rec.monitor_input())
        await asyncio.sleep(0.05)
        aufgabe.cancel()
    with patch.object(recorder.sd, "_terminate"), patch.object(recorder.sd, "_initialize"):
        asyncio.run(lauf())


def _verloren(rec):
    import recorder
    rec._last_callback_at = time.monotonic() - recorder.INPUT_LOST_SECONDS - 1


def test_duplex_ohne_jeden_callback_wird_gesperrt(cfg):
    cfg.uebersetzung_kanaele = [63, 64]
    rec, _, _ = _oeffnen()
    assert rec._duplex
    _verloren(rec)
    with patch("sounddevice.query_devices") as q, \
         patch("sounddevice.InputStream") as ein, \
         patch("sounddevice.Stream") as duplex:
        q.side_effect = lambda device=None: GERAETE if device is None else GERAETE[device]
        _monitor_einmal(rec)
        assert rec.ausgang_fehler() == "keine_callbacks"
        duplex.assert_not_called()          # neuer Versuch: reiner Eingang
        ein.assert_called_once()
    assert not rec._duplex and rec._stream is not None


def test_abgezogenes_geraet_sperrt_den_ausgang_nicht(cfg):
    cfg.uebersetzung_kanaele = [63, 64]
    rec, _, _ = _oeffnen()
    _ausgang(rec, 64)                       # er hat geliefert
    _verloren(rec)
    with patch("sounddevice.query_devices") as q, \
         patch("sounddevice.InputStream"), \
         patch("sounddevice.Stream") as duplex:
        q.side_effect = lambda device=None: GERAETE if device is None else GERAETE[device]
        _monitor_einmal(rec)
        assert rec.ausgang_fehler() is None
        duplex.assert_called_once()         # Duplex wird wieder geöffnet
    assert rec._duplex


def test_einstellungsseite_kanaele_1_bis_512():
    import dienst
    for gut in ("1", "63,64", "512", "99,100,499,500,509,510"):
        assert dienst.pruefe({"UEBERSETZUNG_KANAELE": gut}) == [], gut
    for schlecht in ("0", "513", "999", "63,0"):
        assert dienst.pruefe({"UEBERSETZUNG_KANAELE": schlecht}), schlecht
