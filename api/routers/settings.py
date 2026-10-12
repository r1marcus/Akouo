from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

STREAM_TARGET_KEY = "stream_target_url"
AUTO_REFINE_KEY = "auto_refine_boundaries"   # "1"/"0" — Schnittkanten automatisch korrigieren


async def get_flag(db: AsyncSession, key: str, default: bool = True) -> bool:
    """Ein/Aus-Einstellung lesen; fehlt der Eintrag, gilt der Standard."""
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None or setting.value is None:
        return default
    return setting.value.strip() not in ("0", "false", "False", "")


class SettingUpdate(BaseModel):
    value: Optional[str] = None


@router.get("/{key}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    """Return the value for a settings key, or null if not set."""
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return {"key": key, "value": setting.value if setting else None}


@router.put("/{key}")
async def put_setting(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Create or update a settings key — accepts JSON as well as form data (HTMX posts forms)."""
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        value = payload.get("value") if isinstance(payload, dict) else None
    else:
        form = await request.form()
        value = form.get("value")
    body = SettingUpdate(value=None if value is None else str(value))
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = Setting(key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    await db.commit()
    await db.refresh(setting)
    if request.headers.get("HX-Request"):
        return HTMLResponse(f'<span class="text-emerald-400">Gespeichert {datetime.now():%H:%M}</span>')
    return {"key": setting.key, "value": setting.value}
