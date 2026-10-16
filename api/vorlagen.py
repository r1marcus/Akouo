"""Die **eine** Jinja-Umgebung des Projekts.

Vorher legte jeder Router, der ein Partial rendert, seine eigene an — fünf Instanzen auf
dasselbe Verzeichnis. Solange Templates nur Daten aus dem Kontext zogen, fiel das nicht auf.
Mit dem ersten Template-Global war es ein Fehler: `main.py` setzte `t` auf seiner Instanz,
die Router rendern aber mit ihrer eigenen, und jedes HTMX-Partial antwortete mit 500 —
`jinja2.exceptions.UndefinedError: 't' is undefined`, in jeder Sprache, auch auf Deutsch.

Das Tückische daran: Die Tests waren grün. Sie fassen keinen HTMX-Pfad an, und wer ein
Template von Hand rendert, baut sich eine Umgebung, in der die Globals selbstverständlich
da sind. Deshalb gibt es die Umgebung jetzt genau einmal, und `tests/test_vorlagen.py`
rendert jedes Partial durch sie hindurch.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

import sprache
import typen

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def static_url(pfad: str) -> str:
    """Adresse einer statischen Datei mit mtime-Stempel.

    Ohne den Stempel behält ein Tablet die alte `app.css`, bis jemand den Cache leert —
    und das merkt man erst, wenn das Dashboard schwarz-weiß aussieht."""
    try:
        version = int((BASE_DIR / "static" / pfad).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{pfad}?v={version}"


templates.env.globals["static_url"] = static_url

# Sprache. `t` ist die Übersetzung, der deutsche Satz ist zugleich der Schlüssel.
templates.env.globals["t"] = sprache.uebersetze
templates.env.globals["sprache_aktuell"] = sprache.aktuelle
templates.env.globals["sprachen"] = sprache.SPRACHEN
templates.env.globals["sprach_namen"] = sprache.NAMEN
templates.env.globals["sprach_katalog"] = sprache.katalog

# Anzeigenamen der Arten. Als Funktion und nicht als Liste: der Wert, der an die API geht,
# bleibt der deutsche Name — übersetzt wird nur, was danebensteht.
templates.env.globals["anzeige_gottesdienstart"] = typen.anzeige_gottesdienstart
templates.env.globals["anzeige_beitragsart"] = typen.anzeige_beitragsart
templates.env.globals["anzeige_katalog"] = typen.anzeige_katalog


def typen_bereitstellen(stand: "typen.Typen") -> None:
    """Die Listen als Globals bereitstellen, nach jedem Neuladen aus der Datenbank.

    Sie stehen in jedem Seitenaufbau (Gottesdienst-Auswahl im Kopf, Track-Knöpfe während
    der Aufnahme). Statt sie pro Request zu übergeben, werden die Globals überschrieben —
    die Templates bleiben dadurch unverändert."""
    templates.env.globals["service_types"] = stand.gottesdienstarten
    templates.env.globals["primary_service_types"] = stand.haupt_gottesdienstarten
    templates.env.globals["marker_types"] = stand.beitragsarten


typen.bei_aenderung(typen_bereitstellen)
typen_bereitstellen(typen.aktuell)   # Rückfallwerte, bis der Start die Datenbank gelesen hat
