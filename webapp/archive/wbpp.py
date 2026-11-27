"""Read PixInsight WeightedBatchPreprocessing logs (<Processing|PI>/logs/<yyyymmddhhmmss>.log) - only read.

A WBPP log holds
  - PixInsight / WBPP version, start time, total run time
  - a report at the end, in blocks "***** LIGHT FRAMES CALIBRATION *****", "***** IMAGE INTEGRATION *****" …:
    frames per group (active / total), the master dark/flat/bias used, frames rejected, registration
    result, the master light written, errors ("!!! Error: …")
  - per light: noise (sigma_n per channel) and number of PSF fits when it was measured, and the share of
    pixels rejected in the final integration
  - warnings ("** Warning: …")
The paths in the log are those of the computer that ran WBPP; lights are matched by file name.
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from webapp import db
from webapp.models import IndexRun, WbppRun
from webapp.archive import archives

log = logging.getLogger("photonledger.wbpp")

LOG_DIRS = {"log", "logs"}
LOG_NAME = re.compile(r"^\d{14}\.log$")
_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ?(.*)$")
_BLOCK = re.compile(r"^\*{5,} ?([A-Z][A-Z0-9 \-/]+?) ?\*{5,}$")
_BLOCK_END = re.compile(r"^\*{10,}$")
_GROUP = re.compile(r"Group of (\d+) (\w[\w ]*?) frames \((\d+) active\)")
_SUFFIX = re.compile(r"(_(c|cc|d|r|a|ln|lps|ci))+$")


def stem_of(path: str) -> str:
    """Original light name (without extension and WBPP suffixes _c, _cc, _d, _r …)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    return _SUFFIX.sub("", stem)


def _duration(s: str) -> float | None:
    s = s.strip()
    if s.endswith(" s"):
        try:
            return float(s[:-2])
        except ValueError:
            return None
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


_CAL_NAME = re.compile(r"^(flat|dark|bias|offset|darkflat|flatdark)[_\-. ]", re.I)


def _is_light_calibration(src: str, dst: str) -> bool:
    """WBPP writes calibrated lights to calibrated/Light_<group>/…, flats to calibrated/Flat_<group>/…;
    older versions without group folders: decide by the source file name."""
    group = dst.replace("\\", "/").rsplit("/", 2)[-2] if "/" in dst.replace("\\", "/") else ""
    if group.lower().startswith("light"):
        return True
    if group.lower().startswith(("flat", "dark", "bias")):
        return False
    return not _CAL_NAME.match(src.replace("\\", "/").rsplit("/", 1)[-1])


def find_logs(dirpath: str, dirs: list[str], failed: set[str] | None = None, rel=None) -> list[str]:
    out = []
    for d in dirs:
        if d.lower() in LOG_DIRS:
            full = os.path.join(dirpath, d)
            try:
                out += [os.path.join(full, f) for f in os.listdir(full) if LOG_NAME.match(f)]
            except OSError:
                if failed is not None and rel is not None:
                    failed.add(rel(full))
    return out


