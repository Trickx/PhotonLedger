"""Downloads: single result files, the finals of a target and whole sessions as ZIP.

The ZIP is written while it is sent (no temporary file on the NAS), without compression: FITS and RAW
hardly shrink and compressing would only load the NAS CPU. Files keep their archive path inside the ZIP.
"""
from __future__ import annotations

import io
import os
import zipfile
from collections import defaultdict
from urllib.parse import quote

from flask import Response, abort, current_app, request, send_file
from sqlalchemy import select

from webapp import db
from webapp.archive import archives
from webapp.models import Frame, Intermediate, NightLog, PhdLog, SadSeries, Session, Target, Version, WbppRun

from .routes import main

CHUNK = 1024 * 1024
CATEGORIES = {
    "lights": "Lights",
    "calibration": "Flats, Darks, Bias",
    "logs": "Logs (ASIAIR, PHD2, WBPP)",
    "processing": "Processing und Finals",
    "sad": "StarAberrationDiagnostics (Karten, CSV)",
    "rejected": "Aussortierte Lights (REJECTED)",
}
DEFAULT = ("lights", "calibration", "logs", "processing")


class _Sink(io.RawIOBase):
    """Write end of the ZIP: collects the bytes until the generator hands them out."""

    def __init__(self):
        self.parts: list[bytes] = []
        self.pos = 0

    def writable(self):
        return True

    def write(self, b):
        self.parts.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def drain(self):
        out, self.parts = self.parts, []
        return out


def _zip_stream(files: list[tuple[str, str]]):
    """files: (absolute path, name in the ZIP). Unreadable files are skipped (listed in a note)."""
    sink = _Sink()
    missing = []
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for full, arc in files:
            try:
                info = zipfile.ZipInfo.from_file(full, arc)
                info.compress_type = zipfile.ZIP_STORED
                with open(full, "rb") as src, z.open(info, "w", force_zip64=True) as dst:
                    while chunk := src.read(CHUNK):
                        dst.write(chunk)
                        yield from sink.drain()
            except OSError:
                missing.append(arc)
            yield from sink.drain()
        if missing:
            z.writestr("FEHLENDE_DATEIEN.txt", "Nicht lesbar beim Herunterladen:\n" + "\n".join(missing))
    yield from sink.drain()


def _zip_response(files: list[tuple[str, str]], name: str) -> Response:
    if not files:
        abort(404, "Keine Dateien")
    disp = f"attachment; filename=\"{name.encode('ascii', 'replace').decode().replace('?', '_')}\"; " \
           f"filename*=UTF-8''{quote(name)}"
    return Response(_zip_stream(files), mimetype="application/zip",
                    headers={"Content-Disposition": disp, "X-Accel-Buffering": "no"})


def _under(path: str, root: str) -> bool:
    return path.startswith(root + "/")


def session_files(s: Session) -> dict[str, list[tuple[str, int]]]:
    """Files of a session per category: [(stored path, size)]. Files of other sessions below the same
    folder (session root = target folder) are left out."""
    cfg = current_app.config
    others = [r for r in db.session.scalars(select(Session.root)) if r != s.root and _under(r, s.root)]

    def mine(p: str) -> bool:
        return _under(p, s.root) and not any(_under(p, o) for o in others)

    out: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for f in db.session.scalars(select(Frame).where(Frame.session_root == s.root)):
        if f.frame_type == "light":
            out["rejected" if f.rejected else "lights"].append((f.path, f.size))
        elif f.frame_type in ("flat", "dark", "bias", "darkflat"):
            out["rejected" if f.rejected else "calibration"].append((f.path, f.size))
        else:
            out["processing"].append((f.path, f.size))          # stacks next to the raw data
    for model in (Intermediate, Version):
        for p, size in db.session.execute(select(model.path, model.size).where(model.path.startswith(s.root + "/"))):
            if mine(p):
                out["processing"].append((p, size))
    for model in (PhdLog, NightLog, WbppRun):
        for p, size in db.session.execute(select(model.path, model.size).where(model.path.startswith(s.root + "/"))):
            if mine(p):
                out["logs"].append((p, size))
    for (folder,) in db.session.execute(select(SadSeries.folder).where(SadSeries.session_root == s.root)):
        base = archives.resolve(cfg, folder)
        if base is None or not base.is_dir():
            continue
        for dirpath, _, names in os.walk(base):
            for n in names:
                full = os.path.join(dirpath, n)
                try:
                    out["sad"].append((archives.to_rel(cfg, full), os.path.getsize(full)))
                except (OSError, ValueError):
                    pass
    return {k: sorted(set(v)) for k, v in out.items()}


def session_summary(s: Session) -> list[dict]:
    files = session_files(s)
    return [dict(key=k, label=lbl, n=len(files.get(k, [])), size=sum(sz for _, sz in files.get(k, [])),
                 default=k in DEFAULT) for k, lbl in CATEGORIES.items() if files.get(k)]


def _entries(paths) -> list[tuple[str, str]]:
    cfg = current_app.config
    out = []
    for p in paths:
        full = archives.resolve(cfg, p)
        if full is not None:
            out.append((str(full), p))
    return out


@main.route("/session/<int:sid>/download.zip")
def download_session(sid: int):
    s = db.session.get(Session, sid) or abort(404)
    want = [k for k in request.args.getlist("k") if k in CATEGORIES] or list(DEFAULT)
    files = session_files(s)
    paths = sorted({p for k in want for p, _ in files.get(k, [])})
    t = s.target
    name = f"{t.name if t else 'Session'} {s.date.isoformat() if s.date else s.label or 'Session'}.zip"
    return _zip_response(_entries(paths), name)


def target_finals(t: Target) -> list[tuple[str, int]]:
    """Results of a target: its versions (JPG/PNG/TIFF) and every file in a Finals folder (any format)."""
    folders = t.folders or []
    out = {(v.path, v.size) for v in t.versions}
    for p, size in db.session.execute(select(Intermediate.path, Intermediate.size)
                                      .where(Intermediate.target_folder.in_(folders))):
        if "/Finals/" in f"/{p}":
            out.add((p, size))
    return sorted(out)


@main.route("/target/<int:tid>/finals.zip")
def download_finals(tid: int):
    t = db.session.get(Target, tid) or abort(404)
    return _zip_response(_entries(p for p, _ in target_finals(t)), f"{t.name} Finals.zip")


@main.route("/version/<int:vid>/file")
def download_version(vid: int):
    v = db.session.get(Version, vid) or abort(404)
    full = archives.resolve(current_app.config, v.path)
    if full is None or not full.is_file():
        abort(404)
    return send_file(full, as_attachment=True, download_name=v.path.rsplit("/", 1)[-1])
