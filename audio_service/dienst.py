"""Der Audio-Dienst über sich selbst: Zustand der Umgebung, die .env, der Neustart.

Drei Dinge, die bisher nur auf der Kommandozeile gingen:

- **Status:** Läuft alles, was ein Gottesdienst braucht? Eingang, whisper, Ollama, NAS.
  Gedacht für vorher — oben in der Checkliste —, nicht für mittendrin.
- **Umgebung:** Ein fester Satz unkritischer Werte aus der .env (Gerät, Kanäle, Modelle)
  lässt sich von der Einstellungsseite ändern. Zugangsdaten stehen bewusst NICHT in der
  Liste: das Dashboard hat keine Anmeldung, und was es anzeigt, sieht jeder im Netz.
- **Neustart:** Der Dienst beendet sich selbst sauber; launchd (KeepAlive) startet ihn
  neu und er liest die .env frisch. Verweigert, solange aufgenommen, gestreamt oder
  telefoniert wird — ein Neustart beendet all das.
"""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx

ENV_DATEI = Path(__file__).parent / ".env"


@dataclass(frozen=True)
class Wert:
    schluessel: str
    name: str
    hilfe: str
    standard: str
    muster: Optional[str] = None      # Regex, gegen die geprüft wird
    nur_lesen: bool = False


UMGEBUNG: tuple[Wert, ...] = (
    Wert("AUDIO_DEVICE_NAMES", "Audiogerät", "Das erste Gerät, dessen Name einen dieser Begriffe "
         "enthält, wird genommen. Durch Komma getrennt, Reihenfolge entscheidet.", "Dante,Focusrite",
         r"[^,\n]+(,[^,\n]+)*"),
    Wert("AUDIO_RECORD_CHANNELS", "Kanäle Aufnahme", "Gerätekanäle der Aufnahme, 1-basiert, "
         "zwei durch Komma.", "1,2", r"\d{1,3},\d{1,3}"),
    Wert("AUDIO_STREAM_CHANNELS", "Kanäle Stream", "Gerätekanäle für Livestream, Mithören und "
         "Telefon.", "1,2", r"\d{1,3},\d{1,3}"),
    Wert("AUDIO_SAMPLE_RATE", "Abtastrate", "Muss zur Soundkarte passen; Dante läuft meist auf 48000.",
         "48000", r"(44100|48000|96000)"),
    Wert("TRANSCRIBE_ENABLED", "Live-Transkript", "1 = an, 0 = aus.", "1", r"[01]"),
    Wert("TRANSCRIBE_LANGUAGE", "Sprache des Transkripts", "Sprachkürzel für whisper, z. B. de.",
         "de", r"[a-z]{2}"),
    Wert("WHISPER_URL", "whisper-Server", "Adresse des whisper-servers.",
         "http://127.0.0.1:8090/inference", r"https?://\S+"),
    Wert("OLLAMA_URL", "Ollama", "Adresse des lokalen Sprachmodells.", "http://127.0.0.1:11434",
         r"https?://\S+"),
    Wert("OLLAMA_MODELS", "Modelle KI-Auswertung", "Das erste installierte Modell wird benutzt.",
         "qwen2.5:14b-instruct,llama3.1:8b,llama3.2", r"[^\s,]+(,[^\s,]+)*"),
    Wert("PROGRAMM_MODELL", "Modell für Ansagen", "Liest live mit, was angekündigt wird.",
         "qwen2.5:14b-instruct", r"[^\s]+"),
    Wert("EINORDNUNG_MODELL", "Modell für Neu erkennen", "Ordnet gesprochene Beiträge nach dem "
         "Gottesdienst ein. Leer = wie das Modell für Ansagen.", "", r"[^\s]*"),
    Wert("STREAM_NAME", "Name des Streams", "Was Hörer im Player sehen.", "Tontechnik", r"[^\n]+"),
    Wert("RECORDINGS_DIR", "Aufnahmeordner", "Nur zur Anzeige: die Datenbank speichert absolute "
         "Pfade, ein Ändern braucht eine Datenmigration.", "~/Recordings/Tontechnik", nur_lesen=True),
)
_NACH_SCHLUESSEL = {w.schluessel: w for w in UMGEBUNG}
_ZEILE = re.compile(r"^\s*([A-Z0-9_]+)\s*=(.*)$")


# ─── .env ──────────────────────────────────────────────────────────────────

