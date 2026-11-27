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

**Brennweite aus dem Plate-Solve.** ASIAIR rechnet `FOCALLEN` aus dem gelösten Bildmaßstab und der
Pixelgröße, die sie für die Kamera kennt – ist diese falsch (z. B. 4,29 statt 4,1 µm bei der Canon 7D
Mark II), stimmt die Brennweite nicht (792 statt 756 mm). Weicht `XPIXSZ` um mehr als 2 % von der Pixelgröße
der Kamera unter *Rigs* ab, rechnet Photon Ledger die Brennweite mit dieser neu (aus der WCS-Lösung im Header);
Bilder ohne eigene Lösung mit demselben Header-Wert bekommen dieselbe Korrektur. Der Katalog markiert solche
Brennweiten mit einem Info-Symbol, der Header bleibt unverändert.

Brennweiten einer Kamera, die weniger als 3 % voneinander abweichen
(`FOCAL_TOLERANCE`), gelten als dieselbe Optik – z. B. 751,9 … 756 mm → *755 mm*.

**Archiv umstrukturiert?** Werden Ordner verschoben oder umbenannt, erkennt der Indexer die
Dateien wieder (gleicher Name, gleiche Größe und Änderungszeit, alter Pfad verschwunden) und
übernimmt ihre Daten, statt sie neu einzulesen. Titelbild und abweichend gewählte Rigs je
Session wandern mit.

**Notizen.** Eine Datei `Notizen.md` (oder `notes.md`) im Session- oder Zielordner wird beim
Indexlauf gelesen und auf der Session-Seite bzw. im Katalog angezeigt – als Markdown: Überschriften,
Listen, Aufgaben (`- [x]`), **fett**, *kursiv*, Links, Zitate und Code. HTML in der Datei wird als Text
gezeigt. Sessions mit Notizen tragen im Katalog ein kleines Notiz-Symbol.

**Prüfungen quittieren.** Gewollte Meldungen (z. B. Darks im Session-Ordner) lassen sich auf der
Seite *Prüfungen* mit **quittieren** ausblenden; sie zählen dann auch nicht mehr im Zähler der
Navigation. Ändert sich ihr Text – etwa weil neue Dateien dazukommen –, erscheinen sie wieder.
Die quittierten stehen unter **N quittiert** und lassen sich dort zurückholen.

**Plausibilität.** Nennt der Header eines Lights ein anderes Objekt (`OBJECT`, z. B. „M 31“ im
Ordner der Perseiden) oder zeigt er per `RA`/`DEC` deutlich neben das Ziel des Ordners, meldet
*Prüfungen › Ziele* das Light als „passt laut Header nicht“ – typisch für falsch einsortierte oder
beim Kopieren überschriebene Dateien. Bei Ordnern ohne Katalognummer ist es nur ein Hinweis.

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
Flattener/Reducer/Extender (Faktor), Montierungen (Art, Traglast) – und zu Rigs kombiniert;
optional mit Guiding-Kamera (aus der Kameraliste). Ein Flattener/Reducer/Extender kann als **optional** markiert werden
(z. B. eine Barlow, die nur in manchen Nächten im Einsatz ist): Das Rig deckt dann beide Brennweiten ab
und zeigt alle Werte als „ohne / mit“; zwei Rigs, die sich nur darin unterscheiden, fasst die Seite *Rigs*
auf Klick zusammen. **Smart-Teleskope** (DWARF 3, DWARF II, Seestar S50/S30,
Vespera, Vespera II, Stellina) lassen sich mit einem Klick als Rig anlegen – Kamera, Optik und
integrierte Montierung entstehen zusammen; findet Photon Ledger ein solches Gerät in den FITS-Headern,
schlägt die Seite *Rigs* das von selbst vor. Daraus ergeben
sich effektive Brennweite, Öffnungsverhältnis, Bildmaßstab, Bildfeld und die
Abtastung bei gewähltem Seeing.

