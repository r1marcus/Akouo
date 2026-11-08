"""Wie die Automatik ihre Erkennung in Marker überträgt — und wo sie die Hand respektiert."""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


@pytest_asyncio.fixture
async def fabrik():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from models import Base
    import automode_sync

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fabrik = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    automode_sync.zuruecksetzen()
    with patch.object(automode_sync, "AsyncSessionLocal", fabrik), \
         patch("session_dokument.schreiben", AsyncMock()):
        yield fabrik
    automode_sync.zuruecksetzen()
    await engine.dispose()


async def _aufnahme(fabrik, *hand):
    from models import Marker, RecordingSession
    async with fabrik() as db:
        s = RecordingSession(started_at=datetime(2026, 10, 20, 19, 1), wav_path="/tmp/x.wav",
                             service_type="Bibelstunde", status="recording")
        db.add(s)
        await db.commit()
        for typ, ts in hand:
            db.add(Marker(session_id=s.id, marker_type=typ, placed_at_ts=ts,
                          placed_at_wall=datetime(2026, 10, 20, 19, 1)))
        await db.commit()
        return s.id


async def _marker(fabrik, sid):
    from sqlalchemy import select
    from models import Marker
    async with fabrik() as db:
        q = await db.execute(select(Marker).where(Marker.session_id == sid).order_by(Marker.placed_at_ts))
        return [(m.marker_type, m.placed_at_ts, m.ended_at_ts) for m in q.scalars()]


def _zustand(*beitraege, gelesen=400.0):
    return {"aktiv": True, "gelesen_s": gelesen, "beitraege": [
        {"nr": i, "start_s": s, "ende_s": e, "typ": t, "exportieren": True}
        for i, (s, e, t) in enumerate(beitraege, start=1)]}


@pytest.mark.asyncio
async def test_hand_marker_gilt_statt_eines_eigenen_daneben(fabrik):
    """Predigt von Hand bei 1,7 s, die Automatik erkennt ab 3,25 s "Chorlied" — es bleibt
    bei einem Marker, und der heißt weiter Predigt."""
    from automode_sync import _abgleichen

    sid = await _aufnahme(fabrik, ("Predigt", 1.7))
    await _abgleichen(_zustand((3.25, None, "Chorlied")))
    await _abgleichen(_zustand((3.25, None, "Predigt")))      # Typ wird nachgeschärft
    assert await _marker(fabrik, sid) == [("Predigt", 1.7, None)]

    # Ein fehlendes Ende ergänzt die Automatik
    await _abgleichen(_zustand((3.25, 1800.0, "Chorlied"), gelesen=1810.0))
    assert await _marker(fabrik, sid) == [("Predigt", 1.7, 1800.0)]


@pytest.mark.asyncio
async def test_spaeterer_beitrag_bekommt_eigenen_marker(fabrik):
    """Der Hand-Marker meint nur den Beitrag, in dem er liegt — nicht alles danach."""
    from automode_sync import _abgleichen

    sid = await _aufnahme(fabrik, ("Predigt", 1.7))
    await _abgleichen(_zustand((3.25, 1800.0, "Predigt"), (1815.0, None, "Chorlied"), gelesen=1900.0))
    assert await _marker(fabrik, sid) == [("Predigt", 1.7, 1800.0), ("Chorlied", 1815.0, None)]


@pytest.mark.asyncio
async def test_ohne_hand_setzt_die_automatik_und_schaerft_nach(fabrik):
    from automode_sync import _abgleichen

    sid = await _aufnahme(fabrik)
    await _abgleichen(_zustand((12.0, None, "Chorlied")))
    await _abgleichen(_zustand((12.0, 200.0, "Gruppenlied")))
    assert await _marker(fabrik, sid) == [("Gruppenlied", 12.0, 200.0)]


async def _roh(fabrik, sid):
    from sqlalchemy import select
    from models import Marker
    async with fabrik() as db:
        q = await db.execute(select(Marker).where(Marker.session_id == sid))
        return q.scalars().first()


@pytest.mark.asyncio
async def test_vorgezogener_anfang_landet_im_marker(fabrik):
    """Session 37: erst bei 683,25 s bestätigt, dann der leise Einsatz bei 677,5 s
    gefunden — der eigene Marker wandert mit, samt Uhrzeit."""
    from automode_sync import _abgleichen

    sid = await _aufnahme(fabrik)
    await _abgleichen(_zustand((683.25, None, "Chorlied"), gelesen=700.0))
    await _abgleichen(_zustand((677.5, None, "Chorlied"), gelesen=703.0))
    assert await _marker(fabrik, sid) == [("Chorlied", 677.5, None)]
    m = await _roh(fabrik, sid)
    assert m.placed_at_wall == datetime(2026, 10, 20, 19, 1) + timedelta(seconds=677.5)

    # Nach hinten springt er nie
    await _abgleichen(_zustand((680.0, 978.25, "Chorlied"), gelesen=990.0))
    assert await _marker(fabrik, sid) == [("Chorlied", 677.5, 978.25)]


@pytest.mark.asyncio
async def test_von_hand_verschobener_anfang_bleibt(fabrik):
    from automode_sync import _abgleichen
    from models import Marker

    sid = await _aufnahme(fabrik)
    await _abgleichen(_zustand((683.25, None, "Chorlied"), gelesen=700.0))
    async with fabrik() as db:
        m = await db.get(Marker, (await _roh(fabrik, sid)).id)
        m.start_nudge, m.nudge_manual = -10.0, True
        await db.commit()
    await _abgleichen(_zustand((677.5, None, "Chorlied"), gelesen=703.0))
    m = await _roh(fabrik, sid)
    assert (m.placed_at_ts, m.start_nudge) == (683.25, -10.0)


@pytest.mark.asyncio
async def test_umbenannter_marker_behaelt_anfang(fabrik):
    from automode_sync import _abgleichen
    from models import Marker

    sid = await _aufnahme(fabrik)
    await _abgleichen(_zustand((683.25, None, "Chorlied"), gelesen=700.0))
    async with fabrik() as db:
        m = await db.get(Marker, (await _roh(fabrik, sid)).id)
        m.marker_type = "Gruppenlied"
        await db.commit()
    await _abgleichen(_zustand((677.5, None, "Chorlied"), gelesen=703.0))
    assert await _marker(fabrik, sid) == [("Gruppenlied", 683.25, None)]


@pytest.mark.asyncio
async def test_hand_marker_bekommt_keinen_anderen_anfang(fabrik):
    from automode_sync import _abgleichen

    sid = await _aufnahme(fabrik, ("Chorlied", 690.0))
    await _abgleichen(_zustand((683.25, None, "Chorlied"), gelesen=700.0))
    await _abgleichen(_zustand((677.5, None, "Chorlied"), gelesen=703.0))
    assert await _marker(fabrik, sid) == [("Chorlied", 690.0, None)]
