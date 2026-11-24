"""Übersetzung in der API: Weiterreichen an den Audio-Dienst, übersetzte Fehler und die
Statuskarte im Dashboard. Der Audio-Dienst ist durch ein gemocktes httpx ersetzt."""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUDIO_SERVICE_URL", "http://127.0.0.1:9")
sys.path.insert(0, "api")

import httpx  # noqa: E402
import pytest  # noqa: E402

LAEUFT = {
    "status": "laeuft", "configured": True, "aktiv": True, "grund": None,
    "sendet_deutsch": True, "uebersetzer_verbunden": True, "pegel_ru_db": -18.2,
    "dante_ausgabe": True, "dante_grund": None, "kanaele": [63, 64], "laeuft_seit_s": 42,
}


def _antwort(status: int, daten: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = daten
    return r


def _httpx(get=None, post=None, fehler=None):
    client = AsyncMock()
    if fehler:
        client.get.side_effect = fehler
        client.post.side_effect = fehler
    else:
        client.get.return_value = get
        client.post.return_value = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return patch("httpx.AsyncClient", return_value=client), client


@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)      # ohne Lifespan — sie spräche den Audio-Dienst an


def test_status_als_json(app_client):
    p, client = _httpx(get=_antwort(200, LAEUFT))
    with p:
        r = app_client.get("/api/uebersetzung/status")
    assert r.status_code == 200 and r.json()["sendet_deutsch"] is True
    assert client.get.call_args.args[0].endswith("/api/uebersetzung/status")


def test_status_als_karte(app_client):
    p, _ = _httpx(get=_antwort(200, LAEUFT))
    with p:
        r = app_client.get("/api/uebersetzung/status", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Sendet Deutsch" in r.text and "Übersetzer verbunden" in r.text
    assert "63+64" in r.text
    assert "uebersetzung = true" in r.text


def test_karte_auf_russisch(app_client):
    p, _ = _httpx(get=_antwort(200, LAEUFT))
    with p:
        r = app_client.get("/api/uebersetzung/status", headers={"HX-Request": "true"},
                           cookies={"akouo_sprache": "ru"})
    assert "Переводчик подключён" in r.text


def test_karte_ohne_dante_ausgang(app_client):
    daten = {**LAEUFT, "dante_ausgabe": False, "dante_grund": "keine_kanaele",
             "uebersetzer_verbunden": False, "pegel_ru_db": None}
    p, _ = _httpx(get=_antwort(200, daten))
    with p:
        r = app_client.get("/api/uebersetzung/status", headers={"HX-Request": "true"})
    assert "Russisch wird nicht auf Dante ausgegeben" in r.text
    assert "UEBERSETZUNG_KANAELE ist nicht gesetzt" in r.text


def test_karte_nicht_eingerichtet(app_client):
    p, _ = _httpx(get=_antwort(200, {"status": "nicht_eingerichtet", "configured": False}))
    with p:
        r = app_client.get("/api/uebersetzung/status", headers={"HX-Request": "true"})
    assert "Nicht eingerichtet" in r.text
    assert "uebersetzungBereit = false" in r.text


def test_audio_dienst_weg(app_client):
    p, _ = _httpx(fehler=httpx.ConnectError("weg"))
    with p:
        r = app_client.get("/api/uebersetzung/status", headers={"HX-Request": "true"})
        assert r.status_code == 200 and "Audio-Dienst nicht erreichbar" in r.text
        r = app_client.post("/api/uebersetzung/start")
    assert r.status_code == 503


def test_start_reicht_weiter_und_rendert(app_client):
    p, client = _httpx(post=_antwort(200, {**LAEUFT, "status": "verbindet", "sendet_deutsch": False}))
    with p:
        r = app_client.post("/api/uebersetzung/start", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert client.post.call_args.args[0].endswith("/api/uebersetzung/start")
    assert "Verbindet…" in r.text


def test_start_fehler_wird_uebersetzt(app_client):
    p, _ = _httpx(post=_antwort(409, {"detail": "srt-live-transmit ist nicht installiert"}))
    with p:
        r = app_client.post("/api/uebersetzung/start", cookies={"akouo_sprache": "en"})
    assert r.status_code == 409
    assert r.json()["detail"] == "srt-live-transmit is not installed"


def test_stop_reicht_weiter(app_client):
    p, client = _httpx(post=_antwort(200, {"status": "aus", "configured": True, "aktiv": False}))
    with p:
        r = app_client.post("/api/uebersetzung/stop")
    assert r.status_code == 200 and r.json()["status"] == "aus"
    assert client.post.call_args.args[0].endswith("/api/uebersetzung/stop")


def test_meldungen_des_audio_dienstes_stehen_im_katalog():
    """Der Audio-Dienst schickt feste deutsche Sätze, die API übersetzt sie per
    uebersetze(detail) — das sieht der Katalogtest für api/ nicht."""
    import json
    import re
    from pathlib import Path
    wurzel = Path(__file__).resolve().parent.parent
    quelle = (wurzel / "audio_service" / "uebersetzung.py").read_text()
    saetze = re.findall(r'^MELDUNG_\w+ = "([^"]+)"', quelle, flags=re.M)
    assert len(saetze) >= 3
    for code in ("de", "en", "ru"):
        katalog = json.loads((wurzel / "api" / "sprachen" / f"{code}.json").read_text())
        fehlend = [s for s in saetze if not katalog.get(s)]
        assert not fehlend, f"{code}: {fehlend}"


def test_karte_meldet_fehlenden_dante_ausgang(app_client):
    daten = {**LAEUFT, "dante_ausgabe": False, "dante_grund": "ausgang_fehlt",
             "ausgang_fehler": "duplex_fehlgeschlagen"}
    p, _ = _httpx(get=_antwort(200, daten))
    with p:
        r = app_client.get("/api/uebersetzung/status", headers={"HX-Request": "true"})
    assert "Dante-Ausgang nicht verfügbar" in r.text


def test_neustart_sperre_kennt_die_uebersetzung():
    from routers import dienste
    assert dienste.LAEUFT["uebersetzung"] == "Übersetzung"
