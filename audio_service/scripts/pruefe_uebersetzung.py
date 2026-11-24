"""Übersetzer-Strecke ohne Audio-Dienst prüfen: Testton nach „de“, „ru“ mitlesen.

Läuft mit genau dem Code des Dienstes (uebersetzung.py), aber ohne Dante: Statt des
Stream-Mixes geht ein 440-Hz-Ton hinaus, und was auf „ru“ ankommt, wird nur gemessen.

    ~/akouo-audio-venv/bin/python audio_service/scripts/pruefe_uebersetzung.py --sekunden 20

NUR wenn die Übersetzung im Dashboard aus ist — sonst senden zwei auf „de“. Der Übersetzer
hört während des Laufs den Testton. Zugangsdaten kommen aus der .env und werden nie
ausgegeben.
"""
import argparse
import logging
import queue
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

HIER = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", default=str(HIER / ".env"), help="Pfad der .env (Standard: audio_service/.env)")
    parser.add_argument("--sekunden", type=float, default=20.0)
    args = parser.parse_args()
    if not load_dotenv(args.env):
        print(f".env nicht gefunden: {args.env}")
        return 2
    sys.path.insert(0, str(HIER))
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    import numpy as np
    import recorder
    import uebersetzung
    from config import config

    if not uebersetzung.eingerichtet():
        print("Nicht eingerichtet: UEBERSETZUNG_SERVER oder UEBERSETZUNG_SRT_PASSPHRASE fehlt.")
        return 2

    class Messung:
        """Steht für den Recorder: nimmt das Russisch an und zählt nur."""
        bloecke = 0

        def uebersetzung_ausgang_bereit(self) -> bool:
            return True

        def uebersetzung_einreihen(self, block) -> bool:
            self.bloecke += 1
            return True

        def uebersetzung_ausgang_leeren(self) -> None:
            pass

    halt = threading.Event()

    def testton() -> None:
        """Stereo-s16le im Takt der Abtastrate, wie es der Callback liefern würde."""
        n = config.blocksize
        kanaele = len(config.stream_channels)
        phase = 0
        takt = n / config.sample_rate
        naechster = time.monotonic()
        while not halt.is_set():
            t = (np.arange(n) + phase) / config.sample_rate
            ton = (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
            phase += n
            try:
                recorder._uebs_q.put_nowait(np.repeat(ton[:, None], kanaele, axis=1).tobytes())
            except queue.Full:
                pass
            naechster += takt
            time.sleep(max(0.0, naechster - time.monotonic()))

    messung = Messung()
    strecke = uebersetzung.Uebersetzung(messung)
    print(f"Server {config.uebersetzung_server}:{config.uebersetzung_srt_port}, {args.sekunden:.0f} s")
    st = strecke.starten()
    if not st.get("aktiv"):
        print("Start fehlgeschlagen:", st)
        return 1
    tongeber = threading.Thread(target=testton, daemon=True)
    tongeber.start()
    ende = time.monotonic() + args.sekunden
    try:
        while time.monotonic() < ende:
            time.sleep(2)
            st = strecke.status()
            print(f"  {st['status']:<9} Deutsch {'sendet' if st['sendet_deutsch'] else 'verbindet':<9} "
                  f"Übersetzer {'verbunden' if st['uebersetzer_verbunden'] else '-':<9} "
                  f"Pegel {st['pegel_ru_db'] if st['pegel_ru_db'] is not None else '-'} dB  "
                  f"Blöcke ru {messung.bloecke}  Grund {st['grund'] or '-'}")
    finally:
        halt.set()
        strecke.stoppen()
    print("gestoppt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
