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
