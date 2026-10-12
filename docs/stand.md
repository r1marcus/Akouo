# Projektstand Tontechnik

Stand: 12. Oktober 2026. Was funktioniert, was belegt ist, was fehlt.
Betriebsanleitung: [betrieb.md](betrieb.md) · Nächste Schritte: [roadmap.md](roadmap.md) · Architektur: [../ARCHITECTURE.md](../ARCHITECTURE.md)

---

## 1. Kurzfassung

Das System nimmt auf der produktiven Hardware (Mac Studio) einen Gottesdienst durchgehend über
Dante auf, verschriftet ihn **live** mit, der Tontechniker markiert die Programmpunkte am
Dashboard, und danach schneidet das System jeden Punkt als eigene MP3 heraus und wertet ihn per
lokalem Sprachmodell aus. Aufnahme, Live-Transkript, Marker, Schnitt und Auswertung sind
**an echter Hardware getestet**.

Seit dem 27. September kann das System die Programmpunkte auch **selbst erkennen**: ein
zuschaltbarer Automodus setzt die Marker, rät den Typ und schärft ihn nach, während der Beitrag
läuft. Der Tontechniker korrigiert statt zu markieren.

Offen sind vor allem die Schritte *nach* dem Schnitt: Archivansicht, NAS-Upload, Streaming,
Veröffentlichung — und die Anmeldung.

---

## 1a. Gebaut, aber noch nicht in Betrieb — offene Entscheidung

**Am 11. Oktober wurde einiges gebaut, das noch nicht wirkt.** Der Code liegt im Repository,
die Dienste laufen aber unverändert weiter: Python ist kalt, und die Datenbank steht noch auf
Migrationsstand 013.

Nachprüfbar:

```bash
curl -s -o /dev/null -w "%{http_code}\n" localhost:8001/api/config/erkennung
# 404 = alter Code läuft noch · 200 = neue Fassung ist aktiv

sqlite3 data/tontechnik.db "SELECT version_num FROM alembic_version;"
# 013 = noch nicht migriert · 014 = aktiv
```

### Was mit der Aktivierung dazukommt

- **Gottesdienstarten und Beitragsarten stehen in der Datenbank** statt im Code und lassen sich
  in der Oberfläche anlegen, umbenennen, sortieren und ausblenden. Mitgewandert ist, was die
  Erkennung über einen Typ weiß — Klangklasse, typische Dauer, Ankerfunktion. Ein selbst
  angelegter Typ nimmt dadurch an der automatischen Erkennung teil, statt ignoriert zu werden.
- **Die Einstellungsseite deckt alles ab**: 19 Werte in fünf Bereichen (Export, Erkennung,
  Schnittkanten, Livestream, NAS-Ablage) statt bisher zwei Felder.
- **Die Ansage-Regeln sind editierbar**, mit Probe an einem Beispielsatz vor dem Speichern.
- **Das NAS-Ordnerlayout ist eine Vorlage** statt fest verdrahtet, mit Vorschau.
- Eine bestehende Fehlerquelle ist beseitigt: die Liste der Beitragsarten gab es **zweimal**
  (`api/service_types.py` und `audio-service/recorder.py`), und die beiden wichen bereits
  voneinander ab — der Audio-Dienst kannte `Unbenannt`, die API nicht. In der Datenbank steht
  ein solcher Marker, dessen Wert das Auswahlfeld im Dashboard nicht anzeigen konnte.

### Geschützte Namen

An einigen Namen hängt Logik. Sie lassen sich umsortieren und ausblenden, aber **nicht umbenennen
oder löschen** — die Oberfläche nennt beim Versuch den Grund:

| Name | Warum geschützt |
|---|---|
| `Predigt`, `Orchester` | Anker der Programmzuordnung — an ihnen gilt alles davor als erledigt |
| `Chorlied`, `Jugendchor`, `Gedicht`, `Kindergeschichte`, `Gemeindegesang` | kommen in den Ansage-Regeln vor |
| `Hochzeit` | schaltet die Brautpaar-Felder und das NAS-Layout `Hochzeiten/…` frei |
| `Sonstiges` | eigenes NAS-Layout |

### Was zu tun ist

Der Ablauf steht in [betrieb.md, Abschnitt 8a](betrieb.md), im Unterabschnitt *Der erste Lauf*.
Kurz: sichern, beide Dienste anhalten, `alembic upgrade head`, `make css`, dann die beiden
Installer laufen lassen. Dauert etwa eine Viertelstunde.
**Nicht an einem Gottesdienst-Wochenende und nicht bei laufender Aufnahme.**

**Der Umstieg auf die Akouo-Dienstnamen hängt mit dran.** Der Rechner trägt noch die Labels
`de.tontechnik.api`, `com.tontechnik.audio`, `de.tontechnik.whisper`, `de.tontechnik.dashboard`;
die Installer im Repository richten `de.akouo.*` ein und entladen die Vorgänger dabei selbst.
Beides in einem Zug zu machen, spart den zweiten Dienst-Neustart. Datenbank
(`data/tontechnik.db`) und venv (`~/tontechnik-audio-venv`) behalten ihre Namen — der
Autostart-Installer erkennt eine vorhandene Datenbank und benutzt sie, statt eine leere
`akouo.db` anzulegen.

