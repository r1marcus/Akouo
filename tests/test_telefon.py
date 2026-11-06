"""Telefon-Übertragung — Stream-Mix per baresip in die Telefonkonferenz.

baresip selbst läuft hier nicht. Ein kleiner Server spielt seine Steuerschnittstelle
(ctrl_tcp) nach: Er meldet die Anmeldung, nimmt den Anruf an und merkt sich die PIN.
"""
import asyncio
import json
import queue
import sys

import numpy as np
import pytest

sys.path.insert(0, "audio-service")

import telefon  # noqa: E402
from telefon import (  # noqa: E402
    TelefonAdapter, baresip_konfiguration, baresip_konto, fuelle_ausgabe, lies_netstrings, netstring,
)


@pytest.fixture
def eingerichtet(monkeypatch):
    c = telefon.config   # das Objekt, das telefon.py tatsächlich liest
    monkeypatch.setattr(c, "telefon_sip_konto", "1234@kunde.example")
    monkeypatch.setattr(c, "telefon_sip_passwort", "geheim")
    monkeypatch.setattr(c, "telefon_konferenz", "0800123")
    monkeypatch.setattr(c, "telefon_pin", "4711#")
    monkeypatch.setattr(c, "telefon_pin_verzoegerung", 0.0)
    monkeypatch.setattr(telefon, "ZIFFER_ABSTAND", 0.0)
    monkeypatch.setattr(telefon, "NEUWAHL_PAUSEN", (0,))
    return c


# ─── Ton ────────────────────────────────────────────────────────────────────

def _quelle(*bloecke):
    q = queue.Queue()
    for b in bloecke:
        q.put(b)
    return q.get_nowait


def test_ausgabe_fuellt_luecken_mit_stille():
    block, rest = fuelle_ausgabe(b"", _quelle(b"\x01" * 8), bedarf=16, maximum=64)
    assert block == b"\x01" * 8 + b"\x00" * 8
    assert rest == b""


def test_ausgabe_verwirft_ueberschuss_statt_verzoegerung_aufzubauen():
    # 40 Bytes angestaut, erlaubt sind 16: das Älteste fällt weg, das Neueste bleibt.
    angestaut = bytes(range(40))
    block, rest = fuelle_ausgabe(angestaut, _quelle(), bedarf=8, maximum=16)
    assert block == bytes(range(24, 32))
    assert rest == bytes(range(32, 40))


def test_ausgabe_schneidet_nur_auf_ganze_frames():
    angestaut = bytes(range(42))   # 42 ist kein Vielfaches von 4
    block, rest = fuelle_ausgabe(angestaut, _quelle(), bedarf=4, maximum=17)
    assert (len(block) + len(rest)) % 2 == 0
    assert len(block) == 4


def test_callback_gibt_stream_paar_an_das_telefon(monkeypatch):
    import recorder
    from recorder import ContinuousRecorder, _tel_q, telefon_enabled
    # recorder.config, nicht ein frisch importiertes: andere Tests laden Module neu
    monkeypatch.setattr(recorder.config, "record_channels", [1, 2])
    monkeypatch.setattr(recorder.config, "stream_channels", [3, 4])
    rec = ContinuousRecorder()
    indata = np.zeros((256, 4), dtype=np.float32)
    indata[:, 2] = 0.5
    telefon_enabled.set()
    try:
        rec._audio_callback(indata, 256, None, None)
    finally:
        telefon_enabled.clear()
    pcm = np.frombuffer(_tel_q.get_nowait(), dtype=np.int16).reshape(-1, 2)
    assert pcm[0, 0] == int(0.5 * 32767)
    assert pcm[0, 1] == 0


def test_callback_fuellt_telefon_queue_nur_bei_bedarf():
    from recorder import ContinuousRecorder, _tel_q
    rec = ContinuousRecorder()
    rec._audio_callback(np.zeros((64, 2), dtype=np.float32), 64, None, None)
    assert _tel_q.empty()


# ─── baresip-Konfiguration ─────────────────────────────────────────────────

def test_konto_ergaenzt_sip_und_haengt_zusatz_an(eingerichtet, monkeypatch):
    monkeypatch.setattr(eingerichtet, "telefon_sip_zusatz", ";transport=tls")
    zeile = baresip_konto()
    assert zeile.startswith("<sip:1234@kunde.example>;auth_pass=geheim;")
    assert zeile.rstrip().endswith("transport=tls")
    assert ";;" not in zeile


def test_konferenz_wird_nie_zurueck_in_die_quelle_gespielt(eingerichtet):
    cfg = baresip_konfiguration()
    assert "audio_source\t\tcoreaudio,BlackHole 2ch" in cfg
    assert "audio_player\t\taufile,/dev/null" in cfg
    assert "ctrl_tcp_listen\t\t127.0.0.1:4444" in cfg


