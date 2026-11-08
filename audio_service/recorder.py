import asyncio
import io
import pathlib
import queue
import threading
import wave
from datetime import datetime
from time import monotonic
from typing import Optional

import numpy as np
import sounddevice as sd
from pydub import AudioSegment

from config import config, SESSION_WAV_NAME


# Gueltige Beitragsarten. Diese Liste stand hier UND in api/service_types.py, und die
# beiden wichen bereits voneinander ab: der Audio-Dienst kannte "Unbenannt", die API
# nicht. Seit Migration 014 ist die Datenbank die Quelle, und die API ist ihre
# Besitzerin — sie prueft eingehende Typen (routers/sessions.py).
#
# Hier bleibt nur noch ein Startwert fuer den Fall, dass der Dienst vor der API laeuft.
# Die API schiebt den Stand per PUT /api/config/erkennung herein.
BEITRAGSARTEN: list = []


def beitragsarten_setzen(namen) -> None:
    """Gueltige Beitragsarten uebernehmen. Leere Liste heisst: nicht pruefen."""
    global BEITRAGSARTEN
    BEITRAGSARTEN = list(namen or [])

# Module-level threading queues — single producer (PortAudio realtime thread),
# single consumer (asyncio drain loop). NOT asyncio.Queue — not thread-safe from callback.
_rec_q: queue.Queue = queue.Queue()
_str_q: queue.Queue = queue.Queue()

# Set by StreamAdapter while FFmpeg consumes _str_q. The input stream stays open for the
# level meter, so without this gate _str_q would grow unbounded when nobody is streaming.
stream_enabled = threading.Event()

# Monitoring (listening in on the stream bus from a browser) gets its own queue on purpose:
# _str_q has exactly one consumer, so sharing it would hand every second chunk to the wrong
# reader and tear the Icecast stream apart.
_mon_q: queue.Queue = queue.Queue()
monitor_enabled = threading.Event()

# Die Telefon-Übertragung bekommt aus demselben Grund eine eigene Queue: sie hört den
# Stream-Bus mit, aber jeder Verbraucher muss jeden Block bekommen. Gesetzt von telefon.py.
_tel_q: queue.Queue = queue.Queue()
telefon_enabled = threading.Event()

# Rückspiel: Blöcke float32 (Frames × Ausgangskanäle), die der Callback in den Ausgang
# legt. Begrenzt, damit ein Zuführer nie mehr als einige Sekunden vorausliest; der
# Zuführer wartet mit put(timeout=…) — im Callback wird nur get_nowait() aufgerufen.
RUECKSPIEL_MAX_BLOECKE = 64

METER_STALE_SECONDS = 0.5   # no callback for this long → meter reads 0.0
INPUT_LOST_SECONDS = 3.0    # no callback for this long → close stream and rescan devices
INPUT_POLL_SECONDS = 3.0


