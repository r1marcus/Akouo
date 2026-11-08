import asyncio
import contextlib
import json
import logging
import pathlib

import httpx
import queue
import os
import signal as _signal
import tempfile
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent / ".env")
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

try:
    from fastapi.sse import EventSourceResponse
except ImportError:  # fallback: sse-starlette
    from sse_starlette.sse import EventSourceResponse

import boundaries as boundaries_modul
import classifier
import laufzeit
import recorder as recorder_modul
from recorder import ContinuousRecorder
from rueckspiel import Rueckspiel, RueckspielFehler
from streamer import StreamAdapter, public_target
from telefon import TelefonAdapter
import online as online_modul
import dienst
import vermeldungen as vermeldungen_modul
from config import config, export_dir
from exporter import export_wav_to_mp3, gesamt_mp3
from segment_exporter import export_segment
from boundaries import refine as refine_boundaries, envelope as level_envelope
import automode as automode_modul
import programm as programm_modul
from automode import AutoModus
from insights import analyse as analyse_segment, transcript_slice
from nas_client import NASClient
from transcriber import LiveTranscriber


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keep the audio input open from service start so the dashboard level meter is always live.
    monitor = asyncio.create_task(continuous_recorder.monitor_input())
    yield
    monitor.cancel()
    await telefon.stoppe()   # auflegen, statt die Konferenz mit einem toten Teilnehmer zu belassen
    # Die WAV-Kopfzeile schreiben, BEVOR der Eingang zugeht. Ohne das steht beim
    # Entladen des Dienstes waehrend einer Aufnahme nframes=0 im Kopf — die Daten sind
    # da, aber jeder Player sieht eine leere Datei. Der SIGTERM-Handler weiter unten
    # kommt dafuer nicht in Frage: uvicorn setzt in capture_signals() einen eigenen
    # und ueberschreibt ihn, er laeuft also nie.
    continuous_recorder.emergency_close()
    continuous_recorder.close_input()


app = FastAPI(title="Akouo Audio-Dienst", version="2.0.0", lifespan=lifespan)
log = logging.getLogger(__name__)
continuous_recorder = ContinuousRecorder()  # Phase 4
stream_adapter = StreamAdapter()            # Phase 4
telefon = TelefonAdapter()                  # Stream-Mix in die Telefonkonferenz
auto_modus = AutoModus(continuous_recorder)  # erkennt Beiträge, schreibt aber nichts
nas_client = NASClient(config)              # Phase 7
transcriber = LiveTranscriber()             # live transcription
rueckspiel = Rueckspiel(continuous_recorder, config.blocksize, config.sample_rate)


@app.get("/healthz")
async def health():
    return {"ok": True}


# ─── Phase 4 session endpoints ─────────────────────────────────────────────

class SessionStartRequest(BaseModel):
    service_type: str = "Gottesdienst"
    output_dir: Optional[str] = None


class MarkerRequest(BaseModel):
    marker_type: str


class MarkerNudgeRequest(BaseModel):
    field: str  # "start" or "end"
    delta_seconds: float  # -1.0 or +1.0


@app.post("/api/session/start")
async def session_start(req: SessionStartRequest):
    result = continuous_recorder.start_session(
        service_type=req.service_type,
        output_dir=req.output_dir,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=409, detail=result["message"])
    transcriber.start(result["wav_path"])
    return result


@app.post("/api/session/stop")
async def session_stop():
    result = await continuous_recorder.stop_session()
    if result["status"] == "error":
        raise HTTPException(status_code=409, detail=result["message"])
    await transcriber.stop()
    # Die ganze Aufnahme als MP3 — im Hintergrund, der Stopp wartet nicht darauf.
    if result.get("wav_path"):
        asyncio.get_running_loop().run_in_executor(None, gesamt_mp3, result["wav_path"])
    # Livestream und Telefon laufen weiter: Nach dem letzten Lied wird oft noch gesprochen,
    # und wer zuhört, soll nicht mit dem Stopp der Aufnahme abgeschnitten werden. Beide
    # haben ihren eigenen Knopf. Früher wurden sie hier mitbeendet, weil Aufnahme und
    # Stream an einem gemeinsamen Eingang hingen — der bleibt seit September dauerhaft offen.
    return result


@app.get("/api/session/status")
async def session_status():
    # input_available rides along so the dashboard can tell whether monitoring is possible
    # at all — the input is open independently of any recording.
    return {**continuous_recorder.status(), "input_available": continuous_recorder.input_available()}


@app.post("/api/session/marker")
async def session_marker(req: MarkerRequest):
    result = continuous_recorder.place_marker(req.marker_type)
    if result["status"] == "error":
        # Distinguish "unknown type" (422) vs "not recording" (409)
        # Der Vergleich lief frueher gegen "Unknown marker type" — die Meldung heisst
        # seit der Umstellung auf die Datenbank "Unbekannte Beitragsart: …", der Zweig
        # war also tot und es kam 409 statt 422.
        if "Unbekannte Beitragsart" in result["message"]:
            raise HTTPException(status_code=422, detail=result["message"])
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@app.get("/api/session/offset")
async def session_offset():
    """Current WAV-relative position — used to stamp the end of a running track."""
    offset = continuous_recorder.current_offset()
    if offset is None:
        raise HTTPException(status_code=409, detail="Not recording")
    return {"offset_seconds": offset}


