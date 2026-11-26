"""Phase 4: plausibility checks of the archive structure.

Everything is derived from what the indexer already stored - no file is opened.
Each check yields Issue rows with a severity:
  error    something is wrong and changes the numbers (wrong frame type, unreadable file)
  warning  likely a mistake that hurts processing (uncalibrated, darks don't match …)
  hint     convention / tidiness (stacks outside Processing, naming …)
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict

from sqlalchemy import delete, select

from webapp import db
from webapp.models import Frame, Intermediate, Issue, SadSeries, Session, Target, Version

from . import archives, rules, sad
from .catalog import separation
from .headers import CONTENT_CARD
from . import wbpp as wbpp_log

log = logging.getLogger("photonledger.checks")

NO_CATALOG_TYPES = {"Mond", "Sonne", "Meteore", "Satellit"}
_COPY_NAME = re.compile(r"\(\d+\)|[ _-](copy|kopie)\b|[ _-]\d+\.\w+$(?<=\s\d\.\w{3})", re.I)


def _median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def run(cfg: dict) -> int:
    db.session.execute(delete(Issue))
    frames = db.session.scalars(select(Frame)).all()
    sessions = {s.root: s for s in db.session.scalars(select(Session))}
    targets = {t.id: t for t in db.session.scalars(select(Target))}
    versions = db.session.scalars(select(Version)).all()
    temp_tol = cfg.get("CHECK_TEMP_TOLERANCE", 5.0)
    reject_ratio = cfg.get("CHECK_REJECT_RATIO", 0.3)
    tol = cfg["FOCAL_TOLERANCE"]
    out: list[Issue] = []

    def add(severity, code, message, hint=None, session=None, target_id=None, path=None, count=1):
        out.append(Issue(severity=severity, code=code, message=message, hint=hint,
                         session_root=session.root if session else None,
                         target_id=target_id if target_id is not None else (session.target_id if session else None),
                         path=path, count=count))

    by_root: dict[str, list[Frame]] = defaultdict(list)
    for f in frames:
        by_root[f.session_root].append(f)
    # the light a duplicate was copied from (the one that is counted)
    light_original: dict[tuple, Frame] = {}
    for f in frames:
        if f.frame_type == "light" and not f.duplicate and f.date_obs is not None:
            light_original.setdefault((f.camera, f.date_obs, f.exptime), f)

    # darks/bias that are copies of the same frame in another session folder: reported once as
    # "calibration_copies"; the folder holding the original keeps the "darks_bias_present" hint
    def first_folder(root):
        s_ = sessions.get(root)
        return (s_ is None or s_.date is None, s_.date if s_ and s_.date else None, root)
    cal_groups: dict[tuple, list[Frame]] = defaultdict(list)
    for f in frames:
        if f.frame_type in ("dark", "bias") and f.date_obs is not None and not f.rejected:
            cal_groups[(f.camera, f.frame_type, f.date_obs, f.exptime)].append(f)
    cal_copy: set[int] = set()
    for fs_ in cal_groups.values():
        roots_ = sorted({f.session_root for f in fs_}, key=first_folder)
        if len(roots_) > 1:
            cal_copy.update(f.id for f in fs_ if f.session_root != roots_[0])

    # ---------------------------------------------------------------- per file
    unreadable = [f for f in frames if f.error]
    for root, fs in _group(unreadable).items():
        add("error", "unreadable", f"{len(fs)} Datei(en) nicht lesbar: {fs[0].error}",
            "Datei beschädigt oder kein FITS/XISF/RAW – ersetzen oder löschen.",
            session=sessions.get(root), path=fs[0].path, count=len(fs))

    for root, fs in by_root.items():
        s = sessions.get(root)
        # header says something else than the folder
        wrong = Counter()
        sample = {}
        for f in fs:
            if f.error or not f.frame_type or not f.header:
                continue
            ht = rules.frame_type_from_header(_imagetyp(f))
            if ht and ht != f.frame_type and not (ht == "dark" and f.frame_type == "darkflat"):
                wrong[(f.frame_type, ht)] += 1
                sample.setdefault((f.frame_type, ht), f.path)
        for (folder_t, header_t), n in wrong.items():
            add("error", "type_mismatch",
                f"{n} Datei(en) im Ordner für {_de(folder_t)} sind laut Header {_de(header_t)}",
                "In den passenden Ordner verschieben – sie werden sonst falsch gezählt.",
                session=s, path=sample[(folder_t, header_t)], count=n)
        lights = [f for f in fs if f.frame_type == "light" and not f.rejected and not f.duplicate]
        missing = [f for f in lights if not f.error and (f.exptime is None or f.date_obs is None)]   # unreadable: own message
        if missing:
            add("error", "missing_header",
                f"{len(missing)} Light(s) ohne Belichtungszeit oder Aufnahmedatum im Header",
                "Fehlen EXPTIME/DATE-OBS (FITS/XISF) bzw. ExposureTime/DateTimeOriginal (EXIF bei RAW), "
                "kann die Belichtung nicht gezählt werden.",
                session=s, path=missing[0].path, count=len(missing))
        # content does not match the file extension (e.g. a FITS file renamed to .raf)
        renamed = defaultdict(list)
        for f in fs:
            if f.header and f.header.get(CONTENT_CARD):
                renamed[f.header[CONTENT_CARD]].append(f)
        for content, group in renamed.items():
            ext = group[0].path.rsplit(".", 1)[-1]
            add("warning", "wrong_extension",
                f"{len(group)} Datei(en) mit Endung „.{ext}“ sind {content}-Dateien",
                "Endung an den Inhalt anpassen (z. B. „.fit“ für FITS) – der Header wird trotzdem gelesen.\n"
                + "\n".join(f.path for f in group[:25]),
                session=s, path=group[0].path, count=len(group))

    # ---------------------------------------------------------------- per session
    for root, s in sessions.items():
        fs = by_root.get(root, [])
        lights = [f for f in fs if f.frame_type == "light" and not f.rejected and not f.duplicate]
        flats = [f for f in fs if f.frame_type == "flat" and not f.rejected]
        darks = [f for f in fs if f.frame_type == "dark" and not f.rejected]
        biases = [f for f in fs if f.frame_type == "bias" and not f.rejected]
        label = s.label or "(Zielordner)"

        t = targets.get(s.target_id)
        for (sev_, desc), group in _foreign_lights(lights, t).items():
            add(sev_, "foreign_lights",
                f"{len(group)} Light(s) in „{root}“ passen laut Header nicht zu {t.name}: {desc}",
                "Falsch einsortiert oder beim Kopieren überschrieben? Ordner und Header (OBJECT, RA/DEC) "
                "vergleichen.\n" + "\n".join(f.path for f in group[:25]),
                session=s, path=group[0].path, count=len(group))

        if s.duplicate_of:
            pairs = [(f, light_original.get((f.camera, f.date_obs, f.exptime)))
                     for f in fs if f.frame_type == "light" and f.duplicate]
            pairs = [(c, o) for c, o in pairs if o is not None]
            orig_roots = Counter(o.session_root for _, o in pairs)
            orig_root = orig_roots.most_common(1)[0][0] if orig_roots else s.duplicate_of
            add("warning", "duplicate",
                f"{s.duplicates} Lights in „{root}“ liegen auch in „{orig_root}“ (gleiche Kamera, Zeit und Belichtung)",
                _with_pairs("Kopie löschen oder in ein Archiv außerhalb des Bildordners verschieben; sie wird nicht "
                            "mitgezählt. Links die Kopie, rechts das gezählte Original.", pairs), session=s, path=pairs[0][0].path if pairs else None, count=s.duplicates)
        # darks / bias present (reported on purpose, e.g. to move them into a calibration library)
        own_darks = [f for f in darks if f.id not in cal_copy]          # copies are reported as duplicates
        own_biases = [f for f in biases if f.id not in cal_copy]
        if own_darks or own_biases:
            parts = [f"{len(own_darks)} Darks ({_cams(own_darks, lights)})" if own_darks else None,
                     f"{len(own_biases)} Bias ({_cams(own_biases, lights)})" if own_biases else None]
            copied = len(darks) + len(biases) - len(own_darks) - len(own_biases)
            add("hint", "darks_bias_present", f"„{label}“ enthält {' und '.join(p for p in parts if p)}",
                "Liegen Darks/Bias zentral in einer Kalibrier-Bibliothek, können sie hier entfallen."
                + (f" Dazu {copied} Kopie(n) aus anderen Ordnern – siehe „Doppelte Bilder“." if copied else ""),
                session=s, path=(own_darks or own_biases)[0].path, count=len(own_darks) + len(own_biases))

        if not lights:
            if not s.duplicates and (flats or darks or biases):
                cal = [x for x in (("Flats", flats), ("Darks", darks), ("Bias", biases)) if x[1]]
                add("hint", "calibration_only", f"„{label}“ enthält nur Kalibrierdaten: "
                    + ", ".join(f"{len(fs_)} {n} ({_cams(fs_)})" for n, fs_ in cal),
                    "Gewollt (Bibliothek) oder fehlen die Lights?", session=s)
            continue

        # flats present? (not needed for Moon, Sun, meteors)
        t = targets.get(s.target_id)
        if not flats and not (t and t.object_type in NO_CATALOG_TYPES):
            add("hint", "no_flats", f"„{label}“: {len(lights)} Lights ohne Flats",
                "Flats im Session-Ordner ablegen (Ordner „Flats“), falls vorhanden.", session=s)

        # darks: exposure and temperature
        if darks:
            light_exp = Counter(round(f.exptime, 1) for f in lights if f.exptime)
            dark_exp = {round(f.exptime, 1) for f in darks if f.exptime}
            unmatched = {e: n for e, n in light_exp.items()
                         if not any(abs(e - d) <= max(0.5, e * 0.01) for d in dark_exp)}
            if not dark_exp:
                add("hint", "dark_no_header", f"„{label}“: Darks ({_cams(darks, lights)}) ohne Belichtungszeit im Header",
                    "Ob sie zu den Lights passen, lässt sich nicht prüfen.", session=s, path=darks[0].path,
                    count=len(darks))
            elif unmatched:
                exps = ", ".join(f"{e:g} s" for e in sorted(unmatched))
                add("warning", "dark_exposure",
                    f"„{label}“: keine Darks mit der Belichtungszeit der Lights ({exps}) – Darks von {_cams(darks, lights)}",
                    f"Vorhandene Darks: {', '.join(f'{d:g} s' for d in sorted(dark_exp)) or '–'}; "
                    f"Lights von {_cams(lights)}.",
                    session=s, count=sum(unmatched.values()))
            lt, dt = _median(f.temp for f in lights), _median(f.temp for f in darks)
            if lt is not None and dt is not None and abs(lt - dt) > temp_tol:
                add("hint", "dark_temperature",
                    f"„{label}“: Darks ({_cams(darks, lights)}) {dt:.0f} °C, Lights {lt:.0f} °C (Abweichung {abs(lt - dt):.0f} K)",
                    f"Mehr als {temp_tol:g} K Unterschied – Dark-Skalierung prüfen.", session=s)

        # flats: same camera, optics and filters as the lights
        if flats:
            lcams = {f.camera for f in lights if f.camera}
            fcams = {f.camera for f in flats if f.camera}
            if lcams and fcams and not (lcams & fcams):
                add("warning", "flat_camera",
                    f"„{label}“: Flats von {', '.join(sorted(fcams))}, Lights von {', '.join(sorted(lcams))}",
                    "Flats müssen mit derselben Kamera aufgenommen sein.", session=s)
            lf, ff = _median(f.focal for f in lights), _median(f.focal for f in flats)
            if lf and ff and abs(lf - ff) / lf > tol:
                add("warning", "flat_focal",
                    f"„{label}“: Flats bei {ff:.0f} mm, Lights bei {lf:.0f} mm",
                    "Andere Optik/Brennweite – die Flats passen vermutlich nicht.", session=s)
            lfil = {f.filter for f in lights if f.filter}
            ffil = {f.filter for f in flats if f.filter}
            missing_f = lfil - ffil
            if ffil and missing_f and not (ffil == {"Ohne Filter"} or missing_f == {"Ohne Filter"}):
                add("warning", "flat_filter",
                    f"„{label}“: keine Flats für Filter {', '.join(sorted(missing_f))}",
                    f"Flats vorhanden für: {', '.join(sorted(ffil))}.", session=s)

        # equipment / dates
        if len(s.datasets or []) > 1:
            parts = ", ".join(f"{d['camera'] or '?'} {d['focal'] or '?'} mm" for d in s.datasets)
            add("warning", "mixed_equipment", f"„{label}“ mischt mehrere Ausrüstungen: {parts}",
                "Je Kamera/Optik einen eigenen Session-Ordner anlegen.", session=s)
        fdate = rules.folder_date(root.rsplit("/", 1)[-1])
        nights = sorted(f.night for f in lights if f.night)
        if fdate and nights and all(abs((n - fdate).days) > 1 for n in set(nights)):
            add("warning", "folder_date",
                f"Ordnerdatum {fdate:%d.%m.%Y}, die Lights stammen aber aus der Nacht {nights[0]:%d.%m.%Y}"
                + (f" bis {nights[-1]:%d.%m.%Y}" if nights[-1] != nights[0] else ""),
                "Ordner umbenennen oder Lights in den richtigen Ordner verschieben.", session=s)
        elif not fdate and len(set(nights)) > 1:
            add("hint", "multi_night",
                f"„{label}“ enthält Lights aus {len(set(nights))} Nächten ohne Datumsordner",
                "Je Nacht einen Ordner JJJJ-MM-TT anlegen – dann werden die Sessions getrennt gezählt.",
                session=s)

        rejected = s.rejected
        if lights and rejected / (rejected + len(lights)) > reject_ratio:
            share = rejected / (rejected + len(lights))
            add("hint", "many_rejected",
                f"„{label}“: {share:.0%} der Lights aussortiert ({rejected} von {rejected + len(lights)})",
                "Wolken, Tau, Nachführung? Nur zur Info.", session=s)

        # naming conventions of frame folders
        odd = sorted({part for f in fs for part in f.path.split("/")[:-1]
                      if part.lower() in rules.FRAME_DIRS and part not in ("Lights", "Flats", "Darks", "Biases", "DarkFlats")})
        if odd:
            add("hint", "folder_naming", f"„{label}“: Ordnername(n) {', '.join(odd)} weichen von Lights/Flats/Darks/Biases ab",
                "Wird erkannt – nur der Einheitlichkeit halber.", session=s)

    # ---------------------------------------------------------------- structure
    for root, s in sessions.items():
        fs = [f for f in by_root.get(root, []) if f.frame_type]
        if not fs:
            continue
        label = s.label or "(Zielordner)"
        tfolder = fs[0].target_folder
        if root == tfolder and any(f.frame_type == "light" for f in fs):
            add("warning", "no_session_folder",
                f"Lights liegen direkt im Zielordner „{tfolder}“, ohne Session-Ordner",
                "Struktur: <Ziel>/<JJJJ-MM-TT>/Lights – sonst lassen sich Nächte nicht trennen.", session=s)
        elif root != tfolder and not rules.folder_date(root.rsplit("/", 1)[-1]):
            add("hint", "session_without_date", f"Session-Ordner „{label}“ hat kein Datum im Namen",
                "Empfohlen: JJJJ-MM-TT, ggf. mit Zusatz („2026-07-08 DWARF3“).", session=s)
        loose = [f for f in fs if f.path.rsplit("/", 1)[0] == root]
        if loose:
            add("warning", "loose_frames",
                f"„{label}“: {len(loose)} Rohbild(er) ohne Lights/Flats/Darks/Biases-Ordner",
                "Bildtyp wird nur aus dem Header geraten – in den passenden Unterordner verschieben.",
                session=s, path=loose[0].path, count=len(loose))
        extra = Counter()
        for f in fs:
            parts = f.path[len(root) + 1:].split("/")[:-1] if root != tfolder or f.path.startswith(root + "/") else []
            below = [p for p in parts[1:] if p.lower() not in rules.REJECT_DIRS]
            if parts and parts[0].lower() in rules.FRAME_DIRS and below:
                extra["/".join(parts)] += 1
        for sub, n in extra.items():
            add("hint", "extra_subfolder", f"„{label}“: {n} Bild(er) im Unterordner „{sub}“",
                "Unterordner (außer REJECTED) werden mitgezählt – gewollt?", session=s, count=n)
    for folder in sorted({f.target_folder for f in frames if f.frame_type}):
        if "/" in archives.inner(cfg, folder):   # target folder below a year folder (old layout)
            add("hint", "year_folder", f"Zielordner „{folder}“ liegt in einem Jahresordner",
                "Struktur: <Archiv>/<Ziel>/… – den Zielordner eine Ebene höher verschieben.",
                target_id=_target_of(folder, sessions))

    # ---------------------------------------------------------------- duplicates
    # the same sub frame (camera, type, DATE-OBS, exposure) in several places
    seen: dict[tuple, list[Frame]] = defaultdict(list)
    for f in frames:
        if f.frame_type and f.date_obs is not None:
            seen[(f.camera, f.frame_type, f.date_obs, f.exptime)].append(f)
    same_folder = defaultdict(list)
    copies: dict[tuple, list[Frame]] = defaultdict(list)
    for k, fs in seen.items():
        if len(fs) < 2:
            continue
        roots = sorted({f.session_root for f in fs})
        if len(roots) == 1:
            # second-precision timestamps (e.g. camera EXIF) repeat for bursts of short frames: only report
            # when the time is precise or a copy-named file has a twin of the same size (a camera or import
            # tool also writes "… (2).raf" for a second shot in the same second, which differs in size)
            precise = k[2].microsecond != 0
            if precise:
                # the original is the file whose name does not look like a copy (else the first one)
                ordered = sorted(fs, key=lambda f: (bool(_COPY_NAME.search(f.path.rsplit("/", 1)[-1])),
                                                    len(f.path), f.path))
                same_folder[roots[0]].extend((d, ordered[0]) for d in ordered[1:])
            else:
                by_size = defaultdict(list)
                for f in fs:
                    by_size[f.size].append(f)
                for twins in by_size.values():
                    copied = [f for f in twins if _COPY_NAME.search(f.path.rsplit("/", 1)[-1])]
                    if len(twins) > 1 and copied:
                        orig = sorted(twins, key=lambda f: (f in copied, len(f.path), f.path))[0]
                        same_folder[roots[0]].extend((d, orig) for d in copied if d is not orig)
        elif k[1] != "light":                       # light copies are reported per session above
            copies[(k[1], tuple(roots))].extend(fs)
    for root, pairs in same_folder.items():
        folder = sorted({d.path.rsplit("/", 1)[0] for d, _ in pairs})
        add("warning", "duplicate_in_folder",
            f"{len(pairs)} Bild(er) doppelt in „{folder[0]}“{' …' if len(folder) > 1 else ''} (gleicher Aufnahmezeitpunkt)",
            _with_pairs("Mehrfach kopierte Dateien („… (2).raf“) entfernen – links jeweils die Kopie.", pairs),
            session=sessions.get(root), path=pairs[0][0].path, count=len(pairs))
    # partial copies of one set (e.g. 8 bias in 3 folders, 17 more in 2 of them) become one message:
    # merge the groups of a type and camera whose folders overlap
    clusters: list[tuple[str, set, list[Frame], set]] = []
    for (ftype, roots), fs in sorted(copies.items(), key=lambda kv: -len(kv[0][1])):
        cams = {f.camera for f in fs}
        hit = [c for c in clusters if c[0] == ftype and c[1] & cams and c[3] & set(roots)]
        if hit:
            c = hit[0]
            c[1].update(cams); c[2].extend(fs); c[3].update(roots)
        else:
            clusters.append((ftype, cams, list(fs), set(roots)))
    for ftype, _, fs, roots_set in clusters:
        roots = sorted(roots_set, key=first_folder)
        key = lambda f: (f.camera, f.date_obs, f.exptime)  # noqa: E731
        by_key = defaultdict(list)
        for f in fs:
            by_key[key(f)].append(f)
        pairs = []
        for k in sorted(by_key, key=lambda k: min(f.path for f in by_key[k])):
            grp = sorted(by_key[k], key=lambda f: (first_folder(f.session_root), f.path))
            pairs.extend((grp[0], d) for d in grp[1:])
        n = len(by_key)
        per_root = Counter(f.session_root for f in fs)
        where = ", ".join(f"„{r}“" + (f" ({per_root[r]})" if len(set(per_root.values())) > 1 else "")
                          for r in roots)
        add("hint", "calibration_copies",
            f"{n} {_de(ftype)} ({_cams(fs)}) liegen identisch in {len(roots)} Ordnern: " + where,
            _with_pairs(f"Wiederverwendete {_de(ftype)} der Kamera {_cams(fs)}{_cal_props(fs)} – einmal zentral "
                        f"ablegen spart Platz. Links das Original im ältesten Ordner.", pairs),
            session=sessions.get(roots[0]), path=fs[0].path, count=len(fs))
    # versions: same file name and size in different folders
    vseen: dict[tuple, list[Version]] = defaultdict(list)
    for v in versions:
        vseen[(v.filename.lower(), v.size)].append(v)
    for (name, _), vs in vseen.items():
        if len(vs) > 1:
            add("hint", "duplicate_version", f"Version „{vs[0].filename}“ liegt {len(vs)}× im Archiv",
                "\n".join(v.path for v in vs), target_id=vs[0].target_id, path=vs[0].path, count=len(vs))

    # ---------------------------------------------------------------- StarAberrationDiagnostics
    series_by_root = {}
    for sr in db.session.scalars(select(SadSeries)):
        series_by_root[sr.session_root] = sr
        s = sessions.get(sr.session_root)
        label = (s.label if s else None) or "(Zielordner)"
        if sr.error:
            add("error", "sad_unreadable", f"SAD-Ergebnisse in „{sr.folder}“ nicht lesbar: {sr.error}",
                "Analyse in StarAberrationDiagnostics neu speichern.", session=s, path=sr.folder)
            continue
        sm = sad.summary(sr, cfg.get("SAD_OUTLIER_FACTOR", 1.3))
        if sm["tilt"] is not None and sm["tilt"] > cfg.get("SAD_TILT_WARN", 0.25):
            add("hint", "sad_tilt",
                f"„{label}“: Verkippung {_n(sm['tilt'])} (Richtung {sm['tilt_dir'] or 0:.0f}°) laut StarAberrationDiagnostics",
                f"Grenzwert {_n(cfg.get('SAD_TILT_WARN', 0.25))} – Kamera/Adapter prüfen; Karte „Verkippung“ auf der Session-Seite.",
                session=s)
        if sm["ratio"] and sm["ratio"] > cfg.get("SAD_RATIO_WARN", 1.4):
            add("hint", "sad_edge",
                f"„{label}“: Sterne am Rand {_n(sm['ratio'])}× so groß wie in der Mitte",
                f"Grenzwert {_n(cfg.get('SAD_RATIO_WARN', 1.4))} – Bildfeldwölbung, Korrektorabstand oder Objektiv; Karte „Sterngröße“.",
                session=s)
        if sm["ellipticity"] is not None and sm["ellipticity"] > cfg.get("SAD_ELLIPTICITY_WARN", 0.15):
            add("hint", "sad_tracking",
                f"„{label}“: Sterne gestreckt (Elliptizität {_n(sm['ellipticity'])}) – Nachführung oder Durchbiegung?",
                f"Grenzwert {_n(cfg.get('SAD_ELLIPTICITY_WARN', 0.15))} – Karte „Sternform“.", session=s)
        if sm["outliers"]:
            add("hint", "sad_outliers",
                f"„{label}“: {len(sm['outliers'])} Light(s) mit FWHM über dem {_n(cfg.get('SAD_OUTLIER_FACTOR', 1.3), 1)}-fachen Median – Kandidaten für REJECTED",
                "\n".join(f.name for f in sm["outliers"][:30]), session=s, count=len(sm["outliers"]))
        if sm["errors"]:
            add("hint", "sad_frame_errors", f"„{label}“: SAD konnte {sm['errors']} Light(s) nicht auswerten",
                None, session=s, count=sm["errors"])
    missing = [s for s in sessions.values() if s.lights and s.root not in series_by_root
               and (targets.get(s.target_id) is None or targets[s.target_id].object_type not in NO_CATALOG_TYPES)]
    if missing:
        add("hint", "sad_missing", f"{len(missing)} Session(s) ohne Auswertung von StarAberrationDiagnostics",
            "\n".join(s.root for s in sorted(missing, key=lambda s: s.root)))

    # ---------------------------------------------------------------- intermediate results
    # stacks, masters and PixInsight/Siril steps belong into a folder named exactly "Processing"
    folder_target = {f: t.id for t in targets.values() for f in t.folders or []}
    misplaced: dict[str, list[str]] = defaultdict(list)       # containing folder -> files
    target_of_dir: dict[str, str] = {}
    for i in db.session.scalars(select(Intermediate)):
        if not rules.allowed_location(i.path, sessions):
            d = i.path.rsplit("/", 1)[0]
            misplaced[d].append(i.path)
            target_of_dir[d] = i.target_folder
    for f in frames:
        if f.frame_type is None and not f.error:                  # stacked FITS lying next to raw data
            d = f.path.rsplit("/", 1)[0]
            misplaced[d].append(f.path)
            target_of_dir[d] = f.target_folder
    for d, paths in sorted(misplaced.items()):
        tfolder = target_of_dir[d]
        where = d[len(tfolder) + 1:] if d != tfolder else "(Zielordner)"
        wrong_case = [p for p in d.split("/") if p.lower() == rules.PROCESSING_DIR.lower() and p != rules.PROCESSING_DIR]
        if wrong_case:
            msg = f"Ordner „{wrong_case[0]}“ bei „{where}“ – einheitlich „{rules.PROCESSING_DIR}“ schreiben"
        else:
            msg = f"{len(paths)} Zwischenergebnis(se) liegen in „{where}“ statt in „{rules.PROCESSING_DIR}“"
        add("warning", "intermediate_location", msg,
            f"Stacks, Master und PixInsight-/Siril-Zwischenschritte gehören in einen Ordner „{rules.PROCESSING_DIR}“, "
            f"fertige Ergebnisse in jedem Format in „{rules.FINALS_DIR}“ (jeweils im Ziel- oder Session-Ordner). Beispiel: {paths[0].rsplit('/', 1)[-1]}",
            target_id=folder_target.get(tfolder), path=paths[0], count=len(paths))

    # ---------------------------------------------------------------- WBPP results
    # keep only masterLight_… and masterLight_…_autocrop; everything else of WBPP can go
    wbpp: dict[tuple[str, str], list[str]] = defaultdict(list)   # (kind, folder) -> files
    for i in db.session.scalars(select(Intermediate)):
        if rules.in_processing_master(i.path):                     # steps and finals allowed there
            continue
        kind = rules.wbpp_kind(i.path)
        if kind in ("step", "calibration_master", "other"):
            d = i.path.rsplit("/", 1)[0]
            wbpp[(kind, d)].append(i.path)
            target_of_dir.setdefault(d, i.target_folder)
    texts = {
        "step": ("warning", "wbpp_steps", "{n} WBPP-/Stacking-Zwischenschritt(e) in „{where}“",
                 "Kalibrierte, registrierte, debayerte Einzelbilder, Local-Normalization- und Drizzle-Daten "
                 "werden nach dem Stacken nicht mehr gebraucht."),
        "calibration_master": ("warning", "wbpp_calibration_masters", "{n} Kalibrier-Master (masterDark/Flat/Bias) in „{where}“",
                               "Von WBPP bleiben nur masterLight und masterLight_autocrop; Kalibrier-Master gehören "
                               "höchstens in eine zentrale Kalibrier-Bibliothek (in Processing/master erlaubt)."),
        "other": ("hint", "wbpp_other", "{n} weitere Datei(en) im WBPP-Ordner „{where}“",
                  "Im WBPP-Ordner master/ sollen nur masterLight und masterLight_autocrop liegen – "
                  "Bearbeitungsschritte und Finals in Processing/master ablegen."),
    }
    for (kind, d), paths in sorted(wbpp.items()):
        sev, code, msg, hint = texts[kind]
        tfolder = target_of_dir[d]
        where = d[len(tfolder) + 1:] if d != tfolder else "(Zielordner)"
        names = sorted(p.rsplit("/", 1)[-1] for p in paths)
        add(sev, code, msg.format(n=len(paths), where=where),
            hint + "\n" + "\n".join(names[:25]) + ("\n…" if len(names) > 25 else ""),
            target_id=folder_target.get(tfolder), path=paths[0], count=len(paths))

    # ---------------------------------------------------------------- WBPP runs (logs)
    lights_by_folder: dict[str, list[Frame]] = defaultdict(list)
    for f in frames:
        if f.frame_type == "light":
            lights_by_folder[f.target_folder].append(f)
    for t in targets.values():
        runs = wbpp_log.runs_by_target(t.folders or [])
        if not runs:
            continue
        newest = runs[0]
        latest_ok = next((w for w in runs if wbpp_log.master_written(w)), None)
        when = lambda w: w.started.strftime("%d.%m.%Y %H:%M") if w.started else "?"  # noqa: E731
        if not wbpp_log.master_written(newest):
            first_err = (newest.errors or [{}])[0].get("message", "ohne Fehlermeldung")
            add("warning", "wbpp_run_failed", f"{t.name}: letzter WBPP-Lauf ({when(newest)}) ohne masterLight – {first_err}",
                "Log auf der Session-Seite unter „WBPP-Läufe“." + ("" if latest_ok else " Es gibt keinen erfolgreichen Lauf."),
                target_id=t.id, path=newest.path)
        if not latest_ok:
            continue
        lights = [f for fd in t.folders or [] for f in lights_by_folder.get(fd, [])]
        cov = wbpp_log.coverage(latest_ok, lights)
        if cov["newer"]:
            add("hint", "wbpp_outdated", f"{t.name}: {len(cov['newer'])} Light(s) sind neuer als der WBPP-Lauf vom {when(latest_ok)}",
                "Der masterLight enthält diese Nächte noch nicht – neu stacken.", target_id=t.id, path=latest_ok.path,
                count=len(cov["newer"]))
        elif cov["missing"]:
            add("hint", "wbpp_missing_lights", f"{t.name}: {len(cov['missing'])} verwendbare Light(s) fehlen im Stack vom {when(latest_ok)}",
                "\n".join(cov["missing"][:25]), target_id=t.id, path=latest_ok.path, count=len(cov["missing"]))
        if cov["rejected_now"]:
            add("hint", "wbpp_rejected_in_stack", f"{t.name}: {len(cov['rejected_now'])} Light(s) im Stack liegen inzwischen in REJECTED",
                "\n".join(cov["rejected_now"][:25]), target_id=t.id, path=latest_ok.path, count=len(cov["rejected_now"]))
        errs = Counter(e["message"] for e in latest_ok.errors or [])
        if errs:
            add("hint", "wbpp_errors", f"{t.name}: WBPP meldet {sum(errs.values())} Fehler im Lauf vom {when(latest_ok)}",
                "\n".join(f"{m} ({n}×)" if n > 1 else m for m, n in errs.most_common(8)), target_id=t.id, path=latest_ok.path)
        neg = sum(n for m, n in (latest_ok.warnings or {}).items() if m.startswith("negative or insignificant pixel"))
        if neg:
            add("hint", "wbpp_calibration", f"{t.name}: {neg} Lights mit negativen Pixelwerten nach der Kalibrierung",
                "Darks zu hell oder ohne Pedestal kalibriert – Output Pedestal in WBPP setzen oder passende Darks verwenden.",
                target_id=t.id, path=latest_ok.path, count=neg)

    has_version = {v.target_id for v in versions if v.target_id}
    for t in targets.values():
        for w in t.warnings or []:
            add("warning", "pointing", w, "Ordner und Koordinaten (OBJCTRA/RA) vergleichen.", target_id=t.id)
        if t.lights and t.id not in has_version:
            add("hint", "no_version", f"{t.name}: noch keine bearbeitete Version (JPG/TIFF in Finals/ oder Processing/)",
                None, target_id=t.id)
        if not t.catalog_ids and t.object_type not in NO_CATALOG_TYPES:
            add("hint", "no_catalog_id", f"Ordner „{t.folders[0]}“ enthält keine Katalognummer",
                "Z. B. „NGC 1234 (Name)“ – dann gibt es Katalogdaten und Objekte im Bildfeld.", target_id=t.id)

    db.session.add_all(out)
    db.session.commit()
    log.info("%d Prüfhinweise", len(out))
    return len(out)


PAIR_LIMIT = 25


_ISO_NAME = re.compile(r"(?:^|[_ -])ISO(\d{2,6})(?=[_ .-]|$)", re.I)


def _cal_props(frames) -> str:
    """Exposure, ISO/gain and temperature of calibration frames, e.g. " (180 s, ISO 800, 14–15 °C)".
    ISO comes from the header or, for DSLR frames saved by ASIAIR & co., from the file name."""
    def span(vals, fmt):
        vals = sorted({v for v in vals if v is not None})
        if not vals:
            return None
        lo, hi = fmt(vals[0]), fmt(vals[-1])
        return lo if lo == hi else f"{lo}–{hi}"
    exps = [f.exptime for f in frames if f.exptime is not None]
    exp = span(exps, lambda v: f"{v * 1000:.3g} ms" if v < 1 else f"{v:g} s")
    if exp and " ms–" in exp:
        exp = exp.replace(" ms–", "–")
    elif exp and " s–" in exp:
        exp = exp.replace(" s–", "–")
    isos = [f.iso for f in frames if f.iso] or [
        float(m.group(1)) for f in frames if (m := _ISO_NAME.search(f.path.rsplit("/", 1)[-1]))]
    iso = span(isos, lambda v: f"{v:g}")
    gain = span([f.gain for f in frames], lambda v: f"{v:g}")
    temp = span([round(f.temp) for f in frames if f.temp is not None], lambda v: f"{v:.0f}")
    parts = [exp, f"ISO {iso}" if iso else (f"Gain {gain}" if gain else None), f"{temp} °C" if temp else None]
    parts = [p for p in parts if p]
    return f" ({', '.join(parts)})" if parts else ""


def _cams(frames, lights=()) -> str:
    """Camera(s) the frames were taken with, for messages about calibration frames. Without a camera in the
    header, the camera of the session's lights is named as the likely one."""
    names = sorted({f.camera for f in frames if f.camera})
    if names:
        return ", ".join(names)
    lcams = sorted({f.camera for f in lights if f.camera})
    return f"Kamera nicht im Header, Lights: {', '.join(lcams)}" if lcams else "Kamera unbekannt"


