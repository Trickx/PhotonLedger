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

from . import rules, sad
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
        missing = [f for f in lights if f.exptime is None or f.date_obs is None]
        if missing:
            add("error", "missing_header",
                f"{len(missing)} Light(s) ohne Belichtungszeit oder Aufnahmedatum im Header",
                "Fehlen EXPTIME/DATE-OBS, kann die Belichtung nicht gezählt werden.",
                session=s, path=missing[0].path, count=len(missing))

    # ---------------------------------------------------------------- per session
    for root, s in sessions.items():
        fs = by_root.get(root, [])
        lights = [f for f in fs if f.frame_type == "light" and not f.rejected and not f.duplicate]
        flats = [f for f in fs if f.frame_type == "flat" and not f.rejected]
        darks = [f for f in fs if f.frame_type == "dark" and not f.rejected]
        biases = [f for f in fs if f.frame_type == "bias" and not f.rejected]
        label = s.label or "(Zielordner)"

        if s.duplicate_of:
            add("warning", "duplicate",
                f"{s.duplicates} Lights liegen auch in „{s.duplicate_of}“ (gleiche Kamera, Zeit und Belichtung)",
                "Kopie löschen oder in ein Archiv außerhalb des Bildordners verschieben; sie wird nicht mitgezählt.",
                session=s, count=s.duplicates)
        # darks / bias present (reported on purpose, e.g. to move them into a calibration library)
        if darks or biases:
            parts = [f"{len(darks)} Darks" if darks else None, f"{len(biases)} Bias" if biases else None]
            add("hint", "darks_bias_present", f"„{label}“ enthält {' und '.join(p for p in parts if p)}",
                "Liegen Darks/Bias zentral in einer Kalibrier-Bibliothek, können sie hier entfallen.",
                session=s, path=(darks or biases)[0].path, count=len(darks) + len(biases))

        if not lights:
            if not s.duplicates and (flats or darks or biases):
                add("hint", "calibration_only", f"„{label}“ enthält nur Kalibrierdaten",
                    "Gewollt (Bibliothek) oder fehlen die Lights?", session=s)
            continue

        # flats present? (not needed for Moon, Sun, meteors)
        t = targets.get(s.target_id)
        if not flats and not (t and t.object_type in NO_CATALOG_TYPES):
            add("warning", "no_flats", f"„{label}“: {len(lights)} Lights ohne Flats",
                "Flats im Session-Ordner ablegen (Ordner „Flats“), falls vorhanden.", session=s)

        # darks: exposure and temperature
        if darks:
            light_exp = Counter(round(f.exptime, 1) for f in lights if f.exptime)
            dark_exp = {round(f.exptime, 1) for f in darks if f.exptime}
            unmatched = {e: n for e, n in light_exp.items()
                         if not any(abs(e - d) <= max(0.5, e * 0.01) for d in dark_exp)}
            if not dark_exp:
                add("hint", "dark_no_header", f"„{label}“: Darks ohne Belichtungszeit im Header",
                    "Ob sie zu den Lights passen, lässt sich nicht prüfen.", session=s, path=darks[0].path,
                    count=len(darks))
            elif unmatched:
                exps = ", ".join(f"{e:g} s" for e in sorted(unmatched))
                add("warning", "dark_exposure",
                    f"„{label}“: keine Darks mit der Belichtungszeit der Lights ({exps})",
                    f"Vorhandene Darks: {', '.join(f'{d:g} s' for d in sorted(dark_exp)) or '–'}.",
                    session=s, count=sum(unmatched.values()))
            lt, dt = _median(f.temp for f in lights), _median(f.temp for f in darks)
            if lt is not None and dt is not None and abs(lt - dt) > temp_tol:
                add("hint", "dark_temperature",
                    f"„{label}“: Darks {dt:.0f} °C, Lights {lt:.0f} °C (Abweichung {abs(lt - dt):.0f} K)",
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
        if "/" in folder:                # target folder below a year folder (old layout)
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
            # second-precision timestamps (e.g. camera EXIF) repeat for bursts of short frames:
            # only report when the time is precise or the file name looks like a copy
            precise = k[2].microsecond != 0
            copied = [f for f in fs if _COPY_NAME.search(f.path.rsplit("/", 1)[-1])]
            if precise:
                same_folder[roots[0]].extend(fs[1:])
            elif copied:
                same_folder[roots[0]].extend(copied)
        elif k[1] != "light":                       # light copies are reported per session above
            copies[(k[1], tuple(roots))].extend(fs)
    for root, fs in same_folder.items():
        add("warning", "duplicate_in_folder",
            f"{len(fs)} Bild(er) doppelt im selben Ordner (gleicher Aufnahmezeitpunkt), z. B. {fs[0].path.rsplit('/', 1)[-1]}",
            "Mehrfach kopierte Dateien („… (2).raf“) entfernen.", session=sessions.get(root), path=fs[0].path, count=len(fs))
    for (ftype, roots), fs in copies.items():
        n = len(fs) // len(roots)
        names = [sessions[r].label or r if r in sessions else r for r in roots]
        add("hint", "calibration_copies",
            f"{n} {_de(ftype)} liegen identisch in {len(roots)} Ordnern: " + ", ".join(
                f"{(sessions[r].target.name + ' ') if r in sessions and sessions[r].target else ''}{nm}" for r, nm in zip(roots, names)),
            "Wiederverwendete Kalibrierdaten – einmal zentral ablegen spart Platz.",
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
            add("warning", "sad_tilt",
                f"„{label}“: Verkippung {_n(sm['tilt'])} (Richtung {sm['tilt_dir'] or 0:.0f}°) laut StarAberrationDiagnostics",
                f"Grenzwert {_n(cfg.get('SAD_TILT_WARN', 0.25))} – Kamera/Adapter prüfen; Karte „Verkippung“ auf der Session-Seite.",
                session=s)
        if sm["ratio"] and sm["ratio"] > cfg.get("SAD_RATIO_WARN", 1.4):
            add("warning", "sad_edge",
                f"„{label}“: Sterne am Rand {_n(sm['ratio'])}× so groß wie in der Mitte",
                f"Grenzwert {_n(cfg.get('SAD_RATIO_WARN', 1.4))} – Bildfeldwölbung, Korrektorabstand oder Objektiv; Karte „Sterngröße“.",
                session=s)
        if sm["ellipticity"] is not None and sm["ellipticity"] > cfg.get("SAD_ELLIPTICITY_WARN", 0.15):
            add("warning", "sad_tracking",
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
            f"Stacks, Master und PixInsight-/Siril-Zwischenschritte gehören in einen Ordner „{rules.PROCESSING_DIR}“ "
            f"(im Ziel- oder Session-Ordner), Ergebnisse einer Session auch in „<Session>/{rules.FINALS_DIR}“. Beispiel: {paths[0].rsplit('/', 1)[-1]}",
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

