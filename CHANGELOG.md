# Changelog

Versionsnummern nach `MAJOR.MINOR.PATCH`; die aktuelle steht in [`VERSION`](VERSION).

## 1.0.0 – 2026-11-26

Erste nummerierte Version.

- Ziele, Sessions und Datensätze aus Ordnerstruktur und FITS-/XISF-/RAW-Headern
  (`<Archiv>/<Ziel>/<Session>/…`), Katalog mit OpenNGC-Daten und Bildfeld.
- Mehrere Archive (`ARCHIVES`), Ziele werden archivübergreifend zusammengeführt.
- Auswertung von PHD2-, ASIAIR-/N.I.N.A.-Logs (Airgus), WBPP-Läufen und StarAberrationDiagnostics.
- Rigs mit Nutzungsstatistik; Session-Übersicht mit Log-, PHD2-, WBPP- und SAD-Status.
- Prüfungen der Ablage und Header, darunter:
  - doppelte Lights und Kalibrierdaten (je einmal gemeldet, Teilkopien zusammengefasst),
  - Dateien, deren Inhalt nicht zur Endung passt (z. B. FITS mit Endung `.raf`),
  - Lights, deren Header (`OBJECT`, `RA`/`DEC`) nicht zum Ziel des Ordners passt,
  - Meldungen lassen sich quittieren.
- `Finals` darf jedes Format enthalten; schlechte SAD-Werte sind Hinweise.
- Tägliche Sicherung der Datenbank (`BACKUP_KEEP`).
- Live-Aktualisierung der Seiten nach einem Indexlauf, Fehleranzeige bei fehlgeschlagenem Lauf.
- Docker-Image für amd64/arm64 auf ghcr.io; Versionen über Git-Tags `vX.Y.Z`.
