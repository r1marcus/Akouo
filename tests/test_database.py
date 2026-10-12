import pytest
import pytest_asyncio
import sqlalchemy as sa


@pytest.mark.asyncio
async def test_create_recording_row():
    """FND-03: Recording row can be inserted and retrieved via async SQLAlchemy session."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from models import Recording, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        rec = Recording(
            filename="2026-03-29_Gottesdienst_001.mp3",
            path="/Recordings/Tontechnik/2026/03/2026-03-29_Gottesdienst_001.mp3",
            date="2026-03-29",
            service_type="Gottesdienst",
            duration=3600,
        )
        session.add(rec)
        await session.commit()
        assert rec.id is not None


@pytest.mark.asyncio
async def test_recording_fields():
    """FND-03: Recording model has all required fields: id, filename, path, date, service_type, duration, created_at."""
    from models import Recording
    rec = Recording()
    required_fields = ["id", "filename", "path", "date", "service_type", "duration", "created_at"]
    for field in required_fields:
        assert hasattr(rec, field), f"Recording model missing field: {field}"


@pytest.mark.asyncio
async def test_recording_session_insert():
    """REC-07: RecordingSession row can be inserted via async SQLAlchemy session."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime
    from models import RecordingSession, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        rs = RecordingSession(
            started_at=datetime.now(),
            wav_path="/tmp/test.wav",
            service_type="Gottesdienst",
            status="recording",
        )
        session.add(rs)
        await session.commit()
        assert rs.id is not None


@pytest.mark.asyncio
async def test_marker_fk_to_session():
    """REC-08/REC-09: Marker can be inserted with valid session_id FK."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime
    from models import RecordingSession, Marker, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        rs = RecordingSession(
            started_at=datetime.now(),
            wav_path="/tmp/test.wav",
            service_type="Gottesdienst",
            status="recording",
        )
        session.add(rs)
        await session.commit()

        m = Marker(
            session_id=rs.id,
            marker_type="Predigt",
            placed_at_ts=42.5,
            placed_at_wall=datetime.now(),
        )
        session.add(m)
        await session.commit()
        assert m.id is not None
        assert m.placed_at_ts == 42.5


@pytest.mark.asyncio
async def test_marker_orphan_rejected():
    """FK constraint: marker with non-existent session_id raises IntegrityError."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime
    from sqlalchemy.exc import IntegrityError
    from models import Marker, Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        # Enable FK enforcement on sqlite (off by default)
        await session.execute(sa.text("PRAGMA foreign_keys=ON"))
        m = Marker(
            session_id=999999,
            marker_type="Predigt",
            placed_at_ts=10.0,
            placed_at_wall=datetime.now(),
        )
        session.add(m)
        with pytest.raises(IntegrityError):
            await session.commit()