Danach kann `api/templates/settings.html` gelöscht werden — die Datei wird seit dem Umbau nicht
mehr gerendert und liegt nur noch da, weil die laufende API sie bis zum Neustart erwartet.

### Noch nicht gebaut

- Der Bereich für die Werte, die nur über die `.env` gehen (Kanäle, Whisper, Ollama,
  Aufnahmeordner) samt Neustart-Knopf, gesperrt solange eine Aufnahme läuft.

---

## 2. Was zuletzt gebaut wurde (15. bis 20. September 2026)

### Betrieb
- API (Port 8000) startet beim Hochfahren über einen LaunchDaemon, Audio-Service (Port 8001) und
  das Dashboard in Safari nach der Anmeldung. Kein Docker.
- Die API fand den Audio-Service vorher gar nicht (feste Docker-Adresse) — behoben.
- uvicorn beendet sich jetzt zuverlässig (`--timeout-graceful-shutdown 3`), sonst hängt der
  Neustart an den offenen Pegel-Verbindungen.

### Dashboard
- Neugestaltung im dunklen Studio-Look, Touch-tauglich, Handy- und Tablet-Breite geprüft.
- Checkliste ein- und ausklappbar, nach Gruppen sortiert, Zustand bleibt erhalten.
- Gottesdienst-Auswahl in der Kopfzeile: vier große Kacheln für die regelmäßigen Dienste, der Rest
  unter „Weitere“. Gilt für alle Geräte, ist während der Aufnahme gesperrt, aktiviert die
  passende Checkliste.
- „Aufnahme starten“ startet sofort, ohne Rückfrage. Stoppen braucht zwei Tipps.
- Pause und Fortsetzen, Fehlermeldungen als Einblendung statt `alert()`.
- Behobene Fehler: Stoppen stoppte die Daueraufnahme nicht, der Startdialog schloss sich sofort
  wieder, die Typenliste war durch fehlerhaft eingebettetes JSON zerstört, das CSS war seit März
  nicht gebaut und enthielt Fremdklassen aus `.venv`.

### Audio
- **Pegelanzeige läuft immer**, nicht nur während der Aufnahme. Der Eingang bleibt dauerhaft offen
  und wird nach Ab-/Anstecken automatisch neu geöffnet.
- Speicherleck behoben: Die Stream-Warteschlange lief früher bei jeder Aufnahme voll, auch ohne
  Stream (rund 1,3 GB in zwei Stunden).
- **Umstellung auf Dante** (18.09.): Gerätesuche über `AUDIO_DEVICE_NAMES` statt fest „Focusrite“,
  48 kHz, Aufnahme und Stream auf **Dante-Kanal 44+45** (`AUDIO_RECORD_CHANNELS`,
  `AUDIO_STREAM_CHANNELS`).

### Marker und Schnitt
- **Tracks haben jetzt Anfang und Ende.** Typ antippen startet den Abschnitt, „Track beenden“
  schließt ihn. Was dazwischen liegt (Umbau, Zwischenreden), gehört zu keinem Segment.
- Vergisst man das Ende, gilt die alte Regel: bis zum nächsten Track oder bis zum Ende.
- „Alle exportieren“ schneidet nach der Aufnahme sämtliche Tracks in einem Durchgang.

### Belege aus echten Messungen
| Prüfung | Ergebnis |
|---|---|
| Aufnahme 81 s | Datei exakt 81,1 s, keine Aussetzer, beide Kanäle gleich |
| Laufende Aufnahme, 40 s gemessen | 40,02 s Audio geschrieben — samplegenau |
| Zwei Tracks mit Anfang/Ende | Segmente 2,0–6,1 s und 9,1–13,2 s, Pause dazwischen ausgelassen |
| Export | 2 MP3s, je 4,0 s, 48 kHz, Stereo, auf −3,4 dBFS normalisiert |
| Tests | 112 Tests grün (56 API, 56 Audio-Dienst) — `make test` |

---

### 20. September 2026 — Livestream läuft

- **Der Stream ließ sich vom Dashboard nie starten.** Die Oberfläche las das Ziel aus der
  Datenbank, die leer war; das echte Ziel steht in `audio-service/.env`. Fehlt ein Eintrag in den
  Einstellungen, nutzt die API jetzt das Ziel des Audio-Service. **Vom Betreiber live bestätigt.**
- Dashboard zeigt Ziel (ohne Zugangsdaten) und Kanalpaar statt einer fest verdrahteten Angabe.
- **Kanalaufteilung:** Aufnahme 44+45, **Livestream 46+47**. Der Stream-Mix lag rund 10 dB leiser
  als der Aufnahme-Mix — beim Stream wird nicht normalisiert.
- Ziel: `icecast://<icecast-host>:8000/<mount>`, Mithören unter `http://<icecast-host>:8000/<mount>`.

