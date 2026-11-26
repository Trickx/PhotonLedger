from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from webapp import db


class Target(db.Model):
    """An object, possibly spread over several folders (e.g. "M42" and "M42 (Orion Nebula)")."""
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    common_name: Mapped[str | None] = mapped_column(String(300))
    catalog_ids: Mapped[list] = mapped_column(JSON, default=list)
    folders: Mapped[list] = mapped_column(JSON, default=list)
    object_type: Mapped[str | None] = mapped_column(String(60))
    ra: Mapped[float | None]
    dec: Mapped[float | None]
    constellation: Mapped[str | None] = mapped_column(String(60))
    # from the catalog (OpenNGC)
    catalog_name: Mapped[str | None] = mapped_column(String(60))     # e.g. "NGC 3627" for M 66
    magnitude: Mapped[float | None]
    size_arcmin: Mapped[float | None]
    # where the camera actually pointed (median of the lights) and the field of view
    pointing_ra: Mapped[float | None]
    pointing_dec: Mapped[float | None]
    fov_w: Mapped[float | None]                                      # arcmin, largest data set
    fov_h: Mapped[float | None]
    objects_in_field: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    warnings: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # user choice, kept across re-indexing (stores the archive path, not the id)
    cover_path: Mapped[str | None] = mapped_column(String(1000))
    # cached statistics, recomputed by the indexer
    exposure_s: Mapped[float] = mapped_column(default=0)
    lights: Mapped[int] = mapped_column(default=0)
    last_night: Mapped[date | None]

    sessions: Mapped[list[Session]] = relationship(back_populates="target", order_by="Session.date")
    versions: Mapped[list[Version]] = relationship(back_populates="target", order_by="Version.path")


class Session(db.Model):
    """One session = one folder that holds Lights/Flats/... (or loose sub frames)."""
    id: Mapped[int] = mapped_column(primary_key=True)
    root: Mapped[str] = mapped_column(String(1000), unique=True)
    label: Mapped[str] = mapped_column(String(300), default="")
    target_id: Mapped[int | None] = mapped_column(ForeignKey("target.id", ondelete="SET NULL"), index=True)
    date: Mapped[date | None]
    nights: Mapped[list] = mapped_column(JSON, default=list)
    exposure_s: Mapped[float] = mapped_column(default=0)
    lights: Mapped[int] = mapped_column(default=0)
    rejected: Mapped[int] = mapped_column(default=0)
    duplicates: Mapped[int] = mapped_column(default=0, server_default="0")
    duplicate_of: Mapped[str | None] = mapped_column(String(300))   # label of the session holding the originals
    flats: Mapped[int] = mapped_column(default=0)
    darks: Mapped[int] = mapped_column(default=0)
    biases: Mapped[int] = mapped_column(default=0)
    darkflats: Mapped[int] = mapped_column(default=0)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)      # filter -> seconds
    datasets: Mapped[list] = mapped_column(JSON, default=list)     # [{camera, focal, telescope, seconds, lights}]

    target: Mapped[Target | None] = relationship(back_populates="sessions")

    @property
    def calibrated(self) -> bool:
        return self.flats > 0 and (self.darks > 0 or self.biases > 0)


class Frame(db.Model):
    """A sub frame (light, flat, dark, bias) found in the archive."""
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    size: Mapped[int]
    mtime: Mapped[float]
    target_folder: Mapped[str] = mapped_column(String(1000), index=True)
    session_root: Mapped[str] = mapped_column(String(1000), index=True)
    frame_type: Mapped[str | None] = mapped_column(String(20), index=True)
    rejected: Mapped[bool] = mapped_column(default=False)
    # same light (camera, DATE-OBS, exposure) already exists in another folder of the same target
    duplicate: Mapped[bool] = mapped_column(default=False, server_default="0")
    exptime: Mapped[float | None]
    date_obs: Mapped[datetime | None]
    night: Mapped[date | None]
    camera: Mapped[str | None] = mapped_column(String(120))
    telescope: Mapped[str | None] = mapped_column(String(120))
    focal: Mapped[float | None]
    focal_group: Mapped[int | None]
    filter: Mapped[str | None] = mapped_column(String(80))
    gain: Mapped[float | None]
    iso: Mapped[float | None]
    temp: Mapped[float | None]
    binning: Mapped[int | None]
    width: Mapped[int | None]
    height: Mapped[int | None]
    ra: Mapped[float | None]
    dec: Mapped[float | None]
    object_name: Mapped[str | None] = mapped_column(String(200))
    program: Mapped[str | None] = mapped_column(String(200))
    header: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)


