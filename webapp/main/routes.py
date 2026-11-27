from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import func, select

from webapp import db
from webapp.archive.rules import catalog_sort_key
from webapp.models import Frame, IndexRun, Intermediate, Issue, IssueAck, Note, Session, Target, Version

main = Blueprint("main", __name__)

TYPE_ICONS = {
    "Galaxie": "bi-hurricane", "Galaxienpaar": "bi-hurricane", "Galaxientripel": "bi-hurricane",
    "Galaxiengruppe": "bi-hurricane",
    "Nebel": "bi-cloud-haze2", "Emissionsnebel": "bi-cloud-haze2", "Reflexionsnebel": "bi-cloud-haze2",
    "Dunkelnebel": "bi-cloud-fog2", "Supernovaüberrest": "bi-bullseye", "Planetarischer Nebel": "bi-record-circle",
    "Sternhaufen": "bi-stars", "Offener Sternhaufen": "bi-stars", "Sternhaufen mit Nebel": "bi-stars",
    "Sternassoziation": "bi-stars", "Kugelsternhaufen": "bi-brightness-high",
    "Mond": "bi-moon", "Sonne": "bi-sun", "Meteore": "bi-lightning", "Satellit": "bi-broadcast",
}


CHECK_GROUPS = [
    ("struktur", "Struktur", "bi-diagram-3",
     {"no_session_folder", "loose_frames", "session_without_date", "folder_naming", "extra_subfolder",
      "year_folder", "intermediate_location", "mixed_equipment", "multi_night", "folder_date", "type_mismatch",
      "calibration_only"}),
    ("wbpp", "WBPP", "bi-stack",
     {"wbpp_steps", "wbpp_calibration_masters", "wbpp_other", "wbpp_run_failed", "wbpp_outdated",
      "wbpp_missing_lights", "wbpp_rejected_in_stack", "wbpp_errors", "wbpp_calibration"}),
    ("doppelt", "Doppelte Bilder", "bi-files",
     {"duplicate", "duplicate_in_folder", "calibration_copies", "duplicate_version"}),
    ("kalibrierung", "Kalibrierung", "bi-sliders",
     {"darks_bias_present", "no_flats", "dark_exposure", "dark_no_header", "dark_temperature", "flat_camera",
      "flat_focal", "flat_filter"}),
    ("dateien", "Dateien & Header", "bi-file-earmark-x", {"unreadable", "missing_header", "wrong_extension"}),
    ("optik", "Optik (SAD)", "bi-bullseye",
     {"sad_tilt", "sad_edge", "sad_tracking", "sad_outliers", "sad_frame_errors", "sad_missing", "sad_unreadable"}),
    ("rigs", "Rigs", "bi-tools", {"no_rig"}),
    ("ziele", "Ziele", "bi-flag", {"pointing", "foreign_lights", "no_version", "no_catalog_id", "many_rejected"}),
]
SEVERITY = {"error": ("Fehler", "bi-x-octagon-fill", "danger"),
            "warning": ("Warnung", "bi-exclamation-triangle-fill", "warning"),
            "hint": ("Hinweis", "bi-info-circle", "info")}


def _check_group(code: str) -> str:
    return next((g[0] for g in CHECK_GROUPS if code in g[3]), "ziele")


def _night_status():
    from webapp.main.night_view import night_status
    return night_status()


def sad_overview() -> dict[str, dict]:
    """session root -> short SAD summary (FWHM″, ratio, ellipticity) for tables."""
    from webapp.archive.sad import summary
    from webapp.models import SadSeries
    out = {}
    for sr in db.session.scalars(select(SadSeries)):
        out[sr.session_root] = summary(sr, current_app.config.get("SAD_OUTLIER_FACTOR", 1.3))
    return out