def test_netstrings_auch_stueckweise_und_mit_muell():
    roh = netstring({"a": 1}) + b"xx," + netstring({"b": 2})
    teil1, teil2 = roh[:5], roh[5:]
    nachrichten, rest = lies_netstrings(teil1)
    assert nachrichten == []
    nachrichten, rest = lies_netstrings(rest + teil2)
    assert nachrichten == [{"a": 1}, {"b": 2}]
    assert rest == b""


# ─── Ablauf ────────────────────────────────────────────────────────────────

def test_ohne_zugangsdaten_bleibt_das_telefon_aus(monkeypatch):
    monkeypatch.setattr(telefon.config, "telefon_sip_konto", "")
    adapter = TelefonAdapter()
    assert adapter.status() == {"status": "nicht_eingerichtet", "configured": False}
    assert adapter.starte() is False


class _FalschesBaresip:
    """Spielt ctrl_tcp nach. `trenne_nach_pin` legt nach der ersten Einwahl einmal auf."""

    def __init__(self, trenne_nach_pin: bool = False):
        self.befehle = []
        self.waehlversuche = 0
        self.trenne_nach_pin = trenne_nach_pin
        self.verbunden = asyncio.Event()
        self.zweite_einwahl = asyncio.Event()

    async def _paar(self):
        server = await asyncio.start_server(self._bediene, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        self._server = server
        return await asyncio.open_connection("127.0.0.1", port)

    @staticmethod
    def _ereignis(schreiber, typ):
        schreiber.write(netstring({"event": True, "class": "call", "type": typ}))

    async def _bediene(self, leser, schreiber):
        self._ereignis(schreiber, "REGISTER_OK")
        puffer = b""
        while True:
            daten = await leser.read(4096)
            if not daten:
                return
            nachrichten, puffer = lies_netstrings(puffer + daten)
            for n in nachrichten:
                self.befehle.append((n["command"], n["params"]))
                schreiber.write(netstring({"response": True, "ok": True, "token": n["token"]}))
                if n["command"] == "dial":
                    self.waehlversuche += 1
                    if self.waehlversuche == 2:
                        self.zweite_einwahl.set()
                    self._ereignis(schreiber, "CALL_ESTABLISHED")
                if n["command"] == "sndcode" and n["params"] == "#":
                    self.verbunden.set()
                    if self.trenne_nach_pin and self.waehlversuche == 1:
                        self._ereignis(schreiber, "CALL_CLOSED")
            await schreiber.drain()


def _adapter_mit(falsch: _FalschesBaresip, monkeypatch) -> TelefonAdapter:
    adapter = TelefonAdapter()
    monkeypatch.setattr(adapter._ausgabe, "oeffne", lambda: True)
    monkeypatch.setattr(adapter._ausgabe, "schliesse", lambda: None)
    monkeypatch.setattr(telefon.shutil, "which", lambda _: "/usr/bin/true")

    async def starte_baresip():
        return await falsch._paar()

    monkeypatch.setattr(adapter, "_starte_baresip", starte_baresip)
    return adapter


async def _warte(bedingung, sekunden=3.0):
    ablauf = asyncio.get_running_loop().time() + sekunden
    while not bedingung():
        assert asyncio.get_running_loop().time() < ablauf, "Zeit abgelaufen"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_waehlt_konferenz_an_und_gibt_pin_ein(eingerichtet, monkeypatch):
    falsch = _FalschesBaresip()
    adapter = _adapter_mit(falsch, monkeypatch)
    assert adapter.starte() is True
    await asyncio.wait_for(falsch.verbunden.wait(), 3)
    await _warte(lambda: adapter.status()["status"] == "verbunden")

    assert ("dial", "0800123") in falsch.befehle
    pin = "".join(p for k, p in falsch.befehle if k == "sndcode")
    assert pin == "4711#"
    assert adapter.status()["aktiv"] is True

    assert await adapter.stoppe() is True
    assert adapter.status()["status"] == "aus"
    await _warte(lambda: ("hangup", "") in falsch.befehle)   # kommt beim Auflegen hinterher


@pytest.mark.asyncio
async def test_waehlt_nach_abbruch_von_selbst_neu(eingerichtet, monkeypatch):
    falsch = _FalschesBaresip(trenne_nach_pin=True)
    adapter = _adapter_mit(falsch, monkeypatch)
    adapter.starte()
    await asyncio.wait_for(falsch.zweite_einwahl.wait(), 3)
    await _warte(lambda: adapter.status()["status"] == "verbunden")
    await adapter.stoppe()


@pytest.mark.asyncio
async def test_fehlende_soundkarte_wird_gemeldet(eingerichtet, monkeypatch):
    adapter = TelefonAdapter()
    monkeypatch.setattr(telefon.shutil, "which", lambda _: "/usr/bin/true")
    monkeypatch.setattr(adapter._ausgabe, "oeffne", lambda: False)
    assert adapter.starte() is False
    assert adapter.status()["status"] == "fehler"
    assert adapter.status()["grund"] == "ausgang_fehlt"
