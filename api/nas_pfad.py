"""Den NAS-Ordner einer Aufnahme aus einer Vorlage bauen.

Vorher stand das Layout fest in routers/segments.py. Es ist aus der gewachsenen Ablage
abgeleitet, die es vor diesem Uploader schon gab:

    Gottesdienste/2026-10-04 Sonntag 1. morgens/
    Hochzeiten/2026/2026-06-06 Robin & Laura/2. Teil/
    Sonstige Aufnahmen/2026/2026-02-03/

Für eine andere Gemeinde ist das unbrauchbar, also wird es eine Einstellung. Das ist der
riskanteste Teil der Einstellbarkeit: ein Fehler verstreut Aufnahmen über das Archiv,
und bestehende Ordner werden nicht wiedergefunden. Deshalb drei Sicherungen:

  1. Die Standardvorlagen reproduzieren das bisherige Verhalten exakt — ein Test
     vergleicht sie gegen die frühere, fest verdrahtete Fassung.
  2. `[...]` sind optionale Gruppen. Fehlt darin ein Wert, verschwindet die ganze
     Gruppe. Ohne das ergäbe eine Hochzeit ohne Brautpaar-Angabe einen Ordner mit
     hängendem Leerzeichen, und ohne Teil-Nummer ein ". Teil" ohne Zahl.
  3. Der fertige Pfad wird geprüft: keine absoluten Pfade, kein "..", keine leeren
     Segmente. Eine Vorlage aus der Oberfläche darf nicht aus der Freigabe herausführen.
"""
import re
from datetime import datetime
from typing import Optional

WOCHENTAGE_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
                 "Samstag", "Sonntag"]

# Was in einer Vorlage stehen darf — treibt auch die Hilfe in der Oberfläche.
PLATZHALTER: dict[str, str] = {
    "datum": "Datum der Aufnahme, 2026-10-04",
    "jahr": "Jahr, 2026",
    "monat": "Monat zweistellig, 10",
    "tag": "Tag zweistellig, 04",
    "wochentag": "Wochentag ausgeschrieben, Sonntag",
    "tageszeit": "„1. morgens“ vor 12 Uhr, sonst „2. abends“",
    "art": "Gottesdienstart, Sonntagmorgen",
    "thema": "Freies Thema der Aufnahme, falls eines eingetragen ist",
    "brautpaar": "Brautpaar; fällt auf das Thema zurück, wenn keines eingetragen ist",
    "teil": "Teil-Nummer bei mehrteiligen Aufnahmen",
    "uhrzeit": "Startzeit, 16-49",
}

STANDARD_VORLAGEN = {
    "nas_vorlage_standard": "Gottesdienste/{datum} {wochentag} {tageszeit}",
    "nas_vorlage_hochzeit": "Hochzeiten/{jahr}/{datum}[ {brautpaar}][/{teil}. Teil]",
    "nas_vorlage_sonstiges": "Sonstige Aufnahmen/{jahr}/{datum}",
}

_GRUPPE = re.compile(r"\[([^\[\]]*)\]")
_PLATZ = re.compile(r"\{(\w+)\}")


class VorlagenFehler(ValueError):
    """Die Vorlage selbst ist unbrauchbar — wird beim Speichern gemeldet."""


def pruefen(vorlage: str) -> None:
    """Vorlage auf Brauchbarkeit prüfen. Wirft VorlagenFehler mit einem Satz für die
    Oberfläche."""
    if not vorlage or not vorlage.strip():
        raise VorlagenFehler("Die Vorlage darf nicht leer sein")
    if vorlage.count("[") != vorlage.count("]"):
        raise VorlagenFehler("Zu einer öffnenden Klammer [ fehlt die schließende ]")
    unbekannt = sorted(set(_PLATZ.findall(vorlage)) - set(PLATZHALTER))
    if unbekannt:
        erlaubt = ", ".join("{" + p + "}" for p in PLATZHALTER)
        raise VorlagenFehler(
            f"Unbekannte Platzhalter: {', '.join('{' + u + '}' for u in unbekannt)}. "
            f"Erlaubt sind: {erlaubt}")
    if vorlage.lstrip().startswith("/"):
        raise VorlagenFehler("Die Vorlage darf nicht mit / beginnen — der Pfad gilt "
                             "innerhalb der Freigabe")
    if ".." in vorlage:
        raise VorlagenFehler("„..“ ist in einer Vorlage nicht erlaubt")