@main.app_context_processor
def _globals():
    def issue_badge():
        try:
            open_, _ = split_acknowledged(db.session.scalars(
                select(Issue).where(Issue.severity.in_(("error", "warning")))).all())
            return len(open_)
        except Exception:  # noqa: BLE001 - e.g. table not migrated yet
            return 0
    def indexer_problem():
        """The latest finished index run, if it failed - shown on every page."""
        try:
            last = db.session.scalars(select(IndexRun).where(IndexRun.finished.is_not(None))
                                      .order_by(IndexRun.id.desc()).limit(1)).first()
            if not last or last.phase != "failed":
                return None
            # a newer run is going on (e.g. after the mount was fixed): its result decides, not the old error
            from webapp import indexer
            newer = db.session.scalar(select(func.max(IndexRun.id)).where(IndexRun.finished.is_(None)))
            if newer and newer > last.id and indexer.is_running(current_app.config):
                return None
            ok = db.session.scalars(select(IndexRun).where(IndexRun.phase == "done")
                                    .order_by(IndexRun.id.desc()).limit(1)).first()
            return dict(message=last.message, when=last.finished, last_ok=ok.finished if ok else None)
        except Exception:  # noqa: BLE001 - e.g. table not migrated yet
            return None
    return {"type_icon": lambda t: TYPE_ICONS.get(t, "bi-star"), "is_htmx": bool(request.headers.get("HX-Request")),
            "issue_badge": issue_badge, "indexer_problem": indexer_problem, "data_version": data_version,
            "SEVERITY": SEVERITY}


# --------------------------------------------------------------------------- helpers

def _sort_key_name(t: Target):
    return catalog_sort_key(t.name) if t.catalog_ids else (999, 0, t.name.lower())


def session_rigs(sessions, res) -> dict[int, list[dict]]:
    """Session id -> [{rig, how, d}] per data set (camera x focal length), one entry per rig."""
    rigs = {}
    for s in sessions:
        seen, out = set(), []
        for d in s.datasets or []:
            rig, how = res.resolve(s.root, d["camera"], d["focal"])
            key = rig.id if rig else (d["camera"], d["focal"])
            if key not in seen:
                seen.add(key)
                out.append(dict(rig=rig, how=how, d=d))
        rigs[s.id] = out
    return rigs


def _datasets(sessions: list[Session], res=None) -> list[dict]:
    agg: dict[tuple, dict] = {}
    for s in sessions:
        for d in s.datasets or []:
            k = (d["camera"], d["focal"], d["telescope"])
            a = agg.setdefault(k, dict(camera=d["camera"], focal=d["focal"], telescope=d["telescope"],
                                       seconds=0.0, lights=0, sessions=set(), first=None, last=None,
                                       scale=None, fov_w=None, fov_h=None, rigs={}))
            if res is not None:                # rigs of this data set (a session may have chosen another)
                rig, how = res.resolve(s.root, d["camera"], d["focal"])
                a["rigs"].setdefault(rig.id if rig else None, dict(rig=rig, how=how, d=d))
            if d.get("focal_header"):
                a["focal_header"] = d["focal_header"]
            if d.get("scale") and not a["scale"]:
                a["scale"], a["fov_w"], a["fov_h"] = d["scale"], d["fov_w"], d["fov_h"]
            a["seconds"] += d["seconds"]
            a["lights"] += d["lights"]
            a["sessions"].add(s.id)
            if s.date:
                a["first"] = min(filter(None, [a["first"], s.date]))
                a["last"] = max(filter(None, [a["last"], s.date]))
    out = sorted(agg.values(), key=lambda a: -a["seconds"])
    for a in out:
        a["sessions"] = len(a["sessions"])
        a["rigs"] = sorted(a["rigs"].values(), key=lambda x: x["rig"] is None)
    return out


def _filter_options():
    sessions = db.session.scalars(select(Session)).all()
    cams, focals, filters, years = set(), set(), set(), set()
    for s in sessions:
        for d in s.datasets or []:
            if d["camera"]:
                cams.add(d["camera"])
            if d["focal"]:
                focals.add(d["focal"])
        filters.update((s.filters or {}).keys())
        if s.date:
            years.add(s.date.year)
    return dict(cameras=sorted(cams), focals=sorted(focals), filters=sorted(filters),
                years=sorted(years, reverse=True))


