"""Chorbildschirm: das Programm des Dirigenten lesen, speichern und an die Automatik geben.

Hier das Zerlegen der Programmzeilen (api/chor_programm.py) und die API. Was die Automatik
daraus macht, steht in tests/test_chor_vorgabe.py — eigene Datei, weil der Audio-Dienst
seine eigene Umgebung hat.
Die Beispielzeilen sind nachgebaut — keine echten Programme, keine echten Namen.
"""
import os
import sys
from unittest.mock import patch

import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
for _ordner in ("api",):
    _pfad = os.path.join(os.path.dirname(__file__), "..", _ordner)
    if _pfad not in sys.path:
        sys.path.insert(0, _pfad)

import chor_programm  # noqa: E402

ARTEN = ["Chorlied", "Kinderchor", "Gemeindegesang", "Kinderlied", "Gruppenlied", "Predigt",
         "Gedicht", "Orchester", "Musikstück"]


# ─── Programmzeilen ──────────────────────────────────────────────────────────

def test_kurztext_aus_den_klammern():
    p = chor_programm.punkt_lesen("Kinderchor: Ein Lied   (Grün 14 | Dirigent A)", "(", ")", ARTEN)
    assert p == {"kurz": "Grün 14 | Dirigent A",
                 "voll": "Kinderchor: Ein Lied   (Grün 14 | Dirigent A)",
                 "typ": "Kinderchor", "titel": "Ein Lied"}


def test_mehrere_klammern_werden_verbunden_und_der_titel_endet_davor():
    p = chor_programm.punkt_lesen("Chor: Ein Lied (mit Begleitung) (Grün 48)", "(", ")", ARTEN)
    assert p["kurz"] == "mit Begleitung, Grün 48"
    assert p["titel"] == "Ein Lied"
    assert p["typ"] == "Chorlied"          # „Chor“ ist der Sprachgebrauch für Chorlied


def test_leere_klammern_blenden_aus_und_ohne_klammern_bleibt_alles():
    assert chor_programm.punkt_lesen("Sonntag Morgengottesdienst  []", "[", "]", ARTEN) is None
    p = chor_programm.punkt_lesen("Begrüßung", "(", ")", ARTEN)
    assert (p["kurz"], p["typ"], p["titel"]) == ("Begrüßung", None, None)


def test_sprachgebrauch_der_programme():
    lies = lambda z: chor_programm.punkt_lesen(z, "(", ")", ARTEN)["typ"]  # noqa: E731
    assert lies("Allgemeines Lied") == "Gemeindegesang"
    assert lies("Allgemeines Kinderlied") == "Kinderlied"
    assert lies("Blasorchester: Ein Stück") == "Orchester"
    assert lies("Predigt") == "Predigt"
    assert lies("Abschluss") is None


def test_eine_unbekannte_art_bleibt_reine_anzeige():
    """Was es als Beitragsart nicht gibt, wird nicht geraten."""
    p = chor_programm.punkt_lesen("Männerchor: Ein Lied", "(", ")", ["Predigt"])
    assert p["typ"] is None


def test_umgebrochene_klammer_wird_wieder_eine_zeile():
    roh = ["Kinderchor:  Ein Lied        (", "Ordner 73 | Dirigent B) ", " ", "Predigt "]
    punkte = chor_programm.programm_lesen(roh, "( )", ARTEN)
    assert [p["kurz"] for p in punkte] == ["Ordner 73 | Dirigent B", "Predigt"]


def test_eckige_klammern_als_einstellung():
    roh = ["Kopfzeile []", "Gruppenlied: Ein Lied   [Grün 54] Dirigent C"]
    punkte = chor_programm.programm_lesen(roh, "[ ]", ARTEN)
    assert [(p["kurz"], p["typ"], p["titel"]) for p in punkte] == [
        ("Grün 54", "Gruppenlied", "Ein Lied")]


def test_erwartungen_nur_mit_art_und_mit_stelle():
    punkte = chor_programm.programm_lesen(
        ["Begrüßung", "Chor: Eins (A)", "Predigt", "Abschluss"], "( )", ARTEN)
    assert chor_programm.erwartungen(punkte) == [
        {"typ": "Chorlied", "titel": "Eins", "index": 1},
        {"typ": "Predigt", "titel": None, "index": 2},
    ]


