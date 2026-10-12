"""Tests for /api/sessions/* router.

Uses in-memory SQLite and monkeypatches httpx.AsyncClient to mock the audio-service.
Pattern: set DATABASE_URL before importing api modules, then use TestClient.
"""
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Must set DATABASE_URL before importing any api module
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_session():
    """Async SQLAlchemy session backed by in-memory SQLite."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
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
    """FastAPI TestClient with overridden get_db dependency."""
    import sys
    # Ensure api/ is on path
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


# ─── Helper: build a mock httpx response ────────────────────────────────────

def _mock_response(status_code: int, json_data: dict):
    """Create a MagicMock that quacks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


# ─── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_session_persists_row(db_session):
    """POST /api/sessions/start: audio-service returns 200; RecordingSession row inserted."""
    import sys
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    from models import RecordingSession
    from sqlalchemy import select

    audio_response = _mock_response(200, {
        "status": "recording",
        "wav_path": "/tmp/test.wav",
        "session_started_at": "2026-04-29T10:00:00",
        "service_type": "Gottesdienst",
    })

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = audio_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        from routers.sessions import start_session, StartSessionRequest
        req = StartSessionRequest(service_type="Gottesdienst")
        result = await start_session(req, db=db_session)

    assert result["status"] == "recording"
    assert "id" in result

    # Verify DB row
    rows = (await db_session.execute(
        select(RecordingSession).where(RecordingSession.status == "recording")
    )).scalars().all()
    assert len(rows) >= 1
    assert rows[0].wav_path == "/tmp/test.wav"
    assert rows[0].service_type == "Gottesdienst"


@pytest.mark.asyncio
async def test_marker_persists_with_session_fk(db_session):
    """POST /api/sessions/marker: Marker row inserted with correct session FK."""
    import sys
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    from models import RecordingSession, Marker
    from sqlalchemy import select

    # Seed an active session
    rs = RecordingSession(
        started_at=datetime.now(),
        wav_path="/tmp/test.wav",
        service_type="Gottesdienst",
        status="recording",
    )
    db_session.add(rs)
    await db_session.commit()
    await db_session.refresh(rs)

    audio_response = _mock_response(200, {
        "status": "ok",
        "marker": {
            "type": "Predigt",
            "offset_seconds": 42.5,
            "wall_time": "2026-04-29T10:01:00",
        },
    })

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = audio_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        from routers.sessions import place_marker, MarkerRequest
        req = MarkerRequest(marker_type="Predigt")
        result = await place_marker(req, db=db_session)

    assert result["status"] == "ok"

    # Verify Marker row
    markers = (await db_session.execute(
        select(Marker).where(Marker.session_id == rs.id)
    )).scalars().all()
    assert len(markers) == 1
    assert markers[0].marker_type == "Predigt"
    assert markers[0].placed_at_ts == 42.5


@pytest.mark.asyncio
async def test_stop_session_updates_status(db_session):
    """POST /api/sessions/stop: session status becomes stopped; stopped_at set."""
    import sys
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    from models import RecordingSession
    from sqlalchemy import select

    # Seed an active session
    rs = RecordingSession(
        started_at=datetime.now(),
        wav_path="/tmp/test.wav",
        service_type="Gottesdienst",
        status="recording",
    )
    db_session.add(rs)
    await db_session.commit()
    await db_session.refresh(rs)

    audio_response = _mock_response(200, {
        "status": "stopped",
        "wav_path": "/tmp/test.wav",
        "duration": 120,
        "service_type": "Gottesdienst",
        "markers": [],
        "gaps": [],
    })

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = audio_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        from routers.sessions import stop_session
        result = await stop_session(db=db_session)

    assert result["status"] == "stopped"
    assert result.get("session_id") == rs.id

    # Refresh and verify DB
    await db_session.refresh(rs)
    assert rs.status == "stopped"
    assert rs.stopped_at is not None


def test_streaming_proxy_targets_8001():
    """Belegt, dass streaming.py vom toten streaming-service (8002) auf den
    Audio-Dienst (8001) umgezogen ist.

    Geprüft wird der Standard im Quellcode, nicht der Laufzeitwert: AUDIO_SERVICE_URL
    ist per Umgebung überschreibbar, und die Testläufe biegen sie absichtlich auf einen
    Discard-Port um, damit kein Test einen echten Dienst erreicht.
    """
    import pathlib

    quelle = pathlib.Path(__file__).parent.parent / "api" / "routers" / "streaming.py"
    text = quelle.read_text()
    assert "8001" in text
    assert "8002" not in text


def test_streaming_paths_updated():
    """Verify streaming.py uses /api/streaming/* paths for httpx proxy calls."""
    import sys
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    # Read the source file directly to check proxy call paths
    import pathlib
    source = (pathlib.Path(api_dir) / "routers" / "streaming.py").read_text()
    assert "/api/streaming/status" in source
    assert "/api/streaming/start" in source
    assert "/api/streaming/stop" in source
    # The STREAMING_SERVICE_URL string must not contain a bare /status, /start, /stop call
    # (i.e. the f-string calls must use the full /api/streaming/* prefix)
    assert 'STREAMING_SERVICE_URL}/status"' not in source
    assert 'STREAMING_SERVICE_URL}/start"' not in source
    assert 'STREAMING_SERVICE_URL}/stop"' not in source
