"""Läuft alles, was ein Gottesdienst braucht? Und die .env des Audio-Dienstes.

Die Prüfungen selbst macht der Audio-Dienst (audio-service/dienst.py) — er kennt seine
Adressen, sein Gerät und die Modelle. Hier kommt dazu, ob er überhaupt antwortet, und
alles wird übersetzt: die Zeile steht oben in der Checkliste, auf Deutsch, Englisch
oder Russisch.
"""
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from sprache import uebersetze
from vorlagen import templates

router = APIRouter(prefix="/api/dienste", tags=["dienste"])

AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")

DIENSTE = {
    "audio": "Audio-Dienst",
    "eingang": "Audio-Eingang",
    "whisper": "Live-Transkript (whisper)",
    "ollama": "Sprachmodell (Ollama)",
    "nas": "NAS",
}
GRUENDE = {
    "nicht_erreichbar": "nicht erreichbar",
    "kein_eingang": "kein Signal vom Gerät",
    "modell_fehlt": "Modell fehlt",
    "abgeschaltet": "abgeschaltet",
    "nicht_eingerichtet": "nicht eingerichtet",
}
LAEUFT = {"aufnahme": "Aufnahme", "stream": "Stream", "telefon": "Telefon"}


def _uebersetzt(pruefungen: list[dict]) -> list[dict]:
    return [{**p, "name": uebersetze(DIENSTE.get(p["dienst"], p["dienst"])),
             "grund_text": uebersetze(GRUENDE[p["grund"]]) if p.get("grund") in GRUENDE else ""}
            for p in pruefungen]


async def _audio(methode: str, pfad: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.request(methode, f"{AUDIO_SERVICE_URL}{pfad}", **kwargs)


@router.get("/status")
async def status(request: Request):
    try:
        r = await _audio("GET", "/api/dienst/status")
        daten = r.json()
        pruefungen = [{"dienst": "audio", "ok": True, "grund": None}] + daten["pruefungen"]
    except (httpx.HTTPError, ValueError, KeyError):
        pruefungen = [{"dienst": "audio", "ok": False, "grund": "nicht_erreichbar"}]
    pruefungen = _uebersetzt(pruefungen)
    ergebnis = {"alle_ok": all(p["ok"] for p in pruefungen), "pruefungen": pruefungen}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/dienste_status.html",
                                          {"request": request, **ergebnis})
    return ergebnis


@router.get("/umgebung")
async def umgebung_lesen():
    try:
        r = await _audio("GET", "/api/dienst/umgebung")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    daten = r.json()
    for w in daten["werte"]:
        w["name"], w["hilfe"] = uebersetze(w["name"]), uebersetze(w["hilfe"])
    return daten


class Umgebung(BaseModel):
    werte: dict


@router.put("/umgebung")
async def umgebung_schreiben(u: Umgebung):
    try:
        r = await _audio("PUT", "/api/dienst/umgebung", json={"werte": u.werte})
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if r.status_code == 422:
        raise HTTPException(status_code=422, detail=uebersetze("Ein Wert hat nicht die erwartete Form")
                            + ": " + r.json().get("detail", ""))
    r.raise_for_status()
    return await umgebung_lesen()


@router.post("/neustart")
async def neustart():
    """Startet den Audio-Dienst neu — nie während Aufnahme, Stream oder Telefon."""
    try:
        r = await _audio("POST", "/api/dienst/neustart")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail=uebersetze("Audio-Dienst nicht erreichbar"))
    if r.status_code == 409:
        laeuft = (await _audio("GET", "/api/dienst/umgebung")).json().get("laeuft", [])
        raise HTTPException(status_code=409, detail=uebersetze("Neustart gesperrt, solange das läuft:")
                            + " " + ", ".join(uebersetze(LAEUFT.get(x, x)) for x in laeuft))
    r.raise_for_status()
    return {"status": "neustart"}
