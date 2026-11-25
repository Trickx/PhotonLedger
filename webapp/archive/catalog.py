"""Offline deep-sky catalog (OpenNGC): look up objects by id and search the field of view."""
from __future__ import annotations

import csv
import gzip
import io
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

TYPES_DE = {
    "G": "Galaxie", "GPair": "Galaxienpaar", "GTrpl": "Galaxientripel", "GGroup": "Galaxiengruppe",
    "PN": "Planetarischer Nebel", "HII": "Emissionsnebel", "EmN": "Emissionsnebel", "Neb": "Nebel",
    "RfN": "Reflexionsnebel", "SNR": "Supernovaüberrest", "DrkN": "Dunkelnebel",
    "OCl": "Offener Sternhaufen", "GCl": "Kugelsternhaufen", "Cl+N": "Sternhaufen mit Nebel",
    "*Ass": "Sternassoziation", "Nova": "Nova", "*": "Stern", "**": "Doppelstern", "Other": "Sonstiges",
}
# not shown as "objects in field"
_SKIP_IN_FIELD = {"*", "**", "Dup", "NonEx", "Other", "Nova"}

_PREFIX_DISPLAY = {
    "NGC": "NGC", "IC": "IC", "M": "M", "B": "B", "C": "C", "CL": "Cl", "ESO": "ESO", "H": "H",
    "HCG": "HCG", "MEL": "Mel", "MWSC": "MWSC", "PGC": "PGC", "UGC": "UGC", "UGCA": "UGCA",
    "LBN": "LBN", "LDN": "LDN", "SH2": "Sh2", "VDB": "vdB", "CR": "Cr", "ARP": "Arp", "TR": "Tr",
}
# cross identifiers worth showing (the rest are survey numbers like 2MASX, IRAS, MCG, SDSS)
_USEFUL_XIDS = {"PGC", "UGC", "UGCA", "LBN", "LDN", "SH2", "VDB", "CR", "MEL", "ARP", "TR", "B", "C", "HCG"}


def norm_key(cid: str) -> str | None:
    """'NGC 0224' / 'NGC224' / 'M 31' / 'SH 2-155' / 'Sh2-155' -> 'NGC224', 'M31', 'SH2155'."""
    s = re.sub(r"[\s_]+", "", cid.strip().upper())
    s = s.replace("SH2-", "SH2").replace("SH-2", "SH2")
    m = re.match(r"^([A-Z]+?)-?0*(\d[\d\-]*[A-Z]?)$", s)
    if not m:
        return None
    prefix, num = m.group(1), m.group(2).lstrip("0") or "0"
    if prefix == "SH" and num.startswith("2"):
        prefix, num = "SH2", num[1:].lstrip("-0")
    return prefix + num.replace("-", "")


def display_id(cid: str) -> str:
    s = re.sub(r"\s+", " ", cid.strip())
    m2 = re.match(r"^SH\s*-?\s*2\s*-\s*0*(\d+)$", s, re.I)
    if m2:
        return f"Sh2-{int(m2.group(1))}"
    m = re.match(r"^([A-Za-z]+)\s*-?\s*0*(\d+)([A-Za-z]?)$", s)
    if not m:
        return s
    prefix = m.group(1).upper()
    return f"{_PREFIX_DISPLAY.get(prefix, m.group(1))} {int(m.group(2))}{m.group(3)}"


@dataclass
class Entry:
    id: str                       # display id, e.g. "NGC 3627"
    type: str
    ra: float | None              # degrees
    dec: float | None
    const: str | None
    major: float | None           # arcmin
    minor: float | None
    mag: float | None
    messier: str | None = None
    names: list[str] = field(default_factory=list)
    xids: list[str] = field(default_factory=list)   # useful cross ids (display form)
    dup_of: str | None = None

    @property
    def type_de(self) -> str:
        return TYPES_DE.get(self.type, self.type)

    @property
    def all_ids(self) -> list[str]:
        out: list[str] = []
        for x in ([self.messier] if self.messier else []) + [self.id] + self.xids:
            if x not in out:
                out.append(x)
        return out


def _hms(s: str) -> float | None:
    if not s:
        return None
    h, m, sec = s.split(":")
    return (int(h) + int(m) / 60 + float(sec) / 3600) * 15


