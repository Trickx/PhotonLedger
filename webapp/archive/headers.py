"""Read metadata from FITS, XISF and camera RAW files without loading pixel data."""
from __future__ import annotations

import io
import re
import struct
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .rules import FITS_EXT, RAW_EXT, XISF_EXT


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip().strip("'"))
    except ValueError:
        return None


def _str(v):
    if v is None:
        return None
    s = str(v).strip().strip("'").strip()
    return s or None


def _date(v):
    s = _str(v)
    if not s:
        return None
    s = s.replace("Z", "")
    m = re.match(r"(\d{4})[-:](\d{2})[-:](\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?", s)
    if not m:
        return None
    frac = (m.group(7) or "0")[:6].ljust(6, "0")
    try:
        return datetime(*(int(m.group(i)) for i in range(1, 7)), int(frac))
    except ValueError:
        return None


def _sexa(v, hours: bool):
    s = _str(v)
    if not s:
        return None
    parts = re.split(r"[\s:hdm°'\"]+", s.strip())
    parts = [p for p in parts if p]
    try:
        sign = -1 if parts[0].startswith("-") else 1
        vals = [abs(float(p)) for p in parts[:3]] + [0, 0]
        deg = vals[0] + vals[1] / 60 + vals[2] / 3600
        return sign * deg * (15 if hours else 1)
    except (ValueError, IndexError):
        return None


def normalize(cards: dict) -> dict:
    """Map raw header cards to the fields PhotonLedger uses."""
    g = {k.upper(): v for k, v in cards.items()}
    ra = _sexa(g.get("OBJCTRA"), hours=True)
    dec = _sexa(g.get("OBJCTDEC"), hours=False)
    if ra is None:
        ra = _num(g.get("RA"))
    if dec is None:
        dec = _num(g.get("DEC"))
    naxis3 = _num(g.get("NAXIS3"))
    return {
        "imagetyp": _str(g.get("IMAGETYP") or g.get("FRAME") or g.get("FRAMETYP")),
        "exptime": _num(g.get("EXPTIME") if g.get("EXPTIME") is not None else g.get("EXPOSURE")),
        "date_obs": _date(g.get("DATE-OBS") or g.get("DATE-LOC") or g.get("DATE")),
        "camera": _str(g.get("INSTRUME")),
        "telescope": _str(g.get("TELESCOP")),
        "focal": _num(g.get("FOCALLEN")),
        "filter": _str(g.get("FILTER")),
        "gain": _num(g.get("GAIN")),
        "iso": _num(g.get("ISOSPEED") or g.get("ISO")),
        "temp": _num(g.get("CCD-TEMP") if g.get("CCD-TEMP") is not None else g.get("CCDTEMP")),
        "binning": int(_num(g.get("XBINNING")) or 1),
        "width": int(_num(g.get("NAXIS1")) or 0) or None,
        "height": int(_num(g.get("NAXIS2")) or 0) or None,
        "naxis3": int(naxis3) if naxis3 else None,
        "ra": ra,
        "dec": dec,
        "object": _str(g.get("OBJECT")),
        "program": _str(g.get("PROGRAM") or g.get("SWCREATE") or g.get("CREATOR")),
        "bayer": _str(g.get("BAYERPAT")),
    }


# --------------------------------------------------------------------------- FITS

def read_fits_cards(path: Path) -> dict:
    """Read only the primary header (2880-byte blocks) - no astropy needed, fast on NAS shares."""
    cards: dict = {}
    with open(path, "rb") as fh:
        for _ in range(200):                       # max 200 blocks = 7200 cards
            block = fh.read(2880)
            if len(block) < 2880:
                break
            for i in range(0, 2880, 80):
                card = block[i:i + 80].decode("ascii", "replace")
                key = card[:8].strip()
                if key == "END":
                    return cards
                if card[8:10] != "= " or not key:
                    continue
                raw = card[10:]
                if raw.lstrip().startswith("'"):
                    m = re.match(r"\s*'((?:[^']|'')*)'", raw)
                    value = m.group(1).replace("''", "'").rstrip() if m else raw
                else:
                    value = raw.split("/", 1)[0].strip()
                    if value in ("T", "F"):
                        value = value == "T"
                cards[key] = value
    if not cards:
        raise ValueError("no FITS header found")
    return cards


# --------------------------------------------------------------------------- XISF

