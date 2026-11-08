"""Telefon-Übertragung — der Stream-Mix in die Telefonkonferenz der Gemeinde.

Wer den Gottesdienst nicht übers Internet hören kann, ruft eine Nummer an und gibt
eine PIN ein. Früher saß dafür ein zweiter PC mit der App der Telefonanlage in der
Konferenz. Jetzt wählt sich der Audio-Dienst selbst ein, sobald der Stream startet.

Der Weg des Tons:

    Stream-Paar (z. B. Dante 46+47)
      → recorder._tel_q                  (aus dem einen CoreAudio-Strom, kein zweites Öffnen)
      → OutputStream auf BlackHole 2ch   (virtuelle Soundkarte, Schleife zum eigenen Eingang)
      → baresip, Quelle BlackHole        (SIP-Softphone, eine Nebenstelle der Telefonanlage)
      → Konferenz

baresip läuft als Kindprozess ohne Oberfläche und wird über ctrl_tcp gesteuert
(JSON in Netstrings). Bricht die Verbindung mitten im Gottesdienst ab, wählt der
Dienst von selbst neu — die Hörer am Telefon sollen davon so wenig wie möglich merken.
"""
import asyncio
import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import config

log = logging.getLogger(__name__)

BYTES_PRO_FRAME = 4                 # int16, stereo
PUFFER_MAX_SEKUNDEN = 0.25          # mehr Vorlauf wird verworfen, siehe fuelle_ausgabe()
ANMELDUNG_TIMEOUT = 20.0
VERBINDUNG_TIMEOUT = 40.0
NEUWAHL_PAUSEN = (5, 10, 20, 30)    # Sekunden; die letzte gilt für alle weiteren Versuche
ZIFFER_ABSTAND = 0.3

# Zustände, wie sie das Dashboard anzeigt. Die Gründe sind Schlüssel, keine Texte:
# übersetzt wird in der API, der Audio-Dienst spricht keine Sprache.
AUS, VERBINDET, VERBUNDEN, FEHLER, NICHT_EINGERICHTET = (
    "aus", "verbindet", "verbunden", "fehler", "nicht_eingerichtet")


# ─── Ton: Stream-Bus → BlackHole ───────────────────────────────────────────

def fuelle_ausgabe(puffer: bytes, hole: Callable[[], bytes], bedarf: int, maximum: int) -> tuple:
    """Einen Ausgabeblock aus dem Stream-Bus zusammensetzen.

    Dante und BlackHole laufen auf zwei verschiedenen Uhren. Ohne Ausgleich wächst der
    Puffer über einen Gottesdienst hinweg an (Verzögerung) oder läuft leer (Knackser).
    Deshalb: Was über `maximum` hinaus ansteht, fällt weg; fehlt etwas, wird mit Stille
    aufgefüllt. Am Telefon hört man beides nicht.

    Gibt (block, rest) zurück. `hole` liefert den nächsten Block oder wirft queue.Empty.
    """
    while len(puffer) < bedarf:
        try:
            puffer += hole()
        except queue.Empty:
            break
    if len(puffer) > maximum:
        ueberschuss = len(puffer) - maximum
        ueberschuss -= ueberschuss % BYTES_PRO_FRAME
        puffer = puffer[ueberschuss:]
    block, puffer = puffer[:bedarf], puffer[bedarf:]
    if len(block) < bedarf:
        block += b"\x00" * (bedarf - len(block))
    return block, puffer


