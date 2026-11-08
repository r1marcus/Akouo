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


def test_mehrfach_genanntes_kapitel_bleibt_einmal_mit_genauester_angabe():
    from insights import verified_references
    transcript = "Wir lesen 1. Samuel 17, 49-51. Und nochmal 1. Samuel 17, 49. Dazu Johannes 3,16."

    assert verified_references([], transcript) == ["1. Samuel 17,49-51", "Johannes 3,16"]


def test_gesprochene_stellen_vom_08_11():
    """Die drei Predigttexte vom 08.11., wie Whisper sie geschrieben hat."""
    from insights import stellen_im_text

    assert stellen_im_text("wollen wir uns einen Vers lesen aus dem Matthäus-Evangelium, "
                           "Kapitel 7. Kapitel 7. Ich denke") == ["Matthäus 7"]
    assert stellen_im_text("Evangelium Lukas Kapitel 16 Ab Verse 19 bis 31 einschließen. "
                           "Es war aber ein reicher Mann") == ["Lukas 16,19-31"]
    assert stellen_im_text("aus Psalm 51, die Verse 9. Psalm 51, die Verse 3 bis 5. O Gott ... "
                           "In Epheser 2, 8 und 9, da lesen wir") == ["Psalm 51,3-5", "Epheser 2,8-9"]


def test_kapitel_ohne_buch_ist_keine_stelle():
    from insights import stellen_im_text
    assert stellen_im_text("wie in Kapitel 11 und Kapitel 12 erzählt, 2000 Jahre") == []


def test_fuellsaetze_fliegen_aus_dem_ausschnitt(tmp_path):
    from insights import transcript_slice
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    zeilen = ["Untertitelung des ZDF, 2020", "Vielen Dank.", "Amen. Amen. Amen.",
              "Komm zum Kreuz", "Komm zum Kreuz", "Amen, sagt er, das ist wahr."]
    (tmp_path / "transcript.jsonl").write_text("\n".join(
        json.dumps({"start": i, "end": i + 1, "text": t}) for i, t in enumerate(zeilen)),
        encoding="utf-8")

    assert transcript_slice(wav, 0, 10) == "Komm zum Kreuz Amen, sagt er, das ist wahr."


def test_lieder_werden_nicht_geraten():
    import asyncio
    from insights import analyse
    ergebnis = asyncio.run(analyse("Amen. Beeship, beeship, O dear", "Gebetslied"))

    assert ergebnis["titel"] is None and ergebnis["error"] is None
