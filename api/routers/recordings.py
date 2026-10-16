import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
import httpx

from database import get_db
from models import Recording
from sprache import uebersetze

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

BASE_DIR = Path(__file__).parent.parent
from vorlagen import templates   # die eine Jinja-Umgebung, siehe api/vorlagen.py


class StartRequest(BaseModel):
    service_type: str = "Gottesdienst"


@router.get("/status")
async def recording_status(request: Request):
    """Get current recording state from native audio service."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{AUDIO_SERVICE_URL}/status")
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/recording_status.html",
            {
                "request": request,
                "status": data.get("state", "idle"),
                "elapsed_seconds": data.get("elapsed_seconds"),
            },
        )
    return data


@router.post("/start")
async def start_recording(request: Request, req: StartRequest, db: AsyncSession = Depends(get_db)):
    """Proxy start command to native audio service."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{AUDIO_SERVICE_URL}/start",
                json={"service_type": req.service_type},
            )
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/recording_status.html",
            {
                "request": request,
                "status": "recording",
                "elapsed_seconds": None,
            },
        )
    return data


@router.post("/stop")
async def stop_recording(request: Request, db: AsyncSession = Depends(get_db)):
    """Proxy stop command; on success, save recording metadata to SQLite."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{AUDIO_SERVICE_URL}/stop")
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))

    # Save metadata to SQLite
    from datetime import date
    recording = Recording(
        filename=data.get("filename", ""),
        path=data.get("path", ""),
        date=date.today().isoformat(),
        service_type=data.get("service_type", "Gottesdienst"),
        duration=data.get("duration"),
    )
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/recording_status.html",
            {
                "request": request,
                "status": "idle",
                "elapsed_seconds": None,
            },
        )
    return {"id": recording.id, **data}


@router.get("")
async def list_recordings(request: Request, db: AsyncSession = Depends(get_db)):
    """Return all recordings from SQLite, newest first.

    Returns HTML partial for HTMX requests (HX-Request header present),
    JSON otherwise.
    """
    result = await db.execute(
        select(Recording).order_by(Recording.created_at.desc())
    )
    recordings_list = result.scalars().all()
    recordings_data = [
        {
            "id": r.id,
            "filename": r.filename,
            "path": r.path,
            "date": r.date,
            "service_type": r.service_type,
            "duration": r.duration,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recordings_list
    ]

    # Return HTML partial for HTMX requests, JSON otherwise
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/recording_list.html",
            {"request": request, "recordings": recordings_data},
        )
    return recordings_data
