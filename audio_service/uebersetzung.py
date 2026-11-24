"""Übersetzer-Strecke — Deutsch zum Übersetzer, sein Russisch zurück auf Dante.

Ein Übersetzer arbeitet von außerhalb. Er hört den Stream-Mix mit weniger als einer Sekunde
Verzögerung im Browser und spricht seine Übersetzung in eine Seite der Gemeinde-App. Der
Medienserver (mediamtx neben der Gemeinde-App) hat dafür zwei Pfade:

    Stream-Paar (z. B. Dante 50+51)
      → recorder._uebs_q                     (aus dem einen CoreAudio-Strom)
      → ffmpeg: s16le → Opus mono → MPEG-TS
      → srt-live-transmit ──SRT──▶ Pfad „de“  ──WebRTC──▶ Übersetzer hört

    Übersetzer spricht ──WebRTC──▶ Pfad „ru“
      ──SRT──▶ srt-live-transmit → ffmpeg: MPEG-TS → s16le mono
      → recorder.uebersetzung_einreihen()    (kleiner Puffer, Überlauf wird verworfen)
      → Duplex-Callback legt es auf UEBERSETZUNG_KANAELE (z. B. Dante 63+64)

Warum zwei Programme je Richtung: Das ffmpeg aus Homebrew kann kein SRT mehr. ffmpeg macht
nur MPEG-TS über eine Pipe, die SRT-Verbindung übernimmt `srt-live-transmit` (brew install
srt). `-chunk:1316` ist Pflicht — mit 1456 Byte zerfallen die TS-Pakete, und mediamtx
meldet Dekodierfehler (Durchstich 6. November 2026).

Der Mac baut alle Verbindungen selbst nach außen auf. Sendet der Übersetzer (noch) nicht,
lehnt der Server das Lesen von „ru“ ab; dann wird nach einer Pause still neu versucht —
das ist der Normalzustand vor dem Gottesdienst und gehört nicht ins Log.

Passphrase und Passwort stehen in der SRT-URL und damit in jeder Meldung von
srt-live-transmit. Gemeldet wird deshalb nur durch `maskiere()`.
"""
import logging
import math
import queue
import re
import shutil
import subprocess
import threading
from collections import deque
from time import monotonic
from typing import Optional
from urllib.parse import quote, urlencode

import numpy as np

from config import config

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
SRT_LATENZ_MS = 120          # SRT-Puffer gegen Paketverlust; mehr heißt später im Saal
SRT_CHUNK = 1316             # 7 × 188 Byte — ganze TS-Pakete je SRT-Paket
NEUVERBINDEN_PAUSEN = (2, 5, 10)    # Sekunden nach einem Abbruch; die letzte gilt weiter
WARTEN_PAUSE = 5             # Sekunden zwischen zwei Versuchen, solange niemand übersetzt
DATEN_FRISCH_S = 1.5         # so lange nach dem letzten Ton gilt der Übersetzer als verbunden
STILLE_NEUSTART_S = 15.0     # verbunden, aber kein Byte mehr: Verbindung hängt, neu aufbauen
SCHREIBEN_HAENGT_S = 5.0     # Sender nimmt nichts mehr ab: neu aufbauen
AUFPASSER_TAKT_S = 1.0
STOPP_FRIST_S = 5.0          # so lange darf stoppen() höchstens dauern
MASKE_MIN_LAENGE = 6         # kürzere Werte (Nutzername „mac“) würden jedes Wort zerhacken

# Zustände, wie sie das Dashboard anzeigt. Gründe sind Schlüssel, keine Texte: übersetzt
# wird in der API, der Audio-Dienst spricht keine Sprache.
AUS, VERBINDET, LAEUFT, FEHLER, NICHT_EINGERICHTET = (
    "aus", "verbindet", "laeuft", "fehler", "nicht_eingerichtet")

