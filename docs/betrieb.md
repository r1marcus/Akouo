# Betrieb auf dem Mac Studio

Wie Akouo auf der produktiven Hardware tatsächlich läuft, wie es automatisch startet,
wie Dashboard und Pegelanzeige funktionieren — und was bei Problemen zu tun ist.

> `README.md` beschreibt den Aufbau allgemein, für einen beliebigen Mac. Hier steht, wie
> **dieser** Rechner eingerichtet ist, mit seinen Pfaden und seinen Eigenheiten.

Stand: 18. Oktober 2026.

---

## 1. Überblick

Der **Mac Studio** (`<rechner>.local`, Benutzer `<benutzer>`) ist die produktive Hardware.
Docker ist nicht installiert — beide Dienste laufen nativ.

| Dienst | Port | Erreichbar | Läuft als | Python-Umgebung |
|---|---|---|---|---|
| Web-API + Dashboard (`api/`) | 8000 | ganzes Netzwerk (`0.0.0.0`) | LaunchDaemon `de.akouo.api` | `<repo>/.venv` |
| Audio-Service (`audio_service/`) | 8001 | nur lokal (`127.0.0.1`) | LaunchAgent `de.akouo.audio` | `~/akouo-audio-venv` (hier: `~/tontechnik-audio-venv`) |
| whisper-server (Transkription) | 8090 | nur lokal | LaunchAgent `de.akouo.whisper` | — (Homebrew `whisper-cpp`) |
| Ollama (KI-Auswertung) | 11434 | nur lokal | LaunchAgent `homebrew.mxcl.ollama` | — |
| Dashboard im Browser öffnen | — | — | LaunchAgent `de.akouo.dashboard` | — |

> **Dieser Rechner läuft noch unter den alten Namen.** Er wurde vor der Umbenennung von
> *Tontechnik* auf *Akouo* eingerichtet und trägt bis zum nächsten Lauf der Installer die
> Labels `de.tontechnik.api`, `com.tontechnik.audio`, `de.tontechnik.whisper`,
> `de.tontechnik.dashboard` — und schreibt seine Logs nach `/tmp/tontechnik-*.log` statt
> nach `~/Library/Logs/Akouo/`. Die Installer entladen die alten Labels und löschen deren
> Plists, bevor sie die neuen laden; der Umstieg gehört zum Aktivierungsschritt in
> **Abschnitt 8a**, nicht davor und nicht an einem Gottesdienst-Wochenende.
>
> Bis dahin gilt beim Lesen: `de.akouo.audio` → `com.tontechnik.audio`, jedes andere
> `de.akouo.X` → `de.tontechnik.X`, `~/Library/Logs/Akouo/audio.log` →
> `/tmp/tontechnik-audio.log` (ebenso `audio-error`, `whisper`, `dashboard`) und
> `~/Library/Logs/Akouo/api.log` → `~/Logs/tontechnik-api.log`. Datenbank und venv behalten
> ihren Namen auch nach dem Umstieg.

Der Browser spricht nur mit Port 8000. Die API leitet Aufnahme-, Stream-, Pegel-, Transkript- und
KI-Anfragen an den Audio-Service weiter; dieser wiederum an whisper-server und Ollama.

**Signalweg:** Mischpult → Dante → Dante Virtual Soundcard (64 Kanäle, 48 kHz) → Audio-Service.
Kanal 44+45 werden aufgenommen und transkribiert, Kanal 50+51 gehen als Livestream an Icecast
(seit 25.10.2026 mit eigener Stream-Mischung; vom 18. bis 25.10. lief der Stream auf 44+45,
vorher auf 46+47).

---

## 2. Autostart

### Was beim Hochfahren passiert

1. **Beim Booten** (noch vor der Anmeldung) startet die Web-API über
   `/Library/LaunchDaemons/de.akouo.api.plist`.
2. **Nach der Anmeldung** startet der Audio-Service (braucht die Benutzersitzung für den
   Mikrofonzugriff) und das Dashboard öffnet sich im Browser, sobald die API antwortet.

Damit das ohne Eingriff klappt, muss die **automatische Anmeldung** aktiv sein:
Systemeinstellungen → Benutzer:innen & Gruppen → „Automatisch anmelden als" → `<benutzer>`.
(Geht nur, solange FileVault aus ist — Stand heute: aus.)

### Einrichten — zwei Skripte

Die Plists und Startskripte liegen im Repository, mit Platzhaltern statt Pfaden. Eingesetzt
werden sie von den beiden Installern; von Hand ist daran nichts zu tun.

```bash
# API, whisper-server, Dashboard. Fragt nach dem Passwort — der API-Daemon
# gehoert root. Ohne AKOUO_WHISPER_MODEL wird whisper uebersprungen.
AKOUO_WHISPER_MODEL=~/ai/models/ggml-large-v3-turbo.bin bash autostart/install.sh

# Audio-Dienst, braucht kein sudo
bash audio_service/install.sh
```

Beide Skripte sind wiederholbar: sie entladen das jeweilige Label, schreiben die Plist neu und
laden sie wieder. **Beide entladen auch die Vorgänger** aus der Tontechnik-Zeit und löschen
deren Plists — ohne das liefen zwei whisper-Server auf Port 8090 und, schlimmer, zwei
Audio-Dienste auf demselben CoreAudio-Eingang.

Was `autostart/install.sh` nicht rät, ist die **Datenbank**: Es benutzt eine vorhandene Datei
und legt nur bei einer frischen Installation `data/akouo.db` an. Auf diesem Rechner heißt sie
noch `data/tontechnik.db` und bleibt es auch — der Name steht in keinem Code, nur im
Startskript. Erzwingen lässt sich ein anderer Pfad über `AKOUO_DB`.

Umstellbar sind außerdem `AKOUO_LISTEN_HOST` (Standard `0.0.0.0` — das Dashboard ist damit im
ganzen Netz offen und hat **keine Anmeldung**, siehe `SECURITY.md`), `AKOUO_AUDIO_VENV` und
`AKOUO_SKIP_API`, falls der Daemon nicht angefasst werden soll.

### Web-API (LaunchDaemon)

- `/Library/LaunchDaemons/de.akouo.api.plist` — `RunAtLoad` + `KeepAlive`, läuft als `<benutzer>`.
  LaunchDaemon und nicht LaunchAgent, weil die API schon vor der Anmeldung laufen soll.
- startet `~/run-akouo-api.sh`, erzeugt aus `autostart/run-api.sh.template`:

```bash
#!/bin/bash
export DATABASE_URL="sqlite+aiosqlite:///<repo>/data/tontechnik.db"
export AUDIO_SERVICE_URL="http://127.0.0.1:8001"
cd <repo> || exit 1
exec ./.venv/bin/uvicorn main:app --app-dir api --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 3
```

- Log: `~/Library/Logs/Akouo/api.log`
- Der **absolute** Pfad in `DATABASE_URL` ist nicht Geschmackssache: der Standard in
  `api/database.py` ist relativ zum Arbeitsverzeichnis, und aus einem Unterordner gestartet legt
  der Dienst eine zweite, leere Datenbank an statt die vorhandene zu öffnen.
