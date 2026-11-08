"""Live transcription — slices the running session WAV and asks whisper-server for text.

Reads the growing WAV directly by byte offset (its header still says 0 frames while recording,
so ffmpeg cannot be used on it), downmixes the recording pair to 16 kHz mono and posts each
chunk to whisper-server, which keeps the model in memory (~0.35 s per chunk on the M4 Max).

Best effort by design: every failure is caught and logged. Transcription must never disturb
the recording.
"""
import asyncio
import io
import json
import pathlib
import wave
from typing import Optional

import httpx
import numpy as np

from config import config, session_dir

WAV_HEADER_BYTES = 44      # canonical header written by wave.open
BYTES_PER_FRAME = 4        # stereo int16
TARGET_RATE = 16000        # whisper input rate
NOISE_MARKERS = ("[BLANK_AUDIO]", "(Musik)", "[Musik]", "*", "(applaus)")
SNAP_WINDOW_SECONDS = 2.0   # look this far back for a quiet spot to cut at
SNAP_STEP_MS = 100          # resolution of the quiet-spot search
CONTEXT_CHARS = 200         # previous text handed to whisper so it keeps the thread


class LiveTranscriber:
    def __init__(self):
        self.lines: list[dict] = []          # {"start": s, "end": s, "text": str}
        self.last_error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._wav_path: Optional[pathlib.Path] = None
        self._jsonl: Optional[pathlib.Path] = None
        self._pos_frames = 0

    # ─── lifecycle ──────────────────────────────────────────────────────────

    def start(self, wav_path: str):
        if not config.transcribe_enabled:
            return
        self._wav_path = pathlib.Path(wav_path)
        self._jsonl = self._transcript_path("jsonl")
        self._pos_frames = 0
        self.lines = []
        self.last_error = None
        self._jsonl.unlink(missing_ok=True)
        try:
            self._task = asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            self._task = None  # no loop (tests)

    async def stop(self):
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._process_available(final=True)
        self._write_text_file()

    def status(self) -> dict:
        return {
            "enabled": bool(config.transcribe_enabled),
            "running": self._task is not None and not self._task.done(),
            "line_count": len(self.lines),
            "error": self.last_error,
        }

    def since(self, index: int) -> dict:
        index = max(0, int(index))
        return {"lines": self.lines[index:], "next": len(self.lines), **self.status()}

    # ─── worker ─────────────────────────────────────────────────────────────

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(1.0)
                await self._process_available()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let transcription kill anything else
            self.last_error = str(exc)
            print(f"[transcript] worker stopped: {exc}", flush=True)

    async def _process_available(self, final: bool = False):
        if self._wav_path is None or not self._wav_path.exists():
            return
        chunk_frames = int(config.transcribe_chunk_seconds * config.sample_rate)
        min_frames = int(1.5 * config.sample_rate) if final else chunk_frames
        while True:
            available = max(0, (self._wav_path.stat().st_size - WAV_HEADER_BYTES) // BYTES_PER_FRAME)
            ready = available - self._pos_frames
            if ready < min_frames:
                return
            take = min(chunk_frames, ready)
            if not final and take >= chunk_frames:
                take = await asyncio.to_thread(self._snap_to_quiet, self._pos_frames, take)
            start_s = self._pos_frames / config.sample_rate
            audio = await asyncio.to_thread(self._read_mono16k, self._pos_frames, take)
            self._pos_frames += take
            end_s = self._pos_frames / config.sample_rate
            context = " ".join(line["text"] for line in self.lines[-2:])[-CONTEXT_CHARS:]
            text = await self._transcribe(audio, context)
            if text:
                line = {"start": round(start_s, 1), "end": round(end_s, 1), "text": text}
                self.lines.append(line)
                self._append_jsonl(line)
            if not final:
                return  # one chunk per tick keeps the loop responsive

    def _snap_to_quiet(self, start_frame: int, take: int) -> int:
        """Cut at the quietest 100 ms inside the last 2 s of the chunk — avoids slicing mid-word,
        which is what makes whisper invent words like a stray "Amen"."""
        window = int(SNAP_WINDOW_SECONDS * config.sample_rate)
        if take <= window:
            return take
        audio = self._read_mono16k(start_frame + take - window, window)
        step = int(SNAP_STEP_MS / 1000 * TARGET_RATE)
        blocks = len(audio) // step
        if blocks < 2:
            return take
        level = np.abs(audio[: blocks * step].reshape(blocks, step).astype(np.float32)).mean(axis=1)
        quietest = int(np.argmin(level))
        # translate the 16 kHz block index back into 48 kHz frames
        factor = config.sample_rate // TARGET_RATE
        cut_back = (blocks - 1 - quietest) * step * factor
        return max(int(take * 0.5), take - cut_back)

    def _read_mono16k(self, start_frame: int, frames: int) -> np.ndarray:
        """Recording pair → mono → 16 kHz (3:1 decimation with a 3-tap average as anti-alias)."""
        with open(self._wav_path, "rb") as fh:
            fh.seek(WAV_HEADER_BYTES + start_frame * BYTES_PER_FRAME)
            raw = fh.read(frames * BYTES_PER_FRAME)
        data = np.frombuffer(raw, dtype=np.int16)
        usable = (len(data) // 2) * 2
        stereo = data[:usable].reshape(-1, 2).astype(np.float32)
        mono = stereo.mean(axis=1)
        factor = config.sample_rate // TARGET_RATE          # 48000 / 16000 = 3
        trimmed = mono[: (len(mono) // factor) * factor]
        return trimmed.reshape(-1, factor).mean(axis=1).astype(np.int16)

    async def _transcribe(self, audio: np.ndarray, context: str = "") -> Optional[str]:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_RATE)
            wf.writeframes(audio.tobytes())
        buf.seek(0)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    config.whisper_url,
                    files={"file": ("chunk.wav", buf.read(), "audio/wav")},
                    data={"language": config.transcribe_language, "response_format": "verbose_json",
                          "temperature": "0", "prompt": context},
                )
            if response.status_code != 200:
                self.last_error = f"whisper-server {response.status_code}"
                return None
            payload = response.json()
            # Join segments verbatim — each carries its own leading space. Joining on whitespace
            # would turn a mid-word segment split ("Besch" + "reibungen") into two words.
            segments = payload.get("segments") or []
            text = ("".join(seg.get("text", "") for seg in segments) if segments
                    else (payload.get("text") or "")).strip()
        except Exception as exc:
            self.last_error = str(exc)
            return None
        self.last_error = None
        cleaned = " ".join(text.split())
        if not cleaned or cleaned.lower().strip("().[]* ") in ("", "musik", "applaus", "blank_audio"):
            return None
        for marker in NOISE_MARKERS:
            cleaned = cleaned.replace(marker, "").strip()
        return cleaned or None

    # ─── files next to the WAV ──────────────────────────────────────────────

    def _transcript_path(self, kind: str) -> pathlib.Path:
        """transcript.txt / transcript.jsonl inside the session folder. Old flat recordings
        keep their previous names next to the WAV (<wav>.txt / <wav>.transcript.jsonl)."""
        folder = session_dir(self._wav_path)
        if folder is not None:
            return folder / f"transcript.{kind}"
        return self._wav_path.with_suffix(".txt" if kind == "txt" else ".transcript.jsonl")

    def _append_jsonl(self, line: dict):
        try:
            with open(self._jsonl, "a") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.last_error = str(exc)

    def _write_text_file(self):
        if self._wav_path is None or not self.lines:
            return
        try:
            self._transcript_path("txt").write_text(
                "\n".join(line["text"] for line in self.lines) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            self.last_error = str(exc)
