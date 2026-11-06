# Plan: Übersetzer-Strecke über den Hetzner-Server

Stand: 6. November 2026 · Entwurf, noch nichts gebaut

## Worum es geht

Ein russischer Übersetzer arbeitet von außerhalb. Heute hört er den öffentlichen Livestream
und schickt seine Übersetzung über LiveVoice. Das hat zwei Schwächen:

- **Verzögerung:** Der Livestream hängt 10 bis 30 Sekunden hinter dem Saal. Die Übersetzung
  kommt mindestens so spät zurück und passt damit weder zum Saal noch zum Stream.
- **Kein Weg zurück nach Dante:** LiveVoice gibt Ton nur über den kostenpflichtigen Export
  (PLUS, RTP/Opus) heraus.

Ziel: Der Übersetzer hört das Deutsch mit weniger als einer Sekunde Verzögerung und spricht
in eine Seite der Gemeinde-App. Seine Stimme liegt in der Gemeinde auf einem eigenen
Dante-Kanal „RU“.

## Aufbau

```
Mac Studio                       Hetzner CX32 (Gemeinde-App)             Übersetzer (Browser)
──────────                       ───────────────────────────             ────────────────────
Dante-Paar (Deutsch)
  → Audio-Dienst
  → ffmpeg, Opus ──SRT──────────▶ mediamtx, Pfad „de“ ──WebRTC (WHEP)──▶ hört Deutsch

Dante-TX-Kanal „RU“                                                       Mikrofon
  ◀ Audio-Dienst                                                            │
  ◀ ffmpeg ◀────────SRT───────── mediamtx, Pfad „ru“ ◀──WebRTC (WHIP)──────┘
```

Der Mac baut **alle Verbindungen selbst nach außen** auf. Am Router der Gemeinde wird nichts
freigegeben. Der Server ist die einzige Stelle mit öffentlicher Adresse.

## Bausteine

### 1. Server: mediamtx neben der Gemeinde-App

- **Neuer Dienst** `mediamtx` in `Church_Tool/docker/docker-compose.yml` (offizielles Image,
  feste Version). Er läuft getrennt von Django, Postgres und Redis und braucht für zwei
  Audiospuren mit je etwa 64 kbit/s kaum Leistung.
- **Pfade:**
  - `de`: Der Mac sendet per SRT, der Übersetzer hört per WebRTC.
  - `ru`: Der Übersetzer sendet per WebRTC, der Mac holt den Ton per SRT ab.
- **Caddy:** `handle /uebersetzung/*` wird an `mediamtx:8889` weitergeleitet (Signalisierung
  für WHIP und WHEP). Die Seite und der Medienserver liegen damit unter derselben Domain,
  CORS ist nicht nötig, und Caddy stellt das HTTPS-Zertifikat, das der Browser fürs Mikrofon
  verlangt.
- **Offene Ports** (Hetzner-Cloud-Firewall und `ports:` in Compose):
  - UDP 8189 für die WebRTC-Medien
  - UDP 8890 für SRT
  - In mediamtx `webrtcAdditionalHosts` auf die öffentliche IP setzen. Auf einem Server mit
    öffentlicher Adresse sollte WebRTC ohne TURN-Server auskommen. Klappt es aus einem
    bestimmten Netz nicht, wird TURN nachgerüstet.
- **Zugriffsschutz:**
  - SRT ist für beide Pfade mit einer Passphrase verschlüsselt (`srtPublishPassphrase`,
    `srtReadPassphrase`). Die Passphrase steht nur in den `.env`-Dateien auf dem Server und
    auf dem Mac.
  - WebRTC läuft über `authMethod: http`. mediamtx fragt bei jedem Zugriff einen neuen
    Django-Endpunkt `/api/uebersetzung/auth/` und reicht das JWT der App mit. Django
    prüft, ob der Nutzer Übersetzer ist, und erlaubt ihm nur `read de` und `publish ru`.

### 2. Gemeinde-App: Seite für den Übersetzer