class ContinuousRecorder:
    def __init__(self):
        self._stream: Optional[sd.InputStream | sd.Stream] = None
        self._device: Optional[int] = None
        self._wav_file: Optional[wave.Wave_write] = None
        self._wav_path: Optional[pathlib.Path] = None
        self._service_type: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self._paused: bool = False
        self._pause_started: Optional[datetime] = None
        self._markers: list = []
        self._gaps: list = []
        self._drain_task: Optional[asyncio.Task] = None
        self._is_recording: bool = False
        self._last_rms: float = 0.0
        self._last_callback_at: float = 0.0
        self._rec_idx: list = []   # 0-based column indices into indata
        self._str_idx: list = []
        self._out_idx: list = []
        # Rückspiel. _out_rest gehört allein dem Callback-Thread; die anderen Threads
        # reden mit ihm nur über die Queue und das Event.
        self._out_q: queue.Queue = queue.Queue(maxsize=RUECKSPIEL_MAX_BLOECKE)
        self._out_rest: Optional[np.ndarray] = None
        self._out_verwerfen = threading.Event()
        self._duplex: bool = False
        self._refresh_channel_map()

    def _refresh_channel_map(self):
        self._rec_idx = [c - 1 for c in config.record_channels]
        self._str_idx = [c - 1 for c in config.stream_channels]
        self._out_idx = [c - 1 for c in config.output_channels]

    def _find_input_device(self) -> Optional[int]:
        """Index of the first device from config.device_names with enough input channels."""
        devices = list(enumerate(sd.query_devices()))
        for wanted in config.device_names:
            for i, dev in devices:
                if wanted in dev["name"] and dev["max_input_channels"] >= config.continuous_channels:
                    return i
        return None

    def _output_reicht(self, device: int) -> bool:
        """Hat das Eingangsgerät genug Ausgangskanäle für das Rückspiel?"""
        try:
            return sd.query_devices(device).get("max_output_channels", 0) >= config.continuous_output_channels
        except (sd.PortAudioError, ValueError, AttributeError):
            return False

    def _generate_wav_path(self, service_type: str) -> pathlib.Path:
        """Create this session's own folder and return the WAV path inside it.

        The folder name carries date, service type and the daily counter — what the WAV
        file name used to carry. Transcripts and segment exports land next to the WAV.
        Old flat recordings from before this layout still count towards the counter, so a
        number is never handed out twice on the same day.
        """
        now = datetime.now()
        config.sessions_dir.mkdir(parents=True, exist_ok=True)
        date_str = now.strftime("%Y-%m-%d")
        prefix = f"{date_str}_{service_type}_"
        existing = [
            p for p in config.sessions_dir.glob(f"{prefix}*")
            if p.is_dir() or p.suffix.lower() == ".wav"
        ]
        seq = len(existing) + 1
        while (config.sessions_dir / f"{prefix}{seq:03d}").exists():
            seq += 1   # gaps from deleted sessions must never overwrite an existing folder
        session_dir = config.sessions_dir / f"{prefix}{seq:03d}"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / SESSION_WAV_NAME

    # ─── Input stream — open whenever an audio interface is present ─────────

    def ensure_input(self) -> bool:
        """Open the single 4-channel InputStream if an input device is present. Idempotent."""
        if self._stream is not None:
            return True
        device = self._find_input_device()
        if device is None:
            return False
        self._refresh_channel_map()
        # Duplex nur, wenn eingerichtet UND das Gerät die Ausgänge hat. Sonst lieber ohne
        # Rückspiel aufnehmen als gar nicht. Die Art des Streams folgt allein der .env und
        # ändert sich damit nie während einer Aufnahme, nur beim Dienst-Neustart.
        duplex = bool(config.output_channels)
        if duplex and not self._output_reicht(device):
            print(f"[input] Rückspiel: Gerät hat keine {config.continuous_output_channels} "
                  f"Ausgangskanäle — nur Eingang", flush=True)
            duplex = False
        try:
            if duplex:
                stream = sd.Stream(
                    samplerate=config.sample_rate,
                    channels=(config.continuous_channels, config.continuous_output_channels),
                    dtype=config.continuous_dtype,
                    device=(device, device),
                    blocksize=config.blocksize,
                    callback=self._duplex_callback,
                )
            else:
                stream = sd.InputStream(
                    samplerate=config.sample_rate,
                    channels=config.continuous_channels,
                    dtype=config.continuous_dtype,
                    device=device,
                    blocksize=config.blocksize,
                    callback=self._audio_callback,
                )
            stream.start()
        except Exception as exc:  # e.g. PortAudioError when microphone access is denied
            print(f"[input] could not open input device: {exc}", flush=True)
            return False
        self._stream = stream
        self._device = device
        self._duplex = duplex
        self._last_callback_at = monotonic()
        return True

    def close_input(self):
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        self._duplex = False
        self.rueckspiel_stoppen()
        self._last_rms = 0.0

    def input_available(self) -> bool:
        """True while the input is open and PortAudio is delivering callbacks."""
        return self._stream is not None and monotonic() - self._last_callback_at < 2 * METER_STALE_SECONDS

    async def monitor_input(self):
        """Background task: keep the input open for the level meter; recover after unplug/replug."""
        while True:
            try:
                if self._stream is not None and monotonic() - self._last_callback_at > INPUT_LOST_SECONDS:
                    print("[input] no audio callbacks — closing stream and rescanning", flush=True)
                    self.close_input()
                if self._stream is None:
                    # PortAudio caches the device list at init; re-init to see hot-plugged interfaces.
                    sd._terminate()
                    sd._initialize()
                    if self.ensure_input():
                        print(
                            f"[input] input open: {sd.query_devices(self._device)['name']} "
                            f"({config.continuous_channels} Kanäle, Aufnahme {config.record_channels}, "
                            f"Stream {config.stream_channels}"
                            + (f", Rückspiel {config.output_channels}" if self._duplex else "")
                            + ")",
                            flush=True,
                        )
            except Exception as exc:
                print(f"[input] monitor error: {exc}", flush=True)
            await asyncio.sleep(INPUT_POLL_SECONDS)

    def _audio_callback(self, indata: np.ndarray, frames: int, time, status):
        """PortAudio realtime thread — NO asyncio calls, only cheap NumPy work and queue puts.

        Runs whenever the input is open: the meter is always fed, WAV chunks are queued only
        while recording (not paused), stream chunks only while StreamAdapter consumes them.
        """
        if indata.shape[1] < config.continuous_channels:
            return  # stream opened with fewer channels than configured — nothing to slice
        clipped = np.clip(indata, -1.0, 1.0)
        rec = clipped[:, self._rec_idx]
        self._last_rms = min(1.0, float(np.sqrt(np.mean(np.square(rec)))))
        self._last_callback_at = monotonic()
        if self._is_recording and not self._paused:
            _rec_q.put_nowait((rec * 32767).astype(np.int16).tobytes())
        if stream_enabled.is_set() or monitor_enabled.is_set() or telefon_enabled.is_set():
            stream_pcm = (clipped[:, self._str_idx] * 32767).astype(np.int16).tobytes()
            if stream_enabled.is_set():
                _str_q.put_nowait(stream_pcm)
            if monitor_enabled.is_set():
                _mon_q.put_nowait(stream_pcm)
            if telefon_enabled.is_set():
                _tel_q.put_nowait(stream_pcm)

    def _duplex_callback(self, indata: np.ndarray, outdata: np.ndarray, frames: int, time, status):
        """PortAudio-Echtzeit-Thread im Duplex-Betrieb: Eingang wie immer, dazu den Ausgang
        füllen. Dieselben Regeln: nur NumPy und get_nowait(), kein I/O, kein Logging."""
        self._audio_callback(indata, frames, time, status)
        self._ausgang_fuellen(outdata, frames)

    def _ausgang_fuellen(self, outdata: np.ndarray, frames: int) -> None:
        """Rückspiel-Blöcke in die konfigurierten Ausgangskanäle legen, Rest Stille."""
        outdata.fill(0)
        if self._out_verwerfen.is_set():
            # Stopp: angefangenen Block wegwerfen, diesen Durchgang still bleiben. Die
            # Queue leert rueckspiel_stoppen() selbst, nicht der Callback.
            self._out_rest = None
            self._out_verwerfen.clear()
            return
        pos = 0
        rest = self._out_rest
        while pos < frames:
            if rest is None or len(rest) == 0:
                try:
                    rest = self._out_q.get_nowait()
                except queue.Empty:
                    rest = None
                    break
            n = min(frames - pos, len(rest))
            outdata[pos:pos + n, self._out_idx] = rest[:n]
            rest = rest[n:]
            pos += n
        self._out_rest = rest

    # ─── Rückspiel ──────────────────────────────────────────────────────────

    def rueckspiel_bereit(self) -> bool:
        """True, wenn ein Duplex-Stream offen ist und Blöcke angenommen werden."""
        return self._stream is not None and self._duplex

    @property
    def rueckspiel_kanaele(self) -> int:
        return len(self._out_idx)

    def rueckspiel_einreihen(self, pcm: np.ndarray, timeout: float = 1.0) -> bool:
        """PCM (float32, Frames × Ausgangskanäle, Abtastrate des Streams) zum Abspielen
        einreihen. Blockiert höchstens `timeout`, wenn der Puffer voll ist — also nie aus
        dem Callback oder der Event-Loop aufrufen, nur aus einem Zuführer-Thread.
        False heißt: kein Platz (oder kein Ausgang); der Zuführer versucht es erneut."""
        if not self.rueckspiel_bereit():
            return False
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.ndim == 1:
            pcm = pcm[:, None]
        if pcm.shape[1] != len(self._out_idx):
            raise ValueError(f"erwartet {len(self._out_idx)} Kanäle, bekommen {pcm.shape[1]}")
        try:
            self._out_q.put(np.clip(pcm, -1.0, 1.0), timeout=timeout)
        except queue.Full:
            return False
        return True

    def rueckspiel_stoppen(self) -> None:
        """Puffer leeren und den angefangenen Block verwerfen — ab dem nächsten Callback
        ist der Ausgang still."""
        while True:
            try:
                self._out_q.get_nowait()
            except queue.Empty:
                break
        self._out_verwerfen.set()

    def rueckspiel_puffer_s(self) -> float:
        """Grob, wie viel noch im Puffer liegt — für die Statusanzeige."""
        return self._out_q.qsize() * config.blocksize / config.sample_rate

    async def _drain_rec_queue(self):
        """Async consumer — safe to call wave.writeframes here."""
        try:
            while self._is_recording:
                drained = 0
                while drained < 100:  # cap per iteration to keep loop responsive
                    try:
                        chunk = _rec_q.get_nowait()
                    except queue.Empty:
                        break
                    if self._wav_file is not None and not self._paused:
                        self._wav_file.writeframes(chunk)
                    drained += 1
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    # ─── Session ────────────────────────────────────────────────────────────

    def start_session(self, service_type: str, output_dir: Optional[str] = None) -> dict:
        if self._is_recording:
            return {"status": "error", "message": "Session already active"}
        if not self.ensure_input():
            return {
                "status": "error",
                "message": f"Kein Audio-Eingang mit >={config.continuous_channels} Kanälen gefunden (gesucht: {', '.join(config.device_names)})",
            }

        self._wav_path = self._generate_wav_path(service_type)
        self._wav_file = wave.open(str(self._wav_path), "wb")
        self._wav_file.setnchannels(len(config.record_channels))  # stream pair goes to FFmpeg instead
        self._wav_file.setsampwidth(2)    # int16
        self._wav_file.setframerate(config.sample_rate)

        self._service_type = service_type
        self.start_time = datetime.now()
        self._paused = False
        self._markers = []
        self._gaps = []
        self._is_recording = True

        try:
            loop = asyncio.get_running_loop()
            self._drain_task = loop.create_task(self._drain_rec_queue())
        except RuntimeError:
            self._drain_task = None  # tests without running loop drain manually

        return {
            "status": "recording",
            "wav_path": str(self._wav_path),
            "session_started_at": self.start_time.isoformat(),
            "service_type": service_type,
        }

    def place_marker(self, marker_type: str) -> dict:
        if not self._is_recording:
            return {"status": "error", "message": "Not recording"}
        # Nur pruefen, wenn die API uns ihre Liste geschoben hat. Sie prueft selbst
        # schon; eine zweite, womoeglich veraltete Liste hier war die Fehlerquelle.
        if BEITRAGSARTEN and marker_type not in BEITRAGSARTEN:
            return {"status": "error", "message": f"Unbekannte Beitragsart: {marker_type}"}
        offset = (datetime.now() - self.start_time).total_seconds()
        entry = {
            "type": marker_type,
            "offset_seconds": offset,
            "wall_time": datetime.now().isoformat(),
        }
        self._markers.append(entry)
        return {"status": "ok", "marker": entry, "audio_service_index": len(self._markers) - 1}

    def nudge_marker(self, index: int, field: str, delta_seconds: float) -> dict:
        """Adjust marker offset by ±1s. field: 'start' (mutates in-memory) or 'end' (DB-only)."""
        if not self._is_recording:
            return {"status": "error", "message": "Not recording"}
        if field not in ("start", "end"):
            return {"status": "error", "message": f"Invalid field: {field}"}
        if delta_seconds not in (-1.0, 1.0):
            return {"status": "error", "message": f"delta_seconds must be -1.0 or 1.0, got {delta_seconds}"}
        if index < 0 or index >= len(self._markers):
            return {"status": "error", "message": f"Marker index {index} out of range"}
        if field == "start":
            self._markers[index]["offset_seconds"] = max(0.0, self._markers[index]["offset_seconds"] + delta_seconds)
        return {"status": "ok", "marker": self._markers[index], "field": field, "delta_seconds": delta_seconds}

    def pause(self) -> dict:
        if not self._is_recording or self._paused:
            return {"status": "error", "message": "Cannot pause"}
        self._paused = True
        self._pause_started = datetime.now()
        offset = (self._pause_started - self.start_time).total_seconds()
        self._gaps.append({"start_offset": offset, "end_offset": None})
        return {"status": "paused", "paused_at_offset": offset}

    def resume(self) -> dict:
        if not self._is_recording or not self._paused:
            return {"status": "error", "message": "Not paused"}
        self._paused = False
        now = datetime.now()
        end_offset = (now - self.start_time).total_seconds()
        gap_seconds = (now - self._pause_started).total_seconds() if self._pause_started else 0.0
        if self._gaps and self._gaps[-1]["end_offset"] is None:
            self._gaps[-1]["end_offset"] = end_offset
        self._pause_started = None
        return {
            "status": "recording",
            "resumed_at_offset": end_offset,
            "gap_seconds": gap_seconds,
        }

    async def stop_session(self) -> dict:
        if not self._is_recording:
            return {"status": "error", "message": "No active session"}
        self._is_recording = False
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        # Drain any remaining queue contents
        while not _rec_q.empty():
            try:
                chunk = _rec_q.get_nowait()
                if self._wav_file:
                    self._wav_file.writeframes(chunk)
            except queue.Empty:
                break
        # The input stream stays open — it keeps feeding the level meter after the session.
        if self._wav_file:
            self._wav_file.close()  # writes nframes header
            self._wav_file = None
        duration = int((datetime.now() - self.start_time).total_seconds())
        result = {
            "status": "stopped",
            "wav_path": str(self._wav_path),
            "duration": duration,
            "service_type": self._service_type,
            "markers": list(self._markers),
            "gaps": list(self._gaps),
        }
        self.start_time = None
        self._wav_path = None
        self._service_type = None
        self._markers = []
        self._gaps = []
        return result

    def status(self) -> dict:
        if not self._is_recording:
            return {"state": "idle"}
        elapsed = int((datetime.now() - self.start_time).total_seconds()) if self.start_time else 0
        return {
            "state": "paused" if self._paused else "recording",
            "elapsed_seconds": elapsed,
            "marker_count": len(self._markers),
            "wav_path": str(self._wav_path) if self._wav_path else None,
            "service_type": self._service_type,
        }

    def emergency_close(self):
        """Called from SIGTERM handler — closes wav handle to flush header, releases the input."""
        if self._wav_file:
            try:
                self._wav_file.close()
            except Exception:
                pass
            self._wav_file = None
        self.close_input()

    def current_offset(self) -> Optional[float]:
        """Seconds since session start (WAV-relative), or None when not recording."""
        if not self._is_recording or self.start_time is None:
            return None
        return (datetime.now() - self.start_time).total_seconds()

    def get_rms_level(self) -> float:
        """Most recent ch1+2 RMS in [0.0, 1.0] — live whenever the input is open, recording or not."""
        if self._stream is None or monotonic() - self._last_callback_at > METER_STALE_SECONDS:
            return 0.0
        return self._last_rms
