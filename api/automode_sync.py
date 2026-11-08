"""Überträgt, was der Audio-Dienst erkennt, in echte Marker.

Warum hier und nicht dort: die Datenbank gehört der API. Und warum als
Hintergrundaufgabe und nicht im Browser: der Automodus soll auch dann Marker
setzen, wenn niemand das Dashboard offen hat.

Die Aufgabe läuft immer, tut aber nichts, solange der Automodus aus ist.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import httpx
from sqlalchemy import select

import ki_live
import session_dokument
import typen
from database import AsyncSessionLocal
from models import AutoMarker, Marker, RecordingSession

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")
TAKT_S = 2.0

# Beitragsnummer -> (marker_id, zuletzt von der Automatik gesetzter Typ).
# Weicht der Typ in der Datenbank davon ab, hat der Tontechniker ihn geändert —
# dann fasst die Automatik ihn nicht mehr an.
_bekannt: dict[int, tuple[int, str]] = {}


# So weit darf ein Hand-Marker vor dem erkannten Beginn liegen und trotzdem denselben
# Beitrag meinen: wer "Predigt" drückt, während der Prediger noch nach vorn geht.
HAND_VORLAUF_S = 30.0


async def _hand_marker_fuer(db, session_id: int, b: dict, jetzt_s: float):
    """Hat der Tontechniker diesen Beitrag schon selbst markiert?

    Dann gilt seiner. Vorher legte die Automatik ihren eigenen Marker ein paar Sekunden
    dahinter — und weil ein Marker ohne Ende bis zum nächsten reicht, schrumpfte die von
    Hand gesetzte Predigt auf diese paar Sekunden.
    """
    auto_ids = set((await db.execute(select(AutoMarker.marker_id))).scalars())
    vergeben = {marker_id for marker_id, _ in _bekannt.values()}
    q = await db.execute(select(Marker).where(Marker.session_id == session_id))
    b_start = b["start_s"]
    b_ende = b["ende_s"] if b.get("ende_s") is not None else jetzt_s
    for m in q.scalars():
        if m.id in auto_ids or m.id in vergeben:
            continue
        start = m.placed_at_ts + (m.start_nudge or 0.0)
        # Ohne Ende zählt der Marker mit einem kurzen Vorlauf, mit Ende über seine Spanne
        ende = m.ended_at_ts if m.ended_at_ts is not None else start + HAND_VORLAUF_S
        if start <= b_ende and ende >= b_start:
            return m
    return None


def zuruecksetzen():
    _bekannt.clear()


async def _aktive_session(db):
    q = await db.execute(
        select(RecordingSession)
        .where(RecordingSession.status.in_(["recording", "paused"]))
        .order_by(RecordingSession.started_at.desc())
    )
    return q.scalars().first()


async def _abgleichen(zustand: dict):
    beitraege = zustand.get("beitraege", [])
    if not beitraege:
        return
    async with AsyncSessionLocal() as db:
        session = await _aktive_session(db)
        if session is None:
            return
        fertig: list[int] = []          # Tracks, die gerade ein Ende bekamen → KI live
        for b in beitraege:
            nr = b["nr"]
            eintrag = _bekannt.get(nr)

            if eintrag is None:
                # Nicht exportierbares (Gemeindegesang, Gebet, Ansage) bekommt keinen
                # Marker — es wird nur im Dashboard gemeldet.
                if not b.get("exportieren", True):
                    continue
                # Die Hand war schneller: ihr Marker gilt. Gemerkt ohne Typ, damit die
                # Automatik ihn nie umbenennt — sie ergänzt höchstens ein fehlendes Ende.
                hand = await _hand_marker_fuer(db, session.id, b, zustand.get("gelesen_s", 0.0))
                if hand is not None:
                    _bekannt[nr] = (hand.id, None)
                    if b.get("ende_s") is not None and hand.ended_at_ts is None:
                        hand.ended_at_ts = b["ende_s"]
                        fertig.append(hand.id)
                    continue
                marker = Marker(
                    session_id=session.id,
                    marker_type=b["typ"] if b["typ"] != "?" else "Unbenannt",
                    placed_at_ts=b["start_s"],
                    placed_at_wall=session.started_at + timedelta(seconds=b["start_s"]),
                )
                db.add(marker)
                await db.commit()
                await db.refresh(marker)
                db.add(AutoMarker(marker_id=marker.id, typ=marker.marker_type))
                _bekannt[nr] = (marker.id, marker.marker_type)
                continue

            marker_id, zuletzt = eintrag
            marker = await db.get(Marker, marker_id)
            if marker is None:
                continue

            # Typ nachschärfen, solange niemand von Hand eingegriffen hat
            if marker.marker_type == zuletzt and b["typ"] not in ("?", zuletzt):
                marker.marker_type = b["typ"]
                _bekannt[nr] = (marker_id, b["typ"])
                auto = await db.get(AutoMarker, marker_id)
                if auto is not None:
                    auto.typ = b["typ"]

            if b.get("ende_s") is not None and marker.ended_at_ts is None:
                marker.ended_at_ts = b["ende_s"]
                fertig.append(marker.id)
        await db.commit()
        for marker_id in fertig:
            ki_live.einreihen(session.id, marker_id)
        # Ablauf neben der Aufnahme aktuell halten, damit der Ordner für sich steht
        await session_dokument.schreiben(session.id, db)


async def _konfiguration_nachschieben(client) -> None:
    """Dem Audio-Dienst die Beitragsarten schieben, falls er keine kennt.

    Nötig, weil er sie nur im Speicher hält: nach einem Neustart des Dienstes — und
    der passiert auch mitten im Betrieb — wäre er sonst ohne Erkennungskonfiguration.
    Die API ist die Besitzerin, also holt sie es nach.
    """
    try:
        r = await client.get(f"{AUDIO_SERVICE_URL}/api/config/erkennung")
        if r.status_code != 200:
            return
        if r.json().get("beitragsarten"):
            return      # kennt schon welche, nichts zu tun
    except (httpx.HTTPError, ValueError):
        return
    await typen.an_audio_dienst_schieben()


async def schleife():
    """Läuft mit der API, fragt den Automodus ab und schreibt die Marker."""
    takte = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            await asyncio.sleep(TAKT_S)
            # Etwa jede Minute prüfen, ob der Audio-Dienst seine Konfiguration noch hat.
            takte += 1
            if takte % 30 == 1:
                await _konfiguration_nachschieben(client)
            try:
                r = await client.get(f"{AUDIO_SERVICE_URL}/api/session/automode")
                if r.status_code != 200:
                    continue
                zustand = r.json()
            except (httpx.HTTPError, ValueError):
                continue        # Audio-Dienst weg: beim nächsten Takt wieder versuchen
            if not zustand.get("aktiv"):
                if _bekannt:
                    zuruecksetzen()
                continue
            try:
                await _abgleichen(zustand)
            except Exception:       # noqa: BLE001 — eine Aufnahme darf daran nie scheitern
                continue
