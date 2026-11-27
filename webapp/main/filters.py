"""Jinja filters for astronomical and time formatting."""
from __future__ import annotations


def hours(seconds: float | None, digits: int = 1) -> str:
    if not seconds:
        return "0"
    return f"{seconds / 3600:.{digits}f}".replace(".", ",")


def duration(seconds: float | None) -> str:
    if not seconds:
        return "–"
    s = int(round(seconds))
    h, m = divmod(s // 60, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min"
    return f"{s} s"


def exposure(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    if seconds >= 1:
        return f"{seconds:g} s"
    if seconds > 0:
        return f"1/{round(1 / seconds):g} s"
    return "0 s"


def ra_hms(deg: float | None) -> str:
    if deg is None:
        return "–"
    h = (deg % 360) / 15
    hh = int(h)
    mm = int((h - hh) * 60)
    ss = ((h - hh) * 60 - mm) * 60
    return f"{hh:02d}h {mm:02d}m {ss:04.1f}s"


def dec_dms(deg: float | None) -> str:
    if deg is None:
        return "–"
    sign = "-" if deg < 0 else "+"
    d = abs(deg)
    dd = int(d)
    mm = int((d - dd) * 60)
    ss = ((d - dd) * 60 - mm) * 60
    return f"{sign}{dd:02d}° {mm:02d}′ {ss:04.1f}″"


def de_date(d, fmt: str = "%d.%m.%Y") -> str:
    if not d:
        return "–"
    return d.strftime(fmt)


def timestamp(ts: float | None) -> str:
    if not ts:
        return "–"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def num(n) -> str:
    if n is None:
        return "–"
    return f"{n:,}".replace(",", ".")


def filesize(n) -> str:
    """Bytes as 1,2 GB / 830 MB / 4 KB."""
    if not n:
        return "0 B"
    for unit, f in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= f:
            v = n / f
            return (f"{v:.1f}" if v < 10 else f"{v:.0f}").replace(".", ",") + " " + unit
    return f"{n} B"


def register_filters(app) -> None:
    from webapp.archive.notes import render
    app.jinja_env.globals["render_notes"] = render
    app.jinja_env.filters.update(filesize=filesize, 
        hours=hours, duration=duration, exposure=exposure,
        ra=ra_hms, dec=dec_dms, de_date=de_date, num=num, timestamp=timestamp,
    )
