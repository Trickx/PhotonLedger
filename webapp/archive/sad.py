"""Read the results of StarAberrationDiagnostics (SAD) from <Lights>/SAD/ folders.

Files written by SAD (only read here, never changed):
  StarAberrationSeries.csv   one row per light (FWHM, tilt, tracking, coma, altitude …)
  StarAberrationSeries.json  the same in detail, plus debayer mode, optics and per-side summary (~20 MB)
  <stem>_<Kind>.png          maps per light: Size, Shape, Coma, Tilt, Mosaic, Aberration3D
  StarAberrationAI.json      AI reviews of the series
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import statistics
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from webapp import db
from webapp.models import Frame, IndexRun, SadFrame, SadSeries

from . import rules
from webapp.archive import archives

log = logging.getLogger("photonledger.sad")

SAD_DIR = "SAD"
SERIES_CSV = "StarAberrationSeries.csv"
SERIES_JSON = "StarAberrationSeries.json"
AI_JSON = "StarAberrationAI.json"
MAP_KINDS = ["Mosaic", "Size", "Shape", "Coma", "Tilt", "Aberration3D"]


def map_cache_name(rel: str, big: bool) -> str:
    """Relative path in DATA_PATH of the JPEG rendered from a SAD map PNG."""
    import hashlib
    h = hashlib.sha1(f"{rel}|{big}".encode()).hexdigest()[:24]
    return f"sad/{h[:2]}/{h}.jpg"


def is_sad_dir(dirpath: str, name: str) -> bool:
    if name != SAD_DIR:
        return False
    d = os.path.join(dirpath, name)
    return os.path.isfile(os.path.join(d, SERIES_CSV)) or os.path.isfile(os.path.join(d, SERIES_JSON))


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _t(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _mtime(p: Path) -> float | None:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def sync(cfg: dict, run: IndexRun, sad_dirs: list[str], protect: set[str] | None = None) -> None:
    """Update SadSeries/SadFrame for the given SAD folders (relative paths); remove vanished ones."""
    existing = {s.folder: s for s in db.session.scalars(select(SadSeries))}
    for rel in sad_dirs:
        d = archives.resolve(cfg, rel)
        s = existing.get(rel) or SadSeries(folder=rel)
        s.seen_run = run.id
        lights_dir = rel.rsplit("/", 1)[0]
        last = lights_dir.rsplit("/", 1)[-1]
        s.session_root = lights_dir.rsplit("/", 1)[0] if last.lower() in rules.FRAME_DIRS else lights_dir
        csv_m, json_m, ai_m = _mtime(d / SERIES_CSV), _mtime(d / SERIES_JSON), _mtime(d / AI_JSON)
        changed = (csv_m, json_m) != (s.csv_mtime, s.json_mtime)
        try:
            if changed or s.id is None:
                _read_series(s, d, lights_dir)
                s.csv_mtime, s.json_mtime = csv_m, json_m
                log.info("SAD-Serie gelesen: %s (%d Lights)", rel, len(s.frames))
            if ai_m != s.ai_mtime:
                s.ai_reviews = _read_ai(d / AI_JSON) if ai_m else []
                s.ai_mtime = ai_m
            s.error = None
        except Exception as e:  # noqa: BLE001 - a broken SAD folder must not stop indexing
            s.error = f"{type(e).__name__}: {e}"[:500]
            run.errors += 1
            log.warning("SAD-Ordner %s: %s", rel, s.error)
        db.session.add(s)
        db.session.commit()
    from .scanner import keep
    db.session.execute(delete(SadSeries).where(SadSeries.seen_run != run.id, *keep(SadSeries.folder, protect or set())))
    db.session.commit()
    link_frames()


def _read_series(s: SadSeries, d: Path, lights_dir: str) -> None:
    rows: dict[str, dict] = {}
    if (d / SERIES_CSV).is_file():
        text = (d / SERIES_CSV).read_text(encoding="utf-8-sig")
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("File"):
                rows[r["File"]] = r
    files = set(os.listdir(d))
    meta: dict = {}
    if (d / SERIES_JSON).is_file():
        with open(d / SERIES_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
        s.created, s.script = data.get("created"), data.get("script")
        s.static = [_static(x) for x in data.get("static") or []]
        for fr in data.get("frames") or []:
            m = fr.get("metrics") or {}
            sip = fr.get("sip") or {}
            if not meta and m.get("w") and sip.get("file_w"):
                meta = dict(factor=round(sip["file_w"] / m["w"]), pitch=fr.get("pitch_um"), focal=fr.get("focal_mm"))
    if meta:
        s.pixel_factor = float(meta["factor"] or 1)
        s.pitch_um, s.focal_mm = meta["pitch"], meta["focal"]

    s.frames.clear()
    db.session.flush()
    for name, r in rows.items():
        stem = name.rsplit(".", 1)[0]
        maps = {}
        for kind in MAP_KINDS:
            fn = f"{stem}_{kind}.png"
            if fn in files:
                maps[kind] = f"{lights_dir}/{SAD_DIR}/{fn}"
        s.frames.append(SadFrame(
            name=name, side=r.get("Side") or None, time_utc=_t(r.get("TimeUTC")),
            hour_angle=_f(r.get("HourAngle_h")), altitude=_f(r.get("Altitude_deg")),
            focus=_f(r.get("FocusPos")), temp=_f(r.get("Temp_C")),
            stars=int(_f(r.get("Stars")) or 0) or None,
            fwhm_center=_f(r.get("FWHMCenter_px")), fwhm_edge=_f(r.get("FWHMEdge_px")),
            tilt_rel=_f(r.get("TiltRel")), tilt_dir=_f(r.get("TiltDir_deg")),
            ellipticity=_f(r.get("TrackingEllipticity")), tracking_angle=_f(r.get("TrackingAngle_deg")),
            coma_k=_f(r.get("ComaK")), error=r.get("Error") or None, maps=maps,
        ))


def _static(x: dict) -> dict:
    t = x.get("tracking") or {}
    return dict(label=x.get("label"), frames=len(x.get("frames") or []),
                ellipticity=t.get("ellipticity"), angle=t.get("angle_deg"))


def _read_ai(p: Path) -> list[dict]:
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for r in data.get("reviews") or []:
        rv = (r.get("review") or {}).get("data") or {}
        out.append(dict(
            created=r.get("created"), model=r.get("model"), frames=len(r.get("series") or []),
            labels=rv.get("labels") or {}, verdict=rv.get("verdict"), order=rv.get("order") or [],
            findings=[{k: f.get(k) for k in ("topic", "status", "confidence", "confidenceText", "observation",
                                             "appearance", "reasoning", "alternatives", "action", "check")}
                      for f in rv.get("findings") or []],
            text=None if rv else (r.get("review") or {}).get("text"),
        ))
    out.sort(key=lambda r: r["created"] or "", reverse=True)
    return out[:10]


def link_frames() -> None:
    """Connect SAD rows to the indexed lights (by file name within the session)."""
    series = db.session.scalars(select(SadSeries)).all()
    for s in series:
        lights = db.session.scalars(select(Frame).where(Frame.session_root == s.session_root,
                                                        Frame.frame_type == "light")).all()
        by_name = {f.path.rsplit("/", 1)[-1]: f.id for f in lights}
        for sf in s.frames:
            sf.frame_id = by_name.get(sf.name)
    db.session.commit()


def _med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def summary(series: SadSeries, outlier_factor: float = 1.3) -> dict:
    fs = [f for f in series.frames if not f.error]
    scale = series.arcsec_per_px
    c, e = _med(f.fwhm_center for f in fs), _med(f.fwhm_edge for f in fs)
    outliers = [f for f in fs if c and f.fwhm_center and f.fwhm_center > c * outlier_factor]
    return dict(
        frames=len(series.frames), errors=sum(1 for f in series.frames if f.error),
        fwhm_center=c, fwhm_edge=e, ratio=(e / c) if c and e else None,
        fwhm_center_arcsec=c * scale if c and scale else None,
        fwhm_edge_arcsec=e * scale if e and scale else None,
        tilt=_med(f.tilt_rel for f in fs), tilt_dir=_med(f.tilt_dir for f in fs),
        ellipticity=_med(f.ellipticity for f in fs), stars=_med(f.stars for f in fs),
        sides=sorted({f.side for f in fs if f.side}), outliers=outliers, scale=scale,
    )


