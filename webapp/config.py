"""Configuration: defaults < config.json < environment variables (FLASK_<NAME>).

Same pattern as vds-sternwarte: a config.json next to app.py, environment
variables win (Flask's from_prefixed_env is applied in create_app).
"""
import json
import os

_CONFIG_FILE = os.environ.get("PHOTONLEDGER_CONFIG", "config.json")
try:
    with open(_CONFIG_FILE) as fh:
        config = json.load(fh)
except FileNotFoundError:
    config = {}

_data = config.get("DATA_PATH", "./data")


def _version() -> str:
    """Release number from the VERSION file (the image passes its own APPVERSION)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")) as fh:
            return fh.read().strip()
    except OSError:
        return "0.0.0"


VERSION = _version()


class Config:
    SECRET_KEY = config.get("SECRET_KEY", "change-me")
    # optional login (see webapp/auth.py): without AUTH_USER everything stays open
    AUTH_USER = config.get("AUTH_USER")
    AUTH_PASSWORD_HASH = config.get("AUTH_PASSWORD_HASH")
    AUTH_PUBLIC_READ = config.get("AUTH_PUBLIC_READ", False)
    # read-only archive root and writable data folder (database, thumbnails)
    ARCHIVE_PATH = config.get("ARCHIVE_PATH", "./archive")
    # several archives: {"<name>": "<path>", …}; stored paths then start with the name (ARCHIVE_PATH is unused)
    ARCHIVES = config.get("ARCHIVES")
    # how the user's computer reaches the archive, for "Im Finder öffnen" (e.g. smb://mars/Multimedia/Astro);
    # with several archives ARCHIVE_URLS = {"<name>": "<url>"}
    ARCHIVE_URL = config.get("ARCHIVE_URL")
    ARCHIVE_URLS = config.get("ARCHIVE_URLS")
    # downloads (ZIP of sessions, finals) with a login: false = only after logging in, also in public read mode
    DOWNLOAD_PUBLIC = config.get("DOWNLOAD_PUBLIC", False)
    DATA_PATH = _data
    # None = <DATA_PATH>/photonledger.db (resolved in create_app, after env overrides)
    SQLALCHEMY_DATABASE_URI = config.get("SQLALCHEMY_DATABASE_URI")
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
    # indexer
    SCAN_INTERVAL = int(config.get("SCAN_INTERVAL", 900))            # seconds between scans
    FOCAL_TOLERANCE = float(config.get("FOCAL_TOLERANCE", 0.03))     # 3 % = same focal length
    NIGHT_OFFSET_HOURS = int(config.get("NIGHT_OFFSET_HOURS", 12))   # DATE-OBS - 12 h = evening date
    MAX_VERSIONS_PER_DIR = int(config.get("MAX_VERSIONS_PER_DIR", 30))
    IGNORE_DIRS = config.get("IGNORE_DIRS", [])                       # extra folder names (glob)
    CHECK_TEMP_TOLERANCE = float(config.get("CHECK_TEMP_TOLERANCE", 5.0))   # K between darks and lights
    CHECK_REJECT_RATIO = float(config.get("CHECK_REJECT_RATIO", 0.3))       # hint above 30 % rejected
    # StarAberrationDiagnostics: limits for the checks (medians of a session)
    SAD_OUTLIER_FACTOR = float(config.get("SAD_OUTLIER_FACTOR", 1.3))     # light FWHM > 1.3 × median
    SAD_TILT_WARN = float(config.get("SAD_TILT_WARN", 0.25))              # TiltRel
    SAD_RATIO_WARN = float(config.get("SAD_RATIO_WARN", 1.4))             # FWHM edge / centre
    SAD_ELLIPTICITY_WARN = float(config.get("SAD_ELLIPTICITY_WARN", 0.15))  # tracking ellipticity
    # time zone of the computer that wrote the PHD2 logs (they use local time)
    TIMEZONE = config.get("TIMEZONE", os.environ.get("TZ", "Europe/Berlin"))
    # PHD2 status in the session overview: red above these limits
    PHD_LOST_RATIO = float(config.get("PHD_LOST_RATIO", 0.01))          # share of frames without guide star
    PHD_SETTLE_FAILED = int(config.get("PHD_SETTLE_FAILED", 3))         # failed settlings per session …
    PHD_SETTLE_RATIO = float(config.get("PHD_SETTLE_RATIO", 0.10))      # … or share of dithers
    PHD_RMS_ERROR = float(config.get("PHD_RMS_ERROR", 5.0))             # arcsec RMS of a longer section
    MAX_CONTENT_LENGTH = 30 * 1024 * 1024                       # largest upload (rig photos)
    BACKUP_KEEP = int(config.get("BACKUP_KEEP", 14))   # daily database copies in DATA_PATH/backups, 0 = none
    THUMB_SIZE = int(config.get("THUMB_SIZE", 480))
    PREVIEW_SIZE = int(config.get("PREVIEW_SIZE", 2000))
    APPVERSION = os.environ.get("APPVERSION") or f"{VERSION}-local"
    GITCOMMIT = os.environ.get("GITCOMMIT", "")
