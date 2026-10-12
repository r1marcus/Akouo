"""KI-Auswertung: Transkript-Ausschnitt und Prüfung der Bibelstellen."""
import json
import sys

sys.path.insert(0, "audio-service")


def test_transcript_slice_picks_overlapping_lines(tmp_path):
    from insights import transcript_slice
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    lines = [
        {"start": 0.0, "end": 10.0, "text": "davor"},
        {"start": 10.0, "end": 20.0, "text": "mittendrin"},
        {"start": 20.0, "end": 30.0, "text": "auch noch"},
        {"start": 40.0, "end": 50.0, "text": "danach"},
    ]
    (tmp_path / "aufnahme.transcript.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )

    text = transcript_slice(wav, 12.0, 25.0)

    assert "mittendrin" in text and "auch noch" in text
    assert "danach" not in text


def test_transcript_slice_finds_per_session_layout(tmp_path):
    """Transkript liegt im Sessionordner statt neben der Datei."""
    from insights import transcript_slice
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    (tmp_path / "transcript.jsonl").write_text(
        json.dumps({"start": 0.0, "end": 5.0, "text": "im Ordner"}), encoding="utf-8"
    )

    assert "im Ordner" in transcript_slice(wav, 0.0, 5.0)


def test_invented_bible_references_are_dropped():
    """Das Modell erfindet gern Verse — nur belegte Stellen bleiben."""
    from insights import verified_references
    transcript = "Wir lesen Johannes 3,16 und dazu 1. Korinther 13,4-7."

    refs = verified_references(["Johannes 3,16", "Hesekiel 99,9", "Quatsch 1,1"], transcript)

    assert "Johannes 3,16" in refs
    assert all("Hesekiel" not in r and "Quatsch" not in r for r in refs)


def test_spoken_reference_is_added_even_if_model_missed_it():
    from insights import verified_references
    transcript = "Der Predigttext steht in Römer 8,28 und das tröstet."

    assert any("Römer 8,28" in r for r in verified_references([], transcript))


def test_contained_references_are_merged():
    """"1. Samuel 17,49" steckt schon in "17,49-51" — nur die weitere Stelle bleibt."""
    from insights import verified_references
    transcript = "Wir lesen 1. Samuel 17, 49-51. Und nochmal 1. Samuel 17, 49. Dazu Johannes 3,16."

    refs = verified_references(["1. Samuel 17, 49-51", "1. Samuel 17, 49", "Johannes 3,16"], transcript)

    assert "1. Samuel 17, 49-51" in refs
    assert "1. Samuel 17, 49" not in refs
    assert "Johannes 3,16" in refs
