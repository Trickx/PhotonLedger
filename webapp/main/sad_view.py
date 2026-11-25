"""Session page: StarAberrationDiagnostics results (summary, charts, maps, AI review)."""
from __future__ import annotations

import os

from datetime import timedelta
from pathlib import Path

from flask import abort, current_app, render_template, request, send_file
from PIL import Image
from sqlalchemy import select

from webapp import db
from webapp.models import Issue, SadFrame, SadSeries, Session

from webapp.archive.sad import _med, map_cache_name, summary

from .routes import main

SAD_PROBLEMS = ("sad_unreadable", "sad_tilt", "sad_edge", "sad_tracking")     # red dot
SAD_NOTES = ("sad_outliers", "sad_frame_errors")


def _sad_dot(s, blocks) -> dict:
    if not blocks:
        return dict(status="none", reasons=[], notes=[])
    return sad_status({s.root: blocks[0]["summary"]}).get(s.root)


def sad_status(overview: dict[str, dict]) -> dict[str, dict]:
    """session root -> dot status for SAD (missing = no SAD results): ok, or error when the checks found
    tilt, edge, tracking or unreadable results; outliers are notes only."""
    issues: dict[str, list] = {}
    for i in db.session.scalars(select(Issue).where(Issue.code.in_(SAD_PROBLEMS + SAD_NOTES))):
        if i.session_root:
            issues.setdefault(i.session_root, []).append(i)
    out = {}
    for root, sm in overview.items():
        mine = issues.get(root, [])
        reasons = [i.message for i in mine if i.code in SAD_PROBLEMS]
        notes = [i.message for i in mine if i.code in SAD_NOTES]
        out[root] = dict(status="error" if reasons else "ok", reasons=reasons, notes=notes, summary=sm)
    return out


MAP_LABELS = {"Mosaic": "Mosaik", "Size": "Sterngröße", "Shape": "Sternform", "Coma": "Koma",
              "Tilt": "Verkippung", "Aberration3D": "3D"}
SIDE_COLORS = {"West": "#e0a43a", "East": "#5aa9ff"}


def _chart(frames, key, label, unit, fmt, outliers, scale=None):
    pts = [(f, getattr(f, key)) for f in frames if getattr(f, key) is not None and f.time_utc]
    if len(pts) < 2:
        return None
    vals = [v * scale if scale else v for _, v in pts]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.1 or abs(hi) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    t0, t1 = pts[0][0].time_utc, pts[-1][0].time_utc
    span = (t1 - t0).total_seconds() or 1
    W, H, L, R, T, B = 760, 150, 46, 10, 10, 22
    out = []
    for (f, _), v in zip(pts, vals):
        x = L + (f.time_utc - t0).total_seconds() / span * (W - L - R)
        y = T + (hi - v) / (hi - lo) * (H - T - B)
        out.append(dict(x=round(x, 1), y=round(y, 1), id=f.id, v=fmt.format(v).replace(".", ","),
                        side=f.side, color=SIDE_COLORS.get(f.side, "#9aa4b2"), outlier=f in outliers,
                        t=f.time_utc.strftime("%H:%M"), name=f.name))
    ticks = [lo + (hi - lo) * i / 3 for i in range(4)]
    yt = [dict(y=round(T + (hi - v) / (hi - lo) * (H - T - B), 1), label=fmt.format(v).replace(".", ",")) for v in ticks]
    xt = []
    for i in range(5):
        x = L + i / 4 * (W - L - R)
        xt.append(dict(x=round(x, 1), label=(t0 + timedelta(seconds=span * i / 4)).strftime("%H:%M")))
    return dict(key=key, label=label, unit=unit, points=out, yticks=yt, xticks=xt, W=W, H=H, L=L, T=T, B=B,
                median=fmt.format(_med(vals)).replace(".", ","))


