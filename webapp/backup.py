"""Daily copy of the database (manual settings like rigs, covers and acknowledged checks cannot be
rebuilt from the archive). Made with SQLite's online backup, so it is consistent while the web app runs."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("photonledger.backup")
PREFIX = "photonledger-"


def backup_dir(cfg) -> Path:
    return Path(cfg["DATA_PATH"]) / "backups"


def _db_file(cfg) -> Path | None:
    uri = cfg.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("sqlite:///") or uri.endswith(":memory:"):
        return None
    return Path(uri[len("sqlite:///"):])


def daily(cfg, today: date | None = None) -> Path | None:
    """Copy the database once per day to data/backups/photonledger-YYYY-MM-DD.db and keep the newest
    BACKUP_KEEP copies (0 = no backups). Returns the new file, or None if nothing was done."""
    keep = int(cfg.get("BACKUP_KEEP", 14))
    src = _db_file(cfg)
    if keep <= 0 or src is None or not src.is_file():
        return None
    folder = backup_dir(cfg)
    out = folder / f"{PREFIX}{(today or date.today()).isoformat()}.db"
    if out.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    a, b = sqlite3.connect(src), sqlite3.connect(tmp)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()
    os.replace(tmp, out)
    for old in sorted(folder.glob(f"{PREFIX}*.db"))[:-keep]:
        old.unlink()
    log.info("Datenbank gesichert: %s", out.name)
    return out


def listing(cfg) -> list[dict]:
    """Existing backups, newest first."""
    folder = backup_dir(cfg)
    if not folder.is_dir():
        return []
    files = sorted(folder.glob(f"{PREFIX}*.db"), reverse=True)
    return [dict(name=f.name, size=f.stat().st_size, time=datetime.fromtimestamp(f.stat().st_mtime))
            for f in files]