def _targets(args) -> list[Target]:
    targets = db.session.scalars(select(Target)).all()
    q = (args.get("q") or "").strip().lower()
    cam, focal, filt, year = args.get("camera"), args.get("focal"), args.get("filter"), args.get("year")
    out = []
    for t in targets:
        if q:
            hay = " ".join([t.name, t.common_name or "", t.object_type or "", t.constellation or "",
                            *t.catalog_ids, *t.folders]).lower()
            if q not in hay and q.replace(" ", "") not in hay.replace(" ", ""):
                continue
        if cam or focal or filt or year:
            ok = False
            for s in t.sessions:
                if year and not (s.date and str(s.date.year) == year):
                    continue
                if filt and filt not in (s.filters or {}):
                    continue
                if (cam or focal) and not any(
                        (not cam or d["camera"] == cam) and (not focal or str(d["focal"]) == focal)
                        for d in s.datasets or []):
                    continue
                ok = True
                break
            if not ok:
                continue
        out.append(t)
    sort = args.get("sort", "name")
    if sort == "hours":
        out.sort(key=lambda t: -t.exposure_s)
    elif sort == "recent":
        out.sort(key=lambda t: (t.last_night is None, -(t.last_night.toordinal() if t.last_night else 0)))
    else:
        out.sort(key=_sort_key_name)
    return out


def _cover(t: Target) -> Version | None:
    vs = [v for v in t.versions if v.thumb]
    if not vs:
        return None
    for v in vs:
        if v.path == t.cover_path:
            return v
    finals = [v for v in vs if "/finals/" in v.path.lower()]
    return max(finals or vs, key=lambda v: v.mtime)


def _session_calibration() -> dict:
    from webapp.main.wbpp_view import session_calibration
    return session_calibration()


def _detail(t: Target) -> dict:
    sessions = sorted(t.sessions, key=lambda s: (s.date is None, s.date))
    filt: dict[str, float] = defaultdict(float)
    for s in sessions:
        for k, v in (s.filters or {}).items():
            filt[k] += v
    from webapp.main.rigs import Resolver
    res = Resolver(current_app.config["FOCAL_TOLERANCE"])
    datasets = _datasets(sessions, res)
    with_lights = [s for s in sessions if s.lights]
    frames_total = sum(s.lights + s.flats + s.darks + s.biases + s.darkflats for s in sessions)
    versions = sorted([v for v in t.versions if v.thumb], key=lambda v: -v.mtime)
    broken = [v for v in t.versions if v.error]
    from webapp.main.phd_view import guiding_status
    return dict(
        sad=sad_overview(), phd=guiding_status(), night=_night_status(), cal=_session_calibration(),
        notes=db.session.scalars(select(Note).where(Note.folder.in_([f for f in t.folders or []
                                                                    if f not in {s.root for s in sessions}]))).all(),
        noted=set(db.session.scalars(select(Note.folder).where(Note.folder.in_([s.root for s in sessions])))),
        t=t, sessions=sessions, datasets=datasets, rigs=session_rigs(sessions, res), cover=_cover(t), versions=versions, broken=broken,
        filters=sorted(filt.items(), key=lambda kv: -kv[1]), filter_max=max(filt.values(), default=0),
        n_sessions=len(with_lights),
        rejected=sum(s.rejected for s in sessions), frames_total=frames_total,
    )


# --------------------------------------------------------------------------- pages

@main.route("/")
def index():
    targets = _targets(request.args)
    sel = request.args.get("t", type=int)
    t = db.session.get(Target, sel) if sel else (targets[0] if targets else None)
    last = db.session.scalars(select(IndexRun).order_by(IndexRun.id.desc()).limit(1)).first()
    return render_template("index.html", targets=targets, selected=t, opts=_filter_options(),
                           args=request.args, last_run=last, **(_detail(t) if t else {}))


@main.route("/targets")
def target_list():
    sel = request.args.get("t", type=int)
    return render_template("_target_list.html", targets=_targets(request.args), selected_id=sel)


@main.route("/target/<int:tid>")
def target(tid: int):
    t = db.session.get(Target, tid) or abort(404)
    if request.headers.get("HX-Request"):
        return render_template("_target_detail.html", **_detail(t))
    return index_with(t)


def index_with(t: Target):
    targets = _targets(request.args)
    return render_template("index.html", targets=targets, selected=t, opts=_filter_options(),
                           args=request.args, last_run=None, **_detail(t))


@main.post("/target/<int:tid>/cover/<int:vid>")
def set_cover(tid: int, vid: int):
    t = db.session.get(Target, tid) or abort(404)
    v = db.session.get(Version, vid) or abort(404)
    if v.target_id != t.id:
        abort(400)
    t.cover_path = v.path              # stored in PhotonLedger's database only
    db.session.commit()
    return render_template("_target_detail.html", **_detail(t))


