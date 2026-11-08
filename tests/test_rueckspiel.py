"""Rückspiel über den Dante-Ausgang: Duplex nur, wenn eingerichtet; der Callback füllt den
Ausgang aus dem Puffer, sonst Stille. sounddevice ist durchgehend gemockt — kein Test
öffnet ein echtes Gerät."""
import sys
import time
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, "audio_service")


@pytest.fixture
def cfg(monkeypatch):
    # Das config, das recorder wirklich liest — andere Tests laden config neu, und dann
    # zeigen sys.modules["config"] und recorder.config auf verschiedene Objekte.
    import recorder
    import rueckspiel
    c = recorder.config
    monkeypatch.setattr(rueckspiel, "config", c)
    monkeypatch.setattr(c, "record_channels", [1, 2])
    monkeypatch.setattr(c, "stream_channels", [3, 4])
    monkeypatch.setattr(c, "output_channels", [])
    monkeypatch.setattr(c, "device_names", ["Dante"])
    monkeypatch.setattr(c, "sample_rate", 48000)
    monkeypatch.setattr(c, "blocksize", 1024)
    return c


GERAETE = [{"name": "Dante Virtual Soundcard", "max_input_channels": 64, "max_output_channels": 64}]


def _oeffnen(cfg, geraete=GERAETE):
    """ensure_input() mit gemocktem sounddevice; gibt (Recorder, InputStream-, Stream-Mock)."""
    from recorder import ContinuousRecorder
    with patch("sounddevice.query_devices") as q, \
         patch("sounddevice.InputStream") as ein, \
         patch("sounddevice.Stream") as duplex:
        q.side_effect = lambda device=None: geraete if device is None else geraete[device]
        rec = ContinuousRecorder()
        assert rec.ensure_input()
    return rec, ein, duplex


def _ausgang(rec, frames=1024, kanaele=4) -> np.ndarray:
    indata = np.zeros((frames, 4), dtype=np.float32)
    outdata = np.full((frames, kanaele), 9.0, dtype=np.float32)   # Müll, muss überschrieben werden
    rec._duplex_callback(indata, outdata, frames, None, None)
    return outdata


def test_ohne_ausgangskanaele_bleibt_es_beim_eingang(cfg):
    rec, ein, duplex = _oeffnen(cfg)
    ein.assert_called_once()
    duplex.assert_not_called()
    assert not rec.rueckspiel_bereit()
    assert not rec.rueckspiel_einreihen(np.zeros((10, 2), dtype=np.float32))