def lies_umgebung(datei: Path = ENV_DATEI) -> list[dict]:
    """Die erlaubten Werte, wie sie in der Datei stehen (sonst wie sie gerade gelten)."""
    in_datei = {}
    if datei.exists():
        for zeile in datei.read_text(encoding="utf-8").splitlines():
            m = _ZEILE.match(zeile)
            if m and m.group(1) in _NACH_SCHLUESSEL:
                in_datei[m.group(1)] = m.group(2).strip()
    return [{"schluessel": w.schluessel, "name": w.name, "hilfe": w.hilfe,
             "wert": in_datei.get(w.schluessel, os.environ.get(w.schluessel, w.standard)),
             "standard": w.standard, "nur_lesen": w.nur_lesen,
             "geaendert": in_datei.get(w.schluessel, os.environ.get(w.schluessel, w.standard))
             != os.environ.get(w.schluessel, w.standard)}
            for w in UMGEBUNG]


def pruefe(werte: dict) -> list[str]:
    """Fehlermeldungen; leer heißt: alles darf geschrieben werden."""
    fehler = []
    for schluessel, wert in werte.items():
        w = _NACH_SCHLUESSEL.get(schluessel)
        if w is None or w.nur_lesen:
            fehler.append(f"{schluessel}: darf hier nicht geändert werden")
            continue
        wert = str(wert).strip()
        if "\n" in wert or (w.muster and not re.fullmatch(w.muster, wert.replace(" ", ""))):
            fehler.append(f"{w.name}: „{wert}“ hat nicht die erwartete Form")
    return fehler


def schreibe_umgebung(werte: dict, datei: Path = ENV_DATEI) -> None:
    """Ändert nur die übergebenen Zeilen; Kommentare, Reihenfolge und alle anderen Werte —
    auch die Zugangsdaten — bleiben, wie sie sind. Fehlende Schlüssel kommen ans Ende."""
    zeilen = datei.read_text(encoding="utf-8").splitlines() if datei.exists() else []
    offen = {k: str(v).strip() for k, v in werte.items()}
    for i, zeile in enumerate(zeilen):
        m = _ZEILE.match(zeile)
        if m and m.group(1) in offen:
            zeilen[i] = f"{m.group(1)}={offen.pop(m.group(1))}"
    zeilen += [f"{k}={v}" for k, v in offen.items()]
    neu = datei.with_suffix(".env.neu")
    neu.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    if datei.exists():
        os.chmod(neu, datei.stat().st_mode & 0o777)
    neu.replace(datei)          # ganz oder gar nicht — eine halbe .env startet nicht


# ─── Status ────────────────────────────────────────────────────────────────

def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code < 500
    except httpx.HTTPError:
        return False


def _ollama(url: str, modelle: list[str]) -> dict:
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=2.0)
        r.raise_for_status()
        da = {m.get("name", "") for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "grund": "nicht_erreichbar"}
    fehlt = [m for m in modelle if m and m not in da and f"{m}:latest" not in da]
    return {"ok": not fehlt, "grund": "modell_fehlt" if fehlt else None, "detail": ", ".join(fehlt)}


def _whisper(an: bool, url: str) -> dict:
    if not an:
        return {"dienst": "whisper", "ok": True, "grund": "abgeschaltet", "detail": ""}
    ok = _http_ok(url)
    return {"dienst": "whisper", "ok": ok, "grund": None if ok else "nicht_erreichbar", "detail": ""}


def _nas(server: str) -> dict:
    if not server:
        return {"ok": True, "grund": "nicht_eingerichtet"}
    try:
        with socket.create_connection((server, 445), timeout=1.5):
            return {"ok": True, "grund": None}
    except OSError:
        return {"ok": False, "grund": "nicht_erreichbar"}


def status(cfg, eingang_da: bool, geraet: Optional[str]) -> list[dict]:
    """Was ein Gottesdienst braucht, in der Reihenfolge, in der man es prüfen würde."""
    whisper = urlsplit(cfg.whisper_url)
    modelle = [os.environ.get("PROGRAMM_MODELL", "qwen2.5:14b-instruct"),
               os.environ.get("EINORDNUNG_MODELL", "")]
    return [
        {"dienst": "eingang", "ok": eingang_da, "grund": None if eingang_da else "kein_eingang",
         "detail": geraet or ", ".join(cfg.device_names)},
        _whisper(cfg.transcribe_enabled, f"{whisper.scheme}://{whisper.netloc}/"),
        {"dienst": "ollama", **_ollama(cfg.ollama_url, [m for m in modelle if m])},
        {"dienst": "nas", **_nas(cfg.nas_server), "detail": ""},
    ]