class Version(db.Model):
    """A processed result (JPG/PNG/TIFF) of a target."""
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    size: Mapped[int]
    mtime: Mapped[float]
    target_folder: Mapped[str] = mapped_column(String(1000), index=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("target.id", ondelete="SET NULL"), index=True)
    thumb: Mapped[str | None] = mapped_column(String(200))
    preview: Mapped[str | None] = mapped_column(String(200))
    width: Mapped[int | None]
    height: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)

    target: Mapped[Target | None] = relationship(back_populates="versions")

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


class Intermediate(db.Model):
    """A FITS/XISF file in a processing folder (stack, master, PixInsight step). Not read, only listed."""
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    size: Mapped[int]
    mtime: Mapped[float]
    target_folder: Mapped[str] = mapped_column(String(1000), index=True)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


class SadSeries(db.Model):
    """Results of StarAberrationDiagnostics: one SAD/ folder next to the lights."""
    id: Mapped[int] = mapped_column(primary_key=True)
    folder: Mapped[str] = mapped_column(String(1000), unique=True)      # .../Lights/SAD
    session_root: Mapped[str] = mapped_column(String(1000), index=True)
    csv_mtime: Mapped[float | None]
    json_mtime: Mapped[float | None]
    ai_mtime: Mapped[float | None]
    created: Mapped[str | None] = mapped_column(String(40))
    script: Mapped[str | None] = mapped_column(String(20))
    pixel_factor: Mapped[float] = mapped_column(default=1.0)           # 2 = SuperPixel debayer
    pitch_um: Mapped[float | None]
    focal_mm: Mapped[float | None]
    static: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")   # per pier side
    ai_reviews: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)

    frames: Mapped[list[SadFrame]] = relationship(back_populates="series", cascade="all, delete-orphan",
                                                  order_by="SadFrame.time_utc")

    @property
    def arcsec_per_px(self) -> float | None:
        """Scale of SAD's measurement pixel (after SuperPixel debayering if used)."""
        if not self.pitch_um or not self.focal_mm:
            return None
        return 206.265 * self.pitch_um * self.pixel_factor / self.focal_mm


class SadFrame(db.Model):
    """Measurements of one light by StarAberrationDiagnostics (StarAberrationSeries.csv)."""
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("sad_series.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int | None] = mapped_column(ForeignKey("frame.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    side: Mapped[str | None] = mapped_column(String(10))
    time_utc: Mapped[datetime | None]
    hour_angle: Mapped[float | None]
    altitude: Mapped[float | None]
    focus: Mapped[float | None]
    temp: Mapped[float | None]
    stars: Mapped[int | None]
    fwhm_center: Mapped[float | None]          # SAD pixels
    fwhm_edge: Mapped[float | None]
    tilt_rel: Mapped[float | None]
    tilt_dir: Mapped[float | None]
    ellipticity: Mapped[float | None]          # tracking ellipticity
    tracking_angle: Mapped[float | None]
    coma_k: Mapped[float | None]
    error: Mapped[str | None] = mapped_column(Text)
    maps: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")   # kind -> archive path

    series: Mapped[SadSeries] = relationship(back_populates="frames")
    frame: Mapped[Frame | None] = relationship()


class PhdLog(db.Model):
    """A PHD2 guide log (PHD2_GuideLog_*.txt) found in a log folder of the archive."""
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    size: Mapped[int]
    mtime: Mapped[float]
    started: Mapped[datetime | None]                 # UTC
    version: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)

    sections: Mapped[list[PhdSection]] = relationship(back_populates="log", cascade="all, delete-orphan",
                                                      order_by="PhdSection.start_utc")


