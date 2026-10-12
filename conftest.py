"""Root conftest.py — adds audio-service/ and api/ to sys.path so tests can import modules without PYTHONPATH."""
import sys
from pathlib import Path

# Allow tests to import modules from audio-service/ without PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "audio-service"))

# Allow tests to import modules from api/ without PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "api"))
