"""Übersetzer-Strecke: Knopf und Statuskarte im Dashboard, weitergereicht an den Audio-Dienst.

Der Audio-Dienst besitzt ffmpeg, srt-live-transmit und den Dante-Ausgang; hier wird nur
weitergereicht, übersetzt und die Karte gerendert."""
import os

import httpx
from fastapi import APIRouter, HTTPException, Request

from sprache import uebersetze
from vorlagen import templates

router = APIRouter(prefix="/api/uebersetzung", tags=["uebersetzung"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

NICHT_ERREICHBAR = {"status": "fehler", "grund": "dienst", "configured": True}


async def _lies_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{AUDIO_SERVICE_URL}/api/uebersetzung/status")
        if response.status_code != 200:
            return dict(NICHT_ERREICHBAR)
        return response.json()
    except (httpx.HTTPError, ValueError):
        return dict(NICHT_ERREICHBAR)


def _karte(request: Request, daten: dict):
    return templates.TemplateResponse(
        "partials/uebersetzung_status.html", {"request": request, "ue": daten})


async def _weiterreichen(pfad: str) -> dict:
    """POST an den Audio-Dienst. Seine Meldungen sind feste deutsche Sätze und landen
    ungefiltert im Toast — deshalb hier übersetzen."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f"{AUDIO_SERVICE_URL}{pfad}")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise HTTPException(status_code=response.status_code,
                            detail=uebersetze(detail) if isinstance(detail, str) else detail)
    return response.json()


@router.get("/status")
async def uebersetzung_status(request: Request):
    daten = await _lies_status()
    if request.headers.get("HX-Request"):
        return _karte(request, daten)
    return daten


@router.post("/start")
async def uebersetzung_starten(request: Request):
    daten = await _weiterreichen("/api/uebersetzung/start")
    if request.headers.get("HX-Request"):
        return _karte(request, daten)
    return daten


@router.post("/stop")
async def uebersetzung_stoppen(request: Request):
    daten = await _weiterreichen("/api/uebersetzung/stop")
    if request.headers.get("HX-Request"):
        return _karte(request, daten)
    return daten