# Feste deutsche Sätze für HTTP-Fehler; die API übersetzt sie.
MELDUNG_NICHT_EINGERICHTET = "Übersetzung ist nicht eingerichtet"
MELDUNG_FFMPEG_FEHLT = "ffmpeg ist nicht installiert"
MELDUNG_SRT_FEHLT = "srt-live-transmit ist nicht installiert"
MELDUNGEN = {"ffmpeg_fehlt": MELDUNG_FFMPEG_FEHLT, "srt_fehlt": MELDUNG_SRT_FEHLT}


# ─── Befehle ───────────────────────────────────────────────────────────────

def eingerichtet() -> bool:
    return bool(config.uebersetzung_server and config.uebersetzung_srt_passphrase)


def srt_url(modus: str, pfad: str) -> str:
    """SRT-Adresse für mediamtx: `modus` ist publish oder read, `pfad` de oder ru.
    Nutzer und Passwort gehen in die streamid (mediamtx fragt damit die Gemeinde-App)."""
    teile = [modus, pfad]
    if config.uebersetzung_mac_user:
        teile += [config.uebersetzung_mac_user, config.uebersetzung_mac_pass]
    abfrage = urlencode({
        "streamid": ":".join(teile),
        "passphrase": config.uebersetzung_srt_passphrase,
        "latency": str(SRT_LATENZ_MS),
    })
    return f"srt://{config.uebersetzung_server}:{config.uebersetzung_srt_port}?{abfrage}"


def _srt_befehl(quelle: str, ziel: str) -> list:
    # -a:no: bei Abbruch beenden statt endlos selbst neu zu wählen — die Pausen und die
    # Statusanzeige gehören uns. -ll:fatal: sonst schreibt die SRT-Bibliothek bei manchen
    # Gegenstellen Dutzende Warnungen pro Sekunde.
    return [config.srt_bin, "-a:no", "-ll:fatal", f"-chunk:{SRT_CHUNK}", quelle, ziel]


def befehle_hin() -> tuple:
    """Stream-Mix (s16le, so viele Kanäle wie das Stream-Paar) → Opus mono → SRT „de“.
    Mono reicht für Predigt und Gesang, und der Übersetzer hört ohnehin mit Kopfhörer."""
    ffmpeg = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats",
        "-f", "s16le", "-ar", str(config.sample_rate), "-ac", str(len(config.stream_channels)),
        "-i", "pipe:0",
        "-ac", "1", "-c:a", "libopus", "-b:a", config.uebersetzung_bitrate,
        "-application", "audio", "-frame_duration", "20",
        "-flush_packets", "1", "-muxdelay", "0", "-muxpreload", "0",
        "-f", "mpegts", "pipe:1",
    ]
    return ffmpeg, _srt_befehl("file://con", srt_url("publish", "de"))


def befehle_rueck() -> tuple:
    """SRT „ru“ → MPEG-TS → s16le mono in der Abtastrate des Dante-Streams. Ohne Puffern
    und ohne langes Abtasten des Datenstroms — jede Zehntelsekunde ist Verzögerung im Saal."""
    ffmpeg = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats",
        "-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "32", "-analyzeduration", "0",
        "-f", "mpegts", "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", str(config.sample_rate), "-f", "s16le", "pipe:1",
    ]
    return _srt_befehl(srt_url("read", "ru"), "file://con"), ffmpeg


def maskiere(text: str) -> str:
    """Zugangsdaten aus einer Meldung entfernen — roh und URL-kodiert.

    streamid und passphrase werden immer ganz geschwärzt. Einzelne Werte zusätzlich überall
    im Text, aber erst ab MASKE_MIN_LAENGE Zeichen: ein kurzer Nutzername wie „mac“ würde
    sonst aus jeder Meldung Wortsalat machen und schützt ohnehin nichts."""
    text = re.sub(r"(streamid|passphrase)=[^&\s'\"]*", r"\1=***", text)
    for geheim in (config.uebersetzung_srt_passphrase, config.uebersetzung_mac_pass,
                   config.uebersetzung_mac_user):
        if geheim and len(geheim) >= MASKE_MIN_LAENGE:
            text = text.replace(geheim, "***").replace(quote(geheim, safe=""), "***")
    return text