def charts(series: SadSeries, outliers) -> list[dict]:
    fs = [f for f in series.frames if not f.error]
    scale = series.arcsec_per_px
    specs = [
        ("fwhm_center", "FWHM Bildmitte", "″" if scale else "px", "{:.1f}" if scale else "{:.2f}", scale),
        ("fwhm_edge", "FWHM Bildrand", "″" if scale else "px", "{:.1f}" if scale else "{:.2f}", scale),
        ("ellipticity", "Elliptizität (Nachführung)", "", "{:.2f}", None),
        ("tilt_rel", "Verkippung (relativ)", "", "{:.2f}", None),
        ("altitude", "Höhe", "°", "{:.0f}", None),
        ("stars", "Sterne", "", "{:.0f}", None),
    ]
    return [c for c in (_chart(fs, k, lbl, u, fmt, outliers, sc) for k, lbl, u, fmt, sc in specs) if c]


@main.route("/session/<int:sid>")
def session_page(sid: int):
    s = db.session.get(Session, sid) or abort(404)
    series = db.session.scalars(select(SadSeries).where(SadSeries.session_root == s.root)).all()
    blocks = []
    for sr in series:
        sm = summary(sr, current_app.config.get("SAD_OUTLIER_FACTOR", 1.3))
        sel = request.args.get("f", type=int)
        frame = next((f for f in sr.frames if f.id == sel), None) or _default_frame(sr)
        blocks.append(dict(series=sr, summary=sm, charts=charts(sr, sm["outliers"]), frame=frame,
                           kind=request.args.get("k", "Mosaic")))
    from webapp.main.rigs import Resolver
    res = Resolver(current_app.config["FOCAL_TOLERANCE"])
    rig_rows = [dict(d=d, rig=r, how=how) for d in s.datasets or []
                for r, how in [res.resolve(s.root, d["camera"], d["focal"])]]
    from webapp.main.phd_view import guiding_summary
    from webapp.main.wbpp_view import session_runs
    return render_template("session.html", s=s, t=s.target, archive_path=current_app.config["ARCHIVE_PATH"],
                           wbpp=session_runs(s), sad_status=_sad_dot(s, blocks), blocks=blocks, map_labels=MAP_LABELS,
                           side_colors=SIDE_COLORS, rig_rows=rig_rows, all_rigs=res.rigs,
                           guiding=guiding_summary(s), night=_night(s))


def _night(s):
    from webapp.archive.nightlog import status
    from webapp.models import NightAnalysis
    a = db.session.scalar(select(NightAnalysis).where(NightAnalysis.session_root == s.root))
    return dict(a=a, st=status(a)) if a else None


def _default_frame(sr: SadSeries):
    fs = [f for f in sr.frames if f.maps and not f.error]
    if not fs:
        return None
    c = _med(f.fwhm_center for f in fs)
    return min(fs, key=lambda f: abs((f.fwhm_center or 0) - (c or 0)))    # a typical light


@main.route("/session/<int:sid>/sad-map")
def sad_viewer(sid: int):
    f = db.session.get(SadFrame, request.args.get("f", type=int)) or abort(404)
    return render_template("_sad_viewer.html", sid=sid, frame=f, series=f.series,
                           kind=request.args.get("k", "Mosaic"), map_labels=MAP_LABELS)


@main.route("/sad/map/<int:fid>/<kind>")
def sad_map(fid: int, kind: str):
    """SAD map as JPEG; rendered once from the archive PNG (several MB) into DATA_PATH."""
    f = db.session.get(SadFrame, fid) or abort(404)
    rel = (f.maps or {}).get(kind) or abort(404)
    big = request.args.get("size") == "full"
    data = Path(current_app.config["DATA_PATH"])
    out = data / map_cache_name(rel, big)
    src = Path(current_app.config["ARCHIVE_PATH"]) / rel
    if not out.is_file() or out.stat().st_mtime < src.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((2400, 2400) if big else (1100, 1100), Image.LANCZOS)
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=88)
            os.replace(tmp, out)
    return send_file(out, mimetype="image/jpeg", max_age=86400)
