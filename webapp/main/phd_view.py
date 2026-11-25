"""Session page: PHD2 guiding (summary + graph, loaded when the section is opened)."""
from __future__ import annotations

import math
import statistics
from datetime import timedelta

from flask import abort, current_app, render_template, request
from sqlalchemy import select

from webapp import db
from webapp.archive.phd2 import sections_for
from webapp.models import Frame, Session

from .routes import main

MIN_FRAMES = 60          # shorter sections (slews, star lost, calibration checks) are not counted in the summary


def session_window(s: Session):
    """UTC time span of the session's lights (start of the first to end of the last exposure)."""
    lights = db.session.scalars(select(Frame).where(Frame.session_root == s.root, Frame.frame_type == "light",
                                                    Frame.date_obs.is_not(None))).all()
    if not lights:
        return None, None, []
    lo = min(f.date_obs for f in lights)
    hi = max(f.date_obs + timedelta(seconds=f.exptime or 0) for f in lights)
    return lo, hi, lights


def guiding_summary(s: Session) -> dict | None:
    lo, hi, _ = session_window(s)
    if not lo:
        return None
    secs = sections_for(lo, hi)
    if not secs:
        return None
    good = [x for x in secs if x.frames >= MIN_FRAMES and x.rms_total is not None]
    n = sum(x.frames for x in good) or 1

    def wmean(attr):
        vals = [(getattr(x, attr), x.frames) for x in good if getattr(x, attr) is not None]
        return math.sqrt(sum(v * v * w for v, w in vals) / sum(w for _, w in vals)) if vals else None

    return dict(
        sections=secs, good=good, frames=sum(x.frames for x in secs),
        rms_ra=wmean("rms_ra"), rms_dec=wmean("rms_dec"), rms_total=wmean("rms_total"),
        dithers=sum(x.dithers for x in secs), settle_failed=sum(x.settle_failed for x in secs),
        dropped=sum(x.dropped for x in secs),
        duration=sum(x.duration_s for x in good), weight=n,
        scale=statistics.median([x.pixel_scale for x in secs if x.pixel_scale] or [0]) or None,
        camera=next((x.camera for x in secs if x.camera), None),
        exposure=statistics.median([x.exposure_ms for x in secs if x.exposure_ms] or [0]) or None,
        logs=sorted({x.log.path for x in secs}),
    )


def guiding_status() -> dict[str, dict]:
    """session root -> {status: ok | none | error, reasons, rms} for the session overview.

    ok     a PHD2 log covers the session and shows no problems
    none   no PHD2 log for the session
    error  log not readable, settling failed after a dither, or the guide star was lost
    """
    from sqlalchemy import func

    from webapp.models import PhdLog, PhdSection

    windows: dict[str, list] = {}
    for root, start, last, exp in db.session.execute(
            select(Frame.session_root, func.min(Frame.date_obs), func.max(Frame.date_obs), func.max(Frame.exptime))
            .where(Frame.frame_type == "light", Frame.date_obs.is_not(None)).group_by(Frame.session_root)):
        windows[root] = [start - timedelta(minutes=30), last + timedelta(seconds=exp or 0) + timedelta(minutes=30)]
    cfg = current_app.config
    lost_max = cfg.get("PHD_LOST_RATIO", 0.01)          # > 1 % frames without star
    settle_max = cfg.get("PHD_SETTLE_FAILED", 3)        # failed settlings (absolute) …
    settle_ratio = cfg.get("PHD_SETTLE_RATIO", 0.10)    # … or share of dithers
    rms_max = cfg.get("PHD_RMS_ERROR", 5.0)             # arcsec: guiding broken in a longer section
    rows = db.session.execute(select(
        PhdSection.start_utc, PhdSection.end_utc, PhdSection.frames, PhdSection.dropped, PhdSection.settle_failed,
        PhdSection.dithers, PhdSection.rms_total, PhdLog.path).join(PhdLog)).all()
    secs: dict[tuple, dict] = {}                        # copies of a log -> one section, all paths
    for r in rows:
        if not r.end_utc:
            continue
        s = secs.setdefault((r.start_utc, r.frames), dict(row=r, dirs=set()))
        s["dirs"].add(r.path.rsplit("/", 2)[0])          # folder that holds the log directory
    out: dict[str, dict] = {}
    for root, (lo, hi) in windows.items():
        hit = []
        for s in secs.values():
            r = s["row"]
            if not (r.start_utc <= hi and r.end_utc >= lo):
                continue
            # simultaneous sessions of the same target (e.g. DWARF + telescope): the log belongs to the
            # session whose folder holds it
            rivals = [o for o, (lo2, hi2) in windows.items() if o != root and o.rsplit("/", 1)[0] == root.rsplit("/", 1)[0]
                      and r.start_utc <= hi2 and r.end_utc >= lo2]
            if rivals and root not in s["dirs"] and any(o in s["dirs"] for o in rivals):
                continue
            hit.append(r)
        if not hit:
            out[root] = dict(status="none", reasons=["kein PHD2-Log zu dieser Session"], notes=[], rms=None)
            continue
        good = [r for r in hit if r.frames >= MIN_FRAMES and r.rms_total is not None]
        calm = [r for r in good if r.rms_total <= rms_max]          # broken sections are reported, not averaged
        w = sum(r.frames for r in calm)
        rms = math.sqrt(sum(r.rms_total ** 2 * r.frames for r in calm) / w) if w else None
        frames = sum(r.frames for r in good) or 1
        failed, dithers = sum(r.settle_failed for r in hit), sum(r.dithers for r in hit)
        dropped = sum(r.dropped for r in good)
        reasons, notes = [], []
        msg_lost = f"{dropped} Guiding-Frames ohne Stern ({dropped / frames:.1%})".replace(".", ",")
        (reasons if dropped / frames > lost_max else notes if dropped else []).append(msg_lost)
        msg_settle = f"{failed}× Settling nach Dither fehlgeschlagen (von {dithers} Dithern)"
        (reasons if failed >= settle_max or (dithers and failed / dithers > settle_ratio) else
         notes if failed else []).append(msg_settle)
        broken = [r for r in good if r.rms_total > rms_max]
        if broken:
            reasons.append(f"{len(broken)} Abschnitt(e) mit mehr als {rms_max:g}″ RMS – Guiding gestört")
        out[root] = dict(status="error" if reasons else "ok", reasons=reasons, notes=notes, rms=rms)
    # logs that could not be read: mark the sessions in the folder they belong to
    for path, err in db.session.execute(select(PhdLog.path, PhdLog.error).where(PhdLog.error.is_not(None))):
        owner = path.rsplit("/", 2)[0]                       # …/<session or target>/<log dir>/<file>
        for root in windows:
            if root == owner or root.startswith(owner + "/"):
                st = out.setdefault(root, dict(status="error", reasons=[], notes=[], rms=None))
                if st["status"] == "none":
                    st["reasons"] = []
                st["status"] = "error"
                st["reasons"].append(f"Log nicht lesbar: {path.rsplit('/', 1)[-1]} ({err})")
    return out


