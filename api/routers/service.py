"""Current service selection shown in the header — shared by every device."""
import json
import os
from datetime import datetime
from typing import Optional

import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import automode_sync
from database import get_db
from models import RecordingSession, Setting
from service_types import CURRENT_SERVICE_KEY, ensure_checklist_for_service
import typen
from sprache import uebersetze

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

router = APIRouter(prefix="/api/service", tags=["service"])


class CurrentServiceRequest(BaseModel):
    service_type: str
    couple_name: Optional[str] = None
    wedding_part: Optional[int] = Field(default=None, ge=1, le=3)
    thema: Optional[str] = None


async def _active_session(db: AsyncSession) -> Optional[RecordingSession]:
    result = await db.execute(
        select(RecordingSession)
        .where(RecordingSession.status.in_(["recording", "paused"]))
        .order_by(RecordingSession.started_at.desc())
    )
    return result.scalars().first()


async def _stored_current(db: AsyncSession) -> dict:
    result = await db.execute(select(Setting).where(Setting.key == CURRENT_SERVICE_KEY))
    setting = result.scalar_one_or_none()
    try:
        data = json.loads(setting.value) if setting and setting.value else {}
    except (ValueError, TypeError):
        data = {}
    return {
        "service_type": data.get("service_type"),
        "couple_name": data.get("couple_name"),
        "wedding_part": data.get("wedding_part"),
        "thema": data.get("thema"),
        "gewaehlt_am": data.get("gewaehlt_am"),
    }


@router.get("/current")
async def get_current_service(db: AsyncSession = Depends(get_db)):
    """Selected service (null fields when nothing chosen yet); locked while a recording runs."""
    current = await _stored_current(db)
    active = await _active_session(db)
    return {**current, "locked": active is not None}


@router.put("/current")
async def put_current_service(req: CurrentServiceRequest, db: AsyncSession = Depends(get_db)):
    """Change the selected service and activate its checklist (if one is mapped)."""
    if req.service_type not in typen.aktuell.gottesdienstarten:
        raise HTTPException(status_code=422, detail=f"Unbekannter Gottesdiensttyp: {req.service_type}")
    if await _active_session(db) is not None:
        raise HTTPException(status_code=409, detail=uebersetze("Während einer Aufnahme kann der Gottesdienst nicht geändert werden"))

    wedding = req.service_type == "Hochzeit"
    current = {
        "service_type": req.service_type,
        "couple_name": ((req.couple_name or "").strip() or None) if wedding else None,
        "wedding_part": req.wedding_part if wedding else None,
        "thema": (req.thema or "").strip() or None,
        # Ab wann dieser Gottesdienst gilt. Die Segment-Review zeigt nur Aufnahmen,
        # die danach begonnen haben — sonst steht nach einer neuen Auswahl noch der
        # vorige Gottesdienst in der Liste.
        "gewaehlt_am": datetime.now().isoformat(),
    }

    result = await db.execute(select(Setting).where(Setting.key == CURRENT_SERVICE_KEY))
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(Setting(key=CURRENT_SERVICE_KEY, value=json.dumps(current)))
    else:
        setting.value = json.dumps(current)
    await db.commit()

    await ensure_checklist_for_service(db, req.service_type)

    # Ein neuer Gottesdienst heißt: von vorn. Sonst stünden im Dashboard noch die
    # erkannten Beiträge des vorigen, und der Automodus läse an dessen Aufnahme.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{AUDIO_SERVICE_URL}/api/session/automode/stop")
    except httpx.HTTPError:
        pass        # Audio-Dienst weg: die Auswahl selbst darf daran nicht scheitern
    automode_sync.zuruecksetzen()

    return {**current, "locked": False}
