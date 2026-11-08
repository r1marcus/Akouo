"""Export-Check: vor dem Export alles einmal sehen — und Fehler noch korrigieren.

Am 07.11. ging eine Predigt als „Gebetsstunde“ in die Gemeinde-App, obwohl es ein
Jungverheirateten-Abend war; am 08.11. hing das Thema vom Vortag am Sonntagmorgen.
Beides hätte man gesehen, wenn vor dem Export einmal dastünde, wohin was geht.

Die Vorschau rechnet mit denselben Bausteinen wie der Export (Dateiname, NAS-Ordner,
CopyCenter, Eintrag der Gemeinde-App), legt aber nichts fest: kein NAS-Ordner wird
gemerkt, kein CopyCenter-Buchstabe vergeben, nichts hochgeladen.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import einstellungen
import nas_pfad
import online as online_modul
import typen
from database import get_db
from models import CopyCenterAblage, Marker, RecordingSession, SegmentExport, SegmentInsight, Setting
from service_types import CURRENT_SERVICE_KEY
from routers.segments import (AUDIO_SERVICE_URL, _build_segments, _nas_folder, _nas_track_name,
                              mp3_dateiname)
from sprache import uebersetze

router = APIRouter(prefix="/api/sessions", tags=["export-check"])

# Kürzer ist fast immer eine verrutschte Kante, länger als zwei Stunden ein vergessenes Ende
KURZ_S = 30.0
LANG_S = 2 * 3600.0


def _hinweis(text: str, **werte) -> str:
    """Übersetzt, dann eingesetzt — die Platzhalter stehen in allen drei Sprachen gleich."""
    return text.format(**werte) if werte else text


async def _ordner_vorschau(session: RecordingSession, started: datetime,
                           db: AsyncSession) -> tuple[str, bool]:
    """(NAS-Ordner, schon festgelegt?) — ohne ihn zu merken."""
    if session.nas_folder:
        return session.nas_folder, True
    ordner = await _nas_folder(session, started, db)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{AUDIO_SERVICE_URL}/api/session/nas-folder",
                                 params={"folder": ordner})
        if r.status_code == 200:
            ordner = r.json().get("folder", ordner)
    except httpx.HTTPError:
        pass                    # NAS nicht erreichbar: der gedachte Ordner reicht zur Anzeige
    return ordner, False


@router.get("/{session_id}/export-check")
async def export_check(session_id: int, ids: str = "", station: Optional[bool] = None,
                       online: Optional[bool] = None, db: AsyncSession = Depends(get_db)) -> dict:
    """Alles, was beim Export dieser Tracks passieren würde. `ids` leer = Sammel-Export."""
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    werte_db = await einstellungen.alle_lesen(db)
    station = bool(werte_db.get("nas_copycenter")) if station is None else station
    online = bool(werte_db.get("export_online")) if online is None else online
    ki_vorher = bool(werte_db.get("export_ki_auswertung", True))

    segments = await _build_segments(session, db)
    gewuenscht = {int(x) for x in ids.split(",") if x.strip().isdigit()}
    auswahl = [s for s in segments if (s["marker_id"] in gewuenscht if gewuenscht else s["exportieren"])]
    uebersprungen = [s for s in segments if not gewuenscht and not s["exportieren"]]

    started = session.started_at or datetime.now()
    ordner, ordner_fest = await _ordner_vorschau(session, started, db)
    ist_gottesdienst = ordner.startswith("Gottesdienste/")

    cc = await db.get(CopyCenterAblage, session_id)
    station_info = {
        "an": station and ist_gottesdienst,
        "ordner": cc.ordner if cc else None,
        "beschriftung": nas_pfad.copycenter_beschriftung(session.service_type, session.thema),
    }

    insights = {
        i.marker_id: i for i in (await db.execute(
            select(SegmentInsight).where(SegmentInsight.session_id == session_id))).scalars()
    }

    tracks, ereignis = [], None
    for seg in auswahl:
        typ = seg["marker_type"]
        insight = insights.get(seg["marker_id"])
        nas_datei = _nas_track_name(seg["nas_nr"], typ)
        eintrag = (online_modul.eintrag(ordner, nas_datei, typ, seg["nas_nr"], insight,
                                        session.service_type, session.thema)
                   if online else None)
        if eintrag is not None and ereignis is None:
            ereignis = eintrag["event"]

        hinweise = []
        if seg["duration_seconds"] < KURZ_S:
            hinweise.append(_hinweis(uebersetze("Nur {s} s lang — Kanten prüfen"),
                                     s=round(seg["duration_seconds"])))
        if seg["duration_seconds"] > LANG_S:
            hinweise.append(uebersetze("Länger als zwei Stunden — fehlt das Ende?"))
        if typ == "Unbenannt":
            hinweise.append(uebersetze("Typ fehlt noch"))
        if not seg["exportieren"]:
            hinweise.append(uebersetze("Dieser Typ wird sonst nicht exportiert"))
        if seg["export"]:
            hinweise.append(uebersetze("Schon exportiert — die Datei wird ersetzt"))
        if online and seg["online"] and seg["online"]["status"] == "uploaded":
            hinweise.append(uebersetze("Schon online — wird ersetzt"))
        if insight is None or not insight.ki_gelaufen:
            hinweise.append(uebersetze("KI-Auswertung läuft vor dem Export") if ki_vorher
                            else uebersetze("Ohne KI-Auswertung — in der App ohne Titel"))

        tracks.append({
            "marker_id": seg["marker_id"],
            "nr": seg["nas_nr"],
            "typ": typ,
            "start_s": seg["start_seconds"],
            "ende_s": seg["end_seconds"],
            "dauer_s": seg["duration_seconds"],
            "datei": mp3_dateiname(session, seg, typ),
            "nas_datei": nas_datei,
            "titel": (insight.title or "") if insight else "",
            "sprecher": (insight.speaker or "") if insight else "",
            "ki": insight is not None and insight.ki_gelaufen,
            "online_titel": eintrag["recording"]["title"] if eintrag else None,
            "online_art": eintrag["recording"]["recording_type"] if eintrag else None,
            "hinweise": hinweise,
        })

    allgemein = []
    if online and not ist_gottesdienst:
        allgemein.append(uebersetze("Geht nicht online: kein Gottesdienst-Ordner"))
    if station and not ist_gottesdienst:
        allgemein.append(uebersetze("Nicht auf die Station: kein Gottesdienst-Ordner"))
    if ordner_fest:
        allgemein.append(uebersetze("Der NAS-Ordner steht seit dem ersten Export fest"))

    return {
        "gottesdienst": {
            "art": session.service_type,
            "thema": session.thema or "",
            "datum": started.date().isoformat(),
            "beginn": started.strftime("%H:%M"),
            "arten": typen.aktuell.gottesdienstarten,
        },
        "nas": {"ordner": ordner, "fest": ordner_fest},
        "station": station_info,
        "online": {"an": online and ist_gottesdienst, "ereignis": ereignis},
        "tracks": tracks,
        "uebersprungen": [{"nr": s["index"], "typ": s["marker_type"]} for s in uebersprungen],
        "hinweise": allgemein,
    }


class Angaben(BaseModel):
    service_type: Optional[str] = None
    thema: Optional[str] = None


@router.patch("/{session_id}/angaben")
async def angaben_aendern(session_id: int, req: Angaben, db: AsyncSession = Depends(get_db)) -> dict:
    """Gottesdienstart und Thema einer fertigen Aufnahme richtigstellen — im Export-Check."""
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if session.status in ("recording", "paused"):
        raise HTTPException(status_code=409,
                            detail=uebersetze("Während der Aufnahme nicht änderbar"))
    alt = session.service_type
    if req.service_type is not None:
        if req.service_type not in typen.aktuell.gottesdienstarten:
            raise HTTPException(status_code=422,
                                detail=f"Unbekannter Gottesdiensttyp: {req.service_type}")
        session.service_type = req.service_type
    if req.thema is not None:
        session.thema = req.thema.strip() or None
    if session.service_type != alt:
        # Noch nichts exportiert: der NAS-Ordner folgt dem neuen Typ. Sonst bleibt er, wo die
        # ersten Dateien schon liegen — das sagt die Oberfläche vorher.
        schon = (await db.execute(select(SegmentExport.id).where(
            SegmentExport.session_id == session_id).limit(1))).first()
        if schon is None:
            session.nas_folder = None
        await _auswahl_mitziehen(db, session, alt)
    await db.commit()
    return {"service_type": session.service_type, "thema": session.thema or ""}


async def _auswahl_mitziehen(db: AsyncSession, session: RecordingSession, alt: Optional[str]) -> None:
    """Die Trackliste im Dashboard zeigt nur die Aufnahme zum gewählten Gottesdienst. Ändert
    man bei der letzten Aufnahme den Typ, muss die Auswahl mitgehen — sonst verschwand die
    Karte beim nächsten Neuladen (gefunden am 08.11.)."""
    letzte = (await db.execute(
        select(RecordingSession).where(RecordingSession.status == "stopped")
        .order_by(RecordingSession.stopped_at.desc()).limit(1))).scalar_one_or_none()
    if letzte is None or letzte.id != session.id:
        return
    zeile = (await db.execute(select(Setting).where(Setting.key == CURRENT_SERVICE_KEY))).scalar_one_or_none()
    try:
        daten = json.loads(zeile.value) if zeile and zeile.value else {}
    except (ValueError, TypeError):
        daten = {}
    if daten.get("service_type") not in (None, alt):
        return              # jemand hat inzwischen einen anderen Gottesdienst gewählt
    daten["service_type"] = session.service_type
    daten["thema"] = session.thema
    if zeile is None:
        db.add(Setting(key=CURRENT_SERVICE_KEY, value=json.dumps(daten)))
    else:
        zeile.value = json.dumps(daten)


class TrackAngaben(BaseModel):
    titel: Optional[str] = None
    sprecher: Optional[str] = None


@router.patch("/{session_id}/segments/{marker_id}/angaben")
async def track_angaben_aendern(session_id: int, marker_id: int, req: TrackAngaben,
                                db: AsyncSession = Depends(get_db)) -> dict:
    """Titel und Sprecher eines Tracks. Sie stehen bei der KI-Auswertung und gehen von dort
    in die Gemeinde-App. Gibt es noch keine Auswertung, entsteht ein Eintrag nur mit den
    Handangaben (model = "hand"); die KI läuft vor dem Export trotzdem und lässt sie stehen."""
    marker = await db.get(Marker, marker_id)
    if marker is None or marker.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} not found")
    insight = (await db.execute(
        select(SegmentInsight).where(SegmentInsight.marker_id == marker_id))).scalar_one_or_none()
    if insight is None:
        insight = SegmentInsight(session_id=session_id, marker_id=marker_id, model="hand")
        db.add(insight)
    if req.titel is not None:
        insight.title = req.titel.strip() or None
        insight.titel_von_hand = insight.title is not None
    if req.sprecher is not None:
        insight.speaker = req.sprecher.strip() or None
        insight.sprecher_von_hand = insight.speaker is not None
    await db.commit()
    return {"titel": insight.title or "", "sprecher": insight.speaker or "",
            "keywords": json.loads(insight.keywords or "[]")}
