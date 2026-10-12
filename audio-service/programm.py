"""Stufe 0: dem Gottesdienst zuhören und mitschreiben, was angekündigt wird.

Läuft unabhängig von den Beitragsgrenzen. Wer eine Vermeldung hält, sagt oft das
ganze Restprogramm an — am Erntedankfest 2026 in einem einzigen Satz:

    "Nach dem Orchester werden wir dann die nächsten Beiträge hören, eine Prusa.
     Trotzdem dankbar ein Gruppenlied, wer rechtfertigt sich vor Gott, ein Gedicht.
     Das älteste Fest der Menschheit und ein Gruppenlied an dem Horizont."

Darin stehen fünf Beiträge mit Titeln. Wer das mitliest, muss hinterher nicht mehr
raten, *was* kommt — nur noch erkennen, *wann* es anfängt. Das ist das leichtere
Problem, und es funktioniert auch dort, wo zwischen zwei Beiträgen kaum eine Pause
liegt.

Ausgewertet wird mit dem lokalen Sprachmodell, nicht mit Mustern. Grund steht
oben im Zitat: Whisper schreibt "Prusa" statt "Prosa". Ein Muster findet das nie,
ein Modell liest darüber hinweg. Fällt Ollama aus, bleibt die Mustererkennung aus
classifier.erkenne_ankuendigung() als Rückfallebene.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELL = os.environ.get("PROGRAMM_MODELL", "qwen2.5:14b-instruct")
ZEITLIMIT_S = 45.0

# Nur diese Typen darf das Modell zurückgeben. Keine Freitexte, keine neuen
# Kategorien — sonst stehen morgen "Lobpreis" und "Musikstück" in der Datenbank.
ERLAUBTE_TYPEN = [
    "Chorlied", "Jugendchor", "Kinderchor", "Orchester", "Gruppenlied",
    "Gemeindegesang", "Gedicht", "Anspiel", "Predigt", "Kindergeschichte",
    "Prosa", "Gebetslied", "Musikstück", "Kinderlied", "Gebet", "Ansage",
    "Vermeldungen",
]

# Wie die Gemeinde die Dinge nennt, gegenüber dem, was im Marker steht.
# Aus den gedruckten Programmen vom Erntedankfest 2026 abgelesen.
_SPRACHGEBRAUCH = """Wortgebrauch in dieser Gemeinde:
- "Allgemeines Lied" und "Allgemeiner Gesang" bedeuten Gemeindegesang.
- "Das Orchester spielt" bedeutet Orchester.
- "ein Musikstück" bedeutet Musikstück — Musik auf der Bühne von einem Ensemble
  oder Solisten. Das ist etwas anderes als das Orchester. Nimm den Typ, der im
  Text genannt wird, und schließe nicht vom einen auf den andern.
- "Gebetslied" und "Kinderlied" sind eigene Typen.
- "wir singen gemeinsam" bedeutet Gemeindegesang, "unser Chor singt" Chorlied."""

# Lohnt sich die Anfrage überhaupt? Ohne eines dieser Wörter ist es keine Ansage,
# und das Modell muss nicht bemüht werden.
_ANKLANG = re.compile(
    r"\b(singen|singt|lied|chor|orchester|gedicht|beitrag|beiträge|hören|"
    r"anschließend|danach|folgt|nächste|vortragen|anspiel|geschichte)\b", re.I)

_PROMPT = """Du liest das Transkript eines Gottesdienstes mit. Der folgende Abschnitt
könnte eine Ansage sein, in der angekündigt wird, welche Beiträge als Nächstes kommen.

Erlaubte Typen: {typen}

Gib als JSON ein Objekt zurück: {{"beitraege": [{{"typ": "...", "titel": "..."}}]}}
- Nur Beiträge, die angekündigt werden, in der genannten Reihenfolge.
- WICHTIG: Der Sprecher nennt erst den Typ, dann den Titel. In "eine Prosa,
  Trotzdem dankbar, ein Gruppenlied, Wer rechtfertigt sich vor Gott" ist
  "Trotzdem dankbar" der Titel der Prosa und "Wer rechtfertigt sich vor Gott"
  der Titel des Gruppenlieds. Die Satzzeichen des Transkripts sind unzuverlässig.
