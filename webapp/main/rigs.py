"""Rigs: components (camera, optic, corrector), rigs, assignment to data sets, statistics."""
from __future__ import annotations

import re
import secrets
import statistics
from collections import defaultdict
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import delete, func, select

from webapp import db
from webapp.archive import archives
from webapp.models import (Camera, Corrector, Frame, Issue, Mount, Optic, Rig, RigMatch, RigSessionOverride, SadSeries,
                           Session)

from .routes import main

OPTIC_KINDS = {"telescope": "Teleskop", "prime": "Festbrennweite", "zoom": "Zoomobjektiv"}
CORRECTOR_KINDS = {"flattener": "Flattener / Komakorrektor", "reducer": "Reducer", "extender": "Extender / Barlow"}
# smart telescopes: camera, optic and mount in one device (header name = INSTRUME of its files)
SMART_PRESETS = {
    "dwarf3": dict(name="DWARF 3", header="DWARF 3", pixel_um=2.0, width_px=3856, height_px=2180,
                   focal_mm=150, aperture_mm=35),
    "dwarf2": dict(name="DWARF II", header="DWARF II", pixel_um=1.45, width_px=3840, height_px=2160,
                   focal_mm=100, aperture_mm=24),
    "seestar_s50": dict(name="Seestar S50", header="Seestar S50", pixel_um=2.9, width_px=1920, height_px=1080,
                        focal_mm=250, aperture_mm=50),
    "seestar_s30": dict(name="Seestar S30", header="Seestar S30", pixel_um=2.9, width_px=1920, height_px=1080,
                        focal_mm=150, aperture_mm=30),
    "vespera": dict(name="Vaonis Vespera", header="Vespera", pixel_um=2.9, width_px=1920, height_px=1080,
                    focal_mm=200, aperture_mm=50),
    "vespera2": dict(name="Vaonis Vespera II", header="Vespera II", pixel_um=2.9, width_px=3840, height_px=2160,
                     focal_mm=250, aperture_mm=50),
    "stellina": dict(name="Vaonis Stellina", header="Stellina", pixel_um=2.4, width_px=3072, height_px=2048,
                     focal_mm=400, aperture_mm=80),
}

MOUNT_KINDS = {"eq": "Parallaktisch (deutsch)", "fork": "Gabel / parallaktisch", "altaz": "Azimutal",
               "tracker": "Star Tracker", "smart": "Smart-Teleskop (integriert)"}


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
        if r.factor_for(focal, tol) is not None:            # with or (if optional) without the corrector
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


def rig_issues(tol: float) -> list[Issue]:
    """One warning per session whose data sets no rig covers (none or several fitting rigs).
    Nothing while no rig is defined at all (rigs not in use)."""
    res = Resolver(tol)
    if not res.rigs:
        return []
    out = []
    for s in db.session.scalars(select(Session).where(Session.lights > 0).order_by(Session.root)):
        missing = []
        for d in s.datasets or []:
            rig, how = res.resolve(s.root, d["camera"], d["focal"])
            if rig:
                continue
            what = f"{d['camera'] or 'Kamera unbekannt'}" + (f" {d['focal']} mm" if d["focal"] else ", Brennweite unbekannt")
            if how == "ambiguous":
                what += " (passen mehrere: " + ", ".join(r.name for r in res.suggestions(d["camera"], d["focal"])) + ")"
            missing.append(what)
        if missing:
            out.append(Issue(
                severity="warning", code="no_rig", session_root=s.root, target_id=s.target_id, count=len(missing),
                message=f"„{s.root}“: kein Rig für " + "; ".join(missing),
                hint="Unter „Rigs“ ein passendes Rig anlegen oder die Kamera/Brennweite einem Rig zuordnen; "
                     "für eine einzelne Nacht das Rig auf der Session-Seite wählen."))
    return out


