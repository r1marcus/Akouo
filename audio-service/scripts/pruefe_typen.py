#!/usr/bin/env python3
"""Wie gut trifft „Neu erkennen" den Typ? Gegen die Marker, die von Hand stehen.

    ~/tontechnik-audio-venv/bin/python audio-service/scripts/pruefe_typen.py            # alle
    ~/tontechnik-audio-venv/bin/python audio-service/scripts/pruefe_typen.py --ohne-modell 28 31

Jeder abgeschlossene Marker wird dem erkannten Beitrag zugeordnet, der ihn am weitesten
überlappt. Gezählt wird, ob der Typ stimmt, und eine Verwechslungsliste zeigt, was mit
was verwechselt wird. Liest nur, ändert nichts. Braucht Rechenzeit und — ohne
--ohne-modell — das Sprachmodell: nicht während eines Gottesdienstes laufen lassen.
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sqlite3
import sys
import time

HIER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIER))
sys.path.insert(0, str(HIER / "scripts"))

import automode  # noqa: E402
from pruefe_erkennung import DB, marker_der_session  # noqa: E402


def ueberlappung(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sessions", nargs="*", type=int)
    p.add_argument("--ohne-modell", action="store_true", help="Stufe 3 auslassen")
    args = p.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ids = args.sessions or [r["id"] for r in con.execute(
        "select id from recording_sessions where status='stopped' order by id")]

    richtig = gesamt = 0
    verwechslung = collections.Counter()
    je_typ = collections.defaultdict(lambda: [0, 0])
    for sid in ids:
        daten = marker_der_session(con, sid)
        if daten[0] is None:
            continue
        s, wav, echte = daten
        if not wav.exists() or not echte:
            continue
        if not (wav.parent / "transcript.jsonl").exists():
            continue
        t0 = time.time()
        beitraege = automode.nachlauf(wav, mit_modell=not args.ohne_modell)
        r = n = 0
        for start, ende, typ in echte:
            if ende - start < 20:
                continue
            best = max(beitraege, default=None,
                       key=lambda b: ueberlappung(start, ende, b["start_s"], b["ende_s"] or ende))
            gefunden = best["typ"] if best and ueberlappung(
                start, ende, best["start_s"], best["ende_s"] or ende) > 0 else "—"
            n += 1
            je_typ[typ][1] += 1
            if gefunden == typ:
                r += 1
                je_typ[typ][0] += 1
            else:
                verwechslung[(typ, gefunden)] += 1
        richtig += r
        gesamt += n
        print(f"Session {sid:>3} {s['service_type']:<16} Typ {r:>2}/{n:<2}  ({time.time()-t0:.0f} s)",
              flush=True)

    if gesamt:
        print(f"\nGesamt: {richtig}/{gesamt} = {richtig/gesamt:.0%}"
              f"  ({'ohne' if args.ohne_modell else 'mit'} Sprachmodell)")
        print("\nJe Typ (richtig/von Hand):")
        for typ, (r, n) in sorted(je_typ.items(), key=lambda x: -x[1][1]):
            print(f"  {typ:<18} {r:>3}/{n:<3}")
        print("\nHäufigste Verwechslungen (von Hand → erkannt):")
        for (soll, ist), k in verwechslung.most_common(12):
            print(f"  {soll:<18} → {ist:<18} {k}x")


if __name__ == "__main__":
    main()