### 18. September 2026 — Dante, Live-Transkript, Tracks, KI

- **Dante Virtual Soundcard** statt Focusrite: Gerätesuche über `AUDIO_DEVICE_NAMES`, 48 kHz,
  Aufnahme und Stream auf Kanal 44+45 (frei einstellbar).
- **Live-Transkript** im Dashboard über der Checkliste, rund 10 Sekunden Verzögerung.
  whisper-server hält `large-v3-turbo` im Speicher, ein Häppchen dauert 0,35 s.
  Geschnitten wird an der leisesten Stelle, sonst zerreißt es Wörter.
- **Tracks mit Anfang und Ende:** großer Knopf „Track starten“, Typ danach wählbar,
  „Track beenden“ schließt ab. Was dazwischen liegt, gehört zu keinem Segment.
- **Schnittkanten-Automatik** mit Schalter in den Einstellungen (Standard an): zieht Anfang und
  Ende auf den echten Ton, lässt durchgehende Rede in Ruhe.
- **KI-Auswertung je Segment** über Ollama: Titel, Zusammenfassung, geprüfte Bibelstellen,
  Schlagwörter, Sprecher.
- **Ein Ordner je Aufnahme** mit `aufnahme.wav`, `transcript.txt`, `transcript.jsonl` und
  `exports/`. Alte flache Aufnahmen funktionieren unverändert.
- **„Alle exportieren“** schneidet nach dem Gottesdienst sämtliche Tracks in einem Durchgang.
- Behoben: Einstellungsseite konnte nie speichern (422), doppelte Transkriptzeilen durch zwei
  Abfrageschleifen, 404 auf `/favicon.ico`.

Belege: 40 s Echtzeit → 40,02 s Audio geschrieben; zwei Tracks samt Pause korrekt geschnitten;
Knopf 4 s zu spät → auf den echten Beginn korrigiert; Export im Sessionordner gelandet.

---

## 2b. Was am 27. September dazukam

### Geführter Ablauf
- Die Checkliste ist ein **eigener Schritt** zwischen Gottesdienstwahl und Dashboard, keine Karte
  mehr mittendrin. Nach „Übernehmen" landet man dort, arbeitet sie ab und geht weiter.
  Am 20. September blieben alle 20 Punkte ungehakt — genau das löst der Ablauf.
- Auf dem Dashboard bleibt eine Statuszeile `Checkliste 7/20`. Vorlagen bearbeiten steckt in den
  Einstellungen.

### Pegelverlauf
- Verlaufsband über die ganze Session plus Mini-Kurve je Marker, gespeist aus der wachsenden WAV.
  Beim Laden die ganze Kurve, danach nur das neue Stück; ein Reload verliert nichts.
  91 Minuten sind 8.036 Werte und 39 KB.

### Automatische Erkennung
- Grenzen, Sprache/Musik und Track-Typ werden aus dem Klang bestimmt, ohne trainiertes Modell.
  Gemessen über drei Gottesdienste: Grenzen 25/26, Sprache/Musik 26/26, Typ 20/26.
  Details und Grenzen der Methode in [betrieb.md](betrieb.md), Abschnitt 5d.
- Der **Automodus** setzt daraus Marker, rückdatiert auf den echten Anfang. Er wird ausdrücklich
  eingeschaltet und läuft nie von allein. Erster echter Einsatz am 27. September nachmittags.
- Das **Transkript** wird mitgelesen: Gebete an der direkten Gottesanrede, Gesang an Whispers
  Wiederholungen, und Ansagen sagen, was als Nächstes kommt — am Erntedankfest steckte das ganze
  Restprogramm mit Titeln in einem Satz. Ausgewertet mit Ollama, weil Whisper sich verhört
  („Prusa" statt „Prosa") und Muster daran scheitern.

### Verlässlichkeit
- **Von Hand gesetzte Grenzen überleben die Automatik.** Vorher warf die Korrektur beim Stoppen
  jede Handarbeit weg, ohne Hinweis — am 20. September traf das eine Predigt.
- **Marker lassen sich löschen.** Bis dahin blieb jeder Fehlgriff für immer stehen.
- **Der Ablauf liegt neben der Aufnahme** als `marker.json` und `ablauf.txt`, absturzsicher
  geschrieben. Vorher standen die Marker nur in der Datenbank.
- Gemeindegesang, Gebet, Ansage und Vermeldungen werden markiert, aber nicht exportiert.

### Mithören
- Lautsprecher in der Kopfzeile, gibt das Stream-Signal (Dante 46+47) direkt aus — ohne den
  Icecast-Umweg und auch dann, wenn noch nicht gesendet wird.

### Behobene Fehler
- Die Segment-Review zeigte während einer laufenden Aufnahme noch den vorigen Gottesdienst.
- Das Dashboard ließ sich auf dem Tablet seitlich wegschieben (873 px Inhalt, 753 px Fenster).
- Der Ende-Versatz eines Markers war unsichtbar — die Klicks landeten in der Datenbank, die Liste
  zeigte davon nichts.