def _dms(s: str) -> float | None:
    if not s:
        return None
    sign = -1 if s.startswith("-") else 1
    d, m, sec = s.lstrip("+-").split(":")
    return sign * (int(d) + int(m) / 60 + float(sec) / 3600)


def _f(s: str) -> float | None:
    try:
        return float(s) if s else None
    except ValueError:
        return None


class Catalog:
    def __init__(self, rows: list[dict]):
        self.entries: list[Entry] = []
        self.by_key: dict[str, Entry] = {}
        for r in rows:
            name = r["Name"]
            e = Entry(
                id=display_id(name), type=r["Type"], ra=_hms(r["RA"]), dec=_dms(r["Dec"]),
                const=r["Const"] or None, major=_f(r["MajAx"]), minor=_f(r["MinAx"]),
                mag=_f(r["V-Mag"]) or _f(r["B-Mag"]),
                messier=f"M {int(r['M'])}" if r.get("M") else None,
                names=[n.strip() for n in (r["Common names"] or "").split(",") if n.strip()],
            )
            for x in (r["Identifiers"] or "").split(","):
                k = norm_key(x)
                if k and re.match(r"^[A-Z]+", k).group(0) in _USEFUL_XIDS:
                    e.xids.append(display_id(x))
            if e.type == "Dup":
                first = lambda v: v.split(",")[0].strip().lstrip("0")  # noqa: E731 - "1530A", "0224"
                e.dup_of = ("NGC " + first(r["NGC"])) if r.get("NGC") else (
                    ("IC " + first(r["IC"])) if r.get("IC") else None)
                if e.messier and e.dup_of is None:
                    e.dup_of = e.messier       # e.g. M102 -> M 101
            self.entries.append(e)
        # primary keys first, then messier, then cross ids (never overwrite a primary)
        for e in self.entries:
            self.by_key.setdefault(norm_key(e.id), e)
        for e in self.entries:
            if e.messier and e.type != "Dup":
                self.by_key.setdefault(norm_key(e.messier), e)
        for e in self.entries:
            if e.type != "Dup":
                for x in e.xids:
                    self.by_key.setdefault(norm_key(x), e)
        self._sky = [e for e in self.entries if e.ra is not None and e.type not in _SKIP_IN_FIELD]

    @classmethod
    def from_files(cls, paths: list[Path]) -> "Catalog":
        rows: list[dict] = []
        for p in paths:
            raw = gzip.decompress(p.read_bytes()).decode("utf-8")
            rows.extend(csv.DictReader(io.StringIO(raw), delimiter=";"))
        return cls(rows)

    def lookup(self, cid: str) -> Entry | None:
        k = norm_key(cid)
        e = self.by_key.get(k) if k else None
        hops = 0
        while e is not None and e.type == "Dup" and e.dup_of and hops < 3:
            e = self.by_key.get(norm_key(e.dup_of))
            hops += 1
        return e

    def within(self, ra: float, dec: float, radius_deg: float, limit: int = 300) -> list[tuple[float, Entry]]:
        """Objects whose centre lies within `radius_deg` of (ra, dec), nearest first."""
        out = []
        cos_r = math.cos(math.radians(radius_deg))
        sd, cd = math.sin(math.radians(dec)), math.cos(math.radians(dec))
        for e in self._sky:
            if abs(e.dec - dec) > radius_deg:
                continue
            c = sd * math.sin(math.radians(e.dec)) + cd * math.cos(math.radians(e.dec)) * math.cos(math.radians(e.ra - ra))
            if c >= cos_r:
                out.append((math.degrees(math.acos(min(1.0, c))), e))
        out.sort(key=lambda t: t[0])
        return out[:limit]


def separation(ra1, dec1, ra2, dec2) -> float:
    """Angular distance in degrees."""
    r = math.radians
    c = (math.sin(r(dec1)) * math.sin(r(dec2))
         + math.cos(r(dec1)) * math.cos(r(dec2)) * math.cos(r(ra1 - ra2)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return Catalog.from_files([DATA / "openngc-ngc.csv.gz", DATA / "openngc-addendum.csv.gz"])
