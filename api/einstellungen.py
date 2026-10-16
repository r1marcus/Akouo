"""Alle Einstellungen an einer Stelle — was es gibt, was sie bedeuten, wer sie braucht.

Vorher lagen diese Werte verstreut: zwei Schlüssel in der settings-Tabelle, der Rest als
Konstanten in audio-service/classifier.py, boundaries.py und exporter.py. Wer die
Schnittkanten anders wollte, musste Code ändern.

Die Registry hier ist die Quelle für drei Dinge:

  * die Einstellungsseite baut ihre Felder daraus — eine neue Einstellung braucht nur
    einen Eintrag hier, kein neues Formular
  * die Prüfung beim Speichern (Typ und Grenzen)
  * was dem Audio-Dienst geschoben wird; er hat keinen Datenbankzugriff

`wirkt` sagt, wann eine Änderung greift:
  "sofort"       — die API liest den Wert beim nächsten Zugriff
  "geschoben"    — wird dem Audio-Dienst gemeldet und gilt ab dem nächsten Beitrag
  "neustart"     — steht in der .env, braucht einen Dienst-Neustart
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import nas_pfad
from sprache import uebersetze
from models import Setting


@dataclass(frozen=True)
class Feld:
    schluessel: str
    bereich: str                  # Abschnitt in der Oberfläche
    name: str                     # Beschriftung
    art: str                      # bool | int | float | text | auswahl
    standard: Any
    hilfe: str = ""
    einheit: str = ""
    von: Optional[float] = None
    bis: Optional[float] = None
    auswahl: tuple = ()
    wirkt: str = "sofort"
    # Name im Audio-Dienst, falls der Wert dorthin geschoben wird
    schiebt_als: Optional[str] = None

    def pruefen(self, rohwert: Optional[str]) -> Any:
        """Eingabe in den richtigen Typ bringen und Grenzen prüfen.
        Wirft ValueError mit einem Satz, der in der Oberfläche angezeigt werden kann."""
        if self.art == "bool":
            return str(rohwert).strip() not in ("0", "false", "False", "", "None", "off")
        if rohwert is None or str(rohwert).strip() == "":
            if self.art in ("int", "float"):
                raise ValueError(uebersetze("{feld} braucht einen Wert").format(feld=uebersetze(self.name)))
            return ""
        text = str(rohwert).strip()
        if self.art in ("int", "float"):
            try:
                wert = int(text) if self.art == "int" else float(text.replace(",", "."))
            except ValueError:
                raise ValueError(uebersetze("{feld}: „{wert}“ ist keine Zahl")
                                 .format(feld=uebersetze(self.name), wert=text))
            if self.von is not None and wert < self.von:
                raise ValueError(uebersetze("{feld} darf nicht kleiner als {grenze} sein")
                                 .format(feld=uebersetze(self.name),
                                         grenze=f"{self.von}{uebersetze(self.einheit)}"))
            if self.bis is not None and wert > self.bis:
                raise ValueError(uebersetze("{feld} darf nicht größer als {grenze} sein")
                                 .format(feld=uebersetze(self.name),
                                         grenze=f"{self.bis}{uebersetze(self.einheit)}"))
            return wert
        if self.art == "auswahl" and text not in self.auswahl:
            raise ValueError(uebersetze("{feld}: „{wert}“ ist keine der erlaubten Angaben")
                             .format(feld=uebersetze(self.name), wert=text))
        if self.schluessel.startswith("nas_vorlage_"):
            # Eine unbrauchbare Vorlage verstreut Aufnahmen über das Archiv — hier
            # abfangen, nicht erst beim Upload.
            nas_pfad.pruefen(text)
        return text


FELDER: tuple[Feld, ...] = (
    # ─── Aufnahme und Export ────────────────────────────────────────────────
    Feld("export_bitrate", "Export", "MP3-Bitrate", "auswahl", "192k",
         auswahl=("128k", "160k", "192k", "256k", "320k"),
         hilfe="Höher heißt größere Dateien, nicht hörbar bessere Sprache. 192k ist der gewachsene Wert.",
         wirkt="geschoben", schiebt_als="export_bitrate"),
    Feld("export_peak_dbfs", "Export", "Spitzenpegel", "float", -3.0,
         einheit=" dBFS", von=-12.0, bis=0.0,
         hilfe="Worauf jedes Segment normalisiert wird. −3 dBFS lässt Luft für die Weiterverarbeitung.",
         wirkt="geschoben", schiebt_als="export_peak_dbfs"),
    Feld("export_fade_ms", "Export", "Ein- und Ausblende", "int", 1000,
         einheit=" ms", von=0, bis=5000,
         hilfe="Blende am Anfang und Ende jedes Segments.",
         wirkt="geschoben", schiebt_als="export_fade_ms"),

    # ─── Automatische Erkennung ─────────────────────────────────────────────
    Feld("auto_refine_boundaries", "Erkennung", "Schnittkanten automatisch korrigieren",
         "bool", True,
         hilfe="Verschiebt den Schnitt auf die leiseste Stelle in der Nähe. Von Hand "
               "gesetzte Kanten bleiben unangetastet.",
         wirkt="sofort"),
    Feld("min_stille_s", "Erkennung", "Mindest-Stille zwischen Beiträgen", "float", 8.0,
         einheit=" s", von=2.0, bis=30.0,
         hilfe="Gemessen, nicht geschätzt: bei 5 s zerschneiden Sprechpausen die Predigt, "
               "bei 15 s verschmelzen aufeinanderfolgende Beiträge.",
         wirkt="geschoben", schiebt_als="min_stille_s"),
    Feld("min_beitrag_s", "Erkennung", "Mindestlänge eines Beitrags", "float", 45.0,
         einheit=" s", von=10.0, bis=300.0,
         hilfe="Kürzeres gilt nicht als eigener Programmpunkt. Bestimmt auch, wie früh "
               "der erste Marker erscheint.",
         wirkt="geschoben", schiebt_als="min_beitrag_s"),
    Feld("orchester_flachheit", "Erkennung", "Schwelle für Instrumentalmusik", "float", 0.22,
         von=0.05, bis=0.6,
         hilfe="Spektrale Flachheit. Gemessen: instrumental 0,14–0,17, gesungen 0,27–0,37.",
         wirkt="geschoben", schiebt_als="orchester_flachheit"),
    Feld("stimmen_fuer_sprache", "Erkennung", "Stimmen für „Sprache“", "int", 3,
         von=1, bis=5,
         hilfe="Wie viele der fünf Klangmerkmale für Sprache stimmen müssen.",
         wirkt="geschoben", schiebt_als="stimmen_fuer_sprache"),
    Feld("programm_automatisch", "Erkennung", "Typ aus Ansagen ableiten", "bool", True,
         hilfe="Wertet die Ansagen im Transkript aus, um den Typ des nächsten Beitrags zu "
               "erraten. Titel bleiben immer nur Anzeige.",
         wirkt="geschoben", schiebt_als="programm_automatisch"),

    # ─── Schnittkanten im Detail ────────────────────────────────────────────
    Feld("grenzen_suchfenster_s", "Schnittkanten", "Suchfenster um den Marker", "float", 8.0,
         einheit=" s", von=1.0, bis=30.0,
         hilfe="Wie weit um den gesetzten Zeitpunkt nach der leisesten Stelle gesucht wird.",
         wirkt="geschoben", schiebt_als="grenzen_suchfenster_s"),
    Feld("grenzen_min_ton_ms", "Schnittkanten", "Ton muss halten", "int", 400,
         einheit=" ms", von=50, bis=3000,
         hilfe="So lange muss Ton anliegen, damit er als echter Anfang zählt.",
         wirkt="geschoben", schiebt_als="grenzen_min_ton_ms"),
    Feld("grenzen_min_stille_ms", "Schnittkanten", "Stille davor", "int", 1200,
         einheit=" ms", von=100, bis=5000,
         hilfe="So viel Ruhe muss vor dem Anfang liegen. Satzpausen sind etwa 500 ms und "
               "dürfen nicht zählen, sonst springt die Kante in laufender Rede umher.",
         wirkt="geschoben", schiebt_als="grenzen_min_stille_ms"),
    Feld("grenzen_vorlauf_ms", "Schnittkanten", "Luft vor dem Ton", "int", 250,
         einheit=" ms", von=0, bis=2000,
         hilfe="Wird vor dem ersten Ton zusätzlich behalten.",
         wirkt="geschoben", schiebt_als="grenzen_vorlauf_ms"),
    Feld("grenzen_nachlauf_ms", "Schnittkanten", "Luft nach dem Ton", "int", 400,
         einheit=" ms", von=0, bis=3000,
         hilfe="Wird nach dem letzten Ton zusätzlich behalten.",
         wirkt="geschoben", schiebt_als="grenzen_nachlauf_ms"),
    Feld("grenzen_schwelle_db", "Schnittkanten", "Schwelle über dem Grundrauschen", "float", 12.0,
         einheit=" dB", von=3.0, bis=30.0,
         hilfe="Ab wie viel Dezibel über dem gemessenen Grundrauschen etwas als Ton gilt.",
         wirkt="geschoben", schiebt_als="grenzen_schwelle_db"),

    # ─── Livestream ─────────────────────────────────────────────────────────
    Feld("stream_target_url", "Livestream", "Stream-Ziel", "text", "",
         hilfe="Icecast- oder RTMP-Adresse. Enthält Zugangsdaten — wird in Anzeigen und "
               "Protokollen maskiert. Leer heißt: das Ziel aus der .env verwenden.",
         wirkt="sofort"),

    # ─── NAS-Ablage ─────────────────────────────────────────────────────────
    # Die Standardwerte kommen aus nas_pfad.py und reproduzieren die gewachsene
    # Ablage genau — ein Test vergleicht sie gegen die frühere, fest verdrahtete
    # Fassung. In [] stehende Gruppen fallen weg, wenn ein Wert darin fehlt.
    Feld("nas_vorlage_standard", "NAS-Ablage", "Ordnervorlage", "text",
         nas_pfad.STANDARD_VORLAGEN["nas_vorlage_standard"],
         hilfe="Gilt für alle Gottesdienstarten ohne eigene Vorlage. "
               "Eckige Klammern machen einen Teil optional.",
         wirkt="sofort"),
    Feld("nas_vorlage_hochzeit", "NAS-Ablage", "Ordnervorlage für Hochzeiten", "text",
         nas_pfad.STANDARD_VORLAGEN["nas_vorlage_hochzeit"],
         hilfe="Gilt für die Gottesdienstart „Hochzeit“. Ohne Brautpaar-Angabe und ohne "
               "Teil-Nummer fallen die Klammergruppen weg.",
         wirkt="sofort"),
    Feld("nas_vorlage_sonstiges", "NAS-Ablage", "Ordnervorlage für Sonstiges", "text",
         nas_pfad.STANDARD_VORLAGEN["nas_vorlage_sonstiges"],
         hilfe="Gilt für die Gottesdienstart „Sonstiges“.",
         wirkt="sofort"),
)

NACH_SCHLUESSEL: dict[str, Feld] = {f.schluessel: f for f in FELDER}

BEREICHE: tuple[str, ...] = tuple(dict.fromkeys(f.bereich for f in FELDER))


async def alle_lesen(db: AsyncSession) -> dict[str, Any]:
    """Alle Einstellungen mit ihrem Standard als Rückfall."""
    gespeichert = {
        s.key: s.value
        for s in (await db.execute(select(Setting))).scalars().all()
    }
    werte: dict[str, Any] = {}
    for feld in FELDER:
        roh = gespeichert.get(feld.schluessel)
        if roh is None:
            werte[feld.schluessel] = feld.standard
            continue
        try:
            werte[feld.schluessel] = feld.pruefen(roh)
        except ValueError:
            # Ein unbrauchbarer gespeicherter Wert darf die Seite nicht lahmlegen.
            werte[feld.schluessel] = feld.standard
    return werte


async def setzen(db: AsyncSession, schluessel: str, rohwert: Optional[str]) -> Any:
    """Eine Einstellung prüfen und speichern. Gibt den geprüften Wert zurück."""
    feld = NACH_SCHLUESSEL.get(schluessel)
    if feld is None:
        raise KeyError(schluessel)
    wert = feld.pruefen(rohwert)
    gespeichert = "1" if wert is True else ("0" if wert is False else str(wert))
    vorhanden = (await db.execute(
        select(Setting).where(Setting.key == schluessel)
    )).scalar_one_or_none()
    if vorhanden is None:
        db.add(Setting(key=schluessel, value=gespeichert))
    else:
        vorhanden.value = gespeichert
    await db.commit()
    return wert


def zahlen_fuer_audio(werte: dict[str, Any]) -> dict[str, Any]:
    """Die Teilmenge, die dem Audio-Dienst geschoben wird."""
    return {
        feld.schiebt_als: werte[feld.schluessel]
        for feld in FELDER
        if feld.schiebt_als and feld.schluessel in werte
    }
