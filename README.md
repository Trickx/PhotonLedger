# Photon Ledger

Ein Logbuch für gesammeltes Licht: Photon Ledger liest ein Astrofoto-Archiv
(nur lesend), erkennt Ziele, Sessions und Ausrüstung anhand der Ordnerstruktur
und der FITS-Header und zeigt pro Objekt Belichtungszeit, Sessions, Filter,
Datensätze und die bearbeiteten Versionen.

## Ordnerstruktur

Ausführlich mit Beispielen für eine und mehrere Sessions je Objekt: **[docs/ordnerstruktur.md](docs/ordnerstruktur.md)**.

```
<Ziel>/[<Session>/]<Lights|Flats|Darks|Biases>/[REJECTED/]<Datei>
```

| Beispiel | Ergebnis |
|---|---|
| `M97, M108 (Surfboard, Owl)/2026-04-28/Lights/…` | Ziel *M 97* (Katalog M 97, M 108), Session *2026-04-28* |
| `M51 (Whirlpool Galaxy)/Lights/…` | Session = Zielordner (Datum aus `DATE-OBS`) |
| `…/2026-07-08 DWARF3/Lights/…`, `…/NGC7000 (NA Nebula)[Canon]/Lights/…` | je eine Session pro Ordner |
| `…/2026-05-25-100/*.fit` (ohne Lights-Ordner) | Session, Bildtyp aus `IMAGETYP` |
| `Lights/REJECTED`, `Flats/Rejected` | aussortiert, zählt nicht zur Belichtung |
| `Finals/`, `Processing/`, `PI*/`, `master*/`, `DXO/` | JPG/PNG/TIFF = **Versionen**, FITS/XISF = Zwischenstände (ignoriert) |
| `M42` und `M42 (Orion Nebula)` | ein Ziel (gemeinsame Katalognummer) |
| `2026/M42 (…)/…` (alte Struktur mit Jahresordner) | wird erkannt, aber als *Zielordner liegt in einem Jahresordner* gemeldet |
| `M66, M65, NGC (Leo-Triplett)` und `M65, M66, NGC 3628 (Leo-Triplett)` | ein Ziel |

**Zwischenergebnisse** (Stacks, Master, PixInsight-/Siril-Schritte als FITS/XISF)
gehören einheitlich in einen Ordner **`Processing`** (genau so geschrieben, im
Ziel- oder Session-Ordner). Sie werden erfasst, aber nicht gezählt; liegen sie
woanders (`PI/master`, `PixInsight`, direkt neben den Rohdaten …), meldet die
Seite *Prüfungen* das als Warnung.

Von **WBPP** (PixInsight WeightedBatchPreprocessing) bleiben nur
`masterLight_…` und `masterLight_…_autocrop` (auch `_drizzle_Nx`,
`_fastIntegration`). Gemeldet werden Kalibrier-Master (`masterDark/Flat/Bias`),
Zwischenschritte (`calibrated`, `cosmetized`, `debayered`, `registered`,
`local_normalization`, `.xnml`, `.xdrz`, `LN_Reference_…`) und alle anderen
Dateien im WBPP-Ordner `master/` (Bearbeitungsschritte gehören daneben nach
`Processing/`). Ordner mit mehr als 30 Bildern (Burst-Serien,
registrierte Einzelbilder) zählen nicht als Versionen.

Brennweiten einer Kamera, die weniger als 3 % voneinander abweichen
(`FOCAL_TOLERANCE`), gelten als dieselbe Optik – z. B. 751,9 … 756 mm → *755 mm*.

**Archiv umstrukturiert?** Werden Ordner verschoben oder umbenannt, erkennt der Indexer die
Dateien wieder (gleicher Name, gleiche Größe und Änderungszeit, alter Pfad verschwunden) und
übernimmt ihre Daten, statt sie neu einzulesen. Titelbild und abweichend gewählte Rigs je
Session wandern mit.

## Katalog und Bildfeld

Ziele mit Katalognummer im Ordnernamen werden offline mit **OpenNGC** angereichert
(NGC, IC, Messier und Nachträge): Objekttyp, Helligkeit, Größe, Position und
Querverweise (z. B. M 66 → NGC 3627, PGC 34695, UGC 6346). Der Ordnername hat
immer Vorrang – die Koordinaten der Aufnahmen dienen nur zur Kontrolle
(Warnung, wenn die Aufnahmen deutlich neben dem Objekt liegen oder über mehr
als 10° streuen).

