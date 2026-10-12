# Betrieb auf dem Mac Studio

Wie Akouo auf der produktiven Hardware tatsächlich läuft, wie es automatisch startet,
wie Dashboard und Pegelanzeige funktionieren — und was bei Problemen zu tun ist.

> `README.md` beschreibt den Aufbau allgemein, für einen beliebigen Mac. Hier steht, wie
> **dieser** Rechner eingerichtet ist, mit seinen Pfaden und seinen Eigenheiten.

Stand: 12. Oktober 2026.

---

## 1. Überblick

Der **Mac Studio** (`<rechner>.local`, Benutzer `<benutzer>`) ist die produktive Hardware.
Docker ist nicht installiert — beide Dienste laufen nativ.

| Dienst | Port | Erreichbar | Läuft als | Python-Umgebung |
|---|---|---|---|---|
| Web-API + Dashboard (`api/`) | 8000 | ganzes Netzwerk (`0.0.0.0`) | LaunchDaemon `de.akouo.api` | `<repo>/.venv` |
| Audio-Service (`audio-service/`) | 8001 | nur lokal (`127.0.0.1`) | LaunchAgent `de.akouo.audio` | `~/akouo-audio-venv` (hier: `~/tontechnik-audio-venv`) |
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
Kanal 44+45 werden aufgenommen und transkribiert, Kanal 46+47 gehen als Livestream an Icecast.

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
bash audio-service/install.sh
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
~/akouo-audio-venv/bin/pip install -r audio-service/requirements.txt
bash audio-service/install.sh
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
| `AUDIO_RECORD_CHANNELS` | `audio-service/.env` | `1,2` (hier: `44,45`) | Kanalpaar für die Aufnahme, 1-basiert wie in Dante Controller. |
| `STREAM_URL` | `audio-service/.env` | leer | Icecast-Ziel **mit Zugangsdaten**, aktuell `icecast://…@<icecast-host>:8000/<mount>`. Gehört nicht ins Repo. Fehlt in den Einstellungen ein Ziel, nutzt die API dieses hier. |
| `AUDIO_STREAM_CHANNELS` | `audio-service/.env` | `3,4` (hier: `46,47`) | Kanalpaar für den Livestream. Darf dasselbe Paar wie die Aufnahme sein. |
| `NAS_*` | `audio-service/.env` | auskommentiert | NAS über SMB. Zugang geprüft, aber die Freigabe `Aufnahmen` verweigert dem Dienstkonto noch den Zutritt — das muss die NAS-Verwaltung freischalten. Bis dahin bleiben die Zeilen auskommentiert: schon ein gesetztes `NAS_SERVER` löst den Upload aus, und jede Datei bekäme *fehlgeschlagen* statt *übersprungen*. Prüfen mit `audio-service/scripts/check_nas.py`. |
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
- Der NAS-Upload (Synology) ist davon nicht betroffen — dort bleibt die Ablage `Jahr/Monat/Datei.mp3`.

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

