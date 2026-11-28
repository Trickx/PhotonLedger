"""Annotation overlays (SVG, e.g. from PixInsight AnnotateImage) for the finals of a target.

An SVG in a Finals folder (or below it, e.g. Finals/Overlays) belongs to
  1. the versions with the same file name (without extension), else
  2. the versions of the same target with exactly its pixel size (viewBox / width × height).
Several exports of the same image (JPG and Astrobin JPG) then share one overlay.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_SIZE_RE = re.compile(rb"<svg\b[^>]*>", re.S)
_ATTR_RE = re.compile(rb'\b(viewBox|width|height)\s*=\s*["\']([^"\']*)["\']')


@lru_cache(maxsize=512)
def _size_cached(full: str, mtime: float) -> tuple[int, int] | None:
    try:
        with open(full, "rb") as fh:
            head = fh.read(16384)
    except OSError:
        return None
    m = _SIZE_RE.search(head)
    if not m:
        return None
    attrs = {k.decode(): v.decode() for k, v in _ATTR_RE.findall(m.group(0))}
    try:
        if "viewBox" in attrs:
            _, _, w, h = (float(x) for x in attrs["viewBox"].replace(",", " ").split())
        else:
            w, h = float(re.sub("px$", "", attrs["width"])), float(re.sub("px$", "", attrs["height"]))
    except (KeyError, ValueError):
        return None
    return round(w), round(h)


def svg_size(full: Path) -> tuple[int, int] | None:
    """Pixel size an SVG overlay was made for, None if unreadable."""
    try:
        return _size_cached(str(full), full.stat().st_mtime)
    except OSError:
        return None


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def match(versions, svgs: list[tuple[str, tuple[int, int] | None]]) -> tuple[dict[int, str], list[str]]:
    """versions: Version rows of one target; svgs: (stored path, size). Returns
    ({version id: svg path}, [svg paths that fit no version])."""
    out: dict[int, str] = {}
    unmatched = []
    images = [v for v in versions if not v.is_video]
    for path, size in sorted(svgs):
        hit = [v for v in images if _stem(v.path) == _stem(path)]
        if not hit and size:
            hit = [v for v in images if (v.width, v.height) == size]
        for v in hit:
            out.setdefault(v.id, path)
        if not hit:
            unmatched.append(path)
    return out, unmatched


def target_overlays(cfg, t) -> tuple[dict[int, str], list[tuple[str, tuple[int, int] | None]]]:
    """Overlays of a target: ({version id: svg path}, [(unmatched svg path, size)])."""
    from sqlalchemy import select

    from webapp import db
    from webapp.models import Intermediate

    from . import archives
    folders = t.folders or []
    if not folders:
        return {}, []
    rows = db.session.scalars(select(Intermediate.path).where(Intermediate.target_folder.in_(folders),
                                                             Intermediate.path.ilike("%.svg"))).all()
    svgs = []
    for p in rows:
        if "/finals/" not in f"/{p.lower()}":
            continue
        full = archives.resolve(cfg, p)
        svgs.append((p, svg_size(full) if full is not None else None))
    if not svgs:
        return {}, []
    found, unmatched = match(t.versions, svgs)
    sizes = dict(svgs)
    return found, [(p, sizes[p]) for p in unmatched]
