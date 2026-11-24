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
    """Zugangsdaten aus einer Meldung entfernen — roh und URL-kodiert."""
    for geheim in (config.uebersetzung_srt_passphrase, config.uebersetzung_mac_pass,
                   config.uebersetzung_mac_user):
        if geheim:
            text = text.replace(geheim, "***").replace(quote(geheim, safe=""), "***")
    return re.sub(r"(streamid|passphrase)=[^&\s'\"]*", r"\1=***", text)


# ─── Zwei Prozesse hintereinander ──────────────────────────────────────────

class _Kette:
    """Zwei Prozesse, die Ausgabe des ersten im Eingang des zweiten.

    `schreiben`: wir füttern den ersten (Hinweg). `lesen`: wir lesen den zweiten (Rückweg).
    stderr beider wird mitgelesen — ungelesen liefe die Pipe über einen Gottesdienst voll
    und hielte den Prozess an —, maskiert und nur als letzte Zeilen behalten."""

    VERBUNDEN_MARKE = "connected"     # srt-live-transmit: „SRT target connected“

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
                if self.VERBUNDEN_MARKE in text:
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

    def beenden(self) -> None:
        for p in (self.a, self.b):
            if p.poll() is None:
                p.terminate()
        for p in (self.a, self.b):
            try:
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for strom in (self.a.stdin, self.b.stdout):
            if strom is not None:
                try:
                    strom.close()
                except OSError:
                    pass


# ─── Die Strecke ───────────────────────────────────────────────────────────

