"""Read PHD2 guide logs (PHD2_GuideLog_*.txt, also written by ASIAIR) - only read, never changed.

Structure of a log:
    PHD2 version …, Log version 2.5. Log enabled at 2026-04-21 21:45:44
    Guiding Begins at 2026-04-21 21:45:45            <- local time of the guiding computer
    Pixel scale = 6.45 arc-sec/px, Binning = 1, Focal length = 120 mm
    Camera = …, Exposure = 500 ms, Dec = 47.2 deg, Hour angle = -1.92 hr, Pier side = West
    Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,…,StarMass,SNR,ErrorCode
    1,0.628,"Mount",0.339,0.029,0.338,0.042,…
    INFO: DITHER by …   INFO: SETTLING STATE CHANGE, Settling started|complete|failed
    Guiding Ends at 2026-04-21 21:51:15
"""
from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from webapp import db
from webapp.models import IndexRun, PhdLog, PhdSection

log = logging.getLogger("photonledger.phd2")

LOG_DIRS = {"log", "logs"}
LOG_NAME = re.compile(r"^PHD2_GuideLog_.*\.txt$", re.I)
_TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"


def find_logs(dirpath: str, dirs: list[str], failed: set[str] | None = None, base=None) -> list[str]:
    """PHD2 logs in log/logs/Logs sub folders of dirpath (absolute paths)."""
    out = []
    for d in dirs:
        if d.lower() in LOG_DIRS:
            full = os.path.join(dirpath, d)
            try:
                out += [os.path.join(full, f) for f in os.listdir(full) if LOG_NAME.match(f)]
            except OSError:
                if failed is not None and base is not None:
                    failed.add(os.path.relpath(full, base).replace(os.sep, "/"))
    return out


def _local_to_utc(s: str, tz: ZoneInfo) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _num(rx: str, text: str):
    m = re.search(rx, text)
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def parse(text: str, tz: ZoneInfo) -> tuple[dict, list[dict]]:
    meta: dict = {}
    sections: list[dict] = []
    cur: dict | None = None
    cols: list[str] = []
    settling = False
    last_t = 0.0

    def close(end_line: str | None):
        nonlocal cur
        if cur is None:
            return
        if end_line:
            m = re.search(_TS, end_line)
            if m:
                cur["end_utc"] = _local_to_utc(m.group(1), tz)
        if not cur.get("end_utc"):
            cur["end_utc"] = cur["start_utc"] + timedelta(seconds=last_t)
        _stats(cur)
        sections.append(cur)
        cur = None

    for line in text.splitlines():
        if line.startswith("PHD2 version"):
            meta["version"] = line.split(",")[0].replace("PHD2 version", "").strip()
            m = re.search(_TS, line)
            if m:
                meta["started"] = _local_to_utc(m.group(1), tz)
        elif line.startswith("Guiding Begins at"):
            close(None)
            m = re.search(_TS, line)
            cur = dict(start_utc=_local_to_utc(m.group(1), tz), end_utc=None, header="", points=[], events=[],
                       dropped=0)
            cols, settling, last_t = [], False, 0.0
        elif cur is None:
            continue
        elif line.startswith("Guiding Ends"):
            close(line)
        elif line.startswith("Frame,"):
            cols = line.split(",")
        elif line.startswith("INFO:"):
            if "Settling started" in line:
                settling = True
            elif "Settling complete" in line:
                settling = False
            elif "Settling failed" in line:
                settling = False
                cur["events"].append([round(last_t, 1), "settle_fail"])
            elif "DITHER" in line:
                cur["events"].append([round(last_t, 1), "dither"])
        elif line[:1].isdigit() and cols:
            parts = line.split(",")
            row = dict(zip(cols, parts))
            try:
                t = float(row.get("Time") or 0)
            except ValueError:
                continue
            last_t = t
            if row.get("mount", "").strip('"') != "Mount" or not row.get("RARawDistance"):
                cur["dropped"] += 1
                continue
            try:
                ra, dec = float(row["RARawDistance"]), float(row["DECRawDistance"])
            except (KeyError, ValueError):
                cur["dropped"] += 1
                continue
            cur["points"].append((t, ra, dec, 1 if settling else 0))
        elif not cols:
            cur["header"] += line + "\n"
    close(None)
    return meta, sections