Zuordnung der Aufnahmen: automatisch, wenn die Kamera (`INSTRUME`) und die
Brennweite (bei Zoomobjektiven der ganze Bereich, ± `FOCAL_TOLERANCE`) zu genau
einem Rig passen; sonst manuell je Datensatz (Kamera × Brennweite) oder
abweichend für einzelne Sessions. *Ausrüstung* zeigt pro Rig Stunden, Ziele,
Sessions, Lights, Zeitraum, Stunden pro Jahr, Filter und die mittlere FWHM aus
StarAberrationDiagnostics. Rigs und Zuordnungen liegen in der Datenbank und
bleiben bei jedem Indexlauf erhalten.

Jedes Rig kann ein **Foto** bekommen (*Foto hochladen* auf der Rig-Karte; JPG, PNG oder WebP bis
30 MB). Es wird gedreht wie aufgenommen, auf 1600 px verkleinert und als JPEG in `data/rigs/`
gespeichert – nicht in der Datenbank, also bei einer Sicherung den ganzen `data`-Ordner mitnehmen.

Kann einer Session kein Rig zugeordnet werden (keins oder mehrere passen), meldet *Prüfungen ›
Rigs* das je Session; die Meldung verschwindet, sobald ein Rig angelegt oder zugeordnet ist.
Solange gar kein Rig angelegt ist, gibt es diese Meldung nicht.

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

### Versionen

Die Versionsnummer steht in der Datei [`VERSION`](VERSION) (Schema `MAJOR.MINOR.PATCH`), Änderungen
in [`CHANGELOG.md`](CHANGELOG.md). Die Seite *Indexer* zeigt die laufende Version: `1.2.3` für ein
Release, `1.2.3-dev.<commit>` für einen Stand von `main`, `1.2.3-local` ohne Docker.

Release: `VERSION` und `CHANGELOG.md` anpassen, committen, dann

```bash
git tag v1.2.3 && git push origin v1.2.3
```

Der Build bricht ab, wenn Tag und `VERSION` nicht übereinstimmen. Auf dem NAS hält
`PHOTONLEDGER_TAG=1.2.3` (oder `1.2` für Bugfix-Updates) eine Version fest; zurück geht es,
indem man dort die alte Nummer einträgt und `pull` / `up -d` ausführt.

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
- **Sicherung:** Der erste Indexlauf eines Tages kopiert die Datenbank vorher nach
  `data/backups/photonledger-JJJJ-MM-TT.db` (SQLite-Online-Backup, auch bei laufender
  Web-App konsistent); die neuesten `BACKUP_KEEP` Kopien (Standard 14) bleiben. Darin stecken
  die Einstellungen, die sich nicht aus dem Archiv neu einlesen lassen: Rigs, Titelbilder,
  quittierte Prüfungen. Zurückspielen: Container stoppen, Kopie als `data/photonledger.db`
  ablegen (vorher `photonledger.db-wal` und `-shm` löschen), Container starten.

### Login

Ohne Einstellung ist Photon Ledger offen – im Heimnetz oft genug. Mit Login (ein Benutzer):

```bash
openssl rand -hex 32                                           # neuer SECRET_KEY
docker compose run --rm -it web python -m webapp.auth          # fragt das Passwort, gibt eine Zeile aus
```

Der zweite Befehl ändert `.env` nicht: Die ausgegebene Zeile `FLASK_AUTH_PASSWORD_HASH=b64:…`
in `.env` kopieren (ohne `$`, daher ohne Anführungszeichen; ältere Hashes `'scrypt:…$…$…'`
gehen weiter).

```bash
# .env
FLASK_SECRET_KEY=<der neue Schlüssel>
FLASK_AUTH_USER=sven
FLASK_AUTH_PASSWORD_HASH=b64:…                                 # Zeile aus python -m webapp.auth
#FLASK_AUTH_PUBLIC_READ=true                                   # ansehen ohne Login, ändern mit
```

