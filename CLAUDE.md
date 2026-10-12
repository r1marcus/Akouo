## Projekt

**Akouo** — Daueraufnahme, Segment-Erkennung, Livestream und Checklisten für die Tontechnik im
Gottesdienst. Ein Mac ist Server und Aufnahmerechner zugleich; bedient wird alles über ein
Webinterface vom Tablet aus.

**Kernwert:** Ein Tontechniker betreut einen ganzen Gottesdienst technisch, ohne den Platz zu
verlassen — alles über eine Oberfläche.

## Wo die Wahrheit steht

| | |
|---|---|
| [docs/betrieb.md](docs/betrieb.md) | Betrieb, Konfiguration, Bedienung, Fehlersuche. **Die maßgebliche Datei.** |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Aufbau und die Gründe dahinter, inklusive der bekannten Ecken |
| [docs/stand.md](docs/stand.md) | Was läuft, was bekannt kaputt ist, was fehlt |
| [docs/roadmap.md](docs/roadmap.md) | Nächste Schritte, nach Nutzen sortiert |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Einrichten, Tests, Konventionen |

## Tatsächlicher Aufbau

Zwei Prozesse, beide **nativ über launchd — kein Docker** (USB-/CoreAudio-Durchgriff in Container
ist auf macOS kaputt):

- `api/` auf Port 8000 — FastAPI, Jinja2, HTMX, Alpine, Tailwind, SQLite über SQLAlchemy async,
  Alembic. venv: `.venv`
- `audio-service/` auf Port 8001 — sounddevice/CoreAudio, FFmpeg, whisper.cpp, Ollama.
  Eigenes venv, weil es ein eigener Dienst mit eigenem Lebenszyklus ist.

Daneben: whisper-server auf 8090, Ollama auf 11434. Der Browser spricht ausschließlich mit 8000;
die API holt sich vom Audio-Dienst, was sie braucht.

**Python 3.12 genau** — nicht neuer. `pydub` importiert `audioop`, das in 3.13 aus der
Standardbibliothek entfernt wurde.

**Nicht gebaut**, auch wenn ältere Notizen das nahelegen: Shelly-Steuerung und SIP/DTMF-Einwahl.
Für beides existiert kein Code und keine Abhängigkeit.

## Beim Arbeiten am laufenden System

- **Templates sind heiß.** Jinja liest `api/templates/` bei jedem Request neu — ein Tippfehler
  macht das Dashboard sofort kaputt. Während eines Gottesdienstes nur mit Bedacht anfassen.
- **Python ist kalt.** Änderungen in `api/**.py` und `audio-service/**.py` wirken erst beim
  Dienst-Neustart. Man kann also während einer Aufnahme gefahrlos Backend-Code schreiben.
- **`api/static/app.css` ist gebaut, nicht heiß.** Neue Tailwind-Klassen brauchen `make css`.
- **Vor jedem Dienst-Neustart prüfen, ob eine Aufnahme läuft:**
  `curl -s localhost:8001/api/session/status` — ein Neustart beendet sie.

## Konventionen

### Sprache
- **Fachbegriffe der Domäne bleiben deutsch, auch im Code.** Ein Gottesdienst ist kein „service",
  eine Ansage kein „announcement". Deutsche Bezeichner sind erwünscht, wo der Begriff aus dem
  Gemeindealltag kommt: `Programm`, `Erwartung`, `klassifiziere_bereich()`, `lies_ansage()`,
  `thema`.
- **Technische Infrastruktur bleibt englisch:** `recorder`, `transcriber`, `exporter`,
  `nas_client`, `boundaries`, HTTP-Routen, Framework-Hooks.
  Faustregel: Würde der Tontechniker das Wort benutzen → deutsch. Steht es im FastAPI- oder
  PortAudio-Handbuch → englisch.
- **Kommentare und Docstrings: deutsch.** Sie erklären das Warum, und das Warum kommt aus dem
  Gottesdienst.
- **Alles, was der Nutzer liest, ist deutsch** — UI-Texte, Toasts, und ausdrücklich auch
  `HTTPException(detail=…)` und `{"message": …}`: diese Strings landen über
  `api/templates/dashboard.html` ungefiltert im Toast.
- **README und ARCHITECTURE sind englisch**, alles unter `docs/` deutsch. Zielgruppe der
  Betriebsdoku ist der Tontechniker, nicht der Entwickler.
- **Commit-Messages:** englisches Conventional-Commit-Präfix, deutscher Betreff.
  `feat(automodus): Typ live aus Ansagen und Klang ableiten`

### Code
- Rückgabetypen annotieren (`-> dict`, `-> None`); Parameter sind es meist schon.
- Keine nackten `except:` und kein `except Exception` für Kontrollfluss — gezielt fangen
  (`httpx.ConnectError`, `OSError`, `queue.Empty`). `except …: pass` nur in Aufräumpfaden.
- `logging`, nicht `print()` — außer in `audio-service/scripts/`, das sind CLI-Werkzeuge.
- Zugangsdaten gehören nie ins Log. Stream-Ziele über `public_target()` ausgeben.
- **Maschinenspezifisches gehört in `.env` oder in einen `*_PLACEHOLDER` im Plist, nie in den
  Code.** Keine absoluten Pfade, keine IP-Adressen, keine fest verdrahteten Gerätenamen.
- Der Audio-Dienst besitzt den einen CoreAudio-Stream. Kein zweiter Prozess öffnet das Gerät —
  zwei Streams desynchronisieren.
- Im PortAudio-Callback: nur billige NumPy-Arbeit und `put_nowait()`. Kein I/O, keine Locks,
  kein asyncio, kein Logging.

### Tests
`make test` fährt beide Gruppen. Sie laufen **getrennt**, weil `api/main.py` und
`audio-service/main.py` beide `main` heißen und sich in einem Prozess verdecken.
Tests schreiben ausschließlich in temporäre Verzeichnisse — wer einen findet, der
`~/Recordings` anfasst, hat einen Fehler gefunden.

### Nie ins Repository
`data/*.db` (echte Aufnahmen und Personendaten), `.env`, Aufnahmen, Transkripte, interne
IP-Adressen oder Hostnamen, echte Namen. Das Repository hat all das schon einmal enthalten.
