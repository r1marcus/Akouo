"""Vermeldungen: Datum ausrechnen, Belege prüfen, hochladen und zurücknehmen.

Die Gemeinde-App ist mit httpx.MockTransport nachgebaut — es geht nichts hinaus.
"""
import json
import sys
from datetime import date

import httpx

sys.path.insert(0, "audio-service")

import vermeldungen as v  # noqa: E402

SONNTAG = date(2026, 11, 8)        # ein Sonntag

TRANSKRIPT = ("Liebe Geschwister, nächsten Samstag um 17 Uhr ist Straßenevangelisation in Freiburg. "
              "Bitte betet für Aaron, er liegt auf der Intensivstation. "
              "Die Kollekte heute ist für die Mission.")


def test_datum_gegen_den_gottesdienst_ausrechnen():
    assert v.datum_aus("nächsten Samstag um 17 Uhr", SONNTAG) == date(2026, 11, 14)
    assert v.datum_aus("am Sonntag", SONNTAG) == date(2026, 11, 15)          # nicht heute
    assert v.datum_aus("übernächsten Freitag", SONNTAG) == date(2026, 11, 20)
    assert v.datum_aus("nächste Woche Dienstag", SONNTAG) == date(2026, 11, 10)
    assert v.datum_aus("morgen Abend", SONNTAG) == date(2026, 11, 9)
    assert v.datum_aus("Sonntag morgens", SONNTAG) == date(2026, 11, 15)     # "morgens" ist nicht "morgen"
    assert v.datum_aus("am 23.", SONNTAG) == date(2026, 11, 23)
    assert v.datum_aus("am 3.", SONNTAG) == date(2026, 12, 3)                # schon vorbei → nächster Monat
    assert v.datum_aus("am 6. Januar", SONNTAG) == date(2027, 1, 6)
    assert v.datum_aus("am 24.12.", SONNTAG) == date(2026, 12, 24)
    assert v.datum_aus("demnächst", SONNTAG) is None


def test_belege_muessen_im_transkript_stehen():
    assert v.belegt("nächsten Samstag um 17 Uhr ist Straßenevangelisation", TRANSKRIPT)
    assert not v.belegt("Gemeindeversammlung am Mittwoch um 19 Uhr", TRANSKRIPT)


def test_aufbereiten_waehlt_vorsichtig_aus():
    roh = [
        {"art": "termin", "titel": "Straßenevangelisation", "wann": "nächsten Samstag um 17 Uhr",
         "uhrzeit": "17:00", "ort": "Freiburg", "beleg": "nächsten Samstag um 17 Uhr ist Straßenevangelisation in Freiburg"},
        {"art": "gebetsanliegen", "titel": "Gebet für Aaron", "beleg": "Bitte betet für Aaron, er liegt auf der Intensivstation"},
        {"art": "termin", "titel": "Erfunden", "wann": "Mittwoch", "beleg": "Gemeindeversammlung am Mittwoch um 19 Uhr"},
        {"art": "termin", "titel": "Ohne Datum", "wann": "demnächst", "beleg": "Die Kollekte heute ist für die Mission"},
    ]
    e = v.aufbereiten(roh, TRANSKRIPT, SONNTAG)
    assert [x["titel"] for x in e] == ["Straßenevangelisation", "Gebet für Aaron", "Ohne Datum"]
    assert (e[0]["datum"], e[0]["uhrzeit"], e[0]["ausgewaehlt"], e[0]["pruefen"]) == ("2026-11-14", "17:00", True, False)
    assert e[1]["ausgewaehlt"] is False          # Gebetsanliegen nur auf ausdrücklichen Wunsch
    assert (e[2]["ausgewaehlt"], e[2]["pruefen"]) == (False, True)


def _app(kalender_am_tag=()):
    gesendet = []

    def antwort(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"title": t} for t in kalender_am_tag]})
        if request.method == "DELETE":
            gesendet.append(("DELETE", request.url.path))
            return httpx.Response(204)
        gesendet.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={"id": f"id-{len(gesendet)}"})
    return httpx.Client(base_url="https://app.test", transport=httpx.MockTransport(antwort)), gesendet


def test_hochladen_kalender_brett_und_zuruecknehmen():
    client, gesendet = _app()
    eintraege = [
        {"art": "termin", "titel": "Straßenevangelisation", "datum": "2026-11-14", "uhrzeit": "17:00",
         "ende": "", "ort": "Freiburg", "text": "In der Innenstadt.", "ausgewaehlt": True},
        {"art": "hinweis", "titel": "Kollekte für die Mission", "datum": "", "text": "", "ausgewaehlt": True},
        {"art": "gebetsanliegen", "titel": "Gebet für Aaron", "datum": "", "text": "", "ausgewaehlt": False},
    ]
    ergebnis = v.hochladen(eintraege, SONNTAG, client)
    pfad, termin = gesendet[0]
    assert pfad == "/api/calendar/"
    assert (termin["date"], termin["start_time"], termin["visibility"]) == ("2026-11-14", "17:00", "closed")
    assert "Aus den Vermeldungen vom 08.11.2026" in termin["description"]
    assert gesendet[1][0] == "/api/bulletin/" and gesendet[1][1]["category"] == "ankuendigung"
    assert len(gesendet) == 2                      # das Gebetsanliegen war abgewählt
    assert [e["status"] for e in ergebnis] == ["eingetragen", "eingetragen"]

    zurueck = v.zuruecknehmen(ergebnis, client)
    assert [e["status"] for e in zurueck] == ["zurueckgenommen", "zurueckgenommen"]
    assert ("DELETE", "/api/calendar/id-1/") in gesendet


def test_kein_doppelter_termin():
    client, gesendet = _app(kalender_am_tag=["Straßenevangelisation Freiburg"])
    ergebnis = v.hochladen([{"art": "termin", "titel": "Straßenevangelisation", "datum": "2026-11-14",
                             "uhrzeit": "17:00", "ausgewaehlt": True}], SONNTAG, client)
    assert ergebnis[0]["status"] == "schon_da" and gesendet == []