def parse(lines) -> dict:
    """Parse a WBPP log (iterable of lines) into a summary dict."""
    r: dict = dict(started=None, pi_version=None, wbpp_version=None, duration_s=None, blocks=[], errors=[],
                   warnings=Counter(), lights_used=[], frames={}, integrations=[],
                   calibration=dict(groups=[], masters={}))
    cal_group = None                                # light calibration group: {exposure, filter, masters, lights}
    cal_integ = None                                # integration of calibration frames: {kind, frames, folders}
    pending_master = None                           # "* Writing master X frame:" -> path on the next line
    block = None
    current = None                                  # stem of the file currently measured
    integ = None                                    # current integration: {frames, rejection: {stem: [pct]}}
    rej_path = None
    noise: dict[str, list[float]] = defaultdict(list)
    fits: dict[str, int] = defaultdict(int)
    measured: set[str] = set()
    for raw in lines:
        m = _LINE.match(raw.rstrip("\r\n"))
        if not m:
            continue
        ts, text = m.group(1), m.group(2)
        if r["started"] is None:
            r["started"] = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        t = text.strip()
        if r["pi_version"] is None and t.startswith("PixInsight Core"):
            r["pi_version"] = t.replace("PixInsight Core", "").strip()
        elif r["wbpp_version"] is None and t.startswith("Weighted Batch Preprocessing ") and ":" not in t:
            r["wbpp_version"] = t.replace("Weighted Batch Preprocessing", "").strip()
        elif t.startswith("* Weighted Batch Preprocessing:"):
            r["duration_s"] = _duration(t.split(":", 1)[1])
        # ---- report blocks
        bm = _BLOCK.match(t)
        if bm:
            block = dict(title=bm.group(1).strip(), lines=[])
            r["blocks"].append(block)
            continue
        if block is not None:
            if _BLOCK_END.match(t):
                block = None
            elif t:
                block["lines"].append(t)
                if t.startswith("!!! Error"):
                    r["errors"].append(dict(step=block["title"], message=t.replace("!!! Error:", "").strip()))
            continue
        # ---- warnings and errors outside the report
        if t.startswith("** Warning:"):
            r["warnings"][t.replace("** Warning:", "").strip()] += 1
        elif t.startswith("*** Error") or t.startswith("!!! Error"):
            r["errors"].append(dict(step="Verarbeitung", message=t.split(":", 1)[-1].strip()))
        # ---- calibration: masters made from single frames, and which masters calibrated the lights
        if (bm2 := re.match(r"\* Begin (calibration|integration) of (\w+) frames", t)):
            kind = bm2.group(2).lower()
            if bm2.group(1) == "calibration" and kind == "light":
                cal_group = dict(exposure=None, filter=None, frames=None, masters={}, lights=[])
                r["calibration"]["groups"].append(cal_group)
                cal_integ = None
            elif bm2.group(1) == "integration" and kind in ("dark", "flat", "bias", "darkflat"):
                cal_integ = dict(kind=kind, frames=0, folders=[])
                cal_group = None
            else:
                cal_group = cal_integ = None
        # ("Calibration frame n: … ---> …" lines follow after "* End calibration": the group stays current)
        if cal_group is not None:
            if (g := _GROUP.search(t)) and cal_group["frames"] is None:
                cal_group["frames"] = int(g.group(1))
            elif t.startswith("Filter") and ":" in t and cal_group["filter"] is None:
                cal_group["filter"] = t.split(":", 1)[1].strip()
            elif t.startswith("Exposure") and ":" in t and cal_group["exposure"] is None:
                cal_group["exposure"] = _num_s(t.split(":", 1)[1])
            elif (mm := re.match(r"^\*?\s*Master (bias|dark|flat): (.+)$", t)):
                v = mm.group(2).strip()
                cal_group["masters"][mm.group(1)] = None if v.lower() == "none" else v.replace("\\", "/").rsplit("/", 1)[-1]
        if cal_integ is not None:
            if (g := _GROUP.search(t)) and not cal_integ["frames"]:
                cal_integ["frames"] = int(g.group(1))
            elif (mm := re.match(r'^\[(true|false), "([^"]+)"', t)) and mm.group(1) == "true":
                folder = mm.group(2).replace("\\", "/").rsplit("/", 1)[0]
                if folder not in cal_integ["folders"]:
                    cal_integ["folders"].append(folder)
        if pending_master:
            if t.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", t):
                name = t.replace("\\", "/").rsplit("/", 1)[-1]
                r["calibration"]["masters"][name] = dict(cal_integ or dict(kind=pending_master, frames=None, folders=[]))
            pending_master = None
        if (mm := re.match(r"^\* Writing master (\w+) frame:", t)):
            pending_master = mm.group(1).lower()
        elif t.startswith("Add the master file:"):
            name = t.split(":", 1)[1].strip().replace("\\", "/").rsplit("/", 1)[-1]
            r["calibration"]["masters"].setdefault(name, dict(kind=None, frames=None, folders=[], given=True))
        # ---- lights that went in
        if t.startswith("Calibration frame ") and "--->" in t:
            src, dst = (x.strip() for x in t.split(":", 1)[1].split("--->", 1))
            if _is_light_calibration(src, dst):                    # flats are calibrated the same way
                r["lights_used"].append(stem_of(src))
                if cal_group is not None:
                    cal_group["lights"].append(stem_of(src))
        elif cal_group is not None and t.startswith("* Loading target file:") is False and \
                (mm := re.match(r"^\* Calibrating file \d+ of \d+: (.+)$", t)):
            cal_group["lights"].append(stem_of(mm.group(1)))
        # ---- per-frame measurements (first time a calibrated light is loaded: noise + PSF fits)
        if t.startswith("* Loading target file:"):
            path = t.split(":", 1)[1].strip()
            s = stem_of(path)
            current = s if ("/calibrated/" in path or "/cosmetized/" in path) and s not in measured else None
            if current:
                measured.add(current)
        elif current and "sigma_n =" in t:
            try:
                noise[current].append(float(t.split("sigma_n =")[1].split(",")[0]))
            except (IndexError, ValueError):
                pass
        elif current and "PSF fits" in t:
            mm = re.search(r"(\d+) PSF fits", t)
            if mm:
                fits[current] += int(mm.group(1))
        # ---- integrations and per-frame pixel rejection
        im = re.match(r"Integration of (\d+) images:", t)
        if im:
            integ = dict(frames=int(im.group(1)), rejection=defaultdict(list))
            r["integrations"].append(integ)
            rej_path = None
        elif integ is not None:
            if t.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", t):
                rej_path = t
            elif rej_path and (mm := re.match(r"^\d+ :\s+\d+\s+([\d.]+)%", t)):
                integ["rejection"][stem_of(rej_path)].append(float(mm.group(1)))
                rej_path = None
    # ---- summary of the report blocks
    summary = []
    for b in r["blocks"]:
        info = dict(step=b["title"])
        for line in b["lines"]:
            if (g := _GROUP.search(line)):
                info.update(frames=int(g.group(1)), kind=g.group(2), active=int(g.group(3)))
            elif line.startswith("Filter"):
                info["filter"] = line.split(":", 1)[1].strip()
            elif line.startswith("Exposure"):
                info["exposure"] = line.split(":", 1)[1].strip()
            elif line.startswith("Keywords") and line.split(":", 1)[1].strip() not in ("[]", ""):
                info["keywords"] = line.split(":", 1)[1].strip()
            elif line.startswith(("Master bias:", "Master dark:", "Master flat:")):
                k, v = line.split(":", 1)
                info.setdefault("masters", {})[k.replace("Master ", "")] = v.strip().rsplit("/", 1)[-1]
            elif (mm := re.match(r"^(\d+) rejected", line)):
                info["rejected"] = int(mm.group(1))
            elif line.startswith(("Registration completed", "Calibration completed", "Integration completed",
                                  "Autocrop completed", "Demosaicing completed", "Local normalization: completed",
                                  "Astrometric solution completed", "Frames measurement")):
                info.setdefault("results", []).append(line.replace("/Users/", "…/").rsplit("/", 1)[-1]
                                                      if "saved at" in line or "completed:" in line and "/" in line
                                                      else line)
            elif line.startswith("!!! Error"):
                info.setdefault("errors", []).append(line.replace("!!! Error:", "").strip())
        summary.append(info)
    r["blocks"] = summary
    # ---- per frame
    final = max(r["integrations"], key=lambda i: i["frames"], default=None)
    stems = set(noise) | set(fits) | (set(final["rejection"]) if final else set())
    for s in stems:
        rej = final["rejection"].get(s) if final else None
        r["frames"][s] = dict(
            noise=round(sum(noise[s]) / len(noise[s]), 6) if noise.get(s) else None,
            psf_fits=fits.get(s) or None,
            rejected_pct=round(sum(rej) / len(rej), 3) if rej else None)
    r["integrations"] = [dict(frames=i["frames"]) for i in r["integrations"]]
    r["warnings"] = dict(r["warnings"])
    r["lights_used"] = sorted(set(r["lights_used"]))
    for g in r["calibration"]["groups"]:
        g["lights"] = sorted(set(g["lights"]))
    return r


