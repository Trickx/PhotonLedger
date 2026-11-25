"""Rigs: components (camera, optic, corrector), rigs, assignment to data sets, statistics."""
from __future__ import annotations

import statistics
from collections import defaultdict

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from webapp import db
from webapp.models import Camera, Corrector, Frame, Optic, Rig, RigMatch, RigSessionOverride, SadSeries, Session

from .routes import main

OPTIC_KINDS = {"telescope": "Teleskop", "prime": "Festbrennweite", "zoom": "Zoomobjektiv"}
CORRECTOR_KINDS = {"flattener": "Flattener / Komakorrektor", "reducer": "Reducer", "extender": "Extender / Barlow"}


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


# --------------------------------------------------------------------------- matching

def candidates(rigs: list[Rig], camera: str | None, focal: float | None, tol: float) -> list[Rig]:
    """Rigs whose camera matches the header name and whose (effective) focal length fits."""
    out = []
    for r in rigs:
        names = {_norm(r.camera.header_name), _norm(r.camera.name)} - {""}
        if _norm(camera) not in names or not focal:
            continue
        lo, hi = r.focal_range
        if lo * (1 - tol) <= focal <= hi * (1 + tol):
            out.append(r)
    return out


class Resolver:
    """Which rig took a data set of a session: override > manual assignment > automatic match."""

    def __init__(self, tol: float):
        self.rigs = db.session.scalars(select(Rig)).all()
        self.by_id = {r.id: r for r in self.rigs}
        self.manual = {(m.camera, m.focal): m.rig_id for m in db.session.scalars(select(RigMatch))}
        self.override = {o.session_root: o.rig_id for o in db.session.scalars(select(RigSessionOverride))}
        self.tol = tol

    def resolve(self, session_root: str, camera: str | None, focal: int | None) -> tuple[Rig | None, str]:
        if session_root in self.override:
            return self.by_id.get(self.override[session_root]), "session"
        if (camera, focal) in self.manual:
            return self.by_id.get(self.manual[(camera, focal)]), "manual"
        c = candidates(self.rigs, camera, focal, self.tol)
        if len(c) > 1:
            c = [r for r in c if r.active] or c
        if len(c) == 1:
            return c[0], "auto"
        return None, "ambiguous" if c else "none"

    def suggestions(self, camera, focal):
        return candidates(self.rigs, camera, focal, self.tol)


def rig_statistics(tol: float) -> tuple[list[dict], list[dict]]:
    """Per rig: hours, lights, sessions, targets, period, years, filters, median FWHM (SAD).
    Also returns the data sets that no rig covers."""
    from webapp.archive.sad import summary

    res = Resolver(tol)
    sad = {s.session_root: summary(s) for s in db.session.scalars(select(SadSeries))}
    stats = {r.id: dict(rig=r, seconds=0.0, lights=0, sessions=set(), targets=set(), first=None, last=None,
                        years=defaultdict(float), filters=defaultdict(float), focals=set(), fwhm=[], how=set())
             for r in res.rigs}
    unassigned: dict[tuple, dict] = {}
    for s in db.session.scalars(select(Session).where(Session.lights > 0)):
        for d in s.datasets or []:
            rig, how = res.resolve(s.root, d["camera"], d["focal"])
            if not rig:
                u = unassigned.setdefault((d["camera"], d["focal"]), dict(
                    camera=d["camera"], focal=d["focal"], telescope=d["telescope"], seconds=0.0, lights=0,
                    sessions=0, how=how, suggestions=res.suggestions(d["camera"], d["focal"])))
                u["seconds"] += d["seconds"]
                u["lights"] += d["lights"]
                u["sessions"] += 1
                continue
            st = stats[rig.id]
            st["seconds"] += d["seconds"]
            st["lights"] += d["lights"]
            st["sessions"].add(s.id)
            st["targets"].add(s.target_id)
            st["how"].add(how)
            if d["focal"]:
                st["focals"].add(d["focal"])
            if s.date:
                st["first"] = min(filter(None, [st["first"], s.date]))
                st["last"] = max(filter(None, [st["last"], s.date]))
                st["years"][s.date.year] += d["seconds"]
            share = d["seconds"] / s.exposure_s if s.exposure_s else 0
            for f, sec in (s.filters or {}).items():
                st["filters"][f] += sec * share
            sm = sad.get(s.root)
            if sm and sm.get("fwhm_center_arcsec"):
                st["fwhm"].append(sm["fwhm_center_arcsec"])
    out = []
    for st in stats.values():
        st["n_sessions"], st["n_targets"] = len(st["sessions"]), len(st["targets"])
        st["years"] = sorted(st["years"].items())
        st["filters"] = sorted(st["filters"].items(), key=lambda kv: -kv[1])
        st["fwhm"] = statistics.median(st["fwhm"]) if st["fwhm"] else None
        st["focals"] = sorted(st["focals"])
        out.append(st)
    out.sort(key=lambda st: (not st["rig"].active, -st["seconds"]))
    return out, sorted(unassigned.values(), key=lambda u: -u["seconds"])