@app.get("/api/session/transcript")
async def session_transcript(since: int = 0):
    """Live transcript lines from `since` onwards (best effort — never blocks recording)."""
    return transcriber.since(since)


@app.get("/api/session/marker-types")
async def session_marker_types():
    """Was der Dienst gerade als gueltig kennt. Die Liste besitzt die API; hier steht
    nur, was sie geschoben hat."""
    return {"types": recorder_modul.BEITRAGSARTEN}


class ErkennungConfig(BaseModel):
    """Alles, was die Erkennung ueber die Beitragsarten wissen muss.

    Wird von der API nach jeder Aenderung geschoben. Der Audio-Dienst hat keinen
    Datenbankzugriff, und eine zweite Kopie der Listen im Code war genau die
    Fehlerquelle, die das hier ersetzt.
    """
    beitragsarten: Optional[list] = None
    musik_typen: Optional[list] = None
    sprach_typen: Optional[list] = None
    nur_auf_ansage: Optional[dict] = None
    kein_auto_marker: Optional[list] = None
    anker_typen: Optional[list] = None
    typische_dauer: Optional[dict] = None
    ansage_regeln: Optional[list] = None
    min_stille_s: Optional[float] = None
    min_beitrag_s: Optional[float] = None
    orchester_flachheit: Optional[float] = None
    stimmen_fuer_sprache: Optional[int] = None
    programm_automatisch: Optional[bool] = None
    # Export
    export_bitrate: Optional[str] = None
    export_peak_dbfs: Optional[float] = None
    export_fade_ms: Optional[int] = None
    export_normalisierung: Optional[str] = None
    export_lufs: Optional[float] = None     # alt: ein Ziel für beide Klassen
    export_lufs_sprache: Optional[float] = None
    export_lufs_musik: Optional[float] = None
    # Schnittkanten
    grenzen_suchfenster_s: Optional[float] = None
    grenzen_min_ton_ms: Optional[int] = None
    grenzen_min_stille_ms: Optional[int] = None
    grenzen_vorlauf_ms: Optional[int] = None
    grenzen_nachlauf_ms: Optional[int] = None
    grenzen_schwelle_db: Optional[float] = None


@app.put("/api/config/erkennung")
async def erkennung_setzen(cfg: ErkennungConfig):
    daten = cfg.model_dump(exclude_none=True)
    if "beitragsarten" in daten:
        recorder_modul.beitragsarten_setzen(daten["beitragsarten"])
        programm_modul.erlaubte_setzen(daten["beitragsarten"])
    if "anker_typen" in daten:
        automode_modul.anker_setzen(daten["anker_typen"])
    if "programm_automatisch" in daten:
        automode_modul.programm_automatisch_setzen(daten["programm_automatisch"])
    uebernommen = classifier.anwenden(daten)
    uebernommen.update(boundaries_modul.anwenden(daten))
    uebernommen.update(laufzeit.export_setzen(daten))
    return {"status": "ok", "uebernommen": uebernommen,
            "beitragsarten": len(recorder_modul.BEITRAGSARTEN)}


@app.get("/api/config/erkennung")
async def erkennung_lesen():
    stand = classifier.aktueller_stand()
    stand.update(boundaries_modul.aktueller_stand())
    stand["beitragsarten"] = recorder_modul.BEITRAGSARTEN
    stand["anker_typen"] = list(automode_modul.ANKER_TYPEN)
    stand["programm_automatisch"] = automode_modul.PROGRAMM_AUTOMATISCH
    stand["export_bitrate"] = laufzeit.EXPORT["bitrate"]
    stand["export_peak_dbfs"] = laufzeit.EXPORT["peak_dbfs"]
    stand["export_fade_ms"] = laufzeit.EXPORT["fade_ms"]
    stand["export_normalisierung"] = laufzeit.EXPORT["normalisierung"]
    stand["export_lufs_sprache"] = laufzeit.EXPORT["lufs_sprache"]
    stand["export_lufs_musik"] = laufzeit.EXPORT["lufs_musik"]
    return stand


