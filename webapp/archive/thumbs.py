"""Phase 3: render thumbnails and previews of versions into DATA_PATH (never into the archive)."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import select

from webapp import db
from webapp.models import Version

log = logging.getLogger("photonledger.thumbs")
Image.MAX_IMAGE_PIXELS = 400_000_000


def _to_8bit(arr: np.ndarray) -> np.ndarray:
    if arr.dtype == np.uint8:
        return arr
    a = arr.astype(np.float32)
    if np.issubdtype(arr.dtype, np.integer):
        a /= float(np.iinfo(arr.dtype).max)
    else:
        hi = float(np.nanmax(a)) if a.size else 1.0
        if hi > 1.0:
            a /= hi
    return (np.clip(np.nan_to_num(a), 0, 1) * 255 + 0.5).astype(np.uint8)


def _load_tiff(path: Path, max_side: int) -> Image.Image:
    import tifffile

    arr = tifffile.imread(path)
    if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[-1] not in (3, 4):
        arr = np.moveaxis(arr, 0, -1)
    if arr.ndim == 3 and arr.shape[-1] == 4:
        arr = arr[..., :3]
    step = max(1, int(max(arr.shape[:2]) // (max_side * 2)))
    arr = _to_8bit(np.ascontiguousarray(arr[::step, ::step]))
    return Image.fromarray(arr)


def load_image(path: Path, max_side: int) -> tuple[Image.Image, tuple[int, int]]:
    if path.suffix.lower() in (".tif", ".tiff"):
        try:
            with Image.open(path) as im:
                size = im.size
                if im.mode in ("RGB", "L", "RGBA", "P"):
                    im.draft("RGB", (max_side, max_side))
                    return im.convert("RGB"), size
        except Exception:  # noqa: BLE001 - 16/32 bit TIFFs: fall back to tifffile
            pass
        img = _load_tiff(path, max_side)
        return img, img.size
    im = Image.open(path)
    size = im.size
    im.draft("RGB", (max_side, max_side))
    return im.convert("RGB"), size


def thumb_name(rel_path: str, kind: str) -> str:
    h = hashlib.sha1(rel_path.encode()).hexdigest()[:24]
    return f"{kind}/{h[:2]}/{h}.jpg"


def render(cfg: dict, limit: int | None = None) -> int:
    archive = Path(cfg["ARCHIVE_PATH"])
    data = Path(cfg["DATA_PATH"])
    todo = db.session.scalars(
        select(Version).where(Version.thumb.is_(None), Version.error.is_(None))).all()
    done = 0
    for v in todo[:limit] if limit else todo:
        try:
            img, size = load_image(archive / v.path, cfg["PREVIEW_SIZE"])
            v.width, v.height = size
            for kind, side in (("previews", cfg["PREVIEW_SIZE"]), ("thumbs", cfg["THUMB_SIZE"])):
                name = thumb_name(v.path, kind)
                out = data / name
                out.parent.mkdir(parents=True, exist_ok=True)
                im = img.copy()
                im.thumbnail((side, side), Image.LANCZOS)
                tmp = out.with_suffix(".tmp")
                im.save(tmp, "JPEG", quality=85 if kind == "previews" else 80, optimize=True)
                os.replace(tmp, out)
                if kind == "thumbs":
                    v.thumb = name
                else:
                    v.preview = name
        except Exception as e:  # noqa: BLE001
            v.error = f"{type(e).__name__}: {e}"[:500]
            log.warning("Vorschau fehlgeschlagen: %s (%s)", v.path, v.error)
        done += 1
        db.session.commit()
    return done


def cleanup(cfg: dict) -> int:
    """Delete rendered images whose source is gone (thumbnails, previews, SAD map cache, temp files)."""
    from webapp.models import SadFrame

    from .sad import map_cache_name

    data = Path(cfg["DATA_PATH"])
    keep: set[str] = set()
    for thumb, preview in db.session.execute(select(Version.thumb, Version.preview)):
        keep.update(x for x in (thumb, preview) if x)
    for (maps,) in db.session.execute(select(SadFrame.maps)):
        for rel in (maps or {}).values():
            keep.add(map_cache_name(rel, False))
            keep.add(map_cache_name(rel, True))
    removed = 0
    for sub in ("thumbs", "previews", "sad"):
        base = data / sub
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(data).as_posix()
            # files younger than 5 min may be written right now by the web server (SAD maps on demand)
            if rel not in keep and time.time() - f.stat().st_mtime > 300:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        for d in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
            try:
                d.rmdir()                        # only succeeds for empty folders
            except OSError:
                pass
    if removed:
        log.info("%d verwaiste Vorschaubild(er) gelöscht", removed)
    return removed
