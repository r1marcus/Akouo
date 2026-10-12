"""Mehrsprachige Oberfläche — Deutsch, Englisch, Russisch.

**Deutsch ist die Ausgangssprache**, und zwar nicht aus Bequemlichkeit: Die Oberfläche ist
voller Begriffe aus dem Gemeindealltag, und die stehen im Template so, wie der Tontechniker
sie sagt. Der deutsche Satz ist deshalb zugleich der Schlüssel. Fehlt eine Übersetzung, steht
dort Deutsch — nie ein nackter Schlüssel wie `dashboard.aufnahme.start`, der im Betrieb wie
ein Fehler aussieht.

Übersetzt wird nur, was die Oberfläche *sagt*. Was sie *zeigt* — Transkripte, Themen,
Zusammenfassungen — bleibt in der Sprache, in der es gesprochen wurde. Und die Namen der
Gottesdienst- und Beitragsarten bleiben deutsch, weil an ihnen Logik hängt; dafür gibt es
Anzeigenamen, siehe `typen.py`.

Die Sprachdateien werden bei jeder Änderung neu gelesen. Das ist Absicht: Templates sind heiß,
und eine Übersetzung, die einen Dienst-Neustart braucht, würde man nie korrigieren.
"""
import json
import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from starlette.requests import Request

SPRACHEN: tuple[str, ...] = ("de", "en", "ru")
# Wie die Sprache in ihrer eigenen Sprache heißt — für den Umschalter. Hier und nicht im
# Template: sonst muss eine vierte Sprache an zwei Stellen gepflegt werden, und die
# zweite vergisst man.
NAMEN: dict[str, str] = {"de": "Deutsch", "en": "English", "ru": "Русский"}
AUSGANGSSPRACHE = "de"
COOKIE = "akouo_sprache"

_log = logging.getLogger(__name__)

ORDNER = Path(__file__).parent / "sprachen"

# Geladene Kataloge je Sprache, dazu die mtime der Datei, aus der sie stammen.
_katalog: dict[str, dict[str, str]] = {}
_gelesen: dict[str, float] = {}

_aktuell: ContextVar[str] = ContextVar("sprache", default=AUSGANGSSPRACHE)


def _datei(sprache: str) -> Path:
    return ORDNER / f"{sprache}.json"


def _laden(sprache: str) -> dict[str, str]:
    """Katalog holen, bei geänderter Datei neu lesen."""
    pfad = _datei(sprache)
    try:
        stand = pfad.stat().st_mtime
    except OSError:
        # Keine Datei für diese Sprache: leerer Katalog, es bleibt bei Deutsch.
        _katalog[sprache] = {}
        return _katalog[sprache]
    if _gelesen.get(sprache) != stand:
        try:
            with pfad.open(encoding="utf-8") as f:
                _katalog[sprache] = json.load(f)
        except (OSError, json.JSONDecodeError) as fehler:
            # Kaputte Sprachdatei darf die Oberfläche nicht abschießen — dann eben
            # Deutsch. Aber sie muss im Log stehen: Sonst merkt man einen Tippfehler in
            # ru.json nur daran, dass alles deutsch bleibt, und findet nirgends warum.
            _log.warning("Sprachdatei %s nicht lesbar (%s) — es bleibt bei Deutsch.",
                         pfad.name, type(fehler).__name__)
            _katalog.setdefault(sprache, {})
        # Auch nach einem Fehlschlag merken: sonst wird die kaputte Datei bei JEDEM
        # Aufruf neu gelesen und neu geparst, und eine Seite ruft hundertmal auf.
        _gelesen[sprache] = stand
    return _katalog[sprache]


def gueltig(sprache: Optional[str]) -> str:
    """Auf eine unterstützte Sprache abbilden. Alles Unbekannte wird Deutsch."""
    if not sprache:
        return AUSGANGSSPRACHE
    kurz = sprache.strip().lower()[:2]
    return kurz if kurz in SPRACHEN else AUSGANGSSPRACHE


def setzen(sprache: str) -> str:
    """Sprache für diesen Request festlegen."""
    s = gueltig(sprache)
    _aktuell.set(s)
    return s


def aktuelle() -> str:
    return _aktuell.get()


def uebersetze(text: str, sprache: Optional[str] = None) -> str:
    """Den deutschen Satz in die aktive Sprache bringen, oder ihn unverändert lassen."""
    s = gueltig(sprache) if sprache else _aktuell.get()
    if s == AUSGANGSSPRACHE:
        return text
    return _laden(s).get(text) or text


def katalog(sprache: Optional[str] = None) -> dict[str, str]:
    """Der ganze Katalog einer Sprache — für das JavaScript im Browser.

    Deutsch liefert bewusst ein leeres Wörterbuch: Die Texte stehen dort schon im Quelltext,
    und ein Wörterbuch, das jeden Satz auf sich selbst abbildet, wäre nur Ballast auf dem
    Tablet."""
    s = gueltig(sprache) if sprache else _aktuell.get()
    return {} if s == AUSGANGSSPRACHE else dict(_laden(s))


def aus_request(request: "Request") -> str:
    """Sprache dieses Geräts: Cookie, sonst Deutsch.

    Bewusst **pro Gerät** und nicht pro Anlage: Die Gottesdienst-Auswahl gilt für alle Geräte,
    weil alle denselben Gottesdienst aufnehmen. Die Sprache ist dagegen persönlich — zwei Leute
    können dieselbe Aufnahme bedienen, einer auf Deutsch, einer auf Russisch.

    Die Browsersprache wird absichtlich *nicht* ausgewertet: Ein Tablet, das auf Englisch
    steht, würde die Oberfläche beim ersten Aufruf englisch machen, und wer dann Deutsch sucht,
    sucht es in einer Sprache, die er nicht erwartet hat.
    """
    return gueltig(request.cookies.get(COOKIE))


def fehlende(sprache: str) -> list[str]:
    """Sätze aus dem deutschen Katalog, für die diese Sprache nichts hat.

    Grundlage des Tests, der eine vergessene Übersetzung in der CI auffallen lässt, statt sie
    im Gottesdienst auffallen zu lassen."""
    quelle = _laden(AUSGANGSSPRACHE)
    ziel = _laden(sprache)
    return sorted(k for k in quelle if not ziel.get(k))
