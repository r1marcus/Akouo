"""Phase 5 MP3 export: 192kbit/s, 48kHz, peak -3dBFS, 1s fade in/out (REC-11, REC-12)."""
import math
import pathlib
from typing import Optional

from pydub import AudioSegment


def export_wav_to_mp3(
    wav_path,
    mp3_path,
    bitrate: str = "192k",
    target_sample_rate: int = 48000,
    fade_ms: int = 1000,
    peak_dbfs: float = -3.0,
    tags: Optional[dict] = None,
) -> dict:
    """Export a session WAV as broadcast-quality MP3.

    Steps (REC-11, REC-12):
      1. Load WAV
      2. Apply fade_in(fade_ms).fade_out(fade_ms)            — REC-12
      3. Peak-normalize to peak_dbfs (skip if all-silence)   — REC-11 (Pitfall 3 guard)
      4. Export with bitrate + FFmpeg "-ar" resample         — REC-11 (Pitfall 4: list form)

    Args:
        wav_path: Path to source WAV file.
        mp3_path: Path for output MP3 file (parent dirs created if missing).
        bitrate: MP3 bitrate string, e.g. "192k".
        target_sample_rate: Output sample rate in Hz (48000 for broadcast standard).
        fade_ms: Duration of fade-in and fade-out in milliseconds.
        peak_dbfs: Target peak level in dBFS (e.g. -3.0 for broadcast headroom).

    Returns:
        dict with keys: mp3_path (str), duration_ms (int), frame_rate (int).
    """
    wav_path = pathlib.Path(wav_path)
    mp3_path = pathlib.Path(mp3_path)
    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    audio = AudioSegment.from_wav(str(wav_path))

    # REC-12: apply 1s fade-in and 1s fade-out
    audio = audio.fade_in(fade_ms).fade_out(fade_ms)

    # REC-11: peak-normalize to peak_dbfs.
    # Pitfall 3: max_dBFS is -inf for full silence; apply_gain(+inf) would raise OverflowError.
    max_db = audio.max_dBFS
    if max_db != float("-inf") and not math.isinf(max_db):
        gain = peak_dbfs - max_db
        audio = audio.apply_gain(gain)

    # REC-11: export at 192kbit/s with 48kHz resampling.
    # Pitfall 4: parameters must be a list of separate strings — NOT a single combined string.
    audio.export(
        str(mp3_path),
        format="mp3",
        bitrate=bitrate,
        parameters=["-ar", str(target_sample_rate)],
        tags=tags or None,
    )

    return {
        "mp3_path": str(mp3_path),
        "duration_ms": len(audio),
        "frame_rate": target_sample_rate,
    }
