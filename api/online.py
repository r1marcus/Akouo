"""Online stellen: aus einem exportierten Beitrag einen Eintrag für die Gemeinde-App machen.

Die Gemeinde-App (Church_Tool) führt das Archiv aller Gottesdienste seit 2016. Sie nimmt
Aufnahmen über `/api/archive/recordings/bulk-import/` entgegen; jede Aufnahme hat einen
`file_key`, der zugleich der Schlüssel im Speicher ist:

    archive/2026/10/18/sonntag-abends/Track-03_-_Predigt.mp3

Dieser Schlüssel muss **genau** so gebildet werden wie im Importskript der Gemeinde-App
(`Church_Tool/scripts/enrichment/build_meta.py`), sonst stünde derselbe Gottesdienst
doppelt im Archiv — einmal von hier, einmal aus einem Nachtrag von Hand. Deshalb sind
Ordner- und Dateiregel unten aus build_meta.py übernommen, nicht neu erfunden.

Hier wird nur der Eintrag gebaut. Hochladen und Eintragen macht der Audio-Dienst, der die
Datei hat (audio-service/online.py).
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

WOCHENTAGE = {"Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"}
_ORDNER = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<weekday>[A-ZÄÖÜ][a-zäöüß]+)\s+"
                     r"(?P<seq>\d+)\.\s+(?P<rest>.+?)\s*$")
_ORDNER_SONDER = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<rest>.+?)\s*$")
_STANDARD = {"morgens", "abends", "morgens 2. Teil", "abends 2. Teil"}

# Akouos Beitragsarten → Arten der Gemeinde-App, so wie sie LIVE vorkommen (gezählt am
# 6. November 2026 über 1.178 Aufnahmen aus 2026): predigt, lied, chor, orchester,
# instrumental, gedicht, prosa, gebet, ansage, zeugnis, sonstiges. Der Code im Repository
# von Church_Tool kennt nur sechs davon — maßgeblich ist, was die App tatsächlich führt.
# Was hier fehlt, wird "sonstiges" und behält seinen Namen im Titel.
APP_ART = {
    "Predigt": "predigt", "Kindergeschichte": "predigt",
    "Chorlied": "chor", "Jugendchor": "chor", "Kinderchor": "chor", "Gebetslied": "chor",
    "Gruppenlied": "lied", "Gemeindegesang": "lied", "Kinderlied": "lied",
    "Orchester": "orchester", "Musikstück": "instrumental",
    "Gedicht": "gedicht", "Prosa": "prosa", "Gebet": "gebet",
    "Ansage": "ansage", "Vermeldungen": "ansage",
    "Zeugnis": "zeugnis",
    # Die App führt live keine eigene Art für Männer- und Frauenchor; bis sie eine hat,
    # sind beide Chor, und der Name steht im Titel ("Männerchor: …").
    "Männerchor": "chor", "Frauenchor": "chor",
}

# Akouos Gottesdienstart weiß mehr als der Wochentag: eine Gebetsstunde am Samstag ist
# eine Gebetsstunde, ein Erntedankfest kein gewöhnlicher Sonntag. Der Slot bleibt trotzdem
# der aus dem Ordner — an ihm hängt der file_key, und der darf sich nie ändern.
APP_EREIGNIS = {"Gebetsstunde": "gebetsstunde", "Bibelstunde": "bibelstunde",
                "Jugendstunde": "jugendstunde"}
REGELMAESSIG = {"Sonntagmorgen", "Sonntagabend", "Gebetsstunde", "Bibelstunde", "Gottesdienst"}
_NAME = {"gottesdienst": "Gottesdienst", "gebetsstunde": "Gebetsstunde",
         "bibelstunde": "Bibelstunde", "jugendstunde": "Jugendstunde"}


def sauber(name: str) -> str:
    """Wie build_meta.sanitize: alles außer Buchstaben, Ziffern, . _ - wird zu _."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def ereignis_aus_ordner(ordner: str) -> Optional[dict]:
    """'2026-10-18 Sonntag 2. abends' → Datum, Titel, Art und Slot wie in der Gemeinde-App.

    Übernommen aus build_meta.parse_aufnahme_folder — Änderungen dort gehören auch hierher.
    """
    m = _ORDNER.match(ordner)
    if not m or m.group("weekday") not in WOCHENTAGE:
        m2 = _ORDNER_SONDER.match(ordner)
        if not m2:
            return None
        iso, rest = m2.group("date"), m2.group("rest").strip()
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            return None
        slot = re.sub(r"[^a-z0-9]+", "-", rest.lower()).strip("-") or "event"
        return {"date": iso, "title": f"{rest}, {d.strftime('%d.%m.%Y')}",
                "event_type": "gottesdienst", "slot": slot}
    iso, wochentag, rest = m.group("date"), m.group("weekday"), m.group("rest").strip()
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return None
    rn = rest.lower()
    morgens = rn.startswith("morgens")
    abends = rn.startswith("abends")
    teil2 = rest.endswith("2. Teil")
    if rest in _STANDARD:
        if d.month == 1 and d.day <= 7 and abends:
            art, name = "gebetsstunde", "Gebetsstunde"
        elif wochentag == "Sonntag":
            art, name = "gottesdienst", "Gottesdienst"
        elif wochentag == "Dienstag" and abends:
            art, name = "bibelstunde", "Bibelstunde"
        elif wochentag == "Freitag" and abends:
            art, name = "gebetsstunde", "Gebetsstunde"
        else:
            art, name = "gottesdienst", "Gottesdienst"
        zeit = "morgens" if morgens else "abends"
        titel = f"{name} — {wochentag} {zeit}, {d.strftime('%d.%m.%Y')}"
        slot = f"{wochentag.lower()}-{zeit}" + ("-teil2" if teil2 else "")
        if teil2:
            titel += " (2. Teil)"
    else:
        art, titel = "gottesdienst", f"{rest}, {d.strftime('%d.%m.%Y')}"
        slot = re.sub(r"[^a-z0-9]+", "-", rest.lower()).strip("-")
    return {"date": iso, "title": titel, "event_type": art, "slot": slot}