@main.route("/media/<kind>/<int:vid>")
def media(kind: str, vid: int):
    if kind not in ("thumbs", "previews"):
        abort(404)
    v = db.session.get(Version, vid) or abort(404)
    name = v.thumb if kind == "thumbs" else v.preview
    if not name:
        abort(404)
    path = Path(current_app.config["DATA_PATH"]) / name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=86400)


def split_acknowledged(issues) -> tuple[list, list]:
    """(open, acknowledged): an acknowledgement holds while the message stays the same - a new count
    or another file in the message brings the issue back."""
    acks = {a.key: a.message for a in db.session.scalars(select(IssueAck))}
    open_, done = [], []
    for i in issues:
        (done if acks.get(i.key) == i.message else open_).append(i)
    return open_, done


@main.post("/checks/<int:iid>/ack")
def check_ack(iid: int):
    from datetime import datetime
    i = db.session.get(Issue, iid) or abort(404)
    a = db.session.scalar(select(IssueAck).where(IssueAck.key == i.key)) or IssueAck(key=i.key)
    a.message, a.created = i.message, datetime.now()
    db.session.add(a)
    db.session.commit()
    return redirect(request.form.get("next") or url_for("main.checks"))


@main.post("/checks/<int:iid>/unack")
def check_unack(iid: int):
    i = db.session.get(Issue, iid) or abort(404)
    a = db.session.scalar(select(IssueAck).where(IssueAck.key == i.key))
    if a:
        db.session.delete(a)
        db.session.commit()
    return redirect(request.form.get("next") or url_for("main.checks"))


@main.route("/checks")
def checks():
    order = {"error": 0, "warning": 1, "hint": 2}
    group = request.args.get("g")
    sev = request.args.get("s")
    show_ack = request.args.get("ack") == "1"
    open_, acked = split_acknowledged(db.session.scalars(select(Issue)).all())
    issues = acked if show_ack else open_
    counts = {g[0]: {"error": 0, "warning": 0, "hint": 0} for g in CHECK_GROUPS}
    for i in issues:
        counts[_check_group(i.code)][i.severity] += 1
    shown = [i for i in issues
             if (not group or _check_group(i.code) == group) and (not sev or i.severity == sev)]
    shown.sort(key=lambda i: (order.get(i.severity, 9), _check_group(i.code), i.code,
                              i.target.name if i.target else "", i.session_root or ""))
    totals = {k: sum(c[k] for c in counts.values()) for k in order}
    # the severity buttons count what they would show: within the selected group
    in_group = counts.get(group, totals) if group else totals
    return render_template("checks.html", issues=shown, groups=CHECK_GROUPS, counts=counts, totals=totals,
                           group=group, sev=sev, group_of=_check_group, show_ack=show_ack, n_acked=len(acked),
                           in_group=in_group)


FILE_KINDS = {
    "light": ("Lights", Frame, Frame.frame_type == "light"),
    "flat": ("Flats", Frame, Frame.frame_type == "flat"),
    "dark": ("Darks", Frame, Frame.frame_type == "dark"),
    "bias": ("Bias", Frame, Frame.frame_type == "bias"),
    "ignored": ("Stacks neben Rohdaten", Frame, Frame.frame_type.is_(None) & Frame.error.is_(None)),
    "intermediate": ("Zwischenergebnisse", Intermediate, None),
    "error": ("Nicht lesbar", Frame, Frame.error.is_not(None)),
}
FILES_PAGE = 250
# GLOB is case sensitive (LIKE is not): only a folder named exactly "Processing" counts
_IN_PROCESSING = Intermediate.path.op("GLOB")("*/Processing/*")
def _session_roots() -> set[str]:
    return set(db.session.scalars(select(Session.root)))


def _misplaced_intermediates() -> int:
    """Intermediates outside 'Processing' and 'Finals'."""
    from webapp.archive.rules import allowed_location
    roots = _session_roots()
    paths = db.session.scalars(select(Intermediate.path).where(Intermediate.path.op("NOT GLOB")("*/Processing/*")))
    return sum(1 for p in paths if not allowed_location(p, roots))


def _kind_count(model, cond) -> int:
    stmt = select(func.count()).select_from(model)
    return db.session.scalar(stmt.where(cond) if cond is not None else stmt)