class PhdSection(db.Model):
    """One 'Guiding Begins … Guiding Ends' block of a PHD2 log."""
    id: Mapped[int] = mapped_column(primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("phd_log.id", ondelete="CASCADE"), index=True)
    start_utc: Mapped[datetime] = mapped_column(index=True)
    end_utc: Mapped[datetime | None] = mapped_column(index=True)
    pixel_scale: Mapped[float | None]                # arcsec / guide pixel
    exposure_ms: Mapped[float | None]
    camera: Mapped[str | None] = mapped_column(String(120))
    pier_side: Mapped[str | None] = mapped_column(String(10))
    dec_deg: Mapped[float | None]
    hour_angle: Mapped[float | None]
    frames: Mapped[int] = mapped_column(default=0)
    dropped: Mapped[int] = mapped_column(default=0)
    dithers: Mapped[int] = mapped_column(default=0)
    settle_failed: Mapped[int] = mapped_column(default=0)
    rms_ra: Mapped[float | None]                     # arcsec, settled frames only
    rms_dec: Mapped[float | None]
    rms_total: Mapped[float | None]
    peak_ra: Mapped[float | None]
    peak_dec: Mapped[float | None]
    # [[seconds since start, RA″, Dec″, settling 0/1], …] and [[seconds, "dither" | "settle_fail"], …]
    series: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    events: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")

    log: Mapped[PhdLog] = relationship(back_populates="sections")

    @property
    def duration_s(self) -> float:
        return (self.end_utc - self.start_utc).total_seconds() if self.end_utc else 0


class NightLog(db.Model):
    """A log file Airgus can read (ASIAIR Autorun, NINA, PHD2, iOptron) - time span for the assignment."""
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    kind: Mapped[str] = mapped_column(String(20))            # asiair | nina | phd2 | ioptron
    size: Mapped[int]
    mtime: Mapped[float]
    first_utc: Mapped[datetime | None]
    last_utc: Mapped[datetime | None]
    events: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)


class NightAnalysis(db.Model):
    """Airgus run over all logs of a session: alerts, counts and a timeline."""
    id: Mapped[int] = mapped_column(primary_key=True)
    session_root: Mapped[str] = mapped_column(String(1000), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    logs: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")        # [{path, kind}]
    summary: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    alerts: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")      # [{time, severity, rule, title, message}]
    timeline: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class WbppRun(db.Model):
    """One run of PixInsight WeightedBatchPreprocessing (its .log in a logs/ folder of the target)."""
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    size: Mapped[int]
    mtime: Mapped[float]
    target_folder: Mapped[str] = mapped_column(String(1000), index=True)
    started: Mapped[datetime | None]                 # local time of the processing computer
    started_utc: Mapped[datetime | None]
    duration_s: Mapped[float | None]
    pi_version: Mapped[str | None] = mapped_column(String(80))
    wbpp_version: Mapped[str | None] = mapped_column(String(40))
    blocks: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")        # report summary
    errors: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    warnings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")      # message -> count
    lights_used: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")   # stems of the lights
    frames: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")        # stem -> measurements
    integrations: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    seen_run: Mapped[int] = mapped_column(default=0, index=True)


# --------------------------------------------------------------------------- equipment / rigs
# Entered by the user (not touched by the indexer).

class Camera(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    header_name: Mapped[str | None] = mapped_column(String(120))   # INSTRUME in the FITS header, for matching
    pixel_um: Mapped[float | None]
    width_px: Mapped[int | None]
    height_px: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def sensor_mm(self) -> tuple[float, float] | None:
        if self.pixel_um and self.width_px and self.height_px:
            return self.width_px * self.pixel_um / 1000, self.height_px * self.pixel_um / 1000
        return None


class Optic(db.Model):
    """Telescope, prime lens or zoom lens."""
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="telescope")    # telescope | prime | zoom
    focal_mm: Mapped[float]                                               # zoom: shortest
    focal_max_mm: Mapped[float | None]                                    # zoom: longest
    aperture_mm: Mapped[float | None]
    f_number: Mapped[float | None]                                        # lenses: f/ (zoom: at shortest)
    f_number_max: Mapped[float | None]                                    # zoom: at longest
    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def is_zoom(self) -> bool:
        return self.kind == "zoom" and bool(self.focal_max_mm)


class Corrector(db.Model):
    """Flattener, reducer or extender (factor 1.0 = flattener / coma corrector)."""
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="flattener")   # flattener | reducer | extender
    factor: Mapped[float] = mapped_column(default=1.0)
    notes: Mapped[str | None] = mapped_column(Text)