# --------------------------------------------------------------------------- pages

@main.route("/rigs")
def rigs_page():
    edit = {k: request.args.get(k, type=int) for k in ("camera", "optic", "corrector", "rig")}
    cams = db.session.scalars(select(Camera).order_by(Camera.name)).all()
    optics = db.session.scalars(select(Optic).order_by(Optic.focal_mm)).all()
    corrs = db.session.scalars(select(Corrector).order_by(Corrector.name)).all()
    rigs = db.session.scalars(select(Rig).order_by(Rig.active.desc(), Rig.name)).all()
    header_cams = _header_cameras()
    known = {_norm(c.header_name) for c in cams} | {_norm(c.name) for c in cams}
    return render_template(
        "rigs.html", cameras=cams, optics=optics, correctors=corrs, rigs=rigs, edit=edit,
        cur=dict(camera=db.session.get(Camera, edit["camera"]) if edit["camera"] else None,
                 optic=db.session.get(Optic, edit["optic"]) if edit["optic"] else None,
                 corrector=db.session.get(Corrector, edit["corrector"]) if edit["corrector"] else None,
                 rig=db.session.get(Rig, edit["rig"]) if edit["rig"] else None),
        new_header_cams=[h for h in header_cams if _norm(h["name"]) not in known],
        optic_kinds=OPTIC_KINDS, corrector_kinds=CORRECTOR_KINDS, seeing=float(request.args.get("seeing", 2.5)))


def _header_cameras() -> list[dict]:
    """Cameras seen in the lights, with pixel size and image size from the header."""
    rows = db.session.execute(
        select(Frame.camera, func.json_extract(Frame.header, "$.XPIXSZ"), Frame.width, Frame.height, func.count())
        .where(Frame.frame_type == "light", Frame.camera.is_not(None))
        .group_by(Frame.camera, func.json_extract(Frame.header, "$.XPIXSZ"), Frame.width, Frame.height)).all()
    best: dict[str, dict] = {}
    for cam, pix, w, h, n in rows:
        b = best.get(cam)
        if not b or n > b["n"]:
            try:
                pix = round(float(pix), 2) if pix not in (None, "") else None
            except (TypeError, ValueError):
                pix = None
            best[cam] = dict(name=cam, pixel_um=pix, width_px=w, height_px=h, n=n)
    return sorted(best.values(), key=lambda b: b["name"])


def _f(name):
    v = (request.form.get(name) or "").replace(",", ".").strip()
    try:
        return float(v) if v else None
    except ValueError:
        abort(400, f"Ungültige Zahl für {name}")


def _i(name):
    v = _f(name)
    return int(v) if v is not None else None


@main.post("/rigs/camera")
def save_camera():
    c = db.session.get(Camera, request.form.get("id", type=int)) if request.form.get("id") else Camera()
    c.name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    c.header_name = (request.form.get("header_name") or "").strip() or None
    c.pixel_um, c.width_px, c.height_px = _f("pixel_um"), _i("width_px"), _i("height_px")
    c.notes = request.form.get("notes") or None
    db.session.add(c)
    db.session.commit()
    flash(f"Kamera „{c.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#cameras")


@main.post("/rigs/cameras-from-headers")
def import_cameras():
    n = 0
    for h in _header_cameras():
        if db.session.scalar(select(Camera).where(func.lower(Camera.header_name) == h["name"].lower())):
            continue
        db.session.add(Camera(name=h["name"], header_name=h["name"], pixel_um=h["pixel_um"],
                              width_px=h["width_px"], height_px=h["height_px"]))
        n += 1
    db.session.commit()
    flash(f"{n} Kamera(s) aus den FITS-Headern übernommen.", "success")
    return redirect(url_for("main.rigs_page") + "#cameras")


