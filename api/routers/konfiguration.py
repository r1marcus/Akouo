"""Alles einstellen, was einstellbar ist: Zahlenwerte, Gottesdienstarten, Beitragsarten,
Ansage-Regeln.

Vorher stand das meiste davon im Code — die Beitragsarten sogar doppelt. Dieser Router
ist die Schreibseite dazu; gelesen wird über typen.py und einstellungen.py.

Eine Regel durchgehend: geschützte Zeilen dürfen umsortiert und ausgeblendet, aber nicht
umbenannt oder gelöscht werden. Daran hängt Logik — "Predigt" und "Orchester" sind die
Anker der Programmzuordnung, "Hochzeit" schaltet die Brautpaar-Felder frei, und die
Ansage-Regeln verweisen per Name auf Beitragsarten. Ein Umbenennen würde die Erkennung
still aufhören lassen, und genau das soll die Oberfläche nicht zulassen.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import einstellungen
import nas_pfad
import typen
from database import get_db
from models import AnsageRegel, Marker, MarkerType, RecordingSession, ServiceType

router = APIRouter(prefix="/api/konfiguration", tags=["konfiguration"])


async def _neu_laden_und_schieben(db: AsyncSession) -> dict:
    """Nach jeder Änderung: Stand neu laden und dem Audio-Dienst mitgeben."""
    t = await typen.neu_laden(db)
    werte = await einstellungen.alle_lesen(db)
    return await typen.an_audio_dienst_schieben(t, einstellungen.zahlen_fuer_audio(werte))


# ─── Zahlenwerte und Schalter ───────────────────────────────────────────────

@router.get("")
async def alles_lesen(db: AsyncSession = Depends(get_db)):
    """Was die Einstellungsseite braucht, in einem Aufruf."""
    werte = await einstellungen.alle_lesen(db)
    arten = (await db.execute(
        select(ServiceType).order_by(ServiceType.sort_order, ServiceType.name)
    )).scalars().all()
    beitraege = (await db.execute(
        select(MarkerType).order_by(MarkerType.sort_order, MarkerType.name)
    )).scalars().all()
    regeln = (await db.execute(
        select(AnsageRegel).order_by(AnsageRegel.sort_order, AnsageRegel.id)
    )).scalars().all()
    return {
        "einstellungen": werte,
        "felder": [
            {
                "schluessel": f.schluessel, "bereich": f.bereich, "name": f.name,
                "art": f.art, "hilfe": f.hilfe, "einheit": f.einheit,
                "von": f.von, "bis": f.bis, "auswahl": list(f.auswahl),
                "wirkt": f.wirkt, "standard": f.standard,
            }
            for f in einstellungen.FELDER
        ],
        "gottesdienstarten": [
            {"id": a.id, "name": a.name, "sort_order": a.sort_order,
             "is_primary": a.is_primary, "active": a.active,
             "geschuetzt": a.geschuetzt, "nas_vorlage": a.nas_vorlage}
            for a in arten
        ],
        "beitragsarten": [
            {"id": b.id, "name": b.name, "sort_order": b.sort_order, "active": b.active,
             "exportieren": b.exportieren, "klang_klasse": b.klang_klasse,
             "anker": b.anker, "auto_marker": b.auto_marker,
             "dauer_min": b.dauer_min, "dauer_max": b.dauer_max,
             "geschuetzt": b.geschuetzt}
            for b in beitraege
        ],
        "ansage_regeln": [
            {"id": r.id, "muster": r.muster, "marker_type": r.marker_type,
             "sort_order": r.sort_order, "active": r.active}
            for r in regeln
        ],
    }


@router.put("/einstellung/{schluessel}")
async def einstellung_setzen(schluessel: str, request: Request,
                             db: AsyncSession = Depends(get_db)):
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        rohwert = payload.get("value") if isinstance(payload, dict) else None
    else:
        form = await request.form()
        rohwert = form.get("value")
        # Ein nicht angehakter Schalter sendet gar nichts mit.
        feld = einstellungen.NACH_SCHLUESSEL.get(schluessel)
        if feld is not None and feld.art == "bool" and rohwert is None:
            rohwert = "0"
    try:
        wert = await einstellungen.setzen(db, schluessel, rohwert)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unbekannte Einstellung: {schluessel}")
    except ValueError as fehler:
        raise HTTPException(status_code=422, detail=str(fehler))
    geschoben = await _neu_laden_und_schieben(db)
    return {"schluessel": schluessel, "wert": wert, "audio_dienst": geschoben}


# ─── Gottesdienstarten ──────────────────────────────────────────────────────

class ArtEingabe(BaseModel):
    name: Optional[str] = None
    is_primary: Optional[bool] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None
    nas_vorlage: Optional[str] = None


@router.post("/gottesdienstarten")
async def art_anlegen(eingabe: ArtEingabe, db: AsyncSession = Depends(get_db)):
    name = (eingabe.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Die Gottesdienstart braucht einen Namen")
    vorhanden = (await db.execute(
        select(ServiceType).where(ServiceType.name == name)
    )).scalar_one_or_none()
    if vorhanden is not None:
        raise HTTPException(status_code=409, detail=f"„{name}“ gibt es schon")
    letzte = (await db.execute(select(func.max(ServiceType.sort_order)))).scalar() or 0
    art = ServiceType(
        name=name,
        sort_order=eingabe.sort_order if eingabe.sort_order is not None else letzte + 10,
        is_primary=bool(eingabe.is_primary),
        active=True if eingabe.active is None else bool(eingabe.active),
        nas_vorlage=(eingabe.nas_vorlage or None),
    )
    db.add(art)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"id": art.id, "name": art.name}


@router.patch("/gottesdienstarten/{art_id}")
async def art_aendern(art_id: int, eingabe: ArtEingabe, db: AsyncSession = Depends(get_db)):
    art = await db.get(ServiceType, art_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Gottesdienstart nicht gefunden")

    if eingabe.name is not None and eingabe.name.strip() != art.name:
        if art.geschuetzt:
            raise HTTPException(
                status_code=409,
                detail=f"„{art.name}“ lässt sich nicht umbenennen: daran hängt Logik "
                       f"(Brautpaar-Felder bzw. die NAS-Ablage). Ausblenden ist möglich.")
        neuer = eingabe.name.strip()
        if not neuer:
            raise HTTPException(status_code=422, detail="Der Name darf nicht leer sein")
        kollision = (await db.execute(
            select(ServiceType).where(ServiceType.name == neuer, ServiceType.id != art_id)
        )).scalar_one_or_none()
        if kollision is not None:
            raise HTTPException(status_code=409, detail=f"„{neuer}“ gibt es schon")
        # Bestehende Aufnahmen verweisen per Name — die wandern mit.
        sessions = (await db.execute(
            select(RecordingSession).where(RecordingSession.service_type == art.name)
        )).scalars().all()
        for s in sessions:
            s.service_type = neuer
        art.name = neuer

    for feld in ("is_primary", "active", "sort_order"):
        wert = getattr(eingabe, feld)
        if wert is not None:
            setattr(art, feld, wert)
    if eingabe.nas_vorlage is not None:
        art.nas_vorlage = eingabe.nas_vorlage.strip() or None

    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"id": art.id, "name": art.name}


@router.delete("/gottesdienstarten/{art_id}")
async def art_loeschen(art_id: int, db: AsyncSession = Depends(get_db)):
    art = await db.get(ServiceType, art_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Gottesdienstart nicht gefunden")
    if art.geschuetzt:
        raise HTTPException(
            status_code=409,
            detail=f"„{art.name}“ lässt sich nicht löschen: daran hängt Logik. Ausblenden "
                   f"nimmt sie aus der Auswahl, ohne alte Aufnahmen zu beschädigen.")
    benutzt = (await db.execute(
        select(func.count()).select_from(RecordingSession)
        .where(RecordingSession.service_type == art.name)
    )).scalar() or 0
    if benutzt:
        raise HTTPException(
            status_code=409,
            detail=f"„{art.name}“ wird von {benutzt} Aufnahme(n) benutzt. Ausblenden nimmt "
                   f"sie aus der Auswahl und lässt das Archiv unberührt.")
    await db.delete(art)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"geloescht": art_id}


# ─── Beitragsarten ──────────────────────────────────────────────────────────

class BeitragEingabe(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None
    exportieren: Optional[bool] = None
    klang_klasse: Optional[str] = None
    anker: Optional[bool] = None
    auto_marker: Optional[bool] = None
    dauer_min: Optional[int] = None
    dauer_max: Optional[int] = None
    sort_order: Optional[int] = None


def _klang_klasse_pruefen(wert: Optional[str]) -> Optional[str]:
    if wert is None:
        return None
    wert = wert.strip().lower()
    if wert in ("", "keine", "nur_auf_ansage"):
        return None
    if wert not in ("musik", "sprache"):
        raise HTTPException(
            status_code=422,
            detail="Klangklasse muss „musik“, „sprache“ oder leer sein. Leer heißt: wird "
                   "nie von sich aus vorgeschlagen, nur auf Ansage oder von Hand.")
    return wert


def _dauer_pruefen(von: Optional[int], bis: Optional[int]) -> None:
    if von is not None and bis is not None and von >= bis:
        raise HTTPException(status_code=422,
                            detail="Die Mindestdauer muss kleiner als die Höchstdauer sein")
    for wert, name in ((von, "Mindestdauer"), (bis, "Höchstdauer")):
        if wert is not None and (wert < 1 or wert > 36000):
            raise HTTPException(status_code=422,
                                detail=f"{name} muss zwischen 1 und 36000 Sekunden liegen")


@router.post("/beitragsarten")
async def beitrag_anlegen(eingabe: BeitragEingabe, db: AsyncSession = Depends(get_db)):
    name = (eingabe.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Die Beitragsart braucht einen Namen")
    vorhanden = (await db.execute(
        select(MarkerType).where(MarkerType.name == name)
    )).scalar_one_or_none()
    if vorhanden is not None:
        raise HTTPException(status_code=409, detail=f"„{name}“ gibt es schon")
    klasse = _klang_klasse_pruefen(eingabe.klang_klasse)
    _dauer_pruefen(eingabe.dauer_min, eingabe.dauer_max)
    letzte = (await db.execute(select(func.max(MarkerType.sort_order)))).scalar() or 0
    art = MarkerType(
        name=name,
        sort_order=eingabe.sort_order if eingabe.sort_order is not None else letzte + 10,
        active=True if eingabe.active is None else bool(eingabe.active),
        exportieren=True if eingabe.exportieren is None else bool(eingabe.exportieren),
        klang_klasse=klasse,
        anker=bool(eingabe.anker),
        auto_marker=True if eingabe.auto_marker is None else bool(eingabe.auto_marker),
        dauer_min=eingabe.dauer_min,
        dauer_max=eingabe.dauer_max,
    )
    db.add(art)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"id": art.id, "name": art.name}


@router.patch("/beitragsarten/{art_id}")
async def beitrag_aendern(art_id: int, eingabe: BeitragEingabe,
                          db: AsyncSession = Depends(get_db)):
    art = await db.get(MarkerType, art_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Beitragsart nicht gefunden")

    if eingabe.name is not None and eingabe.name.strip() != art.name:
        if art.geschuetzt:
            raise HTTPException(
                status_code=409,
                detail=f"„{art.name}“ lässt sich nicht umbenennen: der Name steht in den "
                       f"Ansage-Regeln oder dient als Anker der Programmzuordnung. Die "
                       f"Erkennung würde still aufhören zu funktionieren.")
        neuer = eingabe.name.strip()
        if not neuer:
            raise HTTPException(status_code=422, detail="Der Name darf nicht leer sein")
        kollision = (await db.execute(
            select(MarkerType).where(MarkerType.name == neuer, MarkerType.id != art_id)
        )).scalar_one_or_none()
        if kollision is not None:
            raise HTTPException(status_code=409, detail=f"„{neuer}“ gibt es schon")
        # Marker und Ansage-Regeln verweisen per Name — mitziehen.
        for m in (await db.execute(
            select(Marker).where(Marker.marker_type == art.name)
        )).scalars().all():
            m.marker_type = neuer
        for r in (await db.execute(
            select(AnsageRegel).where(AnsageRegel.marker_type == art.name)
        )).scalars().all():
            r.marker_type = neuer
        art.name = neuer

    if eingabe.klang_klasse is not None:
        art.klang_klasse = _klang_klasse_pruefen(eingabe.klang_klasse)
    neu_min = eingabe.dauer_min if eingabe.dauer_min is not None else art.dauer_min
    neu_max = eingabe.dauer_max if eingabe.dauer_max is not None else art.dauer_max
    _dauer_pruefen(neu_min, neu_max)
    art.dauer_min, art.dauer_max = neu_min, neu_max

    for feld in ("active", "exportieren", "anker", "auto_marker", "sort_order"):
        wert = getattr(eingabe, feld)
        if wert is not None:
            setattr(art, feld, wert)

    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"id": art.id, "name": art.name}


@router.delete("/beitragsarten/{art_id}")
async def beitrag_loeschen(art_id: int, db: AsyncSession = Depends(get_db)):
    art = await db.get(MarkerType, art_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Beitragsart nicht gefunden")
    if art.geschuetzt:
        raise HTTPException(
            status_code=409,
            detail=f"„{art.name}“ lässt sich nicht löschen: der Name steht in den "
                   f"Ansage-Regeln oder dient als Anker. Ausblenden nimmt den Knopf weg, "
                   f"ohne alte Marker zu beschädigen.")
    benutzt = (await db.execute(
        select(func.count()).select_from(Marker).where(Marker.marker_type == art.name)
    )).scalar() or 0
    if benutzt:
        raise HTTPException(
            status_code=409,
            detail=f"„{art.name}“ wird von {benutzt} Marker(n) benutzt. Ausblenden nimmt "
                   f"den Knopf weg und lässt das Archiv unberührt.")
    await db.delete(art)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"geloescht": art_id}


# ─── Ansage-Regeln ──────────────────────────────────────────────────────────

class RegelEingabe(BaseModel):
    muster: Optional[str] = None
    marker_type: Optional[str] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


async def _regel_pruefen(db: AsyncSession, muster: Optional[str],
                         marker_type: Optional[str]) -> None:
    if muster is not None:
        if not muster.strip():
            raise HTTPException(status_code=422, detail="Das Muster darf nicht leer sein")
        try:
            re.compile(muster)
        except re.error as fehler:
            raise HTTPException(
                status_code=422,
                detail=f"Das ist kein brauchbarer regulärer Ausdruck: {fehler}")
    if marker_type is not None:
        bekannt = (await db.execute(
            select(MarkerType).where(MarkerType.name == marker_type)
        )).scalar_one_or_none()
        if bekannt is None:
            raise HTTPException(
                status_code=422,
                detail=f"„{marker_type}“ ist keine bekannte Beitragsart")


@router.post("/ansage-regeln")
async def regel_anlegen(eingabe: RegelEingabe, db: AsyncSession = Depends(get_db)):
    if not eingabe.muster or not eingabe.marker_type:
        raise HTTPException(status_code=422,
                            detail="Eine Regel braucht ein Muster und eine Beitragsart")
    await _regel_pruefen(db, eingabe.muster, eingabe.marker_type)
    letzte = (await db.execute(select(func.max(AnsageRegel.sort_order)))).scalar() or 0
    regel = AnsageRegel(
        muster=eingabe.muster.strip(),
        marker_type=eingabe.marker_type,
        sort_order=eingabe.sort_order if eingabe.sort_order is not None else letzte + 10,
        active=True if eingabe.active is None else bool(eingabe.active),
    )
    db.add(regel)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"id": regel.id}


@router.patch("/ansage-regeln/{regel_id}")
async def regel_aendern(regel_id: int, eingabe: RegelEingabe,
                        db: AsyncSession = Depends(get_db)):
    regel = await db.get(AnsageRegel, regel_id)
    if regel is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    await _regel_pruefen(db, eingabe.muster, eingabe.marker_type)
    if eingabe.muster is not None:
        regel.muster = eingabe.muster.strip()
    for feld in ("marker_type", "sort_order", "active"):
        wert = getattr(eingabe, feld)
        if wert is not None:
            setattr(regel, feld, wert)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"id": regel.id}


@router.delete("/ansage-regeln/{regel_id}")
async def regel_loeschen(regel_id: int, db: AsyncSession = Depends(get_db)):
    regel = await db.get(AnsageRegel, regel_id)
    if regel is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    await db.delete(regel)
    await db.commit()
    await _neu_laden_und_schieben(db)
    return {"geloescht": regel_id}


@router.post("/ansage-regeln/probe")
async def regel_proben(request: Request, db: AsyncSession = Depends(get_db)):
    """Ein Muster gegen einen Beispielsatz prüfen, bevor es gespeichert wird.

    Regex-Felder in einer Oberfläche sind ein scharfes Werkzeug; wer sein Muster
    vorher ausprobieren kann, speichert weniger Unsinn.
    """
    payload = await request.json()
    muster = (payload.get("muster") or "").strip()
    text = payload.get("text") or ""
    if not muster:
        raise HTTPException(status_code=422, detail="Kein Muster angegeben")
    try:
        rx = re.compile(muster, re.I)
    except re.error as fehler:
        return {"gueltig": False, "fehler": str(fehler), "treffer": None}
    fund = rx.search(text)
    return {
        "gueltig": True,
        "fehler": None,
        "treffer": fund.group(0) if fund else None,
    }


# ─── NAS-Ordnervorlagen ─────────────────────────────────────────────────────

@router.get("/nas-platzhalter")
async def nas_platzhalter():
    """Welche Platzhalter eine Ordnervorlage kennt — treibt die Hilfe in der Oberfläche."""
    return {
        "platzhalter": [{"name": n, "bedeutung": b} for n, b in nas_pfad.PLATZHALTER.items()],
        "standard": nas_pfad.STANDARD_VORLAGEN,
        "hinweis": "Eckige Klammern machen einen Teil optional: fehlt ein Wert darin, "
                   "verschwindet die ganze Gruppe.",
    }


@router.post("/nas-vorschau")
async def nas_vorschau(request: Request):
    """Zeigen, welcher Ordner aus einer Vorlage entsteht — vor dem Speichern.

    Gibt zwei Pfade zurück: einmal mit Beispielwerten, einmal ohne die optionalen
    (Brautpaar, Teil, Thema). Der zweite Fall ist der, der in einer gewachsenen Ablage
    am leichtesten übersehen wird.
    """
    payload = await request.json()
    vorlage = (payload.get("vorlage") or "").strip()
    if not vorlage:
        raise HTTPException(status_code=422, detail="Keine Vorlage angegeben")
    return nas_pfad.vorschau(vorlage)
