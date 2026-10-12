import json
import os
from datetime import datetime, date
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sa_delete
from pydantic import BaseModel
import httpx

import automode_sync
import session_dokument
from database import get_db
from models import (RecordingSession, Marker, ServiceTypeTemplate, ChecklistInstance,
                    SegmentExport, SegmentInsight)
from service_types import ensure_checklist_for_service
import typen
from routers.segments import refine_session
from routers.settings import AUTO_REFINE_KEY, get_flag
from sprache import uebersetze

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")


class StartSessionRequest(BaseModel):
    service_type: str = "Gottesdienst"
    couple_name: Optional[str] = None
    wedding_part: Optional[int] = None
    thema: Optional[str] = None


class MarkerRequest(BaseModel):
    marker_type: str


async def _get_active_session(db: AsyncSession) -> Optional[RecordingSession]:
    result = await db.execute(
        select(RecordingSession)
        .where(RecordingSession.status.in_(["recording", "paused"]))
        .order_by(RecordingSession.started_at.desc())
    )
    return result.scalars().first()


@router.post("/start")
async def start_session(req: StartSessionRequest, db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{AUDIO_SERVICE_URL}/api/session/start",
                json={"service_type": req.service_type},
            )
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))

    # Persist session row (with Phase-6 Hochzeitsfeldern)
    session_row = RecordingSession(
        started_at=datetime.now(),
        wav_path=data.get("wav_path", ""),
        service_type=req.service_type,
        status="recording",
        couple_name=req.couple_name,
        wedding_part=req.wedding_part,
        thema=(req.thema or "").strip() or None,
    )
    db.add(session_row)
    await db.commit()
    await db.refresh(session_row)

    # Phase 6: Track-Sequenz und Auto-Checklist aus ServiceTypeTemplate ziehen
    track_sequence: list = []
    svc_tmpl_result = await db.execute(
        select(ServiceTypeTemplate).where(
            ServiceTypeTemplate.service_type == req.service_type
        )
    )
    svc_tmpl = svc_tmpl_result.scalar_one_or_none()
    if svc_tmpl is not None:
        if svc_tmpl.track_sequence:
            try:
                track_sequence = json.loads(svc_tmpl.track_sequence)
            except (ValueError, TypeError):
                track_sequence = []
        # SVC-04: Checkliste aktivieren — nur wenn nicht schon heute aktiv (sonst wären Haken weg)
        await ensure_checklist_for_service(db, req.service_type)

    return {
        "id": session_row.id,
        "track_sequence": track_sequence,
        **data,
    }


@router.post("/marker")
async def place_marker(req: MarkerRequest, db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.post(
                f"{AUDIO_SERVICE_URL}/api/session/marker",
                json={"marker_type": req.marker_type},
            )
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))

    active = await _get_active_session(db)
    if active is not None:
        marker_data = data.get("marker", {})
        asi = data.get("audio_service_index")
        m = Marker(
            session_id=active.id,
            marker_type=req.marker_type,
            placed_at_ts=float(marker_data.get("offset_seconds", 0.0)),
            placed_at_wall=datetime.fromisoformat(marker_data.get("wall_time")) if marker_data.get("wall_time") else datetime.now(),
            audio_service_index=int(asi) if asi is not None else None,
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        data["marker_id"] = m.id  # surface DB id so the dashboard can reference for nudge
        await session_dokument.schreiben(m.session_id, db)
    return data


@router.post("/marker/end")
async def end_marker(db: AsyncSession = Depends(get_db)):
    """End the running track — stamps ended_at_ts on the newest marker that has no end yet."""
    active = await _get_active_session(db)
    if active is None:
        raise HTTPException(status_code=409, detail=uebersetze("Keine laufende Aufnahme"))
    result = await db.execute(
        select(Marker)
        .where(Marker.session_id == active.id, Marker.ended_at_ts.is_(None))
        .order_by(Marker.placed_at_ts.desc())
    )
    marker = result.scalars().first()
    if marker is None:
        raise HTTPException(status_code=409, detail=uebersetze("Kein offener Track"))

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{AUDIO_SERVICE_URL}/api/session/offset")
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            offset = float(response.json()["offset_seconds"])
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))

    marker.ended_at_ts = max(offset, float(marker.placed_at_ts) + 0.5)
    await db.commit()
    await db.refresh(marker)
    await session_dokument.schreiben(marker.session_id, db)
    return {
        "id": marker.id,
        "marker_type": marker.marker_type,
        "placed_at_ts": marker.placed_at_ts,
        "ended_at_ts": marker.ended_at_ts,
    }


class MarkerTypeRequest(BaseModel):
    marker_type: str


