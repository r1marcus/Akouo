"""Gottesdienstarten, Beitragsarten und Ansage-Regeln — jetzt aus der Datenbank.

Vorher standen diese Listen im Code, die Beitragsarten sogar zweimal (api/service_types.py
und audio-service/recorder.py), und die beiden wichen bereits voneinander ab. Seit
Migration 014 ist die Datenbank die einzige Quelle.

Warum ein Cache: Die Listen stehen in jedem Seitenaufbau und in jedem Marker-Aufruf, aendern
sich aber selten. Sie werden beim Start einmal geladen und nach jeder Aenderung neu — nicht
pro Request abgefragt.

Warum Rueckfallwerte: api/main.py ruft beim Start `Base.metadata.create_all` auf. Auf einer
Datenbank, die so entstanden ist, existieren die Tabellen leer, ohne dass die Migration
gelaufen waere. Dann gelten die Werte aus service_types.py, damit das Dashboard trotzdem
bedienbar ist.
"""
import os
from typing import Callable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import sprache
from models import AnsageRegel, MarkerType, ServiceType
import service_types as rueckfall


class Typen:
    """Der geladene Stand. Absichtlich schlichte Listen und Mengen, damit Templates und
    Pruefungen damit arbeiten koennen, ohne die Datenbank zu kennen."""

    def __init__(self) -> None:
        self.gottesdienstarten: list[str] = list(rueckfall.SERVICE_TYPES)
        self.haupt_gottesdienstarten: list[str] = list(rueckfall.PRIMARY_SERVICE_TYPES)
        self.beitragsarten: list[str] = list(rueckfall.MARKER_TYPES)
        # Gueltig als Wert, auch wenn nicht als Knopf sichtbar — der Automodus schreibt
        # "Unbenannt", solange ein Beitrag noch nicht benannt ist.
        self.beitragsarten_gueltig: set[str] = set(rueckfall.MARKER_TYPES) | {"Unbenannt"}
        self.nicht_exportieren: set[str] = set(rueckfall.NICHT_EXPORTIEREN)
        self.nas_vorlagen: dict[str, str] = {}
        # Anzeigenamen je Sprache, getrennt nach Art: {"ru": {"Predigt": "Проповедь"}}.
        # Getrennt, weil derselbe Name in beiden Tabellen stehen darf — "Sonstiges" gibt
        # es als Gottesdienstart und als Beitragsart.
        self.anzeige_gottesdienst: dict[str, dict[str, str]] = {}
        self.anzeige_beitrag: dict[str, dict[str, str]] = {}
        self.geschuetzte_arten: set[str] = {"Hochzeit", "Sonstiges"}
        self.geschuetzte_beitragsarten: set[str] = {"Predigt", "Orchester"}
        # Alles, was der Audio-Dienst fuer die Erkennung braucht. Er hat keinen
        # Datenbankzugriff und bekommt es per HTTP geschoben.
        self.erkennung: dict = {
            "musik_typen": [], "sprach_typen": [], "anker_typen": [],
            "kein_auto_marker": [], "typische_dauer": {}, "ansage_regeln": [],
        }
        self.geladen = False


aktuell = Typen()

_zuhoerer: list[Callable[[Typen], None]] = []


def bei_aenderung(fn: Callable[[Typen], None]) -> None:
    """Rueckruf anmelden, der nach jedem Neuladen laeuft — main.py aktualisiert damit die
    Template-Globals, sodass die Templates unveraendert bleiben koennen."""
    _zuhoerer.append(fn)


async def neu_laden(db: AsyncSession) -> Typen:
    """Aus der Datenbank laden. Sind die Tabellen leer, bleiben die Rueckfallwerte stehen."""
    neu = Typen()

    arten = (await db.execute(
        select(ServiceType).order_by(ServiceType.sort_order, ServiceType.name)
    )).scalars().all()
    if arten:
        neu.gottesdienstarten = [a.name for a in arten if a.active]
        neu.haupt_gottesdienstarten = [a.name for a in arten if a.active and a.is_primary]
        neu.geschuetzte_arten = {a.name for a in arten if a.geschuetzt}
        neu.nas_vorlagen = {a.name: a.nas_vorlage for a in arten if a.nas_vorlage}
        neu.anzeige_gottesdienst = {
            # getattr MIT Default: Eine Sprache in SPRACHEN, fuer die es keine Spalte
            # gibt, warf sonst in der Lifespan — die API startete dann gar nicht mehr.
            s: {a.name: getattr(a, f"anzeige_{s}", None) for a in arten
                if getattr(a, f"anzeige_{s}", None)}
            for s in sprache.SPRACHEN if s != sprache.AUSGANGSSPRACHE
        }

    beitraege = (await db.execute(
        select(MarkerType).order_by(MarkerType.sort_order, MarkerType.name)
    )).scalars().all()
    if beitraege:
        neu.beitragsarten = [b.name for b in beitraege if b.active]
        neu.beitragsarten_gueltig = {b.name for b in beitraege}
        neu.nicht_exportieren = {b.name for b in beitraege if not b.exportieren}
        neu.geschuetzte_beitragsarten = {b.name for b in beitraege if b.geschuetzt}
        neu.anzeige_beitrag = {
            s: {b.name: getattr(b, f"anzeige_{s}", None) for b in beitraege
                if getattr(b, f"anzeige_{s}", None)}
            for s in sprache.SPRACHEN if s != sprache.AUSGANGSSPRACHE
        }
        neu.erkennung = {
            "musik_typen": [b.name for b in beitraege if b.klang_klasse == "musik"],
            "sprach_typen": [b.name for b in beitraege if b.klang_klasse == "sprache"],
            "anker_typen": [b.name for b in beitraege if b.anker],
            "kein_auto_marker": [b.name for b in beitraege if not b.auto_marker],
            "typische_dauer": {
                b.name: [b.dauer_min, b.dauer_max]
                for b in beitraege if b.dauer_min is not None and b.dauer_max is not None
            },
            "ansage_regeln": [],
        }

    regeln = (await db.execute(
        select(AnsageRegel).order_by(AnsageRegel.sort_order, AnsageRegel.id)
    )).scalars().all()
    neu.erkennung["ansage_regeln"] = [
        {"muster": r.muster, "typ": r.marker_type} for r in regeln if r.active
    ]

    neu.geladen = bool(arten and beitraege)

    global aktuell
    aktuell = neu
    for fn in _zuhoerer:
        fn(neu)
    return neu


