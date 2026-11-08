"""Root conftest.py — adds audio_service/ and api/ to sys.path so tests can import modules without PYTHONPATH."""
import sys
from pathlib import Path

# Allow tests to import modules from audio_service/ without PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "audio_service"))

# Allow tests to import modules from api/ without PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "api"))