def guide_suggestions(res: Resolver) -> dict[int, dict]:
    """Rig id -> guide camera named in the headers (GUIDECAM, written by ASIAIR) of the lights taken with
    that rig, for rigs without a guide camera: {name, lights, scale (arcsec/px from PHD2, if logged)}."""
    from collections import Counter
    from webapp.models import PhdSection
    guide = func.json_extract(Frame.header, "$.GUIDECAM")
    rows = db.session.execute(
        select(Frame.session_root, Frame.camera, Frame.focal_group, guide, func.count())
        .where(Frame.frame_type == "light", Frame.rejected.is_(False), guide.is_not(None), guide != "")
        .group_by(Frame.session_root, Frame.camera, Frame.focal_group, guide)).all()
    per_rig: dict[int, Counter] = defaultdict(Counter)
    for root, cam, focal, name, n in rows:
        rig, _ = res.resolve(root, cam, focal)
        if rig and not rig.guide_camera_id:
            per_rig[rig.id][str(name).strip()] += n
    scales = dict(db.session.execute(select(PhdSection.camera, func.avg(PhdSection.pixel_scale))
                                     .where(PhdSection.camera.is_not(None)).group_by(PhdSection.camera)).all())
    out = {}
    for rid, c in per_rig.items():
        name, n = c.most_common(1)[0]
        out[rid] = dict(name=name, lights=n, scale=scales.get(name))
    return out


def mount_drivers(res: Resolver) -> dict[int, str]:
    """Rig id -> mount driver named in the headers of its lights, for rigs without a mount. ASIAIR writes the
    mount driver (e.g. "EQMod Mount") as TELESCOP; other programs put the lens or telescope there, so only
    ASIAIR files and values that name a mount are used."""
    from collections import Counter
    tel = func.json_extract(Frame.header, "$.TELESCOP")
    creator = func.json_extract(Frame.header, "$.CREATOR")
    rows = db.session.execute(
        select(Frame.session_root, Frame.camera, Frame.focal_group, tel, creator, func.count())
        .where(Frame.frame_type == "light", tel.is_not(None), tel != "")
        .group_by(Frame.session_root, Frame.camera, Frame.focal_group, tel, creator)).all()
    per_rig: dict[int, Counter] = defaultdict(Counter)
    for root, cam, focal, name, prog, n in rows:
        name = str(name).strip()
        if "asiair" not in str(prog or "").lower() and "mount" not in name.lower():
            continue
        rig, _ = res.resolve(root, cam, focal)
        if rig and not rig.mount_id:
            per_rig[rig.id][name] += n
    return {rid: c.most_common(1)[0][0] for rid, c in per_rig.items()}


