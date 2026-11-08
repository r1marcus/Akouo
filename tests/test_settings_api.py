"""Einstellungen: Schalter für die Schnittkanten-Automatik, Speichern per JSON und Formular."""
import os
import sys

import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    from models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def api_client(db_session):
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()


def test_accepts_json_and_form_data(api_client):
    """Die Seite schickt Formulardaten (HTMX), Skripte schicken JSON — beides muss gehen."""
    assert api_client.put("/api/settings/demo", json={"value": "a"}).status_code == 200
    assert api_client.get("/api/settings/demo").json()["value"] == "a"

    assert api_client.put("/api/settings/demo", data={"value": "b"}).status_code == 200
    assert api_client.get("/api/settings/demo").json()["value"] == "b"


@pytest.mark.asyncio
async def test_auto_refine_defaults_to_on_and_can_be_switched(api_client, db_session):
    from routers.settings import AUTO_REFINE_KEY, get_flag

    assert await get_flag(db_session, AUTO_REFINE_KEY, default=True) is True, "ohne Eintrag: an"

    api_client.put(f"/api/settings/{AUTO_REFINE_KEY}", json={"value": "0"})
    assert await get_flag(db_session, AUTO_REFINE_KEY, default=True) is False

    api_client.put(f"/api/settings/{AUTO_REFINE_KEY}", json={"value": "1"})
    assert await get_flag(db_session, AUTO_REFINE_KEY, default=True) is True


def test_settings_page_shows_the_switch(api_client):
    """Die Einstellungsseite bietet den Schnittkanten-Schalter an.

    Seit die Seite sich selbst aus /api/konfiguration füllt, steht die Beschriftung
    nicht mehr im ausgelieferten HTML, sondern in der Felderliste. Geprüft wird
    deshalb beides: dass die Seite lädt und dass das Feld angeboten wird.
    """
    assert api_client.get("/settings").status_code == 200

    felder = api_client.get("/api/konfiguration").json()["felder"]
    schalter = next(f for f in felder if f["schluessel"] == "auto_refine_boundaries")
    assert schalter["art"] == "bool"
    assert "Schnittkanten" in schalter["name"]


def test_deutsche_adresse_leitet_auf_die_kanonische(api_client):
    """/einstellungen ist bequem zu tippen, /settings ist die Adresse, auf die
    Navigation und ältere Lesezeichen zeigen."""
    antwort = api_client.get("/einstellungen", follow_redirects=False)
    assert antwort.status_code == 307
    assert antwort.headers["location"] == "/settings"


@pytest.mark.asyncio
async def test_altes_lautheitsziel_gilt_fuer_beide_klassen(db_session):
    """Wer früher „Ziellautheit“ gesetzt hatte, behält den Wert nach dem Aufteilen."""
    import einstellungen
    from models import Setting

    werte = await einstellungen.alle_lesen(db_session)
    assert (werte["export_lufs_sprache"], werte["export_lufs_musik"]) == (-16.0, -14.0)

    db_session.add(Setting(key="export_lufs", value="-18"))
    await db_session.commit()
    werte = await einstellungen.alle_lesen(db_session)
    assert (werte["export_lufs_sprache"], werte["export_lufs_musik"]) == (-18.0, -18.0)

    # Ein neu gespeichertes Ziel geht dem alten vor — nur für seine Klasse
    await einstellungen.setzen(db_session, "export_lufs_musik", "-13")
    werte = await einstellungen.alle_lesen(db_session)
    assert (werte["export_lufs_sprache"], werte["export_lufs_musik"]) == (-18.0, -13.0)
    geschoben = einstellungen.zahlen_fuer_audio(werte)
    assert geschoben["export_lufs_sprache"] == -18.0 and geschoben["export_lufs_musik"] == -13.0