@app.patch("/api/session/markers/{index}")
async def session_marker_nudge(index: int, req: MarkerNudgeRequest):
    result = continuous_recorder.nudge_marker(index, req.field, req.delta_seconds)
    if result["status"] == "error":
        msg = result["message"]
        if "Invalid field" in msg or "delta_seconds must be" in msg:
            raise HTTPException(status_code=422, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    return result


@app.get("/api/session/audio-level")
async def audio_level_stream():
    """SSE stream of RMS levels (~10/s) for browser-side level meter (UI-06).
    Emits {"rms": float in [0,1], "input": bool} every 100ms — live whenever the audio
    input is open, recording or not; input=false when no interface is connected."""
    async def generator():
        try:
            while True:
                rms = continuous_recorder.get_rms_level()
                yield {"data": json.dumps({"rms": rms, "input": continuous_recorder.input_available()})}
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return
    return EventSourceResponse(generator())


@app.get("/api/session/levels")
async def session_levels(
    from_seconds: float = 0.0,
    to_seconds: Optional[float] = None,
    block_ms: int = 250,
    wav_path: Optional[str] = None,
):
    """Level curve of a recording — the running one by default, any finished one via wav_path.

    The dashboard asks once for the whole session and then only for the tail since its last
    value, so a live curve costs one short read every couple of seconds. Reading is done on a
    worker thread: a full two-hour session is ~1 s of disk I/O and must not stall the level
    meter SSE or a marker call.
    """
    if wav_path is None:
        wav_path = continuous_recorder.status().get("wav_path")
        if wav_path is None:
            raise HTTPException(status_code=409, detail="Keine laufende Aufnahme")
    else:
        # Only ever read inside the recordings tree — the path comes from the API process.
        target = pathlib.Path(wav_path).resolve()
        if not target.is_relative_to(config.output_dir.resolve()):
            raise HTTPException(status_code=400, detail="Pfad liegt außerhalb des Aufnahmeordners")
        wav_path = str(target)

    if not pathlib.Path(wav_path).exists():
        raise HTTPException(status_code=404, detail=f"Aufnahme nicht gefunden: {wav_path}")

    levels, window_start, block_seconds = await asyncio.to_thread(
        level_envelope, wav_path, from_seconds, to_seconds, block_ms
    )
    return {
        "from_seconds": window_start,
        "block_seconds": block_seconds,
        "levels": levels,              # peak dBFS per block, clipped to [-60, 0]
        "floor_dbfs": -60,
    }


CLIP_MAX_S = 600.0      # zehn Minuten reichen zum Reinhören; mehr ist ein Export


def _wav_im_aufnahmeordner(wav_path: Optional[str]) -> str:
    """Die laufende Aufnahme oder eine fertige — immer nur im Aufnahmeordner."""
    if wav_path is None:
        wav_path = continuous_recorder.status().get("wav_path")
        if wav_path is None:
            raise HTTPException(status_code=409, detail="Keine laufende Aufnahme")
    target = pathlib.Path(wav_path).resolve()
    if not target.is_relative_to(config.output_dir.resolve()):
        raise HTTPException(status_code=400, detail="Pfad liegt außerhalb des Aufnahmeordners")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Aufnahme nicht gefunden: {target}")
    return str(target)


@app.get("/api/session/clip.mp3")
async def session_clip(von: float, bis: float, wav_path: Optional[str] = None):
    """Ein Stück der Aufnahme zum Reinhören — auch während sie läuft.

    Gelesen wird die Datei, nicht das Gerät: der eine CoreAudio-Stream bleibt unberührt. Die
    WAV-Kopfzeile wird bei jedem Schreiben nachgeführt, darum liest FFmpeg auch eine
    wachsende Datei bis zur aktuellen Stelle. Niedrige Priorität, damit Whisper vorgeht."""
    pfad = _wav_im_aufnahmeordner(wav_path)
    von = max(0.0, von)
    dauer = min(max(0.1, bis - von), CLIP_MAX_S)
    befehl = ["nice", "-n", "10", "ffmpeg", "-nostdin", "-loglevel", "error",
              "-ss", f"{von:.3f}", "-t", f"{dauer:.3f}", "-i", pfad,
              "-ac", "2", "-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"]
    proc = await asyncio.create_subprocess_exec(
        *befehl, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    async def strom():
        try:
            while True:
                stueck = await proc.stdout.read(32768)
                if not stueck:
                    break
                yield stueck
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            await proc.wait()
    return StreamingResponse(strom(), media_type="audio/mpeg")


class _MonitorHub:
    """One FFmpeg encoder feeding every listener.

    A per-listener encoder would mean several consumers on one queue, each getting a
    fraction of the chunks — so the hub encodes once and fans the MP3 out. The first
    listener starts it, the last one stops it; while nobody listens the recorder's
    callback does no monitoring work at all.
    """

    def __init__(self):
        self._process = None
        self._clients: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        async with self._lock:
            if self._process is None:
                await self._start()
            q: asyncio.Queue = asyncio.Queue(maxsize=64)
            self._clients.add(q)
            return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._lock:
            self._clients.discard(q)
            if not self._clients:
                await self._stop()

    async def _start(self):
        from recorder import _mon_q, monitor_enabled
        while not _mon_q.empty():          # drop whatever piled up before anyone listened
            try:
                _mon_q.get_nowait()
            except queue.Empty:
                break
        self._process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-f", "s16le", "-ar", str(config.sample_rate), "-ac", "2", "-i", "pipe:0",
            "-acodec", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # PIPE without a consumer deadlocks FFmpeg
        )
        monitor_enabled.set()
        self._tasks = [asyncio.create_task(self._feed()), asyncio.create_task(self._fan_out())]

    async def _stop(self):
        from recorder import monitor_enabled
        monitor_enabled.clear()
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        if self._process is not None:
            try:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass
            self._process = None

    async def _feed(self):
        """Move PCM from the recorder's thread queue into FFmpeg's stdin."""
        from recorder import _mon_q
        try:
            while self._process is not None:
                try:
                    chunk = _mon_q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.02)
                    continue
                self._process.stdin.write(chunk)
                await self._process.stdin.drain()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            return

    async def _fan_out(self):
        """Read encoded MP3 and hand it to every listener."""
        try:
            while self._process is not None:
                data = await self._process.stdout.read(4096)
                if not data:
                    return
                for q in list(self._clients):
                    if q.full():
                        continue    # a stalled listener must not hold up the others
                    q.put_nowait(data)
        except asyncio.CancelledError:
            return