def _stats(sec: dict) -> None:
    h = sec["header"]
    scale = _num(r"Pixel scale = ([\d.]+)", h) or 1.0
    sec["pixel_scale"] = _num(r"Pixel scale = ([\d.]+)", h)
    sec["exposure_ms"] = _num(r"Exposure = ([\d.]+) ms", h)
    m = re.search(r"Camera = ([^,]+)", h)
    sec["camera"] = m.group(1).strip() if m else None
    m = re.search(r"Pier side = (\w+)", h)
    sec["pier_side"] = m.group(1) if m and m.group(1) not in ("Unknown",) else None
    sec["dec_deg"] = _num(r"Dec = (-?[\d.]+) deg", h)
    sec["hour_angle"] = _num(r"Hour angle = (-?[\d.]+) hr", h)
    pts = [(t, ra * scale, dec * scale, s) for t, ra, dec, s in sec["points"]]
    good = [p for p in pts if not p[3]]
    sec["frames"] = len(pts)
    if good:
        rr = math.sqrt(sum(p[1] ** 2 for p in good) / len(good))
        rd = math.sqrt(sum(p[2] ** 2 for p in good) / len(good))
        sec.update(rms_ra=rr, rms_dec=rd, rms_total=math.hypot(rr, rd),
                   peak_ra=max(abs(p[1]) for p in good), peak_dec=max(abs(p[2]) for p in good))
    else:
        sec.update(rms_ra=None, rms_dec=None, rms_total=None, peak_ra=None, peak_dec=None)
    sec["series"] = [[round(t, 1), round(r, 2), round(d, 2), s] for t, r, d, s in pts]
    sec["dithers"] = sum(1 for e in sec["events"] if e[1] == "dither")
    sec["settle_failed"] = sum(1 for e in sec["events"] if e[1] == "settle_fail")


def sync(cfg: dict, run: IndexRun, paths: list[str], protect: set[str] | None = None) -> None:
    archive = Path(cfg["ARCHIVE_PATH"])
    tz = ZoneInfo(cfg.get("TIMEZONE") or os.environ.get("TZ") or "Europe/Berlin")
    existing = {p.path: p for p in db.session.scalars(select(PhdLog))}
    for full in paths:
        rel = os.path.relpath(full, archive).replace(os.sep, "/")
        try:
            st = os.stat(full)
        except OSError:
            continue
        lg = existing.get(rel)
        if lg and lg.size == st.st_size and abs(lg.mtime - st.st_mtime) < 1e-3:
            lg.seen_run = run.id
            continue
        lg = lg or PhdLog(path=rel)
        lg.size, lg.mtime, lg.seen_run, lg.error = st.st_size, st.st_mtime, run.id, None
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                meta, secs = parse(fh.read(), tz)
            lg.version, lg.started = meta.get("version"), meta.get("started")
            lg.sections.clear()
            db.session.flush()
            for s in secs:
                lg.sections.append(PhdSection(**{k: s[k] for k in (
                    "start_utc", "end_utc", "pixel_scale", "exposure_ms", "camera", "pier_side", "dec_deg",
                    "hour_angle", "frames", "dropped", "dithers", "settle_failed", "rms_ra", "rms_dec",
                    "rms_total", "peak_ra", "peak_dec", "series", "events")}))
            log.info("PHD2-Log gelesen: %s (%d Abschnitte)", rel, len(secs))
        except Exception as e:  # noqa: BLE001
            lg.error = f"{type(e).__name__}: {e}"[:500]
            run.errors += 1
        db.session.add(lg)
        db.session.commit()
    from .scanner import keep
    db.session.execute(delete(PhdLog).where(PhdLog.seen_run != run.id, *keep(PhdLog.path, protect or set())))
    db.session.commit()


def sections_for(start: datetime, end: datetime, margin_min: int = 30) -> list[PhdSection]:
    """Guiding sections overlapping [start, end] (UTC); copies of the same log are dropped."""
    lo, hi = start - timedelta(minutes=margin_min), end + timedelta(minutes=margin_min)
    rows = db.session.scalars(select(PhdSection).where(PhdSection.start_utc <= hi, PhdSection.end_utc >= lo)
                              .order_by(PhdSection.start_utc)).all()
    seen, out = set(), []
    for s in rows:
        k = (s.start_utc, s.frames)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out
