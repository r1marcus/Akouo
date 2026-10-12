"""Schreibt den Ablauf einer Aufnahme neben die WAV-Datei.

Bisher standen die Marker nur in der Datenbank. Geht die verloren, hat man zwar
noch Aufnahme und Transkript, weiß aber nicht mehr, wo die Predigt anfing — und
ein kopierter Session-Ordner ist ohne sie nur die halbe Information.

Deshalb landet neben `aufnahme.wav` und `transcript.jsonl` künftig auch:

    marker.json   vollständig und maschinenlesbar
    ablauf.txt    dasselbe zum Draufschauen

Beides wird bei jeder Änderung neu geschrieben, nicht fortgeschrieben — ein
Ablauf ist klein, und ein Neuschreiben kann nicht auseinanderlaufen.

Fehler hier dürfen nie eine Aufnahme aufhalten: Wer die Datei nicht schreiben
kann, protokolliert das und lässt es gut sein.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Marker, RecordingSession
import typen

log = logging.getLogger(__name__)

# Zuletzt geschriebener Inhalt je Ordner. Der Automodus ruft im Sekundentakt an,
# meistens ohne dass sich etwas geändert hat — dann wird nicht geschrieben.
_zuletzt: dict[str, str] = {}


def _atomar_schreiben(ziel: pathlib.Path, inhalt: str) -> None:
    """Erst daneben schreiben, dann umbenennen.

    Direkt in die Zieldatei zu schreiben hieße: stürzt der Rechner mitten im
    Schreiben ab, liegt dort eine halbe Datei. Das Umbenennen ist auf dem
    Dateisystem unteilbar — es gibt entweder die alte oder die neue Fassung.
    """
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ziel.parent,
                                     prefix=f".{ziel.name}.", delete=False) as f:
        f.write(inhalt)
        f.flush()
        os.fsync(f.fileno())     # erst auf die Platte, dann umbenennen
        vorlaeufig = pathlib.Path(f.name)
    os.replace(vorlaeufig, ziel)


def _zeit(sekunden: float) -> str:
    sekunden = max(0, int(sekunden))
    return f"{sekunden // 60}:{sekunden % 60:02d}"


async def _sammeln(session: RecordingSession, db: AsyncSession) -> dict:
    q = await db.execute(
        select(Marker).where(Marker.session_id == session.id).order_by(Marker.placed_at_ts)
    )
    marker = q.scalars().all()

    beitraege = []
    for nr, m in enumerate(marker, start=1):
        von = float(m.placed_at_ts) + float(m.start_nudge or 0.0)
        bis = None if m.ended_at_ts is None else float(m.ended_at_ts) + float(m.end_nudge or 0.0)
        beitraege.append({
            "nr": nr,
            "typ": m.marker_type,
            "von_s": round(von, 2),
            "bis_s": None if bis is None else round(bis, 2),
            "dauer_s": None if bis is None else round(bis - von, 2),
            "exportieren": typen.exportieren(m.marker_type),
            "grenzen_von_hand": bool(getattr(m, "nudge_manual", False)),
        })

    return {
        "session_id": session.id,
        "gottesdienst": session.service_type,
        "begonnen": session.started_at.isoformat() if session.started_at else None,
        "beendet": session.stopped_at.isoformat() if session.stopped_at else None,
        "aufnahme": pathlib.Path(session.wav_path).name if session.wav_path else None,
        "beitraege": beitraege,
    }


def _als_text(daten: dict) -> str:
    kopf = [
        f"{daten['gottesdienst']} — Session {daten['session_id']}",
        f"Beginn  {daten['begonnen'] or '—'}",
        f"Ende    {daten['beendet'] or 'läuft noch'}",
        "",
        f"{'Nr':>3}  {'von':>7}  {'bis':>7}  {'Dauer':>6}  {'Typ':<18}Export",
        "-" * 64,
    ]
    for b in daten["beitraege"]:
        bis = _zeit(b["bis_s"]) if b["bis_s"] is not None else "läuft"
        dauer = _zeit(b["dauer_s"]) if b["dauer_s"] is not None else "—"
        kopf.append(f"{b['nr']:>3}  {_zeit(b['von_s']):>7}  {bis:>7}  {dauer:>6}  "
                    f"{b['typ']:<18}{'ja' if b['exportieren'] else 'nein'}")
    nicht = [b for b in daten["beitraege"] if not b["exportieren"]]
    if nicht:
        kopf += ["", f"{len(nicht)} Beiträge sind dokumentiert, werden aber nicht exportiert."]
    return "\n".join(kopf) + "\n"


async def schreiben(session_id: int, db: AsyncSession) -> Optional[pathlib.Path]:
    """Ablauf der Session neben die Aufnahme schreiben. Gibt den Ordner zurück."""
    session = await db.get(RecordingSession, session_id)
    if session is None or not session.wav_path:
        return None
    ordner = pathlib.Path(session.wav_path).parent
    if not ordner.is_dir():
        return None          # Ordner von Hand gelöscht — nichts zu tun

    try:
        daten = await _sammeln(session, db)
        als_json = json.dumps(daten, indent=1, ensure_ascii=False)
        if _zuletzt.get(str(ordner)) == als_json:
            return ordner        # nichts Neues — kein Schreibvorgang
        _atomar_schreiben(ordner / "marker.json", als_json)
        _atomar_schreiben(ordner / "ablauf.txt", _als_text(daten))
        _zuletzt[str(ordner)] = als_json
        return ordner
    except OSError as exc:
        log.warning("Ablauf konnte nicht geschrieben werden (%s): %s", ordner, exc)
        return None
