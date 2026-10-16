"""Jedes Template muss sich durch die echte Jinja-Umgebung rendern lassen.

Der Test gibt es, weil genau das einmal nicht galt: Fünf Router legten je eine eigene
`Jinja2Templates`-Instanz an, die Template-Globals standen aber nur auf der aus `main.py`.
Jedes HTMX-Partial antwortete mit 500 — in jeder Sprache, auch auf Deutsch — und die
Testsuite blieb grün, weil sie keinen HTMX-Pfad anfasst und ein von Hand gerendertes
Template seine Globals vom Test gestellt bekommt.

Deshalb wird hier nichts gestellt. Was fehlt, fällt auf.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUDIO_SERVICE_URL", "http://127.0.0.1:9")
sys.path.insert(0, "api")

import pytest

VORLAGEN = Path(__file__).resolve().parent.parent / "api" / "templates"

# Kontext, den die Partials von ihren Routern bekommen. Bewusst knapp: Fehlt eine
# Variable, rendert Jinja sie leer — nur ein fehlendes *Global* wirft, und genau darum
# geht es hier.
KONTEXT = {
    "request": type("Anfrage", (), {"url": type("Adresse", (), {"path": "/"})()})(),
    "status": "error", "configured": True, "elapsed_seconds": 7,
    "error_message": None, "target": None, "channels": [46, 47],
    "session_id": 1, "service_type": "Sonntagmorgen", "segments": [], "nicht_export": 0,
    "item": type("I", (), {"id": 1, "text": "Pult an", "done": False, "gruppe": "Vor", "sort_order": 1})(),
    "tpl": type("T", (), {"id": 1, "name": "Sonntag", "items": [], "item_count": 2})(),
    "available_templates": [], "recordings": [],
}


def _alle_templates() -> list[str]:
    return sorted(str(p.relative_to(VORLAGEN)) for p in VORLAGEN.rglob("*.html"))


@pytest.mark.parametrize("name", _alle_templates())
def test_template_rendert_durch_die_echte_umgebung(name):
    from vorlagen import templates
    templates.get_template(name).render(**KONTEXT)


@pytest.mark.parametrize("name", _alle_templates())
def test_template_rendert_auch_auf_russisch(name):
    """Dieselbe Prüfung mit gesetzter Sprache — ein Global, das nur auf Deutsch nie
    aufgerufen wird, fällt sonst durch."""
    import sprache
    from vorlagen import templates
    sprache.setzen("ru")
    try:
        templates.get_template(name).render(**KONTEXT)
    finally:
        sprache.setzen("de")


def test_es_gibt_genau_eine_jinja_umgebung():
    """Ein neuer Router mit eigener Instanz bringt den Fehler zurück."""
    import re
    api = Path(__file__).resolve().parent.parent / "api"
    stellen = [
        f"{p.relative_to(api)}:{i}"
        for p in api.rglob("*.py")
        for i, zeile in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"Jinja2Templates\(", zeile)
    ]
    dateien = sorted({s.split(":")[0] for s in stellen})
    assert dateien == ["vorlagen.py"], (
        f"Jinja2Templates darf nur in api/vorlagen.py stehen, gefunden: {stellen}"
    )