class Rig(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(default=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("camera.id"), index=True)
    optic_id: Mapped[int] = mapped_column(ForeignKey("optic.id"), index=True)
    corrector_id: Mapped[int | None] = mapped_column(ForeignKey("corrector.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    camera: Mapped[Camera] = relationship()
    optic: Mapped[Optic] = relationship()
    corrector: Mapped[Corrector | None] = relationship()

    @property
    def factor(self) -> float:
        return self.corrector.factor if self.corrector else 1.0

    @property
    def focal_range(self) -> tuple[float, float]:
        lo = self.optic.focal_mm * self.factor
        hi = (self.optic.focal_max_mm or self.optic.focal_mm) * self.factor
        return lo, hi

    @property
    def f_ratio_range(self) -> tuple[float, float] | None:
        o = self.optic
        if o.aperture_mm:
            lo, hi = self.focal_range
            return lo / o.aperture_mm, hi / o.aperture_mm
        if o.f_number:
            return o.f_number * self.factor, (o.f_number_max or o.f_number) * self.factor
        return None

    def scale_at(self, focal: float) -> float | None:
        """arcsec / pixel"""
        return 206.265 * self.camera.pixel_um / focal if self.camera.pixel_um and focal else None

    def fov_at(self, focal: float) -> tuple[float, float] | None:
        """arcmin"""
        s = self.scale_at(focal)
        if s and self.camera.width_px and self.camera.height_px:
            return self.camera.width_px * s / 60, self.camera.height_px * s / 60
        return None


class RigMatch(db.Model):
    """Assignment of a data set (camera from the header + focal-length group) to a rig."""
    id: Mapped[int] = mapped_column(primary_key=True)
    camera: Mapped[str] = mapped_column(String(120))
    focal: Mapped[int]
    rig_id: Mapped[int] = mapped_column(ForeignKey("rig.id", ondelete="CASCADE"), index=True)
    __table_args__ = (db.UniqueConstraint("camera", "focal"),)


class RigSessionOverride(db.Model):
    """A session that was taken with another rig than its camera/focal length suggests."""
    id: Mapped[int] = mapped_column(primary_key=True)
    session_root: Mapped[str] = mapped_column(String(1000), unique=True)
    rig_id: Mapped[int] = mapped_column(ForeignKey("rig.id", ondelete="CASCADE"), index=True)


class Issue(db.Model):
    """A finding of the structure checks (recomputed on every index run)."""
    id: Mapped[int] = mapped_column(primary_key=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)     # error | warning | hint
    code: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("target.id", ondelete="CASCADE"), index=True)
    session_root: Mapped[str | None] = mapped_column(String(1000))
    path: Mapped[str | None] = mapped_column(String(1000))
    count: Mapped[int] = mapped_column(default=1)

    target: Mapped[Target | None] = relationship()


class IndexRun(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    started: Mapped[datetime]
    finished: Mapped[datetime | None]
    phase: Mapped[str] = mapped_column(String(40), default="scan")
    files_seen: Mapped[int] = mapped_column(default=0)
    files_read: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(Text)


Index("ix_frame_type_rejected", Frame.session_root, Frame.frame_type, Frame.rejected)