class _Ausgabe:
    """Spielt den Stream-Bus auf die virtuelle Soundkarte. Öffnet NICHT den Eingang."""

    def __init__(self):
        self._stream = None
        self._puffer = b""

    @staticmethod
    def finde_geraet(name: str) -> Optional[int]:
        import sounddevice as sd
        for index, geraet in enumerate(sd.query_devices()):
            if name.lower() in geraet["name"].lower() and geraet["max_output_channels"] >= 2:
                return index
        return None

    def _callback(self, outdata, frames: int, time, status) -> None:
        # PortAudio-Echtzeit-Thread: nur Byte-Arbeit und get_nowait(), wie im Eingang.
        from recorder import _tel_q
        bedarf = frames * BYTES_PRO_FRAME
        maximum = int(config.sample_rate * PUFFER_MAX_SEKUNDEN) * BYTES_PRO_FRAME
        block, self._puffer = fuelle_ausgabe(self._puffer, _tel_q.get_nowait, bedarf, maximum)
        outdata[:] = block

    def oeffne(self) -> bool:
        import sounddevice as sd
        from recorder import _tel_q, telefon_enabled
        geraet = self.finde_geraet(config.telefon_ausgang)
        if geraet is None:
            return False
        while not _tel_q.empty():   # alten Ton aus einer früheren Übertragung verwerfen
            try:
                _tel_q.get_nowait()
            except queue.Empty:
                break
        self._puffer = b""
        self._stream = sd.RawOutputStream(
            device=geraet, samplerate=config.sample_rate, channels=2, dtype="int16",
            blocksize=config.blocksize, callback=self._callback,
        )
        self._stream.start()
        telefon_enabled.set()
        return True

    def schliesse(self) -> None:
        import sounddevice as sd
        from recorder import telefon_enabled
        telefon_enabled.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except sd.PortAudioError:   # Aufräumpfad — das Gerät kann schon weg sein
                pass
            self._stream = None


# ─── baresip ───────────────────────────────────────────────────────────────

def netstring(daten: dict) -> bytes:
    roh = json.dumps(daten).encode()
    return str(len(roh)).encode() + b":" + roh + b","


def lies_netstrings(puffer: bytes) -> tuple:
    """Vollständige Netstrings aus dem Puffer lösen. Gibt (nachrichten, rest) zurück."""
    nachrichten = []
    while True:
        doppelpunkt = puffer.find(b":")
        if doppelpunkt < 0:
            break
        try:
            laenge = int(puffer[:doppelpunkt])
        except ValueError:          # Müll im Strom — bis zum nächsten Komma verwerfen
            komma = puffer.find(b",")
            if komma < 0:
                return nachrichten, b""
            puffer = puffer[komma + 1:]
            continue
        ende = doppelpunkt + 1 + laenge
        if len(puffer) < ende + 1:
            break
        try:
            nachrichten.append(json.loads(puffer[doppelpunkt + 1:ende]))
        except json.JSONDecodeError:
            pass
        puffer = puffer[ende + 1:]
    return nachrichten, puffer


def modulpfad() -> str:
    if config.baresip_module:
        return config.baresip_module
    bin_pfad = shutil.which(config.baresip_bin)
    if bin_pfad:
        kandidat = Path(bin_pfad).resolve().parent.parent / "lib" / "baresip" / "modules"
        if kandidat.is_dir():
            return str(kandidat)
    return "/opt/homebrew/lib/baresip/modules"


def baresip_konfiguration() -> str:
    """Die config-Datei für baresip. Nur was wir brauchen, nichts was klingelt oder blinkt.

    Abspielen geht bewusst in eine Datei nach /dev/null: Spielte baresip die Konferenz auf
    BlackHole zurück, liefe sie über unsere eigene Quelle wieder in die Konferenz."""
    return "\n".join([
        f"module_path\t\t{modulpfad()}",
        f"audio_source\t\tcoreaudio,{config.telefon_ausgang}",
        "audio_player\t\taufile,/dev/null",
        "audio_alert\t\taufile,/dev/null",
        f"ctrl_tcp_listen\t\t127.0.0.1:{config.telefon_steuer_port}",
        "module\t\t\tg711.so",
        "module\t\t\tcoreaudio.so",
        "module\t\t\taufile.so",
        "module\t\t\tsrtp.so",
        "module\t\t\tstun.so",
        "module_app\t\taccount.so",
        "module_app\t\tmenu.so",
        "module_app\t\tctrl_tcp.so",
        "",
    ])


