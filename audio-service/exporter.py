"""Phase 5 MP3 export: 192kbit/s, 48kHz, peak -3dBFS, 1s fade in/out (REC-11, REC-12)."""
import json
import logging
import math
import pathlib
import subprocess
import tempfile
from typing import Optional

from pydub import AudioSegment

log = logging.getLogger(__name__)

TRUE_PEAK_DB = -1.0     # Luft für den MP3-Coder, der Spitzen um gut 0,5 dB anhebt
LRA = 11.0


def _loudnorm(audio: AudioSegment, lufs: float, rate: int) -> Optional[AudioSegment]:
    """Zwei Durchgänge loudnorm (EBU R 128): erst messen, dann linear angleichen.

    Ein Durchgang würde dynamisch regeln und eine Predigt hörbar pumpen lassen; mit den
    Messwerten aus dem ersten bleibt es eine reine Verstärkung, solange die Spitze passt.
    Gibt None zurück, wenn FFmpeg nichts Brauchbares liefert — dann zählt die Spitze.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ein, aus = pathlib.Path(tmp) / "ein.wav", pathlib.Path(tmp) / "aus.wav"
        audio.export(str(ein), format="wav")
        filt = f"loudnorm=I={lufs}:TP={TRUE_PEAK_DB}:LRA={LRA}"
        try:
            mess = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(ein), "-af",
                 filt + ":print_format=json", "-f", "null", "-"],
                capture_output=True, text=True, timeout=600)
            werte = json.loads(mess.stderr[mess.stderr.rindex("{"):mess.stderr.rindex("}") + 1])
            if not math.isfinite(float(werte["input_i"])):
                return None             # Stille — nichts anzugleichen
            filt += (f":measured_I={werte['input_i']}:measured_TP={werte['input_tp']}"
                     f":measured_LRA={werte['input_lra']}:measured_thresh={werte['input_thresh']}"
                     f":offset={werte['target_offset']}:linear=true")
            subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(ein), "-af", filt,
                            "-ar", str(rate), str(aus)],
                           capture_output=True, check=True, timeout=600)
            return AudioSegment.from_wav(str(aus))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
            log.warning("Lautheit: Angleichen fehlgeschlagen, es bleibt bei der Spitze (%s)", e)
            return None


def export_wav_to_mp3(
    wav_path,
    mp3_path,
    bitrate: str = "192k",
    target_sample_rate: int = 48000,
    fade_ms: int = 1000,
    peak_dbfs: float = -3.0,
    tags: Optional[dict] = None,
    cover: Optional[str] = None,
    lufs: Optional[float] = None,
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
        tags: ID3 tags (title, artist, album, track, …).
        cover: Image embedded as album art — skipped if the file is missing.

    Returns:
        dict with keys: mp3_path (str), duration_ms (int), frame_rate (int).
    """
    wav_path = pathlib.Path(wav_path)
    mp3_path = pathlib.Path(mp3_path)
    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    audio = AudioSegment.from_wav(str(wav_path))

    # REC-12: apply 1s fade-in and 1s fade-out
    audio = audio.fade_in(fade_ms).fade_out(fade_ms)

    # Lautheit statt Spitze, wenn eingestellt (lufs gesetzt). Klappt das nicht, fällt es
    # auf die Spitze zurück — ein Export ohne Normalisierung wäre schlechter als beides.
    angeglichen = _loudnorm(audio, lufs, target_sample_rate) if lufs is not None else None
    if angeglichen is not None:
        audio = angeglichen

    # REC-11: peak-normalize to peak_dbfs.
    # Pitfall 3: max_dBFS is -inf for full silence; apply_gain(+inf) would raise OverflowError.
    max_db = audio.max_dBFS
    if angeglichen is None and max_db != float("-inf") and not math.isinf(max_db):
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
        # ID3v2.3, nicht 2.4: der Windows-Explorer und ältere Autoradios lesen nur 2.3,
        # und auf dem NAS werden die Dateien quer durch die Gemeinde geöffnet.
        id3v2_version="3",
        cover=cover if cover and pathlib.Path(cover).is_file() else None,
    )

    return {
        "mp3_path": str(mp3_path),
        "duration_ms": len(audio),
        "frame_rate": target_sample_rate,
    }


GESAMT_BITRATE = "128k"


def gesamt_mp3(wav_path) -> Optional[pathlib.Path]:
    """Die ganze Aufnahme als MP3 neben die WAV — „aufnahme.mp3“.

    Für das Nachhören eines ganzen Gottesdienstes; die WAV ist mit 0,7 GB je Stunde dafür
    zu groß. Die WAV bleibt trotzdem liegen: Neu erkennen, erneuter Export und das Archiv
    schneiden aus ihr. Niedrige Priorität (nice), damit eine laufende Aufnahme nicht leidet.
    """
    wav_path = pathlib.Path(wav_path)
    ziel = wav_path.with_suffix(".mp3")
    if not wav_path.exists():
        return None
    neu = ziel.with_suffix(".mp3.neu")
    try:
        subprocess.run(["nice", "-n", "15", "ffmpeg", "-hide_banner", "-nostdin", "-y",
                        "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", GESAMT_BITRATE,
                        "-f", "mp3", str(neu)],
                       capture_output=True, check=True, timeout=3600)
        neu.replace(ziel)
        return ziel
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Gesamtaufnahme als MP3 fehlgeschlagen: %s", e)
        try:
            neu.unlink()
        except OSError:
            pass
        return None
