"""Tracks bearbeiten: Kanten genau setzen, nachträglich einfügen, teilen, unterbrechen,
reinhören. Gebraucht von der Großansicht eines Tracks (api/static/spur.js).

Rückmeldung vom 08.11.: Mit ±1 s an einer 56 px hohen Kurve ließ sich ein Liedanfang nicht
sicher setzen, eine vergessene Begrüßung nicht nachtragen, und wer mitten in einem Beitrag
ein Lied auslassen wollte, musste die ganze Aufnahme pausieren — und verlor Ton.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import ki_live
import session_dokument
import typen
from database import get_db
from models import Marker, MarkerLuecke, RecordingSession, SegmentExport, SegmentInsight
from routers.segments import _build_segments, wav_laenge_s
from sprache import uebersetze

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")
MIN_TRACK_S = 0.5

router = APIRouter(prefix="/api/sessions", tags=["spur"])


async def _sitzung(db: AsyncSession, session_id: int) -> RecordingSession:
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


async def _marker(db: AsyncSession, marker_id: int) -> Marker:
    marker = await db.get(Marker, marker_id)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} not found")
    return marker


async def _aktuelle_stelle() -> float:
    """Wo die laufende Aufnahme gerade schreibt (WAV-Sekunden, ohne Pausen)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{AUDIO_SERVICE_URL}/api/session/offset")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if r.status_code != 200:
        raise HTTPException(status_code=409, detail=uebersetze("Keine laufende Aufnahme"))
    return float(r.json()["offset_seconds"])


async def _schon_exportiert(db: AsyncSession, session_id: int) -> bool:
    return (await db.execute(select(SegmentExport.id).where(
        SegmentExport.session_id == session_id).limit(1))).first() is not None


def _laenge(session: RecordingSession) -> Optional[float]:
    return wav_laenge_s(session.wav_path) if session.wav_path else None


def _pruefe_bereich(session: RecordingSession, start: float, ende: Optional[float]) -> None:
    if start < 0:
        raise HTTPException(status_code=422, detail=uebersetze("Der Anfang liegt vor der Aufnahme"))
    if ende is not None and ende - start < MIN_TRACK_S:
        raise HTTPException(status_code=422, detail=uebersetze("Das Ende muss nach dem Anfang liegen"))
    laenge = _laenge(session)
    if laenge is not None and ende is not None and ende > laenge + 2.0 \
            and session.status not in ("recording", "paused"):
        raise HTTPException(status_code=422, detail=uebersetze("Das Ende liegt hinter der Aufnahme"))


def _verschiebt_nummern_hinweis(schon: bool) -> Optional[str]:
    if not schon:
        return None
    hinweis = "Die Tracknummern danach verschieben sich — schon exportierte Tracks neu exportieren, sonst liegen sie doppelt auf dem NAS"  # noqa: E501
    return uebersetze(hinweis)


# ─── Lesen ─────────────────────────────────────────────────────────────────