# ─── Zwei Prozesse hintereinander ──────────────────────────────────────────

# srt-live-transmit meldet „SRT target connected“ (Senden) bzw. „SRT source connected“
# (Lesen). Nicht nur nach „connected“ suchen — „disconnected“ enthält es auch.
VERBUNDEN_MUSTER = re.compile(r"\bSRT (target|source) connected\b")


class _Kette:
    """Zwei Prozesse, die Ausgabe des ersten im Eingang des zweiten.

    `schreiben`: wir füttern den ersten (Hinweg). `lesen`: wir lesen den zweiten (Rückweg).
    stderr beider wird mitgelesen — ungelesen liefe die Pipe über einen Gottesdienst voll
    und hielte den Prozess an —, maskiert und nur als letzte Zeilen behalten."""

    def __init__(self, erster: list, zweiter: list, schreiben: bool, lesen: bool):
        self.meldungen: deque = deque(maxlen=10)
        self.verbunden = threading.Event()
        self.a = subprocess.Popen(
            erster, stdin=subprocess.PIPE if schreiben else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.b = subprocess.Popen(
                zweiter, stdin=self.a.stdout,
                stdout=subprocess.PIPE if lesen else subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError:
            self.a.kill()
            self.a.wait()
            raise
        self.a.stdout.close()       # liest allein der zweite Prozess
        for p in (self.a, self.b):
            threading.Thread(target=self._stderr_lesen, args=(p,), daemon=True,
                             name="uebersetzung-stderr").start()

    def _stderr_lesen(self, prozess: subprocess.Popen) -> None:
        try:
            for zeile in iter(prozess.stderr.readline, b""):
                text = maskiere(zeile.decode(errors="replace").strip())
                if VERBUNDEN_MUSTER.search(text):
                    self.verbunden.set()
                if text:
                    self.meldungen.append(text)
        except (OSError, ValueError):
            pass

    @property
    def eingang(self):
        return self.a.stdin

    @property
    def ausgang(self):
        return self.b.stdout

    def laeuft(self) -> bool:
        return self.a.poll() is None and self.b.poll() is None

    def letzte_meldung(self) -> str:
        return self.meldungen[-1] if self.meldungen else ""

    def beenden(self, frist: float = 2.0) -> None:
        """Beide Prozesse beenden; nach `frist` Sekunden insgesamt hart."""
        for p in (self.a, self.b):
            if p.poll() is None:
                p.terminate()
        ende = monotonic() + frist
        for p in (self.a, self.b):
            try:
                p.wait(timeout=max(0.05, ende - monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        for strom in (self.a.stdin, self.b.stdout):
            if strom is not None:
                try:
                    strom.close()
                except (OSError, ValueError):
                    pass


# ─── Ein Lauf: alles zwischen einem Start und dem zugehörigen Stopp ────────

class _Lauf:
    """Zustand genau eines Laufs. Die Threads schreiben nur in IHREN Lauf. Hängt ein
    Thread nach dem Stopp noch kurz, kann er so den nächsten Lauf nicht überschreiben."""

    def __init__(self):
        self.halt = threading.Event()
        self.threads: list = []
        self.hin: Optional[_Kette] = None
        self.rueck: Optional[_Kette] = None
        self.grund_hin: Optional[str] = None
        self.grund_rueck: Optional[str] = None
        self.versuche_hin = 0
        self.letztes_schreiben = monotonic()
        self.letzte_daten = 0.0
        self.daten_seit_verbindung = False
        self.pegel = 0.0
        self.gestartet = monotonic()
        self.letzter_fehler: Optional[str] = None


def _robust(name: str, schleife):
    """Thread-Hülle: Ein unerwarteter Fehler beendet die Schleife nicht für immer — sie
    startet nach einer Pause neu, solange der Lauf gewollt ist.

    Das ist die letzte Verteidigungslinie eines Hintergrund-Threads: Ohne sie hörte die
    Übersetzung mitten im Gottesdienst still auf, und niemand merkte es. Deshalb hier
    bewusst `except Exception`, geloggt aber nur, wenn sich der Fehler ändert."""
    def huelle(lauf: _Lauf) -> None:
        while not lauf.halt.is_set():
            try:
                schleife(lauf)
                return
            except Exception as fehler:  # noqa: BLE001 — siehe Docstring
                kennung = f"{name}: {type(fehler).__name__}: {maskiere(str(fehler))}"
                if kennung != lauf.letzter_fehler:
                    log.warning("Übersetzung %s — neuer Versuch", kennung)
                    lauf.letzter_fehler = kennung
                lauf.halt.wait(NEUVERBINDEN_PAUSEN[-1])
    return huelle


# ─── Die Strecke ───────────────────────────────────────────────────────────

class Uebersetzung:
    """Start und Stopp per Knopf, unabhängig vom Livestream. Zwei Threads tragen je eine
    Richtung, ein dritter passt auf hängende Verbindungen auf.

    starten() und stoppen() laufen unter einer Sperre: Zwei Tablets, die gleichzeitig
    drücken, oder ein Start mitten in einem Stopp ergeben genau einen Lauf, nie zwei."""

    def __init__(self, recorder):
        self._recorder = recorder
        self._sperre = threading.Lock()
        self._lauf: Optional[_Lauf] = None
        self._fehler: Optional[str] = None          # Startfehler, z. B. ffmpeg_fehlt

    @staticmethod
    def eingerichtet() -> bool:
        return eingerichtet()

    def aktiv(self) -> bool:
        return self._lauf is not None

    # ─── Start und Stopp ───────────────────────────────────────────────────

    def starten(self) -> dict:
        """Kehrt sofort zurück; das Verbinden läuft in den Threads."""
        with self._sperre:
            if not eingerichtet() or self._lauf is not None:
                return self.status()
            if shutil.which(FFMPEG) is None:
                self._fehler = "ffmpeg_fehlt"
            elif shutil.which(config.srt_bin) is None:
                self._fehler = "srt_fehlt"
            else:
                self._fehler = None
            if self._fehler:
                log.warning("Übersetzung: %s", MELDUNGEN[self._fehler])
                return self.status()
            from recorder import _uebs_q, uebersetzung_enabled
            _leeren(_uebs_q)
            lauf = _Lauf()
            lauf.threads = [
                threading.Thread(target=_robust(name, ziel), args=(lauf,), daemon=True,
                                 name=f"uebersetzung-{name}")
                for ziel, name in ((self._hin_schleife, "de"),
                                   (self._rueck_schleife, "ru"),
                                   (self._aufpassen, "aufpasser"))
            ]
            self._lauf = lauf
            uebersetzung_enabled.set()
            for t in lauf.threads:
                t.start()
            log.info("Übersetzung: gestartet")
            return self.status()

    def stoppen(self, frist: float = STOPP_FRIST_S) -> dict:
        """Höchstens `frist` Sekunden — aus der Event-Loop per asyncio.to_thread."""
        from recorder import _uebs_q, uebersetzung_enabled
        with self._sperre:
            lauf, self._lauf = self._lauf, None
            uebersetzung_enabled.clear()
            if lauf is not None:
                ende = monotonic() + frist
                lauf.halt.set()
                for kette in (lauf.hin, lauf.rueck):
                    if kette is not None:
                        kette.beenden(frist=max(0.1, (ende - monotonic()) / 2))
                for t in lauf.threads:
                    t.join(timeout=max(0.05, ende - monotonic()))
                if any(t.is_alive() for t in lauf.threads):
                    # Hängt einer, beendet er sich selbst, sobald er wieder auf halt schaut;
                    # er schreibt nur in seinen alten Lauf.
                    log.warning("Übersetzung: ein Thread hängt beim Stoppen, läuft aus")
                log.info("Übersetzung: gestoppt")
            _leeren(_uebs_q)
            self._recorder.uebersetzung_ausgang_leeren()
            return self.status()

    # ─── Hinweg: Deutsch zum Server ────────────────────────────────────────

    def _hin_schleife(self, lauf: _Lauf) -> None:
        from recorder import _uebs_q
        while not lauf.halt.is_set():
            try:
                kette = _Kette(*befehle_hin(), schreiben=True, lesen=False)
            except OSError:
                self._setze_hin(lauf, "start")
            else:
                lauf.hin = kette
                try:
                    _leeren(_uebs_q)            # nichts Altes aus der Pause hinterherschicken
                    lauf.letztes_schreiben = monotonic()
                    grund = self._fuettern(lauf, kette, _uebs_q)
                finally:
                    kette.beenden()
                    lauf.hin = None
                if lauf.halt.is_set():
                    break
                if kette.verbunden.is_set():
                    lauf.versuche_hin = 0
                self._setze_hin(lauf, grund, kette.letzte_meldung())
            pause = NEUVERBINDEN_PAUSEN[min(lauf.versuche_hin, len(NEUVERBINDEN_PAUSEN) - 1)]
            lauf.versuche_hin += 1
            lauf.halt.wait(pause)

    def _fuettern(self, lauf: _Lauf, kette: _Kette, quelle: queue.Queue) -> str:
        """Blöcke vom Stream-Bus an ffmpeg, bis Stopp oder bis die Kette abbricht."""
        while not lauf.halt.is_set():
            if not kette.laeuft():
                return "abgebrochen" if kette.verbunden.is_set() else "keine_verbindung"
            try:
                block = quelle.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                kette.eingang.write(block)
                kette.eingang.flush()
            except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
                return "abgebrochen" if kette.verbunden.is_set() else "keine_verbindung"
            lauf.letztes_schreiben = monotonic()
            if lauf.grund_hin and kette.verbunden.is_set():
                self._setze_hin(lauf, None)
        return "gestoppt"

    @staticmethod
    def _setze_hin(lauf: _Lauf, grund: Optional[str], meldung: str = "") -> None:
        if grund != lauf.grund_hin:
            if grund:
                log.warning("Übersetzung Deutsch: %s%s", grund, f" ({meldung})" if meldung else "")
            else:
                log.info("Übersetzung Deutsch: sendet")
        lauf.grund_hin = grund

    # ─── Rückweg: Russisch vom Server auf Dante ────────────────────────────

    def _rueck_schleife(self, lauf: _Lauf) -> None:
        while not lauf.halt.is_set():
            try:
                kette = _Kette(*befehle_rueck(), schreiben=False, lesen=True)
            except OSError:
                self._setze_rueck(lauf, "start")
                lauf.halt.wait(NEUVERBINDEN_PAUSEN[-1])
                continue
            lauf.rueck = kette
            lauf.daten_seit_verbindung = False
            try:
                self._lesen(lauf, kette)
            finally:
                kette.beenden()
                lauf.rueck = None
            if lauf.halt.is_set():
                break
            if lauf.daten_seit_verbindung:
                # Der Übersetzer war da und ist weg — oder die Leitung hing. Kurz warten
                # und wieder anklopfen; meist ist er nach einem Neuladen gleich zurück.
                self._setze_rueck(lauf, "getrennt")
                lauf.halt.wait(NEUVERBINDEN_PAUSEN[0])
            else:
                # Niemand sendet auf „ru“. Normalzustand vor dem Gottesdienst: still warten.
                self._setze_rueck(lauf, "wartet")
                lauf.halt.wait(WARTEN_PAUSE)

    def _lesen(self, lauf: _Lauf, kette: _Kette) -> None:
        bytes_pro_block = config.blocksize * 2        # s16le mono
        while not lauf.halt.is_set():
            try:
                roh = kette.ausgang.read(bytes_pro_block)
            except (ValueError, OSError):
                return      # Pipe von außen geschlossen (Stopp, Aufpasser) — wie Ende
            if not roh:
                return
            roh = roh[: len(roh) - len(roh) % 2]
            block = np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0
            if not lauf.daten_seit_verbindung:
                lauf.daten_seit_verbindung = True
                self._setze_rueck(lauf, None)
            lauf.letzte_daten = monotonic()
            rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
            lauf.pegel = max(rms, lauf.pegel * 0.9)     # Abfall wie ein Pegelmesser
            if not lauf.halt.is_set():
                self._recorder.uebersetzung_einreihen(block)

    @staticmethod
    def _setze_rueck(lauf: _Lauf, grund: Optional[str]) -> None:
        if grund != lauf.grund_rueck:
            if grund is None:
                log.info("Übersetzung Russisch: Übersetzer verbunden")
            elif grund == "getrennt":
                log.info("Übersetzung Russisch: Übersetzer getrennt, warte")
            elif grund != "wartet":
                log.warning("Übersetzung Russisch: %s", grund)
        lauf.grund_rueck = grund

    # ─── Aufpasser ─────────────────────────────────────────────────────────

    def _aufpassen(self, lauf: _Lauf) -> None:
        """Hängende Verbindungen beenden; die Schleifen bauen sie dann neu auf.

        Ein SRT-Abbruch ohne sauberes Ende lässt beide Richtungen sonst ewig blockieren:
        Lesen wartet auf Daten, die nie kommen, Schreiben auf eine volle Pipe."""
        from recorder import _uebs_q
        while not lauf.halt.wait(AUFPASSER_TAKT_S):
            jetzt = monotonic()
            rueck = lauf.rueck
            if (rueck is not None and lauf.daten_seit_verbindung
                    and jetzt - lauf.letzte_daten > STILLE_NEUSTART_S):
                rueck.beenden()
            hin = lauf.hin
            if (hin is not None and _uebs_q.qsize() > 10
                    and jetzt - lauf.letztes_schreiben > SCHREIBEN_HAENGT_S):
                hin.beenden()

    # ─── Status ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        if not eingerichtet():
            return {"status": NICHT_EINGERICHTET, "configured": False}
        lauf = self._lauf
        hin = lauf.hin if lauf else None
        sendet = bool(hin is not None and hin.laeuft() and hin.verbunden.is_set())
        verbunden = bool(lauf and monotonic() - lauf.letzte_daten < DATEN_FRISCH_S)
        if lauf is None:
            zustand, grund = (FEHLER, self._fehler) if self._fehler else (AUS, None)
        else:
            zustand, grund = (LAEUFT if sendet else VERBINDET), lauf.grund_hin
        dante = self._recorder.uebersetzung_ausgang_bereit()
        if dante:
            dante_grund = None
        elif not config.uebersetzung_kanaele:
            dante_grund = "keine_kanaele"
        else:
            dante_grund = "ausgang_fehlt"
        pegel_db = round(20 * math.log10(max(lauf.pegel, 1e-5)), 1) if verbunden else None
        return {
            "status": zustand,
            "configured": True,
            "aktiv": lauf is not None,
            "grund": grund,
            "sendet_deutsch": sendet,
            "uebersetzer_verbunden": verbunden,
            "pegel_ru_db": pegel_db,
            "dante_ausgabe": dante,
            "dante_grund": dante_grund,
            "ausgang_fehler": getattr(self._recorder, "ausgang_fehler", lambda: None)(),
            "kanaele": list(config.uebersetzung_kanaele),
            "laeuft_seit_s": int(monotonic() - lauf.gestartet) if lauf else None,
        }


def _leeren(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break
