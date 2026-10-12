"""Header service selection — GET/PUT /api/service/current.

The operator picks the current Gottesdienst once in the header; recording start uses it
without asking. Selection is server-side so every device shows the same value.
"""
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


def _mock_audio_service_start():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={
        "wav_path": "/tmp/test.wav",
        "session_started_at": "2026-09-15T10:00:00",
        "status": "recording",
    })
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def test_nothing_selected_by_default(api_client):
    resp = api_client.get("/api/service/current")
    assert resp.status_code == 200
    # thema (freier Titel) und gewaehlt_am (Zeitpunkt der Auswahl) kamen im Oktober dazu
    assert resp.json() == {"service_type": None, "couple_name": None, "wedding_part": None,
                           "thema": None, "gewaehlt_am": None, "locked": False}


def test_selection_persists_and_drops_wedding_fields(api_client):
    resp = api_client.put("/api/service/current", json={
        "service_type": "Sonntagmorgen", "couple_name": "ignored", "wedding_part": 2,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["couple_name"] is None and resp.json()["wedding_part"] is None

    current = api_client.get("/api/service/current").json()
    assert current["service_type"] == "Sonntagmorgen"
    assert current["locked"] is False


def test_wedding_keeps_couple_and_part(api_client):
    resp = api_client.put("/api/service/current", json={
        "service_type": "Hochzeit", "couple_name": "  Max & Maria  ", "wedding_part": 2,
    })
    assert resp.status_code == 200, resp.text
    current = api_client.get("/api/service/current").json()
    assert current["couple_name"] == "Max & Maria"
    assert current["wedding_part"] == 2


def test_rejects_unknown_type_and_invalid_part(api_client):
    assert api_client.put("/api/service/current", json={"service_type": "Karneval"}).status_code == 422
    assert api_client.put("/api/service/current", json={
        "service_type": "Hochzeit", "wedding_part": 4,
    }).status_code == 422


@pytest.mark.asyncio
async def test_locked_while_recording(api_client, db_session):
    from models import RecordingSession
    db_session.add(RecordingSession(
        started_at=datetime.now(), wav_path="/tmp/x.wav", service_type="Sonntagmorgen", status="recording",
    ))
    await db_session.commit()

    assert api_client.get("/api/service/current").json()["locked"] is True
    resp = api_client.put("/api/service/current", json={"service_type": "Sonntagabend"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_mapped_checklist_activated_once(api_client, db_session):
    """Selecting activates the checklist; reselecting or starting the recording must not wipe the ticks."""
    from models import ChecklistInstance, ChecklistTemplate, ServiceTypeTemplate
    from sqlalchemy import select

    tmpl = ChecklistTemplate(name="Gottesdienst")
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    db_session.add(ServiceTypeTemplate(service_type="Sonntagmorgen", track_sequence="[]", checklist_template_id=tmpl.id))
    await db_session.commit()

    assert api_client.put("/api/service/current", json={"service_type": "Sonntagmorgen"}).status_code == 200
    assert api_client.put("/api/service/current", json={"service_type": "Sonntagmorgen"}).status_code == 200
    with patch("routers.sessions.httpx.AsyncClient", return_value=_mock_audio_service_start()):
        assert api_client.post("/api/sessions/start", json={"service_type": "Sonntagmorgen"}).status_code == 200

    instances = (await db_session.execute(select(ChecklistInstance))).scalars().all()
    assert len(instances) == 1
    assert instances[0].template_id == tmpl.id