@main.post("/rigs/optic")
def save_optic():
    o = db.session.get(Optic, request.form.get("id", type=int)) if request.form.get("id") else Optic()
    o.name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    o.kind = request.form.get("kind") if request.form.get("kind") in OPTIC_KINDS else "telescope"
    o.focal_mm = _f("focal_mm") or abort(400, "Brennweite fehlt")
    o.focal_max_mm = _f("focal_max_mm") if o.kind == "zoom" else None
    if o.focal_max_mm and o.focal_max_mm < o.focal_mm:
        o.focal_mm, o.focal_max_mm = o.focal_max_mm, o.focal_mm
    o.aperture_mm, o.f_number = _f("aperture_mm"), _f("f_number")
    o.f_number_max = _f("f_number_max") if o.kind == "zoom" else None
    o.notes = request.form.get("notes") or None
    db.session.add(o)
    db.session.commit()
    flash(f"{OPTIC_KINDS[o.kind]} „{o.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#optics")


@main.post("/rigs/corrector")
def save_corrector():
    c = db.session.get(Corrector, request.form.get("id", type=int)) if request.form.get("id") else Corrector()
    c.name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    c.kind = request.form.get("kind") if request.form.get("kind") in CORRECTOR_KINDS else "flattener"
    c.factor = _f("factor") or 1.0
    c.notes = request.form.get("notes") or None
    db.session.add(c)
    db.session.commit()
    flash(f"„{c.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#correctors")


@main.post("/rigs/rig")
def save_rig():
    r = db.session.get(Rig, request.form.get("id", type=int)) if request.form.get("id") else Rig()
    r.name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    r.camera_id = request.form.get("camera_id", type=int) or abort(400, "Kamera fehlt")
    r.optic_id = request.form.get("optic_id", type=int) or abort(400, "Optik fehlt")
    r.corrector_id = request.form.get("corrector_id", type=int) or None
    r.active = bool(request.form.get("active"))
    r.notes = request.form.get("notes") or None
    db.session.add(r)
    db.session.commit()
    flash(f"Rig „{r.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#rigs")


@main.post("/rigs/<kind>/<int:oid>/delete")
def delete_component(kind: str, oid: int):
    model = {"camera": Camera, "optic": Optic, "corrector": Corrector, "rig": Rig}.get(kind) or abort(404)
    obj = db.session.get(model, oid) or abort(404)
    if kind != "rig":
        col = {"camera": Rig.camera_id, "optic": Rig.optic_id, "corrector": Rig.corrector_id}[kind]
        used = db.session.scalars(select(Rig.name).where(col == oid)).all()
        if used:
            flash(f"„{obj.name}“ wird noch in {', '.join(used)} verwendet.", "danger")
            return redirect(url_for("main.rigs_page"))
    name = obj.name
    db.session.delete(obj)
    db.session.commit()
    flash(f"„{name}“ gelöscht.", "success")
    return redirect(url_for("main.rigs_page"))


@main.post("/equipment/assign")
def assign_dataset():
    camera, focal = request.form.get("camera"), request.form.get("focal", type=int)
    rig_id = request.form.get("rig_id", type=int)
    m = db.session.scalar(select(RigMatch).where(RigMatch.camera == camera, RigMatch.focal == focal))
    if rig_id:
        m = m or RigMatch(camera=camera, focal=focal)
        m.rig_id = rig_id
        db.session.add(m)
    elif m:
        db.session.delete(m)
    db.session.commit()
    return redirect(url_for("main.equipment") + "#datasets")


@main.post("/session/<int:sid>/rig")
def session_rig(sid: int):
    s = db.session.get(Session, sid) or abort(404)
    o = db.session.scalar(select(RigSessionOverride).where(RigSessionOverride.session_root == s.root))
    rig_id = request.form.get("rig_id", type=int)
    if rig_id:
        o = o or RigSessionOverride(session_root=s.root)
        o.rig_id = rig_id
        db.session.add(o)
    elif o:
        db.session.delete(o)
    db.session.commit()
    return redirect(url_for("main.session_page", sid=sid))


def tolerance() -> float:
    return current_app.config["FOCAL_TOLERANCE"]
