"""Phase 1: walk the archive (read-only) and store one row per frame / version.

Only headers are read (a few KB per file). Unchanged files (same size and
mtime) are skipped. Every file is handled on its own: an unreadable file is
stored with its error and never blocks the others.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select, update

from webapp import db
from webapp.models import Frame, IndexRun, Intermediate, Version

from . import nightlog, phd2, rules, sad, wbpp
from .headers import read_header

log = logging.getLogger("photonledger.scan")
COMMIT_EVERY = 200


def _prune(dirs: list[str], ignore: list[str]) -> None:
    dirs[:] = [d for d in dirs
               if not rules._SKIP_RE.match(d) and not (ignore and rules._matches_any(d, ignore))]


def scan(cfg: dict, run: IndexRun) -> None:
    archive = Path(cfg["ARCHIVE_PATH"])
    if not archive.is_dir():
        raise RuntimeError(f"Archiv nicht gefunden: {archive}")
    ignore = cfg.get("IGNORE_DIRS") or []
    cap = cfg["MAX_VERSIONS_PER_DIR"]
    offset = cfg["NIGHT_OFFSET_HOURS"]

    models = {"frame": Frame, "version": Version, "intermediate": Intermediate}
    known_rows = {k: {p: (i, s, m) for i, p, s, m in db.session.execute(
        select(model.id, model.path, model.size, model.mtime))} for k, model in models.items()}
    seen: dict[str, list[int]] = {k: [] for k in models}
    pending = 0

    def flush():
        nonlocal pending
        for kind, ids in seen.items():
            model = models[kind]
            for i in range(0, len(ids), 500):
                db.session.execute(update(model).where(model.id.in_(ids[i:i + 500])).values(seen_run=run.id))
            ids.clear()
        db.session.commit()
        pending = 0

    sad_dirs: list[str] = []
    phd_logs: list[str] = []
    night_logs: list[str] = []
    wbpp_logs: list[str] = []
    # folders that could not be read (SMB hiccup, permissions): nothing below them is deleted this run
    failed: set[str] = set()

    def rel_of(p: str) -> str:
        r = os.path.relpath(p, archive).replace(os.sep, "/")
        return "" if r == "." else r

    def on_walk_error(e: OSError):
        failed.add(rel_of(e.filename) if e.filename else "")
        log.warning("Ordner nicht lesbar: %s (%s)", e.filename, e.strerror)

    for dirpath, dirs, files in os.walk(archive, onerror=on_walk_error):
        rel_here = os.path.relpath(dirpath, archive).replace(os.sep, "/")
        for d in dirs:
            if sad.is_sad_dir(dirpath, d):
                sad_dirs.append(f"{rel_here}/{d}" if rel_here != "." else d)
        phd_logs += phd2.find_logs(dirpath, dirs, failed=failed, base=archive)
        night_logs += nightlog.find_logs(dirpath, dirs, failed=failed, base=archive)
        wbpp_logs += wbpp.find_logs(dirpath, dirs, failed=failed, base=archive)
        _prune(dirs, ignore)
        dirs.sort()
        rel_dir = os.path.relpath(dirpath, archive)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        items = []
        for name in sorted(files):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            c = rules.classify(rel, ignore)
            if c:
                items.append((rel, name, c))
        n_versions = sum(1 for _, _, c in items if c.kind == "version")
        for rel, name, c in items:
            if c.kind == "version" and n_versions > cap:
                continue                     # burst / registered sequences, not results
            known = known_rows[c.kind].get(rel)
            try:
                st = os.stat(os.path.join(dirpath, name))
            except OSError as e:
                # listed but not readable right now: keep what we know about it
                log.warning("Datei nicht lesbar: %s (%s)", rel, e.strerror)
                if known:
                    seen[c.kind].append(known[0])
                failed.add(rel)
                continue
            run.files_seen += 1
            if known and known[1] == st.st_size and abs(known[2] - st.st_mtime) < 1e-3:
                seen[c.kind].append(known[0])
                continue
            run.files_read += 1
            if c.kind == "frame":
                _store_frame(rel, Path(dirpath, name), st, c, run, offset, known)
            elif c.kind == "version":
                _store_version(rel, st, c, run, known)
            else:
                _store_intermediate(rel, st, c, run, known)
            pending += 1
            if pending >= COMMIT_EVERY:
                flush()
                log.info("… %d Dateien gesehen, %d gelesen", run.files_seen, run.files_read)
    flush()

    if run.files_seen == 0 and any(known_rows.values()):
        raise RuntimeError("Archiv ist leer oder nicht eingebunden – nichts gelöscht.")
    if "" in failed:
        raise RuntimeError("Archiv-Wurzel nicht lesbar – nichts gelöscht.")
    removed = 0
    for model in models.values():
        removed += db.session.execute(delete(model).where(model.seen_run != run.id, *keep(model.path, failed))).rowcount
    db.session.commit()
    if removed:
        log.info("%d nicht mehr vorhandene Dateien entfernt", removed)
    if failed:
        run.errors += len(failed)
        run.message = (f"{len(failed)} Ordner/Datei(en) nicht lesbar – darin nichts gelöscht: "
                       + ", ".join(sorted(failed)[:5]) + (" …" if len(failed) > 5 else ""))
        log.warning(run.message)
    sad.sync(cfg, run, sad_dirs, protect=failed)
    phd2.sync(cfg, run, phd_logs, protect=failed)
    nightlog.sync(cfg, run, night_logs, protect=failed)
    wbpp.sync(cfg, run, wbpp_logs, protect=failed)


def keep(column, failed: set[str]) -> list:
    """Conditions that spare rows below (or equal to) a path that could not be read in this run."""
    conds = []
    for f in failed:
        conds.append(column != f)
        conds.append(~column.startswith(f + "/", autoescape=True))
    return conds


def _store_intermediate(rel, st, c, run, known):
    row = db.session.get(Intermediate, known[0]) if known else Intermediate(path=rel)
    row.size, row.mtime, row.seen_run = st.st_size, st.st_mtime, run.id
    row.target_folder = c.target_folder
    db.session.add(row)


def _store_frame(rel, path, st, c, run, offset, known):
    row = db.session.get(Frame, known[0]) if known else Frame(path=rel)
    row.size, row.mtime, row.seen_run = st.st_size, st.st_mtime, run.id
    row.target_folder, row.session_root, row.rejected = c.target_folder, c.session_root, c.rejected
    row.error = None
    try:
        h, cards = read_header(path)
    except Exception as e:  # noqa: BLE001 - one bad file must not stop the scan
        run.errors += 1
        row.error = f"{type(e).__name__}: {e}"[:500]
        row.frame_type = c.frame_type
        db.session.add(row)
        return
    ftype = c.frame_type
    if ftype is None:
        ftype = rules.frame_type_from_header(h["imagetyp"])
        if ftype is None and path.suffix.lower() in rules.RAW_EXT:
            ftype = "light"
        if not rules.is_raw_frame(h):
            ftype = None                     # processed / stacked file: remembered, but ignored
    row.frame_type = ftype
    row.exptime = h["exptime"]
    row.date_obs = h["date_obs"]
    row.night = rules.night_of(h["date_obs"], offset)
    row.camera = h["camera"]
    row.telescope = rules.clean_telescope(h["telescope"], h["camera"])
    row.focal = h["focal"]
    row.filter = rules.clean_filter(h["filter"])
    row.gain, row.iso, row.temp = h["gain"], h["iso"], h["temp"]
    row.binning, row.width, row.height = h["binning"], h["width"], h["height"]
    row.ra, row.dec = h["ra"], h["dec"]
    row.object_name, row.program = h["object"], h["program"]
    row.header = cards
    db.session.add(row)


def _store_version(rel, st, c, run, known):
    row = db.session.get(Version, known[0]) if known else Version(path=rel)
    row.size, row.mtime, row.seen_run = st.st_size, st.st_mtime, run.id
    row.target_folder = c.target_folder
    row.thumb = row.preview = row.error = None       # (re)render
    db.session.add(row)


def now() -> datetime:
    return datetime.now().replace(microsecond=0)