- API und Audio-Dienst kannten unterschiedliche Track-Typen; ein Wechsel auf `Kindergeschichte`
  wäre abgelehnt worden.

---

## 2c. Was am 10. Oktober dazukam

- **Erntedankfest Teil 2 aufgeräumt und exportiert.** Erste Aufnahme, bei der die Automatik die
  Marker gesetzt hat: 23 Stück für 16 Programmpunkte. Nach Abgleich mit dem gedruckten Programm
  bleiben 17 — die 16 Punkte plus ein Gebet. 14 MP3s, drei Beiträge nur dokumentiert.
- **Typ wird live aus Ansagen und Klang abgeleitet.** Predigt und Orchester als Anker, dazwischen
  die angesagten Beiträge. 14 von 17 in richtiger Reihenfolge; vorher schlug die Automatik dort
  siebenmal Chorlied vor, in einem Gottesdienst ohne Chor.
- **Dubletten behoben.** Beiträge werden über Überlappung wiedererkannt statt über den Startpunkt.
- **Marker lassen sich löschen**, Gebetslied, Musikstück und Kinderlied kamen als Typen dazu
  (werden nie geraten, nur angesagt oder von Hand gesetzt).

Noch nie an laufendem Ton erprobt sind die Korrekturen von heute — sie sind an den fertigen
Aufnahmen nachgemessen, nicht im Betrieb bewiesen.

---

## 2d. Was am 11. Oktober dazukam

### Erster Gottesdienst, bei dem die Automatik getragen hat
Abschlussgottesdienst des Orchesterleiterseminars, 116 Minuten, 21 Beiträge. Gegen das
gedruckte Programm geprüft:

- **Struktur stimmte vollständig** — 21 Marker für 19 Programmpunkte plus zwei Gebete, keine
  Überlappung, keine Dublette.
- **Sprache gegen Musik durchgehend richtig**, auch bei Stellen, an denen Whisper nur Füllsätze
  ausgab.
- **Drei aufeinanderfolgende Klavierstücke sauber einzeln erkannt** (47, 45, 46 Sekunden). Das
  war der Fall, bei dem vorher unklar war, ob die Acht-Sekunden-Schwelle reicht — hier lagen
  24 bis 29 Sekunden dazwischen.
- **Falsch waren nur die Typen innerhalb der Musik**: fünfmal „Chorlied" in einem Gottesdienst
  ohne Chor. Zehn Korrekturen von Hand, zwei Minuten Arbeit.

Die Korrekturen vom Vortag haben alle gehalten: keine Überlappung mehr, der Automodus hat sich
nach dem Stoppen selbst beendet und geleert, das Ablaufdokument wurde geschrieben.

### Freies Thema
Im Gottesdienst-Dialog gibt es ein optionales Themenfeld, das für jeden Typ gilt. Es steht in
der Kopfzeile, wird an der Aufnahme gespeichert und landet im Dateinamen:
`2026-10-11_Sonntagmorgen_Orchesterleiter_Abschluss_13_Musikstück.mp3`. Migration 013.

### Archiv
Neuer Tab mit allen abgeschlossenen Aufnahmen. Ein Klick klappt dieselbe Segment-Karte auf wie
im Dashboard. Fehlt die Aufnahmedatei, steht es in der Zeile.

### Behobene Fehler
- **Ein beim Stoppen offener Track war nie exportierbar.** Seine Grenze wird aus der Uhr
  gerechnet, der Schreiber hört Sekundenbruchteile früher auf — der Export lehnte mit 422 ab.
  Beim Abschlussgottesdienst fehlten dadurch 677 Millisekunden und damit eine ganze Datei.
  Jetzt wird bis zum Dateiende geschnitten; erst mehr als fünf Sekunden darüber gilt als Fehler.
- **Der Automodus lief nach dem Stoppen weiter** und zeigte die Beiträge der alten Session. Er
  beendet sich jetzt selbst, und eine neue Gottesdienst-Auswahl setzt ihn zurück.
- **Die Segment-Review hing nur am Gottesdiensttyp**, nicht am Zeitpunkt — nach einer neuen
  Auswahl stand dort noch die Aufnahme des vorigen. Sie zeigt jetzt nur Aufnahmen, die nach der
  Auswahl begonnen haben.
- **Überlappende Marker**: Beiträge werden über jede Überschneidung wiedererkannt, nicht mehr
  über eine Schwelle von „mehr als die Hälfte", die im Betrieb bei 23 von nötigen 25 Sekunden
  versagt hatte.
- Die Marker-Liste lud nicht nach, solange der Automodus arbeitete — sie verglich gegen die
  Markerzahl des Audio-Dienstes, und der kennt die Marker des Automodus nicht.

---

## 2e. Vorbereitung der Veröffentlichung (11. Oktober 2026)

Das Projekt soll als **Akouo** öffentlich werden. Sieben parallele Prüfungen haben das
Repository durchgesehen; daraus wurden fünf Commits, die den Arbeitsbaum von 209 auf 126
verfolgte Dateien bringen. (Die Einstellbarkeit aus Abschnitt 1a kam danach dazu und bringt
ihn wieder auf 137.)