def baresip_konto() -> str:
    """accounts-Zeile. Das Passwort steht nur hier, in einem Ordner nur für diesen Nutzer."""
    konto = config.telefon_sip_konto.strip()
    if not konto.startswith("<"):
        konto = f"<{konto if konto.startswith('sip') else 'sip:' + konto}>"
    teile = [konto, f"auth_pass={config.telefon_sip_passwort}", "answermode=manual"]
    if config.telefon_sip_zusatz.strip():
        teile.append(config.telefon_sip_zusatz.strip().lstrip(";"))
    return ";".join(teile) + "\n"


class TelefonAdapter:
    def __init__(self):
        self._ausgabe = _Ausgabe()
        self._prozess: Optional[subprocess.Popen] = None
        self._ordner: Optional[str] = None
        self._lauf: Optional[asyncio.Task] = None
        self._gewollt = False
        self._zustand = AUS
        self._grund: Optional[str] = None
        self._seit: Optional[datetime] = None
        self._versuche = 0

    @staticmethod
    def eingerichtet() -> bool:
        return bool(config.telefon_sip_konto and config.telefon_sip_passwort and config.telefon_konferenz)

    def _setze(self, zustand: str, grund: Optional[str] = None) -> None:
        if zustand != self._zustand or grund != self._grund:
            log.info("Telefon: %s%s", zustand, f" ({grund})" if grund else "")
        self._zustand, self._grund = zustand, grund
        self._seit = datetime.now() if zustand == VERBUNDEN else None

    def status(self) -> dict:
        if not self.eingerichtet():
            return {"status": NICHT_EINGERICHTET, "configured": False}
        verbunden_s = int((datetime.now() - self._seit).total_seconds()) if self._seit else None
        return {
            "status": self._zustand, "grund": self._grund, "configured": True, "aktiv": self._gewollt,
            "verbunden_seit_s": verbunden_s, "versuche": self._versuche,
        }

    def aktiv(self) -> bool:
        return self._gewollt

    # ─── Start und Stopp ───────────────────────────────────────────────────

    def starte(self) -> bool:
        """Kehrt sofort zurück; Anmelden und Einwählen dauern Sekunden und laufen im Hintergrund."""
        if not self.eingerichtet() or self._gewollt:
            return self._gewollt
        if shutil.which(config.baresip_bin) is None:
            self._setze(FEHLER, "baresip_fehlt")
            return False
        if not self._ausgabe.oeffne():
            self._setze(FEHLER, "ausgang_fehlt")
            return False
        self._gewollt = True
        self._versuche = 0
        self._setze(VERBINDET)
        self._lauf = asyncio.get_running_loop().create_task(self._betrieb())
        return True

    async def stoppe(self) -> bool:
        war_aktiv = self._gewollt
        self._gewollt = False
        if self._lauf:
            self._lauf.cancel()
            try:
                await self._lauf
            except asyncio.CancelledError:
                pass
            self._lauf = None
        await self._beende_baresip()
        self._ausgabe.schliesse()
        if self._zustand != NICHT_EINGERICHTET:
            self._setze(AUS)
        return war_aktiv

    # ─── Betrieb ───────────────────────────────────────────────────────────

    async def _betrieb(self) -> None:
        """Anmelden, einwählen, PIN — und bei Abbruch von vorn, solange der Stream laufen soll."""
        while self._gewollt:
            grund = await self._eine_verbindung()
            if not self._gewollt:
                break
            await self._beende_baresip()
            pause = NEUWAHL_PAUSEN[min(self._versuche, len(NEUWAHL_PAUSEN) - 1)]
            self._versuche += 1
            self._setze(FEHLER if grund != "abgebrochen" else VERBINDET, grund)
            await asyncio.sleep(pause)
            if self._gewollt:
                self._setze(VERBINDET, grund)

    async def _eine_verbindung(self) -> str:
        """Eine Verbindung von Anfang bis Ende. Gibt den Grund zurück, warum sie endete."""
        try:
            leser, schreiber = await self._starte_baresip()
        except (OSError, asyncio.TimeoutError):
            return "baresip_start"
        puffer = b""
        anstehend: list = []
        token = 0

        async def befehl(kommando: str, parameter: str = "") -> None:
            nonlocal token
            token += 1
            schreiber.write(netstring({"command": kommando, "params": parameter, "token": str(token)}))
            await schreiber.drain()

        async def naechstes_ereignis(timeout: float) -> Optional[dict]:
            """Nächstes Ereignis von baresip; Antworten auf Befehle werden übergangen."""
            nonlocal puffer
            ablauf = asyncio.get_running_loop().time() + timeout
            while True:
                while anstehend:
                    nachricht = anstehend.pop(0)
                    if nachricht.get("event"):
                        return nachricht
                bleibt = ablauf - asyncio.get_running_loop().time()
                if bleibt <= 0:
                    return None
                try:
                    daten = await asyncio.wait_for(leser.read(4096), timeout=bleibt)
                except asyncio.TimeoutError:
                    return None
                if not daten:
                    raise ConnectionResetError
                nachrichten, puffer = lies_netstrings(puffer + daten)
                anstehend.extend(nachrichten)

        async def warte_auf(typen: set, timeout: float, fehlertypen: set) -> Optional[str]:
            ablauf = asyncio.get_running_loop().time() + timeout
            while True:
                bleibt = ablauf - asyncio.get_running_loop().time()
                if bleibt <= 0:
                    return None
                ereignis = await naechstes_ereignis(bleibt)
                if ereignis is None:
                    return None
                typ = ereignis.get("type", "")
                if typ in typen or typ in fehlertypen:
                    return typ

        try:
            typ = await warte_auf({"REGISTER_OK"}, ANMELDUNG_TIMEOUT, {"REGISTER_FAIL"})
            if typ != "REGISTER_OK":
                return "anmeldung"
            await befehl("dial", config.telefon_konferenz)
            typ = await warte_auf({"CALL_ESTABLISHED"}, VERBINDUNG_TIMEOUT, {"CALL_CLOSED"})
            if typ != "CALL_ESTABLISHED":
                return "waehlen"
            # Die Konferenz spielt erst ihre Ansage ab und fragt dann nach der PIN.
            await asyncio.sleep(config.telefon_pin_verzoegerung)
            for ziffer in config.telefon_pin:
                await befehl("sndcode", ziffer)
                await asyncio.sleep(ZIFFER_ABSTAND)
            self._versuche = 0
            self._setze(VERBUNDEN)
            while self._gewollt:
                ereignis = await naechstes_ereignis(3600)
                if ereignis and ereignis.get("type") == "CALL_CLOSED":
                    return "abgebrochen"
                if self._prozess is not None and self._prozess.poll() is not None:
                    return "abgebrochen"
            return "gestoppt"
        except (ConnectionResetError, BrokenPipeError, OSError):
            return "abgebrochen"
        finally:
            try:
                await befehl("hangup")
                await befehl("quit")
            except (ConnectionResetError, BrokenPipeError, OSError, RuntimeError):
                pass
            schreiber.close()

    async def _starte_baresip(self) -> tuple:
        self._ordner = tempfile.mkdtemp(prefix="akouo-telefon-")
        os.chmod(self._ordner, 0o700)
        Path(self._ordner, "config").write_text(baresip_konfiguration())
        konten = Path(self._ordner, "accounts")
        konten.write_text(baresip_konto())
        os.chmod(konten, 0o600)
        self._prozess = subprocess.Popen(
            [config.baresip_bin, "-f", self._ordner],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # ctrl_tcp lauscht erst, wenn die Module geladen sind
        ablauf = asyncio.get_running_loop().time() + 10
        while True:
            try:
                return await asyncio.open_connection("127.0.0.1", config.telefon_steuer_port)
            except OSError:
                if self._prozess.poll() is not None or asyncio.get_running_loop().time() > ablauf:
                    raise
                await asyncio.sleep(0.3)

    async def _beende_baresip(self) -> None:
        prozess, self._prozess = self._prozess, None
        if prozess is not None and prozess.poll() is None:
            prozess.terminate()
            for _ in range(30):
                if prozess.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                prozess.kill()
        if self._ordner:
            shutil.rmtree(self._ordner, ignore_errors=True)   # Passwort nicht liegen lassen
            self._ordner = None