- `--timeout-graceful-shutdown 3` ist wichtig: Jedes offene Dashboard hält eine Dauerverbindung
  für die Pegelanzeige. Ohne Zeitlimit wartet uvicorn beim Beenden endlos darauf
  („Waiting for connections to close") und der Neustart hängt.

**Neu starten** (z. B. nach Änderungen an Python-Code in `api/`) — kein `sudo` nötig,
`KeepAlive` startet den Prozess sofort neu:

```bash
kill $(pgrep -f "uvicorn main:app")
```

Templates (`api/templates/`) und statische Dateien werden ohne Neustart neu geladen.

### Audio-Service (LaunchAgent)

Einrichtung einmalig — Python **3.12 genau**, nicht neuer:

```bash
/opt/homebrew/bin/python3.12 -m venv ~/akouo-audio-venv
~/akouo-audio-venv/bin/pip install -r audio_service/requirements.txt
bash audio_service/install.sh
```

Der Installer nimmt ein vorhandenes `~/tontechnik-audio-venv`, wenn es da ist — das venv wird
bewusst nicht umgezogen, darin stecken die übersetzten Pakete von `sounddevice` und `numpy`.

- Plist: `~/Library/LaunchAgents/de.akouo.audio.plist`
- Logs: `~/Library/Logs/Akouo/audio.log`, `~/Library/Logs/Akouo/audio-error.log`

Neu starten:

```bash
launchctl kickstart -k gui/$(id -u)/de.akouo.audio
```

### Dashboard im Browser öffnen (LaunchAgent)

`autostart/open-dashboard.sh` wartet bis zu zwei Minuten auf `http://localhost:8000/healthz` und
öffnet dann den Standardbrowser. Ein anderer Browser über `AKOUO_DASHBOARD_BROWSER`, eine andere
Adresse über `AKOUO_DASHBOARD_URL`. Eingerichtet wird der Agent von `autostart/install.sh`.

Log: `~/Library/Logs/Akouo/dashboard.log`

---

## 2a. Sprache der Oberfläche

Das Dashboard spricht **Deutsch, Englisch und Russisch**. Umgeschaltet wird oben in der
Kopfzeile über die drei Kürzel `DE EN RU`.

**Die Wahl gilt pro Gerät, nicht für die Anlage.** Das ist der Unterschied zur
Gottesdienst-Auswahl: Die gilt überall gleich, weil alle denselben Gottesdienst aufnehmen.
Die Sprache ist persönlich — zwei Leute können dieselbe Aufnahme bedienen, einer auf
Deutsch, einer auf Russisch. Gespeichert wird sie in einem Cookie, ein Jahr lang; ein neues
Tablet beginnt auf Deutsch.

Die Browsersprache wird bewusst **nicht** ausgewertet. Ein Tablet, das auf Englisch steht,
würde die Oberfläche beim ersten Aufruf englisch machen — und wer dann Deutsch sucht, sucht
es in einer Sprache, die er nicht erwartet hat.

**Was nicht übersetzt wird**, und warum:

| | |
|---|---|
| Transkript, Themen, KI-Zusammenfassungen | Die stehen in der Sprache, in der gesprochen wurde. Eine Übersetzung wäre eine Behauptung über den Inhalt. |
| Namen der Gottesdienst- und Beitragsarten | An ihnen hängt Logik — Anker der Zuordnung, Ansage-Regeln, NAS-Vorlagen. Übersetzt wird nur der **Anzeigename**, siehe unten. |
| Checklistenpunkte | Selbst eingegebener Text. Er steht so da, wie er eingetippt wurde. |
| Meldungen des Audio-Dienstes | Der Dienst kennt die Sprache des Geräts nicht. Was über die API kommt, ist deutsch. |

### Anzeigenamen der Arten

Auf der Einstellungsseite hat jede Gottesdienst- und Beitragsart neben ihrem Namen zwei
kleine Felder, `EN` und `RU`. Dort steht nur die Beschriftung; der Name links bleibt
deutsch und trägt die Logik. Deshalb sind die beiden Felder auch bei **geschützten** Arten
änderbar — geschützt ist der Name, nicht die Beschriftung daneben.

Leer heißt: den deutschen Namen anzeigen. Eine selbst angelegte Art beginnt leer und zeigt
überall ihren deutschen Namen, bis jemand eine Übersetzung einträgt.

### Eine Übersetzung ändern

Die Texte stehen in `api/sprachen/de.json`, `en.json` und `ru.json`. Deutsch ist die
Ausgangssprache **und zugleich der Schlüssel**: Links steht der deutsche Satz, rechts die
Übersetzung. Fehlt ein Eintrag, erscheint Deutsch — nie ein Platzhalter.

Die Dateien werden bei jeder Änderung neu gelesen, ein Dienst-Neustart ist nicht nötig.
Ein Tippfehler in der Sprachdatei macht die Oberfläche nicht kaputt: Lässt sich die Datei
nicht lesen, gilt Deutsch.

> **Die russische Fassung stammt nicht von einem Muttersprachler** und ist noch nicht
> gegengelesen. Wer sie prüft, ändert die Werte in `ru.json` — die Schlüssel links bleiben
> unangetastet, sonst findet die Oberfläche den Satz nicht mehr.

---

## 3. Konfiguration

| Variable | Wo | Standard | Zweck |
|---|---|---|---|
| `AUDIO_SERVICE_URL` | Umgebung der API | `http://127.0.0.1:8001` | Adresse des Audio-Service. Früher fest `host.docker.internal` — das gibt es ohne Docker nicht, deshalb schlugen Start/Marker/Export fehl. |
| `DATABASE_URL` | `~/run-akouo-api.sh` | `./data/akouo.db` (hier: `data/tontechnik.db`) | SQLite-Datenbank. Im Betrieb immer absolut, siehe Abschnitt 2. |
| `AUDIO_DEVICE_NAMES` | Umgebung des Audio-Service | `Dante,Focusrite` | Namensteile der Audiogeräte, in dieser Reihenfolge durchsucht. Erstes Gerät mit genug Eingängen gewinnt. |
| `AUDIO_SAMPLE_RATE` | Umgebung des Audio-Service | `48000` | Abtastrate der Aufnahme; muss zur Einstellung der Soundkarte passen (Dante läuft mit 48000). |
| `AUDIO_RECORD_CHANNELS` | `audio_service/.env` | `1,2` (hier: `44,45`) | Kanalpaar für die Aufnahme, 1-basiert wie in Dante Controller. |
| `STREAM_URL` | `audio_service/.env` | leer | Icecast-Ziel **mit Zugangsdaten**, aktuell `icecast://…@<icecast-host>:8000/<mount>`. Gehört nicht ins Repo. Fehlt in den Einstellungen ein Ziel, nutzt die API dieses hier. |
| `AUDIO_STREAM_CHANNELS` | `audio_service/.env` | `1,2` (hier: `50,51`) | Kanalpaar für den Livestream. Darf dasselbe Paar wie die Aufnahme sein. |
| `NAS_*` | `audio_service/.env` | leer (hier: gesetzt) | NAS über SMB, seit dem 16.10.2026 aktiv. Das Dienstkonto braucht auf der Freigabe **Lesen/Schreiben** — ein „Kein Zugriff“ bei ihm oder einer seiner Gruppen sperrt es aus, auch als Admin. Prüfen mit `audio_service/scripts/check_nas.py`. Ohne `NAS_SERVER` werden Exporte als *übersprungen* gemeldet. |
| `EXPORT_COVER` | `audio_service/.env` | leer (hier: `data/cover.jpg`) | Bild, das jede MP3 als Cover trägt. Ein PNG mit durchsichtigem Grund zeigen viele Player schwarz, deshalb auf Weiß als JPEG. `data/cover.*` ist vom Repository ausgenommen. |
| `auto_refine_boundaries` | Einstellungsseite (DB) | an | Schnittkanten beim Stoppen automatisch korrigieren |

### Ablage der Aufnahmen — ein Ordner je Session

Seit dem 18.09.2026 bekommt **jede Aufnahme einen eigenen Ordner** unter
`~/Recordings/Tontechnik/sessions/`. Der Ordnername trägt Datum, Gottesdiensttyp und den
Tageszähler — also das, was früher der Dateiname trug:

```
~/Recordings/Tontechnik/sessions/2026-09-18_Gebetsstunde_003/
    aufnahme.wav                                       # die Aufnahme (Kanalpaar AUDIO_RECORD_CHANNELS)
    transcript.txt                                     # Transkript als Fließtext
    transcript.jsonl                                   # Transkript mit Zeitmarken, zeilenweise
    exports/2026-09-18_Gebetsstunde_01_Predigt.mp3     # exportierte Segmente
```

- Der Zähler (`_001`, `_002` …) läuft pro Tag und Gottesdiensttyp; alte flache Dateien zählen mit,
  eine Nummer wird nie zweimal vergeben.
- Den Export-Zielordner leitet der Audio-Service aus dem WAV-Pfad ab
  (`config.export_dir()` → `<Sessionordner>/exports`). Die API-Schnittstelle ist unverändert: sie
  schickt weiterhin nur den Dateinamen (`mp3_filename`).
- **Ältere Aufnahmen** liegen flach in `sessions/` (so steht ihr `wav_path` in der Datenbank). Sie
  funktionieren unverändert weiter: ihre Exporte gehen nach wie vor in den Sammelordner
  `~/Recordings/Tontechnik/exports/`, ihre Transkripte heißen weiter `<Aufnahme>.txt` bzw.
  `<Aufnahme>.transcript.jsonl`. Es wurden **keine alten Dateien verschoben**.
- Erkannt wird die neue Ablage am festen Dateinamen `aufnahme.wav`.
- Auf dem NAS gilt die Ablage, die dort schon von Hand gewachsen war — siehe nächster Abschnitt.

### Ablage auf dem NAS

Ordner nach Vorlage (Einstellungen → NAS-Ablage), Standard wie im gewachsenen Archiv:

```
Gottesdienste/2026-10-18 Sonntag 1. morgens/
    Track-01 - Chorlied.mp3
    Track-02 - Predigt.mp3
    Track-03 - Kinderchor.mp3
```

- **Nummeriert wird fortlaufend** über die Beiträge, die beim Sammel-Export mitgehen.
  Gemeindegesang, Gebet und Ansagen reißen keine Lücken. Ein einzeln exportierter Sonderbeitrag
  bekommt eine Nummer hinter allen regulären.
- **Hinter der Nummer steht der Typ**, ohne Zeichen, an denen Windows oder SMB scheitern. Ein
  „Unbenannt" landet genauso im Namen — vor dem Export die Typen prüfen.
- **Ist der Ordner schon belegt**, etwa weil jemand von Hand abgelegt hat, kommt `… 2. Teil`
  daneben. Überschrieben wird nur, was das System selbst hochgeladen hat.
- **Erst alle Marker fertig, dann einmal „Alle exportieren".** Wer nach dem Upload Marker ändert
  und neu exportiert, verschiebt die Nummern.

Jede MP3 trägt ID3v2.3-Tags — 2.3, weil der Windows-Explorer und ältere Autoradios 2.4 nicht lesen:

| Tag | Inhalt |
|---|---|
| Titel | Typ des Beitrags, `Kinderchor` |
| Interpret, Album-Interpret | Einstellung *Export → Interpret*, meist der Name der Gemeinde |
| Album | Ordner ohne Bereich und Jahresordner, `2026-10-18 Sonntag 1. morgens` |
| Track | `3/18` |
| Datum, Genre, Sprache | `2026-10-18`, `Gottesdienst`, Deutsch |
| Kommentar | Zusammenfassung und Bibelstellen — nur wenn die KI-Auswertung für den Track gelaufen ist |
| Cover | Bild aus `EXPORT_COVER` |

KI-Titel gehen bewusst nicht in die Tags: Von sechs vergebenen waren drei richtig.

### Zweite Ablage: CopyCenter

Jeder Track eines Gottesdienstes geht ein zweites Mal ins CopyCenter — dieselbe Datei, im
Schema, das dort seit 2015 von Hand gepflegt wird:

```
CopyCenter/2026_mp3/Gottesd_261018_So_mp3_b/
    Track-01 - Kinderchor.mp3
    …
    Gottesdienst.txt        ← leer, der Name ist die Beschriftung
```

| Teil | Regel |
|---|---|
| Ordner | `Gottesd_` + JJMMTT + Wochentag kurz + `_mp3_` + Buchstabe |
| Buchstabe | `a` vor 12 Uhr, sonst `b`. Ist er belegt, `c`, `d` … — nie der andere der beiden |
| Beschriftung | das Thema, wenn eines eingetragen ist; sonst die Gottesdienstart, wobei Sonntage `Gottesdienst` heißen und die Gebetsstunde `Gebetstunde` |
| Wer mitgeht | alles, was unter `Gottesdienste/` liegt — Hochzeiten und Sonstiges nicht |

- Der Ordner wird beim ersten Track entschieden und bleibt für alle weiteren derselbe.
- Ins CopyCenter kommt ein Track nur, wenn der Haupt-Upload geklappt hat.
- Scheitert nur das CopyCenter, bleibt der Track als *hochgeladen* stehen, der Fehler steht
  gelb dahinter. Ein erneuter Export ersetzt dann beide eigenen Dateien.
- Im Code **standardmäßig aus** — eine andere Gemeinde hat kein CopyCenter. Hier eingeschaltet
  unter *Einstellungen → NAS-Ablage → Zusätzlich ins CopyCenter*.
- **Je Export wählbar:** Über der Segmentliste steht *Beim Export: Archiv (immer) · Auf die
  Station · Online stellen*. „Station“ ist das CopyCenter. Die Einstellung ist nur die
  Vorauswahl; angetippt gilt der Schalter für diesen Export.

### Online stellen: die Gemeinde-App

Mit eingeschaltetem *Online stellen* geht jeder exportierte Track zusätzlich in die Gemeinde-App
(Church_Tool) — mit Untertiteln aus dem Live-Transkript und der KI-Auswertung (Thema,
Zusammenfassung, Bibelstellen), falls sie vorher gelaufen ist. Neu transkribiert wird nicht.

| | |
|---|---|
| Ablage | `archive/JJJJ/MM/TT/<slot>/Track-NN_-_Typ.mp3` und `.vtt` daneben — dieselben Schlüssel wie beim Nachtrag von Hand, ein Gottesdienst steht nie doppelt in der App |
| Eintrag | über den Sammelimport der App; ein zweites Online-Stellen ergänzt nur |
| Titel in der App | `Predigt: <Thema>`, ohne KI-Auswertung `Chorlied · Track 07` |
| Wer mitgeht | wie beim CopyCenter nur `Gottesdienste/` |
| Status | eine eigene Zeile *Gemeinde-App* unter der NAS-Zeile jedes Tracks |

- Online geht unabhängig vom NAS: Ist das NAS weg, steht der Track trotzdem in der App.
- Überschrieben wird in der App nur, was von hier schon einmal hochgeladen wurde.
- Fehlt einem Track die KI-Auswertung, läuft sie vor dem Export von selbst (*Einstellungen →
  Export → KI-Auswertung vor dem Export*, Standard an). Scheitert sie, wird ohne Thema exportiert.
- Zugangsdaten in `audio_service/.env` (`ONLINE_*`, Vorlage in `.env.example`); ohne sie meldet
  die Zeile *übersprungen*. Vorauswahl unter *Einstellungen → NAS-Ablage → Beim Export online
  stellen*, hier seit 6. November eingeschaltet (Standard im Code: aus).

### Vermeldungen in die Gemeinde-App

Hat der Gottesdienst einen Track *Vermeldungen*, liest Akouo ihn nach dem Gottesdienst aus dem
Transkript, sobald die Segmentliste aufgeht, und fragt: **„Stimmen die Vermeldungen so zum
Hochladen?“** Darunter stehen die Einträge als Karten:

| Art | wohin | vorausgewählt |
|---|---|---|
| Termin | Kalender der Gemeinde-App, sichtbar nur für Mitglieder | ja, wenn ein Datum erkannt wurde |
| Hinweis | Schwarzes Brett, läuft nach 14 Tagen ab | ja |
| Gebetsanliegen | Schwarzes Brett, Kategorie Gebetsanliegen | **nein** — darin stehen Namen und Krankheiten |

- Titel, Datum, Uhrzeit und Ort sind in der Karte änderbar; darunter steht der Satz aus dem
  Transkript, aus dem der Eintrag stammt. Was das Modell nicht belegen konnte, erscheint gar nicht.
- „nächsten Samstag“, „am 23.“, „morgen“ rechnet Akouo gegen den Tag des Gottesdienstes aus.
  Gelingt das nicht, steht *bitte Datum und Uhrzeit prüfen*, und der Termin ist abgewählt.
- **Bestehende Termine werden geändert, nicht verdoppelt.** Beim Lesen schaut Akouo, was an dem
  Tag schon im Kalender steht. Passt genau ein Termin (gleicher Tag, morgens vor 13 Uhr bzw.
  abends), zeigt die Karte *Ändert: Gottesdienst morgens · 10:00*; passen mehrere, wählt man
  ihn im Auswahlfeld. Neu angelegt wird nur, was wirklich ein eigener Termin ist.
- Art **Thema** — „nächste Woche ist das Thema Psalm 22“ — setzt in der Beschreibung des
  Gottesdienstes die Zeile `Thema: Psalm 22` (eine alte Thema-Zeile wird ersetzt, alles andere
  bleibt). Ohne passenden Gottesdienst im Kalender wird nichts angelegt.
- Der Kalender führt jeden Termin als eigene Zeile — eine Änderung trifft genau diesen Tag.
- **Ja, hochladen** trägt ein; danach steht dort, wie viele angekommen sind, mit **Rückgängig**.
  **Nicht hochladen** verwirft; **Neu lesen** fragt das Modell noch einmal.
- Braucht dieselben Zugangsdaten wie *Online stellen* (`ONLINE_*` in `audio_service/.env`).

### Vorschlag beim Start: was laut Kalender dran ist

Öffnet man die Gottesdienst-Auswahl, fragt Akouo den Kalender der Gemeinde-App für heute ab und
zeigt oben *Laut Kalender: Sonntagmorgen · 10:00 · Thema Psalm 22* mit **Übernehmen**. Der Tipp
setzt Gottesdienstart und Thema; gewählt ist erst mit *Übernehmen* unten im Fenster wie sonst.
Als „jetzt“ gilt ein Termin ab 45 Minuten vor Beginn bis zwei Stunden danach, sonst der nächste.
Das Thema kommt aus der Zeile `Thema:` der Beschreibung, sonst aus einem besonderen Titel.
Ohne Zugangsdaten zur App bleibt die Zeile einfach weg.

### Lautheit: Spitze oder gleich laut

Jedes Segment wird beim Export normalisiert, wahlweise unter *Einstellungen → Export →
Normalisieren auf*:

| | |
|---|---|
| **Spitzenpegel** (Standard) | die lauteste Stelle auf −3 dBFS. Eine leise Predigt bleibt leiser als ein Lied — gemessen am 18. Oktober bis zu 9 dB |
| **Lautheit** | jedes Segment auf ein festes Ziel, Spitze höchstens −1 dBTP. Zwei Durchgänge FFmpeg-`loudnorm`, linear — es wird nur verstärkt, nicht geregelt |

Bei *Lautheit* gibt es zwei Ziele, je nach Klangklasse der Beitragsart (*Einstellungen →
Beitragsarten*):

| Ziel | Standard | gilt für |
|---|---|---|
| *Ziellautheit Sprache* | −16 LUFS | Klangklasse „Sprache“ und alles ohne Klangklasse |
| *Ziellautheit Musik* | −14 LUFS | Klangklasse „Musik“, dazu Gebetslied, Musikstück und Kinderlied |

Gebetslied, Musikstück und Kinderlied haben absichtlich keine Klangklasse — sonst würde die
Klangerkennung sie vorschlagen. Für die Lautheit gelten sie trotzdem als Musik (festgelegt in
`audio_service/laufzeit.py`). Der Export der ganzen Aufnahme nimmt das Sprachziel. Wer vorher
nur die eine *Ziellautheit* eingestellt hatte, behält deren Wert für beide Ziele, bis eines
neu gespeichert wird.

Scheitert das Angleichen, wird auf die Spitze normalisiert, und im Log steht eine Warnung.
Lässt sich das Ziel nicht linear erreichen, weil die Spitze dabei über −1 dBTP stiege, regelt
`loudnorm` dynamisch — das Segment ist dann gleich laut, kann aber leicht pumpen. Auch das
steht als Warnung im Log („linear nicht erreichbar“).

---

## 4. Dashboard und Einstellungen

Dunkler „Studio"-Look, optimiert für Touch (Schaltflächen mindestens 44 px) und für den
abgedunkelten Technikraum. Aufbau von oben nach unten:

### Gottesdienst auswählen (Kopfzeile)

Oben neben „Tontechnik" steht der **aktuelle Gottesdienst** (z. B. „Sonntagmorgen" oder
„Hochzeit · Teil 2"). Ist noch keiner gewählt, steht dort gelb „Gottesdienst wählen".

- Tippen öffnet die Auswahl: die regelmäßigen Gottesdienste **Sonntagmorgen, Sonntagabend,
  Bibelstunde, Gebetsstunde** als große Kacheln, alle übrigen klein unter „Weitere" (klappt
  automatisch auf, wenn gerade einer davon gewählt ist). Bei *Hochzeit* zusätzlich Brautpaar und
  Teil 1–3. „Übernehmen" speichert. Die Hauptliste steht in `api/service_types.py`
  (`PRIMARY_SERVICE_TYPES`).