**Was aus dem Repository raus musste:**

| Was | Warum |
|---|---|
| `data/tontechnik.db` | war verfolgt und enthält echte Betriebsdaten: 19 Gottesdienste, 93 Marker, drei wörtliche Predigt-Transkripte mit KI-Zusammenfassungen, dazu die Spalten `couple_name` und `wedding_part`. Nur ausgetrackt, die Datei liegt unverändert da. |
| `docs/MAC-STUDIO.md` | enthielt das sudo-Passwort im Klartext, dazu SSH-Alias, LAN-Adresse, Tailscale-Namen. Liegt jetzt unter `~/Akouo-privat/`. |
| `docs/transkription.md` | nennt Gemeinde und einen Prediger namentlich, dazu ein Inventar von 16.875 Archivdateien mit wörtlichen Zitaten. Ebenfalls nach `~/Akouo-privat/`. |
| `.planning/` (87 Dateien) | beschreibt einen Mac Mini mit Docker und führt die KI-Funktionen als „out of scope“ — das Gegenteil des heutigen Stands. Ausgetrackt, liegt für das Werkzeug weiter da. |
| `streaming-service/` | toter Code, durch `audio-service/streamer.py` ersetzt. Sein `install.sh` war kaputt ausgeliefert: es lud ein Plist, dessen Interpreter nie erzeugt wird. |
| `Dockerfile`, `docker-compose.yml`, `docs/setup.md` | beschrieben einen Aufbau, den diese Anleitung selbst als nicht existent bezeichnet. |

**Dazugekommen:** `README.md`, `LICENSE` (Apache-2.0), `NOTICE`, `THIRD-PARTY-LICENSES.md`,
`SECURITY.md`, `CONTRIBUTING.md`, `ARCHITECTURE.md`, `Makefile`, `pyproject.toml`,
`requirements-dev.txt`, ein GitHub-Actions-Workflow und `api/.env.example`.

**Repariert, nicht nur beschrieben:**

- `tests/test_segment_export.py` schrieb in den **Produktionsordner** `~/Recordings/.../exports/`.
  Ursache: die Test-WAV heißt `session.wav`, nicht `aufnahme.wav`, deshalb fiel `export_dir()`
  auf den echten Ordner zurück. Drei Dateien von 14:18 liegen noch dort und können weg.
- `alembic.ini` hatte die Datenbank-Adresse hart verdrahtet und ignorierte `DATABASE_URL` —
  genau die Ursache der Versions-Schieflage vom 15.09.2026
  ([betrieb.md, Abschnitt 3](betrieb.md)). `migrations/env.py` liest jetzt
  zuerst die Umgebung.
- `.env.example` deckte 6 von 19 Variablen ab, jetzt alle 22. Darunter die beiden
  Kanal-Variablen, die kein `grep` findet, weil sie über eine Hilfsfunktion laufen.
- Der Web-API fehlte der Installer vollständig: Plist und Startskript lagen außerhalb des
  Repositorys und existierten nur als Codeblock in dieser Anleitung. Jetzt
  `autostart/install.sh` samt Vorlagen.
- Von fünfzehn Platzhaltern in den Plists wurden vorher nur zwei von einem Skript ersetzt.
- `api/static/alpine.min.js` hatte keinen MIT-Copyright-Hinweis — eine echte Lizenzverletzung.
- Die Standard-Kanäle `1,2`/`3,4` verlangten ein Vierkanalgerät und schlossen jedes
  Stereo-Interface aus. Default ist jetzt zweimal dasselbe Paar; **diese Anlage setzt die
  Kanäle ohnehin ausdrücklich in der `.env`**, hier ändert sich nichts.

**Noch offen, und zwar beim Betreiber:** das sudo-Passwort ändern (es stand im Klartext in der
Git-Historie des privaten Repositorys), das Icecast-Quellpasswort und das NAS-Dienstkonto
erneuern, und die Rechte des NAS-Kontos auf genau eine Freigabe einschränken — es kann derzeit
die Privatordner aller 25 NAS-Benutzer lesen (siehe „Rechte des NAS-Kontos“ in
Abschnitt 4).

**Ebenfalls offen:** Screenshots für das README (es gibt bisher kein einziges Bild),
ein Demo-Modus ohne Audio-Hardware, die Umbenennung von `audio-service` nach `audio_service`
(danach funktioniert `pytest` ohne Argumente), und das Anlegen des neuen öffentlichen
Repositorys.

---

## 2f. Zwei Repositories (12. Oktober 2026)

Die Veröffentlichung scheiterte zunächst an etwas, das in keiner Prüfung vom 11. Oktober stand:
**der Historie.** Die fünf Aufräum-Commits haben die Betriebsdaten aus dem Arbeitsbaum genommen,
nicht aus den Commits davor. In den 200 Commits ab März lagen weiter vier Fassungen von
`data/tontechnik.db` — die letzte mit 16 Aufnahmen, 59 Markern und Insight-Texten —, die 87
Planungsdateien und die internen Adressen des Gemeindenetzes. Letztere auch in
**Commit-Botschaften**, wo keine `.gitignore` und kein CI-Wächter sie erwischt.

