"""Usage statistics of the equipment: rigs, cameras, optics and filters.

Counted are the usable lights (not rejected, no duplicates). The rig of a light comes from the rig
assignment (automatic, manual or per session); camera and optic are taken from the rig when there is
one, otherwise from the FITS header (camera name, TELESCOP or focal length).
"""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import select

from webapp import db
from webapp.models import Camera, Frame, Session

NO_RIG = "Ohne Rig"
NO_FILTER = "Ohne Filter"


def _row(name: str, detail: str | None = None) -> dict:
    return dict(name=name, detail=detail, seconds=0.0, lights=0, sessions=set(), nights=set(), targets=set(),
                first=None, last=None, years=defaultdict(float), unassigned=False, no_rig_s=0.0)


def _add(row: dict, f, sid, tid) -> None:
    row["seconds"] += f.exptime or 0
    row["lights"] += 1
    if sid:
        row["sessions"].add(sid)
    if tid:
        row["targets"].add(tid)
    if f.night:
        row["nights"].add(f.night)
        row["first"] = min(filter(None, [row["first"], f.night]))
        row["last"] = max(filter(None, [row["last"], f.night]))
        row["years"][f.night.year] += f.exptime or 0


def _finish(rows: dict) -> list[dict]:
    out = list(rows.values())
    total = sum(r["seconds"] for r in out) or 1
    for r in out:
        r["share"] = r["seconds"] / total
        if r["no_rig_s"] and r["no_rig_s"] >= r["seconds"] - 1e-6:
            r["unassigned"] = True                                  # never used in a rig
        r["n_sessions"], r["n_nights"], r["n_targets"] = len(r["sessions"]), len(r["nights"]), len(r["targets"])
        r["years"] = sorted(r["years"].items())
        del r["sessions"], r["nights"], r["targets"]
    out.sort(key=lambda r: (r["unassigned"], -r["seconds"]))
    return out


def usage_statistics(tol: float) -> dict[str, list[dict]]:
    from webapp.main.rigs import Resolver
    res = Resolver(tol)
    sessions = {s.root: (s.id, s.target_id) for s in db.session.scalars(select(Session))}
    rigs, cams, optics, filters = {}, {}, {}, {}
    # header camera name -> name of the camera on the rigs page (so one camera is one row)
    cam_name = {}
    for c in db.session.scalars(select(Camera)):
        cam_name[c.name] = c.name
        if c.header_name:
            cam_name[c.header_name] = c.name
    resolved: dict[tuple, object] = {}
    q = select(Frame).where(Frame.frame_type == "light", Frame.rejected.is_(False), Frame.duplicate.is_(False),
                            Frame.error.is_(None))
    for f in db.session.scalars(q):
        sid, tid = sessions.get(f.session_root, (None, None))
        key = (f.session_root, f.camera, f.focal_group)
        if key not in resolved:
            resolved[key] = res.resolve(f.session_root, f.camera, f.focal_group)[0]
        rig = resolved[key]
        if rig:
            r = rigs.setdefault(rig.id, _row(rig.name, " · ".join(
                filter(None, [rig.camera.name, rig.optic.name, rig.corrector.name if rig.corrector else None]))))
            c = cams.setdefault(rig.camera.name, _row(rig.camera.name))
            o = optics.setdefault(rig.optic.name, _row(rig.optic.name))
        else:
            r = rigs.setdefault(NO_RIG, _row(NO_RIG, "Aufnahmen ohne passendes Rig"))
            r["unassigned"] = True
            cname = cam_name.get(f.camera or "", f.camera or "Unbekannte Kamera")
            c = cams.setdefault(cname, _row(cname))
            c["no_rig_s"] += f.exptime or 0
            if f.telescope:
                oname, detail = f.telescope, "TELESCOP im Header, keinem Rig zugeordnet"
            elif f.focal_group:
                oname, detail = f"{f.focal_group} mm", "nur Brennweite bekannt, keinem Rig zugeordnet"
            else:
                oname, detail = "Unbekannte Optik", "weder Teleskop noch Brennweite im Header"
            o = optics.setdefault(oname, _row(oname, detail))
            o["no_rig_s"] += f.exptime or 0
        fl = filters.setdefault(f.filter or NO_FILTER, _row(f.filter or NO_FILTER))
        for row in (r, c, o, fl):
            _add(row, f, sid, tid)
    return dict(rigs=_finish(rigs), cameras=_finish(cams), optics=_finish(optics), filters=_finish(filters))


def years_title(years: list[tuple[int, float]]) -> str:
    return " · ".join(f"{y}: {s / 3600:.1f} h".replace(".", ",") for y, s in years)