- Die Auswahl gilt, bis sie geändert wird — **vor jedem Gottesdienst kurz prüfen**.
- Sie ist auf dem Server gespeichert (`settings`-Eintrag `current_service`) und auf allen Geräten
  gleich; andere Geräte sehen eine Änderung nach spätestens 5 Sekunden.
- **Während einer Aufnahme gesperrt** (Schloss-Symbol) — die laufende Aufnahme behält ihren Typ.
- Beim Umstellen wird die zum Typ gehörende **Checkliste aktiviert**, falls eine zugeordnet ist
  (`service_type_templates.checklist_template_id`). Ist dieselbe Checkliste heute schon aktiv,
  bleibt sie unverändert — abgehakte Punkte gehen nicht verloren (auch nicht beim Aufnahmestart).
- Zuordnung (Migration `008_map_service_type_checklists.py`, über den Namen der Vorlage):

  | Gottesdiensttyp | Checkliste |
  |---|---|
  | Sonntagmorgen, Sonntagabend, Heiligabend, Weihnachten, Silvester, Neujahr, Karfreitag, Ostern, Himmelfahrt, Pfingsten, Erntedankfest | Gottesdienst |
  | Bibelstunde | Bibelstunde |
  | Gebetsstunde | Gebetstunde |
  | Hochzeit, Besuch, Sonstiges | — |

  Ändern: `service_type_templates.checklist_template_id` in der Datenbank setzen, oder eine neue
  Migration nach dem Muster von 008 anlegen.
- **Migrationen auf dem Mac Studio:** Die Datenbank stand auf Alembic-Version 006, obwohl die
  Tabelle aus 007 schon existierte (die App legt fehlende Tabellen beim Start selbst an). Sie wurde
  am 15.09.2026 mit `alembic stamp 007` korrigiert und dann auf 008 gebracht. Vor Migrationen
  sichern: `sqlite3 data/tontechnik.db ".backup '/pfad/sicherung.db'"`, dann
  `DATABASE_URL="$DATABASE_URL" .venv/bin/alembic upgrade head` im Repo-Verzeichnis.

  **`DATABASE_URL` dabei wirklich mitgeben.** Ohne die Variable greift der Fallback aus
  `alembic.ini` (`./data/akouo.db`) und es entsteht eine zweite, leere Datenbank statt einer
  Migration der benutzten. Den Wert liefert das Startskript der API; im Zweifel aus
  `/Library/LaunchDaemons/de.akouo.api.plist` bzw. dem Wrapper ablesen.
