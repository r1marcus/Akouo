"""Alles einstellbar — und die Grenzen, die dabei gelten müssen.

Der wichtigste Teil hier sind die Schutzregeln. Eine Oberfläche, in der man "Predigt"
umbenennen kann, lässt die Erkennung still aufhören zu funktionieren: der Name steht in
den Ansage-Regeln und dient als Anker der Programmzuordnung. Umsortieren und ausblenden
muss gehen, umbenennen und löschen nicht.
"""
import os
import sys
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Discard-Port: die Lifespan schiebt die Erkennungskonfiguration zum Audio-Dienst.
# Ohne das würde ein Testlauf einen produktiv laufenden Dienst umkonfigurieren.
os.environ.setdefault("AUDIO_SERVICE_URL", "http://127.0.0.1:9")
sys.path.insert(0, "api")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import AnsageRegel, Base, Marker, MarkerType, RecordingSession, ServiceType


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_session():
    """Eigene Engine je Test.

    Nicht die aus database.py: die ist modulweit, und ein drop_all am Ende würde den
    anderen Tests im selben Prozess die Tabellen unter den Füßen wegziehen. Dasselbe
    Muster wie in test_segments_api.py.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        session.add_all([
            ServiceType(name="Sonntagmorgen", sort_order=10, is_primary=True),
            ServiceType(name="Hochzeit", sort_order=20, geschuetzt=True),
            MarkerType(name="Predigt", sort_order=10, klang_klasse="sprache",
                       anker=True, geschuetzt=True, dauer_min=260, dauer_max=2000),
            MarkerType(name="Chorlied", sort_order=20, klang_klasse="musik",
                       geschuetzt=True),
            MarkerType(name="Prosa", sort_order=30, klang_klasse="sprache"),
            AnsageRegel(muster=r"\bchorlied\b", marker_type="Chorlied", sort_order=10),
        ])
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from database import get_db
    from main import app

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ─── Lesen ──────────────────────────────────────────────────────────────────

async def test_alles_in_einem_aufruf(client):
    r = await client.get("/api/konfiguration")
    assert r.status_code == 200
    d = r.json()
    assert {"einstellungen", "felder", "gottesdienstarten", "beitragsarten",
            "ansage_regeln"} <= set(d)
    # Die Felderliste treibt die Oberfläche: eine neue Einstellung braucht keinen
    # neuen Formularcode.
    assert len(d["felder"]) >= 15
    assert any(f["schluessel"] == "min_stille_s" and f["einheit"] == " s"
               for f in d["felder"])
    assert d["einstellungen"]["min_stille_s"] == 8.0        # Standard


# ─── Zahlenwerte ────────────────────────────────────────────────────────────

async def test_einstellung_setzen_und_lesen(client):
    r = await client.put("/api/konfiguration/einstellung/min_stille_s",
                         json={"value": "6,5"})
    assert r.status_code == 200
    assert r.json()["wert"] == 6.5
    gelesen = (await client.get("/api/konfiguration")).json()
    assert gelesen["einstellungen"]["min_stille_s"] == 6.5


async def test_einstellung_ausserhalb_der_grenzen_wird_abgelehnt(client):
    r = await client.put("/api/konfiguration/einstellung/min_stille_s",
                         json={"value": "99"})
    assert r.status_code == 422
    assert "größer als" in r.json()["detail"]
    # Der alte Wert muss stehen bleiben
    gelesen = (await client.get("/api/konfiguration")).json()
    assert gelesen["einstellungen"]["min_stille_s"] == 8.0


async def test_unbekannte_einstellung(client):
    r = await client.put("/api/konfiguration/einstellung/gibtsnicht",
                         json={"value": "1"})
    assert r.status_code == 404


async def test_schalter_ohne_wert_heisst_aus(client):
    """Ein nicht angehakter Schalter sendet im Formular gar nichts mit."""
    r = await client.put("/api/konfiguration/einstellung/auto_refine_boundaries",
                         data={})
    assert r.status_code == 200
    assert r.json()["wert"] is False


# ─── Gottesdienstarten ──────────────────────────────────────────────────────

async def test_art_anlegen_und_umbenennen(client):
    r = await client.post("/api/konfiguration/gottesdienstarten",
                          json={"name": "Jugendstunde", "is_primary": True})
    assert r.status_code == 200
    art_id = r.json()["id"]

    r = await client.patch(f"/api/konfiguration/gottesdienstarten/{art_id}",
                           json={"name": "Jugendabend"})
    assert r.status_code == 200
    arten = (await client.get("/api/konfiguration")).json()["gottesdienstarten"]
    assert any(a["name"] == "Jugendabend" for a in arten)


async def test_doppelter_name_wird_abgelehnt(client):
    r = await client.post("/api/konfiguration/gottesdienstarten",
                          json={"name": "Sonntagmorgen"})
    assert r.status_code == 409


async def test_geschuetzte_art_nicht_umbenennbar(client):
    arten = (await client.get("/api/konfiguration")).json()["gottesdienstarten"]
    hochzeit = next(a for a in arten if a["name"] == "Hochzeit")
    r = await client.patch(f"/api/konfiguration/gottesdienstarten/{hochzeit['id']}",
                           json={"name": "Trauung"})
    assert r.status_code == 409
    assert "Logik" in r.json()["detail"]


async def test_geschuetzte_art_bleibt_ausblendbar(client):
    """Ausblenden muss gehen — sonst ist der Schutz eine Sackgasse."""
    arten = (await client.get("/api/konfiguration")).json()["gottesdienstarten"]
    hochzeit = next(a for a in arten if a["name"] == "Hochzeit")
    r = await client.patch(f"/api/konfiguration/gottesdienstarten/{hochzeit['id']}",
                           json={"active": False, "sort_order": 5})
    assert r.status_code == 200
    arten = (await client.get("/api/konfiguration")).json()["gottesdienstarten"]
    assert next(a for a in arten if a["name"] == "Hochzeit")["active"] is False


async def test_umbenennen_zieht_bestehende_aufnahmen_mit(client, db_session):
    """Aufnahmen verweisen per Name auf ihre Art. Bliebe der alte Name stehen, wäre
    die Aufnahme danach einer Art zugeordnet, die es nicht mehr gibt."""
    db_session.add(RecordingSession(started_at=datetime.now(), wav_path="/tmp/x.wav",
                                    service_type="Sonntagmorgen", status="stopped"))
    await db_session.commit()
    arten = (await client.get("/api/konfiguration")).json()["gottesdienstarten"]
    morgen = next(a for a in arten if a["name"] == "Sonntagmorgen")
    r = await client.patch(f"/api/konfiguration/gottesdienstarten/{morgen['id']}",
                           json={"name": "Sonntag vormittags"})
    assert r.status_code == 200
    sitzung = (await db_session.execute(select(RecordingSession))).scalars().first()
    assert sitzung.service_type == "Sonntag vormittags"


async def test_benutzte_art_nicht_loeschbar(client, db_session):
    db_session.add(RecordingSession(started_at=datetime.now(), wav_path="/tmp/y.wav",
                                    service_type="Sonntagmorgen", status="stopped"))
    await db_session.commit()
    arten = (await client.get("/api/konfiguration")).json()["gottesdienstarten"]
    morgen = next(a for a in arten if a["name"] == "Sonntagmorgen")
    r = await client.delete(f"/api/konfiguration/gottesdienstarten/{morgen['id']}")
    assert r.status_code == 409
    assert "Ausblenden" in r.json()["detail"]


# ─── Beitragsarten ──────────────────────────────────────────────────────────

async def test_eigene_beitragsart_nimmt_an_der_erkennung_teil(client):
    """Der eigentliche Gewinn: ein selbst angelegter Typ bekommt Klangklasse und
    Dauerfenster und wird von der Automatik berücksichtigt."""
    r = await client.post("/api/konfiguration/beitragsarten", json={
        "name": "Posaunenchor", "klang_klasse": "musik",
        "dauer_min": 90, "dauer_max": 240,
    })
    assert r.status_code == 200
    import typen
    assert "Posaunenchor" in typen.aktuell.erkennung["musik_typen"]
    assert typen.aktuell.erkennung["typische_dauer"]["Posaunenchor"] == [90, 240]


async def test_geschuetzte_beitragsart_nicht_umbenennbar(client):
    arten = (await client.get("/api/konfiguration")).json()["beitragsarten"]
    predigt = next(a for a in arten if a["name"] == "Predigt")
    r = await client.patch(f"/api/konfiguration/beitragsarten/{predigt['id']}",
                           json={"name": "Wortverkündigung"})
    assert r.status_code == 409
    assert "Erkennung" in r.json()["detail"]


async def test_freie_beitragsart_umbenennen_zieht_marker_und_regeln_mit(client, db_session):
    db_session.add(RecordingSession(id=1, started_at=datetime.now(), wav_path="/tmp/z.wav",
                                    service_type="Sonntagmorgen", status="stopped"))
    await db_session.flush()
    db_session.add(Marker(session_id=1, marker_type="Prosa", placed_at_ts=1.0,
                          placed_at_wall=datetime.now()))
    db_session.add(AnsageRegel(muster=r"\bprosa\b", marker_type="Prosa", sort_order=20))
    await db_session.commit()

    arten = (await client.get("/api/konfiguration")).json()["beitragsarten"]
    prosa = next(a for a in arten if a["name"] == "Prosa")
    r = await client.patch(f"/api/konfiguration/beitragsarten/{prosa['id']}",
                           json={"name": "Lesung"})
    assert r.status_code == 200

    m = (await db_session.execute(select(Marker))).scalars().first()
    assert m.marker_type == "Lesung"
    regeln = (await db_session.execute(select(AnsageRegel))).scalars().all()
    assert all(x.marker_type != "Prosa" for x in regeln)


async def test_klangklasse_wird_geprueft(client):
    r = await client.post("/api/konfiguration/beitragsarten",
                          json={"name": "Unsinn", "klang_klasse": "blech"})
    assert r.status_code == 422
    assert "musik" in r.json()["detail"]


async def test_leere_klangklasse_heisst_nur_auf_ansage(client):
    r = await client.post("/api/konfiguration/beitragsarten",
                          json={"name": "Solostück", "klang_klasse": ""})
    assert r.status_code == 200
    arten = (await client.get("/api/konfiguration")).json()["beitragsarten"]
    assert next(a for a in arten if a["name"] == "Solostück")["klang_klasse"] is None


async def test_dauerfenster_muss_sinnvoll_sein(client):
    r = await client.post("/api/konfiguration/beitragsarten",
                          json={"name": "Kaputt", "dauer_min": 500, "dauer_max": 100})
    assert r.status_code == 422
    assert "kleiner" in r.json()["detail"]


async def test_export_schalter_wirkt_auf_die_segmentliste(client):
    arten = (await client.get("/api/konfiguration")).json()["beitragsarten"]
    prosa = next(a for a in arten if a["name"] == "Prosa")
    await client.patch(f"/api/konfiguration/beitragsarten/{prosa['id']}",
                       json={"exportieren": False})
    import typen
    assert typen.exportieren("Prosa") is False
    assert typen.exportieren("Predigt") is True


# ─── Ansage-Regeln ──────────────────────────────────────────────────────────

async def test_regel_anlegen(client):
    r = await client.post("/api/konfiguration/ansage-regeln",
                          json={"muster": r"\bposaune\b", "marker_type": "Chorlied"})
    assert r.status_code == 200


async def test_kaputtes_muster_wird_beim_speichern_abgelehnt(client):
    r = await client.post("/api/konfiguration/ansage-regeln",
                          json={"muster": "[kaputt(", "marker_type": "Chorlied"})
    assert r.status_code == 422
    assert "regulärer Ausdruck" in r.json()["detail"]


async def test_regel_auf_unbekannte_beitragsart(client):
    r = await client.post("/api/konfiguration/ansage-regeln",
                          json={"muster": r"\bx\b", "marker_type": "Gibtsnicht"})
    assert r.status_code == 422


async def test_muster_vorher_ausprobieren(client):
    r = await client.post("/api/konfiguration/ansage-regeln/probe", json={
        "muster": r"\b(unser |der )?chor\s+singt\b",
        "text": "Und nun singt unser Chor singt das Lied",
    })
    d = r.json()
    assert d["gueltig"] is True
    # Die optionale Gruppe (unser |der )? nimmt das "unser" mit — genau das soll
    # die Probe zeigen, bevor jemand das Muster speichert.
    assert d["treffer"] == "unser Chor singt"

    r = await client.post("/api/konfiguration/ansage-regeln/probe",
                          json={"muster": "[kaputt(", "text": "egal"})
    assert r.json()["gueltig"] is False
    assert r.json()["fehler"]