@main.route("/files")
def files():
    kind = request.args.get("kind", "dark")
    if kind not in FILE_KINDS:
        abort(404)
    label, model, cond = FILE_KINDS[kind]
    q = (request.args.get("q") or "").strip()
    camera = request.args.get("camera") or ""
    state = request.args.get("state") or ""
    page = max(1, request.args.get("page", 1, type=int))

    stmt = select(model)
    if cond is not None:
        stmt = stmt.where(cond)
    for word in q.split():
        stmt = stmt.where(model.path.ilike(f"%{word}%"))
    if model is Frame:
        if camera:
            stmt = stmt.where(Frame.camera == camera)
        if state == "ok":
            stmt = stmt.where(Frame.rejected.is_(False), Frame.duplicate.is_(False))
        elif state == "rejected":
            stmt = stmt.where(Frame.rejected.is_(True))
        elif state == "duplicate":
            stmt = stmt.where(Frame.duplicate.is_(True))
        cameras = [c for (c,) in db.session.execute(
            select(Frame.camera).where(cond, Frame.camera.is_not(None)).distinct().order_by(Frame.camera))]
    else:
        return _intermediate_files(stmt, kind, label, q, page)
    total = db.session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.session.scalars(stmt.order_by(model.path).limit(FILES_PAGE * page)).all()
    counts = {k: _kind_count(m, c) for k, (_, m, c) in FILE_KINDS.items()}
    args = dict(kind=kind, q=q, camera=camera, state=state)
    tpl = "_files_rows.html" if request.headers.get("HX-Request") and request.args.get("rows") else "files.html"
    return render_template(tpl, rows=rows, total=total, label=label, kind=kind, kinds=FILE_KINDS, counts=counts,
                           cameras=cameras, args=args, page=page, more=total > len(rows))


GROUP_LIMIT = 50


def _intermediate_files(stmt, kind, label, q, page):
    """Intermediate results grouped by the rule that applies (filterable by rule)."""
    from webapp.archive.rules import INTERMEDIATE_RULE_INFO, INTERMEDIATE_RULES, intermediate_findings

    rule = request.args.get("rule") or ""
    rows = db.session.scalars(stmt.order_by(Intermediate.path)).all()
    roots = _session_roots()
    for r in rows:
        r.findings = intermediate_findings(r.path, roots)
    rule_counts = {c: 0 for c, _, _ in INTERMEDIATE_RULES}
    for r in rows:
        for c in r.findings:
            rule_counts[c] += 1
    if rule:
        rows = [r for r in rows if rule in r.findings]
    groups = []
    if not rule:
        by_primary: dict[str, list] = {}
        for r in rows:
            by_primary.setdefault(r.findings[0], []).append(r)
        for code, lbl, sev in INTERMEDIATE_RULES:
            members = by_primary.get(code)
            if members:
                groups.append(dict(code=code, label=lbl, sev=sev, rows=members[:GROUP_LIMIT], total=len(members)))
        shown = rows
    else:
        shown = rows[:FILES_PAGE * page]
    counts = {k: _kind_count(m, c) for k, (_, m, c) in FILE_KINDS.items()}
    args = dict(kind=kind, q=q, rule=rule)
    tpl = "_files_rows.html" if request.headers.get("HX-Request") and request.args.get("rows") else "files.html"
    return render_template(tpl, rows=shown, total=len(rows), label=label, kind=kind, kinds=FILE_KINDS, counts=counts,
                           cameras=[], args=args, page=page, more=bool(rule) and len(rows) > len(shown),
                           groups=groups, rule=rule, rules=INTERMEDIATE_RULES, rule_info=INTERMEDIATE_RULE_INFO,
                           rule_counts=rule_counts)


@main.route("/healthz")
def healthz():
    db.session.execute(select(1))
    return {"status": "ok"}


@main.route("/sessions")
def sessions():
    rows = db.session.scalars(select(Session).where((Session.lights > 0) | (Session.duplicates > 0))).all()
    rows.sort(key=lambda s: (s.date is None, s.date), reverse=True)
    from webapp.main.phd_view import guiding_status
    from webapp.main.night_view import night_status
    from webapp.main.rigs import Resolver
    from webapp.main.wbpp_view import session_calibration, session_status
    rigs = session_rigs(rows, Resolver(current_app.config["FOCAL_TOLERANCE"]))
    from webapp.main.sad_view import sad_status
    return render_template("sessions.html", sessions=rows, sad=sad_status(sad_overview()), phd=guiding_status(),
                           night=night_status(), wbpp=session_status(), rigs=rigs, cal=session_calibration())


