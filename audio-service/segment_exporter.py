"""Phase 7: Segment slicing for per-marker MP3 export (EXP-01).

Slices a source session WAV between two second-offsets, then delegates the
final 192k/48kHz/-3dBFS/1s-fade encode to the existing export_wav_to_mp3
(preserves REC-11 + REC-12 contract from Phase 5).
"""
from __future__ import annotations
import pathlib
from typing import Optional
import tempfile

from pydub import AudioSegment

from exporter import export_wav_to_mp3


def export_segment(
    wav_path,
    start_seconds: float,
    end_seconds: float,
    mp3_path,
    bitrate: str = "192k",
    target_sample_rate: int = 48000,
    fade_ms: int = 1000,
    peak_dbfs: float = -3.0,
    tags: Optional[dict] = None,
) -> dict:
    """Slice [start_seconds, end_seconds) from wav_path and export as MP3.

    Args:
        wav_path: Path to source session WAV file.
        start_seconds: Start offset in seconds (inclusive).
        end_seconds: End offset in seconds (exclusive).
        mp3_path: Destination MP3 path (parent dirs auto-created).
        bitrate: MP3 bitrate string passed to export_wav_to_mp3 (default "192k").
        target_sample_rate: Output sample rate in Hz (default 48000).
        fade_ms: Fade-in and fade-out duration in ms applied by export_wav_to_mp3.
        peak_dbfs: Peak normalisation target passed to export_wav_to_mp3.

    Returns:
        dict with keys: mp3_path (str), duration_ms (int), frame_rate (int)
        — same shape as export_wav_to_mp3 so callers see one consistent contract.

    Raises:
        ValueError: If start >= end or end exceeds WAV length.
    """
    wav_path = pathlib.Path(wav_path)
    mp3_path = pathlib.Path(mp3_path)

    if start_seconds >= end_seconds:
        raise ValueError(
            f"start must be less than end (got start={start_seconds}, end={end_seconds})"
        )
    if start_seconds < 0:
        raise ValueError(f"start must be >= 0 (got {start_seconds})")

    audio = AudioSegment.from_wav(str(wav_path))
    total_ms = len(audio)
    start_ms = int(round(start_seconds * 1000))
    end_ms = int(round(end_seconds * 1000))

    # Ein Ende hinter dem Dateiende ist kein Fehler, sondern der Normalfall bei einem
    # Track, der beim Stoppen noch lief: dessen Grenze wird aus der Uhr gerechnet
    # (gestoppt minus gestartet), und der Schreiber hört Sekundenbruchteile früher auf.
    # Dann nehmen wir, was da ist. Zwei Sekunden Spielraum decken die Uhrdrift reichlich
    # ab (gemessen waren es 0,7); mehr deutet auf einen echten Fehler hin, etwa eine
    # Grenze aus einer anderen Aufnahme.
    TOLERANZ_MS = 2000
    if end_ms > total_ms + TOLERANZ_MS:
        raise ValueError(
            f"end exceeds WAV length (end_ms={end_ms}, total_ms={total_ms})"
        )
    end_ms = min(end_ms, total_ms)
    if start_ms >= end_ms:
        raise ValueError(
            f"segment starts at or after the end of the recording "
            f"(start_ms={start_ms}, total_ms={total_ms})"
        )

    sliced = audio[start_ms:end_ms]

    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    # Write slice to a temp WAV then call the canonical exporter so fade-in,
    # fade-out, peak normalisation, bitrate and resample all stay in one place.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
    try:
        sliced.export(str(tmp_path), format="wav")
        return export_wav_to_mp3(
            tmp_path,
            mp3_path,
            bitrate=bitrate,
            target_sample_rate=target_sample_rate,
            fade_ms=fade_ms,
            peak_dbfs=peak_dbfs,
            tags=tags,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