@router.get("/{session_id}/spuren")
async def spuren(session_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Alles, was die Großansicht braucht: Tracks mit wirksamen Kanten, Lücken, Titel."""
    session = await _sitzung(db, session_id)
    segmente = await _build_segments(session, db)
    marker = {m.id: m for m in (await db.execute(
        select(Marker).where(Marker.session_id == session_id))).scalars()}
    auswertung = {i.marker_id: i for i in (await db.execute(
        select(SegmentInsight).where(SegmentInsight.session_id == session_id))).scalars()}
    luecken = {}
    for luecke in (await db.execute(select(MarkerLuecke).where(
            MarkerLuecke.marker_id.in_(list(marker))))).scalars():
        luecken.setdefault(luecke.marker_id, []).append(
            {"id": luecke.id, "von_s": luecke.von_s, "bis_s": luecke.bis_s})
    tracks = []
    for seg in segmente:
        m, ins = marker[seg["marker_id"]], auswertung.get(seg["marker_id"])
        tracks.append({
            **{k: seg[k] for k in ("marker_id", "marker_type", "index", "start_seconds", "end_seconds",
                                   "duration_seconds", "teile", "exportieren", "nas_nr")},
            "laeuft": m.ended_at_ts is None and session.status in ("recording", "paused"),
            "luecken": sorted(luecken.get(m.id, []), key=lambda eintrag: eintrag["von_s"]),
            "exportiert": seg["export"] is not None,
            "titel": (ins.title or "") if ins else "",
            "sprecher": (ins.speaker or "") if ins else "",
            "zusammenfassung": (ins.summary or "") if ins else "",
            "ki": bool(ins and ins.ki_gelaufen),
            "titel_von_hand": bool(ins and ins.titel_von_hand),
        })
    return {
        "session_id": session.id,
        "status": session.status,
        "service_type": session.service_type,
        "thema": session.thema or "",
        "laenge_s": _laenge(session),
        "beitragsarten": typen.aktuell.beitragsarten_gueltig,
        "tracks": tracks,
    }


@router.get("/{session_id}/clip.mp3")
async def clip(session_id: int, von: float, bis: float, db: AsyncSession = Depends(get_db)):
    """Reinhören — der Pfad zur Aufnahme kommt aus der Datenbank, nie vom Browser."""
    session = await _sitzung(db, session_id)
    if not session.wav_path:
        raise HTTPException(status_code=409, detail=uebersetze("Session hat keine Aufnahmedatei"))
    params = {"von": von, "bis": bis, "wav_path": session.wav_path}

    async def weiterreichen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{AUDIO_SERVICE_URL}/api/session/clip.mp3",
                                     params=params) as upstream:
                if upstream.status_code != 200:
                    return
                async for stueck in upstream.aiter_raw():
                    yield stueck
    return StreamingResponse(weiterreichen(), media_type="audio/mpeg",
                             headers={"Cache-Control": "no-store"})


# ─── Kanten ────────────────────────────────────────────────────────────────

class Kanten(BaseModel):
    start_s: Optional[float] = None
    ende_s: Optional[float] = None


@router.patch("/markers/{marker_id}/kanten")
async def kanten_setzen(marker_id: int, req: Kanten, db: AsyncSession = Depends(get_db)) -> dict:
    """Anfang und Ende auf eine genaue Stelle setzen (aus der Großansicht, gezogen oder
    in Feinschritten). Von Hand gesetzt — die Kantenautomatik lässt den Track danach in Ruhe."""
    marker = await _marker(db, marker_id)
    session = await _sitzung(db, marker.session_id)
    start = float(marker.placed_at_ts) + float(marker.start_nudge or 0.0)
    if req.start_s is not None:
        start = req.start_s
    ende = None
    if req.ende_s is not None:
        if marker.ended_at_ts is None and session.status in ("recording", "paused"):
            raise HTTPException(status_code=409,
                                detail=uebersetze("Der Track läuft noch — erst beenden, dann das Ende setzen"))
        ende = req.ende_s
    elif marker.ended_at_ts is not None:
        ende = float(marker.ended_at_ts) + float(marker.end_nudge or 0.0)
    _pruefe_bereich(session, start, ende)

    marker.start_nudge = round(start - float(marker.placed_at_ts), 3)
    if req.ende_s is not None:
        # Ein ausdrückliches Ende statt "bis zum nächsten Marker"
        marker.ended_at_ts = round(req.ende_s, 3)
        marker.end_nudge = 0.0
    marker.nudge_manual = True
    await db.commit()
    await session_dokument.schreiben(session.id, db)
    return {"marker_id": marker.id, "start_s": start, "ende_s": ende}


# ─── Einfügen und Teilen ───────────────────────────────────────────────────

class NeuerTrack(BaseModel):
    marker_type: str
    start_s: float
    ende_s: float


@router.post("/{session_id}/markers")
async def track_einfuegen(session_id: int, req: NeuerTrack, db: AsyncSession = Depends(get_db)) -> dict:
    """Einen Track nachträglich einfügen — etwa die Begrüßung nach dem Anfangsgebet, die die
    Automatik mangels Pause nicht getrennt hat. Er bekommt ein festes Ende und gilt als von
    Hand gesetzt: Neu erkennen und die Kantenautomatik lassen ihn stehen."""
    session = await _sitzung(db, session_id)
    if req.marker_type not in typen.aktuell.beitragsarten_gueltig:
        raise HTTPException(status_code=422, detail=f"Unbekannte Beitragsart: {req.marker_type}")
    _pruefe_bereich(session, req.start_s, req.ende_s)
    schon = await _schon_exportiert(db, session_id)
    marker = Marker(session_id=session_id, marker_type=req.marker_type,
                    placed_at_ts=round(req.start_s, 3), ended_at_ts=round(req.ende_s, 3),
                    placed_at_wall=(session.started_at or datetime.now()) + timedelta(seconds=req.start_s),
                    nudge_manual=True)
    db.add(marker)
    await db.commit()
    await db.refresh(marker)
    await session_dokument.schreiben(session_id, db)
    ki_live.einreihen(session_id, marker.id)
    return {"marker_id": marker.id, "hinweis": _verschiebt_nummern_hinweis(schon)}


class Teilen(BaseModel):
    bei_s: float
    marker_type: Optional[str] = None     # Typ des hinteren Teils; leer = wie vorne


@router.post("/markers/{marker_id}/teilen")
async def track_teilen(marker_id: int, req: Teilen, db: AsyncSession = Depends(get_db)) -> dict:
    """Einen Track an einer Stelle in zwei teilen — etwa wenn der Musiker vor dem Stück
    noch ein paar Worte sagt. Jeder Teil wird danach für sich normalisiert.

    Der vordere bekommt sein Ende genau an der Teilstelle; sonst überlappten beide und der
    Ton dazwischen stünde in beiden Dateien."""
    marker = await _marker(db, marker_id)
    session = await _sitzung(db, marker.session_id)
    typ = req.marker_type or marker.marker_type
    if typ not in typen.aktuell.beitragsarten_gueltig:
        raise HTTPException(status_code=422, detail=f"Unbekannte Beitragsart: {typ}")
    seg = next((s for s in await _build_segments(session, db) if s["marker_id"] == marker_id), None)
    if seg is None:
        raise HTTPException(status_code=404, detail=uebersetze("Kein Segment zu diesem Marker"))
    start, ende = seg["start_seconds"], seg["end_seconds"]
    if not (start + MIN_TRACK_S <= req.bei_s <= ende - MIN_TRACK_S):
        raise HTTPException(status_code=422, detail=uebersetze("Die Teilstelle muss im Track liegen"))
    laeuft = marker.ended_at_ts is None and session.status in ("recording", "paused")
    schon = await _schon_exportiert(db, session.id)

    hinten = Marker(session_id=session.id, marker_type=typ, placed_at_ts=round(req.bei_s, 3),
                    ended_at_ts=None if laeuft else round(ende, 3),
                    placed_at_wall=marker.placed_at_wall, nudge_manual=True)
    marker.ended_at_ts = round(req.bei_s, 3)
    marker.end_nudge = 0.0
    marker.nudge_manual = True
    db.add(hinten)
    await db.flush()
    # Lücken gehören zu dem Teil, in dem sie liegen
    for luecke in (await db.execute(select(MarkerLuecke).where(MarkerLuecke.marker_id == marker_id))).scalars():
        if luecke.von_s >= req.bei_s:
            luecke.marker_id = hinten.id
    await db.commit()
    await db.refresh(hinten)
    await session_dokument.schreiben(session.id, db)
    ki_live.einreihen(session.id, marker.id)
    if not laeuft:
        ki_live.einreihen(session.id, hinten.id)
    return {"vorne": marker.id, "hinten": hinten.id, "hinweis": _verschiebt_nummern_hinweis(schon)}


# ─── Unterbrechen ──────────────────────────────────────────────────────────

async def _offener_track(db: AsyncSession) -> Marker:
    session = (await db.execute(select(RecordingSession).where(
        RecordingSession.status.in_(["recording", "paused"])))).scalars().first()
    if session is None:
        raise HTTPException(status_code=409, detail=uebersetze("Keine laufende Aufnahme"))
    marker = (await db.execute(select(Marker).where(
        Marker.session_id == session.id, Marker.ended_at_ts.is_(None))
        .order_by(Marker.placed_at_ts.desc()))).scalars().first()
    if marker is None:
        raise HTTPException(status_code=409, detail=uebersetze("Kein offener Track"))
    return marker


async def _offene_luecke(db: AsyncSession, marker_id: int) -> Optional[MarkerLuecke]:
    return (await db.execute(select(MarkerLuecke).where(
        MarkerLuecke.marker_id == marker_id, MarkerLuecke.bis_s.is_(None)))).scalars().first()


@router.post("/marker/unterbrechen")
async def track_unterbrechen(db: AsyncSession = Depends(get_db)) -> dict:
    """Die Aufnahme läuft weiter, der laufende Track lässt ab jetzt aus — bis „Fortsetzen“.
    Anders als „Pause“ geht dabei kein Ton verloren, wenn man zu spät fortsetzt."""
    marker = await _offener_track(db)
    if await _offene_luecke(db, marker.id) is not None:
        raise HTTPException(status_code=409, detail=uebersetze("Der Track ist schon unterbrochen"))
    stelle = await _aktuelle_stelle()
    luecke = MarkerLuecke(marker_id=marker.id, von_s=round(stelle, 3))
    db.add(luecke)
    await db.commit()
    return {"marker_id": marker.id, "luecke_id": luecke.id, "von_s": luecke.von_s}


@router.post("/marker/fortsetzen")
async def track_fortsetzen(db: AsyncSession = Depends(get_db)) -> dict:
    marker = await _offener_track(db)
    luecke = await _offene_luecke(db, marker.id)
    if luecke is None:
        raise HTTPException(status_code=409, detail=uebersetze("Der Track ist nicht unterbrochen"))
    luecke.bis_s = round(max(await _aktuelle_stelle(), luecke.von_s), 3)
    await db.commit()
    await session_dokument.schreiben(marker.session_id, db)
    return {"marker_id": marker.id, "luecke_id": luecke.id, "von_s": luecke.von_s, "bis_s": luecke.bis_s}


class LueckeRequest(BaseModel):
    von_s: float
    bis_s: float


@router.post("/markers/{marker_id}/luecken")
async def luecke_anlegen(marker_id: int, req: LueckeRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Eine Lücke nachträglich festlegen — in der Großansicht."""
    marker = await _marker(db, marker_id)
    if req.bis_s - req.von_s < MIN_TRACK_S:
        raise HTTPException(status_code=422, detail=uebersetze("Das Ende muss nach dem Anfang liegen"))
    luecke = MarkerLuecke(marker_id=marker.id, von_s=round(req.von_s, 3), bis_s=round(req.bis_s, 3))
    db.add(luecke)
    await db.commit()
    await session_dokument.schreiben(marker.session_id, db)
    return {"luecke_id": luecke.id}


@router.patch("/markers/{marker_id}/luecken/{luecke_id}")
async def luecke_aendern(marker_id: int, luecke_id: int, req: LueckeRequest,
                         db: AsyncSession = Depends(get_db)) -> dict:
    luecke = await db.get(MarkerLuecke, luecke_id)
    if luecke is None or luecke.marker_id != marker_id:
        raise HTTPException(status_code=404, detail=f"Lücke {luecke_id} not found")
    if req.bis_s - req.von_s < MIN_TRACK_S:
        raise HTTPException(status_code=422, detail=uebersetze("Das Ende muss nach dem Anfang liegen"))
    luecke.von_s, luecke.bis_s = round(req.von_s, 3), round(req.bis_s, 3)
    await db.commit()
    return {"luecke_id": luecke.id}


@router.delete("/markers/{marker_id}/luecken/{luecke_id}")
async def luecke_entfernen(marker_id: int, luecke_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    luecke = await db.get(MarkerLuecke, luecke_id)
    if luecke is None or luecke.marker_id != marker_id:
        raise HTTPException(status_code=404, detail=f"Lücke {luecke_id} not found")
    await db.delete(luecke)
    await db.commit()
    return {"geloescht": luecke_id}
