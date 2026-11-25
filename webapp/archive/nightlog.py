"""Night logs (ASIAIR Autorun, NINA, PHD2, iOptron) analysed with Airgus.

Airgus (https://github.com/Trickx/Airgus) turns the log lines of each system into neutral events and
runs its rules over them (guide star lost, failed meridian flip, tracking off, stalled capture …).
PhotonLedger only reads the logs; for every session it feeds Airgus the events of the logs that
overlap the session in time and stores the alerts, some counts and the timeline.
"""
from __future__ import annotations

import hashlib
import heapq
import logging
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from webapp import db
from webapp.models import Frame, IndexRun, NightAnalysis, NightLog

log = logging.getLogger("photonledger.nightlog")

LOG_DIRS = {"log", "logs"}
MARGIN = timedelta(minutes=30)
# rules that only make sense while watching live
LIVE_ONLY_RULES = ("stale-log", "controller-health", "wifi", "controller-offline")
UTC = ZoneInfo("UTC")


def _airgus():
    try:
        from airgus.parsers import parser_for
        return parser_for
    except ImportError:  # pragma: no cover - Airgus not installed
        return None


# every Airgus module PhotonLedger uses - Airgus has no stable API yet, so a rename shows up here
AIRGUS_MODULES = ("airgus.parsers", "airgus.sources", "airgus.engine", "airgus.rules", "airgus.timeline",
                  "airgus.alerts")
REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements-airgus.txt"


def _source_of(dist) -> str | None:
    """Commit or URL Airgus was installed from (pip's direct_url.json)."""
    import json
    try:
        url = json.loads(dist.read_text("direct_url.json") or "{}").get("url")
    except (ValueError, OSError):
        return None
    return url


def _pinned() -> str | None:
    try:
        for line in REQUIREMENTS.read_text().splitlines():
            if line.strip().startswith("airgus") and "@" in line:
                return line.split("@", 1)[1].strip()
    except OSError:
        pass
    return None


def info() -> dict:
    """Is Airgus installed, which version and source, and can all modules PhotonLedger uses be imported?"""
    import importlib
    import importlib.metadata as md
    out = dict(installed=False, version=None, source=None, commit=None, pinned=_pinned(), mismatch=False,
               broken=[], logs={}, analysed=0)
    try:
        dist = md.distribution("airgus")
    except md.PackageNotFoundError:
        return out
    out.update(installed=True, version=dist.version, source=_source_of(dist))
    if out["source"]:
        tail = out["source"].rstrip("/").rsplit("/", 1)[-1]
        out["commit"] = tail.removesuffix(".tar.gz").removesuffix(".zip")
    out["mismatch"] = bool(out["source"] and out["pinned"] and out["source"] != out["pinned"])
    for mod in AIRGUS_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001 - any import problem means the integration is broken
            out["broken"].append(f"{mod}: {type(e).__name__}: {e}"[:200])
    out["logs"] = dict(db.session.execute(select(NightLog.kind, func.count()).group_by(NightLog.kind)).all())
    out["analysed"] = db.session.scalar(select(func.count()).select_from(NightAnalysis)) or 0
    return out


def kind_of(name: str) -> str | None:
    parser_for = _airgus()
    if parser_for is None or parser_for(name) is None:
        return None
    if name.startswith("PHD2_"):
        return "phd2"
    if name.startswith("Autorun_Log_"):
        return "asiair"
    if name.startswith("iOptron"):
        return "ioptron"
    return "nina"


def find_logs(dirpath: str, dirs: list[str], failed: set[str] | None = None, base=None) -> list[str]:
    out = []
    for d in dirs:
        if d.lower() in LOG_DIRS:
            full = os.path.join(dirpath, d)
            try:
                out += [os.path.join(full, f) for f in os.listdir(full) if kind_of(f)]
            except OSError:
                if failed is not None and base is not None:
                    failed.add(os.path.relpath(full, base).replace(os.sep, "/"))
    return out


