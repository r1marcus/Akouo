"""KI-Auswertung schon während des Gottesdienstes — ein fertiger Track nach dem anderen.

Der Tontechniker soll noch in der Kirche sehen, ob Titel und Bibelstellen passen. Ollama,
Whisper und die Automatik teilen sich denselben Rechner; deshalb läuft hier immer nur eine
Auswertung, und nur für Tracks, die schon ein Ende haben. Ein Fehler hält nichts auf: die
Auswertung holt der Export ohnehin nach.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import HTTPException
from sqlalchemy import select

import einstellungen
from database import AsyncSessionLocal
from models import Marker, SegmentInsight

log = logging.getLogger(__name__)

_sperre = asyncio.Lock()
_offen: set[int] = set()
_laeufer: set[asyncio.Task] = set()       # starke Referenzen, sonst räumt der GC sie weg


def einreihen(session_id: int, marker_id: int) -> None:
    """Merkt den Track vor. Mehrfaches Einreihen desselben Tracks ist harmlos."""
    if marker_id in _offen:
        return
    _offen.add(marker_id)
    try:
        task = asyncio.get_running_loop().create_task(_auswerten(session_id, marker_id))
    except RuntimeError:            # kein laufender Loop (Skript, Test ohne asyncio)
        _offen.discard(marker_id)
        return
    _laeufer.add(task)
    task.add_done_callback(_laeufer.discard)


async def _auswerten(session_id: int, marker_id: int) -> None:
    from routers.insights import create_insight      # hier, sonst kreisen die Importe
    try:
        async with _sperre:
            async with AsyncSessionLocal() as db:
                if not (await einstellungen.alle_lesen(db)).get("ki_live", True):
                    return
                marker = await db.get(Marker, marker_id)
                if marker is None or marker.ended_at_ts is None:
                    return
                vorhanden = (await db.execute(select(SegmentInsight).where(
                    SegmentInsight.marker_id == marker_id))).scalar_one_or_none()
                if vorhanden is not None and vorhanden.ki_gelaufen:
                    return
                await create_insight(session_id, marker_id, db)
    except (HTTPException, httpx.HTTPError, OSError) as e:
        log.info("KI live für Marker %s nicht möglich: %s", marker_id, getattr(e, "detail", e))
    finally:
        _offen.discard(marker_id)
