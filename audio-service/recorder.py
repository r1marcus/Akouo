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

METER_STALE_SECONDS = 0.5   # no callback for this long → meter reads 0.0
INPUT_LOST_SECONDS = 3.0    # no callback for this long → close stream and rescan devices
INPUT_POLL_SECONDS = 3.0


class ContinuousRecorder:
    def __init__(self):
        self._stream: Optional[sd.InputStream] = None
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
        self._refresh_channel_map()

    def _refresh_channel_map(self):
        self._rec_idx = [c - 1 for c in config.record_channels]
        self._str_idx = [c - 1 for c in config.stream_channels]

    def _find_input_device(self) -> Optional[int]:
        """Index of the first device from config.device_names with enough input channels."""
        devices = list(enumerate(sd.query_devices()))
        for wanted in config.device_names:
            for i, dev in devices:
                if wanted in dev["name"] and dev["max_input_channels"] >= config.continuous_channels:
                    return i
        return None

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
        try:
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
                            f"[input] input open: {sd.query_devices(self._stream.device)['name']} "
                            f"({self._stream.channels} Kanäle, Aufnahme {config.record_channels}, "
                            f"Stream {config.stream_channels})",
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
        if stream_enabled.is_set() or monitor_enabled.is_set():
            stream_pcm = (clipped[:, self._str_idx] * 32767).astype(np.int16).tobytes()
            if stream_enabled.is_set():
                _str_q.put_nowait(stream_pcm)
            if monitor_enabled.is_set():
                _mon_q.put_nowait(stream_pcm)

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


class Recorder:
    def __init__(self):
        self._buffer: list[bytes] = []
        self._stream: Optional[sd.RawInputStream] = None
        self.is_recording: bool = False
        self.start_time: Optional[datetime] = None
        self._current_path: Optional[pathlib.Path] = None
        self._current_service_type: Optional[str] = None
        self._current_output_dir: Optional[pathlib.Path] = None

    def _find_input_device(self) -> Optional[int]:
        """Return device index of the first stereo-capable device from config.device_names."""
        devices = list(enumerate(sd.query_devices()))
        for wanted in config.device_names:
            for i, dev in devices:
                if wanted in dev["name"] and dev["max_input_channels"] >= 2:
                    return i
        return None

    def _generate_path(self, service_type: str, output_dir: pathlib.Path) -> pathlib.Path:
        """Generate unique output path: output_dir/YYYY/MM/YYYY-MM-DD_ServiceType_NNN.mp3"""
        now = datetime.now()
        year_month_dir = output_dir / str(now.year) / f"{now.month:02d}"
        year_month_dir.mkdir(parents=True, exist_ok=True)

        date_str = now.strftime("%Y-%m-%d")
        # Find next available NNN sequence
        existing = list(year_month_dir.glob(f"{date_str}_{service_type}_*.mp3"))
        seq = len(existing) + 1
        filename = f"{date_str}_{service_type}_{seq:03d}.mp3"
        return year_month_dir / filename

    def _callback(self, indata: bytes, frames: int, time, status) -> None:
        """sounddevice RawInputStream callback — accumulates raw PCM bytes."""
        if status:
            pass  # Log status flags (overflow, etc.) without stopping
        self._buffer.append(bytes(indata))

    def start(self, service_type: str, output_dir: str) -> dict:
        if self.is_recording:
            return {"status": "error", "message": "Already recording"}

        device = self._find_input_device()
        if device is None:
            return {"status": "error", "message": "Kein Audio-Eingang gefunden"}

        output_path = pathlib.Path(output_dir)
        self._buffer = []
        self._current_service_type = service_type
        self._current_output_dir = output_path
        self._current_path = self._generate_path(service_type, output_path)
        self.start_time = datetime.now()
        self.is_recording = True

        self._stream = sd.RawInputStream(
            samplerate=config.sample_rate,
            channels=config.channels,
            dtype=config.dtype,
            device=device,
            callback=self._callback,
        )
        self._stream.start()

        return {"status": "recording", "path": str(self._current_path)}

    def stop(self) -> dict:
        if not self.is_recording:
            return {"status": "error", "message": "Not recording"}

        self._stream.stop()
        self._stream.close()
        self._stream = None
        self.is_recording = False

        # Encode accumulated PCM to MP3
        raw_data = b"".join(self._buffer)
        if not raw_data:
            # No audio captured (e.g., mock stream in tests) — produce silent segment
            audio = AudioSegment.silent(
                duration=0, frame_rate=config.sample_rate
            ).set_channels(config.channels)
        else:
            audio = AudioSegment.from_raw(
                io.BytesIO(raw_data),
                sample_width=2,       # int16 = 2 bytes
                frame_rate=config.sample_rate,
                channels=config.channels,
            )
        output_path = self._current_path
        audio.export(str(output_path), format="mp3", bitrate=config.bitrate)

        duration_seconds = int((datetime.now() - self.start_time).total_seconds())
        self._buffer = []
        self.start_time = None

        return {
            "status": "saved",
            "path": str(output_path),
            "filename": output_path.name,
            "duration": duration_seconds,
            "service_type": self._current_service_type,
        }

    def status(self) -> dict:
        if self.is_recording:
            elapsed = int((datetime.now() - self.start_time).total_seconds())
            return {
                "status": "recording",
                "elapsed_seconds": elapsed,
                "path": str(self._current_path),
            }
        return {"status": "idle"}