def anzeige_gottesdienstart(name: str, s: Optional[str] = None) -> str:
    """Wie diese Gottesdienstart in der aktiven Sprache heissen soll.

    Faellt auf den deutschen Namen zurueck — fuer jeden selbst angelegten Typ ist das
    der Normalfall, und das ist besser als ein leeres Feld oder ein geratener Name.
    """
    s = s or sprache.aktuelle()
    return aktuell.anzeige_gottesdienst.get(s, {}).get(name) or name


def anzeige_beitragsart(name: str, s: Optional[str] = None) -> str:
    """Wie diese Beitragsart in der aktiven Sprache heissen soll."""
    s = s or sprache.aktuelle()
    return aktuell.anzeige_beitrag.get(s, {}).get(name) or name


def anzeige_katalog(s: Optional[str] = None) -> dict:
    """Beide Anzeigenamen-Woerterbuecher fuer das JavaScript im Browser.

    Auf Deutsch bewusst leer: Dort ist der Name schon die Anzeige, und ein Woerterbuch,
    das jeden Namen auf sich selbst abbildet, waere nur Ballast auf dem Tablet."""
    s = s or sprache.aktuelle()
    if s == sprache.AUSGANGSSPRACHE:
        return {"gottesdienst": {}, "beitrag": {}}
    return {
        "gottesdienst": aktuell.anzeige_gottesdienst.get(s, {}),
        "beitrag": aktuell.anzeige_beitrag.get(s, {}),
    }


def exportieren(marker_type: str) -> bool:
    """Geht dieser Typ beim Sammel-Export mit? Einzeln exportieren bleibt immer moeglich."""
    return marker_type not in aktuell.nicht_exportieren


def nas_vorlage_fuer(service_type: str) -> Optional[str]:
    """Eigene NAS-Ordnervorlage dieser Gottesdienstart, falls eine gesetzt ist."""
    return aktuell.nas_vorlagen.get(service_type)


# ─── Zum Audio-Dienst schieben ──────────────────────────────────────────────
AUDIO_SERVICE_URL = os.environ.get("AUDIO_SERVICE_URL", "http://127.0.0.1:8001")


def erkennung_nutzlast(t: Optional[Typen] = None, zahlen: Optional[dict] = None) -> dict:
    """Was der Audio-Dienst fuer die Erkennung braucht, in einem Stueck."""
    t = t or aktuell
    nutzlast = {
        "beitragsarten": sorted(t.beitragsarten_gueltig),
        "musik_typen": t.erkennung["musik_typen"],
        "sprach_typen": t.erkennung["sprach_typen"],
        "kein_auto_marker": t.erkennung["kein_auto_marker"],
        "anker_typen": t.erkennung["anker_typen"],
        "typische_dauer": t.erkennung["typische_dauer"],
        "ansage_regeln": t.erkennung["ansage_regeln"],
        # Typen, die nie geraten werden: haben keine Klangklasse, aber die Pruefung
        # "Typ passt zur Klasse" soll sie trotzdem einordnen koennen.
        "nur_auf_ansage": {
            name: "Musik" for name in t.beitragsarten
            if name not in t.erkennung["musik_typen"]
            and name not in t.erkennung["sprach_typen"]
        },
    }
    if zahlen:
        nutzlast.update({k: v for k, v in zahlen.items() if v is not None})
    return nutzlast


async def an_audio_dienst_schieben(t: Optional[Typen] = None, zahlen: Optional[dict] = None) -> dict:
    """Stand zum Audio-Dienst schieben. Ist er nicht da, ist das kein Fehler —
    die Hintergrundschleife holt es nach, sobald er antwortet."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.put(
                f"{AUDIO_SERVICE_URL}/api/config/erkennung",
                json=erkennung_nutzlast(t, zahlen),
            )
            return {"ok": r.status_code == 200, "antwort": r.json() if r.status_code == 200 else None}
    except (httpx.HTTPError, ValueError) as fehler:
        return {"ok": False, "fehler": type(fehler).__name__}
