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


def transcript_slice(wav_path, start_seconds: float, end_seconds: float) -> str:
    """The spoken text between two points in time, joined into one block."""
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
            if float(line.get("end", 0)) > start_seconds and float(line.get("start", 0)) < end_seconds:
                texts.append(str(line.get("text", "")).strip())
    return " ".join(t for t in texts if t)


def verified_references(candidates, transcript: str) -> list:
    """Drop references that are neither well-formed nor mentioned in the transcript."""
    found = {m.group(0).strip() for m in REFERENCE_RE.finditer(transcript)}
    verified = []
    for candidate in candidates or []:
        text = str(candidate).strip()
        if not REFERENCE_RE.fullmatch(text) and not REFERENCE_RE.match(text):
            continue
        book_chapter = text.lower().replace(" ", "")
        if any(book_chapter[:8] in f.lower().replace(" ", "") for f in found) or text in found:
            verified.append(text)
    # references clearly spoken but not returned by the model are worth keeping too
    for f in sorted(found):
        if f not in verified:
            verified.append(f)
    return _drop_contained(verified)[:5]


def _drop_contained(references: list) -> list:
    """"1. Samuel 17,49" is already covered by "1. Samuel 17,49-51" — keep the wider one."""
    def normalise(r: str) -> str:
        # Achtung: replace(".", ".") ersetzt einen Punkt durch einen Punkt und tut nichts.
        # Vermutlich war replace(".", "") gemeint, damit "1. Samuel" und "1 Samuel" gleich
        # normalisiert werden. Bewusst unveraendert gelassen: eine Korrektur aendert, welche
        # Bibelstellen als Dublette gelten, und das will erst gegen echte Transkripte geprueft
        # werden. Siehe offene Punkte in docs/stand.md.
        return r.lower().replace(" ", "").replace(".", ".")

    kept = []
    for ref in sorted(references, key=len, reverse=True):
        if not any(normalise(ref) in normalise(other) for other in kept):
            kept.append(ref)
    return sorted(kept, key=references.index)


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