monitor_hub = _MonitorHub()


@app.get("/api/session/monitor.mp3")
async def session_monitor():
    """Live MP3 of the stream bus — what the listeners get, without the Icecast detour.

    Plays whenever the input is open, so the operator can check the signal before sending.
    """
    if not continuous_recorder.input_available():
        raise HTTPException(status_code=409, detail="Kein Eingangssignal")

    q = await monitor_hub.subscribe()

    async def relay():
        try:
            while True:
                yield await q.get()
        finally:
            await monitor_hub.unsubscribe(q)

    return StreamingResponse(relay(), media_type="audio/mpeg")


@app.get("/api/session/automode")
async def automode_status():
    return auto_modus.status()


@app.post("/api/session/automode/start")
async def automode_start():
    """Wird ausdrücklich eingeschaltet — nie von selbst."""
    ergebnis = await auto_modus.start()
    if ergebnis.get("status") == "error":
        raise HTTPException(status_code=409, detail=ergebnis["message"])
    return ergebnis


@app.post("/api/session/automode/stop")
async def automode_stop():
    return await auto_modus.stop()


class NeuErkennenRequest(BaseModel):
    wav_path: str


@app.post("/api/session/neu-erkennen")
async def neu_erkennen(req: NeuErkennenRequest):
    """Eine fertige Aufnahme von vorn bis hinten neu erkennen (automode.nachlauf).

    Nur nach dem Stopp: an einer Datei, die noch wächst, gäbe es kein „hinterher",
    und die Rechenarbeit soll nicht neben einer laufenden Aufnahme stattfinden.
    """
    wav = pathlib.Path(req.wav_path)
    if not wav.is_file():
        raise HTTPException(status_code=404, detail="WAV not found")
    status = continuous_recorder.status()
    if status.get("state") in ("recording", "paused") and \
            pathlib.Path(status.get("wav_path") or "") == wav:
        raise HTTPException(status_code=409, detail="Aufnahme läuft noch")
    beitraege = await asyncio.to_thread(automode_modul.nachlauf, wav)
    return {"beitraege": beitraege}


class ErsterFreierRequest(BaseModel):
    kandidaten: list[str]


@app.post("/api/session/nas-erster-freier")
async def nas_erster_freier(req: ErsterFreierRequest):
    """Der erste Ordnername aus der Liste, der auf dem NAS noch nicht existiert.

    Fürs CopyCenter, das belegte Namen nicht mit "2. Teil" weiterzählt, sondern mit dem
    nächsten Buchstaben (_a, _b, _c). Die Reihenfolge bestimmt die API.
    """
    pfade = [k.replace("\\", "/").strip("/") for k in req.kandidaten]
    if not pfade or any(".." in pathlib.PurePosixPath(k).parts or not k for k in pfade):
        raise HTTPException(status_code=422, detail="kandidaten must be relative and contain no ..")
    if not config.nas_server:
        return {"folder": pfade[0], "taken": False}

    def frei() -> dict:
        for k in pfade:
            if not nas_client.exists(k):
                return {"folder": k, "taken": k != pfade[0]}
        raise HTTPException(status_code=409, detail="Kein freier Ordnername")

    return await asyncio.to_thread(frei)


@app.get("/api/session/nas-folder")
async def nas_free_folder(folder: str):
    """First free variant of a folder name on the NAS.

    The share holds an archive people filled by hand for years, and for several of our own
    recordings the folder is already there. Nothing in it may be touched, so a taken name
    gets a suffix — following the archive's own habit of marking a continuation as
    "… 2. Teil".
    """
    if not config.nas_server:
        return {"folder": folder, "taken": False}
    rel = folder.replace("\\", "/").strip("/")
    if ".." in pathlib.PurePosixPath(rel).parts:
        raise HTTPException(status_code=422, detail="folder must not contain ..")

    def frei() -> dict:
        kandidat = rel
        for n in range(2, 12):
            if not nas_client.exists(kandidat):
                return {"folder": kandidat, "taken": kandidat != rel}
            kandidat = f"{rel} {n}. Teil"
        # Rather refuse than write into a folder we could not prove to be free.
        raise HTTPException(status_code=409, detail=f"Kein freier Ordnername für '{rel}'")

    return await asyncio.to_thread(frei)


@app.post("/api/session/pause")
async def session_pause():
    result = continuous_recorder.pause()
    if result["status"] == "error":
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@app.post("/api/session/resume")
async def session_resume():
    result = continuous_recorder.resume()
    if result["status"] == "error":
        raise HTTPException(status_code=409, detail=result["message"])
    return result


# ─── Rückspiel über den Dante-Ausgang ──────────────────────────────────────
# Nur mit AUDIO_OUTPUT_CHANNELS in der .env; sonst 409. Die Meldungen sind feste
# deutsche Sätze, die die API übersetzt.

