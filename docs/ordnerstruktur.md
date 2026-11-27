# Ordnerstruktur des Bildarchivs

PhotonLedger liest das Archiv nur und leitet Ziele, Sessions, Datensätze und
Versionen allein aus **Ordnerstruktur** und **FITS-Headern** ab. Diese Seite
beschreibt die gewünschte Struktur. Abweichungen werden nicht verändert,
sondern auf der Seite *Prüfungen* gemeldet (Regelname in Klammern).

## Überblick

```
<Archiv>/
└── <Ziel>/                                M97, M108 (Owl, Surfboard)
    ├── <Session>/                         2026-04-28            ← eine Nacht, ein Rig
    │   ├── Lights/
    │   │   ├── *.fit | *.fits | *.xisf | *.raf …
    │   │   ├── REJECTED/                  aussortierte Lights (zählen nicht)
    │   │   └── SAD/                       StarAberrationDiagnostics (optional)
    │   ├── Flats/
    │   ├── Darks/
    │   ├── Biases/
    │   ├── DarkFlats/                     optional
    │   ├── Logs/                          ASIAIR-Autorun-, PHD2-, NINA-Logs (optional)
    │   ├── Notizen.md                     eigene Notizen zur Nacht (optional, Markdown)
    │   ├── Processing/                    Stacks/Zwischenschritte/Finals nur dieser Nacht (optional)
    │   └── Finals/                        Ergebnisse nur dieser Nacht, jedes Format (optional)
    ├── <Session 2>/                       2026-04-29
    │   └── …
    ├── Processing/                        Bearbeitung über alle Sessions des Ziels
    │   ├── master/                        WBPP-Ausgabe, Zwischenschritte und *final*-Dateien
    │   └── *.xisf | *.fit | *.tif …       PixInsight-/Siril-Schritte, Zwischenstände
    ├── Finals/                            fertige Ergebnisse, jedes Format (JPG, TIFF, XISF …)
    └── Notizen.md                         eigene Notizen zum Ziel (optional, Markdown)
```

## Ebenen

### 1. Ziel – `<Katalognummer(n)> (<Name>)`

```
M 31 (Andromeda Galaxy)
M97, M108 (Owl, Surfboard)
NGC 7380 (Wizard Nebula) SH2-142
IC1396 (Elephant's Trunk Nebula)
Mineral Moon                           ← ohne Katalognummer: Name allein
```

- **Katalognummern zuerst**, mehrere mit Komma. Erkannt werden
  M, NGC, IC, Sh2, LBN, LDN, vdB, Barnard/B, Arp, Abell, Mel, Cr, Ced, UGC, PGC –
  mit oder ohne Leerzeichen (`M 31`, `M31`, `NGC7000`, `SH2-142`).
- Die **erste** Nummer ist das Hauptobjekt; es liefert Typ, Helligkeit, Größe
  und Position aus OpenNGC. Weitere Nummern zählen als Objekte im Bild.
- Der **Name in Klammern** wird als Beiname angezeigt.
- Ordner, die eine Katalognummer teilen, sind **dasselbe Ziel**
  (`M66, M65 (Leo-Triplett)` und `M65, M66, NGC 3628 (Leo-Triplett)`).
- Alle Nächte eines Objekts – auch aus verschiedenen Jahren – liegen als
  Sessions in **einem** Zielordner direkt unter `<Archiv>/`; das Jahr steht im
  Session-Ordner (`2025-12-03`, `2026-01-14`).
- Zielordner in einem Jahresordner (`<Archiv>/2026/<Ziel>/`, alte Struktur)
  werden weiter erkannt, aber gemeldet (Hinweis *Zielordner liegt in einem
  Jahresordner*).
- Ohne Katalognummer gibt es keine Katalogdaten und keine Objekte im Bildfeld
  (Hinweis *keine Katalognummer*; nicht für Mond, Sonne, Meteore).

### 2. Session – `<JJJJ-MM-TT>[ <Zusatz>]`

**Eine Session = eine Nacht mit einem Rig.** Das Datum ist der **Abend**, an
dem die Nacht beginnt (Lights nach Mitternacht gehören zum Vortag).

```
2026-04-28                 eine Nacht, ein Rig
2026-07-08 DWARF3          gleiche Nacht, zweites Rig: Zusatz nach dem Datum
2026-07-08 Skywatcher
2026-05-25-100             Zusatz auch mit Bindestrich möglich
```

- Datum im Format `JJJJ-MM-TT` am **Anfang** des Ordnernamens.
- Weichen Ordnerdatum und Aufnahmedatum der Lights (aus `DATE-OBS`) um mehr als
  einen Tag ab, gibt es eine Warnung (*Ordnerdatum passt nicht*).
- Ein Session-Ordner ohne Datum wird erkannt, das Datum kommt dann aus den
  Lights (Hinweis *Session-Ordner ohne Datum*).
- **Nicht** mehrere Nächte in einen Session-Ordner legen (Hinweis *mehrere
  Nächte*) und **nicht** mehrere Kameras/Optiken mischen (Warnung *mischt
  mehrere Ausrüstungen*).

#### Nur eine Session

