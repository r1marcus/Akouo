"""Metadata for a finished segment: title, summary, Bible references, keywords.

Takes the transcript lines that fall inside the segment and asks the local Ollama model.
Bible references are checked against the real book names afterwards — language models are
happy to invent a plausible-looking verse.
"""
import json
import pathlib
import re
from typing import Optional

import httpx

from config import config

BOOKS = [
    "1. Mose", "2. Mose", "3. Mose", "4. Mose", "5. Mose", "Genesis", "Exodus", "Levitikus",
    "Numeri", "Deuteronomium", "Josua", "Richter", "Rut", "Ruth", "1. Samuel", "2. Samuel",
    "1. Könige", "2. Könige", "1. Chronik", "2. Chronik", "Esra", "Nehemia", "Ester", "Esther",
    "Hiob", "Ijob", "Psalm", "Psalmen", "Sprüche", "Prediger", "Hoheslied", "Jesaja", "Jeremia",
    "Klagelieder", "Hesekiel", "Ezechiel", "Daniel", "Hosea", "Joel", "Amos", "Obadja", "Jona",
    "Micha", "Nahum", "Habakuk", "Zefanja", "Haggai", "Sacharja", "Maleachi",
    "Matthäus", "Markus", "Lukas", "Johannes", "Apostelgeschichte", "Römer", "1. Korinther",
    "2. Korinther", "Galater", "Epheser", "Philipper", "Kolosser", "1. Thessalonicher",
    "2. Thessalonicher", "1. Timotheus", "2. Timotheus", "Titus", "Philemon", "Hebräer",
    "Jakobus", "1. Petrus", "2. Petrus", "1. Johannes", "2. Johannes", "3. Johannes", "Judas",
    "Offenbarung",
]
_BOOK_PATTERN = "|".join(sorted((re.escape(b) for b in BOOKS), key=len, reverse=True))
REFERENCE_RE = re.compile(rf"\b({_BOOK_PATTERN})\s+\d+[,:.]\s?\d+(\s?[-–]\s?\d+)?", re.IGNORECASE)
_BUCH = {b.lower(): b for b in BOOKS}
_BUCH.update({"psalmen": "Psalm", "ruth": "Rut", "esther": "Ester"})

# So wird eine Stelle gesprochen, nicht geschrieben. Am 08.11. lauteten die drei Predigttexte
# "Matthäus-Evangelium, Kapitel 7", "Lukas Kapitel 16 Ab Verse 19 bis 31" und "Psalm 51, die
# Verse 3 bis 5" — REFERENCE_RE kennt nur "Buch 3,16" und ließ alle drei fallen. Übrig blieb ein
# Nebenzitat ("Epheser 2, 8 und 9"), das dann als einzige Bibelstelle in der Gemeinde-App stand.
_GESPROCHEN = re.compile(
    rf"\b(?P<buch>{_BOOK_PATTERN})(?:-?evangelium)?[\s,.:]*(?:kapitel\s*)?(?P<kap>\d{{1,3}})\b"
    r"(?:\s*[,:.]?\s*(?:(?:die|den|im|in)\s+)?(?:(?:ab\s+)?vers(?:e|en)?\s*)?(?:ab\s+)?"
    r"(?P<v1>\d{1,3})\b"
    r"(?:\s*(?:[-–]|bis|und)\s*(?:vers(?:e|en)?\s*)?(?P<v2>\d{1,3})\b)?)?",
    re.IGNORECASE,
)


def stellen_im_text(text: str) -> list:
    """Bibelstellen, wie sie gesprochen wurden — je Buch und Kapitel eine, in der Reihenfolge
    ihrer ersten Nennung. Der Predigttext wird am Anfang gelesen und steht deshalb vorn.

    Wird ein Kapitel mehrfach genannt, gewinnt die genaueste Angabe: ein Versbereich vor einem
    einzelnen Vers vor dem bloßen Kapitel. Whisper verhört sich beim ersten Ansatz gern
    ("Psalm 51, die Verse 9. Psalm 51, die Verse 3 bis 5")."""
    beste: dict = {}
    for m in _GESPROCHEN.finditer(text):
        buch = _BUCH.get(m.group("buch").lower(), m.group("buch"))
        kap, v1, v2 = m.group("kap"), m.group("v1"), m.group("v2")
        if v1 and v2 and int(v2) > int(v1):
            stelle, guete = f"{buch} {kap},{v1}-{v2}", 2
        elif v1:
            stelle, guete = f"{buch} {kap},{v1}", 1
        else:
            stelle, guete = f"{buch} {kap}", 0
        schluessel = (buch, kap)
        if schluessel not in beste or guete > beste[schluessel][1]:
            beste[schluessel] = (stelle, guete, beste.get(schluessel, (None, None, m.start()))[2])
    return [s for s, _, _ in sorted(beste.values(), key=lambda e: e[2])]


# Bei diesen Beiträgen wird gesungen oder gespielt. Whisper versteht Gesang schlecht und
# schreibt über Instrumenten Füllsätze — aus "Vielen Dank. Vielen Dank." und "Amen. Beeship"
# machte die KI am 08.11. Titel wie "Amen und Beeship", die so in der Gemeinde-App landeten.
# Für Lieder fragen wir das Modell deshalb gar nicht; den Titel trägt der Tontechniker im
# Export-Check ein.
LIED_TYPEN = {
    "Chorlied", "Orchester", "Jugendchor", "Kinderchor", "Gruppenlied", "Gebetslied",
    "Musikstück", "Gemeindegesang", "Kinderlied", "Männerchor", "Frauenchor",
}

