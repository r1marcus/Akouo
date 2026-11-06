"""Welcher Gottesdienst ist jetzt dran? Vorschlag aus dem Kalender der Gemeinde-App.

Beim Öffnen der Gottesdienst-Auswahl fragt Akouo den Kalender des Tages ab (über den
Audio-Dienst, der die Zugangsdaten hat) und schlägt Art und Thema vor — „Laut Kalender:
Gottesdienst morgens · 10:00 · Thema Psalm 22“. Übernommen wird nur auf Tipp.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

VORLAUF = timedelta(minutes=45)       # so früh gilt ein Termin schon als "jetzt"
DAUER = timedelta(hours=2)            # ohne Endzeit
STANDARD_TITEL = {"gottesdienst", "gottesdienst morgens", "gottesdienst abends", "bibelstunde",
                  "gebetsstunde", "jugendstunde"}


def thema_aus(termin: dict) -> str:
    """Die Zeile „Thema: …“ aus der Beschreibung, sonst ein besonderer Titel."""
    for zeile in (termin.get("description") or "").splitlines():
        if zeile.strip().lower().startswith("thema:"):
            return zeile.split(":", 1)[1].strip()
    titel = (termin.get("title") or "").strip()
    if titel.lower() in STANDARD_TITEL:
        return ""
    titel = re.split(r"\s+[–-]\s+gottesdienst\b", titel, flags=re.I)[0]   # "Thema Psalm 22 – Gottesdienst"
    return re.sub(r"^thema[:\s]+", "", titel, flags=re.I).strip()


def art_aus(termin: dict, arten: list[str]) -> Optional[str]:
    """Die passende Akouo-Gottesdienstart — oder None, wenn es keine gibt."""
    nach_name = {a.lower(): a for a in arten}
    typ, zeit = termin.get("event_type"), termin.get("tageszeit")
    if typ == "andere" and termin.get("type_label", "").lower() in nach_name:
        return nach_name[termin["type_label"].lower()]
    kandidat = {"gottesdienst": "Sonntagabend" if zeit == "abends" else "Sonntagmorgen",
                "bibelstunde": "Bibelstunde", "gebetsstunde": "Gebetsstunde",
                "jugendstunde": "Jugendstunde"}.get(typ)
    return kandidat if kandidat and kandidat.lower() in nach_name else None


def _beginn(termin: dict, tag: datetime) -> Optional[datetime]:
    m = re.fullmatch(r"(\d{2}):(\d{2})", termin.get("start_time") or "")
    return tag.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0) if m else None


def vorschlag(termine: list[dict], jetzt: datetime, arten: list[str]) -> Optional[dict]:
    """Der Termin, der gerade läuft oder als Nächstes kommt — mit Art und Thema für Akouo."""
    mit_zeit = [(b, t) for t in termine if (b := _beginn(t, jetzt))]
    laufend = [(b, t) for b, t in mit_zeit if b - VORLAUF <= jetzt <= b + DAUER]
    kommend = sorted([(b, t) for b, t in mit_zeit if b > jetzt], key=lambda x: x[0])
    if not termine:
        return None
    # Läuft gerade etwas? Sonst das Nächste heute; sonst (keine Uhrzeiten) der erste Termin.
    _, termin = (laufend or kommend or [(None, termine[0])])[0]
    thema = thema_aus(termin)
    art = art_aus(termin, arten)
    if art is None and termin.get("type_label"):
        thema = thema or termin["type_label"]
    return {"service_type": art, "thema": thema, "titel": termin.get("title", ""),
            "uhrzeit": termin.get("start_time", ""), "event_id": termin.get("id")}
