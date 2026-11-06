"""Vermeldungen: Termine und Hinweise aus dem Gottesdienst in die Gemeinde-App.

In den Vermeldungen steht, was die Gemeinde vorhat — „nächsten Samstag um 17 Uhr
Straßenevangelisation in Freiburg“ —, dazu Hinweise und Gebetsanliegen. Nach dem
Gottesdienst liest Akouo sie aus dem Transkript, und der Tontechniker wird gefragt:
„Stimmen die Vermeldungen so zum Hochladen?“ Nichts geht ungefragt hinaus — ein
verhörtes Datum im Kalender ist schlimmer als ein fehlender Termin.

Das Sprachmodell zieht die Einträge heraus; drei Dinge prüft Akouo selbst:

- **Beleg:** Zu jedem Eintrag muss das Modell den Satz wörtlich zitieren. Steht der nicht
  im Transkript, war der Eintrag erfunden und fällt weg.
- **Datum:** „nächsten Samstag“, „am 23.“, „morgen“ rechnet Akouo gegen den Tag des
  Gottesdienstes aus, nicht das Modell. Was sich nicht auflösen lässt, bleibt ohne Datum
  und muss von Hand ergänzt werden.
- **Gebetsanliegen** nennen Kranke beim Namen. Sie sind vorab abgewählt.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from typing import Optional

import httpx

log = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELL = os.environ.get("PROGRAMM_MODELL", "qwen2.5:14b-instruct")
ZEITLIMIT_S = 90.0
ARTEN = ("termin", "hinweis", "gebetsanliegen")

WOCHENTAGE = ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]
MONATE = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
          "september", "oktober", "november", "dezember"]

_PROMPT = """Unten steht das automatische Transkript der Vermeldungen eines Gottesdienstes
(mit Hörfehlern). Ziehe heraus, was die Gemeinde wissen soll.

Arten:
- termin: eine Veranstaltung mit Zeitpunkt (Datum oder Wochentag), z. B. Jugendstunde,
  Evangelisation, Gemeindeversammlung, Hochzeit, Beerdigung.
- hinweis: Organisatorisches ohne eigenen Termin (Spenden, Aufräumen, Dank, Hinweise).
- gebetsanliegen: Bitte um Gebet für Personen oder Anliegen.

Gib JSON zurück: {{"eintraege": [{{
  "art": "termin|hinweis|gebetsanliegen",
  "titel": "kurz, z. B. Straßenevangelisation",
  "wann": "Zeitangabe GENAU wie gesagt, z. B. 'nächsten Samstag um 17 Uhr', sonst ''",
  "uhrzeit": "HH:MM oder ''", "ende": "HH:MM oder ''",
  "ort": "oder ''",
  "text": "ein bis zwei Sätze, worum es geht",
  "beleg": "der Satz aus dem Transkript, WÖRTLICH kopiert, höchstens 25 Wörter"
}}]}}
Erfinde nichts. Gibt es nichts, gib {{"eintraege": []}} zurück.

Transkript:
\"\"\"{text}\"\"\""""


# ─── Belege ────────────────────────────────────────────────────────────────

def _woerter(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß0-9]+", (text or "").lower())


def belegt(beleg: str, transkript: str) -> bool:
    """Steht der zitierte Satz wirklich im Transkript? Toleranz für Satzzeichen und
    einzelne Abweichungen: 80 Prozent seiner Wörter in derselben Reihenfolge."""
    zitat, quelle = _woerter(beleg), _woerter(transkript)
    if len(zitat) < 3:
        return False
    treffer, pos = 0, 0
    for w in zitat:
        try:
            pos = quelle.index(w, pos) + 1
            treffer += 1
        except ValueError:
            continue
    return treffer / len(zitat) >= 0.8


# ─── Datum ─────────────────────────────────────────────────────────────────