Ein neues Remote allein löst das nicht: das Repository hatte dieselbe Historie, nur unter neuem
Namen. Deshalb jetzt zwei getrennte:

| Repository | Was drin ist |
|---|---|
| `r1marcus/Akouo` | die veröffentlichte Fassung. Ein Wurzel-Commit, 137 Dateien, keine Vorgeschichte. Der Entwicklungsweg steht in den Dokumenten, nicht in `git log`. |
| `r1marcus/Akouo-Archiv` | die vollständige Historie März–Oktober 2026 mit allem, was nicht nach draußen darf. **Bleibt privat.** |

Lokal zeigt `main` auf die veröffentlichte Fassung; die alte Historie liegt im Zweig
`archiv/historie-tontechnik` und als Bündel in `~/Code/akouo-historie-vor-veroeffentlichung.bundle`.

Ein Force-Push ins alte Repository hätte nicht gereicht: die acht `worktree-agent-*`-Zweige
hielten dieselben Objekte, und selbst nach deren Löschung bleiben unreferenzierte Objekte
serverseitig über ihre SHA abrufbar, bis GitHub aufräumt. Ein frisch angelegtes, leeres
Repository ist die einzige Variante, bei der die Daten das veröffentlichte Repository nie
berühren.

**Seit dem 12. Oktober ist `r1marcus/Akouo` öffentlich.** Der Zugriffsschutz wurde dabei
bewusst zurückgestellt (roadmap.md, Punkt 5): Das Dashboard hat keine Anmeldung, `SECURITY.md`
benennt es, und das Risiko trägt die eigene Anlage, nicht der Leser. Mitgewandert sind drei
Bilder der Oberfläche unter `docs/bilder/` — das Dashboard-Bild stammt aus der laufenden Anlage,
aber nur aus dem oberen Teil: Stream-Ziel und Live-Transkript bleiben draußen. Die anderen
beiden kommen aus einer Demo-Instanz mit leerer Datenbank.

Dabei fiel auf, dass die **Oberfläche durchgehend noch „Tontechnik" hieß** — Kopfzeile,
Seitentitel, Name der Home-Bildschirm-App und die Titel beider FastAPI-Anwendungen. Alles auf
Akouo gezogen. Nicht umbenannt: der Stream-Name, den Hörer im Player sehen; er steht seit dem
ersten Stream in ihren Playern und ist jetzt über `STREAM_NAME` einstellbar, Voreinstellung
unverändert.

Der Archivzweig liegt seit dem 12. Oktober auch im Archiv-Repository
(`r1marcus/Akouo-Archiv`, Zweig `historie-tontechnik`) — bis dahin existierten die elf neuesten
Commits nur auf dieser Platte.

---

## 2g. Dreisprachige Oberfläche (12. Oktober 2026)

Das Repository war englisch, das Werkzeug deutsch — und wer am Pult sitzt, liest nicht
unbedingt beides. Die Oberfläche spricht jetzt **Deutsch, Englisch und Russisch**, wählbar
pro Gerät über einen Umschalter in der Kopfzeile. Bedienung: [betrieb.md, Abschnitt
2a](betrieb.md).

**Deutsch ist die Ausgangssprache und zugleich der Schlüssel.** Im Template steht der
deutsche Satz, nicht ein Kürzel wie `dashboard.aufnahme.start`. Fehlt eine Übersetzung,
steht dort Deutsch — nie ein nackter Schlüssel, der im Betrieb wie ein Fehler aussieht.
255 Sätze je Sprache in `api/sprachen/{de,en,ru}.json`.

**Die Namen der Arten bleiben deutsch**, weil an ihnen Logik hängt: Anker der
Programmzuordnung, Ansage-Regeln, NAS-Vorlagen. Migration 015 legt daneben zwei Spalten für
Anzeigenamen je Sprache an, mit Übersetzungen für alle mitgelieferten Arten — und die sind
auf der Einstellungsseite editierbar, auch bei geschützten Arten.

### Was dabei schiefging und deshalb festgehalten ist

- **`@app.middleware("http")` reicht ContextVars nicht durch.** Das ist eine
  BaseHTTPMiddleware, und die ruft den nachgelagerten Teil in einer eigenen Task auf. Das
  Cookie stand richtig, die Oberfläche blieb deutsch. Jetzt eine reine ASGI-Middleware.
- **Jinja deutet `\b` im Zeichenkettenliteral als Steuerzeichen.** Der Platzhalter der
  Ansage-Regeln (`\bposaunenchor\b`) kam leer an.
- **Beim Normieren mehrzeiliger Schlüssel fielen abschließende Leerzeichen weg**, und aus
  `"Fehler " + status` wurde „Fehler404". Das Trennzeichen steht jetzt im Code statt im
  Übersetzungsschlüssel — dort sieht es niemand und jeder verliert es.
