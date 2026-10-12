"""AI metadata per segment — title, summary, Bible references, keywords (AI-02)."""
import json
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Marker, RecordingSession, SegmentInsight
from routers.segments import _build_segments

router = APIRouter(prefix="/api/sessions", tags=["insights"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _as_dict(row: SegmentInsight) -> dict:
    return {
        "marker_id": row.marker_id,
        "title": row.title,
        "summary": row.summary,
        "bible_refs": json.loads(row.bible_refs) if row.bible_refs else [],
        "keywords": json.loads(row.keywords) if row.keywords else [],
        "speaker": row.speaker,
        "model": row.model,
        "transcript_chars": len(row.transcript or ""),
    }


@router.get("/{session_id}/insights")
async def list_insights(session_id: int, db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(SegmentInsight).where(SegmentInsight.session_id == session_id))
    return [_as_dict(row) for row in rows.scalars().all()]


@router.post("/{session_id}/segments/{marker_id}/insights")
async def create_insight(
    session_id: int,
    marker_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Run the local model over this segment's transcript and store what it found."""
    session = await db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    marker = await db.get(Marker, marker_id)
    if marker is None or marker.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Marker {marker_id} not found in session {session_id}")

    segments = await _build_segments(session, db)
    segment = next((s for s in segments if s["marker_id"] == marker_id), None)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment for marker not found")

    async with httpx.AsyncClient(timeout=420.0) as client:
        try:
            response = await client.post(
                f"{AUDIO_SERVICE_URL}/api/session/insights",
                json={
                    "wav_path": session.wav_path,
                    "start_seconds": segment["start_seconds"],
                    "end_seconds": segment["end_seconds"],
                    "marker_type": marker.marker_type,
                },
            )
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Audio service unreachable")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
    data = response.json()
    if data.get("error"):
        raise HTTPException(status_code=409, detail=data["error"])

    existing = await db.execute(select(SegmentInsight).where(SegmentInsight.marker_id == marker_id))
    row: Optional[SegmentInsight] = existing.scalar_one_or_none()
    if row is None:
        row = SegmentInsight(session_id=session_id, marker_id=marker_id)
        db.add(row)
    row.title = data.get("titel")
    row.summary = data.get("zusammenfassung")
    row.bible_refs = json.dumps(data.get("bibelstellen") or [], ensure_ascii=False)
    row.keywords = json.dumps(data.get("schlagwoerter") or [], ensure_ascii=False)
    row.speaker = data.get("sprecher")
    row.transcript = data.get("transcript")
    row.model = data.get("model")
    await db.commit()
    await db.refresh(row)
    return _as_dict(row)
