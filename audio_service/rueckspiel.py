"""Rückspiel: einen Abschnitt einer Aufnahme über den Dante-Ausgang abspielen.

Wofür: einen Beitrag nach dem Gottesdienst noch einmal über das Pult hören, ohne einen
zweiten Rechner oder ein zweites Programm, das das Gerät öffnet. Der Ton geht über den
einen Duplex-Stream des Recorders hinaus — ein zweiter Stream auf dasselbe Gerät würde
gegen den Eingang driften (siehe CLAUDE.md).

Ein Zuführer-Thread liest die WAV blockweise und reiht die Blöcke beim Recorder ein. Der
Puffer dort ist begrenzt; der Thread wartet also im Takt des Ausgangs und liest nie die
ganze Datei auf einmal — eine Predigt sind sonst Hunderte Megabyte.
"""
import logging
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from config import config

log = logging.getLogger(__name__)


class RueckspielFehler(Exception):
    """Abschnitt lässt sich nicht abspielen. `status` ist der HTTP-Status, die Nachricht
    ein fester deutscher Satz — die API übersetzt ihn."""

    def __init__(self, status: int, nachricht: str):
        super().__init__(nachricht)
        self.status = status
        self.nachricht = nachricht


NICHT_EINGERICHTET = "Rückspiel ist nicht eingerichtet"
AUSGANG_ZU = "Der Ausgang ist nicht geöffnet"
NICHT_GEFUNDEN = "Aufnahme nicht gefunden"
UNLESBAR = "Aufnahme lässt sich nicht lesen"
RATE_PASST_NICHT = "Die Abtastrate der Aufnahme passt nicht zum Ausgang"
ABSCHNITT_UNGUELTIG = "Ungültiger Abschnitt"


def kanaele_anpassen(block: np.ndarray, ziel: int) -> np.ndarray:
    """Spalten der WAV auf die Zahl der Ausgangskanäle bringen.

    Gleich viele: unverändert. Mono heraus: Mittelwert. Sonst reihum verteilen — eine
    Stereo-Aufnahme auf vier Ausgänge ergibt L, R, L, R.
    """
    quelle = block.shape[1]
    if quelle == ziel:
        return block
    if ziel == 1:
        return block.mean(axis=1, keepdims=True)
    return block[:, [i % quelle for i in range(ziel)]]


class Rueckspiel:
    """Höchstens ein Abschnitt zur Zeit; ein neuer beendet den vorigen."""

    def __init__(self, recorder, blockgroesse: int, abtastrate: int):
        self._recorder = recorder
        self._blockgroesse = blockgroesse
        self._abtastrate = abtastrate
        self._thread: Optional[threading.Thread] = None
        self._halt = threading.Event()
        self._info: dict = {}

    def status(self) -> dict:
        laeuft = self._thread is not None and self._thread.is_alive()
        return {"bereit": self._recorder.rueckspiel_bereit(), "laeuft": laeuft,
                **(self._info if laeuft else {})}

    def pruefen(self, wav: Path, von_s: float, bis_s: float) -> tuple[int, int]:
        """Prüft alles, was vor dem Abspielen schiefgehen kann. Gibt (Startframe,
        Endframe) zurück oder wirft RueckspielFehler."""
        if not self._recorder.rueckspiel_bereit():
            if not config.output_channels:
                raise RueckspielFehler(409, NICHT_EINGERICHTET)
            raise RueckspielFehler(409, AUSGANG_ZU)
        if not wav.is_file():
            raise RueckspielFehler(404, NICHT_GEFUNDEN)
        if von_s < 0 or bis_s <= von_s:
            raise RueckspielFehler(422, ABSCHNITT_UNGUELTIG)
        try:
            with wave.open(str(wav), "rb") as w:
                rate, breite, frames = w.getframerate(), w.getsampwidth(), w.getnframes()
        except (OSError, wave.Error, EOFError):
            raise RueckspielFehler(422, UNLESBAR) from None
        if breite != 2:
            raise RueckspielFehler(422, UNLESBAR)
        if rate != self._abtastrate:
            # Umrechnen würde gehen, aber die Aufnahmen kommen vom selben Gerät — eine
            # andere Rate heißt, jemand hat AUDIO_SAMPLE_RATE seitdem geändert.
            log.warning("Rückspiel: Aufnahme %d Hz, Ausgang %d Hz", rate, self._abtastrate)
            raise RueckspielFehler(422, RATE_PASST_NICHT)
        start = int(von_s * rate)
        ende = min(int(bis_s * rate), frames)
        if start >= ende:
            raise RueckspielFehler(422, ABSCHNITT_UNGUELTIG)
        return start, ende

    def starten(self, wav: Path, von_s: float, bis_s: float) -> dict:
        """Prüfen, laufendes Rückspiel beenden, Zuführer starten. Blockiert kurz (Datei-
        kopf lesen) — aus der Event-Loop per asyncio.to_thread aufrufen."""
        start, ende = self.pruefen(wav, von_s, bis_s)
        self.stoppen()
        self._halt = threading.Event()
        self._info = {"wav_path": str(wav), "from_s": von_s,
                      "to_s": ende / self._abtastrate}
        self._thread = threading.Thread(target=self._zufuehren, args=(wav, start, ende, self._halt),
                                        name="rueckspiel", daemon=True)
        self._thread.start()
        return {"status": "playing", "from_s": von_s, "to_s": ende / self._abtastrate,
                "dauer_s": (ende - start) / self._abtastrate}

    def stoppen(self) -> dict:
        """Zuführer anhalten und den Ausgang sofort still machen."""
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._recorder.rueckspiel_stoppen()
        return {"status": "stopped"}

    def _zufuehren(self, wav: Path, start: int, ende: int, halt: threading.Event) -> None:
        """Thread: Blöcke lesen, wandeln, einreihen — bis Ende, Stopp oder Ausgang weg."""
        kanaele = self._recorder.rueckspiel_kanaele
        try:
            with wave.open(str(wav), "rb") as w:
                quelle = w.getnchannels()
                w.setpos(start)
                pos = start
                while pos < ende and not halt.is_set():
                    n = min(self._blockgroesse, ende - pos)
                    roh = w.readframes(n)
                    if not roh:
                        break
                    block = np.frombuffer(roh, dtype=np.int16).reshape(-1, quelle)
                    block = kanaele_anpassen(block.astype(np.float32) / 32768.0, kanaele)
                    pos += len(block)
                    # Warten, bis Platz ist — der Ausgang nimmt im Takt der Abtastrate ab
                    while not halt.is_set():
                        if self._recorder.rueckspiel_einreihen(block, timeout=0.5):
                            break
                        if not self._recorder.rueckspiel_bereit():
                            log.info("Rückspiel: Ausgang geschlossen, abgebrochen")
                            return
        except (OSError, wave.Error, EOFError) as e:
            log.warning("Rückspiel: Lesen fehlgeschlagen (%s)", e)
