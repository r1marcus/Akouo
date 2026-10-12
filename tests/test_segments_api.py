"""Tests for /api/sessions/{id}/segments and /api/sessions/{id}/segments/{marker_id}/export.

Uses in-memory SQLite and monkeypatches httpx.AsyncClient to mock the audio-service.
Pattern mirrors test_session_api.py.
"""
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

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


def _mock_response(status_code: int, json_data: dict):
    """Create a MagicMock that quacks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def _mock_audio_client(json_data: dict, status_code: int = 200):
    """Return a context-manager mock for httpx.AsyncClient that returns json_data on POST."""
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(status_code, json_data)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ─── Helpers ────────────────────────────────────────────────────────────────

async def _make_session(db, started_at=None, stopped_at=None, wav_path="/tmp/test.wav",
                         service_type="Gottesdienst", status="stopped",
                         couple_name=None, wedding_part=None):
    from models import RecordingSession
    started_at = started_at or datetime(2026, 4, 29, 9, 0, 0)
    rs = RecordingSession(
        started_at=started_at,
        stopped_at=stopped_at,
        wav_path=wav_path,
        service_type=service_type,
        status=status,
        couple_name=couple_name,
        wedding_part=wedding_part,
    )
    db.add(rs)
    await db.commit()
    await db.refresh(rs)
    return rs


async def _make_marker(db, session_id, marker_type="Predigt",
                        placed_at_ts=10.0, start_nudge=0.0, end_nudge=0.0,
                        nudge_manual=False):
    from models import Marker
    m = Marker(
        session_id=session_id,
        marker_type=marker_type,
        placed_at_ts=placed_at_ts,
        placed_at_wall=datetime.now(),
        start_nudge=start_nudge,
        end_nudge=end_nudge,
        nudge_manual=nudge_manual,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


# ─── Tests: list_segments ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_segments_empty_when_no_markers(db_session):
    """GET /api/sessions/{id}/segments returns empty list when session has no markers."""
    from routers.segments import list_segments

    sess = await _make_session(db_session)

    # Minimal request mock (no HX-Request header)
    mock_request = MagicMock()
    mock_request.headers = {}

    result = await list_segments(session_id=sess.id, request=mock_request, db=db_session)

    assert result["segments"] == []
    assert result["session_id"] == sess.id


@pytest.mark.asyncio
async def test_list_segments_correct_boundaries_three_markers(db_session):
    """3 markers at t=10, t=120, t=600; stopped_at = started_at + 1500s.
    Expected segments: [10,120), [120,600), [600,1500) — all durations positive.
    """
    from routers.segments import list_segments

    started = datetime(2026, 4, 29, 9, 0, 0)
    stopped = started + timedelta(seconds=1500)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)

    m1 = await _make_marker(db_session, sess.id, "Begruessung", placed_at_ts=10.0)
    m2 = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=120.0)
    m3 = await _make_marker(db_session, sess.id, "Abschluss", placed_at_ts=600.0)

    mock_request = MagicMock()
    mock_request.headers = {}

    result = await list_segments(session_id=sess.id, request=mock_request, db=db_session)
    segs = result["segments"]

    assert len(segs) == 3

    assert segs[0]["start_seconds"] == 10.0
    assert segs[0]["end_seconds"] == 120.0
    assert segs[0]["duration_seconds"] > 0

    assert segs[1]["start_seconds"] == 120.0
    assert segs[1]["end_seconds"] == 600.0
    assert segs[1]["duration_seconds"] > 0

    assert segs[2]["start_seconds"] == 600.0
    assert segs[2]["end_seconds"] == 1500.0
    assert segs[2]["duration_seconds"] > 0

    # marker_ids match insertion order
    assert segs[0]["marker_id"] == m1.id
    assert segs[1]["marker_id"] == m2.id
    assert segs[2]["marker_id"] == m3.id


@pytest.mark.asyncio
async def test_list_segments_start_nudge_applied(db_session):
    """Marker at placed_at_ts=100, start_nudge=-1 → segment start=99."""
    from routers.segments import list_segments

    started = datetime(2026, 4, 29, 9, 0, 0)
    stopped = started + timedelta(seconds=500)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=100.0, start_nudge=-1.0)

    mock_request = MagicMock()
    mock_request.headers = {}

    result = await list_segments(session_id=sess.id, request=mock_request, db=db_session)
    segs = result["segments"]

    assert len(segs) == 1
    assert segs[0]["start_seconds"] == 99.0  # 100 + (-1)


@pytest.mark.asyncio
async def test_list_segments_end_nudge_applied(db_session):
    """Two markers at t=10 and t=200 with end_nudge=+5 on first marker.
    First segment end = 200 (next marker start) + 5 (end_nudge) = 205.
    """
    from routers.segments import list_segments

    started = datetime(2026, 4, 29, 9, 0, 0)
    stopped = started + timedelta(seconds=600)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)

    await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=10.0, end_nudge=5.0)
    await _make_marker(db_session, sess.id, "Abschluss", placed_at_ts=200.0)

    mock_request = MagicMock()
    mock_request.headers = {}

    result = await list_segments(session_id=sess.id, request=mock_request, db=db_session)
    segs = result["segments"]

    assert len(segs) == 2
    # first segment: start=10, end = 200 (next marker) + 5 (end_nudge) = 205
    assert segs[0]["start_seconds"] == 10.0
    assert segs[0]["end_seconds"] == 205.0


# ─── Tests: export_segment ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_segment_404_wrong_marker(db_session):
    """export_segment returns 404 if marker_id not in session."""
    from fastapi import HTTPException
    from routers.segments import export_segment

    sess = await _make_session(db_session)

    mock_request = MagicMock()
    mock_request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await export_segment(session_id=sess.id, marker_id=9999,
                              request=mock_request, db=db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_export_segment_creates_segment_export_row(db_session):
    """export_segment with mocked audio-service creates exactly one SegmentExport row."""
    from sqlalchemy import select, func
    from models import SegmentExport
    from routers.segments import export_segment

    started = datetime(2026, 4, 29, 10, 0, 0)
    stopped = started + timedelta(seconds=300)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    m = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=10.0)

    audio_resp = {
        "export": {"mp3_path": "/exports/x.mp3", "duration_ms": 5000, "frame_rate": 48000},
        "nas": {
            "status": "uploaded",
            "error": None,
            "nas_path": "\\\\srv\\Audio\\GD\\2026\\04\\x.mp3",
            "uploaded_at": "2026-04-29T10:00:00",
        },
        "mp3_filename": "x.mp3",
        "mp3_path": "/exports/x.mp3",
    }

    mock_request = MagicMock()
    mock_request.headers = {}

    with patch("httpx.AsyncClient", return_value=_mock_audio_client(audio_resp)):
        result = await export_segment(session_id=sess.id, marker_id=m.id,
                                      request=mock_request, db=db_session)

    # Should return the export result
    assert result["nas_status"] == "uploaded"

    # Exactly one SegmentExport row
    count = (await db_session.execute(
        select(func.count()).select_from(SegmentExport)
        .where(SegmentExport.session_id == sess.id)
        .where(SegmentExport.marker_id == m.id)
    )).scalar()
    assert count == 1

    row = (await db_session.execute(
        select(SegmentExport)
        .where(SegmentExport.session_id == sess.id)
        .where(SegmentExport.marker_id == m.id)
    )).scalar_one()

    assert row.nas_status == "uploaded"
    assert row.uploaded_at is not None
    assert row.uploaded_at.isoformat() == "2026-04-29T10:00:00"
    assert row.mp3_filename == "x.mp3"


@pytest.mark.asyncio
async def test_export_segment_idempotent_second_call(db_session):
    """Second call to export_segment updates existing row — count stays at 1."""
    from sqlalchemy import select, func
    from models import SegmentExport
    from routers.segments import export_segment

    started = datetime(2026, 4, 29, 10, 0, 0)
    stopped = started + timedelta(seconds=300)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    m = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=10.0)

    audio_resp = {
        "export": {"mp3_path": "/exports/x.mp3", "duration_ms": 5000, "frame_rate": 48000},
        "nas": {
            "status": "uploaded",
            "error": None,
            "nas_path": "\\\\srv\\Audio\\GD\\2026\\04\\x.mp3",
            "uploaded_at": "2026-04-29T10:00:00",
        },
        "mp3_filename": "x.mp3",
        "mp3_path": "/exports/x.mp3",
    }

    mock_request = MagicMock()
    mock_request.headers = {}

    # First call
    with patch("httpx.AsyncClient", return_value=_mock_audio_client(audio_resp)):
        await export_segment(session_id=sess.id, marker_id=m.id,
                              request=mock_request, db=db_session)

    # Second call
    with patch("httpx.AsyncClient", return_value=_mock_audio_client(audio_resp)):
        await export_segment(session_id=sess.id, marker_id=m.id,
                              request=mock_request, db=db_session)

    count = (await db_session.execute(
        select(func.count()).select_from(SegmentExport)
        .where(SegmentExport.session_id == sess.id)
        .where(SegmentExport.marker_id == m.id)
    )).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_export_segment_failed_nas_status(db_session):
    """When audio-service returns nas.status='failed', SegmentExport row has nas_status='failed' and nas_error."""
    from sqlalchemy import select
    from models import SegmentExport
    from routers.segments import export_segment

    started = datetime(2026, 4, 29, 10, 0, 0)
    stopped = started + timedelta(seconds=300)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    m = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=10.0)

    audio_resp = {
        "export": {"mp3_path": "/exports/y.mp3", "duration_ms": 3000, "frame_rate": 48000},
        "nas": {
            "status": "failed",
            "error": "Connection refused to NAS",
            "nas_path": None,
            "uploaded_at": None,
        },
        "mp3_filename": "y.mp3",
        "mp3_path": "/exports/y.mp3",
    }

    mock_request = MagicMock()
    mock_request.headers = {}

    with patch("httpx.AsyncClient", return_value=_mock_audio_client(audio_resp)):
        result = await export_segment(session_id=sess.id, marker_id=m.id,
                                      request=mock_request, db=db_session)

    assert result["nas_status"] == "failed"

    row = (await db_session.execute(
        select(SegmentExport)
        .where(SegmentExport.session_id == sess.id)
        .where(SegmentExport.marker_id == m.id)
    )).scalar_one()

    assert row.nas_status == "failed"
    assert row.nas_error == "Connection refused to NAS"
    assert row.uploaded_at is None


# ─── Tests: refine_session (Handarbeit schlägt Automatik) ───────────────────

@pytest.mark.asyncio
async def test_refine_session_skips_hand_nudged_marker(db_session):
    """A marker flagged nudge_manual keeps its offsets; the others are refined as before."""
    from routers.segments import refine_session

    started = datetime(2026, 9, 20, 9, 0, 0)
    stopped = started + timedelta(seconds=600)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    manual = await _make_marker(db_session, sess.id, "Lied", placed_at_ts=10.0,
                                start_nudge=-3.0, end_nudge=-4.0, nudge_manual=True)
    auto = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=200.0,
                              start_nudge=9.0, end_nudge=9.0)

    audio_resp = {"start_delta": -2.0, "end_delta": 1.5}
    with patch("routers.segments.httpx.AsyncClient",
               return_value=_mock_audio_client(audio_resp)):
        result = await refine_session(sess, db_session)

    await db_session.refresh(manual)
    await db_session.refresh(auto)

    assert manual.start_nudge == -3.0, "Handarbeit darf nicht überschrieben werden"
    assert manual.end_nudge == -4.0
    assert auto.start_nudge == -2.0
    assert auto.end_nudge == 1.5
    assert result.shifted == 1
    assert result.skipped_manual == 1


@pytest.mark.asyncio
async def test_refine_session_repeated_runs_do_not_pile_up(db_session):
    """Automatic markers are reset before every run, so offsets never accumulate."""
    from routers.segments import refine_session

    started = datetime(2026, 9, 20, 9, 0, 0)
    stopped = started + timedelta(seconds=600)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    auto = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=100.0)

    audio_resp = {"start_delta": -2.0, "end_delta": 1.5}
    with patch("routers.segments.httpx.AsyncClient",
               return_value=_mock_audio_client(audio_resp)):
        await refine_session(sess, db_session)
        await refine_session(sess, db_session)

    await db_session.refresh(auto)
    assert auto.start_nudge == -2.0
    assert auto.end_nudge == 1.5


@pytest.mark.asyncio
async def test_refine_session_counts_only_manual_markers_as_skipped(db_session):
    """Nothing hand-set → skipped_manual stays 0 and every marker is refined."""
    from routers.segments import refine_session

    started = datetime(2026, 9, 20, 9, 0, 0)
    stopped = started + timedelta(seconds=600)
    sess = await _make_session(db_session, started_at=started, stopped_at=stopped)
    await _make_marker(db_session, sess.id, "Lied", placed_at_ts=10.0)
    await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=200.0)

    audio_resp = {"start_delta": -1.0, "end_delta": 0.0}
    with patch("routers.segments.httpx.AsyncClient",
               return_value=_mock_audio_client(audio_resp)):
        result = await refine_session(sess, db_session)

    assert result.shifted == 2
    assert result.skipped_manual == 0


# ─── Tests: Nudge-Endpunkt setzt und löscht das Handzeichen ─────────────────

@pytest.mark.asyncio
async def test_nudge_endpoint_marks_marker_as_manual(db_session):
    from routers.sessions import MarkerNudgeRequest, nudge_marker_endpoint

    sess = await _make_session(db_session)
    m = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=100.0)

    body = await nudge_marker_endpoint(
        marker_id=m.id,
        req=MarkerNudgeRequest(field="end", delta_seconds=-1.0),
        db=db_session,
    )

    assert body["nudge_manual"] is True
    await db_session.refresh(m)
    assert m.nudge_manual is True


@pytest.mark.asyncio
async def test_nudge_back_to_zero_releases_marker_to_automation(db_session):
    """±1 back to 0/0 means the operator undid their handwork — automation takes over again."""
    from routers.sessions import MarkerNudgeRequest, nudge_marker_endpoint

    sess = await _make_session(db_session)
    m = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=100.0)

    await nudge_marker_endpoint(m.id, MarkerNudgeRequest(field="start", delta_seconds=1.0), db_session)
    body = await nudge_marker_endpoint(m.id, MarkerNudgeRequest(field="start", delta_seconds=-1.0), db_session)

    assert body["start_nudge"] == 0.0
    assert body["nudge_manual"] is False
    await db_session.refresh(m)
    assert m.nudge_manual is False


# ---------------------------------------------------------------------------
# NAS-Pfade — müssen die gewachsene Ablage treffen, sonst entstehen Doppelordner
# ---------------------------------------------------------------------------

class _Sess:
    def __init__(self, service_type, couple_name=None, wedding_part=None, thema=None):
        self.service_type = service_type
        self.couple_name = couple_name
        self.wedding_part = wedding_part
        self.thema = thema


def _pfad(session, started, art=None):
    """Den Ordner aus der Standardvorlage bauen.

    Geprüft wird der Renderer mit den Standardvorlagen, nicht der Datenbank-Wrapper
    in segments.py: der sucht nur die passende Vorlage heraus. Die Standardwerte
    müssen die gewachsene Ablage genau treffen, sonst entstehen Doppelordner — und
    genau das sichern diese Tests ab.
    """
    import nas_pfad

    schluessel = {
        "Hochzeit": "nas_vorlage_hochzeit",
        "Sonstiges": "nas_vorlage_sonstiges",
    }.get(art or session.service_type, "nas_vorlage_standard")
    return nas_pfad.rendern(nas_pfad.STANDARD_VORLAGEN[schluessel],
                            nas_pfad.werte_fuer(session, started))


def test_nas_subpath_gottesdienst_morgens():
    assert _pfad(_Sess("Sonntagmorgen"), datetime(2026, 10, 4, 9, 51)) == \
        "Gottesdienste/2026-10-04 Sonntag 1. morgens"


def test_nas_subpath_gottesdienst_abends():
    assert _pfad(_Sess("Sonntagabend"), datetime(2026, 10, 4, 18, 0)) == \
        "Gottesdienste/2026-10-04 Sonntag 2. abends"


def test_nas_subpath_tageszeit_schlaegt_gottesdienstart():
    """Eine Dienstagabend-Bibelstunde ist die einzige des Tages und heißt trotzdem '2. abends'."""
    assert _pfad(_Sess("Bibelstunde"), datetime(2026, 9, 29, 19, 30)) == \
        "Gottesdienste/2026-09-29 Dienstag 2. abends"


def test_nas_subpath_hochzeit_mit_teil():
    assert _pfad(_Sess("Hochzeit", "Robin & Laura", 2), datetime(2026, 6, 6, 14, 0)) == \
        "Hochzeiten/2026/2026-06-06 Robin & Laura/2. Teil"


def test_nas_subpath_hochzeit_ohne_teil():
    assert _pfad(_Sess("Hochzeit", "Joel & Viki"), datetime(2026, 3, 14, 11, 0)) == \
        "Hochzeiten/2026/2026-03-14 Joel & Viki"


def test_nas_subpath_hochzeit_ohne_brautpaar():
    """Die Klammergruppe fällt weg, statt ein hängendes Leerzeichen zu hinterlassen."""
    assert _pfad(_Sess("Hochzeit"), datetime(2026, 6, 6, 14, 0)) == \
        "Hochzeiten/2026/2026-06-06"


def test_nas_subpath_hochzeit_faellt_auf_thema_zurueck():
    """Ohne Brautpaar-Angabe nimmt {brautpaar} das freie Thema."""
    assert _pfad(_Sess("Hochzeit", None, 1, thema="Silberhochzeit"),
                 datetime(2026, 6, 6, 14, 0)) == \
        "Hochzeiten/2026/2026-06-06 Silberhochzeit/1. Teil"


def test_nas_folder_sonstiges_eigener_bereich():
    """'Sonstiges' gehört nicht zu den Gottesdiensten, sondern in den eigenen Jahresbereich."""
    assert _pfad(_Sess("Sonstiges"), datetime(2026, 2, 3, 14, 0)) == \
        "Sonstige Aufnahmen/2026/2026-02-03"