class RueckspielRequest(BaseModel):
    wav_path: str
    from_s: float
    to_s: float


@app.post("/api/session/rueckspiel")
async def session_rueckspiel(req: RueckspielRequest):
    if not config.output_channels:
        raise HTTPException(status_code=409, detail="Rückspiel ist nicht eingerichtet")
    # Nur innerhalb des Aufnahmeordners — der Pfad kommt aus einem anderen Prozess.
    wav = pathlib.Path(req.wav_path).resolve()
    if not wav.is_relative_to(config.output_dir.resolve()):
        raise HTTPException(status_code=400, detail="Pfad liegt außerhalb des Aufnahmeordners")
    try:
        return await asyncio.to_thread(rueckspiel.starten, wav, req.from_s, req.to_s)
    except RueckspielFehler as e:
        raise HTTPException(status_code=e.status, detail=e.nachricht)


@app.post("/api/session/rueckspiel/stop")
async def session_rueckspiel_stop():
    if not config.output_channels:
        raise HTTPException(status_code=409, detail="Rückspiel ist nicht eingerichtet")
    return await asyncio.to_thread(rueckspiel.stoppen)


@app.get("/api/session/rueckspiel")
async def session_rueckspiel_status():
    return {"eingerichtet": bool(config.output_channels), **rueckspiel.status()}


class ExportRequest(BaseModel):
    wav_path: str
    mp3_path: Optional[str] = None


class SegmentExportRequest(BaseModel):
    wav_path: str
    start_seconds: float
    end_seconds: float
    mp3_filename: str        # basename only, e.g. "2026-04-29_Predigt.mp3"
    remote_subpath: Optional[str] = None  # e.g. "Gottesdienste/2026-10-04 Sonntag 1. morgens/Track-03.mp3"
    allow_overwrite: bool = False          # only set when replacing our own earlier upload
    tags: Optional[dict] = None            # ID3: title, artist, album, track, comment, …
    # Zweite Ablage für das CopyCenter: derselbe Track noch einmal, plus eine leere
    # Textdatei, deren Name die Beschriftung ist ("Gottesdienst.txt").
    copycenter_folder: Optional[str] = None
    copycenter_label: Optional[str] = None
    # Gemeinde-App: der fertige Eintrag aus api/online.py; ohne ihn bleibt es beim Archiv.
    online: Optional[dict] = None
    online_ersetzen: bool = False
    # Stücke mitten im Track, die nicht dazugehören ([[von, bis], …], absolute Sekunden)
    luecken: list[list[float]] = []
    # Für die Ziellautheit: Klangklasse der Beitragsart ("musik"/"sprache"/None) und ihr
    # Name — ohne Klasse entscheidet der Name, siehe laufzeit.klasse_fuer().
    klang_klasse: Optional[str] = None
    marker_type: Optional[str] = None


@app.post("/api/session/export")
async def session_export(req: ExportRequest):
    """Export a session WAV to broadcast-quality MP3 (192kbit/s, 48kHz, -3dBFS peak, 1s fade).

    Runs export_wav_to_mp3 in a thread to avoid blocking the event loop during
    CPU-bound pydub/FFmpeg processing (T-05-10: DoS via blocked event loop).
    """
    wav = pathlib.Path(req.wav_path)
    if not wav.exists():
        raise HTTPException(status_code=404, detail=f"WAV not found: {wav}")
    mp3 = pathlib.Path(req.mp3_path) if req.mp3_path else wav.with_suffix(".mp3")
    # CPU-bound pydub call → run in thread to avoid blocking event loop (T-05-10)
    result = await asyncio.to_thread(
        export_wav_to_mp3, wav, mp3,
        # Dieselben Werte wie beim Segment-Export. Ohne sie nahm der Gesamt-Export die
        # Signatur-Vorgaben, und wer die Bitrate auf der Einstellungsseite aenderte, sah
        # die Aenderung nur bei Segmenten. Benannt uebergeben: zwischen bitrate und
        # fade_ms steht target_sample_rate, positionell landete der Pegel dort.
        bitrate=laufzeit.EXPORT["bitrate"],
        peak_dbfs=laufzeit.EXPORT["peak_dbfs"],
        fade_ms=laufzeit.EXPORT["fade_ms"],
        # Ein ganzer Gottesdienst ist überwiegend gesprochen — also das Sprachziel.
        # Ohne diese Zeile blieb der Gesamt-Export bei der Spitze, auch bei „Lautheit“.
        lufs=laufzeit.ziel_lufs("sprache"),
    )
    return result