def _graph(secs, lights, lo, hi, width=1100, height=300):
    """RA/Dec error over time as SVG polylines (min/max per pixel column), with dithers and light exposures."""
    t0 = min([lo] + [x.start_utc for x in secs])
    t1 = max([hi] + [x.end_utc or x.start_utc for x in secs])
    lights = [f for f in lights if t0 <= f.date_obs <= t1]
    span = (t1 - t0).total_seconds() or 1
    L, R, T, B = 44, 10, 10, 46
    plot_h = height - T - B
    good = [x for x in secs if x.frames >= MIN_FRAMES and x.rms_total]
    lim = max(2.0, 3.5 * statistics.median([x.rms_total for x in good] or [1.0]))
    lim = math.ceil(lim)

    def X(sec):
        return L + sec / span * (width - L - R)

    def Y(v):
        v = max(-lim, min(lim, v))
        return T + (lim - v) / (2 * lim) * plot_h

    lines = {"ra": [], "dec": []}
    dithers = []
    for x in secs:
        off = (x.start_utc - t0).total_seconds()
        cols: dict[int, list] = {}
        for t, ra, dec, settling in x.series:
            px = int(X(off + t))
            c = cols.setdefault(px, [ra, ra, dec, dec])
            c[0], c[1] = min(c[0], ra), max(c[1], ra)
            c[2], c[3] = min(c[2], dec), max(c[3], dec)
        for key, i in (("ra", 0), ("dec", 2)):
            pts = []
            for px in sorted(cols):
                c = cols[px]
                pts.append(f"{px},{Y(c[i]):.1f}")
                if c[i] != c[i + 1]:
                    pts.append(f"{px},{Y(c[i + 1]):.1f}")
            if pts:
                lines[key].append(" ".join(pts))
        for t, kind in x.events:
            if kind == "dither":
                dithers.append(round(X(off + t), 1))
    exps = []
    for f in lights:
        a = (f.date_obs - t0).total_seconds()
        exps.append(dict(x=round(X(a), 1), w=max(1.0, round((f.exptime or 0) / span * (width - L - R), 1)),
                         rejected=f.rejected or f.duplicate, name=f.path.rsplit("/", 1)[-1],
                         t=f.date_obs.strftime("%H:%M:%S")))
    yt = [dict(y=round(Y(v), 1), label=f"{v:+g}″".replace("+0″", "0″")) for v in (lim, lim / 2, 0, -lim / 2, -lim)]
    xt = []
    step = 3600 if span > 3 * 3600 else 1800 if span > 3600 else 600 if span > 1200 else 120
    first = t0.replace(minute=0, second=0, microsecond=0)
    t = first
    while t <= t1:
        if t >= t0:
            xt.append(dict(x=round(X((t - t0).total_seconds()), 1), label=t.strftime("%H:%M")))
        t += timedelta(seconds=step)
    return dict(W=width, H=height, L=L, R=R, T=T, B=B, plot_h=plot_h, lim=lim, lines=lines, dithers=dithers,
                exps=exps, yticks=yt, xticks=xt, t0=t0, t1=t1, ybar=height - B + 14)


@main.route("/session/<int:sid>/guiding")
def session_guiding(sid: int):
    s = db.session.get(Session, sid) or abort(404)
    lo, hi, lights = session_window(s)
    if not lo:
        abort(404)
    secs = sections_for(lo, hi)
    sec_id = request.args.get("sec", type=int)
    one = [x for x in secs if x.id == sec_id] if sec_id else []
    if one:
        # zoom: time axis = this section, only the lights exposed during it
        secs = one
        lo, hi = one[0].start_utc, one[0].end_utc or one[0].start_utc
        lights = [f for f in lights if f.date_obs <= hi and f.date_obs + timedelta(seconds=f.exptime or 0) >= lo]
    return render_template("_phd_graph.html", g=_graph(secs, lights, lo, hi), secs=secs, s=s,
                           summary=guiding_summary(s), sec_id=sec_id)