Auch bei nur einer Nacht gehört ein Datumsordner dazwischen:

```
M51 (Whirlpool Galaxy)/2026-04-14/Lights/…      ✔ empfohlen
M51 (Whirlpool Galaxy)/Lights/…                 ⚠ Lights direkt im Zielordner
```

Die zweite Form wird gelesen (der Zielordner gilt dann als Session), aber mit
Warnung *Lights direkt im Zielordner* – kommt später eine zweite Nacht dazu,
lassen sich die Nächte nicht mehr trennen.

#### Mehrere Sessions

```
M97, M108 (Owl, Surfboard)/
├── 2026-04-28/
│   ├── Lights/ Flats/ Darks/ Biases/ Logs/
├── 2026-04-29/
│   ├── Lights/ Flats/ Darks/ Biases/ Logs/
├── Processing/            gemeinsames Stacken beider Nächte
│   └── master/
└── Finals/
```

#### Mehrere Rigs in derselben Nacht

```
IC1396 (Elephant's Trunk Nebula)/
├── 2026-07-08 DWARF3/       Lights/ Darks/
├── 2026-07-08 Skywatcher/   Lights/ Flats/ Darks/ Biases/ logs/
├── 2026-07-10 DWARF3/
└── 2026-07-10 Skywatcher/
```

Logs (PHD2, ASIAIR) werden über die Zeit zugeordnet; laufen zwei Sessions
desselben Ziels gleichzeitig, gehört ein Log der Session, in deren Ordner es liegt.

### 3. Bildordner in der Session

| Ordner | Inhalt | erkannte Schreibweisen |
|---|---|---|
| `Lights` | Belichtungen des Objekts | Lights, Light |
| `Flats` | Flats | Flats, Flat |
| `Darks` | Darks | Darks, Dark |
| `Biases` | Bias/Offset | Biases, Bias, Offsets, Offset |
| `DarkFlats` | Dark-Flats | DarkFlats, DarkFlat, FlatDarks |
| `…/REJECTED` | aussortierte Bilder (unterhalb eines der Ordner oben) | REJECTED, Reject, Bad, Trash, Verworfen |

- Groß-/Kleinschreibung wird erkannt, empfohlen ist die Schreibweise der
  ersten Spalte (Hinweis *Ordnername weicht ab* bei `lights`, `FLAT` …).
- Rohbilder: FITS (`.fit`, `.fits`, `.fts`), XISF, Kamera-RAW (`.raf`, `.cr2`,
  `.cr3`, `.nef`, `.arw`, `.dng`, `.orf`, `.rw2`).
- Der Bildtyp kommt aus dem Ordner. Widerspricht `IMAGETYP` im Header, ist das
  ein **Fehler** (*Bildtyp passt nicht zum Ordner*).
- Rohbilder **ohne** diese Ordner (direkt im Session-Ordner) werden gelesen,
  der Typ dann aus dem Header geraten (Warnung *Rohbilder ohne Lights/Flats-Ordner*).
- Weitere Unterordner (außer `REJECTED`) werden mitgezählt (Hinweis), z. B. `Flats/Manual`.
- Kalibrierdaten gehören zur Session, in der sie verwendet werden. Liegen
  dieselben Darks/Bias in mehreren Sessions, wird das als Kopie gemeldet
  (Hinweis); vorhandene Darks/Bias werden grundsätzlich gemeldet, damit sie
  bei Bedarf in eine zentrale Kalibrier-Bibliothek wandern können.

### 4. `Logs` – Protokolle der Nacht

`Logs/`, `logs/` oder `log/` in der Session (oder im Zielordner):

- `PHD2_GuideLog_*.txt` – Guiding (Graph und RMS auf der Session-Seite, Punkt *PHD2*)
- `Autorun_Log_*.txt` – ASIAIR (mit Airgus ausgewertet, Punkt *Log*)
- N.I.N.A.-Logs (`JJJJMMTT-hhmmss-<Version>.log`)

Die Zeitstempel sind Ortszeit (Einstellung `TIMEZONE`).

### 5. `Lights/SAD` – StarAberrationDiagnostics

Der Ordner `SAD/`, den StarAberrationDiagnostics neben die Lights schreibt
(`StarAberrationSeries.csv/.json`, Karten-PNGs, KI-Bewertung). Nicht umbenennen
oder verschieben – die Zuordnung läuft über die Dateinamen der Lights.

### 6. `Processing` – Bearbeitung und Zwischenergebnisse

**Alle** Zwischenergebnisse liegen in einem Ordner, der **genau** `Processing`
heißt – im Zielordner (über alle Sessions) oder in einer Session (nur diese Nacht):
Stacks, Master, PixInsight-/Siril-Zwischenschritte, Projektdateien.