@app.post("/api/session/segment-export")
async def session_segment_export(req: SegmentExportRequest):
    """Slice a session WAV between two seconds and export to MP3 + NAS (EXP-01, EXP-02).

    The CPU-bound pydub slice + FFmpeg encode runs in asyncio.to_thread (T-05-10).
    NAS upload also runs in to_thread because smbprotocol is synchronous.
    NAS failures never block local export — caller sees nas_status='failed' but
    keeps the local mp3.
    """
    wav = pathlib.Path(req.wav_path)
    if not wav.exists():
        raise HTTPException(status_code=404, detail=f"WAV not found: {wav}")

    # Sanitise filename — basename only, no path traversal (T-07-06).
    safe_name = pathlib.PurePosixPath(req.mp3_filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=422, detail="mp3_filename must be a non-empty basename")
    if not safe_name.lower().endswith(".mp3"):
        raise HTTPException(status_code=422, detail="mp3_filename must end with .mp3")

    # Exports live inside the session folder; old flat recordings keep the shared folder.
    target_dir = export_dir(wav)
    target_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = target_dir / safe_name

    try:
        export_result = await asyncio.to_thread(
            export_segment,
            wav,
            req.start_seconds,
            req.end_seconds,
            mp3_path,
            bitrate=laufzeit.EXPORT["bitrate"],
            fade_ms=laufzeit.EXPORT["fade_ms"],
            peak_dbfs=laufzeit.EXPORT["peak_dbfs"],
            lufs=laufzeit.ziel_lufs(req.klang_klasse, req.marker_type),
            tags=req.tags,
            cover=config.export_cover or None,
            luecken=req.luecken,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # NAS upload — best effort, never breaks the response (T-07-10).
    if req.remote_subpath and config.nas_server:
        # Sanitise remote subpath: strip leading slashes/backslashes, reject .. (T-07-07).
        sanitised = req.remote_subpath.replace("\\", "/").lstrip("/")
        if ".." in pathlib.PurePosixPath(sanitised).parts:
            nas_result = {
                "status": "failed",
                "error": "remote_subpath must not contain ..",
                "nas_path": None,
                "uploaded_at": None,
            }
        else:
            nas_result = await asyncio.to_thread(
                nas_client.upload_file, mp3_path, sanitised, req.allow_overwrite
            )
    else:
        nas_result = {
            "status": "skipped",
            "error": None,
            "nas_path": None,
            "uploaded_at": None,
        }

    copycenter_result = None
    if req.copycenter_folder and nas_result["status"] == "uploaded":
        copycenter_result = await asyncio.to_thread(
            _copycenter_ablegen, mp3_path, req.copycenter_folder,
            pathlib.PurePosixPath(sanitised).name, req.copycenter_label, req.allow_overwrite)

    # Gemeinde-App — unabhängig vom NAS: wer online stellen will, soll es auch dann,
    # wenn das NAS gerade nicht erreichbar ist.
    online_result = None
    if req.online and req.online.get("recording", {}).get("file_key"):
        online_result = await asyncio.to_thread(
            online_modul.stelle_online, mp3_path, req.online, wav, req.start_seconds,
            req.end_seconds, export_result.get("duration_ms"), req.online_ersetzen)

    # Serialise datetime for JSON
    for ergebnis in (nas_result, copycenter_result):
        if ergebnis and ergebnis.get("uploaded_at") is not None:
            ergebnis["uploaded_at"] = ergebnis["uploaded_at"].isoformat()

    return {
        "export": export_result,
        "nas": nas_result,
        "copycenter": copycenter_result,
        "online": online_result,
        "mp3_filename": safe_name,
        "mp3_path": str(mp3_path),
    }


def _copycenter_ablegen(mp3_path: pathlib.Path, ordner: str, dateiname: str,
                       beschriftung: Optional[str], allow_overwrite: bool) -> dict:
    """Denselben Track ein zweites Mal ins CopyCenter legen.

    Nur nach einem erfolgreichen Haupt-Upload — sonst läge im CopyCenter, was im Archiv
    fehlt. Die Beschriftung ist eine leere Textdatei; existiert sie schon, bleibt sie.
    """
    ordner = ordner.replace("\\", "/").strip("/")
    if ".." in pathlib.PurePosixPath(ordner).parts or not ordner:
        return {"status": "failed", "error": "copycenter_folder must be relative and contain no ..",
                "nas_path": None, "uploaded_at": None}
    ergebnis = nas_client.upload_file(mp3_path, f"{ordner}/{dateiname}", allow_overwrite)
    if ergebnis["status"] == "uploaded" and beschriftung:
        txt = f"{ordner}/{beschriftung}.txt"
        if not nas_client.exists(txt):
            with tempfile.TemporaryDirectory() as tmp:
                leer = pathlib.Path(tmp) / "leer.txt"
                leer.touch()
                nas_client.upload_file(leer, txt)
    return ergebnis


# ─── Phase 7 EXP-03: NAS-push helper for api/ uploads router ──────────────


class NasPushRequest(BaseModel):
    local_path: str
    remote_subpath: str


class RefineRequest(BaseModel):
    wav_path: str
    start_seconds: float
    end_seconds: float


@app.post("/api/session/refine-boundaries")
async def session_refine_boundaries(req: RefineRequest):
    """Corrected cut points for one segment — finds the real silence-to-sound edges."""
    wav = pathlib.Path(req.wav_path)
    if not wav.exists():
        raise HTTPException(status_code=404, detail=f"WAV not found: {wav}")
    return await asyncio.to_thread(refine_boundaries, wav, req.start_seconds, req.end_seconds)


class InsightsRequest(BaseModel):
    wav_path: str
    start_seconds: float
    end_seconds: float
    marker_type: str = "Predigt"
    luecken: list[list[float]] = []


@app.post("/api/session/insights")
async def session_insights(req: InsightsRequest):
    """Title, summary, Bible references and keywords for one segment (AI-02)."""
    transcript = await asyncio.to_thread(
        transcript_slice, req.wav_path, req.start_seconds, req.end_seconds, req.luecken
    )
    return await analyse_segment(transcript, req.marker_type)


@app.post("/api/uploads/nas-push")
async def uploads_nas_push(req: NasPushRequest):
    """Push a local file to NAS at remote_subpath under nas_base_path (EXP-03 helper).

    Used by the api/ uploads router so it doesn't need its own SMB session.
    Both arguments must be path-traversal-safe; this endpoint enforces that
    locally as defence in depth (api/ already validates).
    """
    p = pathlib.Path(req.local_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"local_path not found: {p}")
    sub = req.remote_subpath.replace("\\", "/").lstrip("/")
    if ".." in pathlib.PurePosixPath(sub).parts or not sub:
        raise HTTPException(status_code=422, detail="remote_subpath must be relative and contain no ..")
    if not config.nas_server:
        return {"status": "skipped", "error": "NAS not configured", "nas_path": None, "uploaded_at": None}
    result = await asyncio.to_thread(nas_client.upload_file, p, sub)
    if result.get("uploaded_at") is not None:
        result["uploaded_at"] = result["uploaded_at"].isoformat()
    return result


# ─── Phase 4 streaming endpoints (replaces streaming-service :8002) ────────

class StreamStartRequest(BaseModel):
    stream_target: Optional[str] = None


@app.post("/api/streaming/start")
async def streaming_start(req: StreamStartRequest):
    """Ein Knopf für beides: Livestream und Telefon-Übertragung.

    Beide laufen unabhängig. Fällt einer aus, läuft der andere trotzdem — ein Fehler
    kommt nur zurück, wenn gar nichts gestartet ist."""
    target = req.stream_target or config.stream_url
    telefon_laeuft = telefon.starte()
    if not target:
        if telefon_laeuft:
            return {"status": "telefon", "telefon": telefon.status()}
        raise HTTPException(status_code=400, detail="No stream target configured (set STREAM_URL env var)")
    result = await stream_adapter.start(stream_target=target)
    if result["status"] == "error":
        if telefon_laeuft:
            return {"status": "telefon", "stream_fehler": result["message"], "telefon": telefon.status()}
        raise HTTPException(status_code=409, detail=result["message"])
    return {**result, "telefon": telefon.status()}


@app.post("/api/streaming/stop")
async def streaming_stop():
    telefon_lief = await telefon.stoppe()
    result = await stream_adapter.stop()
    if result["status"] == "error":
        if telefon_lief:
            return {"status": "stopped", "telefon": telefon.status()}
        raise HTTPException(status_code=409, detail=result["message"])
    return {**result, "telefon": telefon.status()}


@app.get("/api/streaming/status")
async def streaming_status():
    """Adapter state plus what it would send to — credentials stripped."""
    return {
        **stream_adapter.status(),
        "target": public_target(config.stream_url),
        "configured": bool(config.stream_url),
        "channels": config.stream_channels,
        "telefon": telefon.status(),
    }


# ─── Vermeldungen: lesen, hochladen, zurücknehmen ──────────────────────────

class VermeldungenLesen(BaseModel):
    wav_path: str
    bereiche: list            # [[von_s, bis_s], …] — die Vermeldungen-Tracks
    datum: str                # Tag des Gottesdienstes, ISO


@app.post("/api/session/vermeldungen")
async def vermeldungen_lesen(req: VermeldungenLesen):
    from datetime import date as _date
    pfad = pathlib.Path(req.wav_path).parent / "transcript.jsonl"
    zeilen = []
    if pfad.exists():
        for roh in pfad.read_text(encoding="utf-8").splitlines():
            try:
                z = json.loads(roh)
            except ValueError:
                continue
            if any(von <= z.get("start", 0) < bis for von, bis in req.bereiche):
                zeilen.append(z.get("text", "").strip())
    text = " ".join(t for t in zeilen if t)
    eintraege = await asyncio.to_thread(vermeldungen_modul.lies, text, _date.fromisoformat(req.datum))
    if eintraege is None:
        raise HTTPException(status_code=503, detail="Sprachmodell nicht erreichbar")
    # Welche Termine stehen an diesen Tagen schon im Kalender? Damit geändert statt verdoppelt wird.
    kalender = online_modul.eingerichtet()
    if kalender:
        def nachschlagen() -> list:
            with _app_client() as client:
                return vermeldungen_modul.kandidaten_anhaengen(eintraege, client)
        try:
            eintraege = await asyncio.to_thread(nachschlagen)
        except (httpx.HTTPError, RuntimeError) as e:
            log.warning("Vermeldungen: Kalender nicht lesbar (%s)", e)
            kalender = False
    return {"eintraege": eintraege, "kalender": kalender, "transkript_woerter": len(text.split())}


@app.get("/api/online/kalender")
async def kalender_heute(datum: str):
    """Die Gemeindetermine eines Tages — für den Vorschlag beim Start."""
    if not online_modul.eingerichtet():
        raise HTTPException(status_code=409, detail="Gemeinde-App nicht eingerichtet")

    def los() -> list:
        with _app_client() as client:
            return vermeldungen_modul.kandidaten(client, datum)
    try:
        return {"termine": await asyncio.to_thread(los)}
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=f"Gemeinde-App: {e}"[:200])