Aus `XPIXSZ`, Binning, Brennweite und Bildgröße ergeben sich Bildmaßstab und
Bildfeld pro Datensatz, daraus die **Objekte im Bildfeld** (ohne Plate-Solve;
Objekte in den Ecken sind markiert, weil die Kameradrehung unbekannt ist).
Standorte kommen aus `SITELAT`/`SITELONG`.

Katalogdaten: OpenNGC von Mattia Verga, <https://github.com/mattiaverga/OpenNGC>,
Lizenz CC-BY-SA-4.0 (unverändert in `webapp/data`, aktualisieren mit
`scripts/fetch_catalog.py`).

## Guiding (PHD2)

PHD2-Logs (`PHD2_GuideLog_*.txt`, auch vom ASIAIR) in Ordnern `log/`, `Logs/`
oder `logs/` werden eingelesen. Zugeordnet wird über die **Zeit**: Ein
Guiding-Abschnitt gehört zu jeder Session, deren Lights er zeitlich
überschneidet (± 30 min) – egal, in welchem Ordner das Log liegt; Kopien
desselben Logs werden zusammengefasst. Die Zeiten im Log sind Ortszeit des
Guiding-Rechners (`TIMEZONE`, Standard `TZ` bzw. Europe/Berlin).

Auf der Session-Seite zeigt der Bereich *Guiding (PHD2)* RMS gesamt/RA/Dec
(ohne Settling nach Dithern), geführte Zeit und Dither; aufgeklappt den Verlauf
von RA und Dec über die Nacht mit Dither-Markierungen und den Belichtungen der
Lights darunter (aussortierte rot) sowie eine Tabelle der Abschnitte mit Zoom.

## Nachtprotokoll (ASIAIR / N.I.N.A.) mit Airgus

