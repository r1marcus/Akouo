"""Phase 7 segment listing + export router (EXP-01, EXP-02)."""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import NamedTuple, Optional

import httpx
import pathlib as _pl
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (AutoMarker, CopyCenterAblage, Marker, RecordingSession, SegmentExport,
                    SegmentInsight)
import einstellungen
import nas_pfad
import session_dokument
import typen
from routers.service import _stored_current
from sprache import uebersetze

router = APIRouter(prefix="/api/sessions", tags=["segments"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

# Resolve templates relative to api/ package, mirroring main.py's BASE_DIR.
_BASE = _pl.Path(__file__).resolve().parent.parent
from vorlagen import templates   # die eine Jinja-Umgebung, siehe api/vorlagen.py


def _safe_filename_part(s: str) -> str:
    """Make a string filesystem-safe (used for service_type + couple_name in MP3 names)."""
    keep = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            keep.append(ch)
        elif ch == " ":
            keep.append("_")
    cleaned = "".join(keep).strip("._-")
    return cleaned or "Segment"


async def _nas_folder(session: RecordingSession, started: datetime,
                      db: AsyncSession) -> str:
    """Ordner innerhalb der NAS-Freigabe, aus der eingestellten Vorlage.

    Die Vorlage kommt aus den Einstellungen; eine Gottesdienstart kann eine eigene
    mitbringen (ServiceType.nas_vorlage). Die Standardwerte reproduzieren die
    gewachsene Ablage genau — siehe api/nas_pfad.py.

    Fällt eine Vorlage aus, wird nicht geraten: dann gilt der Standard aus nas_pfad,
    damit eine kaputte Einstellung keine Aufnahme ohne Ziel lässt.
    """
    werte = nas_pfad.werte_fuer(session, started)

    eigene = typen.nas_vorlage_fuer(session.service_type)
    if eigene:
        vorlage = eigene
    else:
        werte_db = await einstellungen.alle_lesen(db)
        if session.service_type == "Hochzeit":
            vorlage = werte_db.get("nas_vorlage_hochzeit")
        elif session.service_type == "Sonstiges":
            vorlage = werte_db.get("nas_vorlage_sonstiges")
        else:
            vorlage = werte_db.get("nas_vorlage_standard")

    try:
        return nas_pfad.rendern(vorlage, werte)
    except nas_pfad.VorlagenFehler:
        schluessel = ("nas_vorlage_hochzeit" if session.service_type == "Hochzeit"
                      else "nas_vorlage_sonstiges" if session.service_type == "Sonstiges"
                      else "nas_vorlage_standard")
        return nas_pfad.rendern(nas_pfad.STANDARD_VORLAGEN[schluessel], werte)


def _nas_track_name(index: int, marker_type: str) -> str:
    """Track-03 - Kinderchor.mp3 — die Nummer hält die Reihenfolge, der Typ sagt, was es ist.

    Gleich lesbar wie der Typ, nur ohne Zeichen, an denen SMB oder Windows scheitern.
    """
    typ = re.sub(r'[\\/:*?"<>|]', "", marker_type).strip(" .") or "Beitrag"
    return f"Track-{index:02d} - {typ}.mp3"


def _album(folder: str) -> str:
    """Albumname aus dem NAS-Ordner: der Bereich und reine Jahresordner fallen weg.

    Gottesdienste/2026-10-18 Sonntag 1. morgens        → 2026-10-18 Sonntag 1. morgens
    Hochzeiten/2026/2026-06-06 Robin & Laura/2. Teil   → 2026-06-06 Robin & Laura – 2. Teil
    """
    teile = [t for t in PurePosixPath(folder).parts[1:] if not re.fullmatch(r"\d{4}", t)]
    return " – ".join(teile) or PurePosixPath(folder).name


def _kommentar(insight: Optional[SegmentInsight]) -> Optional[str]:
    """Zusammenfassung und Bibelstellen — nur, wenn die KI-Auswertung schon gelaufen ist."""
    if insight is None:
        return None
    teile = [insight.summary.strip()] if insight.summary and insight.summary.strip() else []
    try:
        stellen = json.loads(insight.bible_refs or "[]")
    except ValueError:
        stellen = []
    if stellen:
        teile.append(", ".join(str(st) for st in stellen))
    return "\n\n".join(teile) or None


async def _build_segments(session: RecordingSession, db: AsyncSession) -> list[dict]:
    """Compute segment boundaries from markers.

    A marker with ended_at_ts spans exactly that range — everything between two tracks
    (setup, talking) belongs to no segment. Markers without an end fall back to the old
    rule: up to the next marker, or to the end of the session.
    """
    markers_q = await db.execute(
        select(Marker).where(Marker.session_id == session.id).order_by(Marker.placed_at_ts)
    )
    markers = list(markers_q.scalars().all())
    if not markers:
        return []

    # Session end in WAV-relative seconds: prefer (stopped_at - started_at), fallback to last marker + 60s
    if session.stopped_at and session.started_at:
        session_end_seconds = max(
            (session.stopped_at - session.started_at).total_seconds(),
            markers[-1].placed_at_ts + 1.0,
        )
    else:
        session_end_seconds = markers[-1].placed_at_ts + 60.0

    # Existing exports keyed by marker_id for status display
    exports_q = await db.execute(
        select(SegmentExport).where(SegmentExport.session_id == session.id)
    )
    exports_by_marker: dict[int, SegmentExport] = {
        e.marker_id: e for e in exports_q.scalars().all()
    }

    segments: list[dict] = []
    for idx, m in enumerate(markers):
        start = float(m.placed_at_ts) + float(m.start_nudge or 0.0)
        if m.ended_at_ts is not None:
            end = float(m.ended_at_ts)
        elif idx + 1 < len(markers):
            nxt = markers[idx + 1]
            end = float(nxt.placed_at_ts) + float(nxt.start_nudge or 0.0)
        else:
            end = float(session_end_seconds)
        # apply end_nudge of the current marker as the segment's end-trim
        end = end + float(m.end_nudge or 0.0)
        if end <= start:
            end = start + 0.5  # guarantee non-empty range; export endpoint will still validate

        existing = exports_by_marker.get(m.id)
        segments.append({
            "marker_id": m.id,
            "marker_type": m.marker_type,
            "index": idx + 1,
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(end - start, 3),
            "nudge_manual": bool(m.nudge_manual),
            # Gemeindegesang, Gebet, Ansagen werden markiert, damit der Ablauf
            # vollständig ist — beim Sammel-Export aber übersprungen.
            "exportieren": typen.exportieren(m.marker_type),
            "export": None if existing is None else {
                "id": existing.id,
                "mp3_filename": existing.mp3_filename,
                "nas_status": existing.nas_status,
                "nas_error": existing.nas_error,
                "uploaded_at": existing.uploaded_at.isoformat() if existing.uploaded_at else None,
            },
        })

    # Auf dem NAS zählt das Archiv fortlaufend — Track-01, -02, … ohne Lücken für
    # Gemeindegesang, Gebet und Ansagen, die beim Sammel-Export nicht mitgehen. Wer so
    # einen Beitrag doch einzeln exportiert, bekommt eine Nummer hinter allen regulären,
    # damit er keinem von ihnen den Platz wegnimmt.
    nas_nr = 0
    for nur_regulaere in (True, False):
        for seg in segments:
            if seg["exportieren"] is nur_regulaere:
                nas_nr += 1
                seg["nas_nr"] = nas_nr
    return segments


@router.get("/latest-stopped/segments")
async def latest_stopped_segments(request: Request, db: AsyncSession = Depends(get_db)):
    # A running session owns the dashboard — the previous service's review would only confuse.
    active = await db.execute(
        select(RecordingSession).where(RecordingSession.status.in_(["recording", "paused"]))
    )
    if active.scalars().first() is not None:
        return HTMLResponse("")

    q = await db.execute(
        select(RecordingSession)
        .where(RecordingSession.status == "stopped")
        .order_by(RecordingSession.stopped_at.desc())
    )
    sess = q.scalars().first()
    if sess is None:
        # Empty fragment — host stays empty until a session has been stopped
        return HTMLResponse("")

    # Nur die Review zum aktuell gewählten Gottesdienst, und nur für Aufnahmen, die
    # nach dieser Auswahl begonnen haben. Eine neue Auswahl heißt von vorn — sonst
    # steht die Aufnahme des vorigen Gottesdienstes noch in der Exportliste.
    current = await _stored_current(db)
    if current["service_type"] and sess.service_type != current["service_type"]:
        return HTMLResponse("")
    gewaehlt = current.get("gewaehlt_am")
    if gewaehlt and sess.started_at:
        try:
            if sess.started_at < datetime.fromisoformat(gewaehlt):
                return HTMLResponse("")
        except (ValueError, TypeError):
            pass        # unlesbarer Zeitstempel darf die Karte nicht verschlucken
    segments = await _build_segments(sess, db)
    return templates.TemplateResponse(
        "partials/segment_review.html",
        {
            "request": request,
            "session_id": sess.id,
            "service_type": sess.service_type,
            "started_at": sess.started_at.isoformat() if sess.started_at else None,
            "stopped_at": sess.stopped_at.isoformat() if sess.stopped_at else None,
            "segments": segments,
        },
    )


@router.get("/archiv")
async def archiv(db: AsyncSession = Depends(get_db)):
    """Alle abgeschlossenen Aufnahmen für die Archivansicht.

    Bewusst ohne Pegel und Transkript — die Liste soll schnell sein. Das Nötige für
    eine Entscheidung steht drin: wann, welcher Gottesdienst, wie viele Beiträge, und
    ob schon exportiert wurde.
    """
    q = await db.execute(
        select(RecordingSession)
        .where(RecordingSession.status == "stopped")
        .order_by(RecordingSession.started_at.desc())
    )
    sessions = q.scalars().all()

    marker_q = await db.execute(select(Marker.session_id, Marker.id))
    marker_zahl: dict[int, int] = {}
    for sid, _ in marker_q.all():
        marker_zahl[sid] = marker_zahl.get(sid, 0) + 1

    export_q = await db.execute(select(SegmentExport.session_id, SegmentExport.id))
    export_zahl: dict[int, int] = {}
    for sid, _ in export_q.all():
        export_zahl[sid] = export_zahl.get(sid, 0) + 1

    eintraege = []
    for sess in sessions:
        dauer = None
        if sess.started_at and sess.stopped_at:
            dauer = int((sess.stopped_at - sess.started_at).total_seconds())
        datei = _pl.Path(sess.wav_path) if sess.wav_path else None
        eintraege.append({
            "id": sess.id,
            "gottesdienst": sess.service_type,
            "thema": sess.thema,
            "begonnen": sess.started_at.isoformat() if sess.started_at else None,
            "dauer_s": dauer,
            "beitraege": marker_zahl.get(sess.id, 0),
            "exporte": export_zahl.get(sess.id, 0),
            # Ohne Aufnahmedatei lässt sich nichts nachexportieren — das muss man sehen
            "aufnahme_da": bool(datei and datei.exists()),
        })
    return {"aufnahmen": eintraege}


@router.get("/{session_id}/segments")
async def list_segments(
    session_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    segments = await _build_segments(session, db)
    payload = {
        "session_id": session.id,
        "service_type": session.service_type,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
        "segments": segments,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/segment_review.html",
            {"request": request, **payload},
        )
    return payload


class RefineResult(NamedTuple):
    """Outcome of a refine run: boundaries moved, plus markers left alone because of handwork."""
    shifted: int
    skipped_manual: int


async def refine_session(session: RecordingSession, db: AsyncSession) -> RefineResult:
    """Snap every segment of a session to the real audio edges.

    Markers the operator nudged by hand are skipped completely — neither reset nor overwritten.
    """
    markers_q = await db.execute(
        select(Marker).where(Marker.session_id == session.id).order_by(Marker.placed_at_ts)
    )
    markers = {m.id: m for m in markers_q.scalars().all()}
    shifted = 0
    skipped_manual = sum(1 for m in markers.values() if m.nudge_manual)
    # start from the raw timestamps, otherwise repeated runs would pile shift upon shift
    for marker in markers.values():
        if marker.nudge_manual:
            continue
        marker.start_nudge = 0.0
        marker.end_nudge = 0.0
    await db.commit()

    segments = await _build_segments(session, db)
    async with httpx.AsyncClient(timeout=120.0) as client:
        for seg in segments:
            marker = markers.get(seg["marker_id"])
            if marker is None or marker.nudge_manual:
                continue
            try:
                r = await client.post(
                    f"{AUDIO_SERVICE_URL}/api/session/refine-boundaries",
                    json={
                        "wav_path": session.wav_path,
                        "start_seconds": seg["start_seconds"],
                        "end_seconds": seg["end_seconds"],
                    },
                )
            except httpx.ConnectError:
                raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
            if r.status_code != 200:
                continue
            data = r.json()
            marker.start_nudge = float(data.get("start_delta") or 0.0)
            marker.end_nudge = float(data.get("end_delta") or 0.0)
            if marker.start_nudge or marker.end_nudge:
                shifted += 1
    await db.commit()
    return RefineResult(shifted=shifted, skipped_manual=skipped_manual)


@router.post("/{session_id}/segments/refine")
async def refine_segments(session_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Manual trigger for the boundary correction (button in the segment review)."""
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not session.wav_path:
        raise HTTPException(status_code=409, detail=uebersetze("Session hat keine Aufnahmedatei"))
    await refine_session(session, db)
    return await list_segments(session_id, request, db)


class NeuErkannt(NamedTuple):
    """Ergebnis von „Neu erkennen": ersetzt, neu gesetzt, aus Rücksicht stehen gelassen."""
    ersetzt: int
    neu: int
    behalten: int


def _von_hand(marker: Marker, seg: dict, auto_typ: Optional[str]) -> bool:
    """Hat ein Mensch diesen Marker gesetzt oder angefasst? Dann bleibt er stehen.

    Schon exportierte zählen mit: ihre MP3 liegt vielleicht schon im Archiv, und ein
    neuer Marker an ihrer Stelle bekäme eine zweite.
    """
    return (auto_typ is None                       # von Hand gesetzt
            or auto_typ != marker.marker_type      # von Hand umbenannt
            or marker.nudge_manual                 # von Hand verschoben
            or seg["export"] is not None)          # schon geschnitten


def _ueberdeckt(beitrag: dict, seg: dict, marker: Marker) -> bool:
    """Liegt ein neu erkannter Beitrag auf einem Marker, der stehen bleibt?

    Ein Marker ohne Ende reicht bis zum nächsten — welcher das nach dem Neuerkennen
    ist, steht aber noch nicht fest. Er zählt deshalb nur mit seinem Anfang: ein
    Beitrag, der ihn enthält, ist derselbe.
    """
    start = seg["start_seconds"]
    ende = seg["end_seconds"] if marker.ended_at_ts is not None else start
    return beitrag["start_s"] <= ende and start <= beitrag["ende_s"]


async def neu_erkennen_session(session: RecordingSession, db: AsyncSession) -> NeuErkannt:
    """Die fertige Aufnahme neu erkennen und die automatischen Marker ersetzen."""
    async with httpx.AsyncClient(timeout=900.0) as client:
        try:
            r = await client.post(f"{AUDIO_SERVICE_URL}/api/session/neu-erkennen",
                                  json={"wav_path": session.wav_path})
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=uebersetze("Neu erkennen ist fehlgeschlagen"))
    beitraege = r.json().get("beitraege", [])

    segments = await _build_segments(session, db)
    markers_q = await db.execute(select(Marker).where(Marker.session_id == session.id))
    markers = {m.id: m for m in markers_q.scalars().all()}
    auto_q = await db.execute(select(AutoMarker).where(AutoMarker.marker_id.in_(list(markers))))
    auto = {a.marker_id: a for a in auto_q.scalars().all()}

    bleiben, weg = [], []
    for seg in segments:
        m = markers[seg["marker_id"]]
        a = auto.get(m.id)
        (bleiben if _von_hand(m, seg, a.typ if a else None) else weg).append((seg, m))

    for _, m in weg:
        insight = (await db.execute(
            select(SegmentInsight).where(SegmentInsight.marker_id == m.id))).scalar_one_or_none()
        if insight is not None:
            await db.delete(insight)
        if m.id in auto:
            await db.delete(auto[m.id])
        await db.delete(m)
    await db.flush()

    neu = 0
    for b in beitraege:
        # Wie live: Gemeindegesang, Gebet und Ansagen bekommen keinen Marker
        if not b.get("exportieren", True) or b.get("ende_s") is None:
            continue
        if any(_ueberdeckt(b, seg, m) for seg, m in bleiben):
            continue            # die Hand hat Vorrang
        typ = b["typ"] if b.get("typ") not in (None, "?") else "Unbenannt"
        marker = Marker(
            session_id=session.id,
            marker_type=typ,
            placed_at_ts=b["start_s"],
            placed_at_wall=session.started_at + timedelta(seconds=b["start_s"]),
            ended_at_ts=b["ende_s"],
        )
        db.add(marker)
        await db.flush()
        db.add(AutoMarker(marker_id=marker.id, typ=typ))
        neu += 1
    await db.commit()

    # Kanten wie nach jedem Stopp auf den echten Ton ziehen — von Hand Verschobenes
    # lässt refine_session ohnehin in Ruhe.
    await refine_session(session, db)
    await session_dokument.schreiben(session.id, db)
    return NeuErkannt(ersetzt=len(weg), neu=neu, behalten=len(bleiben))


@router.post("/{session_id}/segments/neu-erkennen")
async def neu_erkennen(session_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Knopf „Neu erkennen" in der Segmentansicht — nur nach dem Stopp."""
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not session.wav_path:
        raise HTTPException(status_code=409, detail=uebersetze("Session hat keine Aufnahmedatei"))
    if session.status in ("recording", "paused"):
        raise HTTPException(status_code=409,
                            detail=uebersetze("Neu erkennen geht erst nach dem Stopp"))
    await neu_erkennen_session(session, db)
    return await list_segments(session_id, request, db)


async def _copycenter_ordner(session: RecordingSession, started: datetime,
                             db: AsyncSession) -> Optional[str]:
    """Der CopyCenter-Ordner dieser Aufnahme — beim ersten Track entschieden, dann fest."""
    gemerkt = await db.get(CopyCenterAblage, session.id)
    if gemerkt is not None:
        return gemerkt.ordner
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{AUDIO_SERVICE_URL}/api/session/nas-erster-freier",
                                  json={"kandidaten": nas_pfad.copycenter_kandidaten(started)})
    except httpx.ConnectError:
        return None
    if r.status_code != 200:
        return None                 # lieber kein CopyCenter als ein geratener Ordner
    ordner = r.json().get("folder")
    if not ordner:
        return None
    db.add(CopyCenterAblage(session_id=session.id, ordner=ordner))
    await db.commit()
    return ordner


@router.post("/{session_id}/segments/{marker_id}/export")
async def export_segment(
    session_id: int,
    marker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not session.wav_path:
        raise HTTPException(status_code=409, detail=uebersetze("Session hat keine Aufnahmedatei"))

    marker = await db.get(Marker, marker_id)
    if marker is None or marker.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} not found in session {session_id}")

    # Recompute boundaries (single source of truth)
    segments = await _build_segments(session, db)
    seg = next((s for s in segments if s["marker_id"] == marker_id), None)
    if seg is None:
        raise HTTPException(status_code=404, detail=uebersetze("Kein Segment zu diesem Marker"))

    # Filename: YYYY-MM-DD_ServiceType_NN_MarkerType.mp3
    date_part = (session.started_at.strftime("%Y-%m-%d")
                 if session.started_at else datetime.now().strftime("%Y-%m-%d"))
    parts = [date_part, _safe_filename_part(session.service_type)]
    if session.couple_name:
        parts.append(_safe_filename_part(session.couple_name))
    if session.thema:
        parts.append(_safe_filename_part(session.thema))
    if session.wedding_part:
        parts.append(f"Teil{session.wedding_part}")
    parts.append(f"{seg['index']:02d}")
    parts.append(_safe_filename_part(marker.marker_type))
    mp3_filename = "_".join(parts) + ".mp3"

    # Remote path follows the archive's own layout — see _nas_folder. The local file keeps
    # its descriptive name; on the NAS it is "Track-NN - Typ.mp3" (see _nas_track_name).
    started = session.started_at or datetime.now()
    if session.nas_folder:
        folder = session.nas_folder          # decided on the first upload, kept ever after
    else:
        folder = await _nas_folder(session, started, db)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                fr = await client.get(f"{AUDIO_SERVICE_URL}/api/session/nas-folder",
                                      params={"folder": folder})
            if fr.status_code == 200:
                folder = fr.json().get("folder", folder)
        except httpx.ConnectError:
            pass                              # upload will fail and report it on its own
        session.nas_folder = folder
        await db.commit()
    remote_subpath = f"{folder}/{_nas_track_name(seg['nas_nr'], marker.marker_type)}"
    # Defence in depth — never trust constructed path either
    if ".." in PurePosixPath(remote_subpath).parts:
        raise HTTPException(status_code=400, detail="invalid remote_subpath")

    # Replacing is only allowed where we uploaded that very file before. Everything else in
    # the share was put there by hand over the years and must not be clobbered by a re-export.
    prior_q = await db.execute(
        select(SegmentExport)
        .where(SegmentExport.session_id == session_id)
        .where(SegmentExport.marker_id == marker_id)
    )
    prior = prior_q.scalar_one_or_none()
    allow_overwrite = prior is not None and prior.nas_status == "uploaded"

    # Track 3/18: gezählt wird, was beim Sammel-Export mitgeht — so wie auf dem NAS
    gesamt = max(sum(1 for x in segments if x["exportieren"]), seg["nas_nr"])
    werte_db = await einstellungen.alle_lesen(db)
    insight = (await db.execute(
        select(SegmentInsight).where(SegmentInsight.marker_id == marker_id))).scalar_one_or_none()
    tags = {
        "title": marker.marker_type,
        "album": _album(folder),
        "track": f"{seg['nas_nr']}/{gesamt}",
        "date": started.strftime("%Y-%m-%d"),
        "genre": "Gottesdienst",
        "language": "deu",
    }
    interpret = (werte_db.get("export_interpret") or "").strip()
    if interpret:
        tags["artist"] = tags["album_artist"] = interpret
    kommentar = _kommentar(insight)
    if kommentar:
        tags["comment"] = kommentar

    payload = {
        "wav_path": session.wav_path,
        "start_seconds": seg["start_seconds"],
        "end_seconds": seg["end_seconds"],
        "mp3_filename": mp3_filename,
        "remote_subpath": remote_subpath,
        "allow_overwrite": allow_overwrite,
        "tags": tags,
    }
    # Gottesdienste gehen zusätzlich ins CopyCenter — Hochzeiten und Sonstiges nicht,
    # dort gibt es für sie keine Ablage.
    if werte_db.get("nas_copycenter") and folder.startswith("Gottesdienste/"):
        cc_ordner = await _copycenter_ordner(session, started, db)
        if cc_ordner:
            payload["copycenter_folder"] = cc_ordner
            payload["copycenter_label"] = nas_pfad.copycenter_beschriftung(
                session.service_type, session.thema)

    # 90-min sessions slice quickly; segments are <= session length so 180s is generous.
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            r = await client.post(f"{AUDIO_SERVICE_URL}/api/session/segment-export", json=payload)
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.json().get("detail"))
    data = r.json()

    nas = data.get("nas", {}) or {}
    # Ein Fehler im CopyCenter ändert nichts daran, dass der Track im Archiv liegt —
    # der Status bleibt "uploaded", sonst würde ein erneuter Export das eigene File
    # nicht mehr ersetzen dürfen. Sichtbar wird er über den Fehlertext.
    nas_fehler = nas.get("error")
    copycenter = data.get("copycenter")
    if copycenter and copycenter.get("status") != "uploaded":
        nas_fehler = f"{uebersetze('CopyCenter fehlgeschlagen')}: {copycenter.get('error') or ''}"
    uploaded_at_str = nas.get("uploaded_at")
    uploaded_at_dt: Optional[datetime] = None
    if uploaded_at_str:
        try:
            uploaded_at_dt = datetime.fromisoformat(uploaded_at_str)
        except ValueError:
            uploaded_at_dt = None

    # Idempotency: replace existing export row for this (session, marker)
    existing_q = await db.execute(
        select(SegmentExport)
        .where(SegmentExport.session_id == session_id)
        .where(SegmentExport.marker_id == marker_id)
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        existing.mp3_path = data["mp3_path"]
        existing.mp3_filename = data["mp3_filename"]
        existing.duration_ms = data["export"].get("duration_ms")
        existing.nas_status = nas.get("status", "skipped")
        existing.nas_error = nas_fehler
        existing.nas_path = nas.get("nas_path")
        existing.uploaded_at = uploaded_at_dt
        existing.start_seconds = seg["start_seconds"]
        existing.end_seconds = seg["end_seconds"]
        row = existing
    else:
        row = SegmentExport(
            session_id=session_id,
            marker_id=marker_id,
            marker_type=marker.marker_type,
            start_seconds=seg["start_seconds"],
            end_seconds=seg["end_seconds"],
            mp3_path=data["mp3_path"],
            mp3_filename=data["mp3_filename"],
            duration_ms=data["export"].get("duration_ms"),
            nas_path=nas.get("nas_path"),
            nas_status=nas.get("status", "skipped"),
            nas_error=nas_fehler,
            uploaded_at=uploaded_at_dt,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)

    if request.headers.get("HX-Request"):
        # Re-render the segment review partial so the row updates in place
        segments_after = await _build_segments(session, db)
        return templates.TemplateResponse(
            "partials/segment_review.html",
            {
                "request": request,
                "session_id": session.id,
                "service_type": session.service_type,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
                "segments": segments_after,
            },
        )
    return {
        "id": row.id,
        "mp3_filename": row.mp3_filename,
        "nas_status": row.nas_status,
        "nas_error": row.nas_error,
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
    }
