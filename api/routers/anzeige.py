"""Chorbildschirm: der Nachbau der Liedanzeige auf dem Mini-Rechner im Chor.

Der Dirigent tippt Liednummern, schaltet mit F-Tasten den Modus und blättert durch sein
Programm. Bedient wird nur mit der Tastatur, angezeigt im Browser im Vollbild — die Seite
selbst hält alles, was auf dem Bildschirm steht. Hierher kommen nur die Dinge, die bleiben
sollen: die Programme, der Verlauf und der Punkt, auf dem der Dirigent steht.

Der Punkt geht weiter an die Automatik. Die Anzeige darf daran aber nie hängen: Ist der
Audio-Dienst nicht erreichbar, blättert der Dirigent trotzdem weiter.

Stehen mehrere Bildschirme da (oder tippt jemand an einem zweiten Rechner), zeigen alle
dasselbe: Jede Seite schickt ihren Stand hierher, Akouo reicht ihn an die anderen weiter.
"""
import asyncio
import hashlib
import json
import logging
import os
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import chor_programm
import einstellungen
import typen
from database import AsyncSessionLocal, get_db
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


def _kennung(cfg: dict) -> str:
    """Fingerabdruck der Einstellungen — ändert er sich, lädt jede offene Seite neu."""
    roh = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(roh.encode()).hexdigest()[:12]


@router.get("/anzeige", response_class=HTMLResponse, include_in_schema=False)
async def anzeige_seite(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = _einstellungen_fuer_seite(await einstellungen.alle_lesen(db))
    return templates.TemplateResponse(
        "anzeige.html", {"request": request, "einstellungen": cfg, "kennung": _kennung(cfg)})


# ─── Gemeinsamer Bildschirmstand ─────────────────────────────────────────────

# Ein Stand ist Text, Modus, Ansicht und das offene Programm — ein paar Kilobyte.
STAND_MAX_BYTES = 256 * 1024
# So oft kommt ein Lebenszeichen, auch wenn niemand tippt; dabei wird auch geschaut, ob
# sich die Einstellungen geändert haben.
PULS_S = 15.0


class Bildschirm:
    """Was gerade auf dem Chorbildschirm steht, für alle Seiten zugleich.

    Nur im Speicher: Nach einem Neustart der API schickt die erste Seite, die sich meldet,
    ihren Stand — jede Seite merkt ihn sich ohnehin selbst. Letzter gewinnt; zwei Leute,
    die gleichzeitig tippen, gibt es am Chorbildschirm nicht."""

    def __init__(self) -> None:
        self.stand: Optional[dict] = None
        self.version = 0
        self.quelle: Optional[str] = None
        self.folge = 0
        self._folge: dict[str, int] = {}
        self._abos: set[asyncio.Queue] = set()

    def setzen(self, stand: dict, quelle: str, folge: int) -> bool:
        # Anfragen einer Seite können sich überholen — eine ältere darf die neuere nicht
        # überschreiben.
        if folge <= self._folge.get(quelle, -1):
            return False
        self._folge[quelle] = folge
        self.stand, self.quelle, self.folge = stand, quelle, folge
        self.version += 1
        self._verteilen(("stand", self.nachricht()))
        return True

    def nachricht(self) -> dict:
        return {"version": self.version, "quelle": self.quelle, "folge": self.folge, "stand": self.stand}

    def abonnieren(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._abos.add(q)
        return q

    def abbestellen(self, q: asyncio.Queue) -> None:
        self._abos.discard(q)

    def _verteilen(self, ereignis: tuple) -> None:
        for q in list(self._abos):
            try:
                q.put_nowait(ereignis)
            except asyncio.QueueFull:
                # Eine Seite, die nicht mehr abholt, ist weg — sie meldet sich neu.
                self._abos.discard(q)


bildschirm = Bildschirm()


class StandMeldung(BaseModel):
    quelle: str
    folge: int
    stand: dict[str, Any]


@router.get("/api/anzeige/stand")
async def stand_lesen() -> dict:
    return bildschirm.nachricht()


@router.post("/api/anzeige/stand")
async def stand_setzen(m: StandMeldung) -> dict:
    if len(json.dumps(m.stand, ensure_ascii=False)) > STAND_MAX_BYTES:
        raise HTTPException(status_code=413, detail=uebersetze("Die Datei ist zu groß"))
    angenommen = bildschirm.setzen(m.stand, m.quelle[:64], m.folge)
    return {"version": bildschirm.version, "angenommen": angenommen}


async def _kennung_lesen() -> Optional[str]:
    try:
        async with AsyncSessionLocal() as db:
            return _kennung(_einstellungen_fuer_seite(await einstellungen.alle_lesen(db)))
    except SQLAlchemyError:
        return None


def _sse(art: str, daten: Any) -> str:
    return f"event: {art}\ndata: {json.dumps(daten, ensure_ascii=False)}\n\n"


@router.get("/api/anzeige/ereignisse")
async def ereignisse(request: Request) -> StreamingResponse:
    """Server-Sent Events: jeder neue Stand, dazu alle 15 s der Fingerabdruck der Einstellungen."""
    q = bildschirm.abonnieren()

    async def strom():
        try:
            yield _sse("stand", bildschirm.nachricht())
            kennung = await _kennung_lesen()
            if kennung:
                yield _sse("einstellungen", kennung)
            while not await request.is_disconnected():
                try:
                    art, daten = await asyncio.wait_for(q.get(), timeout=PULS_S)
                    yield _sse(art, daten)
                except asyncio.TimeoutError:
                    kennung = await _kennung_lesen()
                    yield _sse("einstellungen", kennung) if kennung else ": puls\n\n"
        finally:
            bildschirm.abbestellen(q)

    return StreamingResponse(strom(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