def test_eine_kaputte_pdf_wirft_valueerror():
    with pytest.raises(ValueError):
        chor_programm.zeilen_aus_pdf(b"keine pdf")


# ─── API ─────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fabrik = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with fabrik() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient
    from database import get_db
    from main import app

    async def override():
        yield db_session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


ROH = ["Kopfzeile ()", "Chorlied: Eins   (Grün 14 | Dirigent A)", "Begrüßung", "Predigt"]


def test_seite_kommt_mit_den_einstellungen(client):
    r = client.get("/anzeige")
    assert r.status_code == 200
    assert '"f8": "ZeigeProgramm"' in r.text
    assert "chorbildschirm()" in r.text


def test_programm_hochladen_lesen_und_auflisten(client):
    with patch("chor_programm.zeilen_aus_pdf", return_value=ROH):
        r = client.post("/api/anzeige/programme",
                        files={"datei": ("Kinderchor 23.02.pdf", b"%PDF-", "application/pdf")})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == "Kinderchor 23.02"
    assert [x["kurz"] for x in p["punkte"]] == ["Grün 14 | Dirigent A", "Begrüßung", "Predigt"]
    assert client.get("/api/anzeige/programme").json()[0]["id"] == p["id"]
    assert client.get(f"/api/anzeige/programme/{p['id']}").json()["punkte"] == p["punkte"]
    assert client.get("/api/anzeige/programme/999").status_code == 404


def test_pdf_ohne_text_wird_abgelehnt(client):
    with patch("chor_programm.zeilen_aus_pdf", return_value=[" ", ""]):
        r = client.post("/api/anzeige/programme", files={"datei": ("scan.pdf", b"%PDF-", "application/pdf")})
    assert r.status_code == 422


def test_zeiger_geht_mit_dem_programm_an_den_audio_dienst(client):
    import httpx
    from routers import anzeige
    with patch("chor_programm.zeilen_aus_pdf", return_value=ROH):
        pid = client.post("/api/anzeige/programme",
                          files={"datei": ("p.pdf", b"%PDF-", "application/pdf")}).json()["id"]
    gesendet = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            gesendet.append((url, json))
            return httpx.Response(200, json={})

    with patch.object(anzeige.httpx, "AsyncClient", _Client):
        assert client.post("/api/anzeige/zeiger", json={"programm_id": pid, "index": 2}).json() == {"automatik": True}
    url, nutzlast = gesendet[0]
    assert url.endswith("/api/session/programm-vorgabe")
    assert nutzlast == {"kennung": f"chor-{pid}", "zeiger": 2, "eintraege": [
        {"typ": "Chorlied", "titel": "Eins", "index": 0},
        {"typ": "Predigt", "titel": None, "index": 2}]}


def test_zeiger_ohne_audio_dienst_stoert_die_anzeige_nicht(client):
    import httpx
    from routers import anzeige
    with patch("chor_programm.zeilen_aus_pdf", return_value=ROH):
        pid = client.post("/api/anzeige/programme",
                          files={"datei": ("p.pdf", b"%PDF-", "application/pdf")}).json()["id"]

    class _Weg:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError("weg")

        async def __aexit__(self, *a):
            return False

    with patch.object(anzeige.httpx, "AsyncClient", _Weg):
        r = client.post("/api/anzeige/zeiger", json={"programm_id": pid, "index": 1})
    assert (r.status_code, r.json()) == (200, {"automatik": False})


@pytest.mark.asyncio
async def test_verlauf_wird_gespeichert(client, db_session):
    from sqlalchemy import select
    from models import AnzeigeVerlauf
    client.post("/api/anzeige/verlauf", json={"modus": "Chor", "text": "SDH 245"})
    client.post("/api/anzeige/verlauf", json={"modus": "Chor", "text": "   "})
    eintraege = (await db_session.execute(select(AnzeigeVerlauf))).scalars().all()
    assert [(e.modus, e.text) for e in eintraege] == [("Chor", "SDH 245")]