Fehler (z. B. „Focusrite with >=4 channels not detected") erscheinen als rote Meldung unten.

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
- Die Zugangsdaten stehen in `audio-service/.env`; das Dashboard und die Logs zeigen die Adresse
  immer ohne Passwort.
- Ist in den Einstellungen kein Ziel hinterlegt, sendet der Audio-Service an das Ziel aus der `.env`.
- Das Stoppen einer Aufnahme beendet auch einen laufenden Stream.

### Weitere Bereiche

- **Segmente exportieren** erscheint nach einer beendeten Aufnahme.
- **Einstellungen:** Stream-Ziel, Schalter für die Schnittkanten-Automatik.

Seit Oktober gibt es den Tab **Archiv** (§ 5c2): er listet abgeschlossene Aufnahmen und öffnet
sie wieder zur Segmentprüfung. Das war ursprünglich bewusst nicht vorgesehen — veröffentlicht wird
nach wie vor über NAS und Webseite, nicht aus dem Dashboard. Gebraucht wurde es, um eine Aufnahme
nachträglich noch einmal aufzuschneiden. Der Endpunkt `GET /api/recordings` existiert weiterhin
für andere Systeme.

---

## 5. Pegelanzeige — immer aktiv

Seit dem 18.09.2026 kommt der Ton über die **Dante Virtual Soundcard** (64 Kanäle, 48 kHz) statt
über ein Focusrite-Interface. **Aufnahme auf Dante-Kanal 44+45, Livestream auf 46+47**
(`AUDIO_RECORD_CHANNELS` / `AUDIO_STREAM_CHANNELS` in `audio-service/.env`; Stand 20.09.2026).
Der Stream-Mix lag zuletzt rund 10 dB leiser als der Aufnahme-Mix.
PortAudio öffnet immer ab Kanal 1, für Kanal 45 werden also 45 Kanäle geöffnet und die richtigen
Spalten herausgeschnitten. Kanäle ändern: Zeile in `.env` anpassen, dann
`launchctl kickstart -k gui/$(id -u)/de.akouo.audio`.
Welche Geräte gesucht werden, steht in `AUDIO_DEVICE_NAMES` (siehe Abschnitt 3) — ein Focusrite
wird weiterhin gefunden, wenn keine Dante-Soundkarte da ist.

Die Anzeige zeigt den Pegel von **Kanal 1+2** (Aufnahme-Signal) als LED-Kette in dBFS
(−60 … 0 dB; grün bis −12, gelb bis −3, rot darüber, mit Spitzenwert-Haltung).
Sie läuft **immer**, nicht nur während einer Aufnahme. Rechts steht
„Focusrite verbunden" oder „Kein Eingang".

### So funktioniert es

- Der Audio-Service öffnet beim Start **einen einzigen 4-Kanal-Stream** auf das Gerät und
  lässt ihn offen (`ContinuousRecorder.ensure_input()`). Zwei parallele Streams auf dasselbe
  Gerät würden sich stören — deshalb nutzen Pegel, Aufnahme und Livestream denselben Stream.
- Der Audio-Callback berechnet bei jedem Block den RMS-Wert von Kanal 1+2.
- Eine Aufnahme **schreibt nur mit** (Kanal 1+2 in die WAV-Datei). Beim Stoppen bleibt der
  Eingang offen; die nächste Aufnahme nutzt ihn weiter.
- Kanal 3+4 werden nur in die Stream-Warteschlange gelegt, **solange tatsächlich gestreamt wird**
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
  auf die Dante-Kanäle 1–4 geroutet. In **Dante Controller** unter *Routing* die Sender-Kanäle des
  Pults auf die Empfangskanäle 1–4 der *Dante Virtual Soundcard* setzen.
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
  (`audio-service/transcriber.py`), rechnet es auf 16 kHz Mono herunter und schickt es hin.
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

`audio-service/boundaries.py` sucht den echten Anfang und Schluss eines Tracks, wenn der Knopf
zu früh oder zu spät kam. Knopf „Kanten korrigieren“ im Segment-Bereich.

- Misst das Grundrauschen im Umkreis von 8 Sekunden und sucht den nächstgelegenen Übergang von
  **echter Stille** (mindestens 1,2 s) zu **anhaltendem Ton** (mindestens 0,4 s).
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
- **Bibelstellen werden nachgeprüft:** Was nicht als echte Stelle im Transkript vorkommt, fliegt
  raus. Sprachmodelle erfinden Verse sonst bereitwillig.
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

---

## 5c2. Archiv — frühere Aufnahmen

Der Tab **Archiv** listet alle abgeschlossenen Aufnahmen mit Datum, Gottesdienst, Thema, Dauer,
Anzahl der Beiträge und bereits erzeugter MP3s. Ein Klick klappt dieselbe Segment-Karte auf wie
im Dashboard: Grenzen korrigieren, Typ ändern, einzeln oder gesammelt exportieren, KI-Auswertung.

Fehlt die Aufnahmedatei — etwa weil der Ordner von Hand gelöscht wurde — steht das in der Zeile,
und ein Export ist nicht möglich.

Der Automodus fasst Archivaufnahmen nie an; er arbeitet ausschließlich an der laufenden.

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
| Sprache oder Musik | fünf Klangmerkmale, Mehrheitsentscheid | 26 von 27 Tracks richtig, steht nach 3 s |
| Grobe Grenzen | mindestens 8 s Ruhe, mindestens 45 s Beitrag | im Erntedankfest 15 von 15 |
| Feine Grenzen | Schnitt am Klassenwechsel, mehrheitsgeglättet | ohne ihn verschmelzen Chorlied und Ansage |
| Orchester | spektrale Flachheit unter 0,22 | instrumental 0,14–0,17, gesungen 0,27–0,37 |
| Gesang trotz Sprach-Klang | Whisper wiederholt sich bzw. wirft Füllsätze aus | 7 Treffer, alle richtig, keine Fehlalarme |
| Gebet | Anteil direkter Gottesanrede über 4,5 % | 6 von 6, aber siehe Warnung unten |
| Was als Nächstes kommt | Ansage im Transkript, ausgewertet mit Ollama | 4 von 4 Typen, 4 von 4 Titeln |

Die acht Sekunden Mindest-Stille sind gemessen, nicht geschätzt: bei fünf Sekunden zerschneiden
Sprechpausen die Predigt, bei fünfzehn verschmelzen aufeinanderfolgende Beiträge.

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
ins Archiv. Abschaltbar über `PROGRAMM_AUTOMATISCH=0` in `audio-service/.env`.

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
Der Papierkorb löscht ohne Rückfrage — die Aufnahme läuft weiter, der Ton ist nicht weg.

**Nicht alles wird exportiert**

`Gemeindegesang`, `Gebet`, `Ansage` und `Vermeldungen` werden markiert, damit der Ablauf
vollständig dokumentiert ist, beim Sammel-Export aber übersprungen. Einzeln exportieren bleibt
möglich.

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

Der Lautsprecher in der Kopfzeile gibt das **Stream-Signal** (Dante 46+47) direkt aus dem
Audio-Dienst aus — nicht den Umweg über Icecast, der 10 bis 20 Sekunden kostet und nur
funktioniert, während gesendet wird. So hört man auch vor dem Gottesdienst hinein.

Ein Encoder bedient alle Zuhörer gleichzeitig; ein zweites Gerät nimmt dem ersten nichts weg.
Beim ersten Einschalten pro Sitzung kommt ein Kopfhörer-Hinweis, gestartet wird mit 30 Prozent
Lautstärke. **Über Lautsprecher koppelt der Ton in die Mikrofone zurück** und landet in der
Aufnahme.

Ungeprüft: Mithören, während der Stream läuft. Die Datenströme sind getrennt (eigene Queue),
bestätigt ist es nicht — zum Testen hätte öffentlich gesendet werden müssen.

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
- Das WLAN des Mac Studio lieferte zuletzt die ungewöhnliche Adresse `192.0.0.2` — vor dem
  iPad-Einsatz prüfen, ob Geräte im WLAN sich gegenseitig erreichen.
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

Stand 12.10.2026: **164 Tests grün** (102 API, 62 Audio-Dienst).

---

## 8a. Aktualisieren — eine neue Version in Betrieb nehmen

Python ist kalt: Änderungen an `api/**.py` und `audio-service/**.py` wirken erst, wenn der
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

# 4. CSS neu bauen, falls Templates geändert wurden
make css

# 5. Dienste wieder starten
sudo launchctl bootstrap system /Library/LaunchDaemons/de.akouo.api.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.akouo.audio.plist
```

### Der erste Lauf — zugleich der Umstieg auf die Akouo-Namen

**Dieser Rechner hat den Schritt noch vor sich** (Stand 12.10.2026, siehe Abschnitt 1). Er läuft
unter den alten Labels, und die Datenbank steht auf 013 statt 014. Beim ersten Mal weicht die
Reihenfolge deshalb ab: angehalten werden die **alten** Dienste, gestartet werden die **neuen** —
durch die Installer, nicht von Hand.

```bash
# 1. Prüfen, dass nichts aufnimmt, und sichern
curl -s localhost:8001/api/session/status
cp data/tontechnik.db data/tontechnik.db.bak-$(date +%Y%m%d-%H%M%S)

# 2. Die alten Dienste anhalten
sudo launchctl bootout system/de.tontechnik.api
launchctl bootout gui/$(id -u)/com.tontechnik.audio

# 3. Migrieren, solange nichts schreibt
DATABASE_URL="sqlite+aiosqlite:///$PWD/data/tontechnik.db" .venv/bin/alembic upgrade head

# 4. CSS neu bauen — die Einstellungsseite ist neu und bringt neue Tailwind-Klassen mit
make css

# 5. Die neuen Dienste einrichten und starten. Die Installer entladen die alten
#    whisper- und Dashboard-Labels selbst und benutzen die vorhandene Datenbank.
AKOUO_WHISPER_MODEL=~/ai/models/ggml-large-v3-turbo.bin bash autostart/install.sh
bash audio-service/install.sh
```

Danach prüfen, ob die Einstellbarkeit wirklich angekommen ist — das ist der Punkt der ganzen
Übung:

```bash
sqlite3 data/tontechnik.db "SELECT version_num FROM alembic_version;"  # 014
curl -s -o /dev/null -w "%{http_code}\n" localhost:8001/api/config/erkennung  # 200, nicht 404
launchctl list | grep -c akouo                                          # 3 (api, whisper, dashboard)
                                                                        # + de.akouo.audio
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
| „Kein Audio-Eingang mit >=4 Kanälen gefunden" | Dante Virtual Soundcard läuft nicht oder heißt anders. Prüfen: `~/tontechnik-audio-venv/bin/python -c "import sounddevice; print(sounddevice.query_devices())"`; Name ggf. in `AUDIO_DEVICE_NAMES` ergänzen. |
| Pegel zeigt „Kein Eingang", obwohl Dante läuft | Bis zu 3 s warten (Geräte-Scan). Sonst Mikrofonzugriff prüfen und `~/Library/Logs/Akouo/audio.log` auf `[input]`-Meldungen ansehen; Audio-Service neu starten. |
| Pegel bleibt still, „Focusrite verbunden" steht aber da | Routing in Dante Controller fehlt (siehe Abschnitt 5). |
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
~/tontechnik-audio-venv/bin/python audio-service/scripts/pruefe_erkennung.py 14
~/tontechnik-audio-venv/bin/python audio-service/scripts/check_nas.py
```

**Wichtige Pfade**

| Was | Wo |
|---|---|
| Aufnahmen | `~/Recordings/Tontechnik/sessions/<Datum>_<Typ>_<Nr>/` |
| Logs | alle unter `~/Library/Logs/Akouo/`: `api.log`, `audio.log`, `audio-error.log`, `whisper.log`, `dashboard.log` |
| Konfiguration | `audio-service/.env`, `~/run-akouo-api.sh`, Einstellungsseite |
| Datenbank | `data/tontechnik.db` — Alembic-Stand **013**, Ziel 014 (Abschnitt 8a) |
| Ablauf je Aufnahme | `marker.json` und `ablauf.txt` im Session-Ordner |
| whisper-Modelle | `~/ai/models/ggml-large-v3-turbo.bin` |

**Tests**

```bash
make test        # 164 Tests, beide Gruppen
```
