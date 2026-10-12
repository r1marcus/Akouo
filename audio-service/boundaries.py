"""Find where a track really starts and ends — the operator rarely hits the button on time.

Measures the noise floor around the operator's timestamp, then looks for the first (or last)
stretch of sound that stays clearly above it. Returns corrected seconds plus the shift, so the
dashboard can show what was changed.
"""
import math
import wave
from typing import Optional

import numpy as np

BLOCK_MS = 50                 # resolution of the level curve
SEARCH_SECONDS = 8.0          # how far around the timestamp we look — operators are off by seconds, not minutes
MIN_SOUND_MS = 400            # sound must hold this long to count as a real start
MIN_SILENCE_MS = 1200         # …and this much quiet must sit in front of it (sentence pauses are ~0.5 s
                              #  and must NOT count, otherwise continuous speech snaps around)
LEAD_IN_MS = 250              # keep a little air before the first sound
TAIL_MS = 400                 # and after the last one
THRESHOLD_OVER_FLOOR_DB = 12.0
ABSOLUTE_FLOOR_DBFS = -60.0
FLOOR_PERCENTILE = 20


def _block_levels(wav_path, from_seconds: float, to_seconds: float):
    """Peak level in dBFS per 50 ms block; returns (levels, window_start_seconds)."""
    with wave.open(str(wav_path), "rb") as wf:
        rate, channels, total = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        first = max(0, int(from_seconds * rate))
        last = min(total, int(to_seconds * rate))
        if last <= first:
            return np.array([]), first / rate, rate
        wf.setpos(first)
        raw = wf.readframes(last - first)
    data = np.frombuffer(raw, dtype=np.int16)
    usable = (len(data) // channels) * channels
    frames = data[:usable].reshape(-1, channels).astype(np.float32) / 32768.0
    peak = np.abs(frames).max(axis=1)
    block = max(1, int(rate * BLOCK_MS / 1000))
    count = len(peak) // block
    if count == 0:
        return np.array([]), first / rate, rate
    levels = peak[: count * block].reshape(count, block).max(axis=1)
    return 20 * np.log10(np.maximum(levels, 1e-7)), first / rate, rate


def envelope(wav_path, from_seconds: float = 0.0, to_seconds: Optional[float] = None, block_ms: int = 250):
    """Peak dBFS per block over a window — the level curve the dashboard draws.

    Reads only the requested window, so polling the tail of a running recording costs a
    couple of hundred KB, not the whole file. Values are clipped to [-60, 0] and rounded to
    whole dB: that is all a 3 px-per-block curve can show, and it keeps the JSON small
    (a two-hour service at 250 ms blocks is ~29k numbers).

    Returns (levels, window_start_seconds, block_seconds).
    """
    if to_seconds is None or not math.isfinite(to_seconds):
        # "to the end" — ask the file rather than passing a huge number down the chain.
        with wave.open(str(wav_path), "rb") as wf:
            to_seconds = wf.getnframes() / float(wf.getframerate())
    block_ms = max(BLOCK_MS, int(block_ms))
    group = max(1, round(block_ms / BLOCK_MS))
    fine, window_start, _ = _block_levels(wav_path, from_seconds, to_seconds)
    if fine.size == 0:
        return [], window_start, group * BLOCK_MS / 1000.0
    count = fine.size // group
    if count == 0:  # shorter than one block — report it as one, rather than nothing
        coarse = np.array([fine.max()])
    else:
        coarse = fine[: count * group].reshape(count, group).max(axis=1)
    clipped = np.clip(np.rint(coarse), ABSOLUTE_FLOOR_DBFS, 0.0)
    return [int(v) for v in clipped], window_start, group * BLOCK_MS / 1000.0


def _threshold(levels: np.ndarray) -> float:
    floor = float(np.percentile(levels, FLOOR_PERCENTILE))
    return max(floor + THRESHOLD_OVER_FLOOR_DB, ABSOLUTE_FLOOR_DBFS)


def _sustained(loud: np.ndarray, blocks: int) -> np.ndarray:
    """True at index i when the next `blocks` blocks all share that state."""
    if len(loud) < blocks:
        return np.zeros(len(loud), dtype=bool)
    return np.lib.stride_tricks.sliding_window_view(loud, blocks).all(axis=1)


def _onsets(levels: np.ndarray, threshold: float, hold: int, quiet: int) -> np.ndarray:
    """Indices where real silence turns into lasting sound."""
    loud = levels > threshold
    sound_ahead = _sustained(loud, hold)
    silence_ahead = _sustained(~loud, quiet)
    candidates = []
    for i in range(quiet, len(sound_ahead)):
        if sound_ahead[i] and i - quiet < len(silence_ahead) and silence_ahead[i - quiet]:
            candidates.append(i)
    return np.array(candidates, dtype=int)


def _offsets(levels: np.ndarray, threshold: float, hold: int, quiet: int) -> np.ndarray:
    """Indices (last loud block) where lasting sound turns into real silence."""
    loud = levels > threshold
    silence_ahead = _sustained(~loud, quiet)
    sound_ahead = _sustained(loud, hold)
    candidates = []
    for i in range(hold, len(silence_ahead)):
        if silence_ahead[i] and i - hold < len(sound_ahead) and sound_ahead[i - hold]:
            candidates.append(i - 1)
    return np.array(candidates, dtype=int)


def _nearest(candidates: np.ndarray, window_start: float, block_s: float, target: float):
    """Candidate closest to the operator's timestamp — not simply the first one in the window."""
    if candidates.size == 0:
        return None
    seconds = window_start + candidates * block_s
    return int(candidates[int(np.argmin(np.abs(seconds - target)))])


def refine(wav_path, start_seconds: float, end_seconds: float) -> dict:
    """Corrected boundaries for one segment. Falls back to the original values when unsure."""
    block_s = BLOCK_MS / 1000.0
    hold = max(1, int(MIN_SOUND_MS / BLOCK_MS))
    quiet = max(1, int(MIN_SILENCE_MS / BLOCK_MS))
    result = {
        "start_seconds": float(start_seconds),
        "end_seconds": float(end_seconds),
        "start_delta": 0.0,
        "end_delta": 0.0,
        "threshold_dbfs": None,
    }

    levels, window_start, _ = _block_levels(
        wav_path, start_seconds - SEARCH_SECONDS, min(start_seconds + SEARCH_SECONDS, end_seconds)
    )
    if levels.size:
        threshold = _threshold(levels)
        result["threshold_dbfs"] = round(threshold, 1)
        index = _nearest(_onsets(levels, threshold, hold, quiet), window_start, block_s, start_seconds)
        if index is not None:
            onset = window_start + index * block_s - LEAD_IN_MS / 1000.0
            onset = max(0.0, onset)
            if abs(onset - start_seconds) <= SEARCH_SECONDS:
                result["start_seconds"] = round(onset, 2)
                result["start_delta"] = round(onset - start_seconds, 2)

    levels, window_start, _ = _block_levels(
        wav_path, max(end_seconds - SEARCH_SECONDS, result["start_seconds"]), end_seconds + SEARCH_SECONDS
    )
    if levels.size:
        threshold = _threshold(levels)
        index = _nearest(_offsets(levels, threshold, hold, quiet), window_start, block_s, end_seconds)
        if index is not None:
            offset = window_start + (index + 1) * block_s + TAIL_MS / 1000.0
            if abs(offset - end_seconds) <= SEARCH_SECONDS and offset > result["start_seconds"] + 0.5:
                result["end_seconds"] = round(offset, 2)
                result["end_delta"] = round(offset - end_seconds, 2)

    return result


# ─── Von der API geschobene Werte ───────────────────────────────────────────
# Die Konstanten oben sind die Startwerte. Alle Leser greifen innerhalb von Funktionen
# auf die Modulnamen zu, deshalb genügt ein Neusetzen.

def anwenden(cfg: dict) -> dict:
    """Schnittkanten-Werte übernehmen. Fehlt ein Feld, bleibt der Startwert stehen."""
    global SEARCH_SECONDS, MIN_SOUND_MS, MIN_SILENCE_MS
    global LEAD_IN_MS, TAIL_MS, THRESHOLD_OVER_FLOOR_DB

    paare = (
        ("grenzen_suchfenster_s", "SEARCH_SECONDS", float),
        ("grenzen_min_ton_ms", "MIN_SOUND_MS", int),
        ("grenzen_min_stille_ms", "MIN_SILENCE_MS", int),
        ("grenzen_vorlauf_ms", "LEAD_IN_MS", int),
        ("grenzen_nachlauf_ms", "TAIL_MS", int),
        ("grenzen_schwelle_db", "THRESHOLD_OVER_FLOOR_DB", float),
    )
    uebernommen = {}
    for feld, name, typ in paare:
        if cfg.get(feld) is not None:
            globals()[name] = typ(cfg[feld])
            uebernommen[feld] = globals()[name]
    return uebernommen


def aktueller_stand() -> dict:
    return {
        "grenzen_suchfenster_s": SEARCH_SECONDS,
        "grenzen_min_ton_ms": MIN_SOUND_MS,
        "grenzen_min_stille_ms": MIN_SILENCE_MS,
        "grenzen_vorlauf_ms": LEAD_IN_MS,
        "grenzen_nachlauf_ms": TAIL_MS,
        "grenzen_schwelle_db": THRESHOLD_OVER_FLOOR_DB,
    }