@main.route("/equipment")
def equipment():
    sessions_ = db.session.scalars(select(Session)).all()
    datasets = _datasets(sessions_)
    targets_per = defaultdict(set)
    for s in sessions_:
        for d in s.datasets or []:
            targets_per[(d["camera"], d["focal"], d["telescope"])].add(s.target_id)
    for d in datasets:
        d["targets"] = len(targets_per[(d["camera"], d["focal"], d["telescope"])])
    cams = defaultdict(lambda: dict(seconds=0.0, lights=0))
    for d in datasets:
        cams[d["camera"]]["seconds"] += d["seconds"]
        cams[d["camera"]]["lights"] += d["lights"]
    tol = current_app.config["FOCAL_TOLERANCE"]
    from webapp.main.rigs import Resolver, rig_statistics
    res = Resolver(tol)
    for d in datasets:
        d["rig"], d["how"] = res.resolve("", d["camera"], d["focal"])
        d["suggestions"] = res.suggestions(d["camera"], d["focal"])
    rig_stats, unassigned = rig_statistics(tol)
    return render_template("equipment.html", datasets=datasets, cameras=sorted(cams.items(), key=lambda kv: -kv[1]["seconds"]),
                           tolerance=tol, rig_stats=rig_stats, unassigned=unassigned, all_rigs=res.rigs)


@main.route("/statistics")
def statistics():
    sessions_ = db.session.scalars(select(Session).where(Session.lights > 0)).all()
    months: dict[str, float] = defaultdict(float)
    years: dict[int, dict] = defaultdict(lambda: dict(seconds=0.0, sessions=0, nights=set(), targets=set()))
    for s in sessions_:
        if not s.date:
            continue
        months[s.date.strftime("%Y-%m")] += s.exposure_s
        y = years[s.date.year]
        y["seconds"] += s.exposure_s
        y["sessions"] += 1
        y["nights"].update(s.nights or [])
        y["targets"].add(s.target_id)
    if months:
        first, last = min(months), max(months)
        y0, m0 = map(int, first.split("-"))
        y1, m1 = map(int, last.split("-"))
        series = []
        while (y0, m0) <= (y1, m1):
            k = f"{y0:04d}-{m0:02d}"
            series.append((k, months.get(k, 0.0)))
            m0 += 1
            if m0 > 12:
                y0, m0 = y0 + 1, 1
    else:
        series = []
    totals = dict(
        seconds=sum(s.exposure_s for s in sessions_),
        lights=sum(s.lights for s in sessions_),
        sessions=len(sessions_),
        nights=len({n for s in sessions_ for n in s.nights or []}),
        targets=len({s.target_id for s in sessions_}),
        rejected=sum(s.rejected for s in sessions_),
    )
    top = db.session.scalars(select(Target).where(Target.exposure_s > 0).order_by(Target.exposure_s.desc()).limit(10)).all()
    from webapp.main.usage import usage_statistics, years_title
    return render_template("statistics.html", series=series, series_max=max((v for _, v in series), default=0),
                           years=sorted(years.items(), reverse=True), totals=totals, top=top, sites=_sites(),
                           usage=usage_statistics(current_app.config["FOCAL_TOLERANCE"]), years_title=years_title)


def _sites() -> list[dict]:
    """Observing sites from SITELAT/SITELONG of the lights, grouped to ~1 km."""
    rows = db.session.execute(
        select(func.json_extract(Frame.header, "$.SITELAT"), func.json_extract(Frame.header, "$.SITELONG"),
               Frame.exptime, Frame.night)
        .where(Frame.frame_type == "light", Frame.rejected.is_(False), Frame.duplicate.is_(False))).all()
    sites: dict[tuple, dict] = {}
    for lat, lon, exp, night in rows:
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        k = (round(lat, 2), round(lon, 2))
        s = sites.setdefault(k, dict(lat=k[0], lon=k[1], seconds=0.0, lights=0, nights=set()))
        s["seconds"] += exp or 0
        s["lights"] += 1
        if night:
            s["nights"].add(night)
    return sorted(sites.values(), key=lambda s: -s["seconds"])


