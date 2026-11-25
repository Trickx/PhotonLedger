"""Phase 2: derive targets, sessions and focal-length groups from the stored frames."""
from __future__ import annotations

import logging
import math
import statistics
from collections import Counter, defaultdict

from sqlalchemy import delete, select

from webapp import db
from webapp.models import Frame, Session, Target, Version

from . import rules
from .catalog import default_catalog, separation

log = logging.getLogger("photonledger.derive")


def _constellation(ra, dec):
    if ra is None or dec is None:
        return None
    try:
        from astropy import units as u
        from astropy.coordinates import SkyCoord, get_constellation
        return str(get_constellation(SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")))
    except Exception:  # noqa: BLE001
        return None


def derive(cfg: dict) -> None:
    frames = db.session.scalars(select(Frame)).all()
    versions = db.session.scalars(select(Version)).all()
    tol = cfg["FOCAL_TOLERANCE"]

    # ---- one spelling per camera ("FUJIFILM X-T4" from EXIF, "Fujifilm X-T4" from Siril)
    spellings: dict[str, Counter] = defaultdict(Counter)
    for f in frames:
        if f.camera:
            spellings[" ".join(f.camera.lower().split())][f.camera] += 1
    canonical = {k: c.most_common(1)[0][0] for k, c in spellings.items()}
    for f in frames:
        if f.camera:
            f.camera = canonical[" ".join(f.camera.lower().split())]

    # ---- focal length groups (per camera, lights only)
    by_cam: dict[str | None, list[float]] = defaultdict(list)
    for f in frames:
        if f.frame_type == "light" and f.focal:
            by_cam[f.camera].append(f.focal)
    groups = {cam: rules.cluster_focal_lengths(vals, tol) for cam, vals in by_cam.items()}
    for f in frames:
        f.focal_group = groups.get(f.camera, {}).get(f.focal) if f.frame_type == "light" else None

    usable = [f for f in frames if f.frame_type in rules.FRAME_TYPES]
    lights = [f for f in usable if f.frame_type == "light" and not f.rejected]

    # ---- targets: group folders that share a catalog id / name
    folders = {f.target_folder for f in usable} | {v.target_folder for v in versions}
    lights_per_folder = Counter(f.target_folder for f in lights)
    # folders with dated session folders are the "originals" when data was copied around
    dated_folders = {f.target_folder for f in lights
                     if rules.folder_date(f.session_root.rsplit("/", 1)[-1])}
    folder_to_key: dict[str, str] = {}
    wanted: dict[str, dict] = {}
    for group in rules.group_folders({f: f.rsplit("/", 1)[-1] for f in folders}):
        parsed = {f: rules.parse_target_folder(f.rsplit("/", 1)[-1]) for f in group}
        ids = sorted({c for p in parsed.values() for c in p.catalog_ids}, key=rules.catalog_sort_key)
        first = Counter()
        for f, p in parsed.items():
            if p.catalog_ids:
                first[p.catalog_ids[0]] += lights_per_folder[f] + 1
        if first:
            best = max(first.values())
            primary = sorted((c for c, n in first.items() if n == best), key=rules.catalog_sort_key)[0]
            key = f"id:{primary}"
        else:
            primary = None
        ordered = sorted(group, key=lambda f: (f not in dated_folders, -lights_per_folder[f], f))
        if primary:
            common = next((parsed[f].common_name for f in ordered if parsed[f].common_name), None)
        else:
            # without catalog id the bracket is usually a variant ("Moon (Standard)"), not a name
            common = None
        base = next((parsed[f].base_name for f in ordered if parsed[f].base_name), None)
        name = primary or base or common or group[0].rsplit("/", 1)[-1]
        if not primary:
            key = f"name:{name.lower()}"
        if common == name:
            common = None
        wanted[key] = dict(name=name, common_name=common, catalog_ids=ids, folders=group,
                           object_type=rules.guess_object_type(common, base, name))
        for f in group:
            folder_to_key[f] = key

    existing = {t.key: t for t in db.session.scalars(select(Target))}
    for key, data in wanted.items():
        t = existing.get(key) or Target(key=key)
        for k, v in data.items():
            setattr(t, k, v)
        db.session.add(t)
    for key, t in existing.items():
        if key not in wanted:
            db.session.delete(t)
    db.session.flush()
    targets = {t.key: t for t in db.session.scalars(select(Target))}

    # where the camera pointed: median of the (non rejected) lights
    coords: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for f in lights:
        if f.ra is not None and f.dec is not None and not (f.ra == 0 and f.dec == 0):
            coords[folder_to_key[f.target_folder]].append((f.ra, f.dec))
    spread: dict[str, float] = {}
    for key, t in targets.items():
        pts = coords.get(key)
        if pts:
            t.pointing_ra = statistics.median(p[0] for p in pts)
            t.pointing_dec = statistics.median(p[1] for p in pts)
            spread[key] = max(separation(t.pointing_ra, t.pointing_dec, ra, dec) for ra, dec in pts)
        else:
            t.pointing_ra = t.pointing_dec = None

    # ---- duplicate lights: same camera, DATE-OBS and exposure in two folders of one target
    def preference(f: Frame):
        dated = rules.folder_date(f.session_root.rsplit("/", 1)[-1]) is not None
        return (not dated, -f.session_root.count("/"), f.session_root, f.path)

    original: dict[tuple, Frame] = {}
    dup_source: dict[str, Counter] = defaultdict(Counter)
    for f in sorted((f for f in usable if f.frame_type == "light"), key=preference):
        f.duplicate = False
        if f.date_obs is None:
            continue
        k = (folder_to_key[f.target_folder], f.camera, f.date_obs, f.exptime)
        first = original.setdefault(k, f)
        if first is not f and first.session_root != f.session_root:
            f.duplicate = True
            dup_source[f.session_root][first.session_root] += 1

    # ---- sessions
    by_root: dict[str, list[Frame]] = defaultdict(list)
    for f in usable:
        by_root[f.session_root].append(f)
    existing_s = {s.root: s for s in db.session.scalars(select(Session))}
    for root, fs in by_root.items():
        s = existing_s.pop(root, None) or Session(root=root)
        tfolder = fs[0].target_folder
        s.label = rules.session_label(root, tfolder)
        s.target_id = targets[folder_to_key[tfolder]].id
        sl = [f for f in fs if f.frame_type == "light" and not f.rejected and not f.duplicate]
        s.duplicates = sum(1 for f in fs if f.frame_type == "light" and f.duplicate)
        src = dup_source.get(root)
        s.duplicate_of = rules.session_label(src.most_common(1)[0][0], tfolder.split("/")[0]) if src else None
        nights = sorted({f.night for f in sl if f.night})
        s.nights = [n.isoformat() for n in nights]
        s.date = rules.folder_date(root.rsplit("/", 1)[-1]) or (nights[0] if nights else None)
        if s.date is None:
            any_n = sorted(f.night for f in fs if f.night)
            s.date = any_n[0] if any_n else None
        s.lights = len(sl)
        s.exposure_s = sum(f.exptime or 0 for f in sl)
        s.rejected = sum(1 for f in fs if f.frame_type == "light" and f.rejected)
        s.flats = sum(1 for f in fs if f.frame_type == "flat" and not f.rejected)
        s.darks = sum(1 for f in fs if f.frame_type == "dark" and not f.rejected)
        s.biases = sum(1 for f in fs if f.frame_type == "bias" and not f.rejected)
        s.darkflats = sum(1 for f in fs if f.frame_type == "darkflat" and not f.rejected)
        filt: dict[str, float] = defaultdict(float)
        ds: dict[tuple, dict] = {}
        geo: dict[tuple, list[tuple[float, float, float]]] = defaultdict(list)
        for f in sl:
            filt[f.filter or "Ohne Filter"] += f.exptime or 0
            k = (f.camera, f.focal_group, f.telescope)
            d = ds.setdefault(k, dict(camera=f.camera, focal=f.focal_group, telescope=f.telescope,
                                      seconds=0.0, lights=0))
            d["seconds"] += f.exptime or 0
            d["lights"] += 1
            g = _geometry(f)
            if g:
                geo[k].append(g)
        for k, d in ds.items():
            if geo.get(k):
                d["scale"] = round(statistics.median(g[0] for g in geo[k]), 3)      # arcsec / pixel
                d["fov_w"] = round(statistics.median(g[1] for g in geo[k]), 1)      # arcmin
                d["fov_h"] = round(statistics.median(g[2] for g in geo[k]), 1)
        s.filters = dict(filt)
        s.datasets = sorted(ds.values(), key=lambda d: -d["seconds"])
        db.session.add(s)
    for s in existing_s.values():
        db.session.delete(s)

    # ---- versions -> target
    for v in versions:
        key = folder_to_key.get(v.target_folder)
        v.target_id = targets[key].id if key else None

    db.session.flush()
    # ---- cached target totals
    for t in targets.values():
        ss = db.session.scalars(select(Session).where(Session.target_id == t.id)).all()
        t.exposure_s = sum(s.exposure_s for s in ss)
        t.lights = sum(s.lights for s in ss)
        dates = [s.date for s in ss if s.date and s.lights]
        t.last_night = max(dates) if dates else None
        fovs = [(d["fov_w"], d["fov_h"]) for s in ss for d in s.datasets or [] if d.get("fov_w")]
        t.fov_w, t.fov_h = max(fovs, key=lambda x: x[0] * x[1]) if fovs else (None, None)
        _enrich(t, spread.get(t.key))
    db.session.commit()
    log.info("%d Ziele, %d Sessions", len(targets), len(by_root))


def _geometry(f: Frame) -> tuple[float, float, float] | None:
    """(arcsec/pixel, field width, field height in arcmin) from pixel size, binning and focal length."""
    pix = (f.header or {}).get("XPIXSZ")
    try:
        pix = float(pix) if pix not in (None, "") else None
    except (TypeError, ValueError):
        pix = None
    if not pix or not f.focal or not f.width or not f.height:
        return None
    scale = 206.265 * pix * (f.binning or 1) / f.focal
    return scale, f.width * scale / 60, f.height * scale / 60


SPREAD_WARN_DEG = 10.0


def _enrich(t: Target, spread_deg: float | None) -> None:
    """Catalog data, position, objects in the field of view and plausibility warnings."""
    cat = default_catalog()
    entry = cat.lookup(t.name) if t.catalog_ids else None
    warnings: list[str] = []
    folder_ids = list(t.catalog_ids or [])
    if entry:
        t.catalog_name = entry.id
        t.object_type = entry.type_de
        t.magnitude = entry.mag
        t.size_arcmin = entry.major
        ids = entry.all_ids + [c for c in folder_ids if c not in entry.all_ids]
        t.catalog_ids = ids
        if not t.common_name and entry.names:
            t.common_name = entry.names[0]
        t.ra, t.dec = entry.ra, entry.dec
    else:
        t.catalog_name = t.magnitude = t.size_arcmin = None
        t.ra, t.dec = t.pointing_ra, t.pointing_dec
    t.constellation = _constellation(t.ra, t.dec) if t.ra is not None else None

    # objects in the field. Without plate solve the rotation is unknown: everything within the
    # half diagonal may be in the frame, within half the short side it certainly is ("inside").
    centre = (t.pointing_ra, t.pointing_dec) if t.pointing_ra is not None else (t.ra, t.dec)
    objs = []
    if entry and centre[0] is not None and t.fov_w and t.fov_h:     # not for Moon, Sun, meteors …
        radius = math.hypot(t.fov_w, t.fov_h) / 2 / 60
        sure = min(t.fov_w, t.fov_h) / 2 / 60
        own = {entry.id} if entry else set()
        for sep, e in cat.within(centre[0], centre[1], radius, limit=400):
            if e.id in own:
                continue
            objs.append(dict(id=e.messier or e.id, alt=e.id if e.messier else None, type=e.type_de,
                             name=e.names[0] if e.names else None, mag=e.mag, sep=round(sep * 60, 1),
                             inside=sep <= sure))
        # the named objects of the folder first, then certain ones, then by magnitude
        named = set(folder_ids)
        objs.sort(key=lambda o: (o["id"] not in named and (o["alt"] or "") not in named, not o["inside"],
                                 o["mag"] if o["mag"] is not None else 99, o["sep"]))
    t.objects_in_field = objs[:200]

    if spread_deg and spread_deg > SPREAD_WARN_DEG:
        warnings.append(f"Die Lights zeigen bis zu {spread_deg:.0f}° auseinander – Mosaik, "
                        "fehlgeschlagene Positionierung oder mehrere Ziele in einem Ordner?")
    if entry and t.pointing_ra is not None:
        off = separation(t.ra, t.dec, t.pointing_ra, t.pointing_dec)
        limit = max(1.0, (max(t.fov_w or 0, t.fov_h or 0) / 60) * 0.75)
        if off > limit:
            warnings.append(f"Die Aufnahmen zeigen {off:.1f}° neben {entry.messier or entry.id} – "
                            "passt der Ordnername?")
    t.warnings = warnings


def cleanup_orphans() -> None:
    db.session.execute(delete(Session).where(Session.target_id.is_(None)))
    db.session.commit()
