import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Union


def _channel_list(env_var: str, default: str) -> list:
    """1-based device channel numbers from a comma list, e.g. AUDIO_RECORD_CHANNELS=44,45"""
    return [int(c) for c in os.environ.get(env_var, default).replace(" ", "").split(",") if c]


def _recordings_dir() -> Path:
    """Wurzel fuer Aufnahmen, Sessions und Exporte.

    Ueber RECORDINGS_DIR umstellbar, damit eine Demo- oder Testinstallation nicht in
    den Produktionsordner schreibt. Der Default heisst weiterhin "Tontechnik": die
    Datenbank speichert absolute Pfade zu den Aufnahmen, ein Umbenennen des Ordners
    braucht also eine Datenmigration und ist bewusst nicht Teil der Umbenennung
    nach Akouo."""
    return Path(os.environ.get("RECORDINGS_DIR", str(Path.home() / "Recordings" / "Tontechnik"))).expanduser()


@dataclass
class Config:
    output_dir: Path = field(default_factory=_recordings_dir)
    sessions_dir: Path = field(default_factory=lambda: _recordings_dir() / "sessions")
    sample_rate: int = field(default_factory=lambda: int(os.environ.get("AUDIO_SAMPLE_RATE", "48000")))
    channels: int = 2          # legacy Recorder still uses 2
    # 1-based device channels: which pair is recorded and which pair feeds the livestream.
    # Default ist bewusst zweimal dasselbe Paar: so laeuft das System mit jedem
    # Stereo-Eingang. Wer Aufnahme und Stream getrennt mischt, setzt in der .env
    # zwei verschiedene Paare (hier: Dante 44,45 und 46,47).
    record_channels: list = field(default_factory=lambda: _channel_list("AUDIO_RECORD_CHANNELS", "1,2"))
    stream_channels: list = field(default_factory=lambda: _channel_list("AUDIO_STREAM_CHANNELS", "1,2"))
    dtype: str = "int16"        # legacy
    continuous_dtype: str = "float32"
    blocksize: int = 1024       # ~21ms per callback at 48000Hz
    bitrate: str = "192k"
    # First device whose name contains one of these (in order) is used as the input
    device_names: list = field(default_factory=lambda: [
        n.strip() for n in os.environ.get("AUDIO_DEVICE_NAMES", "Dante,Focusrite").split(",") if n.strip()
    ])
    api_port: int = 8001
    stream_url: str = field(default_factory=lambda: os.environ.get("STREAM_URL", ""))
    nas_server: str = field(default_factory=lambda: os.environ.get("NAS_SERVER", ""))
    nas_share: str = field(default_factory=lambda: os.environ.get("NAS_SHARE", ""))
    nas_user: str = field(default_factory=lambda: os.environ.get("NAS_USER", ""))
    nas_password: str = field(default_factory=lambda: os.environ.get("NAS_PASSWORD", ""))
    nas_base_path: str = field(default_factory=lambda: os.environ.get("NAS_BASE_PATH", "Tontechnik"))
    # Live-Transkription (whisper-server hält das Modell im Speicher)
    transcribe_enabled: bool = field(default_factory=lambda: os.environ.get("TRANSCRIBE_ENABLED", "1") == "1")
    transcribe_chunk_seconds: float = field(default_factory=lambda: float(os.environ.get("TRANSCRIBE_CHUNK_SECONDS", "10")))
    transcribe_language: str = field(default_factory=lambda: os.environ.get("TRANSCRIBE_LANGUAGE", "de"))
    whisper_url: str = field(default_factory=lambda: os.environ.get("WHISPER_URL", "http://127.0.0.1:8090/inference"))
    # KI-Auswertung über das lokale Ollama
    ollama_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_models: list = field(default_factory=lambda: [
        n.strip() for n in os.environ.get("OLLAMA_MODELS", "qwen2.5:14b-instruct,llama3.1:8b,llama3.2").split(",") if n.strip()
    ])
    exports_dir: Path = field(default_factory=lambda: _recordings_dir() / "exports")


    @property
    def continuous_channels(self) -> int:
        """Channels the input stream must expose to reach the configured pairs (PortAudio always
        starts at channel 1, so channel 45 means opening 45 channels and slicing)."""
        return max(self.record_channels + self.stream_channels)


config = Config()


# ─── Session folder layout ──────────────────────────────────────────────────
# Since 18.09.2026 every session gets its own folder below sessions_dir, holding the
# recording, its transcripts and an exports/ subfolder:
#
#   sessions/2026-09-18_Gebetsstunde_003/
#       aufnahme.wav
#       transcript.txt
#       transcript.jsonl
#       exports/2026-09-18_Gebetsstunde_01_Predigt.mp3
#
# Older sessions lie flat in sessions/ (their wav_path is stored that way in the database)
# and keep working unchanged: they are recognised by the WAV not carrying the fixed name.

SESSION_WAV_NAME = "aufnahme.wav"


def session_dir(wav_path: Union[str, Path]) -> Optional[Path]:
    """Folder of a per-session recording, or None for an old flat WAV in sessions/."""
    wav_path = Path(wav_path)
    return wav_path.parent if wav_path.name == SESSION_WAV_NAME else None


def export_dir(wav_path: Union[str, Path]) -> Path:
    """Where segment MP3s of this session belong — exports/ inside the session folder,
    or the shared exports folder for old flat recordings."""
    folder = session_dir(wav_path)
    return folder / "exports" if folder is not None else config.exports_dir