Danach `docker compose up -d`. Alle Seiten verlangen dann die Anmeldung (Sitzung bis zu 30 Tage,
„Abmelden“ unten in der Navigation); offen bleibt nur `/healthz` für den Docker-Healthcheck.
Mit `FLASK_AUTH_PUBLIC_READ=true` geht beides parallel: `http://<host>:5080/` zeigt alles ohne
Passwort (nur lesen), **Anmelden** unten in der Navigation erlaubt Änderungen.
Ohne gesetzten `SECRET_KEY` oder mit fehlendem bzw. beschädigtem Hash startet die App mit Login
nicht und nennt den Grund im Log (`docker compose logs web`) – mit dem Standardschlüssel ließen
sich Sitzungen fälschen. Für den Zugriff von außen gehört zusätzlich HTTPS davor (Reverse-Proxy).

### Mehrere Archive

Liegt die Sammlung auf mehreren Freigaben oder Platten, bekommt jedes Archiv einen
eigenen, schreibgeschützten Mount und einen Namen:

```yaml
    volumes:
      - ./data:/data
      - /share/Multimedia/Astro:/archives/Astro:ro
      - /share/Backup/Dwarf:/archives/Dwarf:ro
```

```bash
# .env
FLASK_ARCHIVES={"Astro": "/archives/Astro", "Dwarf": "/archives/Dwarf"}
```

- Jedes Archiv hat die übliche Struktur `<Archiv>/<Ziel>/<Session>/…`.
- Gespeicherte Pfade beginnen mit dem Namen (`Astro/M42 (Orion Nebula)/2025-02-19/…`).
- Ziele mit derselben Katalognummer werden archivübergreifend zusammengeführt; Rigs,
  Statistik und Prüfungen gelten für alles.
- Fehlt ein Mount oder ist er leer, meldet die Seite *Indexer* das Archiv als
  „nicht gefunden“ bzw. „leer“; seine Einträge bleiben stehen, die übrigen Archive
  werden normal gescannt.
- Umstellen von einem Archiv (`ARCHIVE_PATH`) auf benannte Archive: Der nächste Lauf
  erkennt die Dateien unter den neuen Pfaden wieder und übernimmt sie, ohne sie neu
  zu lesen – Sessions, Titelbilder und gewählte Rigs bleiben erhalten. Die
  `ARCHIVE_PATH`-Zeile im `volumes`-Abschnitt kann dann entfallen.

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
| `ARCHIVES` | – | mehrere Archive: `{"<Name>": "<Pfad>", …}`; ersetzt `ARCHIVE_PATH` (siehe *Mehrere Archive*) |
| `DATA_PATH` | `./data` (Docker: `/data`) | Datenbank und Vorschaubilder |
| `SCAN_INTERVAL` | `900` | Sekunden zwischen zwei Scans |
| `FOCAL_TOLERANCE` | `0.03` | relative Toleranz für gleiche Brennweite |
| `NIGHT_OFFSET_HOURS` | `12` | `DATE-OBS` minus 12 h = Datum der Beobachtungsnacht |
| `MAX_VERSIONS_PER_DIR` | `30` | Ordner mit mehr Bildern sind keine Versionen |
| `IGNORE_DIRS` | `[]` | zusätzlich zu ignorierende Ordnernamen (Glob) |
| `BACKUP_KEEP` | `14` | Tageskopien der Datenbank in `data/backups` (`0` = keine) |
| `AUTH_USER` | – | Benutzer für den Login; ohne ihn ist alles offen (siehe *Login*) |
| `AUTH_PASSWORD_HASH` | – | Passwort-Hash (`python -m webapp.auth`) |
| `AUTH_PUBLIC_READ` | `false` | `true` = ansehen ohne Login, nur Änderungen brauchen ihn |

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