@router.delete("/markers/{marker_id}")
async def delete_marker(marker_id: int, db: AsyncSession = Depends(get_db)):
    """Marker entfernen — für Fehlgriffe und für das, was die Automatik daneben setzt.

    Bereits erzeugte MP3s bleiben auf der Platte liegen; hier verschwindet nur der
    Eintrag samt seiner Export- und Auswertungszeilen, damit die Review-Karte nicht
    auf einen Marker zeigt, den es nicht mehr gibt.
    """
    marker = await db.get(Marker, marker_id)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} nicht gefunden")
    await db.execute(sa_delete(SegmentInsight).where(SegmentInsight.marker_id == marker_id))
    await db.execute(sa_delete(SegmentExport).where(SegmentExport.marker_id == marker_id))
    session_id = marker.session_id
    await db.delete(marker)
    await db.commit()
    await session_dokument.schreiben(session_id, db)
    return {"geloescht": marker_id}


@router.patch("/markers/{marker_id}/type")
async def set_marker_type(marker_id: int, req: MarkerTypeRequest, db: AsyncSession = Depends(get_db)):
    """Name a track after it was started (big button first, type afterwards)."""
    if req.marker_type not in typen.aktuell.beitragsarten_gueltig:
        raise HTTPException(status_code=422, detail=f"Unbekannter Track-Typ: {req.marker_type}")
    marker = await db.get(Marker, marker_id)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} nicht gefunden")
    marker.marker_type = req.marker_type
    await db.commit()
    await session_dokument.schreiben(marker.session_id, db)
    return {"id": marker.id, "marker_type": marker.marker_type}


@router.get("/transcript")
async def session_transcript(since: int = 0):
    """Proxy the live transcript from the audio-service (browser only talks to port 8000)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{AUDIO_SERVICE_URL}/api/session/transcript", params={"since": since})
            return response.json()
        except (httpx.ConnectError, httpx.ReadTimeout):
            return {"lines": [], "next": since, "enabled": True, "running": False,
                    "error": "Audio-Service nicht erreichbar"}


@router.post("/pause")
async def pause_session(db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.post(f"{AUDIO_SERVICE_URL}/api/session/pause")
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))
    active = await _get_active_session(db)
    if active is not None:
        active.status = "paused"
        active.paused_at = datetime.now()
        await db.commit()
    return data


@router.post("/resume")
async def resume_session(db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.post(f"{AUDIO_SERVICE_URL}/api/session/resume")
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))
    active = await _get_active_session(db)
    if active is not None:
        active.status = "recording"
        await db.commit()
    return data


@router.post("/stop")
async def stop_session(db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{AUDIO_SERVICE_URL}/api/session/stop")
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))
    active = await _get_active_session(db)
    if active is not None:
        active.status = "stopped"
        active.stopped_at = datetime.now()
        # Flush any markers in response not yet in DB
        existing_q = await db.execute(
            select(Marker).where(Marker.session_id == active.id)
        )
        existing_count = len(existing_q.scalars().all())
        response_markers = data.get("markers", [])
        for m in response_markers[existing_count:]:
            db.add(Marker(
                session_id=active.id,
                marker_type=m["type"],
                placed_at_ts=float(m["offset_seconds"]),
                placed_at_wall=datetime.fromisoformat(m["wall_time"]),
            ))
        await db.commit()
        data["session_id"] = active.id
        await session_dokument.schreiben(active.id, db)

        # Schnittkanten automatisch nachziehen, wenn in den Einstellungen gewünscht.
        # Scheitert das (Audio-Service weg, WAV unlesbar), darf es das Stoppen nicht aufhalten.
        if await get_flag(db, AUTO_REFINE_KEY, default=True):
            try:
                refine_result = await refine_session(active, db)
                data["refined_segments"] = refine_result.shifted
                data["manual_segments"] = refine_result.skipped_manual
            except Exception as exc:
                data["refine_error"] = str(exc)
    return data


@router.get("/status")
async def session_status():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{AUDIO_SERVICE_URL}/api/session/status")
            return response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))


@router.get("/marker-types")
async def marker_types():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{AUDIO_SERVICE_URL}/api/session/marker-types")
            return response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))


@router.post("/{session_id}/export")
async def export_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger MP3 export for a stopped session.

    Looks up RecordingSession by id, derives wav_path from the DB row, forwards
    to audio-service POST /api/session/export with a 120s timeout.
    90-min WAV export takes ~10-30s on M4; 120s = 4x headroom (T-05-10).
    wav_path is DB-controlled (written only at session start) — no path traversal (T-05-09).
    """
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not session.wav_path:
        raise HTTPException(status_code=404, detail=f"Session {session_id} has no wav_path")
    # 90-min export takes ~10-30s on M4 per RESEARCH; allow 4x headroom.
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                f"{AUDIO_SERVICE_URL}/api/session/export",
                json={"wav_path": session.wav_path},
            )
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            return response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))


@router.get("/markers")
async def list_active_markers(db: AsyncSession = Depends(get_db)):
    active = await _get_active_session(db)
    if active is None:
        return []
    result = await db.execute(
        select(Marker).where(Marker.session_id == active.id).order_by(Marker.placed_at_ts)
    )
    markers = result.scalars().all()
    return [
        {
            "id": m.id,
            "marker_type": m.marker_type,
            "placed_at_ts": m.placed_at_ts,
            "start_nudge": m.start_nudge or 0.0,
            "end_nudge": m.end_nudge or 0.0,
            "nudge_manual": bool(m.nudge_manual),
            "ended_at_ts": m.ended_at_ts,
            "audio_service_index": m.audio_service_index,
        }
        for m in markers
    ]


