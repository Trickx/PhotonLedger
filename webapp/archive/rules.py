"""Rules that turn the archive's folder structure into targets, sessions and frames.

Everything here is pure (no file access, no database) so it can be unit tested
against real folder names.

Expected layout (all parts optional except the target folder):

    <target folder>/[<session folder>/]<frame folder>/[REJECTED/]<file>

    M97, M108 (Surfboard, Owl)/2026-04-28/Lights/x.fit
    M51 (Whirlpool Galaxy)/Lights/x.fit                (no session folder)
    IC1396 (Elephant's Trunk)/2026-07-08 DWARF3/Lights/x.fits
    NGC7000 (NA Nebula)/NGC7000 (NA Nebula)[Canon]/Lights/x.fit
    Mineral Moon/2026-05-25-100/x.fit                  (frames without frame folder)

The old layout with a year folder above the target (2026/M51 …/…) is still understood, so a
leftover year folder does not turn into a target named "2026"; the checks report it.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

FITS_EXT = {".fit", ".fits", ".fts"}
XISF_EXT = {".xisf"}
RAW_EXT = {".raf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2"}
FRAME_EXT = FITS_EXT | XISF_EXT | RAW_EXT
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

FRAME_DIRS = {
    "lights": "light", "light": "light",
    "flats": "flat", "flat": "flat",
    "darks": "dark", "dark": "dark",
    "biases": "bias", "bias": "bias", "offsets": "bias", "offset": "bias",
    "darkflats": "darkflat", "darkflat": "darkflat", "flatdarks": "darkflat",
}
FRAME_TYPES = ("light", "flat", "dark", "bias", "darkflat")

REJECT_DIRS = {"rejected", "reject", "bad", "trash", "verworfen"}

# Folders holding processed results. Images in here are "versions",
# FITS/XISF in here are intermediates and are ignored.
VERSION_DIR_PATTERNS = [
    r"finals?", r"processing", r"processed", r"pi\d*", r"pi[-_ ].*", r"pixinsight",
    r"master\d*", r"masters", r"dxo", r"selected", r"exports?", r"results?",
]
# Folders that never contain anything of interest.
SKIP_DIR_PATTERNS = [
    r".*\.pxiproject", r"logs?", r"sad",
    r"cropped pngs", r"uncrop png", r"kopie", r"copy", r"@eadir", r"#recycle", r"\..*",
]

_VERSION_RE = re.compile("^(?:" + "|".join(VERSION_DIR_PATTERNS) + ")$", re.I)
_SKIP_RE = re.compile("^(?:" + "|".join(SKIP_DIR_PATTERNS) + ")$", re.I)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")

# --- WBPP (PixInsight WeightedBatchPreprocessing) and other stacking intermediates
WBPP_STEP_DIRS = {"calibrated", "cosmetized", "debayered", "registered", "measured", "local_normalization",
                  "fastintegration", "drizzle", "siril registered"}
WBPP_EXT = {".xnml", ".xdrz"}          # local normalization / drizzle data
_MASTER_DIR_RE = re.compile(r"^master\d*$", re.I)
_CAL_MASTER_RE = re.compile(r"^master(dark|flat|bias|darkflat|flatdark)[_\-. ]", re.I)
_WBPP_STEP_NAME = re.compile(r"^LN_Reference_", re.I)   # local normalization reference
_WBPP_LIGHT_TAIL = re.compile(r"^(RGB|Mono|CFA|[A-Z]+-.+)$")


def is_wbpp_masterlight(name: str) -> bool:
    """masterLight_<WBPP keywords>[_drizzle_Nx][_fastIntegration][_autocrop].<ext> - the results to keep."""
    stem, _, ext = name.rpartition(".")
    if not stem.startswith("masterLight_") or ext.lower() not in ("xisf", "fit", "fits", "fts", "tif", "tiff"):
        return False
    for suffix in (r"_autocrop$", r"_fastIntegration$", r"_drizzle_\d+x$"):
        stem = re.sub(suffix, "", stem)
    return bool(_WBPP_LIGHT_TAIL.match(stem.rsplit("_", 1)[-1]))


def wbpp_kind(rel_path: str) -> str | None:
    """'ok' (master light / autocrop), 'step' (calibrated, registered … data), 'calibration_master',
    'other' (anything else in a WBPP master folder) or None (not WBPP related)."""
    parts = rel_path.split("/")
    name, dirs = parts[-1], parts[:-1]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in WBPP_EXT or any(d.lower() in WBPP_STEP_DIRS for d in dirs) or _WBPP_STEP_NAME.match(name):
        return "step"
    if _CAL_MASTER_RE.match(name):
        return "calibration_master"
    if is_wbpp_masterlight(name):
        return "ok"
    if dirs and _MASTER_DIR_RE.match(dirs[-1]):
        return "other"
    return None
_SUBFRAME_NAME_RE = re.compile(r"^(light|flat|dark|bias|offset|darkflat)[_\- ]", re.I)
_DATE_RE = re.compile(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")


PROCESSING_DIR = "Processing"   # the one folder name where intermediate results belong
FINALS_DIR = "Finals"           # results of a target or a session, in any format


def in_processing(rel_path: str) -> bool:
    """True if a folder named exactly 'Processing' is part of the path."""
    return PROCESSING_DIR in rel_path.split("/")[:-1]


def in_processing_master(rel_path: str) -> bool:
    """Directly in Processing/master (the WBPP output folder): intermediate steps and finals are allowed."""
    dirs = rel_path.split("/")[:-1]
    return len(dirs) >= 2 and dirs[-2] == PROCESSING_DIR and bool(_MASTER_DIR_RE.match(dirs[-1]))


def is_final_name(name: str) -> bool:
    return "final" in name.rsplit("/", 1)[-1].lower()


def in_finals(rel_path: str) -> bool:
    """True if a folder named exactly 'Finals' (of a target or a session) is part of the path."""
    return FINALS_DIR in rel_path.split("/")[:-1]


def allowed_location(rel_path: str, session_roots=()) -> bool:
    """Where intermediate results may lie: anywhere below 'Processing' or 'Finals'."""
    return in_processing(rel_path) or in_finals(rel_path)


# Rules for intermediate results, most important first: (code, label, severity)
INTERMEDIATE_RULES = [
    ("wbpp_step", "WBPP-/Stacking-Zwischenschritt", "warning"),
    ("wbpp_calibration_master", "Kalibrier-Master (masterDark/Flat/Bias)", "warning"),
    ("not_in_processing", "Nicht in „Processing“", "warning"),
    ("wbpp_other", "Sonstiges im WBPP-Ordner master/", "hint"),
    ("wbpp_ok", "WBPP-Ergebnis (masterLight / autocrop)", "ok"),
    ("final", "Final in Processing/master", "ok"),
    ("processing_master", "Zwischenschritt in Processing/master – erlaubt", "ok"),
    ("finals", "In „Finals“ – in Ordnung", "ok"),
    ("ok", "In „Processing“ – in Ordnung", "ok"),
]
INTERMEDIATE_RULE_INFO = {c: (label, sev) for c, label, sev in INTERMEDIATE_RULES}


def intermediate_findings(rel_path: str, session_roots=()) -> list[str]:
    """All rule codes that apply to an intermediate file (problems first; 'ok' only if nothing else)."""
    kind = wbpp_kind(rel_path)
    if in_processing_master(rel_path):
        if kind == "ok":
            return ["wbpp_ok"]
        return ["final"] if is_final_name(rel_path) else ["processing_master"]
    out = []
    if kind == "step":
        out.append("wbpp_step")
    elif kind == "calibration_master":
        out.append("wbpp_calibration_master")
    if not allowed_location(rel_path, session_roots):
        out.append("not_in_processing")
    if kind == "other":
        out.append("wbpp_other")
    if not out:
        out.append("wbpp_ok" if kind == "ok" else
                   "finals" if in_finals(rel_path) else "ok")
    return out


@dataclass(frozen=True)
class Classified:
    kind: str                     # "frame", "version" or "intermediate"
    target_folder: str            # e.g. "2026/M51 (Whirlpool Galaxy)"
    session_root: str | None      # folder that represents the session (frames only)
    frame_type: str | None = None  # from the folder; None = decide from header
    rejected: bool = False


def _matches_any(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in patterns)


def classify(rel_path: str, ignore_dirs: list[str] | None = None) -> Classified | None:
    """Decide what a file in the archive is. Returns None for files to ignore."""
    p = PurePosixPath(rel_path)
    ext = p.suffix.lower()
    if p.name.startswith("."):
        return None
    if ext not in FRAME_EXT and ext not in IMAGE_EXT and ext not in WBPP_EXT:
        return None

    parts = list(p.parts[:-1])
    if parts and _YEAR_RE.match(parts[0]):
        head, parts = parts[:2], parts[1:]
    else:
        head, parts = parts[:1], parts
    if not parts:                       # file directly in the archive root or a (legacy) year folder
        return None
    target_folder = "/".join(head)
    below = parts[1:]                   # folders between target folder and file

    in_version_area = False
    frame_type = None
    frame_idx = None
    rejected = False
    wbpp_step = ext in WBPP_EXT
    for i, d in enumerate(below):
        if _SKIP_RE.match(d) or (ignore_dirs and _matches_any(d, ignore_dirs)):
            return None
        low = d.lower()
        if low in WBPP_STEP_DIRS:
            wbpp_step = True
        if frame_type is None and low in FRAME_DIRS:
            frame_type, frame_idx = FRAME_DIRS[low], i
        elif frame_type is not None and low in REJECT_DIRS:
            rejected = True
        elif frame_type is None and _VERSION_RE.match(d):
            in_version_area = True

    if wbpp_step:
        # calibrated / registered / debayered … frames, local normalization, drizzle data
        return Classified("intermediate", target_folder, None)
    if ext in IMAGE_EXT:
        if frame_type is not None:      # e.g. JPG lights of a solar eclipse: not a version
            return None
        if _SUBFRAME_NAME_RE.match(p.name):
            return None                 # a single sub frame exported as TIFF, not a result
        return Classified("version", target_folder, None)

    # frame candidates
    if in_version_area:
        # stacks, masters, PixInsight/Siril steps: recorded (not counted) so their location can be checked
        return Classified("intermediate", target_folder, None)
    if frame_type is not None:
        root = "/".join(head + below[:frame_idx]) if frame_idx else target_folder
        return Classified("frame", target_folder, root, frame_type, rejected)
    root = "/".join(head + below)
    return Classified("frame", target_folder, root, None, False)


# --------------------------------------------------------------------------- headers

def frame_type_from_header(imagetyp: str | None) -> str | None:
    if not imagetyp:
        return None
    t = imagetyp.strip().lower()
    if "master" in t:
        return None
    if "flat" in t and "dark" in t:
        return "darkflat"
    for key, val in (("light", "light"), ("object", "light"), ("flat", "flat"),
                     ("dark", "dark"), ("bias", "bias"), ("offset", "bias"), ("zero", "bias")):
        if key in t:
            return val
    return None


def is_raw_frame(h: dict) -> bool:
    """For files outside a Lights/Flats/... folder: accept only single raw sub frames."""
    naxis3 = h.get("naxis3")
    if naxis3 and naxis3 > 1:
        return False                    # debayered or stacked colour image
    if h.get("exptime") is None or h.get("date_obs") is None:
        return False
    prog = (h.get("program") or "").lower()
    if "pixinsight" in prog and not h.get("imagetyp"):
        return False
    return True


# --------------------------------------------------------------------------- sessions

def folder_date(name: str) -> date | None:
    m = _DATE_RE.search(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def night_of(ts: datetime | None, offset_hours: int = 12) -> date | None:
    """The observing night a timestamp belongs to (evening date)."""
    if ts is None:
        return None
    return (ts - timedelta(hours=offset_hours)).date()


def without_year(path: str) -> str:
    """Path for display: a leading (legacy) year folder is dropped."""
    first, _, rest = path.partition("/")
    return rest if rest and _YEAR_RE.match(first) else path


def session_label(session_root: str, target_folder: str) -> str:
    if session_root == target_folder:
        return ""
    return session_root[len(target_folder) + 1:]


# --------------------------------------------------------------------------- targets

_CATALOGS = [
    (r"M", "M {}"), (r"NGC", "NGC {}"), (r"IC", "IC {}"),
    (r"SH\s*2", "Sh2-{}"), (r"Arp", "Arp {}"), (r"Abell", "Abell {}"),
    (r"LDN", "LDN {}"), (r"LBN", "LBN {}"), (r"vdB", "vdB {}"),
    (r"Mel", "Mel {}"), (r"Cr", "Cr {}"), (r"UGC", "UGC {}"), (r"PGC", "PGC {}"),
    (r"Barnard", "B {}"), (r"B", "B {}"), (r"Ced", "Ced {}"),
]
_CAT_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(c for c, _ in _CATALOGS) + r")\s*-?\s*(\d{1,5})(?!\d)",
    re.I,
)
_CAT_ORDER = {fmt.split()[0].split("-")[0]: i for i, (_, fmt) in enumerate(_CATALOGS)}


def _norm_catalog(prefix: str, number: str) -> str:
    pre = re.sub(r"\s+", "", prefix).upper()
    for pat, fmt in _CATALOGS:
        if re.fullmatch(pat.replace(r"\s*", ""), pre, re.I):
            return fmt.format(int(number))
    return f"{prefix} {int(number)}"


def catalog_sort_key(cid: str):
    head = re.split(r"[ -]", cid)[0]
    num = re.findall(r"\d+", cid)
    return (_CAT_ORDER.get(head, 99), int(num[-1]) if num else 0, cid)


@dataclass(frozen=True)
class TargetName:
    catalog_ids: tuple[str, ...]
    common_name: str | None
    base_name: str            # name without catalog ids / parentheses / dates


def parse_target_folder(folder_name: str) -> TargetName:
    common = None
    m = re.search(r"\(([^)]*)\)", folder_name)
    if m:
        common = m.group(1).strip() or None
    without_paren = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", folder_name)
    ids: list[str] = []
    for pre, num in _CAT_RE.findall(without_paren):
        cid = _norm_catalog(pre, num)
        if cid not in ids:
            ids.append(cid)
    base = _CAT_RE.sub(" ", without_paren)
    base = _DATE_RE.sub(" ", base)
    base = re.sub(r"\b(NGC|IC|M)\b", " ", base)          # dangling "NGC" without number
    base = re.sub(r"[,;]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" -_")
    return TargetName(tuple(ids), common, base)


def target_keys(name: TargetName) -> set[str]:
    """Keys that link folders of the same object (shared catalog id or same plain name)."""
    if name.catalog_ids:
        return {f"id:{c}" for c in name.catalog_ids}
    return {f"name:{(name.base_name or name.common_name or '').lower()}"}


def group_folders(folder_names: dict[str, str]) -> list[list[str]]:
    """Union folders that share a key. `folder_names` maps folder path -> last path component."""
    parent: dict[str, str] = {f: f for f in folder_names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    for folder, last in folder_names.items():
        for k in target_keys(parse_target_folder(last)):
            if k in owner:
                parent[find(folder)] = find(owner[k])
            else:
                owner[k] = folder
    groups: dict[str, list[str]] = {}
    for f in folder_names:
        groups.setdefault(find(f), []).append(f)
    return [sorted(g) for g in groups.values()]


def guess_object_type(*names: str | None) -> str | None:
    text = " ".join(n for n in names if n).lower()
    rules = [
        ("Galaxie", ("galax", "triplet", "group")),
        ("Kugelsternhaufen", ("globular", "kugel")),
        ("Sternhaufen", ("cluster", "haufen", "pleiades", "plejaden")),
        ("Planetarischer Nebel", ("owl", "ring nebula", "dumbbell", "planetary")),
        ("Nebel", ("nebula", "nebel", "veil", "trunk")),
        ("Mond", ("moon", "mond")),
        ("Sonne", ("sun", "sonne", "solar", "eclipse", "finsternis")),
        ("Meteore", ("perseid", "meteor", "geminid")),
        ("Satellit", ("iss",)),
    ]
    for label, keys in rules:
        if any(k in text for k in keys):
            return label
    return None


# --------------------------------------------------------------------------- equipment

def cluster_focal_lengths(values: list[float], tolerance: float) -> dict[float, int]:
    """Map each focal length to a representative (rounded median of its cluster).

    Values within `tolerance` (relative, e.g. 0.03 = 3 %) of a cluster's first
    value belong to the same cluster.
    """
    out: dict[float, int] = {}
    vals = sorted({v for v in values if v and v > 0})
    cluster: list[float] = []

    def flush():
        if cluster:
            mid = cluster[len(cluster) // 2]
            for v in cluster:
                out[v] = int(round(mid))

    for v in vals:
        if cluster and v > cluster[0] * (1 + tolerance):
            flush()
            cluster = []
        cluster.append(v)
    flush()
    return out


_NOT_A_TELESCOPE = re.compile(r"mount|eqmod|gsserver|ascom|onstep|indi|simulator", re.I)


def clean_telescope(telescop: str | None, instrume: str | None) -> str | None:
    if not telescop:
        return None
    t = telescop.strip()
    if not t or _NOT_A_TELESCOPE.search(t):
        return None
    if instrume and t.lower() == instrume.strip().lower():
        return None
    return t


def clean_filter(value: str | None) -> str:
    if not value or value.strip().lower() in ("", "none", "nofilter", "no filter", "clear", "l-pro?"):
        return "Ohne Filter"
    return value.strip()
