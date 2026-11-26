"""Background indexer: python -m webapp.indexer [--once]

Runs as its own process (container service "indexer"), next to the web server.
The archive is only ever read; everything is written to DATA_PATH.

Coordination with the web server through files in DATA_PATH:
  indexer.lock        held (flock) while a run is in progress - never two runs at once
  indexer.heartbeat   JSON {pid, interval, next_run, updated}, refreshed every few seconds by the loop
  indexer.request     written by the web server ("Jetzt scannen"); the loop starts a run right away
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from webapp import create_app, db
from webapp import backup
from webapp.archive import archives, checks, derive, nightlog, scanner, thumbs
from webapp.models import IndexRun

log = logging.getLogger("photonledger")
POLL_S = 5                      # how often the waiting loop looks for a request
HEARTBEAT_FRESH_S = 30          # older heartbeat = no indexer loop running


def _files(cfg) -> dict[str, Path]:
    d = Path(cfg["DATA_PATH"])
    return {"lock": d / "indexer.lock", "heartbeat": d / "indexer.heartbeat", "request": d / "indexer.request"}


@contextmanager
def run_lock(cfg):
    """Exclusive lock for one run; yields False if another process is indexing."""
    path = _files(cfg)["lock"]
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def is_running(cfg) -> bool:
    with run_lock(cfg) as got:
        return not got


def heartbeat(cfg) -> dict | None:
    """The loop's last heartbeat if it is fresh, else None (no loop running)."""
    try:
        hb = json.loads(_files(cfg)["heartbeat"].read_text())
    except (OSError, ValueError):
        return None
    if time.time() - hb.get("updated", 0) > HEARTBEAT_FRESH_S:
        return None
    return hb


def request_run(cfg) -> None:
    p = _files(cfg)["request"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().isoformat())


def _write_heartbeat(cfg, interval: int, next_run: datetime | None, state: str) -> None:
    p = _files(cfg)["heartbeat"]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(pid=os.getpid(), interval=interval, state=state, updated=time.time(),
                                   next_run=next_run.isoformat() if next_run else None)))
    os.replace(tmp, p)


def _take_request(cfg) -> bool:
    p = _files(cfg)["request"]
    if p.exists():
        p.unlink(missing_ok=True)
        return True
    return False


def run_once(app) -> IndexRun | None:
    cfg = app.config
    with run_lock(cfg) as got:
        if not got:
            log.info("Ein anderer Indexlauf ist aktiv – übersprungen")
            return None
        return _run(app)


def _run(app) -> IndexRun:
    cfg = app.config
    run = IndexRun(started=scanner.now(), phase="scan")
    db.session.add(run)
    db.session.commit()
    saved = None
    try:
        saved = backup.daily(cfg)                 # before the run changes anything
    except Exception as e:  # noqa: BLE001 - a failed backup must not stop the scan
        log.warning("Datenbanksicherung fehlgeschlagen: %s", e)
    try:
        log.info("Scan von %s", ", ".join(f"{a.name} ({a.path})" if a.name else str(a.path)
                                          for a in archives.configured(cfg)))
        scanner.scan(cfg, run)
        run.phase = "derive"
        db.session.commit()
        derive.derive(cfg)
        run.phase = "nightlogs"
        db.session.commit()
        nightlog.analyse(cfg)
        run.phase = "checks"
        db.session.commit()
        checks.run(cfg)
        run.phase = "thumbs"
        db.session.commit()
        n = thumbs.render(cfg)
        cleaned = thumbs.cleanup(cfg)
        run.phase = "done"
        notes = [run.message] if run.message else []
        if saved:
            notes.append(f"Datenbank gesichert ({saved.name})")
        if n:
            notes.append(f"{n} Vorschaubilder erzeugt")
        if cleaned:
            notes.append(f"{cleaned} verwaiste Vorschaubilder gelöscht")
        run.message = " · ".join(notes) or None
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        run = db.session.get(IndexRun, run.id)
        run.phase = "failed"
        run.message = str(e) if type(e) is RuntimeError else f"{type(e).__name__}: {e}"
        log.exception("Indexlauf fehlgeschlagen")
    except (SystemExit, KeyboardInterrupt):
        # container stopped during a run: record it (else the run would show "läuft" forever)
        db.session.rollback()
        run = db.session.get(IndexRun, run.id)
        run.phase, run.message, run.finished = "failed", "abgebrochen – Indexer wurde beendet", scanner.now()
        db.session.commit()
        log.warning("Indexlauf %s abgebrochen", run.id)
        raise
    run.finished = scanner.now()
    db.session.commit()
    log.info("Lauf %s: %s – %d gesehen, %d gelesen, %d Fehler",
             run.id, run.phase, run.files_seen, run.files_read, run.errors)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description="PhotonLedger indexer")
    parser.add_argument("--once", action="store_true", help="einmal scannen und beenden")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # "docker stop": end at once while waiting, record an interrupted run (also as PID 1 without init)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    app = create_app()
    with app.app_context():
        cfg = app.config
        if args.once:
            _take_request(cfg)
            run_once(app)
            return
        while True:
            interval = int(cfg["SCAN_INTERVAL"])
            _take_request(cfg)
            _write_heartbeat(cfg, interval, None, "running")
            run_once(app)
            db.session.remove()
            next_run = datetime.now() + timedelta(seconds=interval)
            log.info("Nächster Scan um %s", next_run.strftime("%H:%M:%S"))
            while datetime.now() < next_run:
                _write_heartbeat(cfg, interval, next_run, "waiting")
                if _take_request(cfg):
                    log.info("Lauf von Hand angefordert")
                    break
                time.sleep(POLL_S)


if __name__ == "__main__":
    main()