def file_key(ereignis: dict, dateiname: str) -> str:
    d = date.fromisoformat(ereignis["date"])
    return f"archive/{d.year:04d}/{d.month:02d}/{d.day:02d}/{ereignis['slot']}/{sauber(dateiname)}"


def _mit_art(ereignis: dict, gottesdienstart: Optional[str], thema: Optional[str]) -> dict:
    """Art und Titel aus Akouo, wo der Ordner weniger weiß. Slot und Datum bleiben."""
    ereignis = dict(ereignis)
    if gottesdienstart in APP_EREIGNIS:
        ereignis["event_type"] = APP_EREIGNIS[gottesdienstart]
    # "Gottesdienst — Sonntag abends, 18.10.2026" → Name vorn austauschen, Rest bleibt
    _, _, rest = ereignis["title"].partition(" — ")
    if rest:
        if thema and thema.strip():
            name = thema.strip()
        elif gottesdienstart and gottesdienstart not in REGELMAESSIG:
            name = gottesdienstart                 # Erntedankfest, Taufe, Karfreitag …
        else:
            name = _NAME.get(ereignis["event_type"], "Gottesdienst")
        ereignis["title"] = f"{name} — {rest}"
    return ereignis


def eintrag(nas_ordner: str, dateiname: str, beitragsart: str, nr: int,
            auswertung=None, gottesdienstart: Optional[str] = None,
            thema: Optional[str] = None) -> Optional[dict]:
    """Der Eintrag für den Sammelimport der Gemeinde-App — oder None, wenn der Ordner
    keinem Gottesdienst zuzuordnen ist (Hochzeiten, Sonstiges)."""
    if not nas_ordner.startswith("Gottesdienste/"):
        return None
    ereignis = ereignis_aus_ordner(nas_ordner.split("/", 1)[1])
    if ereignis is None:
        return None
    ereignis = _mit_art(ereignis, gottesdienstart, thema)
    thema = (getattr(auswertung, "title", None) or "").strip()
    try:
        stellen = json.loads(getattr(auswertung, "bible_refs", None) or "[]")
    except ValueError:
        stellen = []
    titel = f"{beitragsart}: {thema}" if thema else f"{beitragsart} · Track {nr:02d}"
    return {
        "event": ereignis,
        "recording": {
            "file_key": file_key(ereignis, dateiname),
            "title": titel[:255],
            "recording_type": APP_ART.get(beitragsart, "sonstiges"),
            "topic": thema,
            "summary": (getattr(auswertung, "summary", None) or "").strip(),
            "bible_reference": ", ".join(str(s) for s in stellen),
            "speaker": (getattr(auswertung, "speaker", None) or "").strip(),
            "transcript": (getattr(auswertung, "transcript", None) or "").strip(),
        },
    }