- **Berechtigung:** Am einfachsten über eine Gruppe „Übersetzer“, die es im Gruppenmodell
  ohnehin gibt. Alternativ über ein eigenes Recht am Nutzer. Das muss vor dem Bau
  entschieden werden.
- **Neue Seite** `frontend/src/app/(app)/uebersetzen/`, sichtbar nur für Übersetzer:
  - **Hören:** Ein WHEP-Player für `de` mit eigenem Lautstärkeregler.
  - **Senden:** Mikrofon auswählen, dann ein großer Knopf „Senden / Stopp“. Das Mikrofon
    geht per WHIP an `ru`, mono, Opus, 48 kHz.
  - **Anzeigen:** Pegel des eigenen Mikrofons, Verbindungsstatus beider Richtungen und der
    Hinweis „Bitte Kopfhörer benutzen“. Ohne Kopfhörer läuft das Deutsch zurück ins
    Mikrofon.
  - **Browser-Einstellungen:** Echo-Unterdrückung aus, automatische Pegelanpassung aus,
    Rauschunterdrückung als Schalter (voreingestellt an). Einen Wake Lock setzen, damit
    der Bildschirm nicht ausgeht.
  - **Bei Abbruch:** Die Seite verbindet sich von selbst neu und zeigt das deutlich an.
- **Backend:** Der Endpunkt `/api/uebersetzung/auth/` für mediamtx, zu finden in der
  bestehenden App `livestream` oder in einer neuen App `uebersetzung`. Tests wie bei den
  anderen Apps.

### 3. Mac Studio: neuer Teil im Audio-Dienst

Ein neues Modul `audio-service/uebersetzung.py` nach dem Muster von `telefon.py`:

- **Hinweg (Deutsch → Server):**
  - Ein eigener Abgriff `_uebs_q` im Recorder, genau wie `_tel_q`. Der Ton kommt also aus
    dem einen CoreAudio-Strom, die Dante-Karte wird nicht ein zweites Mal geöffnet.
  - Der Ton geht als PCM an ffmpeg, wird dort zu Opus und per SRT an `de` geschickt.
  - Die Quelle ist ein eigenes Kanalpaar (`UEBERSETZUNG_QUELLE`), voreingestellt ist das
    Stream-Paar 50+51.
- **Rückweg (Server → Dante):**
  - ffmpeg holt `ru` per SRT ab und gibt PCM aus.
  - Ein `OutputStream` auf der Dante Virtual Soundcard spielt den Ton auf den Kanal
    `UEBERSETZUNG_DANTE_KANAL`. Die DVS hat 64 Ausgänge.
  - Die beiden Uhren werden wie in `fuelle_ausgabe()` ausgeglichen: Fehlt Ton, wird mit
    Stille aufgefüllt, staut sich Ton, wird er verworfen. Ohne Sender läuft also einfach
    Stille.
- **Ausfallsicherheit:** Bricht eine Richtung ab, wird mit Pausen neu verbunden, wie bei
  `NEUWAHL_PAUSEN`.
- **Steuerung:**
  - Die Strecke startet mit „Stream starten“ oder mit einem eigenen Schalter im Dashboard.
  - Das Dashboard zeigt den Status, etwa „Übersetzer sendet“ oder „verbunden, Stille“,
    und den Pegel des Russisch-Kanals.
- **Einstellungen in `.env`:**
  - `UEBERSETZUNG_SERVER`
  - `UEBERSETZUNG_SRT_PASSPHRASE`
  - `UEBERSETZUNG_QUELLE`
  - `UEBERSETZUNG_DANTE_KANAL`
