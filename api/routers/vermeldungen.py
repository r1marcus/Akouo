"""Nach dem Gottesdienst: „Stimmen die Vermeldungen so zum Hochladen?“

Akouo liest die Vermeldungen-Tracks aus dem Transkript (audio_service/vermeldungen.py),
zeigt die Einträge in der Segmentliste, der Tontechniker korrigiert und wählt aus, und erst
dann gehen Termine in den Kalender und Hinweise aufs Schwarze Brett der Gemeinde-App.
"""
import json
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Marker, RecordingSession, VermeldungStand
from sprache import uebersetze

router = APIRouter(prefix="/api/sessions", tags=["vermeldungen"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")
VERMELDUNG_TYPEN = ("Vermeldungen",)


def _antwort(stand: Optional[VermeldungStand], hat_vermeldungen: bool) -> dict:
    if stand is None:
        return {"status": "offen" if hat_vermeldungen else "keine", "eintraege": [], "ergebnis": []}
    return {"status": stand.status, "eintraege": json.loads(stand.eintraege or "[]"),
            "ergebnis": json.loads(stand.ergebnis or "[]")}


async def _bereiche(session: RecordingSession, db: AsyncSession) -> list:
    marker = (await db.execute(select(Marker).where(
        Marker.session_id == session.id, Marker.marker_type.in_(VERMELDUNG_TYPEN)))).scalars().all()
    bereiche = []
    for m in marker:
        von = float(m.placed_at_ts) + float(m.start_nudge or 0)
        bis = float(m.ended_at_ts) + float(m.end_nudge or 0) if m.ended_at_ts is not None else von + 600
        bereiche.append([von, bis])
    return bereiche


async def _session(session_id: int, db: AsyncSession) -> RecordingSession:
    s = await db.get(RecordingSession, session_id)
    if s is None or not s.wav_path:
        raise HTTPException(status_code=404, detail=uebersetze("Kein Segment zu diesem Marker"))
    return s


async def _audio(pfad: str, nutzlast: dict, timeout: float = 120.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{AUDIO_SERVICE_URL}{pfad}", json=nutzlast)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if r.status_code == 503:
        raise HTTPException(status_code=503, detail=uebersetze("Sprachmodell nicht erreichbar"))
    if r.status_code == 409:
        raise HTTPException(status_code=409, detail=uebersetze("Gemeinde-App nicht eingerichtet"))
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=uebersetze("Gemeinde-App nicht erreichbar"))
    return r.json()


@router.get("/{session_id}/vermeldungen")
async def stand(session_id: int, db: AsyncSession = Depends(get_db)):
    s = await _session(session_id, db)
    return _antwort(await db.get(VermeldungStand, session_id), bool(await _bereiche(s, db)))


@router.post("/{session_id}/vermeldungen/lesen")
async def lesen(session_id: int, db: AsyncSession = Depends(get_db)):
    s = await _session(session_id, db)
    bereiche = await _bereiche(s, db)
    if not bereiche:
        return _antwort(None, False)
    datum = (s.started_at.date() if s.started_at else None)
    daten = await _audio("/api/session/vermeldungen", {
        "wav_path": s.wav_path, "bereiche": bereiche, "datum": datum.isoformat()})
    stand_ = await db.get(VermeldungStand, session_id) or VermeldungStand(session_id=session_id, status="gelesen")
    stand_.status, stand_.eintraege, stand_.ergebnis = "gelesen", json.dumps(daten["eintraege"], ensure_ascii=False), None
    db.add(stand_)
    await db.commit()
    return _antwort(stand_, True)


class Bestaetigt(BaseModel):
    eintraege: list


@router.post("/{session_id}/vermeldungen/hochladen")
async def hochladen(session_id: int, b: Bestaetigt, db: AsyncSession = Depends(get_db)):
    s = await _session(session_id, db)
    daten = await _audio("/api/online/vermeldungen", {
        "eintraege": b.eintraege, "datum": s.started_at.date().isoformat()})
    stand_ = await db.get(VermeldungStand, session_id) or VermeldungStand(session_id=session_id, status="gelesen")
    stand_.status = "hochgeladen"
    stand_.eintraege = json.dumps(b.eintraege, ensure_ascii=False)
    stand_.ergebnis = json.dumps(daten["ergebnis"], ensure_ascii=False)
    db.add(stand_)
    await db.commit()
    return _antwort(stand_, True)


@router.post("/{session_id}/vermeldungen/zuruecknehmen")
async def zuruecknehmen(session_id: int, db: AsyncSession = Depends(get_db)):
    stand_ = await db.get(VermeldungStand, session_id)
    if stand_ is None or stand_.status != "hochgeladen":
        raise HTTPException(status_code=409, detail=uebersetze("Nichts zum Zurücknehmen"))
    daten = await _audio("/api/online/vermeldungen/zuruecknehmen",
                         {"ergebnis": json.loads(stand_.ergebnis or "[]")})
    stand_.status, stand_.ergebnis = "zurueckgenommen", json.dumps(daten["ergebnis"], ensure_ascii=False)
    await db.commit()
    return _antwort(stand_, True)


@router.post("/{session_id}/vermeldungen/verwerfen")
async def verwerfen(session_id: int, db: AsyncSession = Depends(get_db)):
    await _session(session_id, db)
    stand_ = await db.get(VermeldungStand, session_id) or VermeldungStand(session_id=session_id, status="verworfen")
    stand_.status = "verworfen"
    db.add(stand_)
    await db.commit()
    return _antwort(stand_, True)
