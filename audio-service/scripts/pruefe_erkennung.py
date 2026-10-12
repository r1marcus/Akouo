#!/usr/bin/env python3
"""Hält die automatische Erkennung gegen die von Hand gesetzten Marker.

    ~/akouo-audio-venv/bin/python audio-service/scripts/pruefe_erkennung.py 11 14

Ohne Argumente werden alle gestoppten Sessions geprüft, deren WAV noch da ist.
Ändert nichts — liest nur.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys

HIER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIER))

from classifier import (MUSIK_TYPEN, finde_beitraege,      # noqa: E402
                        klassifiziere_bereich)

def _datenbank() -> pathlib.Path:
    """Die Datenbank, die der Dienst benutzt — DATABASE_URL hat Vorrang.

    Ein Rechner aus der Tontechnik-Zeit hat data/tontechnik.db, eine frische
    Installation data/akouo.db. Fest verdrahtet war der alte Name.
    """
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
        pfad = url.split(":///", 1)[1]
        if pfad and pfad != ":memory:":
            return pathlib.Path(pfad).expanduser()
    daten = HIER.parent / "data"
    for name in ("akouo.db", "tontechnik.db"):
        if (daten / name).exists():
            return daten / name
    return daten / "akouo.db"


DB = _datenbank()
TOLERANZ_S = 25.0


def marker_der_session(con, sid):
    s = con.execute("select * from recording_sessions where id=?", (sid,)).fetchone()
    if s is None:
        return None, []
    wav = pathlib.Path(s["wav_path"] or "")
    marker = con.execute(
        "select * from markers where session_id=? and ended_at_ts is not null "
        "order by placed_at_ts", (sid,)).fetchall()
    echte = [(m["placed_at_ts"] + (m["start_nudge"] or 0),
              m["ended_at_ts"] + (m["end_nudge"] or 0),
              m["marker_type"]) for m in marker]
    return (s, wav, echte)


def ablauf_vorlage(con, service_type):
    import json
    r = con.execute("select track_sequence from service_type_templates where service_type=?",
                    (service_type,)).fetchone()
    try:
        return json.loads(r["track_sequence"]) if r and r["track_sequence"] else None
    except (ValueError, TypeError):
        return None


def pruefe(con, sid) -> tuple[int, int, int, int]:
    daten = marker_der_session(con, sid)
    if daten[0] is None:
        print(f"Session {sid}: gibt es nicht")
        return (0, 0, 0, 0)
    s, wav, echte = daten
    if not wav.exists():
        print(f"Session {sid} ({s['service_type']}): WAV fehlt — übersprungen")
        return (0, 0, 0, 0)
    if not echte:
        print(f"Session {sid} ({s['service_type']}): keine abgeschlossenen Marker")
        return (0, 0, 0, 0)

    erwartet = ablauf_vorlage(con, s["service_type"])
    gefunden = finde_beitraege(wav)

    print(f"\n=== Session {sid} — {s['service_type']} — "
          f"{len(echte)} von Hand, {len(gefunden)} automatisch ===")
    print(f"{'von Hand':<12}{'Bereich':>14}   {'automatisch':>14}  {'Abweichung':>20}  Erkennung")

    grenzen_ok = klasse_ok = typ_ok = 0
    zugeordnet = set()
    for start, ende, typ in echte:
        best = min(range(len(gefunden)), key=lambda j: abs(gefunden[j][0] - start),
                   default=None)
        if best is None:
            continue
        gs, ge = gefunden[best]
        passt = abs(gs - start) <= TOLERANZ_S and abs(ge - ende) <= TOLERANZ_S
        grenzen_ok += passt
        if passt:
            zugeordnet.add(best)

        e = klassifiziere_bereich(wav, gs, ge, erwartet)
        soll_klasse = "Musik" if typ in MUSIK_TYPEN else "Sprache"
        if e is None:
            urteil = "keine Einschätzung"
        else:
            klasse_ok += (e.klasse == soll_klasse)
            typ_ok += (e.typ == typ)
            haken = "richtig" if e.typ == typ else f"sagt {e.typ}"
            urteil = f"{e.klasse} ({'ok' if e.klasse == soll_klasse else 'FALSCH'}), {haken}"
        print(f"{typ:<12}{start:>6.0f}-{ende:<7.0f}   {gs:>6.0f}-{ge:<7.0f}  "
              f"{gs-start:>+8.1f}s {ge-ende:>+8.1f}s  {urteil}")

    extra = [g for j, g in enumerate(gefunden) if j not in zugeordnet]
    n = len(echte)
    print(f"  Grenzen {grenzen_ok}/{n} | Sprache-Musik {klasse_ok}/{n} | Typ {typ_ok}/{n}")
    if extra:
        print(f"  {len(extra)} nicht markierte Stellen (dort ist Ton, du exportierst ihn nur nicht):")
        for gs, ge in extra:
            e = klassifiziere_bereich(wav, gs, ge, erwartet)
            print(f"      {gs:>6.0f}-{ge:<7.0f} {ge-gs:>5.0f}s  "
                  f"{e.klasse if e else '?'} / vermutlich {e.typ if e else '?'}")
    return grenzen_ok, klasse_ok, typ_ok, n


if __name__ == "__main__":
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ids = [int(x) for x in sys.argv[1:]]
    if not ids:
        ids = [r["id"] for r in con.execute(
            "select id from recording_sessions where status='stopped' order by id")]

    summe = [0, 0, 0, 0]
    for sid in ids:
        for i, v in enumerate(pruefe(con, sid)):
            summe[i] += v
    if summe[3]:
        print(f"\n=== Gesamt über {len(ids)} Sessions, {summe[3]} Tracks ===")
        print(f"  Grenzen richtig:      {summe[0]}/{summe[3]}")
        print(f"  Sprache/Musik:        {summe[1]}/{summe[3]}")
        print(f"  Track-Typ:            {summe[2]}/{summe[3]}")