def _num_s(s: str) -> float | None:
    m = re.search(r"[\d.]+", s or "")
    return float(m.group(0)) if m else None


def sync(cfg: dict, run: IndexRun, paths: list[str], protect: set[str] | None = None) -> None:
    tz = ZoneInfo(cfg.get("TIMEZONE") or "Europe/Berlin")
    existing = {w.path: w for w in db.session.scalars(select(WbppRun))}
    for full in paths:
        rel = archives.to_rel(cfg, full)
        try:
            st = os.stat(full)
        except OSError:
            continue
        w = existing.get(rel)
        if w and w.size == st.st_size and abs(w.mtime - st.st_mtime) < 1e-3:
            w.seen_run = run.id
            continue
        w = w or WbppRun(path=rel)
        w.size, w.mtime, w.seen_run, w.error = st.st_size, st.st_mtime, run.id, None
        c = archives.classify_stored(cfg, rel.rsplit("/", 2)[0] + "/x.fit")            # the folder that holds logs/ -> target folder
        w.target_folder = c.target_folder if c else rel.split("/")[0]
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                p = parse(fh)
            w.started = p["started"]
            w.started_utc = (p["started"].replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                             if p["started"] else None)
            w.pi_version, w.wbpp_version, w.duration_s = p["pi_version"], p["wbpp_version"], p["duration_s"]
            w.blocks, w.errors, w.warnings = p["blocks"], p["errors"], p["warnings"]
            w.lights_used, w.frames, w.integrations = p["lights_used"], p["frames"], p["integrations"]
            w.calibration = p["calibration"]
            log.info("WBPP-Log gelesen: %s (%d Lights)", rel, len(p["lights_used"]))
        except Exception as e:  # noqa: BLE001
            w.error = f"{type(e).__name__}: {e}"[:500]
            run.errors += 1
        db.session.add(w)
        db.session.commit()
    from .scanner import keep
    db.session.execute(delete(WbppRun).where(WbppRun.seen_run != run.id, *keep(WbppRun.path, protect or set())))
    db.session.commit()