def refresh_rig_issues(tol: float) -> None:
    """Recompute the rig warnings right after a change on the rigs or session page (not only at the next run)."""
    db.session.execute(delete(Issue).where(Issue.code == "no_rig"))
    db.session.add_all(rig_issues(tol))
    db.session.commit()


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
    edit = {k: request.args.get(k, type=int) for k in ("camera", "optic", "corrector", "mount", "rig", "copy")}
    all_cams = db.session.scalars(select(Camera).order_by(Camera.name)).all()
    cams = [c for c in all_cams if c.role != "guide"]
    guide_cams = [c for c in all_cams if c.role == "guide"]
    optics = db.session.scalars(select(Optic).order_by(Optic.focal_mm)).all()
    corrs = db.session.scalars(select(Corrector).order_by(Corrector.name)).all()
    mounts = db.session.scalars(select(Mount).order_by(Mount.name)).all()
    rigs = db.session.scalars(select(Rig).order_by(Rig.active.desc(), Rig.name)).all()
    header_cams = _header_cameras()
    res_ = Resolver(current_app.config["FOCAL_TOLERANCE"])
    guides = guide_suggestions(res_)
    drivers = mount_drivers(res_)
    header_guides = _header_guide_cameras()
    known = {_norm(c.header_name) for c in all_cams} | {_norm(c.name) for c in all_cams}
    # rigs grouped by their optic (e.g. one telescope with several cameras), with the totals of the group
    stats = {st["rig"].id: st for st in rig_statistics(current_app.config["FOCAL_TOLERANCE"])[0]}
    groups: dict[int, dict] = {}
    for r in rigs:
        g = groups.setdefault(r.optic_id, dict(optic=r.optic, rigs=[], seconds=0.0, lights=0, sessions=set(), targets=set()))
        g["rigs"].append(r)
        st = stats.get(r.id)
        if st:
            g["seconds"] += st["seconds"]
            g["lights"] += st["lights"]
            g["sessions"] |= st["sessions"]
            g["targets"] |= st["targets"]
    rig_groups = sorted(groups.values(), key=lambda g: (not any(r.active for r in g["rigs"]), -g["seconds"], g["optic"].name))
    for g in rig_groups:
        g["sessions"], g["targets"] = len(g["sessions"]), len(g["targets"])
    return render_template(
        "rigs.html", rig_groups=rig_groups, cameras=cams, guide_cameras=guide_cams, optics=optics, correctors=corrs, mounts=mounts, rigs=rigs, edit=edit,
        cur=dict(camera=db.session.get(Camera, edit["camera"]) if edit["camera"] else None,
                 optic=db.session.get(Optic, edit["optic"]) if edit["optic"] else None,
                 corrector=db.session.get(Corrector, edit["corrector"]) if edit["corrector"] else None,
                 mount=db.session.get(Mount, edit["mount"]) if edit["mount"] else None,
                 rig=db.session.get(Rig, edit["rig"]) if edit["rig"] else None,
                 source=db.session.get(Rig, edit["copy"]) if edit["copy"] else None),   # duplicate
        new_header_cams=[h for h in header_cams if _norm(h["name"]) not in known],
        new_header_guides=[h for h in header_guides if _norm(h["name"]) not in known],
        fill_guides=[c for c in guide_cams if not c.pixel_um and _norm(c.header_name or c.name) in
                     {_norm(h["name"]) for h in header_guides if h["pixel_um"]}],
        guides=guides, drivers=drivers, mergeable=mergeable_rigs(rigs), smart_presets=SMART_PRESETS, smart_found=smart_candidates(rigs), optic_kinds=OPTIC_KINDS, corrector_kinds=CORRECTOR_KINDS, mount_kinds=MOUNT_KINDS, seeing=float(request.args.get("seeing", 2.5)))


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
    c.role = "guide" if request.form.get("role") == "guide" else "main"
    db.session.add(c)
    db.session.commit()
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
    flash(f"{'Guiding-Kamera' if c.role == 'guide' else 'Kamera'} „{c.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + ("#guide-cameras" if c.role == "guide" else "#cameras"))


_PHD_PIXEL = re.compile(r"pixel size = ([\d.]+) um")
_PHD_SIZE = re.compile(r"full size = (\d+) x (\d+)")


def _header_guide_cameras() -> list[dict]:
    """Guide cameras named in the lights (GUIDECAM) or in PHD2 logs, with pixel and image size from a
    PHD2 log of that camera if there is one."""
    from webapp.models import PhdLog, PhdSection
    guide = func.json_extract(Frame.header, "$.GUIDECAM")
    found: dict[str, dict] = {}
    for name, n in db.session.execute(select(guide, func.count()).where(Frame.frame_type == "light", guide.is_not(None),
                                                                        guide != "").group_by(guide)):
        found.setdefault(_norm(name), dict(name=str(name).strip(), lights=0, pixel_um=None, width_px=None, height_px=None))
        found[_norm(name)]["lights"] += n
    for cam, path in db.session.execute(select(PhdSection.camera, PhdLog.path).join(PhdLog, PhdSection.log_id == PhdLog.id)
                                        .where(PhdSection.camera.is_not(None))):
        h = found.setdefault(_norm(cam), dict(name=cam.strip(), lights=0, pixel_um=None, width_px=None, height_px=None))
        if h["pixel_um"] is None:
            full = archives.resolve(current_app.config, path)
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(20000)
            except (OSError, TypeError):
                continue
            if m := _PHD_PIXEL.search(head):
                h["pixel_um"] = float(m.group(1))
            if m := _PHD_SIZE.search(head):
                h["width_px"], h["height_px"] = int(m.group(1)), int(m.group(2))
    return sorted(found.values(), key=lambda h: -h["lights"])


@main.post("/rigs/guide-cameras-from-headers")
def import_guide_cameras():
    n = 0
    cams = db.session.scalars(select(Camera)).all()
    by_name = {_norm(c.header_name): c for c in cams if c.header_name} | {_norm(c.name): c for c in cams}
    for h in _header_guide_cameras():
        known = by_name.get(_norm(h["name"]))
        if known:                       # fill in what is missing from the PHD2 log
            if known.role == "guide" and not known.pixel_um and h["pixel_um"]:
                known.pixel_um, known.width_px, known.height_px = h["pixel_um"], h["width_px"], h["height_px"]
                n += 1
            continue
        db.session.add(Camera(name=h["name"], header_name=h["name"], pixel_um=h["pixel_um"], role="guide",
                              width_px=h["width_px"], height_px=h["height_px"]))
        n += 1
    db.session.commit()
    flash(f"{n} Guiding-Kamera(s) aus Headern und PHD2-Logs übernommen bzw. ergänzt.", "success")
    return redirect(url_for("main.rigs_page") + "#guide-cameras")


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
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
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
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
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
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
    flash(f"„{c.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#correctors")


@main.post("/rigs/rig/<int:rid>/guide-camera")
def adopt_guide_camera(rid: int):
    """Take the guide camera suggested from the headers: an existing camera with that (header) name,
    or a new one."""
    r = db.session.get(Rig, rid) or abort(404)
    name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    cam = next((c for c in db.session.scalars(select(Camera))
                if _norm(name) in {_norm(c.header_name), _norm(c.name)}), None)
    if cam is None:
        h = next((h for h in _header_guide_cameras() if _norm(h["name"]) == _norm(name)), {})
        cam = Camera(name=name, header_name=name, role="guide", pixel_um=h.get("pixel_um"),
                     width_px=h.get("width_px"), height_px=h.get("height_px"))
        db.session.add(cam)
        flash(f"Guiding-Kamera „{name}“ angelegt.", "info")
    r.guide_camera = cam
    db.session.commit()
    flash(f"Guiding-Kamera von „{r.name}“: {cam.name}.", "success")
    return redirect(url_for("main.rigs_page") + f"#rig-{rid}")


def smart_candidates(rigs) -> list[dict]:
    """Smart telescopes seen in the headers that no rig covers yet: [{key, name, lights}]."""
    used = {_norm(r.camera.header_name) for r in rigs} | {_norm(r.camera.name) for r in rigs}
    seen = {_norm(h["name"]): h for h in _header_cameras()}
    out = []
    for key, p in SMART_PRESETS.items():
        h = seen.get(_norm(p["header"]))
        if h and _norm(p["header"]) not in used:
            out.append(dict(key=key, name=p["name"], lights=h.get("n", 0)))
    return out


@main.post("/rigs/smart")
def create_smart_rig():
    """A smart telescope as one rig: its camera, optic and integrated mount are created (or reused)."""
    p = SMART_PRESETS.get(request.form.get("preset")) or abort(400, "Unbekanntes Smart-Teleskop")
    cams = db.session.scalars(select(Camera)).all()
    cam = next((c for c in cams if _norm(p["header"]) in {_norm(c.header_name), _norm(c.name)}), None)
    h = next((h for h in _header_cameras() if _norm(h["name"]) == _norm(p["header"])), {})
    if cam is None:
        cam = Camera(name=p["name"], header_name=p["header"])
        db.session.add(cam)
    cam.pixel_um = cam.pixel_um or h.get("pixel_um") or p["pixel_um"]
    cam.width_px = cam.width_px or h.get("width_px") or p["width_px"]
    cam.height_px = cam.height_px or h.get("height_px") or p["height_px"]
    optic_name = f"{p['name']} Optik"
    optic = db.session.scalar(select(Optic).where(Optic.name == optic_name)) or Optic(
        name=optic_name, kind="telescope", focal_mm=p["focal_mm"], aperture_mm=p["aperture_mm"])
    mount_name = f"{p['name']} (integriert)"
    mount = db.session.scalar(select(Mount).where(Mount.name == mount_name)) or Mount(name=mount_name, kind="smart")
    db.session.add_all([optic, mount])
    db.session.flush()
    rig = db.session.scalar(select(Rig).where(Rig.camera_id == cam.id, Rig.optic_id == optic.id))
    if rig is None:
        rig = Rig(name=p["name"], camera_id=cam.id, optic_id=optic.id, mount_id=mount.id, active=True)
        db.session.add(rig)
        flash(f"Smart-Teleskop „{p['name']}“ als Rig angelegt (Kamera, Optik und Montierung).", "success")
    else:
        flash(f"Für „{p['name']}“ gibt es schon das Rig „{rig.name}“.", "info")
    db.session.commit()
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
    return redirect(url_for("main.rigs_page") + f"#rig-{rig.id}")


def mergeable_rigs(rigs) -> list[tuple[Rig, Rig]]:
    """Pairs of rigs that differ only in the corrector (one without, one with it): (keep, drop)."""
    out = []
    for a in rigs:
        for b in rigs:
            if a.id != b.id and a.corrector_id and not b.corrector_id and (a.camera_id, a.optic_id) == (b.camera_id, b.optic_id) \
                    and (a.mount_id == b.mount_id or not a.mount_id or not b.mount_id) \
                    and (a.guide_camera_id == b.guide_camera_id or not a.guide_camera_id or not b.guide_camera_id):
                out.append((a, b))
    return out


@main.post("/rigs/merge/<int:keep_id>/<int:drop_id>")
def merge_rigs(keep_id: int, drop_id: int):
    """Merge a rig without corrector into the same rig with it: the corrector becomes optional; manual
    assignments, session choices, photo, mount and guiding move over."""
    keep, drop = db.session.get(Rig, keep_id) or abort(404), db.session.get(Rig, drop_id) or abort(404)
    if (keep, drop) not in mergeable_rigs([keep, drop]):
        abort(400, "Diese Rigs unterscheiden sich nicht nur im Extender")
    keep.corrector_optional = True
    keep.mount_id = keep.mount_id or drop.mount_id
    keep.guide_camera_id = keep.guide_camera_id or drop.guide_camera_id
    keep.active = keep.active or drop.active
    if not keep.photo and drop.photo:
        keep.photo, drop.photo = drop.photo, None
    for m in db.session.scalars(select(RigMatch).where(RigMatch.rig_id == drop.id)):
        m.rig_id = keep.id
    for o in db.session.scalars(select(RigSessionOverride).where(RigSessionOverride.rig_id == drop.id)):
        o.rig_id = keep.id
    _remove_photo(drop)
    name = drop.name
    db.session.delete(drop)
    db.session.commit()
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
    flash(f"„{name}“ in „{keep.name}“ zusammengefasst – {keep.corrector.name} ist jetzt optional.", "success")
    return redirect(url_for("main.rigs_page") + f"#rig-{keep.id}")


@main.post("/rigs/mount")
def save_mount():
    m = db.session.get(Mount, request.form.get("id", type=int)) if request.form.get("id") else Mount()
    m.name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    m.kind = request.form.get("kind") if request.form.get("kind") in MOUNT_KINDS else "eq"
    m.payload_kg = _f("payload_kg")
    m.notes = request.form.get("notes") or None
    db.session.add(m)
    db.session.commit()
    flash(f"„{m.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#mounts")


@main.post("/rigs/rig")
def save_rig():
    r = db.session.get(Rig, request.form.get("id", type=int)) if request.form.get("id") else Rig()
    r.name = (request.form.get("name") or "").strip() or abort(400, "Name fehlt")
    r.camera_id = request.form.get("camera_id", type=int) or abort(400, "Kamera fehlt")
    r.optic_id = request.form.get("optic_id", type=int) or abort(400, "Optik fehlt")
    r.corrector_id = request.form.get("corrector_id", type=int) or None
    r.corrector_optional = bool(r.corrector_id and request.form.get("corrector_optional"))
    r.mount_id = request.form.get("mount_id", type=int) or None
    r.guide_camera_id = request.form.get("guide_camera_id", type=int) or None
    r.active = bool(request.form.get("active"))
    r.notes = request.form.get("notes") or None
    db.session.add(r)
    db.session.commit()
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
    flash(f"Rig „{r.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + "#rigs")


@main.post("/rigs/<kind>/<int:oid>/delete")
def delete_component(kind: str, oid: int):
    model = {"camera": Camera, "optic": Optic, "corrector": Corrector, "mount": Mount, "rig": Rig}.get(kind) or abort(404)
    obj = db.session.get(model, oid) or abort(404)
    if kind != "rig":
        col = {"camera": Rig.camera_id, "optic": Rig.optic_id, "corrector": Rig.corrector_id,
               "mount": Rig.mount_id}[kind]
        cond = (col == oid) | (Rig.guide_camera_id == oid) if kind == "camera" else col == oid
        used = db.session.scalars(select(Rig.name).where(cond)).all()
        if used:
            flash(f"„{obj.name}“ wird noch in {', '.join(used)} verwendet.", "danger")
            return redirect(url_for("main.rigs_page"))
    name = obj.name
    if kind == "rig":
        _remove_photo(obj)
    db.session.delete(obj)
    db.session.commit()
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
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
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
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
    refresh_rig_issues(current_app.config["FOCAL_TOLERANCE"])
    return redirect(url_for("main.session_page", sid=sid))


def tolerance() -> float:
    return current_app.config["FOCAL_TOLERANCE"]


# --------------------------------------------------------------------------- rig photos
PHOTO_SIZE = 1600          # longest side of the stored photo (px)


def _photo_path(rel: str) -> Path:
    return Path(current_app.config["DATA_PATH"]) / rel


def _remove_photo(r: Rig) -> None:
    if r.photo:
        _photo_path(r.photo).unlink(missing_ok=True)
        r.photo = None


@main.post("/rigs/rig/<int:rid>/photo")
def upload_rig_photo(rid: int):
    """Store an uploaded photo of a rig (JPG/PNG/WebP …) as JPEG in DATA_PATH/rigs, oriented and resized."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    r = db.session.get(Rig, rid) or abort(404)
    f = request.files.get("photo")
    if not f or not f.filename:
        flash("Keine Datei ausgewählt.", "warning")
        return redirect(url_for("main.rigs_page") + "#rigs")
    try:
        with Image.open(f.stream) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((PHOTO_SIZE, PHOTO_SIZE), Image.LANCZOS)
            rel = f"rigs/{rid}-{secrets.token_hex(4)}.jpg"         # new name: browsers reload it
            out = _photo_path(rel)
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(out, "JPEG", quality=88)
    except (UnidentifiedImageError, OSError, ValueError):
        flash(f"„{f.filename}“ ist kein lesbares Bild (JPG, PNG oder WebP).", "danger")
        return redirect(url_for("main.rigs_page") + "#rigs")
    _remove_photo(r)
    r.photo = rel
    db.session.commit()
    flash(f"Foto für „{r.name}“ gespeichert.", "success")
    return redirect(url_for("main.rigs_page") + f"#rig-{rid}")


@main.post("/rigs/rig/<int:rid>/photo/delete")
def delete_rig_photo(rid: int):
    r = db.session.get(Rig, rid) or abort(404)
    _remove_photo(r)
    db.session.commit()
    return redirect(url_for("main.rigs_page") + f"#rig-{rid}")


@main.route("/rigs/rig/<int:rid>/photo")
def rig_photo(rid: int):
    r = db.session.get(Rig, rid) or abort(404)
    if not r.photo or not _photo_path(r.photo).is_file():
        abort(404)
    return send_file(_photo_path(r.photo), mimetype="image/jpeg", max_age=86400)


@main.app_errorhandler(413)
def upload_too_large(_e):
    flash(f"Datei zu groß (höchstens {current_app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} MB).", "danger")
    return redirect(request.referrer or url_for("main.rigs_page"))

