"""Online stellen, Seite des Audio-Dienstes: Untertitel aus dem Live-Transkript.

Der Upload selbst ist nicht nachgespielt — ohne Zugangsdaten passiert nichts, und
genau das ist hier geprüft.
"""
import json
import sys

sys.path.insert(0, "audio_service")

import online  # noqa: E402


def _transkript(pfad, zeilen):
    with open(pfad, "w", encoding="utf-8") as f:
        for start, ende, text in zeilen:
            f.write(json.dumps({"start": start, "end": ende, "text": text}, ensure_ascii=False) + "\n")


def test_untertitel_auf_den_trackanfang_verschoben(tmp_path):
    t = tmp_path / "transcript.jsonl"
    _transkript(t, [(90.0, 99.0, "vorher"), (98.0, 107.5, "Liebe Geschwister,"),
                    (107.5, 118.0, "wir lesen Psalm 8."), (300.0, 310.0, "danach")])
    vtt = online.vtt_aus_transkript(t, 100.0, 200.0)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:07.500\nLiebe Geschwister," in vtt
    assert "00:00:07.500 --> 00:00:18.000\nwir lesen Psalm 8." in vtt
    assert "vorher" not in vtt and "danach" not in vtt


def test_ohne_transkript_keine_untertitel(tmp_path):
    assert online.vtt_aus_transkript(tmp_path / "fehlt.jsonl", 0, 10) is None


def test_ohne_zugangsdaten_uebersprungen(tmp_path, monkeypatch):
    monkeypatch.setattr(online.config, "online_s3_key", "")
    mp3 = tmp_path / "x.mp3"
    mp3.write_bytes(b"ID3")
    ergebnis = online.stelle_online(mp3, {"recording": {"file_key": "archive/x.mp3"}, "event": {}},
                                    tmp_path / "aufnahme.wav", 0, 10, 1000, False)
    assert ergebnis == {"status": "skipped", "file_key": "archive/x.mp3", "error": None}


def test_app_client_laesst_sich_mit_with_oeffnen(monkeypatch):
    """Regression 6.11.: Der Client wurde vor dem `with` benutzt — httpx lehnt das ab."""
    import httpx
    import online as o

    def antwort(request):
        return httpx.Response(200, json={"access": "t", "results": []})
    echter = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **k: echter(transport=httpx.MockTransport(antwort), **k))
    monkeypatch.setattr(o.config, "online_api_url", "https://app.test")
    import main as audio_main
    with audio_main._app_client() as client:
        assert client.get("/api/calendar/").status_code == 200
