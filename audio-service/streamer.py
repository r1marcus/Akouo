"""Phase 4 StreamAdapter — consumes _str_q from recorder and pipes to FFmpeg stdin.

Replaces the legacy streaming-service direct-capture approach. Single CoreAudio stream
is owned by ContinuousRecorder; this adapter only forwards already-deinterleaved PCM.
While streaming it sets recorder.stream_enabled so the callback fills _str_q at all.
"""
import asyncio
import queue
from datetime import datetime
from typing import Optional

from config import config


def public_target(url: str) -> str:
    """Stream target without credentials — safe to show in the dashboard and in logs."""
    if not url:
        return ""
    if "@" in url and "//" in url:
        scheme, rest = url.split("//", 1)
        return f"{scheme}//{rest.split('@', 1)[1]}"
    return url


class StreamAdapter:
    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._drain_task: Optional[asyncio.Task] = None
        self._is_streaming: bool = False
        self.start_time: Optional[datetime] = None
        self._stream_target: Optional[str] = None

    async def start(self, stream_target: str) -> dict:
        if self._is_streaming:
            return {"status": "error", "message": "Already streaming"}
        if not stream_target:
            return {"status": "error", "message": "No stream target configured"}

        if stream_target.startswith("icecast://"):
            cmd = [
                "ffmpeg",
                "-f", "s16le", "-ar", str(config.sample_rate), "-ac", "2", "-i", "pipe:0",
                "-acodec", "libmp3lame", "-b:a", "128k",
                "-f", "mp3", "-ice_name", "Tontechnik", "-ice_public", "0",
                stream_target,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-f", "s16le", "-ar", str(config.sample_rate), "-ac", "2", "-i", "pipe:0",
                "-acodec", "aac", "-b:a", config.bitrate,
                "-f", "flv", stream_target,
            ]
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,  # MUST be DEVNULL — PIPE without consumer deadlocks
        )
        from recorder import _str_q, stream_enabled  # late import — recorder owns the queue
        while not _str_q.empty():  # drop stale audio from a previous stream
            try:
                _str_q.get_nowait()
            except queue.Empty:
                break
        stream_enabled.set()
        self._is_streaming = True
        self.start_time = datetime.now()
        self._stream_target = stream_target
        self._drain_task = asyncio.create_task(self._drain_loop())
        return {"status": "streaming"}

    async def _drain_loop(self):
        """Consume _str_q from recorder module; write to FFmpeg stdin; drain periodically."""
        from recorder import _str_q, stream_enabled  # late import — recorder owns the queue
        chunks_since_drain = 0
        try:
            while self._is_streaming:
                while True:
                    try:
                        chunk = _str_q.get_nowait()
                    except queue.Empty:
                        break
                    if self._process and self._process.returncode is None and self._process.stdin:
                        try:
                            self._process.stdin.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            self._is_streaming = False
                            break
                        chunks_since_drain += 1
                # Pitfall 4: drain transport buffer ~1/sec to prevent unbounded asyncio buffer
                if chunks_since_drain >= 43 and self._process and self._process.stdin:
                    try:
                        await self._process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        self._is_streaming = False
                        break
                    chunks_since_drain = 0
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            stream_enabled.clear()  # no consumer any more — stop filling _str_q

    async def stop(self) -> dict:
        if not self._is_streaming or self._process is None:
            return {"status": "error", "message": "Not streaming"}
        from recorder import stream_enabled
        stream_enabled.clear()
        self._is_streaming = False
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        try:
            if self._process.stdin:
                self._process.stdin.close()
        except Exception:
            pass
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()
        duration = int((datetime.now() - self.start_time).total_seconds()) if self.start_time else 0
        self._process = None
        self.start_time = None
        self._stream_target = None
        return {"status": "stopped", "duration": duration}

    def status(self) -> dict:
        if self._is_streaming and self._process is not None:
            if self._process.returncode is not None:
                self._is_streaming = False
                from recorder import stream_enabled
                stream_enabled.clear()
                return {"status": "error", "message": "FFmpeg exited unexpectedly"}
            elapsed = int((datetime.now() - self.start_time).total_seconds()) if self.start_time else 0
            return {"status": "streaming", "elapsed_seconds": elapsed}
        return {"status": "idle"}