- API: `GET /api/service/current` → `{service_type, couple_name, wedding_part, locked}`,
  `PUT /api/service/current` mit `{service_type, couple_name, wedding_part}`
  (422 bei unbekanntem Typ, 409 während einer Aufnahme).

### Aufnahme-Karte

- **Status** oben links: *Aufnahme* (bereit) · *Aufnahme läuft* (rot pulsierend) · *Pausiert* ·
  *Audio-Service offline*. Der Gottesdiensttyp steht rechts daneben.
- **Timer:** große Gesamtzeit, darunter *Track* (Zeit seit dem letzten Marker) und *Gesamt*.
- **Pegelanzeige** (siehe Abschnitt 5).
- **Aufnahme starten** startet **sofort** mit dem Gottesdienst aus der Kopfzeile — ohne Rückfrage.
  Ist noch keiner gewählt, öffnet sich zuerst die Auswahl.
- Während der Aufnahme: **Pause / Fortsetzen** und **Stoppen**. Stoppen braucht einen
  **zweiten Tipp innerhalb von 3 Sekunden** („Wirklich stoppen?") — Schutz gegen versehentliches
  Beenden mitten im Gottesdienst.
- **Als Nächstes:** vorgeschlagene Track-Reihenfolge aus der Gottesdiensttyp-Vorlage; „Setzen"
  platziert den Marker zum aktuellen Zeitpunkt.
- **Marker:** Liste mit ±1 s für Start und Ende.

Fehler (z. B. „Kein Audio-Eingang mit >=47 Kanälen gefunden") erscheinen als rote Meldung unten.

### Stream-Karte

Zeigt *Bereit* / *Live* mit Laufzeit / *Fehler* / *Nicht konfiguriert*. Der Knopf wechselt
zwischen „Stream starten" und „Stream stoppen" anhand des echten Zustands vom Server.

### Checkliste — ein- und ausklappbar

- Ein Tipp auf die Kopfzeile klappt die Liste auf oder zu. Eingeklappt bleibt eine Zeile mit
  Fortschrittsbalken und „10/13" bzw. „Alles erledigt".
- Der Zustand wird im Browser gespeichert (`localStorage`, Schlüssel `checklistOpen`) und
  übersteht das Nachladen alle 2 Sekunden sowie einen Reload.
- **Gruppen:** Beginnt ein Eintrag mit `[Gruppe] `, wird daraus eine Zwischenüberschrift —
  z. B. `[Vor dem Gottesdienst] Mischpult eingeschaltet`. Gruppen werden in der Reihenfolge der
  Einträge gebildet; also Einträge einer Gruppe in der Vorlage zusammenhalten.
- Vorlagen verwalten unter **Checklisten** (oben in der Navigation).

### Stream

- Ziel: `icecast://<icecast-host>:8000/<mount>` (Icecast 2.4.4). **Mithören:**
  `http://<icecast-host>:8000/<mount>`, Übersicht unter `http://<icecast-host>:8000/status-json.xsl`.
- Die Zugangsdaten stehen in `audio_service/.env`; das Dashboard und die Logs zeigen die Adresse
  immer ohne Passwort.
- Ist in den Einstellungen kein Ziel hinterlegt, sendet der Audio-Service an das Ziel aus der `.env`.
- Das Stoppen einer Aufnahme lässt einen laufenden Stream **weiterlaufen** (seit 18.10.2026) —
  nach dem letzten Lied wird oft noch gesprochen. Der Stream hat seinen eigenen Knopf.

### Weitere Bereiche

- **Segmente exportieren** erscheint nach einer beendeten Aufnahme.
- **Einstellungen:** Stream-Ziel, Schalter für die Schnittkanten-Automatik.

Seit Oktober gibt es den Tab **Archiv** (§ 5c2): er listet abgeschlossene Aufnahmen und öffnet
sie wieder zur Segmentprüfung. Das war ursprünglich bewusst nicht vorgesehen — veröffentlicht wird
nach wie vor über NAS und Webseite, nicht aus dem Dashboard. Gebraucht wurde es, um eine Aufnahme
nachträglich noch einmal aufzuschneiden. Der Endpunkt `GET /api/recordings` existiert weiterhin
für andere Systeme.

---

### Dienste prüfen, Audio-Dienst einstellen und neu starten

**Oben in der Checkliste** steht, ob alles läuft, was ein Gottesdienst braucht: Audio-Dienst,
Audio-Eingang (Signal vom Dante-Gerät), Live-Transkript (whisper), Sprachmodell (Ollama, mit
den eingestellten Modellen) und NAS. Alles grün heißt *Alle Dienste bereit*; sonst steht da,
was fehlt und warum. Alle 15 Sekunden neu geprüft — gedacht für **vor** dem Gottesdienst.

**Einstellungen → Audio-Dienst** zeigt dieselbe Prüfung und die Werte aus `audio_service/.env`,
die man im Betrieb ändern will: Gerät, Kanäle für Aufnahme, Stream und Rückspiel, Abtastrate, Transkript,
whisper- und Ollama-Adresse, Modelle, Name des Streams. **Zugangsdaten stehen dort bewusst
nicht** (NAS, Stream-Ziel, Gemeinde-App, Telefon) — das Dashboard hat keine Anmeldung.

- *Speichern* schreibt die `.env` — Kommentare und alle anderen Werte bleiben unberührt.
- *Audio-Dienst neu starten* lässt ihn sich selbst beenden; launchd startet ihn sofort neu, und
  er liest die `.env` frisch. **Gesperrt, solange Aufnahme, Stream oder Telefon läuft.**
- Der Aufnahmeordner wird nur angezeigt: die Datenbank kennt absolute Pfade.

## 5. Pegelanzeige — immer aktiv

Seit dem 18.09.2026 kommt der Ton über die **Dante Virtual Soundcard** (64 Kanäle, 48 kHz) statt
über ein Focusrite-Interface. **Aufnahme auf Dante-Kanal 44+45, Livestream auf 50+51**
(`AUDIO_RECORD_CHANNELS` / `AUDIO_STREAM_CHANNELS` in `audio_service/.env`; Stand 25.10.2026).
Der Stream-Mix lag zuletzt rund 10 dB leiser als der Aufnahme-Mix.
PortAudio öffnet immer ab Kanal 1, für Kanal 51 werden also 51 Kanäle geöffnet und die richtigen
Spalten herausgeschnitten. Kanäle ändern: Zeile in `.env` anpassen, dann
`launchctl kickstart -k gui/$(id -u)/de.akouo.audio`.
Welche Geräte gesucht werden, steht in `AUDIO_DEVICE_NAMES` (siehe Abschnitt 3) — ein Focusrite
wird weiterhin gefunden, wenn keine Dante-Soundkarte da ist.

Die Anzeige zeigt den Pegel des **Aufnahmepaars** (`AUDIO_RECORD_CHANNELS`, hier 44+45) als
LED-Kette in dBFS (−60 … 0 dB; grün bis −12, gelb bis −3, rot darüber, mit
Spitzenwert-Haltung). Sie läuft **immer**, nicht nur während einer Aufnahme. Rechts steht
„Eingang verbunden" oder „Kein Eingang".

### So funktioniert es

- Der Audio-Service öffnet beim Start **einen einzigen Stream** auf das Gerät und lässt ihn
  offen (`ContinuousRecorder.ensure_input()`). PortAudio öffnet immer ab Kanal 1, der Stream ist
  also so breit wie das höchste benutzte Kanal — bei 50+51 sind das **51 Kanäle**, nicht vier.
  Zwei parallele Streams auf dasselbe Gerät würden sich stören; deshalb nutzen Pegel, Aufnahme
  und Livestream denselben.
- Der Audio-Callback berechnet bei jedem Block den RMS-Wert des **Aufnahmepaars**.
- Eine Aufnahme **schreibt nur mit** (das Aufnahmepaar in die WAV-Datei). Beim Stoppen bleibt
  der Eingang offen; die nächste Aufnahme nutzt ihn weiter.
- Das **Streampaar** (hier 50+51) wird nur in die Stream-Warteschlange gelegt, **solange
  tatsächlich gestreamt wird**
  (`stream_enabled`, gesetzt vom `StreamAdapter`). Vorher lief diese Warteschlange während jeder
  Aufnahme auch ohne Stream voll (~1,3 GB in 2 Stunden).
- Ein Hintergrund-Task (`monitor_input`, alle 3 s) öffnet den Eingang, sobald das Gerät
  auftaucht, und öffnet ihn neu, wenn 3 s lang keine Audiodaten kamen (Kabel gezogen, Dante neu
  gestartet).
- Pausieren der Aufnahme unterbricht den Livestream nicht mehr.
- Signal an den Browser: `GET /api/sessions/audio-level` (Server-Sent Events, 10×/s),
  Format `{"rms": 0.0–1.0, "input": true|false}`.

### Zu beachten

- Solange ein Eingang offen ist, zeigt macOS den **orangen Mikrofon-Punkt** in der Menüleiste
  dauerhaft an. Das ist gewollt.
- **Pegel bleibt bei Stille, obwohl „verbunden" steht:** Dann läuft der Stream, es ist nur nichts
  auf die benutzten Dante-Kanäle geroutet. In **Dante Controller** unter *Routing* die
  Sender-Kanäle des Pults auf die Empfangskanäle **44+45 und 50+51** der *Dante Virtual Soundcard* setzen —
  44+45 für die Aufnahme, 50+51 für den Stream. Welche es sind, steht in `audio_service/.env`.
- Beim ersten Öffnen fragt macOS nach dem Mikrofonzugriff für
  `~/tontechnik-audio-venv/bin/python` — erlauben (Systemeinstellungen → Datenschutz & Sicherheit →
  Mikrofon).

---

## 5a. Live-Transkript

Während der Aufnahme verschriftet der Rechner mit — der Text erscheint im Dashboard direkt über
der Checkliste, etwa zehn Sekunden hinter dem gesprochenen Wort.

- **whisper-server** läuft als LaunchAgent `de.akouo.whisper` (Repo:
  `autostart/de.akouo.whisper.plist`, eingerichtet von `autostart/install.sh`) auf
  `127.0.0.1:8090` mit dem Modell aus `AKOUO_WHISPER_MODEL`, hier
  `~/ai/models/ggml-large-v3-turbo.bin`. Das Modell bleibt im Speicher, dadurch dauert ein
  Häppchen nur rund 0,35 Sekunden. Log: `~/Library/Logs/Akouo/whisper.log`.
- Der Audio-Service schneidet alle 10 Sekunden ein Stück aus der laufenden Aufnahme
  (`audio_service/transcriber.py`), rechnet es auf 16 kHz Mono herunter und schickt es hin.
- **Geschnitten wird an der leisesten Stelle** der letzten zwei Sekunden, nicht stur nach Uhr.
  Sonst zerreißt es Wörter und whisper erfindet Text.
- Ergebnis landet live im Dashboard und als `transcript.txt` und `transcript.jsonl`
  (mit Zeitmarken) im Sessionordner.
- Einstellbar: `TRANSCRIBE_ENABLED`, `TRANSCRIBE_CHUNK_SECONDS` (Standard 10), `TRANSCRIBE_LANGUAGE`
  (Standard `de`), `WHISPER_URL`.
- **Warum nicht `whisper-stream`:** Das Programm kann nur ein Gerät wählen, keinen Kanal, und
  würde Dante-Kanal 1 nehmen — der ist stumm. Unser Weg nutzt dieselben Kanäle wie die Aufnahme.

---

## 5b. Schnittkanten automatisch korrigieren

`audio_service/boundaries.py` sucht den echten Anfang und Schluss eines Tracks, wenn der Knopf
zu früh oder zu spät kam. Knopf „Kanten korrigieren“ im Segment-Bereich.

- Misst das Grundrauschen im Umkreis von 8 Sekunden und sucht den nächstgelegenen Übergang von
  **echter Stille** (mindestens 1,2 s) zu **anhaltendem Ton** (mindestens 0,4 s).
- **Vor deiner Marke sucht es weiter (12 s) und nachsichtiger** — zu spät gedrückt wird viel
  öfter als zu früh. Dort genügen 70 % Ton in den 0,4 s, ein einzelnes Knacken bricht die Stille
  nicht, und ein leises Intro gewinnt gegen den lauteren Einsatz danach, wenn keine echte Pause
  dazwischen liegt. Hinter der Marke gilt die strenge Regel; die nachsichtige Suche zieht einen
  Anfang also höchstens nach vorn, nie nach hinten.
- Lässt 0,25 s Luft davor und 0,4 s danach.
- **Gibt es keine echte Pause — etwa bei durchgehender Rede — bleibt deine Marke stehen.**
  Satzpausen von einer halben Sekunde zählen bewusst nicht.
- Die Korrektur wird als Verschiebung an den Marker geschrieben, dein ursprünglicher Zeitpunkt
  bleibt erhalten. Nochmal drücken rechnet neu, nicht obendrauf.
- **Schalter unter Einstellungen** („Schnittkanten automatisch korrigieren“, Standard: an).
  Ist er an, läuft die Korrektur automatisch beim Stoppen der Aufnahme; die Stop-Antwort meldet
  unter `refined_segments`, wie viele Segmente verschoben wurden. Ist er aus, wird exakt dort
  geschnitten, wo gedrückt wurde — der Knopf „Kanten korrigieren“ bleibt für den Einzelfall.
  Gespeichert als `auto_refine_boundaries` in der `settings`-Tabelle.

---

## 5c. KI-Auswertung der Segmente

Knopf „KI-Auswertung“ an jedem Segment. Läuft lokal über **Ollama**, ohne Internet.

- Nimmt die Transkriptzeilen im Zeitbereich des Segments und fragt das Sprachmodell nach
  **Titel, Zusammenfassung, Bibelstellen, Schlagwörtern und Sprecher**.
- **Bibelstellen kommen aus dem Transkript, nicht vom Modell.** Erkannt wird auch die
  gesprochene Form („Lukas Kapitel 16 ab Vers 19 bis 31“, „Psalm 51, die Verse 3 bis 5“).
  Je Buch und Kapitel bleibt eine Stelle, die genaueste; vorn steht, was zuerst gelesen wurde —
  meist der Predigttext. Sprachmodelle erfinden Verse sonst bereitwillig.
- **Lieder und Musik bekommen keinen KI-Titel.** Whisper versteht Gesang schlecht und schreibt
  über Instrumenten Füllsätze („Vielen Dank“, „Untertitelung des ZDF“); daraus wurden Titel wie
  „Amen und Beeship“. Den Liedtitel trägt man im Export-Check ein. Diese Füllsätze werden auch
  bei Wortbeiträgen vor der Auswertung aus dem Transkript genommen.
- Ergebnis steht in der Tabelle `segment_insights` (Migration 010) und unter dem Segment im
  Dashboard.
- Modellwahl über `OLLAMA_MODELS` (Standard: `qwen2.5:14b-instruct,llama3.1:8b,llama3.2`) — das
  erste installierte gewinnt. `ollama list` zeigt, was da ist.

---

## 5c1. Thema des Gottesdienstes

Im Auswahldialog gibt es neben dem Typ ein optionales Feld **Thema**. Es gilt für jeden
Gottesdiensttyp, nicht nur für Hochzeiten, und taucht an drei Stellen wieder auf:

- in der Kopfzeile neben dem Typ (`Sonntagmorgen · Orchesterleiter Abschluss`)
- im Dateinamen der Segmente
  (`2026-10-11_Sonntagmorgen_Orchesterleiter_Abschluss_13_Musikstück.mp3`)
- im Ordnernamen auf dem NAS, sobald der Upload läuft

Leeres Feld heißt: kein Thema, Dateinamen wie zuvor.

Wechselt man im Dialog die Gottesdienstart, wird das Thema geleert. Vorher wanderte es mit:
am 08.11. hing „Verheiratete“ vom Vorabend am Sonntagmorgen.

---

## 5c2. Archiv — frühere Aufnahmen

Der Tab **Archiv** listet alle abgeschlossenen Aufnahmen mit Datum, Gottesdienst, Thema, Dauer,
Anzahl der Beiträge und bereits erzeugter MP3s. Ein Klick klappt dieselbe Segment-Karte auf wie
im Dashboard: Grenzen korrigieren, Typ ändern, einzeln oder gesammelt exportieren, KI-Auswertung.

Fehlt die Aufnahmedatei — etwa weil der Ordner von Hand gelöscht wurde — steht das in der Zeile,
und ein Export ist nicht möglich.

Der Automodus fasst Archivaufnahmen nie an; er arbeitet ausschließlich an der laufenden.

---

## 5c3. Export-Check

„Exportieren“ und „Alle exportieren“ schneiden nicht sofort, sondern öffnen erst ein Fenster
**Vor dem Export prüfen**. Darin steht, was gleich passiert:

- **Gottesdienst:** Datum, Art und Thema — beides hier noch änderbar. Die Art bestimmt, wie der
  Abend in der Gemeinde-App erscheint (am 07.11. ging ein Jungverheirateten-Abend sonst als
  Gebetsstunde hoch).
- **Wohin:** der NAS-Ordner, die Station mit Ordner und Beschriftung, und der Eintrag in der
  Gemeinde-App (Titel, Art, Kategorie). Station und Online lassen sich hier umschalten.
- **Je Track:** Nummer, Typ, Zeiten, Dateiname und der Titel in der App. Titel und Sprecher sind
  änderbar, sobald es eine KI-Auswertung gibt; ohne Auswertung gibt es den Knopf dafür.
  Ein Häkchen entscheidet, ob der Track mitgeht — so bleibt ein schon exportierter Track beim
  Sammel-Export draußen.
- **Hinweise** in Gelb: sehr kurze oder sehr lange Tracks, fehlender Typ, schon exportiert oder
  schon online (wird ersetzt), keine KI-Auswertung.

Erst der Knopf „N exportieren“ schneidet. Das Fenster legt nichts fest: weder NAS-Ordner noch
Station-Buchstabe werden dabei vergeben. Art und Thema einer laufenden Aufnahme lassen sich hier
nicht ändern.

---

## 5d. Automatische Track-Erkennung

Der Automodus erkennt selbst, wo ein Beitrag anfängt, was er ist und wann er vorbei ist. Er
**wird ausdrücklich eingeschaltet** und läuft nie von allein an: Schalter in der Aufnahme-Karte,
sichtbar nur während einer Aufnahme, beim Start immer aus.

Die Aufnahme läuft durch, deshalb muss nichts in Echtzeit entschieden werden. Erkennt der Modus
zwanzig Sekunden zu spät, dass ein Beitrag begonnen hat, trägt er ihn trotzdem mit dem richtigen
Anfangszeitpunkt ein. Der erste Marker erscheint nach rund 50 Sekunden — ein Beitrag muss 45
Sekunden Ton haben, bevor er als solcher zählt.

**Wie erkannt wird**

| Schritt | Mittel | Messwert |
|---|---|---|
| Sprache oder Musik | fünf Klangmerkmale, Mehrheitsentscheid; dazu die Stereo-Korrelation als halbe Stimme | 26 von 27 Tracks richtig, steht nach 3 s |
| Grobe Grenzen | mindestens 8 s Ruhe, mindestens 45 s Beitrag | im Erntedankfest 15 von 15 |
| Anfang | zweite, tiefere Schwelle: vom erkannten Anfang bis zu 15 s zurück, solange es über dem örtlichen Grundpegel + 6 dB bleibt | Chorlied am 08.11. abends: 683,25 → 677,5 s (Intro ab ~676) |
| Feine Grenzen | Schnitt am Klassenwechsel, mehrheitsgeglättet | ohne ihn verschmelzen Chorlied und Ansage |
| Orchester | spektrale Flachheit unter 0,22 | instrumental 0,14–0,17, gesungen 0,27–0,37 |
| Gesang trotz Sprach-Klang | Whisper wiederholt sich bzw. wirft Füllsätze aus; reine Stellenangaben wie „Vers 2." zählen nicht | über 106 beschriftete Abschnitte 25 Treffer, kein Fehlalarm |
| Gebet | Anteil direkter Gottesanrede über 4,5 % | 6 von 6, aber siehe Warnung unten |
| Was als Nächstes kommt | Ansage im Transkript, ausgewertet mit Ollama | 4 von 4 Typen, 4 von 4 Titeln |

Die acht Sekunden Mindest-Stille sind gemessen, nicht geschätzt: bei fünf Sekunden zerschneiden
Sprechpausen die Predigt, bei fünfzehn verschmelzen aufeinanderfolgende Beiträge.

**Leise Anfänge.** Die Stille-Schwelle liegt 6 dB über dem Ruhepegel, und den schätzt der Modus
live aus allem bisher Gehörten — nach ein paar lauten Liedern steht sie so hoch, dass ein leises
Intro darunter bleibt. Am 8. November abends begann ein Chorlied bei −53 dBFS, die Schwelle
stand bei −32, und der Marker lag 7,5 s im Lied. Seitdem gibt es eine zweite Schwelle nur für den
Anfang: Ist ein Beitrag bestätigt, geht der Modus von dort zurück, solange der Pegel über dem
Ruhepegel der halben Minute davor + 6 dB liegt — höchstens 15 s, nie in den Beitrag davor, und
an stummem Kanal (digitale Stille) ist sofort Schluss. Findet er den Anfang erst ein paar Takte
später, zieht er auch den schon gesetzten Marker nach vorn — aber nur seinen eigenen und nur,
solange niemand ihn umbenannt oder verschoben hat.

**Stereo-Korrelation.** Das Rednerpult-Mikrofon liegt mittig auf beiden Kanälen, Chor und
Instrumente kommen verteilt. Gemessen am 8. November morgens: Predigt, Gebet, Ansage und
Vermeldungen 0,999–1,000, Lieder 0,4–0,8. Über 0,95 gibt das eine Stimme mehr für Sprache,
unter 0,8 eine weniger, dazwischen und bei Mono keine. Nie ein Veto: ein Musikstück lag bei 0,96,
ein Kinderlied über ein einzelnes Mikrofon bei 0,85. Die Einstellung „Stimmen für Sprache"
behält ihre Skala von 0 bis 5.

**Das angesagte Programm**

Jede neue Transkriptzeile wird darauf angesehen, ob dort Beiträge angekündigt werden — auch
mitten in einer Vermeldung, unabhängig von den Beitragsgrenzen. Ausgewertet mit dem lokalen
Sprachmodell, höchstens alle 20 Sekunden und nie mehr als 150 Sekunden Text auf einmal. Was
aussteht, steht im Dashboard unter „Angesagt, steht noch aus".

Daraus wird seit dem 10. Oktober der Typ abgeleitet. Das Verfahren beruht auf einer Messung:
Von sechzehn Programmpunkten des Erntedankfest-Nachmittags waren neun angesagt — und die sieben
übrigen waren ausnahmslos Predigten und Orchesterstücke, also genau das, was der Klang sicher
erkennt. Beide Quellen ergänzen sich.

Deshalb dienen **Predigt und Orchester als Anker**. An einem Anker gilt alles als erledigt, was
davor noch offen stand; dazwischen werden die angesagten Beiträge der Reihe nach eingehängt. Ohne
diese Verankerung pflanzte sich ein einziger nie angesagter Punkt über den ganzen Gottesdienst
fort. Gemessen: 17 erkannte Beiträge bei 17 Programmpunkten, 14 davon in richtiger Reihenfolge.

**Titel sind nur Anzeige** und gehen nicht in Marker oder Dateinamen — von sechs vergebenen waren
drei richtig. Ein falscher Typ steht sichtbar im Auswahlfeld, ein falscher Titel wandert unbemerkt
ins Archiv. Abschaltbar über `PROGRAMM_AUTOMATISCH=0` in `audio_service/.env`.

**Typen, die nie geraten werden**

`Gebetslied`, `Musikstück` und `Kinderlied` stehen zur Auswahl, aber die Erkennung schlägt sie nie
von sich aus vor — akustisch sind sie nicht von Chorlied und Orchester zu trennen. Sie entstehen
nur durch eine Ansage oder von Hand. `Musikstück` ist Musik auf der Bühne und etwas anderes als
das Orchester.

**Was die Automatik nicht kann**

- **Chorlied gegen Gruppenlied.** Länge und Klang überdecken sich vollständig. Nur die Ansage
  trennt das: „unser Chor singt" gegen „wir singen gemeinsam".
- **Gedicht gegen Gebet.** Das Erntedank-Gedicht spricht Gott zu 11,2 % direkt an, häufiger als
  die echten Gebete mit 6,5 bis 8,0. Über den Text nicht zu trennen. Deshalb wird ein vermutetes
  Gebet trotzdem markiert — ein überflüssiger Marker kostet einen Klick, ein fehlender die Aufnahme.
- **Die hintere Hälfte eines Gottesdienstes.** Übersieht die Erkennung einen Beitrag, verschiebt
  sich die Zuordnung bis zum nächsten Anker. Mehr Anker wären der Hebel.

Behoben am 10. Oktober, aber hier notiert, weil es in älteren Aufnahmen steckt: Am Anfang einer
Aufnahme pendelt sich der geschätzte Ruhepegel erst ein, wodurch derselbe Beitrag mehrfach
angelegt wurde. Erkennbar an gleichem Ende bei verschiedenem Anfang. Beiträge werden jetzt über
Überlappung wiedererkannt.

**Eingreifen**

Jede Marker-Zeile hat ein Auswahlfeld für den Typ und einen Papierkorb. Sobald ein Typ von Hand
geändert wurde, fasst die Automatik ihn nicht mehr an. Dasselbe gilt für verschobene Grenzen.

**Von Hand gesetzt, dann Automatik eingeschaltet:** Liegt dort, wo die Automatik einen Beitrag
erkennt, schon ein Marker von Hand, übernimmt sie ihn — sie setzt keinen eigenen daneben und
benennt ihn nie um, sie ergänzt höchstens ein fehlendes Ende. Ein Marker ohne Ende zählt dabei
mit seinen ersten 30 Sekunden. Bis zum 20.10.2026 legte sie ihren eigenen Marker ein paar Sekunden
dahinter, und die von Hand gesetzte Predigt schrumpfte auf diese paar Sekunden.
Der Papierkorb löscht nach zwei Tipps: Der erste färbt ihn rot, der zweite binnen drei Sekunden
löscht. Kein Dialog — der kostet mitten im Gottesdienst mehr, als er schützt.

**Nicht alles wird exportiert**

`Gemeindegesang`, `Gebet`, `Ansage` und `Vermeldungen` werden markiert, damit der Ablauf
vollständig dokumentiert ist, beim Sammel-Export aber übersprungen. Einzeln exportieren bleibt
möglich.

**Mitten im Gottesdienst einschalten**

Geht. Der Automodus liest beim Einschalten die ganze Aufnahme ab der ersten Sekunde und setzt
auch für Beiträge, die schon gelaufen sind, Marker auf den echten Anfang. Nur Ansagen liest er
rückwirkend höchstens 150 Sekunden — was davor angesagt wurde, kennt er nicht, und der Typ der
frühen Beiträge kommt dann allein aus dem Klang. Das holt „Neu erkennen" nach.

**Neu erkennen — nach dem Gottesdienst**

Live arbeitet der Modus mit dem, was bisher gelaufen ist: Der Ruhepegel muss sich am Anfang
erst einpendeln, ein fertiger Beitrag wird nie mehr verschoben, damit nichts springt. Nach dem
Stopp liegt alles vor. Der Knopf **„Neu erkennen"** in der Segment-Karte rechnet die ganze
Aufnahme in einem Zug durch — Pegel über den ganzen Gottesdienst, das ganze Transkript mit allen
Ansagen — und ersetzt die Marker, die die Automatik gesetzt hat. Zwei Tipps, wie beim Löschen.
Danach laufen die Schnittkanten wie nach jedem Stopp.

**Stehen bleibt**, was von Hand gesetzt, verschoben oder umbenannt ist, und was schon
exportiert wurde. Ein neu erkannter Beitrag, der auf so einem Marker liegt, wird verworfen —
die Hand hat Vorrang. Marker aus der Zeit vor dem 18. Oktober 2026 gelten alle als von Hand
gesetzt: Woher ein Marker kommt, wird erst seitdem gespeichert.

Dauer: rund eine Minute für zwei Stunden Gottesdienst, gemessen am 11. Oktober. Die Grenzen
trafen dort 20 von 21 Beiträgen. Bei den Typen bleibt es bei dem, was der Klang kann — Prosa
und Predigt, Orchester und Chorlied mit Begleitung verwechselt er, und die Korrektur von Hand
bleibt nötig.

---

## 5e. Ablauf neben der Aufnahme

Im Session-Ordner liegen neben `aufnahme.wav` und dem Transkript:

| Datei | Inhalt |
|---|---|
| `marker.json` | alle Beiträge mit Zeiten, Typ, Export-Kennzeichen, maschinenlesbar |
| `ablauf.txt` | dasselbe als Tabelle zum Draufschauen |

Damit steht der Ordner für sich. Geht die Datenbank verloren, ist trotzdem bekannt, wo die
Predigt anfing; ein kopierter Ordner ist vollständig.

Geschrieben wird bei jeder Änderung — Marker gesetzt, Typ geändert, Grenze verschoben, gelöscht,
Aufnahme gestoppt. Erst in eine Zwischendatei, dann umbenennen: das ist unteilbar, ein Absturz
mitten im Schreiben hinterlässt nie eine halbe Datei. Hat sich nichts geändert, wird nicht
geschrieben.

---

## 5f. Mithören

Der Lautsprecher in der Kopfzeile gibt das **Stream-Signal** (Dante 50+51) direkt aus dem
Audio-Dienst aus — nicht den Umweg über Icecast, der 10 bis 20 Sekunden kostet und nur
funktioniert, während gesendet wird. So hört man auch vor dem Gottesdienst hinein.

Ein Encoder bedient alle Zuhörer gleichzeitig; ein zweites Gerät nimmt dem ersten nichts weg.
Beim ersten Einschalten pro Sitzung kommt ein Kopfhörer-Hinweis, gestartet wird mit 30 Prozent
Lautstärke. **Über Lautsprecher koppelt der Ton in die Mikrofone zurück** und landet in der
Aufnahme.

Ungeprüft: Mithören, während der Stream läuft. Die Datenströme sind getrennt (eigene Queue),
bestätigt ist es nicht — zum Testen hätte öffentlich gesendet werden müssen.

---

## 5g. Telefon-Übertragung

Wer kein Internet hat, ruft die Konferenznummer an und gibt die PIN ein. Früher saß dafür ein
zweiter PC mit der App der Telefonanlage in der Konferenz; jetzt wählt sich der Audio-Dienst
selbst ein. **„Stream starten" startet beides, „Stream stoppen" beendet beides.** Die
Stream-Karte zeigt darunter eine eigene Zeile *Telefon* mit *Wählt ein…*, *Verbunden* samt
Laufzeit oder *Fehler* mit Grund.

Der Ton ist derselbe wie im Stream (Dante 50+51). Er läuft über die virtuelle Soundkarte
**BlackHole** zum Softphone **baresip**, das als eigene Nebenstelle angemeldet ist, die Konferenz
anwählt und die PIN tippt. Der Audio-Dienst öffnet dafür nur BlackHole — der Dante-Eingang
bleibt bei dem einen Strom.

- **Bricht die Verbindung ab, wählt der Dienst von selbst neu** (nach 5, 10, 20, dann alle
  30 Sekunden). Die Zeile zeigt währenddessen den Grund und „neuer Versuch läuft".
- Livestream und Telefon sind unabhängig: Fällt Icecast aus, bleibt das Telefon verbunden, und
  umgekehrt.
- Telefon heißt Schmalband (G.711). Predigt und Gebet klingen gut, Chor und Orchester dünn.
- Ohne die `TELEFON_*`-Werte in `audio_service/.env` erscheint die Zeile gar nicht.

Einrichten, einmalig:

```bash
brew install baresip
brew install --cask blackhole-2ch   # will das Passwort; startet den macOS-Audiodienst neu —
                                    # NIE während einer Aufnahme
```

Dann `TELEFON_*` in `audio_service/.env` eintragen (Vorlage in `.env.example`) und den
Audio-Dienst neu starten. Die SIP-Zugangsdaten vergibt der Verwalter der Telefonanlage für ein
„SIP-Gerät ohne Provisionierung". Wer sie hat, telefoniert auf Kosten der Gemeinde — sie
gehören nur in die `.env`.

**Wählt sich nicht ein:** „Anmeldung fehlgeschlagen" heißt Konto, Passwort oder
`TELEFON_SIP_ZUSATZ` (Transport) stimmen nicht. „Konferenz nicht erreicht" heißt Nummer falsch
oder die Nebenstelle darf nicht nach außen wählen. Kommt man in die Konferenz, aber die PIN
greift nicht, `TELEFON_PIN_VERZOEGERUNG` erhöhen.

## 5h. Rückspiel über Dante (optional, Standard: aus)

Ein Abschnitt einer Aufnahme lässt sich über Dante-Ausgänge zurück aufs Pult spielen — etwa um
einen Beitrag nach dem Gottesdienst noch einmal über die Anlage zu hören. Der Audio-Dienst
öffnet dafür keinen zweiten Strom: Ist das Rückspiel eingerichtet, wird der eine Dante-Strom
beim Start als **Duplex** geöffnet (Eingang und Ausgang zusammen). Ohne Einrichtung bleibt
alles wie bisher — nur Eingang.

**Einschalten:**

1. *Einstellungen → Audio-Dienst → Kanäle Rückspiel* (oder `AUDIO_OUTPUT_CHANNELS` in
   `audio_service/.env`) auf die Dante-**Ausgangs**kanäle setzen, 1-basiert, z. B. `61,62`.
   Leer heißt: aus.
2. Im Dante Controller diese Ausgänge der virtuellen Soundkarte auf einen **freien**
   Pult-Eingang routen.
3. Den Audio-Dienst neu starten — **nur, wenn keine Aufnahme läuft**
   (`curl -s localhost:8001/api/session/status`). Die Einstellung wirkt ausschließlich beim
   Start; mitten in einer Aufnahme wechselt der Strom nie die Art.

Im Log steht danach `[input] input open: … Rückspiel [61, 62]`. Hat das Gerät nicht genug
Ausgänge, steht dort „nur Eingang" — dann wird normal aufgenommen, nur das Rückspiel antwortet
mit *Der Ausgang ist nicht geöffnet*.

**Bedienen** (vorerst ohne Knopf im Dashboard):

```bash
# Sekunde 600 bis 660 der Aufnahme mit der Session-ID 42 abspielen
curl -s -X POST localhost:8000/api/sessions/42/rueckspiel \
     -H 'Content-Type: application/json' -d '{"from_s": 600, "to_s": 660}'
# sofort stoppen
curl -s -X POST localhost:8000/api/sessions/rueckspiel/stop
```

Ein neuer Abschnitt beendet den laufenden. Die Aufnahme muss dieselbe Abtastrate haben wie der
Ausgang (48000), sonst wird abgelehnt.

> **Achtung, Rückkopplung.** Den Rückspiel-Ausgang **nie** auf einen Kanal routen, der
> aufgenommen, gestreamt oder ins Telefon gespeist wird — und nie auf einen Pult-Eingang, der
> in diese Summen gemischt ist. Sonst landet die Wiedergabe in der Aufnahme, wird wieder
> abgespielt, und es pfeift. Im Zweifel den Pult-Eingang stumm lassen und nur auf den
> Kopfhörer legen.

**Nur außerhalb der Gottesdienstzeiten ausprobieren.** Einschalten heißt Neustart des
Audio-Dienstes, und ein falsches Routing ist im Saal sofort zu hören.

---

## 6. Frontend ändern

Kein Node/npm. Tailwind CSS v4 als eigenständiges Programm im Repo-Wurzelverzeichnis.

**Nach jeder Änderung an Klassen in Templates CSS neu bauen:**

```bash
cd ~/Code/Tontechnik
./tailwindcss-macos-arm64 -i api/static/input.css -o api/static/app.css --minify
```

- `api/static/input.css` beschränkt die Suche auf `api/templates`
  (`@import "tailwindcss" source(none); @source "../templates";`). Ohne diese Beschränkung
  durchsucht Tailwind auch `.venv/` und erzeugt Klassen aus Python-Paketen, die das CSS
  unbrauchbar machen.
- Eigene Bausteine in `input.css`: `card`, `btn`, `field`, `eyebrow`.
- **Cache:** Templates binden Dateien über `static_url('app.css')` ein (`api/main.py`). Die URL
  enthält das Änderungsdatum (`app.css?v=…`), damit Browser und Tablets nach einem Neubau sofort
  die neue Version laden. Neue Dateien in `base.html` ebenfalls so einbinden.

---

## 7. Mehrere Geräte / iPad

Das Dashboard kann gleichzeitig auf mehreren Geräten offen sein. Aufnahme-Status, Stream,
Checkliste und Marker-Liste kommen vom Server und gleichen sich alle 2 Sekunden ab — eine hier
gestartete Aufnahme lässt sich z. B. am iPad stoppen.

**Nur auf dem Gerät sichtbar, das die Aufnahme gestartet hat:** die Liste „Als Nächstes".

### iPad einrichten (sobald vorhanden)

1. Im selben WLAN in Safari öffnen: `http://<rechner>.local:8000`
   (alternativ über Tailscale: `http://<tailscale-name>:8000`)
2. Teilen → **Zum Home-Bildschirm** — startet dann als Vollbild-App mit eigenem Icon
   (vorbereitet über `api/static/manifest.json`, `icon-180.png`, `icon-512.png`).
3. Einstellungen → Anzeige & Helligkeit → **Automatische Sperre: Nie**.
   Die Seite kann den Bildschirm-Schlaf nicht selbst verhindern (die Wake-Lock-Schnittstelle
   gibt es in Safari nur über HTTPS).

### Offene Punkte

- **Keine Anmeldung:** Jede Person im selben Netzwerk kann das Dashboard steuern. Vor dem Einsatz
  in einem Gemeinde-WLAN, in das auch Besucher kommen, lösen.
- Das WLAN des Mac Studio lieferte zuletzt eine Adresse aus einem Bereich, der kein übliches
  Heimnetz ist — ein Zeichen dafür, dass der Zugangspunkt die Geräte voneinander trennt. Vor dem
  iPad-Einsatz also prüfen, ob sich Geräte im WLAN gegenseitig erreichen.
- Die macOS-Firewall ist aus; bei Aktivierung eingehende Verbindungen für Python erlauben.

---

## 8. Tests

Die beiden Dienste haben getrennte Umgebungen:

```bash
make test        # beide Gruppen, jede in ihrer eigenen Umgebung
make test-api    # nur die API
make test-audio  # nur den Audio-Dienst
make lint        # ruff
```

`make test` setzt `AUDIO_SERVICE_URL` auf den Discard-Port. Das ist kein Detail: beim Start
schiebt die API ihre Erkennungskonfiguration zum Audio-Dienst — ein Testlauf ohne diese
Umleitung würde den **produktiv laufenden** Dienst umkonfigurieren.

Stand 20.10.2026: **240 Tests grün** (171 API, 69 Audio-Dienst).

---

## 8a. Aktualisieren — eine neue Version in Betrieb nehmen

Python ist kalt: Änderungen an `api/**.py` und `audio_service/**.py` wirken erst, wenn der
jeweilige Dienst neu startet. Templates dagegen sind heiß und wirken sofort. Deshalb ist das
Aktivieren ein eigener, bewusster Schritt — und kein Nebenprodukt des Speicherns.

**Vorher prüfen, ob eine Aufnahme läuft.** Ein Neustart beendet sie.

```bash
curl -s localhost:8001/api/session/status
```

Die Reihenfolge ist nicht beliebig. Alembic ändert bei SQLite Tabellen, indem es kopiert und die
alte löscht; schreibt die API in diesem Moment, kann das fehlschlagen oder Daten verlieren. Also
erst die Dienste anhalten, dann migrieren.

```bash
# 1. Sicherung — der einzige Schritt, der sich nicht rückgängig machen lässt, wenn er fehlt
cp data/tontechnik.db data/tontechnik.db.bak-$(date +%Y%m%d-%H%M%S)

# 2. Dienste anhalten. Die API hat KeepAlive, deshalb bootout statt kill, sonst startet
#    sie sofort wieder und schreibt in die Datenbank, während migriert wird.
sudo launchctl bootout system/de.akouo.api
launchctl bootout gui/$(id -u)/de.akouo.audio

# 3. Migrieren. DATABASE_URL ausdrücklich mitgeben — ohne sie greift der Fallback aus
#    alembic.ini und es entsteht eine zweite, leere Datenbank statt einer Migration.
DATABASE_URL="sqlite+aiosqlite:///$PWD/data/tontechnik.db" .venv/bin/alembic upgrade head

# 5. CSS neu bauen, falls Templates geändert wurden
make css

# 6. Dienste wieder starten
sudo launchctl bootstrap system /Library/LaunchDaemons/de.akouo.api.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.akouo.audio.plist
```

### Der erste Lauf — zugleich der Umstieg auf die Akouo-Namen

**Auf diesem Rechner erledigt am 16.10.2026.** Für einen Rechner, der noch unter den alten
Labels `de.tontechnik.*` läuft, gilt weiter: Beim ersten Mal weicht die Reihenfolge ab: angehalten werden die **alten** Dienste, gestartet werden die **neuen** —
durch die Installer, nicht von Hand.

```bash
# 1. Prüfen, dass nichts aufnimmt, und sichern
curl -s localhost:8001/api/session/status
cp data/tontechnik.db data/tontechnik.db.bak-$(date +%Y%m%d-%H%M%S)

# 2. Die alten Dienste anhalten
sudo launchctl bootout system/de.tontechnik.api
launchctl bootout gui/$(id -u)/com.tontechnik.audio

# 3. Die dreisprachige Oberflaeche dazuholen. Sie liegt bewusst auf einem eigenen
#    Zweig: Templates sind heiss, und sobald eines t("…") aufruft, braucht es die
#    neue Python-Fassung — sonst steht das Dashboard sofort, bis zu genau diesem
#    Neustart. Deshalb erst jetzt, wo die Dienste ohnehin stehen.
git merge --no-ff i18n-oberflaeche -m "merge: dreisprachige Oberflaeche"

# 4. Migrieren, solange nichts schreibt. Jetzt bis 015 — 014 bringt die Arten in die
#    Datenbank, 015 die Anzeigenamen je Sprache.
DATABASE_URL="sqlite+aiosqlite:///$PWD/data/tontechnik.db" .venv/bin/alembic upgrade head

# 5. CSS neu bauen — Einstellungsseite und Sprachumschalter bringen neue Klassen mit
make css

# 6. Die neuen Dienste einrichten und starten. Die Installer entladen die alten
#    whisper- und Dashboard-Labels selbst und benutzen die vorhandene Datenbank.
AKOUO_WHISPER_MODEL=~/ai/models/ggml-large-v3-turbo.bin bash autostart/install.sh
bash audio_service/install.sh
```

Danach prüfen, ob die Einstellbarkeit wirklich angekommen ist — das ist der Punkt der ganzen
Übung:

```bash
sqlite3 data/tontechnik.db "SELECT version_num FROM alembic_version;"  # 015
curl -s -o /dev/null -w "%{http_code}\n" localhost:8001/api/config/erkennung  # 200, nicht 404
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/sprache/ru            # 303
# launchctl list zeigt als Benutzer NUR die Agents, nie den System-Daemon der API.
# Drei sind also richtig — die API steckt in der healthz-Zeile darueber.
launchctl list | grep -c akouo                                          # 3: audio, whisper, dashboard
```

Und erst wenn das steht, kann `api/templates/settings.html` weg — bis zum Neustart erwartet die
laufende API die Datei noch.

**Danach nachsehen, ob es wirklich gegriffen hat** — ein Dienst, der mit KeepAlive in einer
Neustartschleife hängt, sieht von außen aus wie ein laufender Dienst:

```bash
sqlite3 data/tontechnik.db "SELECT version_num FROM alembic_version;"   # erwarteter Stand
curl -s localhost:8000/healthz                                          # {"ok":true}
curl -s localhost:8001/api/session/status                               # state: idle
tail -20 ~/Library/Logs/Akouo/audio-error.log                           # keine Schleife
```

### Wenn die Migration schiefgeht

Die Sicherung zurückspielen, solange die Dienste noch stehen:

```bash
cp data/tontechnik.db.bak-<zeitstempel> data/tontechnik.db
```

Ein bekannter Stolperstein: `api/main.py` ruft beim Start `Base.metadata.create_all` auf. Auf
einer Datenbank, die so entstanden ist, existieren die Tabellen, aber `alembic_version` kennt sie
nicht — ein späteres `alembic upgrade head` will sie dann anlegen und scheitert. Ist das passiert,
hilft `alembic stamp <stand>` auf den tatsächlichen Stand (so geschehen am 15.09.2026, siehe
Abschnitt 3). Deshalb auf einer frischen Installation **zuerst** migrieren und erst danach die
API starten.

---

## 9. Fehlersuche

**Ein Track fehlt im Export.** Prüfen, ob der Marker ein Ende hat — wer beim Stoppen einen
Track offen lässt, bekommt als Grenze das aus der Uhr gerechnete Sessionende. Das liegt
Sekundenbruchteile hinter dem tatsächlichen Dateiende. Seit dem 11. Oktober wird bis zum
Dateiende geschnitten statt abzulehnen; in älteren Aufnahmen kann der Marker aber noch ohne
Ende dastehen.

**Der Automodus zeigt Beiträge einer alten Aufnahme.** Sollte nicht mehr vorkommen — er
beendet sich beim Stoppen selbst und wird bei neuer Gottesdienst-Auswahl zurückgesetzt. Falls
doch: Schalter aus und wieder an.


| Symptom | Ursache / Lösung |
|---|---|
| „Kein Audio-Eingang mit >=N Kanälen gefunden" (N ist das höchste benutzte Kanal, hier 47) | Dante Virtual Soundcard läuft nicht oder heißt anders. Prüfen: `~/tontechnik-audio-venv/bin/python -c "import sounddevice; print(sounddevice.query_devices())"`; Name ggf. in `AUDIO_DEVICE_NAMES` ergänzen. |
| Pegel zeigt „Kein Eingang", obwohl Dante läuft | Bis zu 3 s warten (Geräte-Scan). Sonst Mikrofonzugriff prüfen und `~/Library/Logs/Akouo/audio.log` auf `[input]`-Meldungen ansehen; Audio-Service neu starten. |
| Pegel bleibt still, „Eingang verbunden" steht aber da | Routing in Dante Controller fehlt (siehe Abschnitt 5). |
| Dashboard zeigt „Audio-Service offline" | `launchctl list \| grep akouo`, Fehlerlog `~/Library/Logs/Akouo/audio-error.log`, neu starten (Abschnitt 2). |
| Dashboard sieht kaputt aus (schwarze Schrift, riesige Symbole) | CSS neu bauen (Abschnitt 6); Seite neu laden. |
| Klick auf einen Knopf bewirkt nichts | Seite neu laden (⌘R) — nach Updates kann eine alte Version im Tab laufen. `~/Library/Logs/Akouo/api.log` zeigt, ob die Anfrage ankommt. |
| Browser öffnet sich nach dem Neustart nicht | Automatische Anmeldung aktiv? `~/Library/Logs/Akouo/dashboard.log` prüfen. |

---

## 10. Änderungen vom 15. September 2026

**Betrieb**
- API erreicht den Audio-Service nativ (`AUDIO_SERVICE_URL`) statt über die Docker-Adresse.
- Audio-Service eingerichtet (venv, LaunchAgent) und Dashboard-Autostart in Safari.

**Dashboard**
- Neugestaltung im dunklen Studio-Look; einklappbare, gruppierte Checkliste.
- Veraltetes und fehlerhaftes CSS behoben (Neubau, Suchpfad, Cache-Versionierung).
- Behoben: „Aufnahme stoppen" rief den alten Endpunkt auf — die Daueraufnahme lief weiter.
- Behoben: Start-Dialog schloss sich beim öffnenden Klick sofort wieder; die Typenliste war durch
  fehlerhaft eingebettetes JSON beschädigt.
- Neu: Pause/Fortsetzen, Stopp mit Bestätigung, Track-Laufzeit seit letztem Marker,
  Fehlermeldungen statt `alert()`, Stream-Knopf folgt dem echten Zustand.
- Vorbereitung für mehrere Geräte / iPad (Marker-Abgleich, Home-Bildschirm-App).
- Gottesdienst wird oben in der Kopfzeile gewählt (`/api/service/current`, für alle Geräte gleich);
  „Aufnahme starten" fragt nicht mehr nach. Checkliste wird beim Umstellen aktiviert und beim
  Aufnahmestart nicht mehr neu angelegt (abgehakte Punkte bleiben erhalten).

**Audio-Service**
- 18.09.2026: Live-Transkript (whisper-server), automatische Schnittkanten, KI-Auswertung je
  Segment (Ollama), ein Ordner je Session, großer „Track starten"-Knopf mit nachträglicher
  Typauswahl.
- 18.09.2026: Dante Virtual Soundcard als Eingang (48 kHz); Gerätesuche über `AUDIO_DEVICE_NAMES`
  statt fest „Focusrite".
- Pegelanzeige immer aktiv; Eingang bleibt offen und wird nach Ab-/Anstecken neu geöffnet.
- Speicherleck behoben: Stream-Warteschlange nur noch bei aktivem Stream gefüllt.

---

## 11. Schnellreferenz

**Dienste neu starten**

```bash
kill $(pgrep -f "uvicorn main:app")                      # Web-API (launchd startet neu)
launchctl kickstart -k gui/$(id -u)/de.akouo.audio       # Audio-Service
launchctl kickstart -k gui/$(id -u)/de.akouo.whisper     # whisper-server
```

**Zustand prüfen**

```bash
curl -s localhost:8001/api/session/status      # Aufnahme
curl -s localhost:8001/api/streaming/status    # Stream inkl. Ziel und Kanälen
curl -s localhost:8000/api/sessions/transcript # Live-Transkript
curl -s localhost:8000/api/sessions/automode   # Automodus: aktiv, erkannte Beiträge, Fehler
curl -s "localhost:8000/api/sessions/levels?session_id=14"  # Pegelverlauf einer Aufnahme
ollama list                                    # installierte Sprachmodelle
```

**Erkennung und NAS prüfen**

```bash
~/tontechnik-audio-venv/bin/python audio_service/scripts/pruefe_erkennung.py 14
~/tontechnik-audio-venv/bin/python audio_service/scripts/check_nas.py
```

**Wichtige Pfade**

| Was | Wo |
|---|---|
| Aufnahmen | `~/Recordings/Tontechnik/sessions/<Datum>_<Typ>_<Nr>/` |
| Logs | alle unter `~/Library/Logs/Akouo/`: `api.log`, `audio.log`, `audio-error.log`, `whisper.log`, `dashboard.log` |
| Konfiguration | `audio_service/.env`, `~/run-akouo-api.sh`, Einstellungsseite |
| Datenbank | `data/tontechnik.db` — Alembic-Stand **017** |
| Ablauf je Aufnahme | `marker.json` und `ablauf.txt` im Session-Ordner |
| whisper-Modelle | `~/ai/models/ggml-large-v3-turbo.bin` |

**Tests**

```bash
make test        # 240 Tests, beide Gruppen
```