def read_xisf_cards(path: Path) -> dict:
    with open(path, "rb") as fh:
        sig = fh.read(8)
        if sig != b"XISF0100":
            raise ValueError("not an XISF file")
        length = struct.unpack("<I", fh.read(4))[0]
        fh.read(4)
        xml = fh.read(length)
    root = ET.fromstring(xml)
    cards: dict = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "FITSKeyword":
            name = el.get("name")
            if name and name not in cards:
                cards[name] = el.get("value", "").strip().strip("'").strip()
        elif tag == "Image" and "NAXIS1" not in cards:
            geo = (el.get("geometry") or "").split(":")
            if len(geo) >= 2:
                cards["NAXIS1"], cards["NAXIS2"] = geo[0], geo[1]
                if len(geo) >= 3 and geo[2] != "1":
                    cards["NAXIS3"] = geo[2]
    return cards


# --------------------------------------------------------------------------- RAW

def _raf_embedded_jpeg(path: Path) -> bytes | None:
    with open(path, "rb") as fh:
        head = fh.read(100)
        if not head.startswith(b"FUJIFILMCCD-RAW"):
            return None
        off, length = struct.unpack(">II", head[84:92])
        fh.seek(off)
        return fh.read(length)


def read_raw_cards(path: Path) -> dict:
    import exifread

    data = _raf_embedded_jpeg(path) if path.suffix.lower() == ".raf" else None
    stream = io.BytesIO(data) if data else open(path, "rb")
    try:
        tags = exifread.process_file(stream, details=False)
    finally:
        stream.close()

    def tag(*names):
        for n in names:
            if n in tags:
                return str(tags[n])
        return None

    def ratio(s):
        if not s:
            return None
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b) if float(b) else None
        return _num(s)

    cards = {
        "IMAGETYP": None,
        "EXPTIME": ratio(tag("EXIF ExposureTime")),
        "DATE-OBS": (tag("EXIF DateTimeOriginal") or "").replace(":", "-", 2) or None,
        "INSTRUME": " ".join(x for x in (tag("Image Make"), tag("Image Model")) if x) or None,
        "FOCALLEN": ratio(tag("EXIF FocalLength")),
        "ISOSPEED": tag("EXIF ISOSpeedRatings", "EXIF PhotographicSensitivity"),
        "TELESCOP": tag("EXIF LensModel"),
    }
    make = (tag("Image Make") or "").strip()
    model = (tag("Image Model") or "").strip()
    if model.lower().startswith(make.lower()):
        cards["INSTRUME"] = model or None
    return {k: v for k, v in cards.items() if v is not None}


# --------------------------------------------------------------------------- entry

CONTENT_CARD = "PL_CONTENT"     # raw card set when the content does not match the file extension


def sniff(path: Path) -> str | None:
    """File format from the first bytes: "FITS", "XISF", "RAF" (Fuji RAW) or None (other/unknown)."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(b"SIMPLE  ="):
        return "FITS"
    if head.startswith(b"XISF0100"):
        return "XISF"
    if head.startswith(b"FUJIFILMCCD-RAW"):
        return "RAF"
    return None


def _format_of_ext(ext: str) -> str:
    return "FITS" if ext in FITS_EXT else "XISF" if ext in XISF_EXT else "RAF" if ext == ".raf" else "RAW"


def read_header(path: Path) -> tuple[dict, dict]:
    """Return (normalized fields, raw cards). The format is taken from the content, so that a FITS file
    with a wrong extension (e.g. renamed to .raf) is still read; the raw cards then name the content."""
    ext = path.suffix.lower()
    if ext not in FITS_EXT and ext not in XISF_EXT and ext not in RAW_EXT:
        raise ValueError(f"unsupported file type {ext}")
    expected, content = _format_of_ext(ext), sniff(path)
    fmt = content or expected
    if content is None and expected in ("FITS", "XISF", "RAF"):
        raise ValueError(f"Inhalt ist keine {expected}-Datei (Dateiendung {ext})")
    if fmt == "FITS":
        cards = read_fits_cards(path)
    elif fmt == "XISF":
        cards = read_xisf_cards(path)
    else:
        cards = read_raw_cards(path)
    if content and content != expected:
        cards[CONTENT_CARD] = content
    cards = {k: v for k, v in cards.items() if k not in ("COMMENT", "HISTORY", "")}
    return normalize(cards), {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in cards.items()}