- **Ausgabe auf die DVS:** Die Regel im Projekt lautet, dass nur ein CoreAudio-Strom das
  Gerät besitzt. Am saubersten ist es daher, den Strom des Recorders auf **Ein- und Ausgabe
  zugleich** umzustellen (`sd.RawStream`, gleiche Uhr). Dann füllt derselbe Callback mit
  billiger NumPy-Arbeit den Ausgabekanal aus einer Queue. Die Pufferlogik bleibt außerhalb
  des Callbacks.
  - Notlösung: ein eigener `OutputStream` auf der DVS. Ob er über Stunden ohne Drift und
    ohne Knackser neben dem Eingang läuft, klärt der Durchstich.
  - Diese Umstellung betrifft das Herz der Aufnahme. Sie wird deshalb einzeln getestet und
    nur außerhalb eines Gottesdienstes eingespielt.
- **Texte im Dashboard:** Alle Status- und Fehlermeldungen bekommen Schlüssel in
  `api/sprachen/*.json` (de, en, ru), wie bei `telefon.py`.

### 4. Dante Controller

Den neuen DVS-Sendekanal benennen („RU Übersetzung“) und zum Ziel routen. Wohin, ist noch
offen (siehe unten).

## Verzögerung (geschätzt)

| Strecke | Dauer |
|---|---|
| Mac → Server (SRT, Puffer etwa 120 ms) | ~0,2 s |
| Server → Übersetzer (WebRTC) | ~0,2 s |
| Übersetzer → Server → Mac | ~0,4 s |
| **Hin und zurück, ohne die Denkpause des Übersetzers** | **etwa 0,8 s** |

Zum Vergleich: Heute sind es 10 bis 30 Sekunden.

## Reihenfolge

1. ~~**Klären**~~: erledigt bis auf den Dante-Kanal, siehe Entscheidungen.
2. **Durchstich ohne App** (Server und SRT erledigt, Browser-Test und Dante-Ausgabe offen,
   siehe „Stand Durchstich“):
   - mediamtx auf dem Server starten, mit Firewall und Passphrase.
   - Auf dem Mac ffmpeg von Hand: Deutsch hin, `ru` zurück auf einen DVS-Kanal.
   - Der Übersetzer testet mit der eingebauten Testseite von mediamtx.
   - Danach ist klar, ob Verzögerung, Klang und WebRTC aus seinem Netz passen.
3. **Mac-Dienst** `uebersetzung.py` mit Tests (Pufferlogik, Neuverbindung, Status).
4. **App-Seite und Berechtigung**, deployen.
5. **Probelauf an einem Sonntag parallel zum alten Weg.** LiveVoice läuft weiter, bis
   ausdrücklich umgeschaltet wird.
6. **Doku:** `docs/stand.md`, `docs/betrieb.md` und eine Kurzanleitung für den Übersetzer.

Grob ein bis zwei Tage bis zum Probelauf, je nachdem, was der Durchstich zeigt.

## Stand Durchstich (6. November 2026)

**Läuft auf dem Server:**

- **mediamtx 1.21.2** liegt als eigener Compose-Dienst in `/root/uebersetzung/` auf dem
  Server der Gemeinde-App und hängt im Netz `docker_default`, damit Caddy ihn erreicht.
  Begrenzt auf 256 MB und eine halbe CPU.
  - Ausgeschaltet ist alles, was nicht gebraucht wird: RTSP, RTMP, HLS, MoQ, API und
    Aufnahme.
  - Der Container gehört zu keinem Repo. Die Konfiguration enthält Passwörter und hat die
    Rechte 600.
- **Caddy:** `handle_path /uebersetzung/*` leitet an `mediamtx:8889` weiter. Die Änderung
  steht nur in der Caddyfile auf dem Server, wie dort schon die Domain.
  - Sicherung: `docker/Caddyfile.bak.20261106-183953`.
  - Die Datei ist als Einzeldatei eingehängt. Sie wurde deshalb an Ort und Stelle
    geändert und nicht neu angelegt, und Caddy wurde ohne Unterbrechung neu geladen.
  - Die App antwortet danach unverändert.
- **Zugänge:** Es gibt zwei Nutzer:
  - `akouo` darf `de` senden und `ru` lesen.
  - `uebersetzer` darf `de` lesen und `ru` senden.
  - Zusätzlich verschlüsselt eine SRT-Passphrase das Senden auf `de` und das Lesen von
    `ru`.
  - Alle Werte stehen in `audio-service/.env` (`UEBERSETZUNG_*`) und auf dem Server in
    `/root/uebersetzung/mediamtx.yml`.

