"""Tests for /api/sessions/{id}/segments and /api/sessions/{id}/segments/{marker_id}/export.

Uses in-memory SQLite and monkeypatches httpx.AsyncClient to mock the audio_service.
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
    """export_segment with mocked audio_service creates exactly one SegmentExport row."""
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
    """When audio_service returns nas.status='failed', SegmentExport row has nas_status='failed' and nas_error."""
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


# ─── Tests: fortlaufende Nummer auf dem NAS ────────────────────────────────

async def _gottesdienst_mit_luecken(db):
    """Orchester, Gemeindegesang, Predigt, Gebet, Chorlied — zwei davon gehen nicht mit."""
    started = datetime(2026, 10, 18, 9, 30, 0)
    sess = await _make_session(db, started_at=started, stopped_at=started + timedelta(seconds=400),
                               service_type="Sonntagmorgen")
    marker = {}
    for typ, ts in [("Orchester", 10.0), ("Gemeindegesang", 60.0), ("Predigt", 120.0),
                    ("Gebet", 200.0), ("Chorlied", 260.0)]:
        marker[typ] = await _make_marker(db, sess.id, typ, placed_at_ts=ts)
    return sess, marker


@pytest.mark.asyncio
async def test_nas_nummer_fortlaufend_ohne_luecken(db_session):
    """Das Archiv zählt Track-01, -02, … ohne Lücken; nicht exportierte Beiträge stehen dahinter."""
    from routers.segments import _build_segments

    sess, _ = await _gottesdienst_mit_luecken(db_session)
    segs = await _build_segments(sess, db_session)

    assert [s["index"] for s in segs] == [1, 2, 3, 4, 5]
    assert {s["marker_type"]: s["nas_nr"] for s in segs} == {
        "Orchester": 1, "Predigt": 2, "Chorlied": 3,
        "Gemeindegesang": 4, "Gebet": 5,
    }


@pytest.mark.asyncio
async def test_export_nutzt_nas_nummer_fuer_track_und_id3(db_session):
    """Auf dem NAS Track-02 samt ID3-Nummer 2/3; die lokale Datei behält ihre Markernummer 03."""
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    await db_session.commit()

    audio_resp = {
        "export": {"mp3_path": "/exports/x.mp3", "duration_ms": 5000, "frame_rate": 48000},
        "nas": {"status": "skipped", "error": None, "nas_path": None, "uploaded_at": None},
        "mp3_filename": "x.mp3",
        "mp3_path": "/exports/x.mp3",
    }
    client = _mock_audio_client(audio_resp)
    mock_request = MagicMock()
    mock_request.headers = {}

    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, db=db_session)

    payload = client.post.call_args.kwargs["json"]
    assert payload["remote_subpath"] == \
        "Gottesdienste/2026-10-18 Sonntag 1. morgens/Track-02 - Predigt.mp3"
    assert payload["tags"]["track"] == "2/3"
    assert "_03_Predigt" in payload["mp3_filename"]


@pytest.mark.asyncio
async def test_export_schickt_die_klangklasse_mit(db_session):
    """Der Audio-Dienst wählt die Ziellautheit nach der Klangklasse — die API kennt sie."""
    from models import MarkerType
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    db_session.add_all([MarkerType(name="Chorlied", klang_klasse="musik"),
                        MarkerType(name="Predigt", klang_klasse="sprache")])
    await db_session.commit()

    audio_resp = {
        "export": {"mp3_path": "/exports/x.mp3", "duration_ms": 5000, "frame_rate": 48000},
        "nas": {"status": "skipped", "error": None, "nas_path": None, "uploaded_at": None},
        "mp3_filename": "x.mp3",
        "mp3_path": "/exports/x.mp3",
    }
    mock_request = MagicMock()
    mock_request.headers = {}
    gesehen = {}
    for typ in ("Chorlied", "Predigt", "Orchester"):
        client = _mock_audio_client(audio_resp)
        with patch("httpx.AsyncClient", return_value=client):
            await export_segment(session_id=sess.id, marker_id=marker[typ].id,
                                 request=mock_request, db=db_session)
        payload = client.post.call_args.kwargs["json"]
        gesehen[typ] = (payload["klang_klasse"], payload["marker_type"])

    assert gesehen == {"Chorlied": ("musik", "Chorlied"), "Predigt": ("sprache", "Predigt"),
                       "Orchester": (None, "Orchester")}   # nicht in der Tabelle → None


# ─── Tests: Neu erkennen ───────────────────────────────────────────────────

async def _auto(db, marker, typ=None):
    from models import AutoMarker
    db.add(AutoMarker(marker_id=marker.id, typ=typ or marker.marker_type))
    await db.commit()


async def _beende(db, marker, ende):
    marker.ended_at_ts = ende
    await db.commit()


@pytest.mark.asyncio
async def test_neu_erkennen_ersetzt_nur_unberuehrte_automatik(db_session, tmp_path):
    """Ersetzt wird nur, was die Automatik gesetzt und niemand angefasst hat."""
    from sqlalchemy import select
    from models import AutoMarker, Marker, SegmentExport
    from routers.segments import neu_erkennen_session

    started = datetime(2026, 10, 18, 9, 38, 0)
    sess = await _make_session(db_session, started_at=started,
                               stopped_at=started + timedelta(seconds=800),
                               wav_path=str(tmp_path / "aufnahme.wav"), service_type="Sonntagmorgen")

    auto = await _make_marker(db_session, sess.id, "Chorlied", placed_at_ts=10.0)
    await _beende(db_session, auto, 90.0)
    await _auto(db_session, auto)

    await _make_marker(db_session, sess.id, "Chorlied", placed_at_ts=200.0)   # von Hand, ohne Ende

    umbenannt = await _make_marker(db_session, sess.id, "Gedicht", placed_at_ts=400.0)
    await _beende(db_session, umbenannt, 450.0)
    await _auto(db_session, umbenannt, typ="Chorlied")

    exportiert = await _make_marker(db_session, sess.id, "Predigt", placed_at_ts=500.0)
    await _beende(db_session, exportiert, 550.0)
    await _auto(db_session, exportiert)
    db_session.add(SegmentExport(session_id=sess.id, marker_id=exportiert.id, marker_type="Predigt",
                                 start_seconds=500.0, end_seconds=550.0,
                                 mp3_path=str(tmp_path / "x.mp3"), mp3_filename="x.mp3"))
    await db_session.commit()

    erkannt = {"beitraege": [
        {"start_s": 12.0, "ende_s": 95.0, "typ": "Orchester", "exportieren": True},
        {"start_s": 195.0, "ende_s": 300.0, "typ": "Chorlied", "exportieren": True},     # auf der Hand
        {"start_s": 310.0, "ende_s": 380.0, "typ": "?", "exportieren": True},
        {"start_s": 600.0, "ende_s": 700.0, "typ": "Gemeindegesang", "exportieren": False},
    ]}
    with patch("httpx.AsyncClient", return_value=_mock_audio_client(erkannt)):
        ergebnis = await neu_erkennen_session(sess, db_session)

    assert ergebnis == (1, 2, 3)
    marker = (await db_session.execute(
        select(Marker).where(Marker.session_id == sess.id).order_by(Marker.placed_at_ts)
    )).scalars().all()
    assert [(m.placed_at_ts, m.ended_at_ts, m.marker_type) for m in marker] == [
        (12.0, 95.0, "Orchester"),
        (200.0, None, "Chorlied"),
        (310.0, 380.0, "Unbenannt"),
        (400.0, 450.0, "Gedicht"),
        (500.0, 550.0, "Predigt"),
    ]
    assert auto.id not in {m.id for m in marker}
    neue_auto = (await db_session.execute(select(AutoMarker))).scalars().all()
    assert {a.typ for a in neue_auto if a.marker_id in {marker[0].id, marker[2].id}} == \
        {"Orchester", "Unbenannt"}
    # Der Ordner der Aufnahme erklärt sich weiter selbst
    assert (tmp_path / "marker.json").exists()


@pytest.mark.asyncio
async def test_neu_erkennen_erst_nach_dem_stopp(db_session, tmp_path):
    from fastapi import HTTPException
    from routers.segments import neu_erkennen

    sess = await _make_session(db_session, wav_path=str(tmp_path / "aufnahme.wav"),
                               status="recording")
    mock_request = MagicMock()
    mock_request.headers = {}
    with pytest.raises(HTTPException) as fehler:
        await neu_erkennen(session_id=sess.id, request=mock_request, db=db_session)
    assert fehler.value.status_code == 409



# ─── Tests: Dateiname und Metadaten auf dem NAS ────────────────────────────

def test_nas_dateiname_traegt_den_typ_ohne_verbotene_zeichen():
    from routers.segments import _nas_track_name
    assert _nas_track_name(3, "Kinderchor") == "Track-03 - Kinderchor.mp3"
    assert _nas_track_name(12, 'Lied: "Wer/was?"') == "Track-12 - Lied Werwas.mp3"
    assert _nas_track_name(1, "...") == "Track-01 - Beitrag.mp3"


def test_album_ohne_bereich_und_jahresordner():
    from routers.segments import _album
    assert _album("Gottesdienste/2026-10-18 Sonntag 1. morgens") == "2026-10-18 Sonntag 1. morgens"
    assert _album("Hochzeiten/2026/2026-06-06 Robin & Laura/2. Teil") == \
        "2026-06-06 Robin & Laura – 2. Teil"
    assert _album("Sonstige Aufnahmen/2026/2026-02-03") == "2026-02-03"


@pytest.mark.asyncio
async def test_export_tags_mit_interpret_und_ki_kommentar(db_session):
    """Interpret aus der Einstellung, Kommentar nur aus einer vorhandenen KI-Auswertung."""
    import json
    from models import SegmentInsight, Setting
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    db_session.add(Setting(key="export_interpret", value="Testgemeinde"))
    db_session.add(SegmentInsight(session_id=sess.id, marker_id=marker["Predigt"].id,
                                  summary="Über die Hoffnung.",
                                  bible_refs=json.dumps(["Römer 8,28", "Psalm 23"])))
    await db_session.commit()

    audio_resp = {
        "export": {"mp3_path": "/exports/x.mp3", "duration_ms": 5000, "frame_rate": 48000},
        "nas": {"status": "skipped", "error": None, "nas_path": None, "uploaded_at": None},
        "mp3_filename": "x.mp3",
        "mp3_path": "/exports/x.mp3",
    }
    mock_request = MagicMock()
    mock_request.headers = {}

    client = _mock_audio_client(audio_resp)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, db=db_session)
    tags = client.post.call_args.kwargs["json"]["tags"]
    assert tags == {
        "title": "Predigt",
        "album": "2026-10-18 Sonntag 1. morgens",
        "track": "2/3",
        "date": "2026-10-18",
        "genre": "Gottesdienst",
        "language": "deu",
        "artist": "Testgemeinde",
        "album_artist": "Testgemeinde",
        "comment": "Über die Hoffnung.\n\nRömer 8,28, Psalm 23",
    }

    # Ohne KI-Auswertung kein Kommentar
    client = _mock_audio_client(audio_resp)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Orchester"].id,
                             request=mock_request, db=db_session)
    assert "comment" not in client.post.call_args.kwargs["json"]["tags"]


# ─── Tests: CopyCenter ─────────────────────────────────────────────────────

def _cc_client(export_resp: dict, freier_ordner: str = "CopyCenter/2026_mp3/Gottesd_261018_So_mp3_a"):
    """Audio-Dienst-Attrappe: nas-erster-freier gibt einen Ordner, segment-export export_resp."""
    mock_client = AsyncMock()

    async def post(url, json=None, **_):
        if url.endswith("/nas-erster-freier"):
            return _mock_response(200, {"folder": freier_ordner})
        return _mock_response(200, export_resp)
    mock_client.post.side_effect = post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


_CC_EXPORT = {
    "export": {"mp3_path": "/exports/x.mp3", "duration_ms": 5000, "frame_rate": 48000},
    "nas": {"status": "uploaded", "error": None, "nas_path": "\\\\srv\\x", "uploaded_at": "2026-10-18T12:00:00"},
    "copycenter": {"status": "uploaded", "error": None, "nas_path": "\\\\srv\\y", "uploaded_at": None},
    "mp3_filename": "x.mp3",
    "mp3_path": "/exports/x.mp3",
}


def _export_aufrufe(client):
    return [c.kwargs["json"] for c in client.post.call_args_list if c.args[0].endswith("/segment-export")]


@pytest.mark.asyncio
async def test_copycenter_ordner_wird_einmal_entschieden(db_session):
    """Erster Track entscheidet den Ordner, der zweite bekommt denselben — ohne neue Suche."""
    from models import CopyCenterAblage, Setting
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    db_session.add(Setting(key="nas_copycenter", value="1"))
    await db_session.commit()
    mock_request = MagicMock()
    mock_request.headers = {}

    client = _cc_client(_CC_EXPORT)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Orchester"].id,
                             request=mock_request, db=db_session)
    erster = _export_aufrufe(client)[0]
    assert erster["copycenter_folder"] == "CopyCenter/2026_mp3/Gottesd_261018_So_mp3_a"
    assert erster["copycenter_label"] == "Gottesdienst"
    assert (await db_session.get(CopyCenterAblage, sess.id)).ordner == erster["copycenter_folder"]

    client = _cc_client(_CC_EXPORT, freier_ordner="CopyCenter/2026_mp3/FALSCH")
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, db=db_session)
    assert _export_aufrufe(client)[0]["copycenter_folder"] == erster["copycenter_folder"]
    assert not any(c.args[0].endswith("/nas-erster-freier") for c in client.post.call_args_list)


@pytest.mark.asyncio
async def test_copycenter_nicht_fuer_hochzeit_und_nicht_wenn_abgeschaltet(db_session, tmp_path):
    """Hochzeiten nie; Gottesdienste nur, wenn eingeschaltet — der Standard ist aus."""
    from models import Setting
    from routers.segments import export_segment

    mock_request = MagicMock()
    mock_request.headers = {}
    db_session.add(Setting(key="nas_copycenter", value="1"))
    await db_session.commit()

    hochzeit = await _make_session(db_session, started_at=datetime(2026, 6, 6, 14, 0),
                                   stopped_at=datetime(2026, 6, 6, 15, 0), service_type="Hochzeit",
                                   couple_name="A & B", wav_path=str(tmp_path / "h.wav"))
    hochzeit.nas_folder = "Hochzeiten/2026/2026-06-06 A & B"
    m = await _make_marker(db_session, hochzeit.id, "Predigt", placed_at_ts=10.0)
    client = _cc_client(_CC_EXPORT)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=hochzeit.id, marker_id=m.id, request=mock_request, db=db_session)
    assert "copycenter_folder" not in _export_aufrufe(client)[0]

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    await db_session.delete(await db_session.get(Setting, "nas_copycenter"))   # zurück auf Standard
    await db_session.commit()
    client = _cc_client(_CC_EXPORT)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, db=db_session)
    assert "copycenter_folder" not in _export_aufrufe(client)[0]


@pytest.mark.asyncio
async def test_copycenter_fehler_ist_sichtbar_aber_archiv_bleibt_hochgeladen(db_session):
    from sqlalchemy import select
    from models import SegmentExport, Setting
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    db_session.add(Setting(key="nas_copycenter", value="1"))
    await db_session.commit()
    kaputt = dict(_CC_EXPORT, copycenter={"status": "failed", "error": "kein Platz",
                                          "nas_path": None, "uploaded_at": None})
    mock_request = MagicMock()
    mock_request.headers = {}
    with patch("httpx.AsyncClient", return_value=_cc_client(kaputt)):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, db=db_session)
    row = (await db_session.execute(select(SegmentExport))).scalars().one()
    assert row.nas_status == "uploaded"
    assert row.nas_error == "CopyCenter fehlgeschlagen: kein Platz"


# ─── Tests: Online stellen (Gemeinde-App) ──────────────────────────────────

def test_online_eintrag_wie_im_importskript_der_gemeinde_app():
    """Schlüssel und Ereignis müssen exakt so aussehen wie aus Church_Tool/build_meta.py —
    sonst steht derselbe Gottesdienst doppelt im Archiv der App."""
    import online

    auswertung = MagicMock(title="Die Größe Gottes", summary="Über Psalm 8.",
                           bible_refs='["Psalm 8,4"]', speaker="", transcript="Text")
    e = online.eintrag("Gottesdienste/2026-10-18 Sonntag 2. abends", "Track-03 - Predigt.mp3",
                       "Predigt", 3, auswertung)
    assert e["event"] == {"date": "2026-10-18", "title": "Gottesdienst — Sonntag abends, 18.10.2026",
                          "event_type": "gottesdienst", "slot": "sonntag-abends"}
    assert e["recording"]["file_key"] == "archive/2026/10/18/sonntag-abends/Track-03_-_Predigt.mp3"
    assert e["recording"]["title"] == "Predigt: Die Größe Gottes"
    assert e["recording"]["recording_type"] == "predigt"
    assert e["recording"]["bible_reference"] == "Psalm 8,4"

    bibelstunde = online.ereignis_aus_ordner("2026-10-13 Dienstag 2. abends")
    assert (bibelstunde["event_type"], bibelstunde["slot"]) == ("bibelstunde", "dienstag-abends")
    ohne = online.eintrag("Gottesdienste/2026-10-18 Sonntag 2. abends", "Track-07 - Chorlied.mp3",
                          "Chorlied", 7, None)
    assert (ohne["recording"]["title"], ohne["recording"]["recording_type"]) == ("Chorlied · Track 07", "chor")
    # Arten so, wie die App sie live führt — nicht "sonstiges"
    assert online.APP_ART["Prosa"] == "prosa" and online.APP_ART["Orchester"] == "orchester"
    assert online.APP_ART["Gemeindegesang"] == "allgemeingesang" and online.APP_ART["Vermeldungen"] == "ansage"
    erlaubt = {"predigt", "chor", "gruppenlied", "orchester", "instrumental", "allgemeingesang", "lied",
               "gedicht", "zeugnis", "gebet", "prosa", "ansage", "sonstiges"}
    assert set(online.APP_ART.values()) <= erlaubt      # sonst lehnt der Sammelimport ab
    assert online.eintrag("Hochzeiten/2026/2026-06-06 A & B", "Track-01 - Predigt.mp3",
                          "Predigt", 1, None) is None


def test_online_gottesdienstart_schlaegt_den_wochentag():
    """Gebetsstunde am Samstag bleibt Gebetsstunde, ein Fest behält seinen Namen —
    aber der Slot und damit der file_key ändern sich nie."""
    import online
    samstag = online.eintrag("Gottesdienste/2026-01-10 Samstag 2. abends", "Track-01 - Predigt.mp3",
                             "Predigt", 1, None, "Gebetsstunde", None)
    assert samstag["event"]["event_type"] == "gebetsstunde"
    assert samstag["event"]["title"] == "Gebetsstunde — Samstag abends, 10.01.2026"
    fest = online.eintrag("Gottesdienste/2026-10-04 Sonntag 1. morgens", "Track-02 - Chorlied.mp3",
                          "Chorlied", 2, None, "Erntedankfest", None)
    assert fest["event"]["title"] == "Erntedankfest — Sonntag morgens, 04.10.2026"
    assert fest["recording"]["file_key"] == "archive/2026/10/04/sonntag-morgens/Track-02_-_Chorlied.mp3"
    mit_thema = online.eintrag("Gottesdienste/2026-10-18 Sonntag 2. abends", "Track-01 - Predigt.mp3",
                               "Predigt", 1, None, "Sonntagabend", "Orchesterleiter Abschluss")
    assert mit_thema["event"]["title"].startswith("Orchesterleiter Abschluss — Sonntag abends")
    normal = online.eintrag("Gottesdienste/2026-10-18 Sonntag 2. abends", "Track-01 - Predigt.mp3",
                            "Predigt", 1, None, "Sonntagabend", None)
    assert normal["event"]["title"] == "Gottesdienst — Sonntag abends, 18.10.2026"


def test_online_jungverheiratete_als_thema_mit_tag():
    """Wie die Abende seit 2014 im Archiv: Art Thema, Titel nur das Thema, Tag dazu —
    der Slot und damit der file_key bleiben die aus dem Ordner."""
    import online
    e = online.eintrag("Gottesdienste/2026-11-07 Samstag 2. abends", "Track-03 - Predigt.mp3",
                       "Predigt", 3, None, "Jungverheiratete", "Starke Ehen")
    assert e["event"]["event_type"] == "thema"
    assert e["event"]["title"] == "Starke Ehen"
    assert e["event"]["tags"] == ["thema-jungverheiratete"]
    assert e["recording"]["file_key"] == "archive/2026/11/07/samstag-abends/Track-03_-_Predigt.mp3"
    ohne = online.eintrag("Gottesdienste/2026-11-07 Samstag 2. abends", "Track-01 - Predigt.mp3",
                          "Predigt", 1, None, "Jungverheiratete", None)
    assert ohne["event"]["title"] == "Jungverheiratete"


@pytest.mark.asyncio
async def test_online_und_station_je_export_waehlbar(db_session):
    """Ohne Angabe gilt die Einstellung; der Schalter beim Export geht vor."""
    from models import OnlineAblage, Setting
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    db_session.add(Setting(key="nas_copycenter", value="1"))
    await db_session.commit()
    mock_request = MagicMock()
    mock_request.headers = {}

    antwort = {**_CC_EXPORT, "online": {"status": "uploaded", "file_key": "archive/x.mp3", "error": None}}
    client = _cc_client(antwort)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, station=False, online=True, db=db_session)
    aufruf = _export_aufrufe(client)[0]
    assert "copycenter_folder" not in aufruf
    assert aufruf["online"]["recording"]["file_key"].startswith(
        "archive/2026/10/18/sonntag-morgens/Track-")
    assert aufruf["online_ersetzen"] is False
    assert (await db_session.get(OnlineAblage, marker["Predigt"].id)).status == "uploaded"

    client = _cc_client(antwort)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, db=db_session)
    aufruf = _export_aufrufe(client)[0]
    assert "online" not in aufruf                 # Standard ist aus
    assert "copycenter_folder" in aufruf          # Standard hier an

    client = _cc_client(antwort)
    with patch("httpx.AsyncClient", return_value=client):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, online=True, db=db_session)
    assert _export_aufrufe(client)[0]["online_ersetzen"] is True   # eigener Upload darf ersetzt werden


# ─── Tests: Statuszeile der Dienste ────────────────────────────────────────

@pytest.mark.asyncio
async def test_dienste_status_audio_weg_und_ollama_ohne_modell():
    import httpx as _httpx
    from routers import dienste

    req = MagicMock()
    req.headers = {}

    async def weg(*a, **k):
        raise _httpx.ConnectError("weg")
    with patch.object(dienste, "_audio", weg):
        r = await dienste.status(req)
    assert r["alle_ok"] is False and r["pruefungen"][0]["dienst"] == "audio"

    antwort = MagicMock()
    antwort.json.return_value = {"pruefungen": [
        {"dienst": "eingang", "ok": True, "grund": None, "detail": "Dante"},
        {"dienst": "ollama", "ok": False, "grund": "modell_fehlt", "detail": "qwen2.5:14b-instruct"}]}

    async def da(*a, **k):
        return antwort
    with patch.object(dienste, "_audio", da):
        r = await dienste.status(req)
    assert r["alle_ok"] is False
    ollama = [p for p in r["pruefungen"] if p["dienst"] == "ollama"][0]
    assert ollama["grund_text"] == "Modell fehlt"


# ─── Tests: Vorschlag aus dem Kalender ─────────────────────────────────────

def test_kalender_vorschlag_morgens_mit_thema():
    from datetime import datetime as _dt
    import kalender
    arten = ["Sonntagmorgen", "Sonntagabend", "Gebetsstunde", "Bibelstunde", "Erntedankfest"]
    termine = [
        {"id": "m", "title": "Thema Psalm 22 – Gottesdienst", "start_time": "10:00",
         "event_type": "gottesdienst", "description": "", "tageszeit": "morgens", "type_label": ""},
        {"id": "a", "title": "Gottesdienst", "start_time": "17:00", "event_type": "gottesdienst",
         "description": "Thema: Erntedank", "tageszeit": "abends", "type_label": ""},
    ]
    morgens = kalender.vorschlag(termine, _dt(2026, 11, 15, 9, 30), arten)
    assert (morgens["service_type"], morgens["thema"], morgens["event_id"]) == ("Sonntagmorgen", "Psalm 22", "m")
    mittags = kalender.vorschlag(termine, _dt(2026, 11, 15, 13, 0), arten)
    assert (mittags["service_type"], mittags["thema"]) == ("Sonntagabend", "Erntedank")
    assert kalender.vorschlag([], _dt(2026, 11, 15, 9, 0), arten) is None
    fest = kalender.vorschlag([{"id": "f", "title": "Erntedankfest", "start_time": "10:00", "event_type": "andere",
                                "type_label": "Erntedankfest", "tageszeit": "morgens"}], _dt(2026, 10, 4, 10, 0), arten)
    assert fest["service_type"] == "Erntedankfest"


@pytest.mark.asyncio
async def test_ki_auswertung_laeuft_vor_dem_export(db_session):
    """Fehlt sie, wird sie geholt — und ein Fehler dabei hält den Export nicht auf."""
    from models import SegmentInsight
    from routers.segments import export_segment

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    sess.nas_folder = "Gottesdienste/2026-10-18 Sonntag 1. morgens"
    await db_session.commit()
    mock_request = MagicMock()
    mock_request.headers = {}
    gefragt = []

    def client_mit(insights_antwort):
        client = AsyncMock()

        async def post(url, json=None, **_):
            if url.endswith("/api/session/insights"):
                gefragt.append(url)
                return insights_antwort
            return _mock_response(200, _CC_EXPORT)
        client.post.side_effect = post
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    gut = _mock_response(200, {"titel": "Die Größe Gottes", "zusammenfassung": "…", "bibelstellen": ["Ps 8"]})
    with patch("httpx.AsyncClient", return_value=client_mit(gut)):
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, online=False, db=db_session)
    from sqlalchemy import select
    row = (await db_session.execute(select(SegmentInsight).where(
        SegmentInsight.marker_id == marker["Predigt"].id))).scalar_one()
    assert row.title == "Die Größe Gottes" and len(gefragt) == 1

    with patch("httpx.AsyncClient", return_value=client_mit(gut)):       # schon da → nicht noch einmal
        await export_segment(session_id=sess.id, marker_id=marker["Predigt"].id,
                             request=mock_request, online=False, db=db_session)
    assert len(gefragt) == 1

    kaputt = _mock_response(503, {"detail": "Ollama weg"})
    with patch("httpx.AsyncClient", return_value=client_mit(kaputt)):
        r = await export_segment(session_id=sess.id, marker_id=marker["Chorlied"].id,
                                 request=mock_request, online=False, db=db_session)
    assert r["nas_status"] == "uploaded"                                 # trotzdem exportiert


@pytest.mark.asyncio
async def test_export_check_zeigt_alles_und_legt_nichts_fest(db_session):
    """Die Vorschau nennt Dateien, Ordner und App-Eintrag wie der Export — und merkt
    sich dabei weder einen NAS-Ordner noch ein CopyCenter."""
    from models import CopyCenterAblage
    from routers.export_check import export_check

    sess, _ = await _gottesdienst_mit_luecken(db_session)
    sess.thema = "Erntedank"
    await db_session.commit()

    d = await export_check(sess.id, ids="", station=True, online=True, db=db_session)

    assert [t["typ"] for t in d["tracks"]] == ["Orchester", "Predigt", "Chorlied"]
    assert [u["typ"] for u in d["uebersprungen"]] == ["Gemeindegesang", "Gebet"]
    predigt = d["tracks"][1]
    assert predigt["datei"] == "2026-10-18_Sonntagmorgen_Erntedank_03_Predigt.mp3"
    assert predigt["nas_datei"] == "Track-02 - Predigt.mp3"
    assert d["nas"]["ordner"].startswith("Gottesdienste/2026-10-18")
    assert d["online"]["ereignis"]["title"].startswith("Erntedank — Sonntag morgens")
    assert d["station"]["beschriftung"] == "Erntedank"
    assert d["gottesdienst"]["art"] == "Sonntagmorgen"

    await db_session.refresh(sess)
    assert sess.nas_folder is None
    assert await db_session.get(CopyCenterAblage, sess.id) is None


@pytest.mark.asyncio
async def test_export_check_einzeln_mit_hinweisen(db_session):
    """Einzeln geht auch, was sonst übersprungen wird — mit Hinweis darauf."""
    from routers.export_check import export_check

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    d = await export_check(sess.id, ids=str(marker["Gebet"].id), station=False, online=False, db=db_session)
    assert [t["typ"] for t in d["tracks"]] == ["Gebet"]
    assert "Dieser Typ wird sonst nicht exportiert" in d["tracks"][0]["hinweise"]
    assert d["online"]["an"] is False and d["online"]["ereignis"] is None


@pytest.mark.asyncio
async def test_angaben_im_export_check_aendern(db_session):
    """Art und Thema lassen sich vor dem Export richtigstellen; Titel und Sprecher erst,
    wenn es eine Auswertung gibt."""
    from fastapi import HTTPException
    from models import SegmentInsight
    from routers.export_check import Angaben, TrackAngaben, angaben_aendern, track_angaben_aendern

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    r = await angaben_aendern(sess.id, Angaben(service_type="Gebetsstunde", thema="  Starke Ehen "), db_session)
    assert r == {"service_type": "Gebetsstunde", "thema": "Starke Ehen"}
    with pytest.raises(HTTPException) as e:
        await angaben_aendern(sess.id, Angaben(service_type="Gibtsnicht"), db_session)
    assert e.value.status_code == 422

    predigt = marker["Predigt"].id
    # Ohne Auswertung entsteht ein Eintrag nur mit Handangaben — die KI gilt als nicht gelaufen
    r = await track_angaben_aendern(sess.id, predigt, TrackAngaben(sprecher="P. F."), db_session)
    assert r["sprecher"] == "P. F."
    zeile = (await db_session.execute(__import__("sqlalchemy").select(SegmentInsight).where(
        SegmentInsight.marker_id == predigt))).scalar_one()
    assert zeile.model == "hand" and not zeile.ki_gelaufen and zeile.sprecher_von_hand
    r = await track_angaben_aendern(sess.id, predigt, TrackAngaben(titel="Neu", sprecher="P. F."), db_session)
    assert (r["titel"], r["sprecher"]) == ("Neu", "P. F.")
    assert zeile.titel_von_hand


@pytest.mark.asyncio
async def test_angaben_nicht_waehrend_der_aufnahme(db_session):
    from fastapi import HTTPException
    from routers.export_check import Angaben, angaben_aendern

    sess, _ = await _gottesdienst_mit_luecken(db_session)
    sess.status = "recording"
    await db_session.commit()
    with pytest.raises(HTTPException) as e:
        await angaben_aendern(sess.id, Angaben(thema="x"), db_session)
    assert e.value.status_code == 409



@pytest.mark.asyncio
async def test_typwechsel_nach_stopp_zieht_auswahl_und_nas_ordner_mit(db_session):
    """Die Review verschwand nach einem Typwechsel, weil die Auswahl beim alten Typ blieb."""
    import json as _json
    from models import Setting
    from routers.export_check import Angaben, angaben_aendern
    from service_types import CURRENT_SERVICE_KEY
    from datetime import datetime as _dt
    sess, _ = await _gottesdienst_mit_luecken(db_session)
    sess.status, sess.stopped_at = "stopped", _dt.now()
    sess.nas_folder = "Gottesdienste/alt"
    db_session.add(Setting(key=CURRENT_SERVICE_KEY,
                           value=_json.dumps({"service_type": sess.service_type, "gewaehlt_am": "x"})))
    await db_session.commit()
    neu = next(a for a in __import__("typen").aktuell.gottesdienstarten if a != sess.service_type)

    await angaben_aendern(sess.id, Angaben(service_type=neu), db=db_session)

    await db_session.refresh(sess)
    zeile = (await db_session.execute(__import__("sqlalchemy").select(Setting).where(
        Setting.key == CURRENT_SERVICE_KEY))).scalar_one()
    assert _json.loads(zeile.value)["service_type"] == neu
    assert _json.loads(zeile.value)["gewaehlt_am"] == "x"
    assert sess.nas_folder is None          # noch nichts exportiert → Ordner folgt dem Typ


@pytest.mark.asyncio
async def test_ki_auswertung_laesst_handtitel_stehen(db_session):
    """Von Hand gesetzter Titel gewinnt; die KI füllt den Rest und gilt danach als gelaufen."""
    from routers.export_check import TrackAngaben, track_angaben_aendern
    from routers.insights import create_insight

    sess, marker = await _gottesdienst_mit_luecken(db_session)
    predigt = marker["Predigt"].id
    await track_angaben_aendern(sess.id, predigt, TrackAngaben(titel="Starke Ehen"), db_session)

    client = AsyncMock()
    client.post.return_value = _mock_response(200, {"titel": "KI-Titel", "zusammenfassung": "Z",
                                                    "sprecher": "KI-Sprecher", "model": "qwen"})
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client):
        r = await create_insight(sess.id, predigt, db_session)

    assert r["title"] == "Starke Ehen" and r["titel_von_hand"]
    assert r["speaker"] == "KI-Sprecher" and r["summary"] == "Z" and r["ki"]


@pytest.mark.asyncio
async def test_kantenkorrektur_zieht_nicht_vor_das_ende_des_vorigen(db_session):
    """Zwei Vermeldungen hintereinander: die Suche nach einem leisen Anfang darf den zweiten
    Track nicht in den ersten ziehen."""
    from routers.segments import refine_session
    started = datetime(2026, 11, 8, 9, 30)
    sess = await _make_session(db_session, started_at=started, stopped_at=started + timedelta(seconds=400),
                               service_type="Sonntagmorgen")
    a = await _make_marker(db_session, sess.id, "Vermeldungen", placed_at_ts=100.0)
    a.ended_at_ts = 200.0
    b = await _make_marker(db_session, sess.id, "Vermeldungen", placed_at_ts=205.0)
    b.ended_at_ts = 300.0
    await db_session.commit()

    client = AsyncMock()
    client.post.return_value = _mock_response(200, {"start_delta": -15.0, "end_delta": 0.0})
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client):
        await refine_session(sess, db_session)
    assert a.start_nudge == -15.0
    assert b.start_nudge == -5.0          # bis ans Ende des vorigen, nicht weiter
