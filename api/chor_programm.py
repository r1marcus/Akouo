"""Das Programm des Dirigenten lesen — dieselbe PDF, die bisher in die Liedanzeige ging.

Die Dirigenten schreiben ihr Programm seit Jahren so, und das soll so bleiben: eine
Text-PDF, jede Zeile ein Programmpunkt, in Klammern der Kurztext für den Bildschirm.

    Kinderchor: Fröhlich will ich loben    (Grün 14 | Name)

Auf dem Chorbildschirm steht davon nur „Grün 14 | Name“. Leere Klammern blenden eine
Zeile aus, eine Zeile ohne Klammern erscheint so, wie sie ist. Das sind die Regeln der
alten Liedanzeige, unverändert — eine PDF, die dort richtig aussah, sieht hier genauso aus.

Neu ist, dass Akouo die Zeile auch *versteht*: „Kinderchor:“ ist eine Beitragsart, der
Rest der Titel. Daraus wird die Erwartung, mit der die Automatik arbeitet — sie weiß dann
vor dem Gottesdienst, was kommt, statt es aus den Ansagen zusammenzuhören.
"""
from __future__ import annotations

import io
import re
from typing import Iterable, Optional

# Klammerpaare, zwischen denen man wählen kann. Die Liedanzeige stand auf runden, es gibt
# aber Programme mit eckigen — beides als Einstellung, nichts Drittes.
KLAMMERN = {"( )": ("(", ")"), "[ ]": ("[", "]")}

# Wie die Programme die Dinge nennen, gegenüber der Beitragsart in Akouo. Alles klein und
# ohne Satzzeichen; was hier fehlt, wird noch direkt gegen die Namen der Beitragsarten
# verglichen. Ein Programmpunkt ohne Treffer (Begrüßung, Abschluss) bleibt reine Anzeige.
_SPRACHGEBRAUCH = {
    "chor": "Chorlied",
    "gemeindechor": "Chorlied",
    "chorlied": "Chorlied",
    "allgemeines lied": "Gemeindegesang",
    "allgemeiner gesang": "Gemeindegesang",
    "allgemeines kinderlied": "Kinderlied",
    "gemeindelied": "Gemeindegesang",
    "gemeinsames lied": "Gemeindegesang",
    "blasorchester": "Orchester",
    "orchester": "Orchester",
    "jugend": "Jugendchor",
    "jugendchor": "Jugendchor",
    "brüderchor": "Männerchor",
    "männerchor": "Männerchor",
    "schwesternchor": "Frauenchor",
    "frauenchor": "Frauenchor",
    "bekanntmachungen": "Vermeldungen",
}


def zeilen_aus_pdf(inhalt: bytes) -> list[str]:
    """Rohzeilen einer Text-PDF. Wirft ValueError, wenn sie sich nicht lesen lässt."""
    import pypdf
    from pypdf.errors import PdfReadError

    try:
        leser = pypdf.PdfReader(io.BytesIO(inhalt))
        zeilen = []
        for seite in leser.pages:
            zeilen.extend((seite.extract_text() or "").splitlines())
    except (PdfReadError, ValueError, KeyError, OSError) as fehler:
        raise ValueError(str(fehler)) from fehler
    return zeilen


def _zusammenfuegen(roh: Iterable[str], auf: str, zu: str) -> list[str]:
    """Leerzeilen weg, umgebrochene Klammern wieder zusammen.

    Word bricht eine lange Zeile manchmal mitten in der Klammer um — „… haben   (“ und
    in der nächsten Zeile „Ordner 73 | Name)“. Die Liedanzeige zeigte das als zwei
    kaputte Punkte; hier wird es wieder einer."""
    zeilen: list[str] = []
    offen = False
    for zeile in roh:
        text = " ".join(zeile.split())
        if not text:
            continue
        if offen and zeilen:
            zeilen[-1] = f"{zeilen[-1]} {text}"
        else:
            zeilen.append(text)
        offen = zeilen[-1].count(auf) > zeilen[-1].count(zu)
    return zeilen


def _art_finden(bezeichnung: str, beitragsarten: Iterable[str]) -> Optional[str]:
    schluessel = re.sub(r"[^\wäöüß ]", "", bezeichnung.lower()).strip()
    schluessel = " ".join(schluessel.split())
    if not schluessel:
        return None
    arten = {a.lower(): a for a in beitragsarten}
    if schluessel in arten:
        return arten[schluessel]
    ziel = _SPRACHGEBRAUCH.get(schluessel)
    if ziel and ziel.lower() in arten:
        return arten[ziel.lower()]
    return None


def punkt_lesen(zeile: str, auf: str, zu: str, beitragsarten: Iterable[str]) -> Optional[dict]:
    """Eine Programmzeile in Anzeige und Bedeutung zerlegen. None heißt: ausblenden."""
    muster = re.escape(auf) + r"(.*?)" + re.escape(zu)
    klammern = re.findall(muster, zeile)
    if klammern:
        kurz = ", ".join(k.strip() for k in klammern if k.strip())
        if not kurz:
            return None                      # leere Klammern: bewusst nicht anzeigen
    else:
        kurz = zeile
    # Bedeutung nur aus dem Teil vor der ersten Klammer: dahinter stehen Liedbuch und
    # Name des Dirigenten, und die gehören nicht in den Titel eines Tracks.
    vorne = " ".join(zeile.split(auf, 1)[0].split()) if klammern else zeile
    if ":" in vorne:
        bezeichnung, _, titel = vorne.partition(":")
        titel = titel.strip() or None
    else:
        bezeichnung, titel = vorne, None
    return {
        "kurz": kurz,
        "voll": zeile,
        "typ": _art_finden(bezeichnung, beitragsarten),
        "titel": titel,
    }


def programm_lesen(roh: Iterable[str], klammern: str, beitragsarten: Iterable[str]) -> list[dict]:
    auf, zu = KLAMMERN.get(klammern, KLAMMERN["( )"])
    arten = list(beitragsarten)
    punkte = []
    for zeile in _zusammenfuegen(roh, auf, zu):
        punkt = punkt_lesen(zeile, auf, zu, arten)
        if punkt is not None:
            punkte.append(punkt)
    return punkte


def erwartungen(punkte: list[dict]) -> list[dict]:
    """Was die Automatik daraus erwarten darf: nur Punkte mit erkannter Beitragsart.

    `index` ist die Stelle im angezeigten Programm — darüber sagt der Dirigent später,
    wo er gerade steht."""
    return [
        {"typ": p["typ"], "titel": p["titel"], "index": i}
        for i, p in enumerate(punkte)
        if p.get("typ")
    ]
