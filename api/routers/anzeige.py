"""Chorbildschirm: der Nachbau der Liedanzeige auf dem Mini-Rechner im Chor.

Der Dirigent tippt Liednummern, schaltet mit F-Tasten den Modus und blättert durch sein
Programm. Bedient wird nur mit der Tastatur, angezeigt im Browser im Vollbild — die Seite
selbst hält alles, was auf dem Bildschirm steht. Hierher kommen nur die Dinge, die bleiben
sollen: die Programme, der Verlauf und der Punkt, auf dem der Dirigent steht.

Der Punkt geht weiter an die Automatik. Die Anzeige darf daran aber nie hängen: Ist der
Audio-Dienst nicht erreichbar, blättert der Dirigent trotzdem weiter.
"""
import json
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import chor_programm
import einstellungen
import typen
from database import get_db
from models import AnzeigeVerlauf, ChorProgramm
from sprache import uebersetze
from vorlagen import templates

router = APIRouter(tags=["anzeige"])
_log = logging.getLogger(__name__)

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")
MAX_PDF_BYTES = 10 * 1024 * 1024
# Mehr braucht die Auswahl am Bildschirm nicht; ältere bleiben in der Datenbank.
LISTE_MAX = 30


def _einstellungen_fuer_seite(werte: dict) -> dict:
    """Nur die Werte des Chorbildschirms, ohne Präfix — die Seite sieht nichts anderes."""
    return {k.removeprefix("anzeige_"): v for k, v in werte.items() if k.startswith("anzeige_")}


@router.get("/anzeige", response_class=HTMLResponse, include_in_schema=False)
async def anzeige_seite(request: Request, db: AsyncSession = Depends(get_db)):
    werte = await einstellungen.alle_lesen(db)
    return templates.TemplateResponse(
        "anzeige.html", {"request": request, "einstellungen": _einstellungen_fuer_seite(werte)})


def _kurz(p: ChorProgramm) -> dict:
    return {"id": p.id, "name": p.name, "am": p.am.isoformat() if p.am else None}


def _voll(p: ChorProgramm) -> dict:
    return {**_kurz(p), "punkte": json.loads(p.punkte or "[]")}


@router.get("/api/anzeige/programme")
async def programme(db: AsyncSession = Depends(get_db)) -> list:
    q = await db.execute(select(ChorProgramm).order_by(ChorProgramm.am.desc(), ChorProgramm.id.desc())
                         .limit(LISTE_MAX))
    return [_kurz(p) for p in q.scalars()]


@router.get("/api/anzeige/programme/{programm_id}")
async def programm(programm_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    p = await db.get(ChorProgramm, programm_id)
    if p is None:
        raise HTTPException(status_code=404, detail=uebersetze("Programm nicht gefunden"))
    return _voll(p)


@router.post("/api/anzeige/programme")
async def programm_hochladen(datei: UploadFile = File(...), db: AsyncSession = Depends(get_db)) -> dict:
    """Eine PDF lesen und als Programm speichern — vom Stick am Mini-Rechner."""
    inhalt = await datei.read(MAX_PDF_BYTES + 1)
    if len(inhalt) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail=uebersetze("Die Datei ist zu groß"))
    try:
        roh = chor_programm.zeilen_aus_pdf(inhalt)
    except ValueError:
        raise HTTPException(status_code=422, detail=uebersetze("Die PDF lässt sich nicht lesen"))
    klammern = (await einstellungen.alle_lesen(db))["anzeige_klammern"]
    punkte = chor_programm.programm_lesen(roh, klammern, typen.aktuell.beitragsarten)
    if not punkte:
        # Ein Scan oder eine leere Seite: kein Text, also nichts anzuzeigen
        raise HTTPException(status_code=422, detail=uebersetze("In der PDF steht kein lesbarer Text"))
    name = os.path.splitext(os.path.basename(datei.filename or ""))[0] or uebersetze("Programm")
    p = ChorProgramm(name=name, punkte=json.dumps(punkte, ensure_ascii=False), klammern=klammern)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _voll(p)


class Zeiger(BaseModel):
    programm_id: int
    index: Optional[int] = None


@router.post("/api/anzeige/zeiger")
async def zeiger(z: Zeiger, db: AsyncSession = Depends(get_db)) -> dict:
    """Der Dirigent hat ein Programm geöffnet oder darin geblättert.

    Schickt das ganze Programm jedes Mal mit: Der Audio-Dienst hält es nur im Speicher,
    und nach einem Neustart soll es mit dem nächsten Schritt wieder da sein, ohne dass
    jemand etwas tun muss."""
    werte = await einstellungen.alle_lesen(db)
    if not werte["anzeige_programm_automatik"]:
        return {"automatik": False}
    p = await db.get(ChorProgramm, z.programm_id)
    if p is None:
        raise HTTPException(status_code=404, detail=uebersetze("Programm nicht gefunden"))
    nutzlast = {
        "kennung": f"chor-{p.id}",
        "eintraege": chor_programm.erwartungen(json.loads(p.punkte or "[]")),
        "zeiger": z.index,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.post(f"{AUDIO_SERVICE_URL}/api/session/programm-vorgabe", json=nutzlast)
        return {"automatik": r.status_code == 200}
    except httpx.HTTPError as fehler:
        _log.info("Programm des Dirigenten nicht an den Audio-Dienst: %s", type(fehler).__name__)
        return {"automatik": False}


class Verlauf(BaseModel):
    modus: str
    text: str


@router.post("/api/anzeige/verlauf")
async def verlauf(v: Verlauf, db: AsyncSession = Depends(get_db)) -> dict:
    text = v.text.strip()
    if text:
        db.add(AnzeigeVerlauf(modus=v.modus.strip()[:60] or "?", text=text[:2000]))
        await db.commit()
    return {"status": "ok"}
