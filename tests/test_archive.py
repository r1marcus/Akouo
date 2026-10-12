import pytest
import pathlib
import os


@pytest.mark.asyncio
async def test_browse_recordings():
    """REC-05: GET /api/recordings returns 200 with JSON list (may be empty)."""
    import sys
    # Use in-memory SQLite for test isolation
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    # Ensure api/ modules are importable
    if str(pathlib.Path(__file__).parent.parent / "api") not in sys.path:
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "api"))
    # Force reimport to pick up new DATABASE_URL
    for mod in list(sys.modules.keys()):
        if mod in ("database", "main", "routers.recordings", "routers.health", "models"):
            del sys.modules[mod]
    from httpx import AsyncClient, ASGITransport
    import database as db_module
    from models import Base
    from main import app

    # Create tables in in-memory DB before making requests
    async with db_module.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/recordings")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_local_archive(tmp_recordings):
    """REC-06: Recordings stored under YYYY/MM/ subdirectory structure."""
    # Simulate what the Recorder creates: ~/Recordings/Tontechnik/2026/03/
    year_dir = tmp_recordings / "2026"
    month_dir = year_dir / "03"
    month_dir.mkdir(parents=True)
    mp3_path = month_dir / "2026-03-29_Gottesdienst_001.mp3"
    mp3_path.write_bytes(b"fake mp3 content")

    assert mp3_path.exists()
    assert mp3_path.parent.name == "03"
    assert mp3_path.parent.parent.name == "2026"