def _with_pairs(text: str, pairs) -> str:
    return text + ("\n" + _pairs(pairs) if pairs else "")


def _pairs(pairs, limit: int = PAIR_LIMIT) -> str:
    """One hint line per pair of identical files: full path of both (from the archive root)."""
    lines = [f"{a.path}  ⇄  {b.path}" for a, b in sorted(pairs, key=lambda p: (p[0].path, p[1].path))[:limit]]
    if len(pairs) > limit:
        lines.append(f"… und {len(pairs) - limit} weitere")
    return "\n".join(lines)


def _n(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def _target_of(folder: str, sessions: dict) -> int | None:
    return next((s.target_id for s in sessions.values() if s.root == folder or s.root.startswith(folder + "/")), None)


def _imagetyp(f: Frame) -> str | None:
    h = f.header or {}
    return h.get("IMAGETYP") or h.get("FRAME") or h.get("FRAMETYP")


def _group(frames: list[Frame]) -> dict[str, list[Frame]]:
    out: dict[str, list[Frame]] = defaultdict(list)
    for f in frames:
        out[f.session_root].append(f)
    return out


def _de(t: str) -> str:
    return {"light": "Lights", "flat": "Flats", "dark": "Darks", "bias": "Bias", "darkflat": "Dark-Flats"}.get(t, t)


def _foreign_lights(lights, t) -> dict[tuple[str, str], list[Frame]]:
    """Lights whose header names another object (OBJECT) or points elsewhere (RA/DEC) than the target of
    their folder -> {(severity, description): frames}. A matching OBJECT or a position on the target
    confirms a light; coordinates alone only count where the target has a catalogue position."""
    if t is None:
        return {}
    ids = set(t.catalog_ids or [])
    for folder in t.folders or []:
        ids.update(rules.parse_target_folder(folder.rsplit("/", 1)[-1]).catalog_ids)
    limit = max(2.0, max(t.fov_w or 0, t.fov_h or 0) / 60 * 0.75)
    out: dict[tuple[str, str], list[Frame]] = defaultdict(list)
    for f in lights:
        obj = rules.parse_target_folder(f.object_name).catalog_ids if f.object_name else ()
        # without catalogue number the target position is only the median of its lights: not a reference
        off = (separation(t.ra, t.dec, f.ra, f.dec)
               if ids and None not in (t.ra, t.dec, f.ra, f.dec) else None)
        if obj and set(obj) & ids:
            continue                                   # header names the target (or one in its field)
        if off is not None and off <= limit:
            continue                                   # points at the target, whatever OBJECT says
        if obj:
            # another catalogue object; for a folder without catalogue number this is only a hint
            out[("warning" if ids else "hint", f"OBJECT „{f.object_name.strip()}“")].append(f)
        elif off is not None:
            out[("warning", f"{off:.0f}° neben dem Ziel")].append(f)
    return out

