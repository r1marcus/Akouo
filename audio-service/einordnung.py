"""Stufe 3: Was war es? Das Sprachmodell liest den Text eines gesprochenen Beitrags.

Der Klang trennt Sprache von Musik sicher, aber nicht Prosa von Predigt — beides ist
frei gehaltene Rede, gleich lang, gleich schnell (gemessen: Predigt 98–155, Prosa
104–171 Wörter je Minute). Der Text verrät es: eine Predigt legt einen Bibeltext aus
und spricht die Gemeinde an, eine Prosa erzählt eine Geschichte oder liest ein Zeugnis
vor, ein Gedicht hat Verse, ein Anspiel mehrere Stimmen.

Läuft nur beim Neu-Erkennen nach dem Gottesdienst, wenn das ganze Transkript da ist —
nicht live, wo Whisper und das Modell sich die Grafikeinheit teilen würden.

Rangfolge (roadmap.md, Punkt 3): Hand schlägt alles, Ansage schlägt Modell, Modell
schlägt Klang. Das Modell darf nur aus der festen Liste antworten; fällt Ollama aus,
bleibt der Typ aus dem Klang stehen.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELL = os.environ.get("EINORDNUNG_MODELL", os.environ.get("PROGRAMM_MODELL", "qwen2.5:14b-instruct"))
ZEITLIMIT_S = 60.0
MAX_ZEICHEN = 6000      # Anfang und Ende reichen; eine Predigt hat sonst 20 000 Zeichen

SPRACH_TYPEN = ["Predigt", "Prosa", "Gedicht", "Kindergeschichte", "Anspiel", "Gebet",
                "Ansage", "Vermeldungen"]

_PROMPT = """Du hörst einen Gottesdienst einer freikirchlichen Gemeinde nach. Unten steht das
automatische Transkript EINES gesprochenen Beitrags (mit Hörfehlern). Ordne ihn einem Typ zu.

Erlaubte Typen und woran man sie erkennt:
- Predigt: ein Prediger legt einen Bibeltext aus, nennt Bibelstellen, spricht die Gemeinde
  an ("liebe Geschwister", "wir"), ermahnt und ermutigt. Meist länger als 5 Minuten.
- Prosa: ein vorgetragener Text — eine Geschichte, Erzählung, Begebenheit oder ein Zeugnis
  mit Handlung und Personen, oft mit Titel angekündigt; erzählt statt auszulegen.
- Gedicht: Verse, oft gereimt und rhythmisch; kurz, meist unter 3 Minuten.
- Kindergeschichte: eine Geschichte, die sich erkennbar an Kinder richtet ("liebe Kinder").
- Anspiel: mehrere Sprecher in Rollen, Dialog, szenisches Spiel.
- Gebet: spricht Gott direkt an ("Herr", "Vater", "du"), bittet und dankt, endet mit Amen.
- Ansage: kündigt das Programm oder den nächsten Beitrag an, kurz.
- Vermeldungen: Termine, Organisatorisches, Hinweise für die Gemeinde.

Dauer: {dauer} Minuten.
Transkript:
\"\"\"{text}\"\"\"

Antworte nur mit JSON: {{"typ": "<einer der erlaubten Typen>", "grund": "<höchstens 12 Wörter>"}}"""


def _kuerzen(text: str) -> str:
    if len(text) <= MAX_ZEICHEN:
        return text
    halb = MAX_ZEICHEN // 2
    return text[:halb] + " […] " + text[-halb:]


def ordne_ein(text: str, dauer_s: float) -> Optional[tuple[str, str]]:
    """(Typ, Grund) für einen gesprochenen Beitrag — oder None, wenn das Modell
    nicht erreichbar ist oder etwas außerhalb der Liste antwortet."""
    text = (text or "").strip()
    if len(text.split()) < 15:      # zu wenig, um etwas zu sagen
        return None
    prompt = _PROMPT.format(dauer=round(dauer_s / 60, 1), text=_kuerzen(text))
    try:
        antwort = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": MODELL, "prompt": prompt, "stream": False, "format": "json",
                  "options": {"temperature": 0}},
            timeout=ZEITLIMIT_S,
        )
        antwort.raise_for_status()
        daten = json.loads(antwort.json().get("response", "{}"))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Einordnung: Modell nicht erreichbar oder Antwort unlesbar (%s)", e)
        return None
    typ = str(daten.get("typ", "")).strip()
    if typ not in SPRACH_TYPEN:
        log.info("Einordnung: Antwort außerhalb der Liste verworfen: %r", typ)
        return None
    return typ, str(daten.get("grund", ""))[:120]
