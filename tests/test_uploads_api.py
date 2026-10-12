"""Tests for POST /api/uploads/mp3 and GET /api/uploads/{id} (EXP-03).

Uses in-memory SQLite and monkeypatches httpx.AsyncClient to mock audio-service NAS push.
Pattern mirrors test_segments_api.py.
"""
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

# Must set DATABASE_URL before importing any api module
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import sys
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


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


def _mock_nas_response(status: str, nas_path: str = None, uploaded_at: str = None, error: str = None):
    """Return a context-manager mock for httpx.AsyncClient that returns a NAS push response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": status,
        "nas_path": nas_path,
        "uploaded_at": uploaded_at,
        "error": error,
    }
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ─── POST /api/uploads/mp3 tests ───────────────────────────────────────────

@pytest.mark.anyio
async def test_upload_mp3_happy_path(db_session, monkeypatch, tmp_path):
    """200 + nas_status='uploaded' on happy path with mocked NAS push."""
    import routers.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", tmp_path)

    import httpx
    mock_client = _mock_nas_response(
        status="uploaded",
        nas_path="\\\\srv\\Audio\\GD\\uploads\\2026\\04\\file.mp3",
        uploaded_at="2026-04-29T10:00:00",
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: mock_client)

    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        mp3_bytes = b"\xff\xfb\x90\x00" * 100
        resp = client.post(
            "/api/uploads/mp3",
            files={"file": ("sermon.mp3", mp3_bytes, "audio/mpeg")},
            data={"label": "Predigt"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] == "stored"
        assert body["nas_status"] == "uploaded"
        assert body["label"] == "Predigt"
        assert body["size_bytes"] == len(mp3_bytes)
        assert body["filename"].endswith("_sermon.mp3")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upload_mp3_wrong_content_type(db_session, monkeypatch, tmp_path):
    """415 when content_type is text/plain."""
    import routers.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", tmp_path)

    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/uploads/mp3",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 415, f"Expected 415, got {resp.status_code}: {resp.text}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upload_mp3_filename_no_extension(db_session, monkeypatch, tmp_path):
    """422 when filename has no .mp3 extension."""
    import routers.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", tmp_path)

    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/uploads/mp3",
            files={"file": ("no_extension", b"\xff\xfb\x90\x00", "audio/mpeg")},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        assert "mp3" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upload_mp3_dotdot_filename(db_session, monkeypatch, tmp_path):
    """422 when filename is '..' (invalid after PurePosixPath strip)."""
    import routers.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", tmp_path)

    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/uploads/mp3",
            files={"file": ("..", b"\xff\xfb\x90\x00", "audio/mpeg")},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upload_mp3_oversized(db_session, monkeypatch, tmp_path):
    """413 when file exceeds MAX_BYTES (monkeypatched to 100 bytes for test speed)."""
    import routers.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(uploads_mod, "MAX_BYTES", 100)

    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        big_payload = b"\xff\xfb\x90\x00" * 200  # 800 bytes > 100 byte limit
        resp = client.post(
            "/api/uploads/mp3",
            files={"file": ("big.mp3", big_payload, "audio/mpeg")},
        )
        assert resp.status_code == 413, f"Expected 413, got {resp.status_code}: {resp.text}"
        assert "exceeds" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upload_mp3_nas_failed_on_connect_error(db_session, monkeypatch, tmp_path):
    """nas_status='failed' when audio-service raises httpx.ConnectError."""
    import routers.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "UPLOADS_DIR", tmp_path)

    import httpx

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FailingClient())

    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/uploads/mp3",
            files={"file": ("sermon.mp3", b"\xff\xfb\x90\x00" * 50, "audio/mpeg")},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] == "stored"
        assert body["nas_status"] == "failed"
        assert body["nas_error"] == "audio service unreachable"
    finally:
        app.dependency_overrides.clear()


# ─── GET /api/uploads/{id} tests ───────────────────────────────────────────

@pytest.mark.anyio
async def test_get_upload_404_for_missing_id(db_session):
    """404 when SegmentExport id does not exist."""
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        resp = client.get("/api/uploads/99999")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert "99999" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_get_upload_200_streams_mp3(db_session, tmp_path):
    """200 + audio/mpeg content-type when SegmentExport exists with real local MP3."""
    from models import RecordingSession, Marker, SegmentExport
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    # Create a real MP3 file
    mp3_file = tmp_path / "sermon_segment.mp3"
    mp3_file.write_bytes(b"\xff\xfb\x90\x00" * 500)

    # Insert prerequisite rows
    rs = RecordingSession(
        started_at=datetime(2026, 4, 29, 9, 0, 0),
        stopped_at=datetime(2026, 4, 29, 10, 0, 0),
        wav_path="/tmp/test.wav",
        service_type="Gottesdienst",
        status="stopped",
    )
    db_session.add(rs)
    await db_session.commit()
    await db_session.refresh(rs)

    marker = Marker(
        session_id=rs.id,
        marker_type="Predigt",
        placed_at_ts=100.0,
        placed_at_wall=datetime(2026, 4, 29, 9, 30, 0),
    )
    db_session.add(marker)
    await db_session.commit()
    await db_session.refresh(marker)

    export_row = SegmentExport(
        session_id=rs.id,
        marker_id=marker.id,
        marker_type="Predigt",
        start_seconds=100.0,
        end_seconds=200.0,
        mp3_path=str(mp3_file),
        mp3_filename="sermon_segment.mp3",
        nas_status="skipped",
    )
    db_session.add(export_row)
    await db_session.commit()
    await db_session.refresh(export_row)

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        resp = client.get(f"/api/uploads/{export_row.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.headers["content-type"].startswith("audio/mpeg")
        assert resp.content == mp3_file.read_bytes()
    finally:
        app.dependency_overrides.clear()
