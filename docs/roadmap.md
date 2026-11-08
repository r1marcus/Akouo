# Roadmap

Was als Nächstes gebaut werden soll, nach Nutzen sortiert. Was heute läuft, steht in
[stand.md](stand.md), wie man es bedient in [betrieb.md](betrieb.md).

## Beschlossen am 12. Oktober 2026

Die Reihenfolge ist damit festgelegt, bis sie jemand ändert:

1. ~~**Aktivierung**~~ — erledigt am 16. Oktober 2026, samt `de.akouo.*`-Dienstnamen und
   dreisprachiger Oberfläche. Siehe [stand.md, Abschnitt 1a](stand.md).
2. **Umbenennung `audio-service` → `audio_service`** — danach, als eigener Schritt. Sie fasst
   Importe, Tests, Plists, Makefile, Installer und Doku gleichzeitig an; mit in die Aktivierung
   genommen, würde sie deren Befehle ungültig machen (`bash audio-service/install.sh` gibt es
   danach nicht mehr). Erst aktivieren, prüfen, dann umbenennen.
3. ~~**`.env`-Bereich auf der Einstellungsseite**~~ — gebaut am 6. November 2026 (betrieb.md, Abschnitt 4)., mit Neustart-Knopf daneben — gesperrt, solange
   eine Aufnahme läuft. Danach ist nichts mehr ausschließlich über die Kommandozeile einstellbar.
4. Dann die Punkte unten, in dieser Folge: **Programmblatt vorab einlesen** (Punkt 2),
   **Statusanzeige der Dienste** (Punkt 6), **Sprecher erkennen** (Punkt 4).

**Bewusst zurückgestellt:** der **Zugriffsschutz** (Punkt 5). Das Dashboard bleibt vorerst ohne
Anmeldung, und `SECURITY.md` benennt das offen. Die Entscheidung gilt für ein Netz, in dem nur
Technikteam und Gemeinde sind — sie ist neu zu treffen, sobald Gäste ins selbe WLAN kommen.

**NAS läuft seit dem 16. Oktober 2026**, mit fortlaufender Nummerierung, Typ im Dateinamen,
Tags, Cover und zusätzlicher Ablage im CopyCenter — siehe [stand.md, Abschnitt 2h](stand.md).
Die schon vorher geschnittenen Segmente wurden nicht nachgeladen: Sie lagen im Archiv bereits
von Hand abgelegt.

**Dazugekommen am 18. Oktober 2026**, noch ohne festen Platz in der Reihenfolge:

- **Telefon-Übertragung einschalten** — gebaut, wartet auf SIP-Zugangsdaten
  ([stand.md, Abschnitt 2i](stand.md)).