ASIAIR-Autorun-Logs (`Autorun_Log_*.txt`), N.I.N.A.-Logs und PHD2-Logs in
`log/`-Ordnern werden mit [Airgus](https://github.com/Trickx/Airgus) ausgewertet:
Airgus übersetzt sie in einheitliche Ereignisse und wendet seine Regeln an
(Leitstern verloren oder davongelaufen, Meridian-Flip gescheitert, Auto-Center
ohne Erfolg, Nachführung aus, Aufnahme hängt, Autofokus, Geräteabbrüche …).
Zugeordnet wird wie bei PHD2 über die Zeit. Pro Session gespeichert werden die
Meldungen (ab Warnung), Kennzahlen und eine Zeitleiste; die Session-Seite zeigt
sie im Bereich *Nachtprotokoll*, die Session-Übersicht als Punkt *Log*
(grün: Log ohne schwere Meldung, orange: kein Log, rot: Meldungen ab „hoch“).
Airgus wird ohne seine Web-/MQTT-Abhängigkeiten installiert (`requirements-airgus.txt`,
auf einen Commit festgelegt) und ist optional: Mit `WITH_AIRGUS=0` in `.env` baut
`docker compose build` das Image lokal ohne Airgus (das veröffentlichte Image enthält es immer). Dann fehlt nur das Nachtprotokoll, die
PHD2-Graphen und alles andere bleiben. Die Indexer-Seite zeigt, ob Airgus aktiv ist, mit
Version, Commit und Zahl der Logs. Sie warnt, wenn ein benötigtes Airgus-Modul nicht mehr
importierbar ist oder der installierte Stand vom festgelegten Commit abweicht.

## WBPP-Läufe (PixInsight)

Die Logs von WeightedBatchPreprocessing (`<JJJJMMTThhmmss>.log` in `Processing/logs/`
oder `PI/logs/`) werden gelesen. Die Session-Seite zeigt im (eingeklappten) Bereich
*WBPP-Läufe* die Läufe mit Lights dieser Session, die Session-Übersicht einen Punkt *WBPP*
(grün: gestackt ohne Fehler, orange: nicht gestackt, rot: Lauf fehlgeschlagen oder mit
Fehlern). Je Lauf: Datum, PixInsight-/WBPP-
Version, Laufzeit, Ergebnis (masterLight erzeugt?), die Schritte mit Bilderzahl, die
verwendeten Kalibrier-Master, Fehler und Warnungen sowie die Lights mit den meisten
verworfenen Pixeln (mit Rauschen und Zahl der PSF-Fits). Über die Dateinamen wird der
Stack mit dem Archiv abgeglichen: fehlende Lights, inzwischen aussortierte Lights und
Nächte, die neuer als der Lauf sind (Master veraltet). Die Seite *Prüfungen* meldet
gescheiterte Läufe, veraltete Master, fehlende Lights, WBPP-Fehler und Lights mit
negativen Pixelwerten nach der Kalibrierung.

## Rigs

Unter *Ausrüstung › Rigs verwalten* werden Komponenten angelegt – Kameras
(auch direkt aus den FITS-Headern mit Pixel- und Bildgröße), Optiken
(Teleskop, Festbrennweite, Zoomobjektiv mit Brennweitenbereich) und
Flattener/Reducer/Extender (Faktor) – und zu Rigs kombiniert. Daraus ergeben
sich effektive Brennweite, Öffnungsverhältnis, Bildmaßstab, Bildfeld und die
Abtastung bei gewähltem Seeing.

Zuordnung der Aufnahmen: automatisch, wenn die Kamera (`INSTRUME`) und die
Brennweite (bei Zoomobjektiven der ganze Bereich, ± `FOCAL_TOLERANCE`) zu genau
einem Rig passen; sonst manuell je Datensatz (Kamera × Brennweite) oder
abweichend für einzelne Sessions. *Ausrüstung* zeigt pro Rig Stunden, Ziele,
Sessions, Lights, Zeitraum, Stunden pro Jahr, Filter und die mittlere FWHM aus
StarAberrationDiagnostics. Rigs und Zuordnungen liegen in der Datenbank und
bleiben bei jedem Indexlauf erhalten.

## StarAberrationDiagnostics

Liegt neben den Lights ein Ordner `SAD/` mit `StarAberrationSeries.csv`
(und optional `.json`, Karten-PNGs, `StarAberrationAI.json`), liest der Indexer
die Messwerte pro Light ein – nur lesend, nur wenn sich die Dateien geändert haben.

- **Session-Seite** (Klick auf das Datum einer Session): FWHM Mitte/Rand,
  Verkippung, Elliptizität, Sterne; Verläufe über die Nacht (getrennt nach
  Pier-Seite), die Karten pro Light (Mosaik, Sterngröße, Sternform, Koma,
  Verkippung, 3D) und die KI-Bewertung. Die großen PNGs werden beim ersten
  Ansehen verkleinert in `data/sad/` abgelegt.
- **Prüfungen › Optik (SAD)**: Warnungen bei Verkippung, Randunschärfe und
  Sternstreckung über den Grenzwerten, Ausreißer-Lights als Kandidaten für
  `REJECTED`, Sessions ohne Auswertung.
- FWHM in Bogensekunden berücksichtigt SuperPixel-Debayering (1 SAD-Pixel = 2 Sensorpixel).

Grenzwerte (Mediane einer Session): `SAD_TILT_WARN` 0.25, `SAD_RATIO_WARN` 1.4,
`SAD_ELLIPTICITY_WARN` 0.15, `SAD_OUTLIER_FACTOR` 1.3.

## Betrieb mit Docker (z. B. auf dem NAS)

Das Image baut eine GitHub Action ([.github/workflows/docker.yml](.github/workflows/docker.yml))
bei jedem Push: erst laufen die Tests, dann wird das Image für `linux/amd64` und `linux/arm64`
gebaut. Veröffentlicht wird es in der GitHub Container Registry:

| Push | Image |
|---|---|
| auf `main` | `ghcr.io/trickx/photonledger:latest` und `:sha-<commit>` |
| Tag `v1.2.3` | `ghcr.io/trickx/photonledger:1.2.3` und `:1.2` |
| andere Branches | nur Tests und Build, nichts wird veröffentlicht |

Auf dem NAS reichen [`docker-compose.prod.yml`](docker-compose.prod.yml) und `.env`
(kein Quellcode nötig). Diese Compose-Datei holt nur das fertige Image, sie baut nie:

```bash
cp .env.example .env        # ARCHIVE_PATH, PUID/PGID, SECRET_KEY, PHOTONLEDGER_TAG anpassen
mkdir -p data
docker compose -f docker-compose.prod.yml pull     # Image von ghcr.io holen
docker compose -f docker-compose.prod.yml up -d
```

Update: `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`
(oder die Datei als `docker-compose.yml` ablegen, dann entfällt `-f …`).
`docker-compose.yml` im Repo ist für die Entwicklung: Sie kann zusätzlich lokal bauen.

`./data` muss vor dem ersten Start existieren und dem Benutzer `PUID:PGID` gehören
(Werte aus `id -u` / `id -g` des Benutzers auf dem NAS). Fehlt der Ordner, legt Docker ihn als
root an, und der Container bricht mit „nicht beschreibbar“ ab. Abhilfe:
`sudo chown -R <PUID>:<PGID> ./data`. Ist das Paket auf GitHub privat,
einmal anmelden: `docker login ghcr.io -u <github-user>` mit einem Personal Access Token
(Recht `read:packages`). Lokal bauen statt laden: `docker compose up -d --build`.
Version und Commit des laufenden Images zeigt die Seite *Indexer*.

Danach: `http://<host>:5080`

- Das Archiv wird **nur lesend** (`:ro`) eingebunden.
- Datenbank und Vorschaubilder liegen per Bind-Mount in `./data`
  (`data/photonledger.db`, `data/thumbs`, `data/previews`) – kein Docker-Volume.
- Die Seite *Indexer* zeigt Intervall, letzten und nächsten Lauf und ob der
  Indexer-Dienst aktiv ist; **Jetzt scannen** fordert beim laufenden Dienst
  sofort einen Lauf an (ohne Dienst, etwa lokal, startet die Web-App einen
  einmaligen Lauf). Eine Sperrdatei verhindert zwei Läufe gleichzeitig.
- Gelöschte Dateien verschwinden beim nächsten Lauf aus der Datenbank. Ordner
  oder Dateien, die während des Scans nicht lesbar sind (SMB-Aussetzer,
  Rechte), werden dabei ausgenommen – ist das ganze Archiv leer oder nicht
  erreichbar, wird gar nichts gelöscht. Vorschaubilder ohne Quelle werden am
  Ende jedes Laufs aus `data/` entfernt.
- Zwei Dienste aus demselben Image: `web` (Port 5080) und `indexer`
  (scannt alle `FLASK_SCAN_INTERVAL` Sekunden, nur geänderte Dateien werden neu gelesen).
- `PUID`/`PGID` müssen `./data` beschreiben und das Archiv lesen dürfen.

## Entwicklung

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/pip install --no-deps -r requirements-airgus.txt   # Airgus-Kern (Log-Parser, Regeln)
cp config.json-dist config.json            # ARCHIVE_PATH setzen
./venv/bin/flask --app app.py db upgrade
./venv/bin/python -m webapp.indexer --once # Archiv einlesen
./venv/bin/python app.py                   # http://127.0.0.1:5080
./venv/bin/python -m pytest
```

## Einstellungen

`config.json` oder Umgebungsvariablen `FLASK_<NAME>` (Werte als JSON):

| Name | Standard | Bedeutung |
|---|---|---|
| `ARCHIVE_PATH` | `./archive` (Docker: `/archive`) | Wurzel des Bildarchivs |
| `DATA_PATH` | `./data` (Docker: `/data`) | Datenbank und Vorschaubilder |
| `SCAN_INTERVAL` | `900` | Sekunden zwischen zwei Scans |
| `FOCAL_TOLERANCE` | `0.03` | relative Toleranz für gleiche Brennweite |
| `NIGHT_OFFSET_HOURS` | `12` | `DATE-OBS` minus 12 h = Datum der Beobachtungsnacht |
| `MAX_VERSIONS_PER_DIR` | `30` | Ordner mit mehr Bildern sind keine Versionen |
| `IGNORE_DIRS` | `[]` | zusätzlich zu ignorierende Ordnernamen (Glob) |

## Lizenz

Photon Ledger – Copyright (C) 2026 Sven Kopetzki

Dieses Programm ist freie Software: Sie können es unter den Bedingungen der
GNU General Public License, Version 3 oder (nach Ihrer Wahl) jeder späteren
Version, wie von der Free Software Foundation veröffentlicht, weitergeben
und/oder verändern (SPDX: `GPL-3.0-or-later`). Es wird ohne jede Gewährleistung
verbreitet. Den vollständigen Lizenztext enthält [LICENSE](LICENSE).

Mitgelieferte Fremdbestandteile behalten ihre eigene Lizenz:

| Bestandteil | Ort | Lizenz |
|---|---|---|
| OpenNGC v20260501 (Mattia Verga) | `webapp/data/` | CC-BY-SA-4.0 |
| Bootstrap 5.3.8 | `webapp/static/bootstrap*` | MIT |
| Bootstrap Icons | `webapp/static/bootstrap-icons/` | MIT |
| htmx 2.0.3 | `webapp/static/htmx-2.0.3.min.js` | 0BSD |
| Farbmodus-Umschalter aus der Bootstrap-Doku | `webapp/static/darkmode.js` | CC BY 3.0 |
| Airgus (Log-Parser und Regeln, im Image installiert) | – | GPL-3.0-or-later |