**Getestet:**

- **SRT in beide Richtungen** vom Mac aus, über das Internet:
  - Ein Testton geht nach `de`.
  - Ein Testton geht als Übersetzer nach `ru`, und der Mac liest ihn zurück: 660 Hz kommen
    sauber an. UDP 8890 ist also offen.
- **Rechte:** Der Mac darf `de` nicht lesen, mediamtx meldet „authentication failed“.
- **Testseiten hinter Caddy:** `/uebersetzung/de/` (hören) und `/uebersetzung/ru/publish`
  (senden) laden. Sie verweisen nur relativ auf ihre Skripte, der Präfix stört also nicht.

**Noch nicht getestet:** WebRTC im Browser, also UDP 8189. Das braucht einen echten Browser
mit Mikrofon, siehe unten.

**Erkenntnisse für den Mac-Dienst:**

- Das ffmpeg aus Homebrew (9.0) hat **kein SRT** mehr. ffmpeg erzeugt deshalb nur MPEG-TS
  über eine Pipe, die SRT-Verbindung übernimmt `srt-live-transmit` aus `brew install srt`.
  - Wichtig ist `-chunk:1316` (7 × 188 Byte). Mit der Voreinstellung von 1456 Byte
    zerfallen die TS-Pakete, und mediamtx meldet Dekodierfehler.
- Passwörter erscheinen in Fehlermeldungen von ffmpeg und srt im Klartext, weil sie in der
  URL stehen. Der Dienst darf diese Ausgaben nur maskiert loggen, wie bei
  `public_target()`.
- Der Server ist laut `nproc` und `free` ein Rechner mit 2 vCPU und 4 GB, nicht der CX32
  aus der Church_Tool-Doku. Für zwei Opus-Spuren reicht das trotzdem.

## Entscheidungen (6. November 2026)

1. **Ziel des Russisch:** ein Dante-Kanal. Welcher, steht noch aus
   (`UEBERSETZUNG_DANTE_KANAL`). Weiter ins Internet, also als Icecast oder nach LiveVoice,
   muss es nicht.
2. **Der Übersetzer hört den Stream-Mix** (Dante 50+51). `UEBERSETZUNG_QUELLE` ist damit
   standardmäßig das Stream-Paar.
3. **Nur Russisch.** Es gibt nur die Pfade `de` und `ru`. Mehrsprachigkeit wird nicht
   vorbereitet.
4. **Keine Sprechverbindung** vom Tontechniker zum Übersetzer.
5. **Keine Aufnahme des Russisch.** Der Ton läuft nur durch und wird nirgends gespeichert,
   weder auf dem Server noch auf dem Mac. mediamtx bekommt `record: no`.

## Noch offen

- Die Nummer des Dante-Kanals für Russisch.
- Wer bekommt in der Gemeinde-App das Recht „Übersetzer“: eine Gruppe oder ein Recht am
  Nutzer? Vorschlag: eine Gruppe „Übersetzer“.

## Risiken

- **Internet des Übersetzers:** Ein schwacher Upload führt zu Aussetzern. WebRTC verkraftet
  Paketverluste gut, aber ein Kabel oder stabiles WLAN beim Übersetzer bleibt Voraussetzung.
- **Produktionsserver:** mediamtx läuft auf demselben Server wie die Gemeinde-App. Der
  Container wird deshalb in RAM und CPU begrenzt, damit er die App nicht beeinträchtigen
  kann.
- **Browser im Hintergrund:** Schaltet der Übersetzer den Tab weg oder sperrt das Handy,
  kann der Ton aussetzen. Gegenmittel sind der Wake Lock und der klare Hinweis auf einen
  Laptop.
- **Eingriffe am Mac während eines Gottesdienstes:** Der neue Dienst wird nur außerhalb von
  Aufnahmen eingespielt und neu gestartet.