- **Der JavaScript-Helfer heißt `tr()`, nicht `t()`.** Die Templates nutzen
  `x-for="t in …"`, und der Schleifenname überdeckt im Alpine-Bereich jede gleichnamige
  Funktion — ausgerechnet dort, wo die Track-Knöpfe entstehen.

Geprüft wurde die Umstellung damit, dass **jedes Template auf Deutsch zeichengenau dasselbe
liefert wie vorher**. Dazu 16 Tests, darunter zwei, die eine vergessene oder verwaiste
Übersetzung in der CI auffallen lassen statt im Gottesdienst.

**Noch deutsch:** Meldungen, die aus dem Audio-Dienst kommen (er kennt die Sprache des
Geräts nicht), und rein technische 4xx-Texte wie `invalid filename`. Die russische Fassung
ist nicht von einem Muttersprachler gegengelesen.

---

## 3. Ablauf im Gottesdienst

1. Oben den Gottesdienst wählen — die Seite springt auf die Checkliste.
2. Checkliste abarbeiten, **Weiter zum Dashboard**.
3. **Aufnahme starten.** Das Live-Transkript läuft ab jetzt mit.
4. Entweder **Automatik einschalten** und nur noch korrigieren, oder pro Programmpunkt
   **Track starten** → Typ wählen → **Track beenden**. Beides geht auch nebeneinander.
5. **Stoppen** (zwei Tipps). Dabei werden die Schnittkanten nachgezogen — bei Markern, die von
   Hand verschoben wurden, bleibt die Handarbeit stehen.
6. **Alle exportieren** — die MP3s landen im `exports`-Ordner der Aufnahme. Gemeindegesang,
   Gebet, Ansagen und Vermeldungen werden dabei übersprungen.
7. Bei Bedarf **KI-Auswertung** je Segment.

Der Ablauf liegt danach als `ablauf.txt` und `marker.json` im Session-Ordner.

---

## 4. Was fehlt

### A — vor dem nächsten produktiven Einsatz
| Thema | Stand |
|---|---|
| **Gesamtaufnahme als MP3** | Es gibt nur Segmente. Die vollständige Aufnahme bleibt als WAV liegen (ca. 0,7 GB/Stunde) und wird nie aufgeräumt. |
| **NAS-Upload** | Wartet auf eine Berechtigung. Das NAS ist erreichbar, die Anmeldung als Dienstkonto geprüft — aber die Freigabe `Aufnahmen` verweigert dem Konto den Zutritt. Zugangsdaten liegen auskommentiert in `audio-service/.env`, Prüfskript unter `audio-service/scripts/check_nas.py`. Sobald freigeschaltet: Zeilen einkommentieren, prüfen, Audio-Dienst neu starten. |
| **Rechte des NAS-Kontos** | Das Dienstkonto kann die Freigabe `homes` öffnen und sieht die Privatordner aller 25 Benutzer. Für einen Dienst, der MP3s ablegt, deutlich zu viel. |
| **Aussteuerung** | Eingangspegel rund −24 dBFS Spitze. Der Export normalisiert auf −3 dB und hebt dabei das Grundrauschen mit an. Besser am Pult anheben. |

### B — bald
| Thema | Stand |
|---|---|
| **Keine Anmeldung** | Jeder im WLAN kann die Technik steuern. Vor Einsatz in einem Netz mit Gästen lösen. |
| **Automatik am Aufnahmeanfang** | Der Ruhepegel pendelt sich erst ein; dadurch kann der erste Beitrag mehrfach angelegt werden. Erkennbar an gleichem Ende bei verschiedenem Anfang. Lösung: Beiträge über Überlappung wiedererkennen statt über den Startzeitpunkt. |
| **Chorlied gegen Gruppenlied** | Klanglich nicht trennbar, Länge überdeckt sich vollständig. Nur die Ansage entscheidet. |
| **Gedicht gegen Gebet** | Über den Text nicht zu trennen — das Erntedank-Gedicht spricht Gott häufiger an als die echten Gebete. |
| **Export nach dem Stoppen** | Die Schnittkanten werden automatisch nachgezogen, das Schneiden selbst startet noch der Knopf „Alle exportieren“. |
| **Marker-Drift bei Aussetzern** | Marker-Zeiten kommen von der Uhr, die Datei von den Samples. Fällt der Eingang kurz aus, verschieben sie sich gegeneinander. |
| **Tests schreiben in den Produktionsordner** | `test.mp3` und Co. landen in `~/Recordings/Tontechnik/exports/`. |
| **Alte Aufnahme-Endpunkte** | Der Einzelaufnahme-Recorder (`/start`, `/stop`, `/api/recordings/*`) läuft parallel mit und verwirrt. |
| **„Als Nächstes“-Liste** | Nur auf dem Gerät sichtbar, das die Aufnahme gestartet hat. |
| **Manuelle Tests der Phasen 4–7** | 15 Punkte stehen noch auf „pending“, obwohl vieles inzwischen an echter Hardware lief. |