# Whispers Füllsätze bei Musik und Stille — siehe classifier._HALLUZINATION.
_FUELLSATZ = ("muziek", "music", "untertitelung", "untertitel", "vielen dank",
              "das war's", "bis zum nächsten mal", "copyright", "swr", "zdf")
_NUR_AMEN = re.compile(r"^(?:amen[.!,\s]*)+$", re.IGNORECASE)


def _echte_zeile(text: str) -> bool:
    klein = text.lower()
    return not _NUR_AMEN.match(text) and not any(f in klein for f in _FUELLSATZ)

SYSTEM_PROMPT = (
    "Du hilfst einem Gemeindearchiv. Du bekommst das Transkript eines Gottesdienst-Abschnitts. "
    "Antworte ausschließlich mit JSON und erfinde nichts: Was nicht im Text steht, lässt du leer."
)

USER_TEMPLATE = """Abschnitt-Typ: {marker_type}

Transkript:
\"\"\"
{transcript}
\"\"\"

Gib JSON mit genau diesen Feldern zurück:
{{
  "titel": "kurzer Titel, höchstens 60 Zeichen",
  "zusammenfassung": "2 bis 3 Sätze, sachlich",
  "bibelstellen": ["z.B. Johannes 3,16"],
  "schlagwoerter": ["3 bis 6 Stichworte"],
  "sprecher": "Name, falls im Text genannt, sonst null"
}}"""


def transcript_path(wav_path) -> Optional[pathlib.Path]:
    """Transcript next to the recording — handles both the flat and the per-session layout."""
    wav = pathlib.Path(wav_path)
    for candidate in (wav.with_suffix(".transcript.jsonl"), wav.parent / "transcript.jsonl"):
        if candidate.exists():
            return candidate
    return None


def transcript_slice(wav_path, start_seconds: float, end_seconds: float, luecken=None) -> str:
    """The spoken text between two points in time, joined into one block.
    Zeilen, die ganz in einer Lücke des Tracks liegen, gehören nicht dazu."""
    path = transcript_path(wav_path)
    if path is None:
        return ""
    texts = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            # keep every line that overlaps the segment
            z_von, z_bis = float(line.get("start", 0)), float(line.get("end", 0))
            if any(z_von >= a and z_bis <= b for a, b in (luecken or [])):
                continue
            if z_bis > start_seconds and z_von < end_seconds:
                text = str(line.get("text", "")).strip()
                # Füllsätze raus, und dieselbe Zeile nur einmal hintereinander
                if text and _echte_zeile(text) and (not texts or texts[-1] != text):
                    texts.append(text)
    return " ".join(texts)


def verified_references(candidates, transcript: str) -> list:
    """Bibelstellen, die wirklich gesprochen wurden.

    Maßgeblich ist das Transkript, nicht das Modell: das erfindet gern einen plausiblen Vers.
    Ein Vorschlag des Modells zählt nur, wenn Buch und Kapitel im Text vorkommen — und dann
    steht ohnehin schon die gesprochene Stelle in der Liste. `candidates` bleibt als Parameter,
    damit der Aufruf gleich aussieht; den Ausschlag gibt stellen_im_text()."""
    return stellen_im_text(transcript)[:5]


async def pick_model() -> Optional[str]:
    """Best installed model from the preference list (big model first, small one as fallback)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{config.ollama_url}/api/tags")
            installed = {m.get("name", "") for m in response.json().get("models", [])}
    except Exception:
        return None
    for wanted in config.ollama_models:
        for name in installed:
            if name == wanted or name.split(":")[0] == wanted.split(":")[0]:
                return name
    return None


async def analyse(transcript: str, marker_type: str) -> dict:
    """Title, summary, Bible references and keywords for one segment."""
    result = {"titel": None, "zusammenfassung": None, "bibelstellen": [], "schlagwoerter": [],
              "sprecher": None, "model": None, "error": None, "transcript": transcript}
    if marker_type in LIED_TYPEN:
        return result          # keine Fehlermeldung: es gibt hier schlicht nichts zu raten
    if not transcript.strip():
        result["error"] = "Kein Transkript für diesen Abschnitt"
        return result
    model = await pick_model()
    if model is None:
        result["error"] = "Kein Ollama-Modell verfügbar"
        return result
    result["model"] = model
    payload = {
        "model": model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(marker_type=marker_type,
                                                             transcript=transcript[:12000])},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{config.ollama_url}/api/chat", json=payload)
        data = json.loads(response.json()["message"]["content"])
    except Exception as exc:
        result["error"] = f"Ollama: {exc}"
        return result

    result["titel"] = (data.get("titel") or None)
    result["zusammenfassung"] = (data.get("zusammenfassung") or None)
    result["schlagwoerter"] = [str(k).strip() for k in (data.get("schlagwoerter") or [])][:6]
    sprecher = data.get("sprecher")
    result["sprecher"] = str(sprecher).strip() if sprecher and str(sprecher).lower() != "null" else None
    result["bibelstellen"] = verified_references(data.get("bibelstellen"), transcript)
    return result
