"""Tracks bearbeiten: Kanten, Einfügen, Teilen, Unterbrechen (api/routers/spur.py)."""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
_API = os.path.join(os.path.dirname(__file__), "..", "api")
if _API not in sys.path:
    sys.path.insert(0, _API)


@pytest_asyncio.fixture
async def db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def ohne_ki_live():
    """Keine Hintergrund-Auswertung im Test; feste Beitragsarten, egal was andere Tests laden."""
    import typen
    arten = ["Gebet", "Predigt", "Chorlied", "Ansage", "Musikstück", "Unbenannt"]
    with patch("ki_live.einreihen"), \
         patch.object(typen, "aktuell", MagicMock(beitragsarten_gueltig=arten)):
        yield


async def _gottesdienst(db, status="stopped"):
    from models import Marker, RecordingSession
    start = datetime(2026, 11, 8, 9, 30)
    sess = RecordingSession(service_type="Sonntagmorgen", status=status, started_at=start,
                            stopped_at=start + timedelta(seconds=600) if status == "stopped" else None,
                            wav_path="/gibt/es/nicht.wav")
    db.add(sess)
    await db.commit()
    m = {}
    for typ, von, bis in [("Gebet", 10.0, 60.0), ("Predigt", 100.0, 400.0), ("Chorlied", 420.0, 600.0)]:
        m[typ] = Marker(session_id=sess.id, marker_type=typ, placed_at_ts=von, ended_at_ts=bis,
                        placed_at_wall=start + timedelta(seconds=von))
        db.add(m[typ])
    await db.commit()
    return sess, m


def test_luecken_und_teile():
    from routers.segments import luecken_und_teile
    def L(von, bis):
        return MagicMock(von_s=von, bis_s=bis)
    luecken, teile = luecken_und_teile(100.0, 400.0, [L(200, 260), L(50, 110), L(390, None)])
    assert luecken == [[100.0, 110.0], [200.0, 260.0], [390.0, 400.0]]
    assert teile == [[110.0, 200.0], [260.0, 390.0]]


@pytest.mark.asyncio
async def test_kanten_genau_setzen(db):
    from routers.spur import Kanten, kanten_setzen
    from routers.segments import _build_segments
    sess, m = await _gottesdienst(db)
    await kanten_setzen(m["Predigt"].id, Kanten(start_s=97.3, ende_s=405.6), db)
    seg = next(s for s in await _build_segments(sess, db) if s["marker_id"] == m["Predigt"].id)
    assert (seg["start_seconds"], seg["end_seconds"]) == (97.3, 405.6)
    assert m["Predigt"].nudge_manual


@pytest.mark.asyncio
async def test_einfuegen_mit_festem_ende_und_hinweis_bei_export(db):
    from models import SegmentExport
    from routers.spur import NeuerTrack, track_einfuegen
    from routers.segments import _build_segments
    sess, m = await _gottesdienst(db)
    db.add(SegmentExport(session_id=sess.id, marker_id=m["Gebet"].id, marker_type="Gebet",
                         start_seconds=10, end_seconds=60, mp3_path="x", mp3_filename="x.mp3"))
    await db.commit()
    r = await track_einfuegen(sess.id, NeuerTrack(marker_type="Ansage", start_s=62.0, ende_s=95.0), db)
    assert r["hinweis"]
    typen_folge = [s["marker_type"] for s in await _build_segments(sess, db)]
    assert typen_folge == ["Gebet", "Ansage", "Predigt", "Chorlied"]


@pytest.mark.asyncio
async def test_teilen_ohne_ueberlappung_und_luecken_wandern_mit(db):
    from models import MarkerLuecke
    from routers.spur import Teilen, track_teilen
    from routers.segments import _build_segments
    sess, m = await _gottesdienst(db)
    db.add(MarkerLuecke(marker_id=m["Chorlied"].id, von_s=500.0, bis_s=520.0))
    await db.commit()
    r = await track_teilen(m["Chorlied"].id, Teilen(bei_s=440.0, marker_type="Musikstück"), db)
    segs = {s["marker_id"]: s for s in await _build_segments(sess, db)}
    vorne, hinten = segs[r["vorne"]], segs[r["hinten"]]
    assert (vorne["start_seconds"], vorne["end_seconds"]) == (420.0, 440.0)
    assert (hinten["start_seconds"], hinten["end_seconds"]) == (440.0, 600.0)
    assert hinten["marker_type"] == "Musikstück"
    assert hinten["luecken"] == [[500.0, 520.0]] and vorne["luecken"] == []
    assert hinten["duration_seconds"] == 140.0


@pytest.mark.asyncio
async def test_teilen_ausserhalb_abgelehnt(db):
    from fastapi import HTTPException
    from routers.spur import Teilen, track_teilen
    _, m = await _gottesdienst(db)
    with pytest.raises(HTTPException) as e:
        await track_teilen(m["Gebet"].id, Teilen(bei_s=80.0), db)
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_unterbrechen_und_fortsetzen_waehrend_der_aufnahme(db):
    from models import Marker
    from routers import spur
    from routers.segments import _build_segments
    sess, m = await _gottesdienst(db, status="recording")
    offen = Marker(session_id=sess.id, marker_type="Predigt", placed_at_ts=700.0,
                   placed_at_wall=datetime.now())
    db.add(offen)
    await db.commit()

    with patch.object(spur, "_aktuelle_stelle", AsyncMock(return_value=750.0)):
        await spur.track_unterbrechen(db)
    with patch.object(spur, "_aktuelle_stelle", AsyncMock(return_value=760.0)):
        with pytest.raises(Exception):
            await spur.track_unterbrechen(db)            # schon unterbrochen
    with patch.object(spur, "_aktuelle_stelle", AsyncMock(return_value=812.5)):
        r = await spur.track_fortsetzen(db)
    assert (r["von_s"], r["bis_s"]) == (750.0, 812.5)

    offen.ended_at_ts = 900.0
    await db.commit()
    seg = next(s for s in await _build_segments(sess, db) if s["marker_id"] == offen.id)
    assert seg["teile"] == [[700.0, 750.0], [812.5, 900.0]]
    assert seg["duration_seconds"] == 137.5
