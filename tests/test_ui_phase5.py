"""Phase 5 Wave 0 — UI test stubs for UI-06, UI-07, UI-08, UI-09.

All tests are skipped; each stub locks the expected contract for the
implementing plan (05-02 or 05-03) and cites its requirement ID.

Pattern mirrors tests/test_session_api.py: pytest-asyncio, httpx AsyncClient,
in-memory SQLite, DATABASE_URL set before api imports.
"""
import os
import pytest

# Set DATABASE_URL before any api imports
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_timers(tmp_path):
    # UI-08, UI-09: dashboard partial includes Alpine timer + session_section.html
    from pathlib import Path
    partial = Path("api/templates/partials/session_section.html").read_text()
    assert "setInterval" in partial
    assert "session:started" in partial  # event listener
    assert "formatTime" in partial
    dashboard = Path("api/templates/dashboard.html").read_text()
    assert "session_section.html" in dashboard


@pytest.mark.asyncio
async def test_waveform():
    # UI-06: audio-service exposes /api/session/audio-level as SSE
    # Verify contract by inspecting: route exists, returns EventSourceResponse,
    # and get_rms_level() is callable. Avoids hanging on infinite ASGI SSE stream.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "audio_service_main",
        Path(__file__).parent.parent / "audio-service" / "main.py",
    )
    audio_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audio_mod)
    app = audio_mod.app

    # 1. Route /api/session/audio-level must be registered
    route_paths = [r.path for r in app.routes]
    assert "/api/session/audio-level" in route_paths, (
        f"Route not found. Available: {route_paths}"
    )

    # 2. get_rms_level() must return float in [0, 1] (idle → 0.0)
    rms = audio_mod.continuous_recorder.get_rms_level()
    assert isinstance(rms, float), f"get_rms_level() must return float, got {type(rms)}"
    assert 0.0 <= rms <= 1.0, f"RMS out of range: {rms}"

    # 3. The endpoint function must return an EventSourceResponse
    # Call the endpoint coroutine directly and check return type
    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        from fastapi.sse import EventSourceResponse
    response_obj = await audio_mod.audio_level_stream()
    assert isinstance(response_obj, EventSourceResponse), (
        f"audio_level_stream must return EventSourceResponse, got {type(response_obj)}"
    )


@pytest.mark.asyncio
async def test_marker_nudge(tmp_path):
    # UI-07: PATCH /api/sessions/markers/{id}/nudge updates DB row's start_nudge/end_nudge,
    # leaves placed_at_ts unchanged, and never crashes when audio-service unreachable.
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

    db_file = tmp_path / "test_nudge.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from api.models import Base, RecordingSession, Marker
    from api.main import app
    import database as _db_module  # same object the router imported via `from database import get_db`
    from datetime import datetime
    from httpx import AsyncClient, ASGITransport
    from unittest.mock import patch

    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as db:
        sess = RecordingSession(
            started_at=datetime.now(), wav_path="/tmp/x.wav",
            service_type="Gottesdienst", status="recording",
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        m = Marker(
            session_id=sess.id, marker_type="Predigt",
            placed_at_ts=120.0, placed_at_wall=datetime.now(),
            audio_service_index=0,
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        marker_id = m.id
        original_ts = m.placed_at_ts

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[_db_module.get_db] = override_get_db

    try:
        # ConnectError on audio-service sync must be swallowed
        with patch("api.routers.sessions.httpx.AsyncClient") as mock_client:
            from httpx import ConnectError
            mock_client.return_value.__aenter__.return_value.patch.side_effect = ConnectError("audio-service down")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.patch(
                    f"/api/sessions/markers/{marker_id}/nudge",
                    json={"field": "start", "delta_seconds": 1.0},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["start_nudge"] == 1.0
        assert body["field"] == "start"

        async with TestSessionLocal() as db:
            updated = await db.get(Marker, marker_id)
            assert updated.start_nudge == 1.0
            assert updated.placed_at_ts == original_ts  # unchanged
    finally:
        app.dependency_overrides.pop(_db_module.get_db, None)
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_nudge_api(tmp_path):
    # UI-07: validation — bad field/delta → 422
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

    db_file = tmp_path / "test_nudge_api.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from api.models import Base
    from api.main import app
    import database as _db_module2  # same object the router imported
    from httpx import AsyncClient, ASGITransport

    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[_db_module2.get_db] = override_get_db

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.patch("/api/sessions/markers/9999/nudge",
                                     json={"field": "middle", "delta_seconds": 1.0})
            assert r1.status_code == 422
            r2 = await client.patch("/api/sessions/markers/9999/nudge",
                                     json={"field": "start", "delta_seconds": 5.0})
            assert r2.status_code == 422
            r3 = await client.patch("/api/sessions/markers/9999/nudge",
                                     json={"field": "start", "delta_seconds": 1.0})
            assert r3.status_code == 404  # marker doesn't exist
    finally:
        app.dependency_overrides.pop(_db_module2.get_db, None)
        await test_engine.dispose()