def werte_fuer(session, started: Optional[datetime] = None) -> dict[str, str]:
    """Die Platzhalterwerte einer Aufnahme."""
    started = started or session.started_at
    brautpaar = getattr(session, "couple_name", None) or getattr(session, "thema", None)
    teil = getattr(session, "wedding_part", None)
    return {
        "datum": started.strftime("%Y-%m-%d"),
        "jahr": started.strftime("%Y"),
        "monat": started.strftime("%m"),
        "tag": started.strftime("%d"),
        "wochentag": WOCHENTAGE_DE[started.weekday()],
        "tageszeit": "1. morgens" if started.hour < 12 else "2. abends",
        "art": getattr(session, "service_type", "") or "",
        "thema": getattr(session, "thema", None) or "",
        "brautpaar": brautpaar or "",
        "teil": str(teil) if teil else "",
        "uhrzeit": started.strftime("%H-%M"),
    }


def _segment_saeubern(segment: str) -> str:
    """Ein einzelnes Pfadsegment auf dem NAS brauchbar machen."""
    # Zeichen, die SMB bzw. macOS nicht mögen. Der Doppelpunkt ist der wichtigste:
    # im Finder wird er zum Schrägstrich.
    segment = re.sub(r'[:\\<>"|?*\x00-\x1f]', "-", segment)
    segment = re.sub(r"\s+", " ", segment).strip(" .")
    return segment


def rendern(vorlage: str, werte: dict[str, str]) -> str:
    """Vorlage zu einem Pfad innerhalb der Freigabe machen."""
    pruefen(vorlage)

    def gruppe(fund: re.Match) -> str:
        inhalt = fund.group(1)
        namen = _PLATZ.findall(inhalt)
        # Optionale Gruppe fällt weg, sobald einer ihrer Werte fehlt.
        if any(not werte.get(n, "") for n in namen):
            return ""
        return inhalt

    text = _GRUPPE.sub(gruppe, vorlage)
    text = _PLATZ.sub(lambda m: werte.get(m.group(1), ""), text)

    segmente = [_segment_saeubern(s) for s in text.split("/")]
    segmente = [s for s in segmente if s and s != "."]
    if not segmente:
        raise VorlagenFehler("Die Vorlage ergibt mit diesen Werten einen leeren Pfad")
    if any(s == ".." for s in segmente):
        raise VorlagenFehler("Die Vorlage ergibt einen Pfad mit „..“")
    return "/".join(segmente)


BEISPIEL_SESSION = {
    "datum": "2026-06-06", "jahr": "2026", "monat": "06", "tag": "06",
    "wochentag": "Samstag", "tageszeit": "2. abends", "art": "Hochzeit",
    "thema": "Taufe Familie Müller", "brautpaar": "Robin & Laura",
    "teil": "2", "uhrzeit": "16-49",
}


def vorschau(vorlage: str, werte: Optional[dict] = None) -> dict:
    """Wie der Ordner mit Beispielwerten aussähe — für die Oberfläche, vor dem Speichern.

    Zeigt zusätzlich den Fall, dass die optionalen Werte fehlen: das ist die Variante,
    die in der gewachsenen Ablage am leichtesten übersehen wird.
    """
    werte = werte or BEISPIEL_SESSION
    ohne_optionale = dict(werte)
    for feld in ("brautpaar", "teil", "thema"):
        ohne_optionale[feld] = ""
    try:
        pruefen(vorlage)
    except VorlagenFehler as fehler:
        return {"gueltig": False, "fehler": str(fehler), "pfad": None, "ohne_optionale": None}
    try:
        return {
            "gueltig": True,
            "fehler": None,
            "pfad": rendern(vorlage, werte),
            "ohne_optionale": rendern(vorlage, ohne_optionale),
        }
    except VorlagenFehler as fehler:
        return {"gueltig": False, "fehler": str(fehler), "pfad": None, "ohne_optionale": None}
