"""Session page and overview: ASIAIR / NINA night logs analysed with Airgus."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import abort, render_template
from sqlalchemy import select

from webapp import db
from webapp.archive import nightlog
from webapp.models import NightAnalysis, Session

from .routes import main

SEVERITY_DE = {"WARNING": ("Warnung", "warning"), "HIGH": ("Hoch", "danger"), "EMERGENCY": ("Notfall", "danger")}
LANE_DE = {"ASIAIR-Log": "ASIAIR", "NINA-Log": "NINA", "PHD2-Log": "PHD2-Log", "PHD2-Guiding": "Guiding",
           "Nachführung": "Nachführung", "iOptron-Log": "iOptron"}


def night_status() -> dict[str, dict]:
    """session root -> status dict for every analysed session (missing = no ASIAIR/NINA log)."""
    return {a.session_root: nightlog.status(a) for a in db.session.scalars(select(NightAnalysis))}


def _timeline(a: NightAnalysis, width=1100):
    tl = a.timeline or {}
    if not tl.get("start"):
        return None
    t0, t1 = datetime.fromisoformat(tl["start"]), datetime.fromisoformat(tl["end"])
    span = (t1 - t0).total_seconds() or 1
    L, R, row = 92, 10, 20

    def X(s):
        return round(L + (datetime.fromisoformat(s) - t0).total_seconds() / span * (width - L - R), 1)

    lanes = []
    for i, ln in enumerate(tl.get("lanes", [])):
        segs = []
        for s in ln["segments"]:
            x0, x1 = X(s[0]), X(s[1])
            segs.append(dict(x=x0, w=max(1.5, x1 - x0), off=len(s) > 2 and s[2] == "off",
                             t=f"{s[0][11:16]}–{s[1][11:16]} UTC"))
        lanes.append(dict(name=LANE_DE.get(ln["name"], ln["name"]), y=8 + i * row, segs=segs))
    y_exp = 8 + len(lanes) * row
    exps = [X(t) for t in tl.get("exposures", [])]
    y_alert = y_exp + row
    alerts = [dict(x=X(al["time"]), level=al["level"], title=al["title"], t=al["time"][11:16]) for al in a.alerts]
    stalls = [dict(x=X(s0), w=max(2, X(s1) - X(s0))) for s0, s1 in tl.get("stalls", [])]
    height = y_alert + row + 18
    ticks = []
    step = 3600 if span > 3 * 3600 else 1800 if span > 3600 else 600
    t = t0.replace(minute=0, second=0, microsecond=0)
    while t <= t1:
        if t >= t0:
            ticks.append(dict(x=X(t.isoformat()), label=t.strftime("%H:%M")))
        t = t + timedelta(seconds=step)
    return dict(W=width, H=height, L=L, row=row, lanes=lanes, y_exp=y_exp, exps=exps, y_alert=y_alert,
                alerts=alerts, stalls=stalls, ticks=ticks)


@main.route("/session/<int:sid>/night")
def session_night(sid: int):
    s = db.session.get(Session, sid) or abort(404)
    a = db.session.scalar(select(NightAnalysis).where(NightAnalysis.session_root == s.root)) or abort(404)
    return render_template("_night.html", a=a, g=_timeline(a), sev=SEVERITY_DE)