class MarkerNudgeRequest(BaseModel):
    field: str            # "start" or "end"
    delta_seconds: float  # -1.0 or 1.0


@router.patch("/markers/{marker_id}/nudge")
async def nudge_marker_endpoint(
    marker_id: int,
    req: MarkerNudgeRequest,
    db: AsyncSession = Depends(get_db),
):
    if req.field not in ("start", "end"):
        raise HTTPException(status_code=422, detail=f"field must be 'start' or 'end', got {req.field!r}")
    if req.delta_seconds not in (-1.0, 1.0):
        raise HTTPException(status_code=422, detail="delta_seconds must be -1.0 or 1.0")
    marker = await db.get(Marker, marker_id)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} not found")
    if req.field == "start":
        marker.start_nudge = float(marker.start_nudge or 0.0) + req.delta_seconds
    else:
        marker.end_nudge = float(marker.end_nudge or 0.0) + req.delta_seconds
    # Hand beats automation: from now on refine_session leaves this marker alone. Clicking
    # back to zero on both ends undoes that again, so the automation may take over once more.
    marker.nudge_manual = bool(marker.start_nudge or marker.end_nudge)
    await db.commit()
    await db.refresh(marker)
    await session_dokument.schreiben(marker.session_id, db)

    # Best-effort sync to audio-service for in-memory consistency during active session.
    if marker.audio_service_index is not None and req.field == "start":
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.patch(
                    f"{AUDIO_SERVICE_URL}/api/session/markers/{marker.audio_service_index}",
                    json={"field": req.field, "delta_seconds": req.delta_seconds},
                )
        except httpx.ConnectError:
            pass  # DB is authoritative; audio-service will pick up DB on next read

    return {
        "id": marker.id,
        "field": req.field,
        "delta_seconds": req.delta_seconds,
        "start_nudge": marker.start_nudge,
        "end_nudge": marker.end_nudge,
        "nudge_manual": marker.nudge_manual,
    }


@router.get("/automode")
async def automode_status():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{AUDIO_SERVICE_URL}/api/session/automode")
            return r.json()
    except httpx.HTTPError:
        return {"aktiv": False, "beitraege": [], "fehler": "Audio-Dienst nicht erreichbar"}


@router.post("/automode/{schalter}")
async def automode_schalten(schalter: str):
    """Der Automodus wird ausdrücklich eingeschaltet und läuft nie von selbst an."""
    if schalter not in ("start", "stop"):
        raise HTTPException(status_code=422, detail="start oder stop")
    if schalter == "start":
        automode_sync.zuruecksetzen()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{AUDIO_SERVICE_URL}/api/session/automode/{schalter}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code,
                            detail=r.json().get("detail", "Automodus fehlgeschlagen"))
    return r.json()


@router.get("/levels")
async def session_levels(
    session_id: Optional[int] = None,
    from_seconds: float = 0.0,
    block_ms: int = 250,
    db: AsyncSession = Depends(get_db),
):
    """Level curve for the dashboard — running session by default, a stored one via session_id.

    The browser asks once for the whole recording and afterwards only for the tail since its
    last value, which keeps a live curve at one short read every couple of seconds. The wav
    path never comes from the browser: for a stored session it is looked up here, so the
    audio-service only ever gets a path this process chose.
    """
    params: dict = {"from_seconds": from_seconds, "block_ms": block_ms}
    if session_id is not None:
        session = await db.get(RecordingSession, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        if not session.wav_path:
            raise HTTPException(status_code=409, detail=uebersetze("Session hat keine Aufnahmedatei"))
        params["wav_path"] = session.wav_path

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{AUDIO_SERVICE_URL}/api/session/levels", params=params)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio service unreachable"))
    if r.status_code != 200:
        detail = r.json().get("detail", f"Fehler {r.status_code}") if r.headers.get("content-type", "").startswith("application/json") else f"Fehler {r.status_code}"
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


@router.get("/monitor.mp3")
async def monitor_proxy():
    """Relay the audio-service monitor stream — it listens on localhost only, tablets don't."""
    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{AUDIO_SERVICE_URL}/api/session/monitor.mp3") as upstream:
                if upstream.status_code != 200:
                    return
                async for chunk in upstream.aiter_raw():
                    yield chunk
    return StreamingResponse(relay(), media_type="audio/mpeg")


@router.get("/audio-level")
async def audio_level_proxy():
    """Proxy SSE stream from audio-service to browser (avoids host.docker.internal in browser)."""
    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{AUDIO_SERVICE_URL}/api/session/audio-level") as upstream:
                async for chunk in upstream.aiter_raw():
                    yield chunk
    return StreamingResponse(relay(), media_type="text/event-stream")