- ~~**Zwei Fragen beim Export.**~~ Gebaut am 6. November 2026 (betrieb.md, „Online stellen“). Ins Archiv (NAS) geht immer alles; zusätzlich je Export wählbar:
  „auf die Station laden" und „online stellen" (Gemeinde-App `Church_Tool`). Die „Station" ist
  die USB-Station auf dem NAS, also das CopyCenter — das läuft schon (betrieb.md, „Zweite
  Ablage: CopyCenter"). Offen ist nur „online stellen". Für „online stellen" sollen Akouos eigenes Transkript und die KI-Auswertung
  je Beitrag reichen — kein zweites Transkribieren, kein großes Modell während des Betriebs.
  Bis dahin lädt man von Hand nach, Ablauf in `Church_Tool/scripts/` (Uploader und Loader
  verstehen die Akouo-Dateinamen seit Commit 30b7606).

- ~~**Lautheit angleichen.**~~ Gebaut am 6. November 2026, umschaltbar (betrieb.md, „Lautheit“);
  Standard bleibt die Spitze. Ursprünglich: Der Export normalisiert nur die Spitze. Predigten liegen dadurch bis
  zu 9 dB unter den Liedern — gemessen am 18. Oktober (−16,9 bis −26,4 LUFS), und das alte
  Handarchiv schwankt genauso. Vorschlag: −16 LUFS, Spitze höchstens −1 dBTP, als Einstellung
  zwischen Spitze und Lautheit umschaltbar. Technisch: `loudnorm` von FFmpeg in zwei Durchgängen
  im Exporter, die Werte über `laufzeit.EXPORT` geschoben wie Bitrate und Blende.
- **Rechte des NAS-Kontos einschränken.** Es sieht noch alle Privatordner unter `homes`.
  Das ist Sache der NAS-Verwaltung, kein Entwicklungspunkt.
- **Typen der Erkennung.** „Neu erkennen" trifft die Grenzen fast immer, die Typen nur etwa zur
  Hälfte: Prosa gegen Predigt und Orchester gegen Chorlied mit Begleitung trennt der Klang nicht.
  Kandidat für Punkt 3 unten. **Muss besser werden** — am 8. November 2026 (Sonntagmorgen) lag die
  Automatik live mehrfach daneben: drei Gemeindegesänge am Anfang und das Gruppenlied als Chorlied,
  eine Ansage als Gedicht, das angesagte Kinderlied gar nicht erkannt, ein Chorlied nach der
  ersten Predigt fehlte. Die Ansagen hätten es gesagt („Lied Nummer …“, „die Kinder singen“) —
  also Ansagen stärker gewichten (Punkt 1) und das Programmblatt vorab einlesen (Punkt 2).

---

## 0. Vermeldungen in den Kalender der Gemeinde-App — gebaut am 6. November 2026

Umgesetzt mit Rückfrage statt automatisch (betrieb.md, „Vermeldungen in die Gemeinde-App“).
Die ursprüngliche Überlegung:

In den Vermeldungen steckt, was die Gemeinde in den nächsten Wochen vorhat: „Nächsten Samstag
um 15 Uhr Jugendstunde im Gemeindehaus“, „Am 23. ist Gebetsgemeinschaft für die Kranken“.
Akouo markiert die Vermeldungen ohnehin (exportiert sie aber nicht) und hat das Transkript.

**Ablauf:**
1. Nach dem Gottesdienst, in der Segmentliste: Knopf **„Termine aus den Vermeldungen“** am
   Vermeldungen-Track.
2. Das lokale Sprachmodell zieht daraus eine Liste, nur in fester Form (JSON):
   `art` (*termin* | *hinweis* | *gebetsanliegen*), `titel`, `datum`, `uhrzeit`, `ende`, `ort`,
   `text`. Relative Angaben („nächsten Samstag“, „am 23.“) löst Akouo selbst gegen das Datum
   des Gottesdienstes auf, nicht das Modell.
3. **Der Tontechniker sieht Karten und bestätigt jede einzeln** — Datum, Uhrzeit und Titel sind
   dort änderbar. Nichts geht ungefragt hinaus: ein verhörtes Datum im Kalender ist schlimmer
   als ein fehlender Termin.
4. Bestätigte *Termine* gehen in den Kalender der App (`archive.Event`: Datum, Start, Ende, Ort,
   Beschreibung; Art Gebets-, Bibel-, Jugendstunde, sonst *dienst*), *Hinweise* und
   *Gebetsanliegen* aufs Schwarze Brett (`bulletin.Announcement`, Kategorie *ankuendigung* bzw.
   *gebetsanliegen*, `expires_at` = Termin oder +14 Tage).
5. Vorher Abgleich: gibt es am Tag schon einen Termin mit ähnlichem Titel (der regelmäßige
   Gottesdienst, ein Dienstplan-Eintrag), wird nur ergänzt, nicht verdoppelt.

**Datenschutz:** Gebetsanliegen nennen oft Kranke beim Namen. Darum Sichtbarkeit *closed*
(nur angemeldete Mitglieder), Gebetsanliegen standardmäßig abgewählt, und es bleibt beim
Bestätigen von Hand.

**Was es braucht:** in Akouo einen Endpunkt fürs Auslesen und eine Kartenansicht; in der App
einen Weg, Termine und Brett-Einträge anzulegen (vorhandene API, Konto mit Rechten wie beim
Online-Stellen). Messen vorher: an fünf aufgezeichneten Vermeldungen prüfen, wie viele Termine
das Modell richtig zieht.

---

## 1. Zuordnung der Ansagen verbessern

Seit dem 10. Oktober leitet der Automodus den Typ live aus Ansagen und Klang ab — ohne
Programmblatt. Das Verfahren: Predigten und Orchesterstücke erkennt der Klang sicher und dienen
als **Anker**, dazwischen werden die angesagten Beiträge der Reihe nach eingehängt. An jedem
Anker richtet sich die Warteliste neu aus, damit ein fehlender Punkt sich nicht über den ganzen
Gottesdienst fortpflanzt.

Gemessen am Erntedankfest-Nachmittag: **14 von 17 Beiträgen in richtiger Reihenfolge**, bei
exakt getroffener Anzahl. Vorher schlug die Automatik dort siebenmal Chorlied vor, in einem
Gottesdienst ohne Chor.

**Was noch fehlt:** Die **Titel** sind nur Anzeige und gehen nicht in Marker oder Dateinamen —
von sechs vergebenen waren drei richtig. Der Typ ist auch bei schiefer Zuordnung brauchbar, weil
er zur Klasse passt und sichtbar im Auswahlfeld steht; ein falscher Titel wandert dagegen
unbemerkt ins Archiv.

Die zweite offene Stelle liegt in der hinteren Hälfte des Gottesdienstes: Fehlt ein Beitrag in
der Erkennung, verschiebt sich alles bis zum nächsten Anker. Mehr Anker wären der Hebel —
Gemeindegesang ließe sich zum Beispiel über die Ansage verankern, die ihn ausdrücklich benennt.

---

## 2. Programmblatt vorab einlesen

**Warum das ganz oben steht:** Es löst die beiden größten verbliebenen Schwächen auf einmal.

Die Erkennung kann Grenzen und sie kann Sprache gegen Musik — beides nahezu fehlerfrei. Woran
sie scheitert, ist die Unterscheidung *innerhalb* der Musik: Chorlied, Gruppenlied, Orchester und
Gemeindegesang klingen gleich und sind gleich lang. Im Erntedankfest-Nachmittag schlug sie
siebenmal „Chorlied" vor, obwohl im ganzen Programm kein einziges stand.

Angesagte Programmpunkte werden zwar schon mitgelesen (Stufe 0), aber die Zuordnung auf die
erkannten Beiträge ist abgeschaltet: In der Simulation saßen die Titel durchgehend zwei
Positionen zu weit vorn, weil ein nie angesagter Punkt in der Liste fehlte und der Versatz sich
fortpflanzte.

Das gedruckte Programm hat dieses Problem nicht — es ist **vollständig und in der richtigen
Reihenfolge**. Damit wird aus Raten ein Abhaken.

**Umriss:** Programmblatt (PDF oder Foto) auf der Einstellungsseite hochladen, vom lokalen
Sprachmodell in die Beitragsliste übersetzen, vor dem Gottesdienst zur Kontrolle anzeigen.
Während der Aufnahme hakt der Automodus ab, statt zu raten. Titel wandern in Dateinamen und
MP3-Tag.

**Offene Frage:** Wie verlässlich erkennt das Modell ein abfotografiertes Blatt? Der Wortschatz
unterscheidet sich vom gesprochenen („Musikstück", „Allgemeines Lied", „Gebetslied"); die
Zuordnung steht in betrieb.md, Abschnitt 5d.

---

## 3. Dreistufige Klassifikation sauber trennen

Heute vermischt die Erkennung schnelle und langsame Urteile. Sie soll stattdessen in klar
getrennten Stufen arbeiten, jede mit eigenem Zeitpunkt und eigener Verlässlichkeit:

| Stufe | Wann | Mittel | Aufgabe |
|---|---|---|---|
| 1 | nach 3 s | Klangmerkmale | Musik oder Sprache |
| 2 | nach 45 s, dann laufend | Länge, Flachheit, Ansage | erster Typvorschlag, schärft sich nach |
| 3 | nach 90 s und nach 5 min | Sprachmodell | Prüfung mit Ansage, Inhalt und bisherigem Ablauf |

Stufe 3 läuft bewusst nicht am Ende: Eine Predigt hat dann 2700 Wörter, und nach fünf Minuten
durchgehender Einzelrede ist ohnehin klar, was es ist. Zweimal früh fragen bringt mehr als
einmal spät.

**Rangfolge bei Widerspruch:** Hand des Tontechnikers schlägt alles. Eine Ansage schlägt Modell
und Klang, weil sie eine Tatsachenaussage ist. Das Modell schlägt den Klang. Der Klang schlägt
die reine Längenvermutung. Einzige Ausnahme: Hört niemand Gesang, ist es instrumental, egal was
angesagt wurde.

**Bedingung:** Das Modell darf nur aus der festen Typenliste antworten, und fällt Ollama aus,
bleibt Stufe 2 stehen. Die Automatik darf nie an einem Sprachmodell hängen.

**Gemessen am 6. November 2026** (`audio-service/scripts/pruefe_typen.py`, 15 Gottesdienste,
120 von Hand bestätigte Tracks): „Neu erkennen“ trifft den Typ in 70/120 (58 %). Stufe 3 mit
qwen2.5:14b nach dem Gottesdienst ergab 68/120 und ist deshalb aus (`EINORDNUNG_AN=1` schaltet sie
ein). Predigt 28/28 und Chorlied 18/20 sitzen; die Fehler liegen fast ganz in den Musik-Unterarten
(Gruppenlied, Kinderchor, Orchester → Chorlied: 15 Fälle) und bei Prosa → Predigt (5 von 7).
Beides trennt weder Klang noch Text sicher — das kann nur die Ansage oder das Programmblatt.

**Dazu gehört:** Jede Stufe protokollieren — was sagte 1, was 2, was 3, und was war es wirklich.
Ohne diese Aufzeichnung lässt sich nach ein paar Gottesdiensten nicht beurteilen, ob Stufe 3
ihren Aufwand wert ist.

---

## 4. Sprecher erkennen

Zwei Dinge aus einer Technik: **wer predigt** und **wie viele Stimmen** ein Abschnitt hat.

Kein Training nötig — fertige Modelle (ECAPA-TDNN über SpeechBrain, oder pyannote) verwandeln
Sprache in einen Stimmvektor. Man meldet jeden Prediger einmal mit zwei, drei Minuten aus einer
alten Aufnahme an und vergleicht danach. Das Anmeldematerial liegt vor: jedes `*_Predigt.mp3` im
Archiv ist sauber geschnittene Einzelsprache.

Der Nebennutzen ist die Zahl der Stimmen, und die löst das Anspiel: Ein Anspiel ist eine
Geschichte mit mehreren Sprechern, klingt wie eine Predigt und kann zwanzig Minuten dauern. Weder
Klang noch Länge trennen das — die Anzahl der Stimmen schon.

**Zu bedenken:** Das sind biometrische Stimmdaten von Gemeindemitgliedern. Solange es nur die
Prediger sind und die Bescheid wissen, unproblematisch — aber es sollte eine bewusste
Entscheidung sein.

---

## 5. Zugriffsschutz

Das Dashboard ist ohne Anmeldung im ganzen WLAN erreichbar. Jeder kann die Aufnahme stoppen, den
Stream starten, Marker löschen. Solange nur Technikteam und Gemeinde im Netz sind, vertretbar —
vor dem Einsatz in einem Netz mit Gästen zu lösen.

Dazu gehört auch das NAS-Dienstkonto: Das Dienstkonto kann die Freigabe `homes` öffnen und sieht die
Privatordner aller 25 Benutzer. Ein Konto, das MP3s ablegt, braucht Schreibrecht auf genau eine
Freigabe.

---

## 6. Statusanzeige für die Dienste — gebaut am 6. November 2026

Vier Dienste müssen laufen, damit ein Gottesdienst aufgenommen werden kann: API, Audio-Dienst,
whisper und Ollama. Alle starten automatisch — aber **kommt einer nach dem Hochfahren nicht hoch,
merkt man es erst, wenn man vor dem Dashboard sitzt.** Für eine Anlage, die sonntags
funktionieren muss, ist das die falsche Reihenfolge.

**Umriss:** Eine Zeile im Kopf oder auf der Einstellungsseite, die alle vier prüft und bei
Störung deutlich wird. Die Daten liegen schon vor — `/healthz`, `/api/session/status` mit
`input_available`, der whisper-Port und `ollama list`. Dazu gehört auch der Dante-Eingang: Steckt
die Soundkarte nicht, nützt der beste Dienst nichts.

Sinnvoll wäre, das **vor** dem Gottesdienst zu prüfen statt mittendrin — also auf der
Checklisten-Seite, wo ohnehin die Vorbereitung abgearbeitet wird. Eine Zeile „Alle Dienste
bereit" oben in der Checkliste wäre der natürliche Ort.

---

## 7. Alte Aufnahmen wieder öffnen — gebaut am 11. Oktober

Erledigt: Der Tab **Archiv** listet alle abgeschlossenen Aufnahmen, ein Klick klappt die
Segment-Karte auf. Fehlt die Aufnahmedatei, steht es in der Zeile.

*Ursprüngliche Beschreibung:* Heute zeigt das Dashboard immer nur die laufende Aufnahme. Eine vergangene wieder anzusehen —
Marker prüfen, Typ korrigieren, einzelne Segmente nachexportieren — geht nur über die Datenbank
oder indem man wartet, bis sie zufällig die „zuletzt gestoppte" ist.

**Umriss:** Eine Liste der Aufnahmen (Datum, Gottesdienst, Thema, Anzahl Beiträge, exportiert
ja/nein), ein Klick öffnet sie in der bekannten Ansicht mit Verlaufsband, Marker-Liste und
Export. Die Daten liegen vollständig vor — Marker in der Datenbank, Pegelkurve über
`/api/sessions/levels?session_id=…`, der Ablauf als `marker.json` im Session-Ordner.

Wichtig dabei: Eine geöffnete alte Aufnahme darf nicht mit der laufenden verwechselt werden
können. Deutlich als Archiv kennzeichnen, und die Automatik rührt sie nicht an.

---

## Kleineres, das liegen geblieben ist

- **Gesamtaufnahme als MP3.** Es gibt nur Segmente; die WAVs bleiben liegen (0,7 GB pro Stunde)
  und werden nie aufgeräumt. Aktuell rund 4 GB.
- **Karteileichen.** Session 12 zeigt auf fremdes Audio (ihr Ordner wurde gelöscht und der Name
  neu vergeben), Session 16 ist eine 0-Minuten-Aufnahme.
- **Aussteuerung.** Eingangspegel rund 15 dB zu niedrig; der Export normalisiert auf −3 dB und
  hebt dabei das Grundrauschen mit an. Besser am Pult anheben. Hängt mit der Lautheit oben
  zusammen: Wird auf Lautheit normalisiert, wird das Rauschen leiser Predigten noch weiter
  angehoben.
- **Alte Aufnahme-Endpunkte.** Der Einzelaufnahme-Recorder läuft parallel mit und verwirrt.