class VermeldungenHochladen(BaseModel):
    eintraege: list
    datum: str


@contextlib.contextmanager
def _app_client():
    """Angemeldeter Client für die Gemeinde-App. Erst öffnen, dann anmelden — ein httpx-Client
    lässt sich nach der ersten Anfrage nicht noch einmal mit `with` öffnen."""
    with httpx.Client(base_url=config.online_api_url.rstrip("/"), timeout=60.0) as client:
        online_modul._anmelden(client)
        yield client


@app.post("/api/online/vermeldungen")
async def vermeldungen_hochladen(req: VermeldungenHochladen):
    from datetime import date as _date
    if not online_modul.eingerichtet():
        raise HTTPException(status_code=409, detail="Gemeinde-App nicht eingerichtet")

    def los() -> list:
        with _app_client() as client:
            return vermeldungen_modul.hochladen(req.eintraege, _date.fromisoformat(req.datum), client)
    try:
        return {"ergebnis": await asyncio.to_thread(los)}
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=f"Gemeinde-App: {e}"[:200])


class VermeldungenZurueck(BaseModel):
    ergebnis: list


@app.post("/api/online/vermeldungen/zuruecknehmen")
async def vermeldungen_zuruecknehmen(req: VermeldungenZurueck):
    if not online_modul.eingerichtet():
        raise HTTPException(status_code=409, detail="Gemeinde-App nicht eingerichtet")

    def los() -> list:
        with _app_client() as client:
            return vermeldungen_modul.zuruecknehmen(req.ergebnis, client)
    try:
        return {"ergebnis": await asyncio.to_thread(los)}
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=f"Gemeinde-App: {e}"[:200])


