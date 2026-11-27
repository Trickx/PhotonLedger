# Changelog

Versionsnummern nach `MAJOR.MINOR.PATCH`; die aktuelle steht in [`VERSION`](VERSION).

## Unveröffentlicht

- Rigs: Foto (oder einheitliches Platzhalterbild), Montierung (Vorgabe: Treiber aus dem Header),
  Guiding-Kameras mit eigener Liste und Vorschlag aus `GUIDECAM`/PHD2, Smart-Teleskope mit einem Klick,
  Warnung für Sessions ohne Rig, Rig im Katalog und auf der Session-Seite.
- Session-Seite: Filter mit Lights und Flats, Karte „WBPP und Kalibrierung“ – welche Master WBPP verwendet
  hat (auch Kalibrierdaten außerhalb des Archivs); F/D/B und „Lights ohne Flats“ berücksichtigen das.
- Downloads: Session als ZIP (Auswahl, gestreamt), Finals als ZIP, einzelne Versionen; „Im Finder öffnen“
  über `ARCHIVE_URL`; mit Login nur nach Anmeldung (`DOWNLOAD_PUBLIC`).
- Versionen im Vollbild mit Zoom und Blättern; `Notizen.md` in Session- und Zielordnern.
- Einheitliche Karten auf der Session-Seite; Kachel „Kalibriert“ im Katalog entfernt.
- Rigs: Flattener/Reducer/Extender optional (ein Rig für beide Brennweiten, Paare zusammenfassen),
  Gruppierung nach Optik mit Summen, Duplizieren.
- Brennweite aus Plate-Solve mit der Pixelgröße der Kamera korrigiert (ASIAIR mit falscher Pixelgröße,
  z. B. Canon 7D Mark II: 792 → 756 mm) – für Lights und die Kalibrierbilder derselben Nacht.
- Das Fehlerband eines fehlgeschlagenen Indexlaufs verschwindet, solange ein neuerer Lauf aktiv ist.
- Videos (MP4, MOV, M4V, WebM) als Versionen: im Katalog und in der Vollbild-Ansicht abspielbar.

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