- "Nach dem X hören wir Y" kündigt BEIDES an, in dieser Reihenfolge: erst X, dann Y.
  Der Sprecher blickt nach vorn, nicht zurück.
- Nur ausdrücklich Vergangenes gehört nicht dazu ("das haben wir eben gehört").
- Wird nichts angekündigt, gib {{"beitraege": []}} zurück.

{sprachgebrauch}
- Das Transkript ist automatisch erstellt und enthält Hörfehler. Lies sinngemäß.

Beispiel 1 ("nach dem" blickt nach vorn — das Chorlied kommt auch erst noch):
Abschnitt: "Nach dem Chorlied hören wir dann ein Gedicht, Der Tag ist um, und
ein Gruppenlied, Wie gross bist du."
Antwort: {{"beitraege": [{{"typ": "Chorlied", "titel": null}},
                        {{"typ": "Gedicht", "titel": "Der Tag ist um"}},
                        {{"typ": "Gruppenlied", "titel": "Wie gross bist du"}}]}}

Beispiel 2 (das genannte Wort entscheidet, nicht die Ähnlichkeit):
Abschnitt: "Das Orchester spielt die Nummer 155, Die Himmel rühmen, danach ein
Musikstück, Vergiss nicht zu danken."
Antwort: {{"beitraege": [{{"typ": "Orchester", "titel": "Die Himmel rühmen"}},
                        {{"typ": "Musikstück", "titel": "Vergiss nicht zu danken"}}]}}

Abschnitt:
{text}"""


@dataclass
class Erwartung:
    typ: str
    titel: Optional[str] = None
    angekuendigt_bei_s: float = 0.0
    erledigt: bool = False


@dataclass
class Programm:
    """Was noch erwartet wird, in der angesagten Reihenfolge."""
    eintraege: list = field(default_factory=list)

    def offen(self) -> list:
        return [e for e in self.eintraege if not e.erledigt]

    def naechster(self) -> Optional[Erwartung]:
        offen = self.offen()
        return offen[0] if offen else None

    def abhaken(self, typ: str) -> Optional[Erwartung]:
        """Den ersten offenen Eintrag dieses Typs als erledigt markieren.

        Nicht stur den vordersten: fällt ein Beitrag aus, würde sonst alles
        Folgende verrutschen.
        """
        for e in self.offen():
            if e.typ == typ:
                e.erledigt = True
                return e
        return None

    def uebernehmen(self, neue: list, bei_s: float):
        """Eine neue Ansage ersetzt den offenen Rest — sie ist aktueller."""
        if not neue:
            return
        self.eintraege = [e for e in self.eintraege if e.erledigt]
        self.eintraege.extend(
            Erwartung(typ=n["typ"], titel=n.get("titel"), angekuendigt_bei_s=bei_s)
            for n in neue
        )


def lohnt_anfrage(text: str) -> bool:
    return bool(text.strip()) and bool(_ANKLANG.search(text))


def lies_ansage(text: str, modell: str = MODELL) -> Optional[list]:
    """Fragt das Modell, welche Beiträge hier angekündigt werden.

    Gibt None zurück, wenn das Modell nicht erreichbar ist oder Unsinn antwortet —
    dann bleibt es bei dem, was ohne Ansage bekannt ist. Die Automatik darf nie an
    einem Sprachmodell hängen.
    """
    if not lohnt_anfrage(text):
        return []
    try:
        antwort = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": modell,
                "prompt": _PROMPT.format(typen=", ".join(ERLAUBTE_TYPEN),
                                         sprachgebrauch=_SPRACHGEBRAUCH, text=text),
                "format": "json",
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=ZEITLIMIT_S,
        )
        if antwort.status_code != 200:
            return None
        roh = json.loads(antwort.json().get("response", "{}"))
    except (httpx.HTTPError, ValueError, KeyError):
        return None

    beitraege = roh.get("beitraege")
    if not isinstance(beitraege, list):
        return None

    sauber = []
    for b in beitraege:
        if not isinstance(b, dict):
            continue
        typ = str(b.get("typ", "")).strip()
        if typ not in ERLAUBTE_TYPEN:      # erfundene Kategorien fallen raus
            continue
        titel = b.get("titel")
        titel = str(titel).strip() if titel else None
        sauber.append({"typ": typ, "titel": titel or None})
    return sauber