### Bewusst nicht vorgesehen
- **Kein Veröffentlichen aus dem Dashboard.** Die Aufnahmen liegen im Sessionordner,
  veröffentlicht wird über NAS und Webseite. Der leere Bereich „Aufnahmen“ und der tote
  Wiedergabepfad `/recordings` wurden am 18.09.2026 entfernt.
- Der Tab **Archiv** kam am 11.10.2026 doch dazu, aber nur zum Wiederöffnen abgeschlossener
  Aufnahmen für die Segmentprüfung — nicht als Abspiel- oder Veröffentlichungsweg.

### C — Roadmap und Hygiene
- **Phase 8 (AI-01, AI-02) ist im Kern erfüllt:** Transkription läuft live statt nachgelagert,
  Metadaten werden je Segment erzeugt. Offen bleibt die Veröffentlichung dieser Daten
  (Webseite, NAS, Suche) und ein größeres Sprachmodell für bessere Zusammenfassungen.
- Erledigt am 11.10.2026: `data/tontechnik.db` ist nicht mehr verfolgt, und `.gitignore` deckt
  `.venv`, `__pycache__`, das Tailwind-Binary, Aufnahmen und `.env` ab. Zwei CI-Wächter halten
  es so — einer gegen versionierte Audio- und Datenbankdateien, einer gegen interne Adressen.
- Die früheren Planungsdateien unter `.planning/` sind beim Öffentlichmachen entfernt worden:
  sie beschrieben einen Mac Mini mit Docker und führten die KI-Funktionen als „out of scope“.
  Die Entscheidungsbegründungen daraus stehen jetzt in [../ARCHITECTURE.md](../ARCHITECTURE.md).

---

## 5. Technischer Stand

| | |
|---|---|
| Hardware | Mac Studio (M4 Max), macOS 26, Benutzer `<benutzer>` |
| Audio | Dante Virtual Soundcard, 64 Kanäle, 48 kHz, Latenz „High“ |
| Aufnahme | Dante 44+45 → WAV, Stereo, 48 kHz, 16 Bit, ein Ordner je Session |
| Transkription | whisper.cpp large-v3-turbo über whisper-server auf Port 8090 (LaunchAgent) |
| KI-Auswertung | Ollama lokal, Modellwahl über `OLLAMA_MODELS` |
| Export | MP3 192 kbit/s, 48 kHz, −3 dBFS, je 1 s Blende |
| Web | `http://<rechner>.local:8000` — immer über den Namen, die IP wechselt per DHCP. Von unterwegs über Tailscale: `http://<tailscale-adresse>:8000` |
| Stream | Icecast `<icecast-host>:8000/<mount>`, Quelle = Dante 46+47 |
| Datenbank | SQLite, Alembic-Stand **013** — Ziel 014, siehe Abschnitt 1a |
| Tests | **164 Tests** in zwei Umgebungen (102 API, 62 Audio-Dienst), dazu CI auf ubuntu-24.04 |

**Committet und gepusht:** alles — siehe Abschnitt 2f zu den beiden Repositories. Nicht im Repo:
die laufende Datenbank `data/tontechnik.db`, die Aufnahmen selbst und `audio-service/.env`
(enthält das Icecast-Passwort).

---

## 6. Wenn hier jemand weiterarbeitet

**Zuerst lesen:** [betrieb.md](betrieb.md) — dort stehen Dienste, Konfiguration, Bedienung,
Schnellreferenz und Fehlersuche.

**Die drei wichtigsten Eigenheiten:**
1. **Audio läuft nicht in Docker** und auch sonst nichts — beide Dienste laufen nativ über launchd.
   Docker ist auf diesem Rechner nicht installiert.
2. **Es gibt nur einen Audiostream aufs Gerät.** Aufnahme, Pegelanzeige, Livestream und
   Transkription hängen alle am selben offenen Eingang im Audio-Service. Wer einen zweiten
   Zugriff aufmacht, riskiert Aussetzer.
3. **Vor jedem Dienst-Neustart prüfen, ob eine Aufnahme läuft**
   (`curl -s localhost:8001/api/session/status`). Ein Neustart beendet sie.

**Nächste sinnvolle Schritte, nach Nutzen sortiert:**
1. **NAS-Upload aktivieren** — Code ist fertig, es fehlen `NAS_SERVER`, `NAS_SHARE`, `NAS_USER`,
   `NAS_PASSWORD` in `audio-service/.env`. Damit erfüllt sich EXP-02.
2. **Zugriffsschutz** — das Dashboard ist ohne Anmeldung im ganzen WLAN erreichbar.
3. **Aussteuerung** — Eingangspegel liegt rund 15 dB zu niedrig; besser am Pult anheben als
   nachträglich normalisieren.
4. **Veröffentlichung** — Transkripte und KI-Metadaten liegen bislang nur lokal; Webseite und
   Suche sind offen.
5. **Manuelle Tests der Phasen 4–7 nachziehen** — vieles davon ist inzwischen an echter Hardware