def _indexer_state() -> dict:
    """What the page shows about the schedule: interval, service, last and next run."""
    from webapp import indexer
    cfg = current_app.config
    last = db.session.scalars(select(IndexRun).order_by(IndexRun.id.desc()).limit(1)).first()
    hb = indexer.heartbeat(cfg)
    running = indexer.is_running(cfg)
    requested = (Path(cfg["DATA_PATH"]) / "indexer.request").exists()
    next_run = None
    if hb and hb.get("next_run"):
        from datetime import datetime
        next_run = datetime.fromisoformat(hb["next_run"])
    from webapp import backup
    from webapp.archive import nightlog
    from webapp.archive import archives
    arcs = archives.state(cfg)
    worst = next((s for s in ("missing", "empty") if any(a["state"] == s for a in arcs)), "ok")
    return dict(interval=int(cfg["SCAN_INTERVAL"]), service=hb is not None, running=running, requested=requested,
                archives=arcs, archive=", ".join(a["path"] for a in arcs), archive_ok=worst,
                last=last, next_run=next_run, phase=last.phase if last and running else None,
                airgus=nightlog.info(), backups=backup.listing(cfg), backup_keep=int(cfg.get("BACKUP_KEEP", 14)))


def data_version() -> dict:
    """Small fingerprint of the indexed data and the latest run - polled by every page."""
    from webapp import indexer
    last = db.session.scalars(select(IndexRun).where(IndexRun.finished.is_not(None))
                              .order_by(IndexRun.id.desc()).limit(1)).first()
    parts = []
    for model in (Frame, Version, Intermediate, Session, Issue):
        cnt = db.session.scalar(select(func.count()).select_from(model))
        parts.append(str(cnt))
    for model in (Frame, Version):
        parts.append("%.0f" % (db.session.scalar(select(func.coalesce(func.sum(model.mtime), 0))) or 0))
    parts.append(str(db.session.scalar(select(func.count()).select_from(Issue).where(Issue.severity != "hint"))))
    return dict(run=last.id if last else 0, phase=last.phase if last else None,
                running=indexer.is_running(current_app.config), data="-".join(parts))


@main.route("/status/version")
def status_version():
    return data_version()


@main.route("/status/state")
def status_state():
    return render_template("_indexer_state.html", st=_indexer_state())


@main.post("/status/scan")
def status_scan():
    """'Jetzt scannen': ask the running indexer service, or start a single run if there is none."""
    import subprocess
    import sys

    from webapp import indexer
    cfg = current_app.config
    if indexer.is_running(cfg):
        flash("Ein Indexlauf ist bereits aktiv.", "info")
    elif indexer.heartbeat(cfg):
        indexer.request_run(cfg)
        flash("Lauf angefordert – der Indexer-Dienst startet ihn in wenigen Sekunden.", "success")
    else:
        root = Path(__file__).resolve().parents[2]
        out = open(Path(cfg["DATA_PATH"]) / "indexer-manual.log", "a")
        subprocess.Popen([sys.executable, "-m", "webapp.indexer", "--once"], cwd=root, stdout=out, stderr=out,
                         start_new_session=True, env=dict(os.environ))
        flash("Kein Indexer-Dienst aktiv – ein einmaliger Lauf wurde gestartet.", "success")
    return redirect(url_for("main.status"))


@main.route("/status")
def status():
    runs = db.session.scalars(select(IndexRun).order_by(IndexRun.id.desc()).limit(15)).all()
    frame_errors = db.session.scalars(select(Frame).where(Frame.error.is_not(None)).limit(200)).all()
    version_errors = db.session.scalars(select(Version).where(Version.error.is_not(None)).limit(200)).all()
    counts = dict(db.session.execute(select(Frame.frame_type, func.count()).group_by(Frame.frame_type)).all())
    pending = db.session.scalar(select(func.count()).select_from(Version).where(Version.thumb.is_(None), Version.error.is_(None)))
    ignored = _kind_count(Frame, FILE_KINDS["ignored"][2])
    inter_total = _kind_count(Intermediate, None)
    inter_bad = _misplaced_intermediates() + ignored
    return render_template("status.html", runs=runs, frame_errors=frame_errors, version_errors=version_errors,
                           counts=counts, ignored=ignored, inter_total=inter_total, inter_bad=inter_bad,
                           pending=pending, st=_indexer_state())
