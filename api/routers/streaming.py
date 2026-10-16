import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx

from database import get_db
from models import Setting
from sprache import uebersetze

router = APIRouter(prefix="/api/streaming", tags=["streaming"])

STREAMING_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

BASE_DIR = Path(__file__).parent.parent
from vorlagen import templates   # die eine Jinja-Umgebung, siehe api/vorlagen.py

STREAM_TARGET_KEY = "stream_target_url"

# Only these push targets have an equivalent URL a browser can simply play.
# An RTMP/FLV target has none — it yields no listen URL at all.
LISTENABLE_SCHEMES = {"icecast": "http", "http": "http", "https": "https"}


async def _get_stream_target(db: AsyncSession) -> Optional[str]:
    result = await db.execute(select(Setting).where(Setting.key == STREAM_TARGET_KEY))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


def _listen_url(target: Optional[str]) -> Optional[str]:
    """Playback URL derived from the push target — rebuilt from host/port/path only,
    so credentials can never leak into a response even if the target carries them."""
    if not target:
        return None
    try:
        parts = urlsplit(target.strip())
        scheme = LISTENABLE_SCHEMES.get(parts.scheme.lower())
        if not scheme or not parts.hostname:
            return None
        netloc = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    except ValueError:
        return None
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


@router.get("/status")
async def streaming_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current streaming state from native streaming service."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{STREAMING_SERVICE_URL}/api/streaming/status")
            data = response.json()
        except httpx.ConnectError:
            data = {"status": "error", "message": uebersetze("Stream-Dienst nicht erreichbar")}

    # Same precedence as /start: settings override the audio service's own STREAM_URL.
    listen_url = _listen_url(await _get_stream_target(db) or data.get("target"))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/streaming_status.html",
            {
                "request": request,
                "status": data.get("status", "idle"),
                "elapsed_seconds": data.get("elapsed_seconds"),
                "error_message": data.get("message"),
                "configured": data.get("configured", True),
                "target": data.get("target"),
                "channels": data.get("channels"),
                "listen_url": listen_url,
            },
        )
    return {**data, "listen_url": listen_url}


@router.post("/start")
async def start_streaming(request: Request, db: AsyncSession = Depends(get_db)):
    """Proxy start command; reads stream_target from settings DB."""
    # Kein Ziel in den Einstellungen? Dann nimmt der Audio-Service das aus seiner .env (STREAM_URL).
    stream_target = await _get_stream_target(db)
    payload = {"stream_target": stream_target} if stream_target else {}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{STREAMING_SERVICE_URL}/api/streaming/start",
                json=payload,
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.json().get("detail"),
                )
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Stream-Dienst nicht erreichbar"))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/streaming_status.html",
            {"request": request, "status": "streaming", "elapsed_seconds": None, "configured": True},
        )
    return data


@router.post("/stop")
async def stop_streaming(request: Request):
    """Proxy stop command to streaming service."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(f"{STREAMING_SERVICE_URL}/api/streaming/stop")
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.json().get("detail"),
                )
            data = response.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Stream-Dienst nicht erreichbar"))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/streaming_status.html",
            {
                "request": request,
                "status": "idle",
                "configured": True,
            },
        )
    return data
