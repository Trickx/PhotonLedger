"""Several archives side by side.

Configured as ARCHIVES = {"<name>": "<path>", …} (config.json or FLASK_ARCHIVES as JSON). Every stored
path then starts with the archive name ("Astro/M42 (Orion Nebula)/2025-02-19/Lights/…"); targets with
the same catalogue number are merged across archives. Without ARCHIVES there is one unnamed archive at
ARCHIVE_PATH and paths have no prefix (as before).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import rules


@dataclass(frozen=True)
class Archive:
    name: str          # "" = the single unnamed archive (ARCHIVE_PATH)
    path: Path

    @property
    def prefix(self) -> str:
        return f"{self.name}/" if self.name else ""

    def rel(self, full: str) -> str:
        """Absolute path below this archive -> stored (prefixed) path."""
        r = os.path.relpath(full, self.path).replace(os.sep, "/")
        r = "" if r == "." else r
        return (self.prefix + r) if r else self.name


def configured(cfg) -> list[Archive]:
    raw = cfg.get("ARCHIVES")
    if isinstance(raw, str) and raw.strip():
        raw = json.loads(raw)
    if raw:
        items = raw.items() if isinstance(raw, dict) else ((a["name"], a["path"]) for a in raw)
        out = []
        for name, path in items:
            name = str(name).strip().strip("/")
            if not name or "/" in name:
                raise ValueError(f"Ungültiger Archivname: {name!r}")
            out.append(Archive(name, Path(path)))
        return out
    return [Archive("", Path(cfg["ARCHIVE_PATH"]))]


def split(cfg, rel: str) -> tuple[Archive | None, str]:
    """Stored path -> (archive, path inside the archive); archive None if the prefix is unknown."""
    archives = configured(cfg)
    if len(archives) == 1 and not archives[0].name:
        return archives[0], rel
    first, _, rest = rel.partition("/")
    for a in archives:
        if a.name == first:
            return a, rest
    return None, rel


def resolve(cfg, rel: str) -> Path | None:
    """Stored path -> absolute path (None if it belongs to no configured archive)."""
    a, inner = split(cfg, rel)
    return None if a is None else (a.path / inner if inner else a.path)


def to_rel(cfg, full) -> str:
    """Absolute path in one of the archives -> stored path."""
    full = os.path.abspath(full)
    for a in sorted(configured(cfg), key=lambda a: -len(str(a.path))):
        base = os.path.abspath(a.path)
        if full == base or full.startswith(base.rstrip(os.sep) + os.sep):
            return a.rel(full)
    raise ValueError(f"{full} liegt in keinem Archiv")


def inner(cfg, rel: str) -> str:
    """Stored path without the archive name (for rules that look at the folder layout)."""
    return split(cfg, rel)[1]


def classify(archive: Archive, inner_rel: str, ignore=None) -> rules.Classified | None:
    """Classify a path inside an archive; the folders in the result carry the archive prefix."""
    c = rules.classify(inner_rel, ignore)
    if c is None or not archive.name:
        return c
    p = archive.prefix
    return rules.Classified(c.kind, p + c.target_folder, p + c.session_root if c.session_root else None,
                            c.frame_type, c.rejected)


def classify_stored(cfg, rel: str, ignore=None) -> rules.Classified | None:
    a, inner_rel = split(cfg, rel)
    return classify(a, inner_rel, ignore) if a else None


def state(cfg) -> list[dict]:
    """Per archive: name, path and whether it is reachable ("ok", "empty", "missing")."""
    out = []
    for a in configured(cfg):
        try:
            st = "ok" if any(a.path.iterdir()) else "empty"
        except OSError:
            st = "missing"
        out.append(dict(name=a.name, path=str(a.path), state=st))
    return out
