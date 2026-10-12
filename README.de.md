# Akouo

[English](README.md) · **Deutsch** · [Русский](README.ru.md)

**Daueraufnahme, Live-Transkript und automatische Segment-Erkennung für die Tontechnik im
Gottesdienst — auf einem einzigen Mac im Haus.**

[![Lizenz](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Plattform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-lightgrey)
![Python](https://img.shields.io/badge/python-3.12-blue)

> **Zur Sprache.** Die Betriebsdokumentation unter [`docs/`](docs/) ist deutsch — ihre Zielgruppe
> ist der Tontechniker, nicht der Entwickler. [`README.md`](README.md) und
> [`ARCHITECTURE.md`](ARCHITECTURE.md) sind englisch. Die Oberfläche selbst spricht **deutsch,
> englisch und russisch** — umschaltbar pro Gerät.
>
> *Akouo* — vom griechischen ἀκούω, „ich höre".

---

## Worum es geht

Einen Gottesdienst auf die übliche Art aufzunehmen heißt: zwei Stunden lang Start und Stopp
drücken. Vor dem Chorlied starten, danach stoppen, Datei benennen, zwanzigmal von vorn — und
nebenbei laufen Mischung, Livestream und Checkliste.

Akouo dreht das um. **Es nimmt den ganzen Gottesdienst in einem Zug auf** und findet hinterher
heraus, wo die einzelnen Beiträge lagen. Die durchgehende WAV wird an der leisesten Stelle
dazwischen in einzelne MP3s geschnitten. Während der Aufnahme wird der Ton lokal verschriftet, und
die Ansagen in diesem Transkript sagen dem System, was als Nächstes kommt. Der Tontechniker sieht
zu, korrigiert, was falsch ist, und fasst keinen Startknopf mehr an.

Alles läuft auf einem Mac im Gebäude. Keine Cloud, keine Schlüssel, kein Internet nötig.

![Das Dashboard während eines Gottesdienstes: Gottesdienstart in der Kopfzeile, Fortschritt der
Checkliste, Aufnahmeuhr und eine Pegelanzeige, die unabhängig von der Aufnahme
läuft](docs/bilder/dashboard.png)

<sup>Die laufende Anlage, fünf Minuten vor einem Sonntagabend-Gottesdienst. Die Pegelanzeige läuft
durchgehend — nicht nur während einer Aufnahme —, weil man Signal sehen muss, bevor man irgendetwas
drückt.</sup>

## Stand — die ehrliche Fassung

Das ist **die laufende Anlage einer Gemeinde**, seit September 2026 wöchentlich im Einsatz,
veröffentlicht für den Fall, dass sie jemand anderem nützt. Es ist kein Produkt.

Was an echten Gottesdiensten belegt ist (gemessen, nicht geschätzt — Einzelheiten in
[`docs/betrieb.md`](docs/betrieb.md)):

- Sprache gegen Musik: **26 von 27** Beiträgen richtig, entschieden binnen drei Sekunden
- Grobe Beitragsgrenzen: **15 von 15** an einem Erntedankfest
- Orchester über spektrale Flachheit: instrumental 0,14–0,17 gegen gesungen 0,27–0,37
- Beiträge aus gesprochenen Ansagen: **17 erkannt bei 17 Programmpunkten**, 14 in der richtigen
  Reihenfolge

Was die Automatik **nicht** kann:

- Chorlied von Gruppenlied unterscheiden — klanglich ist das dasselbe
- Gedicht von Gebet zuverlässig trennen — sie zählt die direkte Anrede an Gott, und das ist ein
  Hilfsmaß
- Brauchbare Titel erfinden — von sechs waren drei richtig. Titel sind reine Anzeige und landen
  bewusst nie in einem Marker oder Dateinamen: Ein falscher Typ steht sichtbar im Auswahlfeld, ein
  falscher Titel verschwindet still im Archiv

Und die ehrlichen Einschränkungen im Betrieb:

- **Es gibt keine Anmeldung.** Jeder im Netz kann das Dashboard öffnen, die Aufnahme stoppen und
  Marker löschen. Siehe [Sicherheit](#sicherheit).
- Eine Installation, ein Betreuer. Rechne damit, Dinge anpassen zu müssen.
- `audio-service/classifier.py` trägt Schwellwerte, die in **diesem** Raum an **diesem** Pult
  gemessen wurden. Sie sind ein Startpunkt, keine Naturkonstante.

## Was es tut

| | |
|---|---|
| **Daueraufnahme** | Ein offener Audiostrom für den ganzen Gottesdienst. Getrennte Kanalpaare für Aufnahme und Stream-Mix, damit der Stream eine andere Balance tragen kann. |
| **Live-Transkript** | whisper.cpp lokal, rund 10 s hinterher, sichtbar im Dashboard. |
| **Automatische Segment-Erkennung** | Grenzen bei ≥8 s Stille, Beitragsart aus fünf Klangmerkmalen plus den Ansagen im Transkript. Alles ist ein Vorschlag, den der Tontechniker überstimmen kann. |
| **MP3 je Beitrag** | 192 kbit/s, auf −3 dBFS normalisiert, 1 s Blenden, geschnitten an der leisesten Stelle. Der Sessionordner erklärt sich selbst: `marker.json` und `ablauf.txt` liegen neben dem Ton. |
| **Lokale KI-Auswertung** | Je Segment über Ollama: Zusammenfassung, Stichworte, Bibelstellen — jede Stelle gegen das Transkript geprüft, um Erfindungen abzufangen. |
| **Checklisten** | Je Gottesdienstart, vom Tablet abhakbar, damit die Vorbereitung nicht in einem Kopf steckt. |
| **Livestream** | Icecast oder RTMP über FFmpeg, dazu ein lokaler Mithörweg ohne die Icecast-Verzögerung. |
| **NAS-Upload** | Segmente auf eine SMB-Freigabe, ohne je zu überschreiben. |

## Wie es aussieht

Die Oberfläche ist für ein Tablet neben dem Pult gebaut — große Flächen, kein Hover, nichts, wofür
man eine zweite Hand braucht. Sie spricht deutsch, englisch und russisch; die Wahl gilt pro Gerät,
zwei Leute können dieselbe Aufnahme also in verschiedenen Sprachen bedienen.

![Gottesdienst wählen: vier große Kacheln für die regelmäßigen Dienste, der Rest unter „Weitere",
dazu ein freies Themenfeld](docs/bilder/gottesdienst-waehlen.png)

**Gottesdienst wählen.** Vier Kacheln für die, die jede Woche stattfinden, der Rest
zusammengeklappt. Die Wahl steuert die Checkliste, das Ordnerlayout auf dem NAS und das, was die
Erkennung zu hören erwartet. `Thema` ist freier Text — Erntedankfest, eine Taufe, ein Jubiläum —
und wandert in Ordnernamen und MP3-Tag.

![Die Einstellungsseite: Werte für Export, Erkennung und Schnittkanten, jeder mit einem Satz dazu,
was er bewirkt](docs/bilder/einstellungen.png)

**Einstellungen.** Neunzehn Werte in fünf Bereichen, jeder mit einem Satz, was er ändert — und wo
eine Zahl gemessen statt geraten wurde, auch die Messung. *„Bei 5 s zerschneiden Sprechpausen die
Predigt, bei 15 s verschmelzen aufeinanderfolgende Beiträge."* Beitragsarten, Ansage-Regeln und die
NAS-Ordnervorlage sind auf derselben Seite editierbar.

<sup>Beide Bilder stammen aus einer frischen Installation mit leerer Datenbank — deshalb meldet
sich der Audio-Dienst als offline.</sup>

## Voraussetzungen

Zur Warnung vorweg: Im Audioweg steckt eine **kommerziell lizenzierte** Komponente.

| | Nötig? | Anmerkung |
|---|---|---|
| Mac mit Apple Silicon, dauerhaft an | **ja** | Entwickelt und im Einsatz auf einem Mac Studio, M4 Max / 36 GB. macOS 15+. |
| **Python 3.12 genau** | **ja** | Nicht 3.13+: `pydub` importiert `audioop`, das dort aus der Standardbibliothek entfernt wurde. Homebrew liefert inzwischen Neueres — `python@3.12` ausdrücklich installieren. |
| FFmpeg | **ja** | `brew install ffmpeg`. Nicht mitgeliefert, siehe [`NOTICE`](NOTICE). |
| Audioeingang, ≥2 Kanäle | **ja** | Die Voreinstellung nutzt dasselbe Stereopaar für Aufnahme und Stream. |
| **Dante Virtual Soundcard** | für den vollen Aufbau | **Kommerzielle Lizenz von Audinate.** Nur nötig, wenn das Pult den Ton über Dante liefert, wie hier (Aufnahme auf 44/45, Stream auf 46/47). Ein mehrkanaliges USB-Interface tut es auch — ein Focusrite wird von Haus aus gefunden. |
| Pult mit zwei getrennten Mischungen | empfohlen | Sonst tragen Aufnahme und Stream dieselbe Balance. |
| whisper.cpp + ggml-Modell | optional | ~1,5 GB. Ohne bleibt das Transkript leer und die Erkennung über Ansagen verstummt; die Aufnahme ist unberührt. |
| Ollama + Modell | optional | `qwen2.5:14b-instruct` sind ~9 GB und wollen 16 GB+ RAM. Ohne gibt es keine KI-Auswertung, und nichts stürzt ab. |
| Icecast-2-Server | optional | Für den Livestream. |
| SMB-Freigabe | optional | Für den Upload. Ohne bleiben Exporte lokal und werden als *übersprungen* gemeldet. |
| Tablet im selben Netz | empfohlen | Das Dashboard ist für Berührung gebaut. |

Das System startet und bleibt bedienbar, wenn jede optionale Komponente fehlt — jede einzelne fällt
auf eine klare Statusmeldung zurück statt auf einen Fehler.

**Nicht gebaut**, auch wenn ältere Notizen das nahelegen: Shelly-Steuerung und SIP/DTMF-Einwahl.
Für beides existiert kein Code und keine Abhängigkeit.

## Schnellstart — nur das Dashboard, ohne Audio-Hardware

Das ergibt in etwa zwei Minuten ein laufendes Dashboard zum Ansehen. Kein Dante, kein Interface.

```bash
git clone https://github.com/r1marcus/Akouo.git && cd Akouo
python3.12 -m venv .venv
.venv/bin/pip install -r api/requirements.txt
mkdir -p data && .venv/bin/alembic upgrade head
.venv/bin/uvicorn main:app --app-dir api --host 127.0.0.1 --port 8000
```

<http://127.0.0.1:8000> öffnen. Das Dashboard läuft; die Aufnahmekarte meldet den Dienst als
offline, und das ist richtig — er wurde nicht gestartet.

Für den echten Aufbau — Audio, Autostart, Transkription, Stream, NAS — siehe
**[`docs/betrieb.md`](docs/betrieb.md)**. Dort ist ein bestimmter Rechner beschrieben; das auf den
eigenen zu übertragen, ist die eigentliche Arbeit.

## Wie es funktioniert

```
 Pult ──(Dante / USB)──┐
                       ▼
       ┌───────────────────────────────┐
       │ audio-service  :8001          │   nativ, launchd — kein Docker
       │ ein offener CoreAudio-Strom   │
       │  ├─ Aufnahmepaar → WAV        │
       │  ├─ Streampaar → FFmpeg ──────┼──→ Icecast / RTMP
       │  ├─ Mithörpaar → FFmpeg ──────┼──→ lokaler Mithörweg
       │  └─ Pegel + Transkript ───────┼──→ whisper.cpp :8090
       └───────────────┬───────────────┘
                       │ HTTP
       ┌───────────────▼───────────────┐
       │ api  :8000                    │   FastAPI + Jinja2 + HTMX + Alpine
       │ Dashboard, Checklisten,       │
       │ Marker, Export, Archiv        │──→ Ollama :11434
       │ SQLite + Alembic              │──→ SMB-Freigabe
       └───────────────────────────────┘
                       ▲
                Tablet / Browser
```

**Das Entscheidende:** Ein einziger CoreAudio-Strom versorgt Aufnahme, Pegelanzeige, Livestream und
Transkription gleichzeitig. Öffnet ein zweiter Prozess dasselbe Gerät, gibt es Aussetzer — deshalb
ist der Audio-Dienst ein eigener, nativer Prozess, dem das Gerät gehört, und deshalb läuft er nicht
in Docker (USB-Audio-Durchgriff ist auf macOS kaputt). Die Begründungen zu den übrigen
Entscheidungen stehen in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Datenschutz

Das hier gehört gelesen, bevor man es irgendwo laufen lässt.

Akouo nimmt **ganze Gottesdienste** auf, verschriftet sie und lässt ein lokales Sprachmodell
Zusammenfassungen, Stichworte und Zuordnungen erzeugen. Nach der DSGVO ist die religiöse
Überzeugung eine **besondere Kategorie** personenbezogener Daten (Art. 9). Die Aufgenommenen sind
Gemeindemitglieder und Gäste, keine Angestellten, und sie haben in nichts eingewilligt, indem sie
sich hingesetzt haben.

Was je Gottesdienst entsteht: eine vollständige WAV, `transcript.txt` und `transcript.jsonl`, die
exportierten MP3s, `marker.json`, `ablauf.txt` und Datenbankzeilen mit Themen, Zusammenfassungen
und Bibelstellen. **Alles bleibt auf dem Rechner** — nichts wird irgendwohin gesendet, und die KI
läuft lokal. Das ist ein echter Vorteil, und es macht die Verarbeitung für sich genommen nicht
rechtmäßig.

Als Betreiber musst du für deine Lage klären: eine Rechtsgrundlage, wie du die Gemeinde
informierst, wie lange Aufnahmen bleiben, wer Zugriff hat und wie Löschung funktioniert. Wer
Gesang streamt, hat es mit GEMA oder dem Pauschalvertrag seiner Kirche zu tun.

Dieses Projekt gibt keine Rechtsberatung und übernimmt keine Verantwortung für deine Installation.

## Sicherheit

- **Keine Anmeldung, kein TLS.** Wer Port 8000 erreicht, hat volle Kontrolle: Aufnahme stoppen,
  Stream starten, Marker löschen.
- Gebaut für ein vertrauenswürdiges LAN. **Nicht ins Internet stellen.** Wer Fernzugriff braucht,
  nimmt VPN oder ein Mesh-Netz, keine Portweiterleitung.
- Dauerbetrieb unter macOS will abgeschalteten Ruhezustand und automatische Anmeldung, und beides
  schwächt den Rechner. Betrachte ihn als Teil deiner Angriffsfläche.
- Gib dem NAS-Dienstkonto Zugriff auf genau eine Freigabe. Mehr nicht.

Sicherheitslücke gefunden? Siehe [`SECURITY.md`](SECURITY.md).

## Dokumentation

| | |
|---|---|
| [`docs/betrieb.md`](docs/betrieb.md) | **Deutsch.** Betriebsanleitung: Dienste, Konfiguration, Bedienung, Pegel, Transkription, Automatik, Fehlersuche. Die nützlichste Datei hier. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Englisch. Wie die Teile zusammenpassen und warum so. |
| [`docs/stand.md`](docs/stand.md) | Deutsch. Was läuft, was bekannt kaputt ist, was fehlt. |
| [`docs/roadmap.md`](docs/roadmap.md) | Deutsch. Was als Nächstes kommt, nach Nutzen sortiert. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Wie man daran arbeitet, auch ohne die Hardware. |

## Mitarbeit

Patches willkommen, mit einem Vorbehalt, der gleich am Anfang stehen soll: **Du kannst das
vermutlich nicht lokal laufen lassen.** [`CONTRIBUTING.md`](CONTRIBUTING.md) sagt, was ohne
Dante-Lizenz und Mischpult möglich ist — Tests, Dokumentation, die Regeln der Erkennung und die
Oberfläche nämlich schon.

## Lizenz

[Apache License 2.0](LICENSE). Fremdkomponenten und Markenhinweise: [`NOTICE`](NOTICE) und
[`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md).

## Dank

Gebaut auf [FastAPI](https://fastapi.tiangolo.com/), [htmx](https://htmx.org/),
[Alpine.js](https://alpinejs.dev/), [Tailwind CSS](https://tailwindcss.com/),
[whisper.cpp](https://github.com/ggerganov/whisper.cpp), [Ollama](https://ollama.com/),
[sounddevice](https://python-sounddevice.readthedocs.io/) und [FFmpeg](https://ffmpeg.org/).
