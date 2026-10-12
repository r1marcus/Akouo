"""Automodus: erkennt während der Aufnahme selbständig, wo ein Beitrag anfängt,
was er ist und wann er vorbei ist.

Wird ausdrücklich eingeschaltet und läuft nie von selbst an.

Arbeitsteilung: hier wird nur erkannt. Marker in die Datenbank schreibt die API,
die diesen Zustand abfragt — damit hängt nichts davon ab, ob ein Browser offen ist.

Der Trick für "live": die Aufnahme läuft durch, also muss nichts in Echtzeit
entschieden werden. Erkennt der Modus zwanzig Sekunden zu spät, dass ein Beitrag
begonnen hat, trägt er ihn trotzdem mit dem richtigen Anfangszeitpunkt ein.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

import classifier
import programm as programm_modul
from boundaries import envelope

TAKT_S = 3.0              # so oft wird nachgesehen
ANSAGE_FENSTER_S = 90.0   # so weit vor einem Beitrag wird nach einer Ansage gesucht
ANSAGE_TAKT_S = 20.0      # nicht öfter als alle 20 s das Modell fragen
ANSAGE_MAX_S = 150.0      # und nie mehr als diese Spanne auf einmal
PROGRAMM_GUELTIG_S = 1200.0  # nach 20 Minuten ist eine Ansage überholt

# Ueber PROGRAMM_AUTOMATISCH=0 in der .env abschaltbar. Vorher stand hier ein
# hartverdrahtetes True, und die Betriebsanleitung verwies zum Abschalten auf
# eine Code-Aenderung — fuer ein veroeffentlichtes Projekt der falsche Weg.
PROGRAMM_AUTOMATISCH = os.environ.get("PROGRAMM_AUTOMATISCH", "1") == "1"

# Beiträge, die der Klang sicher erkennt: eine Predigt ist lange Einzelrede, ein
# Orchester ist instrumental. Sie dienen als Anker.
#
# Der Grund: Angesagt wird am Erntedankfest genau das, was der Klang NICHT trennen
# kann — Gruppenlied, Gemeindegesang, Gedicht, Prosa. Nicht angesagt wurden die
# Predigten und die Orchesterstücke, also das, was der Klang kann. Beide Quellen
# ergänzen sich; gemessen decken sie zusammen 15 von 16 Programmpunkten ab.
#
# Die Anker halten die Warteliste in Takt: an einem Anker gilt alles als erledigt,
# was davor noch offen stand. Ohne das pflanzte sich ein einziger nie angesagter
# Punkt über den ganzen Gottesdienst fort — genau der Fehler, an dem die
# Zuordnung zuerst gescheitert ist.
# Startwert; die API schiebt den Stand aus der Datenbank herein (siehe
# classifier.anwenden und PUT /api/config/erkennung).
ANKER_TYPEN = ("Predigt", "Orchester")


def programm_automatisch_setzen(an: bool) -> None:
    """Ob der Typ aus den Ansagen abgeleitet wird. Entspricht PROGRAMM_AUTOMATISCH
    in der .env, lässt sich aber auch im laufenden Betrieb umstellen."""
    global PROGRAMM_AUTOMATISCH
    PROGRAMM_AUTOMATISCH = bool(an)


def anker_setzen(typen) -> None:
    """Welche Beitragsarten als Anker dienen. Leere Liste wird ignoriert — ohne Anker
    pflanzt sich ein einziger nie angesagter Punkt ueber den ganzen Gottesdienst fort."""
    global ANKER_TYPEN
    if typen:
        ANKER_TYPEN = tuple(typen)


# Titel sind bewusst nur Anzeige und gehen NICHT in Marker oder Dateinamen.
# Gemessen am Erntedankfest: von sechs vergebenen Titeln waren drei richtig. Der
# Typ ist auch bei falscher Zuordnung brauchbar (er passt zur Klasse und steht
# sichtbar im Auswahlfeld), ein falscher Titel dagegen wandert unbemerkt ins
# Archiv. Erst wenn die Zuordnung verlässlich ist, gehören sie in den Export.
@dataclass
class Beitrag:
    nr: int
    start_s: float
    ende_s: Optional[float] = None
    typ: str = "Unbenannt"
    klasse: str = "?"
    sicherheit: float = 0.0
    alternativen: list = field(default_factory=list)
    exportieren: bool = True
    quelle: str = "klang"        # "klang" | "ansage" | "programm" | "text"
    liednummer: Optional[int] = None
    titel: Optional[str] = None


class AutoModus:
    def __init__(self, recorder):
        self._recorder = recorder
        self.aktiv = False
        self.beitraege: list[Beitrag] = []
        self._pegel: list[int] = []
        self._spur: list[str] = []      # Sprache/Musik je Fenster, wächst mit
        self._block_s = classifier.BLOCK_MS / 1000.0
        self._wav: Optional[pathlib.Path] = None
        self._task: Optional[asyncio.Task] = None
        self._naechste_nr = 1
        self.fehler: Optional[str] = None
        # Was angesagt wurde und noch aussteht — Stufe 0
        self.programm = programm_modul.Programm()
        self._transkript_gelesen_bis = 0.0
        self._ansage_zuletzt = -1e9

    # ── Steuerung ────────────────────────────────────────────────────────────
    async def start(self) -> dict:
        if self.aktiv:
            return self.status()
        status = self._recorder.status()
        if status.get("state") not in ("recording", "paused"):
            return {"status": "error", "message": "Automodus braucht eine laufende Aufnahme"}
        self._wav = pathlib.Path(status["wav_path"])
        self._pegel, self._spur, self.beitraege, self._naechste_nr = [], [], [], 1
        self.programm = programm_modul.Programm()
        self._transkript_gelesen_bis = 0.0
        self._ansage_zuletzt = -1e9
        self.fehler = None
        self.aktiv = True
        self._task = asyncio.create_task(self._schleife())
        return self.status()

    async def stop(self) -> dict:
        self.aktiv = False
        if self._task:
            self._task.cancel()
            self._task = None
        return self.status()

    def status(self) -> dict:
        return {
            "aktiv": self.aktiv,
            "gelesen_s": round(len(self._pegel) * self._block_s, 1),
            "fehler": self.fehler,
            "beitraege": [asdict(b) for b in self.beitraege],
            "programm": [asdict(e) for e in self.programm.eintraege],
        }

    # ── Schleife ─────────────────────────────────────────────────────────────
    async def _schleife(self):
        try:
            while self.aktiv:
                await asyncio.sleep(TAKT_S)
                zustand = self._recorder.status().get("state")
                if zustand not in ("recording", "paused"):
                    # Aufnahme vorbei: der Modus hat nichts mehr zu tun. Bleibt er an,
                    # zeigt das Dashboard weiter die Beiträge der alten Session und
                    # liest an einer Datei, die niemand mehr schreibt.
                    await self.stop()
                    self.beitraege, self._pegel, self._spur = [], [], []
                    self.programm = programm_modul.Programm()
                    return
                if zustand != "recording":
                    continue          # Pause: nichts lesen, aber wach bleiben
                try:
                    await asyncio.to_thread(self._aktualisieren)
                    self.fehler = None
                except Exception as exc:          # noqa: BLE001 — nie die Aufnahme gefährden
                    self.fehler = str(exc)
        except asyncio.CancelledError:
            return

    def _aktualisieren(self):
        """Pegel nachladen, Grenzen neu bestimmen, offenen Beitrag nachschärfen."""
        ab = len(self._pegel) * self._block_s
        neu, _, block_s = envelope(self._wav, ab, None, block_ms=classifier.BLOCK_MS)
        self._block_s = block_s
        if neu:
            self._pegel.extend(neu)
        if len(self._pegel) < int(classifier.MIN_BEITRAG_S / self._block_s):
            return

        self._spur_nachziehen()
        self._ansagen_mitlesen()
        grenzen = self._grenzen_aus_pegel()
        grenzen = self._an_klassenwechsel_teilen(grenzen)
        self._grenzen_uebernehmen(grenzen)
        self._offenen_nachschaerfen()

    def _grenzen_aus_pegel(self) -> list:
        """Dieselbe Stille-Logik wie im Nachhinein, nur auf dem laufenden Pegel."""
        pegel = np.array(self._pegel)
        schwelle = float(np.percentile(pegel, 20)) + classifier.STILLE_UEBER_GRUND_DB
        laut = pegel > schwelle
        min_stille = int(classifier.MIN_STILLE_S / self._block_s)
        min_beitrag = int(classifier.MIN_BEITRAG_S / self._block_s)

        segmente, start, stille = [], None, 0
        for i, ist_laut in enumerate(laut):
            if ist_laut:
                if start is None:
                    start = i
                stille = 0
            else:
                stille += 1
                if start is not None and stille >= min_stille:
                    ende = i - stille + 1
                    if ende - start >= min_beitrag:
                        segmente.append((start * self._block_s, ende * self._block_s))
                    start = None
        if start is not None and len(laut) - start >= min_beitrag:
            segmente.append((start * self._block_s, None))   # None = läuft noch
        return segmente

    def _ansagen_mitlesen(self):
        """Jede neue Transkriptzeile darauf ansehen, ob dort etwas angekündigt wird.

        Unabhängig von den Beitragsgrenzen: eine Vermeldung kann das halbe
        Restprogramm ansagen, lange bevor der nächste Beitrag beginnt.
        """
        jetzt = len(self._pegel) * self._block_s
        if jetzt - self._ansage_zuletzt < ANSAGE_TAKT_S:
            return                      # Bremse: sonst liefe alle 3 s eine Modellanfrage
        # Nie mehr als ANSAGE_MAX_S auf einmal. Ohne diese Grenze ginge beim ersten
        # Takt nach dem Einschalten das ganze bisherige Transkript ans Modell.
        von = max(self._transkript_gelesen_bis, jetzt - ANSAGE_MAX_S)
        zeilen = self._transkript_zeilen(von, jetzt)
        self._ansage_zuletzt = jetzt
        if not zeilen:
            return
        self._transkript_gelesen_bis = jetzt
        text = " ".join(z.get("text", "").strip() for z in zeilen)
        if not programm_modul.lohnt_anfrage(text):
            return
        neue = programm_modul.lies_ansage(text)
        if neue:
            self.programm.uebernehmen(neue, bei_s=zeilen[0].get("start", jetzt))

    def _spur_nachziehen(self):
        """Klassenspur um die inzwischen vollständigen Fenster verlängern.

        Mitlaufend statt im Nachhinein: eine zwanzigminütige Predigt bei jedem Takt
        neu durchzurechnen wäre zu teuer, ein einzelnes Fenster kostet nichts.
        """
        jetzt = len(self._pegel) * self._block_s
        fenster = classifier.KLASSEN_FENSTER_S
        while (len(self._spur) + 1) * fenster <= jetzt:
            von = len(self._spur) * fenster
            signal, rate = classifier._lade_mono(self._wav, von, von + fenster)
            f = classifier.merkmale(signal, rate)
            self._spur.append(
                "?" if f is None else
                ("Sprache" if classifier._stimmen_fuer_sprache(f) >= classifier.STIMMEN_FUER_SPRACHE
                 else "Musik"))

    def _klassenwechsel(self) -> list:
        """Zeitpunkte, an denen die geglättete Klasse dauerhaft umschlägt."""
        halten = max(1, int(classifier.KLASSEN_HALTEN_S / classifier.KLASSEN_FENSTER_S))
        if len(self._spur) < 2 * halten:
            return []
        geglaettet = []
        for i in range(len(self._spur)):
            f = self._spur[max(0, i - halten // 2): i + halten // 2 + 1]
            geglaettet.append(max(set(f), key=f.count))
        wechsel = [i for i in range(halten, len(geglaettet) - halten)
                   if geglaettet[i] != geglaettet[i - 1]]
        gefiltert = [w for j, w in enumerate(wechsel) if j == 0 or w - wechsel[j - 1] > halten]
        return [w * classifier.KLASSEN_FENSTER_S for w in gefiltert]

    def _an_klassenwechsel_teilen(self, grenzen: list) -> list:
        """Zwischen einem Chorlied und der Ansage danach liegt oft keine Pause."""
        wechsel = self._klassenwechsel()
        if not wechsel:
            return grenzen
        fein = []
        for start, ende in grenzen:
            schluss = ende if ende is not None else len(self._pegel) * self._block_s
            innen = [w for w in wechsel
                     if start + classifier.MIN_BEITRAG_S < w < schluss - classifier.MIN_BEITRAG_S]
            punkte = [start] + innen + [schluss]
            for i in range(len(punkte) - 1):
                letztes = (i == len(punkte) - 2)
                fein.append((punkte[i], ende if (letztes and ende is None) else punkte[i + 1]))
        return fein

    def _grenzen_uebernehmen(self, grenzen: list):
        """Neue Beiträge anlegen, beendete abschließen. Bestehende nie verschieben —
        der Tontechniker soll sich darauf verlassen können, dass nichts springt."""
        for start, ende in grenzen:
            # Über Überlappung wiedererkennen, nicht über den Startpunkt. Am Anfang
            # einer Aufnahme pendelt sich der geschätzte Ruhepegel erst ein, dadurch
            # wandert der erkannte Anfang desselben Stücks um bis zu einer Minute
            # nach vorn — mit Startpunkt-Vergleich entstanden daraus drei Marker für
            # ein Orchesterstück.
            schluss = ende if ende is not None else len(self._pegel) * self._block_s
            # Beiträge dürfen sich NICHT überlappen. Jede Überschneidung, und sei sie
            # klein, bedeutet dasselbe Stück — alles andere erzeugt zwei Marker über
            # derselben Zeit. Eine Schwelle von "mehr als die Hälfte" hatte im Betrieb
            # bei 23 von nötigen 25 Sekunden versagt und ein Lied in zwei Marker
            # zerlegt, einen davon als Ansage.
            treffer = None
            for b in self.beitraege:
                b_ende = b.ende_s if b.ende_s is not None else schluss
                if min(schluss, b_ende) > max(start, b.start_s):
                    treffer = b
                    break
            # Wandert der Anfang nach vorn, korrigieren statt neu anlegen — aber nur
            # solange der Beitrag noch läuft, sonst springt Gesetztes nachträglich weg.
            if treffer is not None and treffer.ende_s is None and start < treffer.start_s:
                treffer.start_s = round(start, 2)
                # Der Bereich ist jetzt ein anderer, also neu bewerten: ein Lied, das
                # zwischendurch nach Sprache klang, soll nicht als Ansage stehen bleiben.
                if treffer.quelle == "klang":
                    self._einschaetzen(treffer, bis=schluss)
            if treffer is None:
                b = Beitrag(nr=self._naechste_nr, start_s=round(start, 2))
                self._naechste_nr += 1
                self._ansage_auswerten(b)
                self.beitraege.append(b)
                treffer = b
            if ende is not None and treffer.ende_s is None:
                treffer.ende_s = round(ende, 2)
                self._einschaetzen(treffer)     # fertiger Beitrag: endgültig bewerten

    def _offenen_nachschaerfen(self):
        """Der laufende Beitrag wird bei jedem Takt neu bewertet — die Einschätzung
        wird mit wachsender Dauer von selbst schärfer (Längen schließen Typen aus)."""
        offen = next((b for b in self.beitraege if b.ende_s is None), None)
        if offen is None:
            return
        jetzt = len(self._pegel) * self._block_s
        if jetzt - offen.start_s < 4:
            return
        self._einschaetzen(offen, bis=jetzt)

    def _einschaetzen(self, b: Beitrag, bis: Optional[float] = None):
        erwartet = [b.typ] if b.quelle == "ansage" else None
        e = classifier.klassifiziere_bereich(self._wav, b.start_s, bis or b.ende_s, erwartet)
        if e is None:
            return
        # Kein Gesang zu hören schlägt jede Ansage: wenn es instrumental klingt, ist
        # es Orchester, auch wenn vorher etwas anderes angekündigt wurde.
        if b.quelle == "ansage" and e.typ == "Orchester" and b.typ != "Orchester":
            b.quelle = "klang"
        # Typ und Klasse müssen zueinander passen. Sagt die Ansage "Gedicht", es klingt
        # aber nach Musik, hat sie sich auf etwas anderes bezogen — dann zählt der Klang.
        erlaubt = tuple(classifier.MUSIK_TYPEN if e.klasse == "Musik" else classifier.SPRACH_TYPEN) + \
            tuple(t for t, k in classifier.NUR_AUF_ANSAGE.items() if k == e.klasse)
        if b.quelle == "ansage" and b.typ not in erlaubt:
            b.quelle = "klang" 
        b.klasse = e.klasse
        b.sicherheit = e.sicherheit
        b.alternativen = e.alternativen[:3]

        # Veto aus dem Text: Whisper wiederholt sich bei Gesang und wirft bei Musik
        # Füllsätze aus. Das schlägt die Akustik, weil es nur anschlägt, wenn es
        # sicher ist — gemessen 7 von 7 richtig, kein Fehlalarm bei echter Sprache.
        if e.klasse == "Sprache":
            gesungen, grund = classifier.gesang_aus_text(
                self._transkript_zeilen(b.start_s, bis or b.ende_s))
            if gesungen:
                b.klasse = "Musik"
                b.quelle = "text"
                b.sicherheit = 0.8
                if b.typ not in classifier.MUSIK_TYPEN:
                    rang = classifier._typ_vermutung("Musik", (bis or b.ende_s) - b.start_s,
                                                     None, None)
                    b.typ, b.alternativen = rang[0], rang[1:4]
                b.exportieren = b.typ not in classifier.NICHT_EXPORTIEREN
                return
        # Steht in der Warteliste etwas, das zur erkannten Klasse passt? Dann ist das
        # verlässlicher als jede Vermutung aus Länge und Klangfarbe.
        # Anker: Predigt und Orchester erkennt der Klang sicher. Der Typ bleibt, und
        # die Warteliste wird an dieser Stelle neu ausgerichtet.
        if b.quelle == "klang" and PROGRAMM_AUTOMATISCH and e.typ in ANKER_TYPEN \
                and e.sicherheit >= 0.8:
            self._anker_setzen(e.typ)
            b.typ = e.typ
            b.exportieren = b.typ not in classifier.NICHT_EXPORTIEREN
            return

        if b.quelle == "klang" and PROGRAMM_AUTOMATISCH:
            zuordnung = self._aus_programm(b, e.klasse)
            if zuordnung is not None:
                b.typ, b.titel = zuordnung.typ, zuordnung.titel
                b.quelle = "programm"
                b.sicherheit = 0.9
                b.exportieren = b.typ not in classifier.NICHT_EXPORTIEREN
                return

        if b.quelle not in ("ansage", "programm"):   # Ansage schlägt Klang
            b.typ = e.typ
            # Ein Gebet klingt wie eine Predigt — nur der Text verrät es.
            if e.klasse == "Sprache":
                text = self._transkript(b.start_s, bis or b.ende_s)
                if text and classifier.ist_gebet(text)[0]:
                    b.typ = "Gebet"
                    b.quelle = "text"
        b.exportieren = b.typ not in classifier.NICHT_EXPORTIEREN

    def _klasse_von(self, typ: str) -> Optional[str]:
        if typ in classifier.MUSIK_TYPEN:
            return "Musik"
        if typ in classifier.SPRACH_TYPEN:
            return "Sprache"
        return classifier.NUR_AUF_ANSAGE.get(typ)

    def _anker_setzen(self, typ: str):
        """Die Warteliste an einem sicher erkannten Beitrag neu ausrichten.

        Alles, was vor diesem Anker noch offen stand, ist entweder gelaufen und
        übersehen worden oder ausgefallen — in beiden Fällen kommt es nicht mehr.
        So bleibt ein Versatz auf die Strecke bis zum nächsten Anker begrenzt.
        """
        for eintrag in self.programm.offen():
            eintrag.erledigt = True
            if eintrag.typ == typ:
                return

    def _aus_programm(self, b: Beitrag, klasse: str):
        """Passt einer der nächsten angesagten Punkte auf diesen Beitrag?

        Ein falsch zugeordneter Titel wandert unbemerkt ins Archiv, während ein
        fehlender nur fehlt — deshalb höchstens drei Einträge vorausschauen und
        eine Ansage nicht ewig gelten lassen.
        """
        for abstand, eintrag in enumerate(self.programm.offen()[:3]):
            if self._klasse_von(eintrag.typ) != klasse:
                continue
            if b.start_s - eintrag.angekuendigt_bei_s > PROGRAMM_GUELTIG_S:
                return None          # zu lange her, die Liste ist wohl überholt
            for ueberholt in self.programm.offen()[:abstand]:
                ueberholt.erledigt = True     # der ist ausgefallen
            eintrag.erledigt = True
            return eintrag
        return None

    # ── Ansagen ──────────────────────────────────────────────────────────────
    def _ansage_auswerten(self, b: Beitrag):
        """Wurde kurz vorher angesagt, was jetzt kommt? Das schlägt den Klang —
        "unser Chor singt" gegen "wir singen gemeinsam" hört man nicht."""
        text = self._transkript(b.start_s - ANSAGE_FENSTER_S, b.start_s)
        if not text:
            return
        a = classifier.erkenne_ankuendigung(text)
        if a is None:
            return
        b.typ = a["erwartet"]
        b.quelle = "ansage"
        b.sicherheit = 0.9
        b.liednummer = a.get("liednummer")
        b.exportieren = a["exportieren"]

    def _transkript_zeilen(self, von_s: float, bis_s: float) -> list:
        """Einzelne Whisper-Zeilen — als Fließtext wäre die Wiederholung nicht mehr
        zu erkennen, und genau die ist das Signal."""
        pfad = self._wav.parent / "transcript.jsonl"
        if not pfad.exists():
            return []
        zeilen = []
        try:
            with open(pfad, encoding="utf-8") as f:
                for roh in f:
                    try:
                        e = json.loads(roh)
                    except ValueError:
                        continue
                    if von_s <= e.get("start", 0) <= bis_s:
                        zeilen.append(e)
        except OSError:
            return []
        return zeilen

    def _transkript(self, von_s: float, bis_s: float) -> str:
        pfad = self._wav.parent / "transcript.jsonl"
        if not pfad.exists():
            return ""
        teile = []
        try:
            with open(pfad, encoding="utf-8") as f:
                for zeile in f:
                    try:
                        e = json.loads(zeile)
                    except ValueError:
                        continue
                    if von_s <= e.get("start", 0) <= bis_s:
                        teile.append(e.get("text", "").strip())
        except OSError:
            return ""
        return " ".join(teile)