# ─── Der Dienst über sich selbst: Status, .env, Neustart ───────────────────

def _was_laeuft() -> list[str]:
    """Was ein Neustart beenden würde."""
    laeuft = []
    if continuous_recorder.status().get("state") in ("recording", "paused"):
        laeuft.append("aufnahme")
    if stream_adapter.status().get("status") == "streaming":
        laeuft.append("stream")
    if telefon.aktiv():
        laeuft.append("telefon")
    return laeuft


@app.get("/api/dienst/status")
async def dienst_status():
    pruefungen = await asyncio.to_thread(
        dienst.status, config, continuous_recorder.input_available(), None)
    return {"pruefungen": pruefungen, "laeuft": _was_laeuft()}


@app.get("/api/dienst/umgebung")
async def dienst_umgebung_lesen():
    return {"werte": dienst.lies_umgebung(), "laeuft": _was_laeuft()}


class UmgebungRequest(BaseModel):
    werte: dict


@app.put("/api/dienst/umgebung")
async def dienst_umgebung_schreiben(req: UmgebungRequest):
    """Schreibt die .env. Wirkt erst nach dem Neustart — schreiben darf man jederzeit."""
    fehler = dienst.pruefe(req.werte)
    if fehler:
        raise HTTPException(status_code=422, detail="; ".join(fehler))
    await asyncio.to_thread(dienst.schreibe_umgebung, req.werte)
    return {"werte": dienst.lies_umgebung(), "laeuft": _was_laeuft()}


@app.post("/api/dienst/neustart")
async def dienst_neustart():
    """Beendet den Dienst sauber; launchd (KeepAlive) startet ihn mit frischer .env neu."""
    laeuft = _was_laeuft()
    if laeuft:
        raise HTTPException(status_code=409, detail="Neustart gesperrt: " + ", ".join(laeuft) + " läuft")
    # Erst antworten, dann gehen — sonst sieht der Aufrufer nur eine abgerissene Verbindung
    asyncio.get_running_loop().call_later(0.5, os.kill, os.getpid(), _signal.SIGTERM)
    return {"status": "neustart"}


# ─── SIGTERM — Rueckfall fuer den Fall, dass die Lifespan nicht laeuft ─────
# Beim Start unter uvicorn ueberschreibt dessen capture_signals() diesen Handler, und
# das ist in Ordnung: Dann erledigt die Lifespan oben dieselbe Arbeit. Der Handler
# bleibt fuer den Fall, dass main.py anders gestartet wird.
def _on_sigterm(signum, frame):
    continuous_recorder.emergency_close()


_signal.signal(_signal.SIGTERM, _on_sigterm)


if __name__ == "__main__":
    import uvicorn
    # timeout_graceful_shutdown ist nicht optional: Der Dienst haelt mit der
    # Pegelanzeige (SSE) und dem Mithoerweg Dauerverbindungen offen. Ohne Zeitlimit
    # wartet uvicorn beim Beenden darauf, bis launchd nach rund 20 s SIGKILL schickt —
    # und dann laeuft auch die Lifespan nicht mehr zu Ende. Die API hat dasselbe
    # Zeitlimit, siehe autostart/run-api.sh.template.
    uvicorn.run(app, host="127.0.0.1", port=config.api_port, log_level="info",
                timeout_graceful_shutdown=3)