def datum_aus(wann: str, bezug: date) -> Optional[date]:
    """Löst die Zeitangabe gegen den Tag des Gottesdienstes auf — oder None."""
    t = (wann or "").lower()
    if not t:
        return None
    if "übermorgen" in t:
        return bezug + timedelta(days=2)
    if re.search(r"\bmorgen\b", t):          # nicht "Sonntag morgens"
        return bezug + timedelta(days=1)
    if re.search(r"\bheute\b", t):
        return bezug

    # 23.11. / 23.11.2026 / 23. November / am 23.
    m = re.search(r"\b(\d{1,2})\.\s*(\d{1,2})\.(\d{2,4})?", t)
    if m:
        tag, monat = int(m.group(1)), int(m.group(2))
        jahr = int(m.group(3)) if m.group(3) else None
        return _naechstes(tag, monat, jahr, bezug)
    m = re.search(r"\b(\d{1,2})\.?\s+(" + "|".join(MONATE) + r")", t)
    if m:
        return _naechstes(int(m.group(1)), MONATE.index(m.group(2)) + 1, None, bezug)

    for i, name in enumerate(WOCHENTAGE):
        if re.search(rf"\b{name}", t):
            if re.search(r"nächste[nr]?\s+woche", t):   # Wochentag der Folgewoche
                return bezug - timedelta(days=bezug.weekday()) + timedelta(days=7 + i)
            tage = (i - bezug.weekday()) % 7 or 7     # "am Sonntag", am Sonntag gesagt
            ziel = bezug + timedelta(days=tage)
            if "übernächst" in t:
                ziel += timedelta(days=7)
            return ziel

    m = re.search(r"\bam\s+(\d{1,2})\.(?!\d)", t)
    if m:
        tag = int(m.group(1))
        monat = bezug.month if tag >= bezug.day else bezug.month % 12 + 1
        return _naechstes(tag, monat, None, bezug)
    return None


def _naechstes(tag: int, monat: int, jahr: Optional[int], bezug: date) -> Optional[date]:
    if jahr is not None and jahr < 100:
        jahr += 2000
    for j in ([jahr] if jahr else [bezug.year, bezug.year + 1]):
        try:
            d = date(j, monat, tag)
        except ValueError:
            return None
        if jahr or d >= bezug:
            return d
    return None


def _uhrzeit(wert: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", wert or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


# ─── Lesen ─────────────────────────────────────────────────────────────────

def aufbereiten(roh: list, transkript: str, bezug: date) -> list[dict]:
    """Modellantwort → Einträge für die Rückfrage. Unbelegtes fällt weg."""
    eintraege = []
    for e in roh if isinstance(roh, list) else []:
        if not isinstance(e, dict):
            continue
        art = str(e.get("art", "")).strip().lower()
        titel = str(e.get("titel", "")).strip()
        if art not in ARTEN or not titel:
            continue
        if not belegt(str(e.get("beleg", "")), transkript):
            log.info("Vermeldungen: unbelegter Eintrag verworfen: %r", titel)
            continue
        d = datum_aus(str(e.get("wann", "")), bezug) if art == "termin" else None
        uhr = _uhrzeit(str(e.get("uhrzeit", "")))
        eintraege.append({
            "art": art, "titel": titel[:120], "wann": str(e.get("wann", ""))[:80],
            "datum": d.isoformat() if d else "", "uhrzeit": uhr,
            "ende": _uhrzeit(str(e.get("ende", ""))),
            "ort": str(e.get("ort", "")).strip()[:120], "text": str(e.get("text", "")).strip()[:600],
            "beleg": str(e.get("beleg", "")).strip()[:300],
            # Gebetsanliegen nennen Kranke beim Namen — nur auf ausdrücklichen Wunsch
            "ausgewaehlt": art != "gebetsanliegen" and (art != "termin" or bool(d)),
            "pruefen": art == "termin" and not (d and uhr),
        })
    return eintraege


def lies(transkript: str, bezug: date) -> Optional[list[dict]]:
    """None, wenn das Modell nicht antwortet; [] wenn es nichts zu vermelden gab."""
    if len(_woerter(transkript)) < 10:
        return []
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/generate", timeout=ZEITLIMIT_S, json={
            "model": MODELL, "prompt": _PROMPT.format(text=transkript[:8000]),
            "stream": False, "format": "json", "options": {"temperature": 0}})
        r.raise_for_status()
        roh = json.loads(r.json().get("response", "{}")).get("eintraege", [])
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        log.warning("Vermeldungen: Modell nicht erreichbar oder Antwort unlesbar (%s)", e)
        return None
    return aufbereiten(roh, transkript, bezug)