class Uebersetzung:
    """Start und Stopp per Knopf, unabhängig vom Livestream. Zwei Threads tragen je eine
    Richtung, ein dritter passt auf hängende Verbindungen auf."""

    def __init__(self, recorder):
        self._recorder = recorder
        self._gewollt = False
        self._halt = threading.Event()
        self._threads: list = []
        self._hin: Optional[_Kette] = None
        self._rueck: Optional[_Kette] = None
        self._fehler: Optional[str] = None          # Startfehler, z. B. ffmpeg_fehlt
        self._grund_hin: Optional[str] = None
        self._grund_rueck: Optional[str] = None
        self._versuche_hin = 0
        self._letztes_schreiben = 0.0
        self._letzte_daten = 0.0
        self._daten_seit_verbindung = False
        self._pegel = 0.0
        self._gestartet: Optional[float] = None

    @staticmethod
    def eingerichtet() -> bool:
        return eingerichtet()

    def aktiv(self) -> bool:
        return self._gewollt

    # ─── Start und Stopp ───────────────────────────────────────────────────

    def starten(self) -> dict:
        """Kehrt sofort zurück; das Verbinden läuft in den Threads."""
        if not eingerichtet() or self._gewollt:
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
        self._halt = threading.Event()
        self._gewollt = True
        self._gestartet = monotonic()
        self._versuche_hin = 0
        self._grund_hin = self._grund_rueck = None
        self._letzte_daten = 0.0
        self._pegel = 0.0
        uebersetzung_enabled.set()
        self._threads = [
            threading.Thread(target=ziel, args=(self._halt,), daemon=True, name=name)
            for ziel, name in ((self._hin_schleife, "uebersetzung-de"),
                               (self._rueck_schleife, "uebersetzung-ru"),
                               (self._aufpassen, "uebersetzung-aufpasser"))
        ]
        for t in self._threads:
            t.start()
        log.info("Übersetzung: gestartet")
        return self.status()

    def stoppen(self) -> dict:
        """Blockiert bis zu einigen Sekunden — aus der Event-Loop per asyncio.to_thread."""
        from recorder import _uebs_q, uebersetzung_enabled
        war_aktiv = self._gewollt
        self._gewollt = False
        self._halt.set()
        uebersetzung_enabled.clear()
        for kette in (self._hin, self._rueck):
            if kette is not None:
                kette.beenden()
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads = []
        _leeren(_uebs_q)
        self._recorder.uebersetzung_ausgang_leeren()
        self._pegel = 0.0
        self._letzte_daten = 0.0
        self._gestartet = None
        if war_aktiv:
            log.info("Übersetzung: gestoppt")
        return self.status()

    # ─── Hinweg: Deutsch zum Server ────────────────────────────────────────

    def _hin_schleife(self, halt: threading.Event) -> None:
        from recorder import _uebs_q
        while not halt.is_set():
            try:
                kette = _Kette(*befehle_hin(), schreiben=True, lesen=False)
            except OSError:
                self._setze_hin("start")
            else:
                self._hin = kette
                _leeren(_uebs_q)            # nichts Altes aus der Pause hinterherschicken
                self._letztes_schreiben = monotonic()
                grund = self._fuettern(kette, _uebs_q, halt)
                kette.beenden()
                self._hin = None
                if halt.is_set():
                    break
                if kette.verbunden.is_set():
                    self._versuche_hin = 0
                self._setze_hin(grund, kette.letzte_meldung())
            pause = NEUVERBINDEN_PAUSEN[min(self._versuche_hin, len(NEUVERBINDEN_PAUSEN) - 1)]
            self._versuche_hin += 1
            halt.wait(pause)

    def _fuettern(self, kette: _Kette, quelle: queue.Queue, halt: threading.Event) -> str:
        """Blöcke vom Stream-Bus an ffmpeg, bis Stopp oder bis die Kette abbricht."""
        while not halt.is_set():
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
            self._letztes_schreiben = monotonic()
            if self._grund_hin and kette.verbunden.is_set():
                self._setze_hin(None)
        return "gestoppt"

    def _setze_hin(self, grund: Optional[str], meldung: str = "") -> None:
        if grund != self._grund_hin:
            if grund:
                log.warning("Übersetzung Deutsch: %s%s", grund, f" ({meldung})" if meldung else "")
            else:
                log.info("Übersetzung Deutsch: sendet")
        self._grund_hin = grund

    # ─── Rückweg: Russisch vom Server auf Dante ────────────────────────────

    def _rueck_schleife(self, halt: threading.Event) -> None:
        while not halt.is_set():
            try:
                kette = _Kette(*befehle_rueck(), schreiben=False, lesen=True)
            except OSError:
                self._setze_rueck("start")
                halt.wait(NEUVERBINDEN_PAUSEN[-1])
                continue
            self._rueck = kette
            self._daten_seit_verbindung = False
            self._lesen(kette, halt)
            kette.beenden()
            self._rueck = None
            if halt.is_set():
                break
            if self._daten_seit_verbindung:
                # Der Übersetzer war da und ist weg — oder die Leitung hing. Kurz warten
                # und wieder anklopfen; meist ist er nach einem Neuladen gleich zurück.
                self._setze_rueck("getrennt")
                halt.wait(NEUVERBINDEN_PAUSEN[0])
            else:
                # Niemand sendet auf „ru“. Normalzustand vor dem Gottesdienst: still warten.
                self._setze_rueck("wartet")
                halt.wait(WARTEN_PAUSE)

    def _lesen(self, kette: _Kette, halt: threading.Event) -> None:
        bytes_pro_block = config.blocksize * 2        # s16le mono
        while not halt.is_set():
            roh = kette.ausgang.read(bytes_pro_block)
            if not roh:
                return
            roh = roh[: len(roh) - len(roh) % 2]
            block = np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0
            if not self._daten_seit_verbindung:
                self._daten_seit_verbindung = True
                self._setze_rueck(None)
            self._letzte_daten = monotonic()
            rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
            self._pegel = max(rms, self._pegel * 0.9)     # Abfall wie ein Pegelmesser
            self._recorder.uebersetzung_einreihen(block)

    def _setze_rueck(self, grund: Optional[str]) -> None:
        if grund != self._grund_rueck:
            if grund is None:
                log.info("Übersetzung Russisch: Übersetzer verbunden")
            elif grund == "getrennt":
                log.info("Übersetzung Russisch: Übersetzer getrennt, warte")
            elif grund != "wartet":
                log.warning("Übersetzung Russisch: %s", grund)
        self._grund_rueck = grund

    # ─── Aufpasser ─────────────────────────────────────────────────────────

    def _aufpassen(self, halt: threading.Event) -> None:
        """Hängende Verbindungen beenden; die Schleifen bauen sie dann neu auf.

        Ein SRT-Abbruch ohne sauberes Ende lässt beide Richtungen sonst ewig blockieren:
        Lesen wartet auf Daten, die nie kommen, Schreiben auf eine volle Pipe."""
        from recorder import _uebs_q
        while not halt.wait(AUFPASSER_TAKT_S):
            jetzt = monotonic()
            rueck = self._rueck
            if (rueck is not None and self._daten_seit_verbindung
                    and jetzt - self._letzte_daten > STILLE_NEUSTART_S):
                rueck.beenden()
            hin = self._hin
            if (hin is not None and _uebs_q.qsize() > 10
                    and jetzt - self._letztes_schreiben > SCHREIBEN_HAENGT_S):
                hin.beenden()

    # ─── Status ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        if not eingerichtet():
            return {"status": NICHT_EINGERICHTET, "configured": False}
        hin = self._hin
        sendet = bool(self._gewollt and hin is not None and hin.laeuft() and hin.verbunden.is_set())
        verbunden = self._gewollt and monotonic() - self._letzte_daten < DATEN_FRISCH_S
        if not self._gewollt:
            zustand, grund = (FEHLER, self._fehler) if self._fehler else (AUS, None)
        else:
            zustand, grund = (LAEUFT if sendet else VERBINDET), self._grund_hin
        dante = self._recorder.uebersetzung_ausgang_bereit()
        if dante:
            dante_grund = None
        elif not config.uebersetzung_kanaele:
            dante_grund = "keine_kanaele"
        else:
            dante_grund = "ausgang_zu"
        pegel_db = (round(20 * math.log10(max(self._pegel, 1e-5)), 1) if verbunden else None)
        return {
            "status": zustand,
            "configured": True,
            "aktiv": self._gewollt,
            "grund": grund,
            "sendet_deutsch": sendet,
            "uebersetzer_verbunden": verbunden,
            "pegel_ru_db": pegel_db,
            "dante_ausgabe": dante,
            "dante_grund": dante_grund,
            "kanaele": list(config.uebersetzung_kanaele),
            "laeuft_seit_s": int(monotonic() - self._gestartet) if self._gestartet else None,
        }


def _leeren(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break
