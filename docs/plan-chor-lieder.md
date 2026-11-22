# Plan: Liednummern vom Chorbildschirm als Titel der Tracks

Stand: 22. November 2026 · Idee, noch nicht durchdacht, nichts gebaut

## Worum es geht

Der Dirigent tippt auf dem Chorbildschirm (`/anzeige`, Abschnitt 5j in
[betrieb.md](betrieb.md)) die Nummer des nächsten Liedes, z. B. „SDH 245“. Akouo bekommt
jeden Bildschirmstand ohnehin mit (Abgleich mehrerer Bildschirme, Zweig `261122-chor-sync`).
Die Idee: Aus der Nummer wird über eine Notendatenbank der Titel, und der landet am Track
des Liedes — ohne dass der Tontechniker etwas eintippt.

**Grundannahme:** Der Normalfall ist der Chor. Welche Sammlung gilt, entscheidet Akouo über
die Beitragsart des Tracks (erkannt oder von Hand gewählt). Ist der Beitrag ein Orchester,
wird im Orchesterbestand nachgeschlagen. Der Modus auf dem Chorbildschirm ist höchstens ein
Hinweis.

## Die Notendatenbank

Liegt als Excel-Datei vor (`Noten-Datenbank.xlsx`, nicht im Repository — sie enthält Namen).
Gesichtet am 22. November 2026:

| Blatt | Inhalt |
|---|---|
| Alle Stücke | 1.336 Stücke: ensemble, sammlung, nr, titel, besetzung, text_von, melodie_von, satz_von, dirigent, weihnachten, bemerkung, unsicher, anz_auftritte, zuletzt |
| Sammlungen | H (121), MCB (201), MCH (27), SDH (180), „ohne Zusatz“ (727) — alle Chor; VO (80) — Vororchester |
| Orchester-Vorspiele | 148 Einträge mit Datum, Reihenfolge, Nummer, Titel |
| Zu prüfen | 117 Einträge mit unsicheren Angaben |

Bekannte Lücken:

- SDH 101–223 nur Titel A–M erfasst, N–Z fehlt.
- H 104 und „ohne Zusatz“ 664 doppelt vergeben.
- Nummern mit Zusatz: 17a, 32a, 39A.
- Die F-Tasten des Chorbildschirms belegen SDH, H, MC, FC, MCB, MCH. **MC und FC gibt es in
  der Datenbank nicht.**

## Grobe Schritte

1. **Notendatenbank übernehmen.** Tabellen `noten` und `sammlungen`, Importskript für die
   Excel-Datei. Erneutes Importieren aktualisiert (Schlüssel: Sammlung + Nummer), statt zu
   verdoppeln. Datei und Dirigentennamen nie ins Repository.
2. **Getippten Text lesen.** „SDH 245“ → Sammlung SDH, Nr. 245; „245“ allein → „ohne Zusatz“;
   Zusätze wie „17a“ mitlesen. Mehrere Zeilen auf dem Bildschirm → mehrere Lieder. Derselbe
   Leser passt auf den Kurztext im Programm des Dirigenten („(SDH 245 | Name)“).
3. **Mitschreiben, was wann zu sehen war.** Während einer Aufnahme jede erkannte Nummer mit
   Aufnahmezeit an der Session speichern („Liedhinweis“).
4. **Dem Beitrag zuordnen** — wie beim Programm des Dirigenten (`_vom_dirigenten` in
   `audio_service/automode.py`): Es zählt, was zu Beginn eines Musikbeitrags auf dem
   Bildschirm steht, mit etwas Nachlauf. Erst dann wird nachgeschlagen, mit der Beitragsart
   des Tracks als Wahl der Sammlung. Titel etwa „SDH 245 · Titel“, mit Herkunft
   „Chorbildschirm“. Von Hand gesetzte Titel gewinnen (`titel_von_hand`). Unbekannte Nummer:
   die Nummer bleibt als Titel, der Track wird zum Prüfen markiert.
5. **In Akouo zeigen.** Kleiner Spiegel des Chorbildschirms im Dashboard; am Track der
   erkannte Titel, mit einem Klick zu korrigieren.
6. **Später: Statistik.** Auftritte und „zuletzt gesungen“ zählt Akouo aus den beschrifteten
   Tracks, statt sie in Excel zu pflegen. Denkbar: Hinweis für den Dirigenten beim Tippen.

## Offene Fragen

- Was sind **MC** und **FC** (Männerchor, Frauenchor?), und gibt es dafür ein Verzeichnis?
- Was ist **„Grün“** aus den Programmen („Grün 14“) — ein Liederbuch, das in die Datenbank
  gehört?
- **Wer pflegt die Datenbank?** Bleibt die Excel-Datei die Quelle und wird ab und zu neu
  importiert, oder wird danach in Akouo gepflegt?
- **Zeitpunkt:** Wird die Nummer vor dem Lied getippt, während der Predigt, oder erst, wenn
  der Chor steht? Davon hängt ab, welcher Stand für den Beitrag zählt (siehe Dirigenten-
  Zeiger: Stand bei Beitragsbeginn plus 20 s Nachlauf).
- **Mehrere Nummern** auf dem Bildschirm: Liedfolge, oder Lied plus Liederbuch der Gemeinde?
- **Gemeindegesang** steht auch auf dem Bildschirm, ist aber nicht in der Notendatenbank —
  braucht es dafür ein eigenes Verzeichnis (Gesangbuch)?
- Was passiert mit Nummern, die getippt, aber nicht gesungen wurden?