# ─── Hochladen ─────────────────────────────────────────────────────────────

def _ereignisart(titel: str) -> str:
    t = titel.lower()
    if "gebet" in t:
        return "gebetsstunde"
    if "bibel" in t:
        return "bibelstunde"
    if "jugend" in t:
        return "jugendstunde"
    return "gottesdienst"


def _aehnlich(a: str, b: str) -> bool:
    wa, wb = set(_woerter(a)), set(_woerter(b))
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def hochladen(eintraege: list[dict], bezug: date, client: httpx.Client) -> list[dict]:
    """Ausgewählte Einträge in Kalender bzw. Schwarzes Brett. Gibt je Eintrag zurück, was
    geschah — samt der ID in der App, damit es sich zurücknehmen lässt."""
    herkunft = f"Aus den Vermeldungen vom {bezug.strftime('%d.%m.%Y')}."
    ergebnis = []
    for e in eintraege:
        if not e.get("ausgewaehlt"):
            continue
        try:
            if e["art"] == "termin":
                if not e.get("datum"):
                    ergebnis.append({**e, "status": "fehler", "fehler": "ohne Datum"})
                    continue
                vorhanden = client.get("/api/calendar/", params={
                    "date_from": e["datum"], "date_to": e["datum"]}).json()
                liste = vorhanden.get("results", vorhanden) if isinstance(vorhanden, dict) else vorhanden
                if any(_aehnlich(v.get("title", ""), e["titel"]) for v in liste or []):
                    ergebnis.append({**e, "status": "schon_da"})
                    continue
                r = client.post("/api/calendar/", json={
                    "date": e["datum"], "title": e["titel"],
                    "start_time": e.get("uhrzeit") or None, "end_time": e.get("ende") or None,
                    "event_type": _ereignisart(e["titel"]), "visibility": "closed",
                    "location": e.get("ort", ""),
                    "description": f"{e.get('text', '')}\n\n{herkunft}".strip()})
                r.raise_for_status()
                ergebnis.append({**e, "status": "eingetragen", "ziel": "kalender", "id": r.json().get("id")})
            else:
                ablauf = (date.fromisoformat(e["datum"]) + timedelta(days=1)) if e.get("datum") \
                    else bezug + timedelta(days=14)
                r = client.post("/api/bulletin/", json={
                    "title": e["titel"], "body": f"{e.get('text', '')}\n\n{herkunft}".strip(),
                    "category": "gebetsanliegen" if e["art"] == "gebetsanliegen" else "ankuendigung",
                    "expires_at": f"{ablauf.isoformat()}T00:00:00"})
                r.raise_for_status()
                ergebnis.append({**e, "status": "eingetragen", "ziel": "brett", "id": r.json().get("id")})
        except (httpx.HTTPError, ValueError) as fehler:
            ergebnis.append({**e, "status": "fehler", "fehler": str(fehler)[:160]})
    return ergebnis


def zuruecknehmen(ergebnis: list[dict], client: httpx.Client) -> list[dict]:
    """Was eingetragen wurde, wieder löschen."""
    for e in ergebnis:
        if e.get("status") != "eingetragen" or not e.get("id"):
            continue
        pfad = "/api/calendar/" if e.get("ziel") == "kalender" else "/api/bulletin/"
        try:
            r = client.delete(f"{pfad}{e['id']}/")
            e["status"] = "zurueckgenommen" if r.status_code in (200, 204, 404) else "fehler"
        except httpx.HTTPError as fehler:
            e["status"], e["fehler"] = "fehler", str(fehler)[:160]
    return ergebnis