def test_mit_ausgangskanaelen_oeffnet_einen_duplex_stream(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, ein, duplex = _oeffnen(cfg)
    ein.assert_not_called()
    kw = duplex.call_args.kwargs
    assert kw["channels"] == (4, 4) and kw["device"] == (0, 0)
    assert kw["callback"] == rec._duplex_callback
    assert rec.rueckspiel_bereit() and rec.rueckspiel_kanaele == 2


def test_geraet_ohne_ausgaenge_nimmt_trotzdem_auf(cfg, monkeypatch):
    """Lieber ohne Rückspiel aufnehmen als gar nicht."""
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, ein, duplex = _oeffnen(cfg, [{"name": "Dante", "max_input_channels": 64,
                                       "max_output_channels": 0}])
    ein.assert_called_once()
    duplex.assert_not_called()
    assert not rec.rueckspiel_bereit()


def test_callback_legt_den_puffer_in_die_konfigurierten_kanaele(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    pcm = np.stack([np.full(1500, 0.25), np.full(1500, -0.5)], axis=1).astype(np.float32)
    assert rec.rueckspiel_einreihen(pcm)

    erst = _ausgang(rec)
    assert np.all(erst[:, 0:2] == 0)                       # Kanäle 1 und 2 still
    assert np.all(erst[:, 2] == 0.25) and np.all(erst[:, 3] == -0.5)

    dann = _ausgang(rec)                                   # 476 Frames Rest, dann Stille
    assert np.all(dann[:476, 2] == 0.25) and np.all(dann[476:] == 0)

    assert np.all(_ausgang(rec) == 0)                      # leerer Puffer: Stille


def test_eingang_laeuft_im_duplex_weiter(cfg, monkeypatch):
    from recorder import _rec_q
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    rec._is_recording = True
    indata = np.full((1024, 4), 0.5, dtype=np.float32)
    rec._duplex_callback(indata, np.zeros((1024, 4), dtype=np.float32), 1024, None, None)
    try:
        assert rec.get_rms_level() == pytest.approx(0.5, abs=1e-3)
        assert len(_rec_q.get_nowait()) == 1024 * 2 * 2
    finally:
        rec._is_recording = False


def test_stoppen_macht_sofort_still(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    for _ in range(3):
        rec.rueckspiel_einreihen(np.full((1500, 2), 0.3, dtype=np.float32))
    _ausgang(rec)                                          # angefangener Block liegt im Rest
    rec.rueckspiel_stoppen()
    assert np.all(_ausgang(rec) == 0)
    assert np.all(_ausgang(rec) == 0)


def test_falsche_kanalzahl_wird_abgelehnt(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    with pytest.raises(ValueError):
        rec.rueckspiel_einreihen(np.zeros((10, 3), dtype=np.float32))


# ─── Zuführer: WAV-Abschnitt lesen ──────────────────────────────────────────

def _wav(pfad, sekunden=2.0, rate=48000, kanaele=2):
    n = int(sekunden * rate)
    # Kanal 1 zählt die Sekunde hoch, Kanal 2 ist sein Negativ — so sieht man, woher ein Block stammt
    werte = (np.arange(n) // rate + 1) * 1000
    daten = np.stack([werte, -werte], axis=1)[:, :kanaele].astype(np.int16)
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(kanaele)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(daten.tobytes())
    return pfad


def _sammeln(rec, frames_gesamt) -> np.ndarray:
    """Den Ausgang abholen, bis so viele Frames mit Ton da sind (oder Zeit um)."""
    gesehen = []
    ende = time.monotonic() + 5
    while sum(len(b) for b in gesehen) < frames_gesamt and time.monotonic() < ende:
        out = _ausgang(rec)
        ton = out[np.any(out != 0, axis=1)]
        if len(ton):
            gesehen.append(ton)
        else:
            time.sleep(0.005)
    return np.concatenate(gesehen) if gesehen else np.zeros((0, 4))


def test_abschnitt_wird_abgespielt(cfg, monkeypatch, tmp_path):
    from rueckspiel import Rueckspiel
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    spiel = Rueckspiel(rec, cfg.blocksize, cfg.sample_rate)
    wav = _wav(tmp_path / "aufnahme.wav", sekunden=2.0)

    antwort = spiel.starten(wav, 1.0, 1.5)
    assert antwort["status"] == "playing" and antwort["dauer_s"] == pytest.approx(0.5)
    ton = _sammeln(rec, 24000)
    assert len(ton) == 24000
    # Nur Sekunde 2 (Wert 2000), in den Kanälen 3 und 4, richtig skaliert
    assert np.allclose(ton[:, 2], 2000 / 32768) and np.allclose(ton[:, 3], -2000 / 32768)
    spiel.stoppen()
    assert not spiel.status()["laeuft"]


def test_mono_aufnahme_geht_auf_beide_ausgaenge(cfg, monkeypatch, tmp_path):
    from rueckspiel import Rueckspiel
    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    spiel = Rueckspiel(rec, cfg.blocksize, cfg.sample_rate)
    spiel.starten(_wav(tmp_path / "mono.wav", sekunden=0.5, kanaele=1), 0.0, 0.1)
    ton = _sammeln(rec, 4800)
    assert len(ton) == 4800 and np.allclose(ton[:, 2], ton[:, 3])
    spiel.stoppen()


def test_ablehnungen(cfg, monkeypatch, tmp_path):
    from rueckspiel import Rueckspiel, RueckspielFehler

    # Nicht eingerichtet
    rec, _, _ = _oeffnen(cfg)
    spiel = Rueckspiel(rec, cfg.blocksize, cfg.sample_rate)
    with pytest.raises(RueckspielFehler) as e:
        spiel.starten(_wav(tmp_path / "a.wav", 0.5), 0.0, 0.2)
    assert e.value.status == 409 and e.value.nachricht == "Rückspiel ist nicht eingerichtet"

    monkeypatch.setattr(cfg, "output_channels", [3, 4])
    rec, _, _ = _oeffnen(cfg)
    spiel = Rueckspiel(rec, cfg.blocksize, cfg.sample_rate)
    faelle = [
        (_wav(tmp_path / "b.wav", 0.5, rate=44100), 0.0, 0.2, 422,
         "Die Abtastrate der Aufnahme passt nicht zum Ausgang"),
        (tmp_path / "fehlt.wav", 0.0, 0.2, 404, "Aufnahme nicht gefunden"),
        (_wav(tmp_path / "c.wav", 0.5), 0.3, 0.1, 422, "Ungültiger Abschnitt"),
        (_wav(tmp_path / "d.wav", 0.5), 0.9, 1.2, 422, "Ungültiger Abschnitt"),   # hinter dem Ende
    ]
    for wav, von, bis, status, text in faelle:
        with pytest.raises(RueckspielFehler) as e:
            spiel.starten(wav, von, bis)
        assert (e.value.status, e.value.nachricht) == (status, text)

    # Ausgang eingerichtet, aber nicht offen (Gerät ohne Ausgänge)
    spiel = Rueckspiel(MagicMock(rueckspiel_bereit=lambda: False), 1024, 48000)
    with pytest.raises(RueckspielFehler) as e:
        spiel.starten(_wav(tmp_path / "e.wav", 0.5), 0.0, 0.2)
    assert (e.value.status, e.value.nachricht) == (409, "Der Ausgang ist nicht geöffnet")


def test_kanaele_anpassen():
    from rueckspiel import kanaele_anpassen
    stereo = np.array([[0.2, 0.4]], dtype=np.float32)
    assert kanaele_anpassen(stereo, 2).tolist() == [[pytest.approx(0.2), pytest.approx(0.4)]]
    assert kanaele_anpassen(stereo, 1).tolist() == [[pytest.approx(0.3)]]
    assert kanaele_anpassen(stereo, 4).shape == (1, 4)