```
Processing/
├── master/                    WBPP-Ausgabe
│   ├── masterLight_BIN-1_…_RGB.xisf               ✔
│   ├── masterLight_BIN-1_…_RGB_autocrop.xisf      ✔
│   ├── masterLight_…_drizzle_2x[_autocrop].xisf   ✔
│   ├── masterDark_… / LN_Reference_… / …_ABE.xisf ✔ Zwischenschritte erlaubt
│   └── IC405_Final_V1.xisf                        ✔ Final (Name enthält „final“)
├── calibrated/ registered/ debayered/ …          ⚠ WBPP-Zwischenschritte – nach dem Stacken löschen
├── logs/                                          WBPP-Protokolle <JJJJMMTThhmmss>.log – behalten, werden ausgewertet
├── 01_crop.xisf
├── 02_gradient.xisf
└── Projekt.pxiproject
```

- Zwischenergebnisse außerhalb von `Processing` – in `PI/`, `PI/master`,
  `PixInsight/`, kleingeschrieben `processing/` oder als Stack direkt neben den
  Rohdaten – sind eine Warnung (*Zwischenergebnis nicht in „Processing“*).
- In `Processing/master/` (direkt darin) ist alles erlaubt: `masterLight_…`
  und `…_autocrop`, Kalibrier-Master, `LN_Reference_…`, weitere
  Bearbeitungsschritte und Finals (Name enthält „final“, z. B.
  `IC405_Final_V1.xisf`). Unterordner wie `master/registered/` zählen weiter
  als WBPP-Zwischenschritte.
- Ein WBPP-Ordner `master/` **außerhalb** von `Processing` (z. B. `PI/master/`)
  wird bemängelt: Kalibrier-Master als Warnung, alles außer `masterLight_…`
  als Hinweis.
- WBPP-Zwischenschritte (`calibrated`, `cosmetized`, `debayered`, `registered`,
  `measured`, `local_normalization`, `.xnml`, `.xdrz`, `LN_Reference_…`) sind
  eine Warnung – sie werden nach dem Stacken nicht mehr gebraucht.
- PhotonLedger zählt Zwischenergebnisse nicht mit; die Seite *Indexer ›
  Zwischenergebnisse* listet sie gruppiert nach Regel.

### 7. `Finals` – fertige Bilder

Fertige Ergebnisse in `Finals/` im Ziel- oder Session-Ordner. Bilder
(JPG, PNG, TIFF) erscheinen als **Versionen** des Ziels; das neueste Bild in
`Finals` ist das Titelbild, solange keins gewählt wurde.

- Bilder (JPG/PNG/TIFF) werden auch in `Processing/`, `PI/`, `DXO/`,
  `Selected/` oder direkt im Zielordner als Versionen erkannt.
- Ein Ordner **genau** `Finals` – im Ziel- oder im Session-Ordner – darf
  jedes Format enthalten, auch FITS/XISF (z. B. `IC1396_Final.xisf` neben dem
  JPG). FITS/XISF dort zählen nicht als Versionen, werden aber auch nicht
  gemeldet.
- Ordner mit mehr als 30 Bildern (Burst-Serien, registrierte Einzelbilder)
  gelten nicht als Versionen (`MAX_VERSIONS_PER_DIR`).
- Einzelbilder als TIFF (Name beginnt mit `Light_`, `Flat_`, `Dark_`, `Bias_`)
  sind keine Versionen.
- Dieselbe Version an mehreren Orten wird als Kopie gemeldet (Hinweis).

## Was nicht ins Archiv gehört

| Ordner/Datei | Verhalten |
|---|---|
| Kopien ganzer Sessions in einem zweiten Zielordner | doppelte Lights werden **einmal** gezählt, die Kopie gemeldet (Warnung *Lights liegen auch in …*) |
| `… (2).raf`, `Kopie`/`copy`-Dateien | Warnung *doppelt im selben Ordner* |
| `Kopie/`, `copy/`, `@eaDir/`, `#recycle/`, `.*` | werden übersprungen |
| Kollimations- oder Testaufnahmen | in einen Ordner legen, der in `IGNORE_DIRS` steht (z. B. `Kollimation`) |
| Ordner, die beim Scan nicht lesbar sind | werden nicht gelöscht, sondern im Lauf gemeldet |

## Vollständiges Beispiel

```
<Archiv>/
├── M97, M108 (Owl, Surfboard)/
│   ├── 2026-04-28/
│   │   ├── Lights/
│   │   │   ├── Light_M97_180.0s_Bin1_ISO1600_20260428-221502_0001.fit
│   │   │   ├── REJECTED/
│   │   │   └── SAD/
│   │   ├── Flats/  Darks/  Biases/
│   │   └── Logs/
│   │       ├── Autorun_Log_2026-04-28_220427.txt
│   │       └── PHD2_GuideLog_2026-04-28_222144.txt
│   ├── 2026-04-29/
│   │   └── Lights/  Flats/  Darks/  Biases/  Logs/
│   ├── Processing/
│   │   ├── master/
│   │   │   ├── masterLight_BIN-1_5496x3670_EXPOSURE-180.00s_FILTER-NoFilter_RGB.xisf
│   │   │   └── masterLight_BIN-1_5496x3670_EXPOSURE-180.00s_FILTER-NoFilter_RGB_autocrop.xisf
│   │   ├── 01_GraXpert.xisf
│   │   └── 02_BXT.xisf
│   └── Finals/
│       └── M97_M108_final.jpg
└── Mineral Moon/
    └── 2026-05-25/
        └── Lights/
```