# --------------------------------------------------------------------------- evaluation

def master_written(w: WbppRun) -> bool:
    return any("masterLight_" in r for b in w.blocks or [] if b.get("step") == "IMAGE INTEGRATION"
               for r in b.get("results", []))


def coverage(w: WbppRun, lights) -> dict:
    """Compare the lights in the master with the lights of the target in the archive (by file name)."""
    used = set(w.lights_used or [])
    by_stem = {}
    for f in lights:
        by_stem.setdefault(f.path.rsplit("/", 1)[-1].rsplit(".", 1)[0], f)
    available = {s for s, f in by_stem.items() if not f.rejected and not f.duplicate}
    rejected_now = sorted(s for s in used if s in by_stem and by_stem[s].rejected)
    missing = sorted(available - used)
    newer = sorted(s for s in missing if w.started_utc and by_stem[s].date_obs and by_stem[s].date_obs > w.started_utc)
    unknown = sorted(used - set(by_stem))
    return dict(used=len(used), available=len(available), in_archive=len(used & set(by_stem)),
                missing=missing, newer=newer, rejected_now=rejected_now, unknown=unknown)


def runs_by_target(target_folders: list[str]) -> list[WbppRun]:
    return db.session.scalars(select(WbppRun).where(WbppRun.target_folder.in_(target_folders))
                              .order_by(WbppRun.started.desc())).all()
