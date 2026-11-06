"""Die .env des Audio-Dienstes von der Einstellungsseite: nur erlaubte Werte, nichts geht verloren."""
import sys

sys.path.insert(0, "audio-service")

import dienst  # noqa: E402

ENV = """# Kommentar bleibt
AUDIO_DEVICE_NAMES=Dante
AUDIO_RECORD_CHANNELS=44,45
NAS_PASSWORD=geheim
STREAM_URL=icecast://source:pw@host:8000/x
"""


def test_schreiben_aendert_nur_die_eine_zeile(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV)
    dienst.schreibe_umgebung({"AUDIO_RECORD_CHANNELS": "50,51", "OLLAMA_URL": "http://127.0.0.1:11434"}, env)
    text = env.read_text()
    assert "AUDIO_RECORD_CHANNELS=50,51" in text
    assert "# Kommentar bleibt" in text and "NAS_PASSWORD=geheim" in text
    assert "STREAM_URL=icecast://source:pw@host:8000/x" in text
    assert text.rstrip().endswith("OLLAMA_URL=http://127.0.0.1:11434")


def test_zugangsdaten_tauchen_nie_auf(tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV)
    schluessel = {w["schluessel"] for w in dienst.lies_umgebung(env)}
    assert "NAS_PASSWORD" not in schluessel and "STREAM_URL" not in schluessel
    werte = {w["schluessel"]: w["wert"] for w in dienst.lies_umgebung(env)}
    assert werte["AUDIO_RECORD_CHANNELS"] == "44,45"


def test_pruefen_lehnt_ab():
    assert dienst.pruefe({"AUDIO_RECORD_CHANNELS": "50,51"}) == []
    assert dienst.pruefe({"AUDIO_RECORD_CHANNELS": "fünfzig"})
    assert dienst.pruefe({"NAS_PASSWORD": "x"})          # nicht in der Liste
    assert dienst.pruefe({"RECORDINGS_DIR": "/tmp"})       # nur zur Anzeige
    assert dienst.pruefe({"STREAM_NAME": "a\nNAS_PASSWORD=x"})   # keine eingeschmuggelte Zeile
