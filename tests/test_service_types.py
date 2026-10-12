"""Phase 6 — Service Types & Templates.

Tests für SVC-01..SVC-04. SVC-01 bleibt Skip bis Plan 06-03.
SVC-02, SVC-03, SVC-04 werden hier implementiert.
"""
import os
import sys
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    """Helper: builds a patched httpx.AsyncClient context manager that returns a fixed JSON for /api/session/start."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={
        "wav_path": "/tmp/test.wav",
        "session_started_at": "2026-04-29T10:00:00",
        "status": "recording",
    })
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def test_service_types_list_contains_16_types(api_client):
    """SVC-01: Dashboard liefert exakt 16 Service-Typen in vorgegebener Reihenfolge."""
    resp = api_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    expected = [
        "Bibelstunde", "Gebetsstunde", "Sonntagmorgen", "Sonntagabend",
        "Hochzeit", "Heiligabend", "Weihnachten", "Silvester", "Neujahr",
        "Karfreitag", "Ostern", "Himmelfahrt", "Pfingsten",
        "Erntedankfest", "Besuch", "Sonstiges",
    ]
    for t in expected:
        assert t in html, f"Service type {t!r} missing from dashboard HTML"
    assert html.index("Bibelstunde") < html.index("Sonstiges")
    assert html.index("Sonntagabend") < html.index("Hochzeit") < html.index("Heiligabend")


@pytest.mark.asyncio
async def test_wedding_session_persists_couple_name_and_part(api_client, db_session):
    """SVC-02"""
    from models import RecordingSession
    from sqlalchemy import select
    with patch("routers.sessions.httpx.AsyncClient", return_value=_mock_audio_service_start()):
        resp = api_client.post("/api/sessions/start", json={
            "service_type": "Hochzeit",
            "couple_name": "Max & Maria",
            "wedding_part": 2,
        })
    assert resp.status_code == 200, resp.text
    rows = (await db_session.execute(select(RecordingSession))).scalars().all()
    assert len(rows) == 1
    assert rows[0].couple_name == "Max & Maria"
    assert rows[0].wedding_part == 2


@pytest.mark.asyncio
async def test_track_sequence_lookup_returns_seeded_default(api_client, db_session):
    """SVC-03"""
    from models import ServiceTypeTemplate
    db_session.add(ServiceTypeTemplate(
        service_type="Sonntagmorgen",
        track_sequence='["Chorlied","Predigt"]',
        checklist_template_id=None,
    ))
    await db_session.commit()
    with patch("routers.sessions.httpx.AsyncClient", return_value=_mock_audio_service_start()):
        resp = api_client.post("/api/sessions/start", json={"service_type": "Sonntagmorgen"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["track_sequence"] == ["Chorlied", "Predigt"]


@pytest.mark.asyncio
async def test_checklist_auto_activated_on_session_start(api_client, db_session):
    """SVC-04"""
    from models import ChecklistTemplate, ChecklistInstance, ServiceTypeTemplate
    from sqlalchemy import select
    tmpl = ChecklistTemplate(name="Sonntagmorgen Standard")
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    db_session.add(ServiceTypeTemplate(
        service_type="Sonntagmorgen",
        track_sequence='[]',
        checklist_template_id=tmpl.id,
    ))
    await db_session.commit()
    with patch("routers.sessions.httpx.AsyncClient", return_value=_mock_audio_service_start()):
        resp = api_client.post("/api/sessions/start", json={"service_type": "Sonntagmorgen"})
    assert resp.status_code == 200
    instances = (await db_session.execute(select(ChecklistInstance))).scalars().all()
    assert len(instances) == 1
    assert instances[0].template_id == tmpl.id