def _to_utc(t: datetime, tz: ZoneInfo) -> datetime:
    if t.tzinfo is not None:
        return t.astimezone(UTC).replace(tzinfo=None)
    return t.replace(tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def _read(full: Path):
    from airgus.sources import read_events
    return read_events(full)


def sync(cfg: dict, run: IndexRun, paths: list[str], protect: set[str] | None = None) -> None:
    """Register the log files and their time span (re-read only when changed)."""
    if _airgus() is None:
        log.warning("Airgus ist nicht installiert – ASIAIR/NINA-Logs werden nicht ausgewertet")
        return
    archive = Path(cfg["ARCHIVE_PATH"])
    tz = ZoneInfo(cfg.get("TIMEZONE") or "Europe/Berlin")
    existing = {n.path: n for n in db.session.scalars(select(NightLog))}
    for full in paths:
        rel = os.path.relpath(full, archive).replace(os.sep, "/")
        try:
            st = os.stat(full)
        except OSError:
            continue
        n = existing.get(rel)
        if n and n.size == st.st_size and abs(n.mtime - st.st_mtime) < 1e-3:
            n.seen_run = run.id
            continue
        n = n or NightLog(path=rel, kind=kind_of(os.path.basename(full)) or "?")
        n.size, n.mtime, n.seen_run, n.error = st.st_size, st.st_mtime, run.id, None
        try:
            evs = _read(Path(full))
            n.events = len(evs)
            n.first_utc = _to_utc(min(e.time for e in evs), tz) if evs else None
            n.last_utc = _to_utc(max(e.time for e in evs), tz) if evs else None
        except Exception as e:  # noqa: BLE001
            n.error = f"{type(e).__name__}: {e}"[:500]
            run.errors += 1
        db.session.add(n)
    db.session.commit()
    from .scanner import keep
    db.session.execute(delete(NightLog).where(NightLog.seen_run != run.id, *keep(NightLog.path, protect or set())))
    db.session.commit()


def _windows() -> dict[str, tuple[datetime, datetime]]:
    out = {}
    for root, start, last, exp in db.session.execute(
            select(Frame.session_root, func.min(Frame.date_obs), func.max(Frame.date_obs), func.max(Frame.exptime))
            .where(Frame.frame_type == "light", Frame.date_obs.is_not(None)).group_by(Frame.session_root)):
        out[root] = (start - MARGIN, last + timedelta(seconds=exp or 0) + MARGIN)
    return out


def logs_for(root: str, windows: dict, logs: list[NightLog]) -> list[NightLog]:
    """Logs overlapping the session; copies (same name and size) only once; among simultaneous sessions of
    one target the log belongs to the session whose folder holds it."""
    lo, hi = windows[root]
    parent = root.rsplit("/", 1)[0]
    chosen: dict[tuple, NightLog] = {}
    for n in logs:
        if n.error or not n.first_utc or n.first_utc > hi or n.last_utc < lo:
            continue
        holder = n.path.rsplit("/", 2)[0]
        rivals = [o for o, (lo2, hi2) in windows.items() if o != root and o.rsplit("/", 1)[0] == parent
                  and n.first_utc <= hi2 and n.last_utc >= lo2]
        if rivals and holder != root and holder in rivals:
            continue
        key = (n.path.rsplit("/", 1)[-1], n.size)
        if key not in chosen or holder == root:
            chosen[key] = n
    return sorted(chosen.values(), key=lambda n: n.path)


def analyse(cfg: dict) -> int:
    """Run Airgus for every session whose logs or time span changed. Returns the number analysed."""
    if _airgus() is None:
        return 0
    from airgus.alerts import Severity
    from airgus.engine import Engine
    from airgus.rules import default_rules
    from airgus.timeline import Timeline

    archive = Path(cfg["ARCHIVE_PATH"])
    tz = ZoneInfo(cfg.get("TIMEZONE") or "Europe/Berlin")
    windows = _windows()
    logs = db.session.scalars(select(NightLog)).all()
    existing = {a.session_root: a for a in db.session.scalars(select(NightAnalysis))}
    cache: dict[str, list] = {}
    done = 0
    for root in windows:
        mine = logs_for(root, windows, logs)
        if not any(n.kind in ("asiair", "nina") for n in mine):
            if root in existing:
                db.session.delete(existing.pop(root))
            continue
        lo, hi = windows[root]
        fp = hashlib.sha1(repr((lo, hi, [(n.path, n.size, n.mtime) for n in mine])).encode()).hexdigest()
        a = existing.pop(root, None)
        if a and a.fingerprint == fp:
            continue
        streams = []
        for n in mine:
            if n.path not in cache:
                try:
                    cache[n.path] = _read(archive / n.path)
                except Exception:  # noqa: BLE001
                    cache[n.path] = []
            # only the events of this session's time span (in UTC)
            streams.append([e for e in cache[n.path] if lo <= _to_utc(e.time, tz) <= hi])
        engine = Engine(default_rules({r: {"enabled": False} for r in LIVE_ONLY_RULES}))
        tl = Timeline()
        counts: Counter = Counter()
        targets: list[str] = []
        for ev in heapq.merge(*streams, key=lambda e: e.time):
            for al in engine.process(ev):
                tl.add_alert(al)
            tl.add(ev)
            name = type(ev).__name__
            counts[name] += 1
            if name == "SequenceStarted" and ev.target not in targets:
                targets.append(ev.target)
            if name == "AutofocusCompleted":
                counts["AutofocusFailed"] += 0 if ev.success else 1
            if name == "AutoCenterResult":
                counts["AutoCenterFailed"] += 0 if ev.centered else 1
            if name == "TrackingChanged" and not ev.on:
                counts["TrackingOff"] += 1
        a = a or NightAnalysis(session_root=root)
        a.fingerprint = fp
        a.logs = [dict(path=n.path, kind=n.kind) for n in mine]
        a.alerts = [dict(time=_to_utc(al.time, tz).isoformat(), severity=al.severity.name, level=int(al.severity),
                         rule=al.rule, title=al.title, message=al.message) for al in engine.alerts
                    if al.severity >= Severity.WARNING]
        a.summary = dict(
            targets=targets, exposures=counts["ExposureStarted"], sequences=counts["SequenceStarted"],
            flips=counts["MeridianFlipStarted"], flips_ok=counts["MeridianFlipCompleted"],
            flips_failed=counts["MeridianFlipFailed"], autofocus=counts["AutofocusCompleted"],
            autofocus_failed=counts["AutofocusFailed"], centering=counts["AutoCenterResult"],
            centering_failed=counts["AutoCenterFailed"], plate_solves=counts["PlateSolved"],
            star_lost=counts["StarLost"], settle_failed=counts["SettleFailed"],
            guide_star_failed=counts["GuideStarSelectFailed"], tracking_off=counts["TrackingOff"],
            device_drops=counts["DeviceConnection"], app_errors=counts["ApplicationError"],
            dithers=counts["Dither"], guide_frames=counts["GuideFrame"])
        a.timeline = _timeline_utc(tl.to_dict(), tz)
        db.session.add(a)
        done += 1
    for a in existing.values():
        db.session.delete(a)
    db.session.commit()
    log.info("Nachtprotokolle: %d Session(s) ausgewertet", done)
    return done


def _timeline_utc(d: dict, tz: ZoneInfo) -> dict:
    def conv(s):
        return _to_utc(datetime.fromisoformat(s), tz).isoformat() if s else s
    return dict(
        start=conv(d.get("start")), end=conv(d.get("end")),
        exposures=[conv(x) for x in d.get("exposures", [])],
        lanes=[dict(name=ln["name"], segments=[[conv(s[0]), conv(s[1])] + list(s[2:]) for s in ln["segments"]])
               for ln in d.get("lanes", [])],
        stalls=[[conv(a), conv(b)] for a, b in d.get("stalls", [])],
    )


def status(analysis: NightAnalysis | None, high_level: int = 2) -> dict:
    """ok (log, no serious alert) / none (no ASIAIR/NINA log) / error (alerts of severity HIGH or above)."""
    if analysis is None:
        return dict(status="none", reasons=["kein ASIAIR- oder NINA-Log zu dieser Session"], notes=[])
    serious = [a for a in analysis.alerts if a["level"] >= high_level]
    minor = [a for a in analysis.alerts if a["level"] < high_level]

    def fmt(a):
        return f"{a['time'][11:16]} UTC {a['title']}"
    return dict(status="error" if serious else "ok", reasons=[fmt(a) for a in serious][:12],
                notes=[fmt(a) for a in minor][:12])
