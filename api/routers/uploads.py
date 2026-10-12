"""Phase 7 EXP-03: HTTP endpoint for external CMS to POST/GET MP3 files.

POST /api/uploads/mp3 — multipart/form-data; saves locally; pushes to NAS.
GET  /api/uploads/{segment_export_id} — streams the previously-exported MP3.
"""
from __future__ import annotations
import os
import pathlib
import secrets
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import SegmentExport
from sprache import uebersetze

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

# 200 MB hard cap. Sermons are ~80 MB at 192kbit/s for 60 minutes; 200 MB is generous.
MAX_BYTES = 200 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"audio/mpeg", "audio/mp3", "application/octet-stream"}

# Local uploads directory (separate from segment exports)
UPLOADS_DIR = pathlib.Path(os.environ.get(
    "UPLOADS_DIR",
    str(pathlib.Path.home() / "Recordings" / "Tontechnik" / "uploads"),
))


def _safe_basename(name: str) -> str:
    base = pathlib.PurePosixPath(name).name
    if not base or base in (".", ".."):
        raise HTTPException(status_code=422, detail="invalid filename")
    if not base.lower().endswith(".mp3"):
        raise HTTPException(status_code=422, detail="filename must end with .mp3")
    # Whitelist: allow alphanumeric, dot, underscore, hyphen
    cleaned_chars = []
    for ch in base:
        if ch.isalnum() or ch in (".", "_", "-"):
            cleaned_chars.append(ch)
        else:
            cleaned_chars.append("_")
    cleaned = "".join(cleaned_chars)
    if not cleaned or cleaned == ".mp3":
        raise HTTPException(status_code=422, detail="filename empty after sanitisation")
    return cleaned


@router.post("/mp3")
async def upload_mp3(
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
):
    """Accept an MP3 from an external system (e.g. website CMS) and store it.

    Validates content-type, enforces MAX_BYTES, sanitises filename, and best-effort
    pushes to NAS. Never raises 5xx for caller-fixable problems — only 4xx.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported content_type {file.content_type!r}; expected audio/mpeg",
        )

    safe_name = _safe_basename(file.filename or "upload.mp3")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # Prefix with timestamp + random to avoid collisions
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    token = secrets.token_hex(4)
    final_name = f"{stamp}_{token}_{safe_name}"
    dest = UPLOADS_DIR / final_name

    # Stream-copy with size enforcement
    written = 0
    chunk_size = 1024 * 1024
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"file exceeds {MAX_BYTES} bytes")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — clean up partial write, surface as 500
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"failed to write upload: {exc}")

    # NAS push — best effort
    remote_subpath = f"uploads/{datetime.now().strftime('%Y/%m')}/{final_name}"
    nas_status = "skipped"
    nas_error: Optional[str] = None
    nas_path: Optional[str] = None
    uploaded_at: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{AUDIO_SERVICE_URL}/api/uploads/nas-push",
                json={"local_path": str(dest), "remote_subpath": remote_subpath},
            )
        if r.status_code == 200:
            data = r.json()
            nas_status = data.get("status", "skipped")
            nas_error = data.get("error")
            nas_path = data.get("nas_path")
            uploaded_at = data.get("uploaded_at")
        else:
            nas_status = "failed"
            nas_error = f"audio-service returned {r.status_code}"
    except httpx.ConnectError:
        nas_status = "failed"
        nas_error = "audio service unreachable"

    return {
        "status": "stored",
        "filename": final_name,
        "local_path": str(dest),
        "size_bytes": written,
        "label": label,
        "nas_status": nas_status,
        "nas_error": nas_error,
        "nas_path": nas_path,
        "uploaded_at": uploaded_at,
    }


@router.get("/{segment_export_id}")
async def get_upload(segment_export_id: int, db: AsyncSession = Depends(get_db)):
    """Stream a previously-exported segment MP3 by SegmentExport.id (CMS pull path)."""
    row = await db.get(SegmentExport, segment_export_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"export {segment_export_id} not found")
    p = pathlib.Path(row.mp3_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=uebersetze("local MP3 missing"))
    return FileResponse(
        path=str(p),
        media_type="audio/mpeg",
        filename=row.mp3_filename,
    )
