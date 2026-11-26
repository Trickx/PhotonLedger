"""End to end: a tiny fake archive -> indexer -> every page renders."""
import os

import pytest
from PIL import Image

from webapp import create_app, db
from webapp.config import Config
from webapp.indexer import run_once
from webapp.models import Frame, Issue, Session, Target, Version


def _fits(path, **cards):
    lines = ["SIMPLE  =                    T", "BITPIX  =                   16", "NAXIS   =                    2"]
    for k, v in cards.items():
        val = f"'{v}'" if isinstance(v, str) else str(v)
        lines.append(f"{k:<8}= {val:>20}")
    lines.append("END")
    data = "".join(line.ljust(80) for line in lines).encode()
    data += b" " * (-len(data) % 2880)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data + b"\0" * 2880)


def _light(path, date, exp=180.0, focal=755.0, camera="Canon EOS 1200D", ra=170.08, dec=12.95, imagetyp="Light"):
    _fits(path, IMAGETYP=imagetyp, EXPTIME=exp, **{"DATE-OBS": date}, INSTRUME=camera,
          TELESCOP="EQMod Mount", FOCALLEN=focal, RA=ra, DEC=dec, NAXIS1=5202, NAXIS2=3465, XPIXSZ=4.29,
          SITELAT=52.3713, SITELONG=9.9177)


@pytest.fixture()
def app(tmp_path):
    arc = tmp_path / "archive"
    leo_a = arc / "M65, M66, NGC 3628 (Leo-Triplett)" / "2026-03-22"
    for i in range(3):
        _light(leo_a / "Lights" / f"l{i}.fit", f"2026-03-22T2{i}:00:00", focal=752.7 + i)
    _light(leo_a / "Lights" / "REJECTED" / "r.fit", "2026-03-22T23:30:00")
    _light(leo_a / "Flats" / "f.fit", "2026-03-23T00:10:00", exp=0.07, imagetyp="Flat")
    _light(leo_a / "Darks" / "d.fit", "2026-03-23T01:10:00", imagetyp="Dark")
    _fits(leo_a / "Processing" / "stack.fit", NAXIS3=3, EXPTIME=120.0)        # ignored
    leo_b = arc / "M66, M65, NGC (Leo-Triplett)"
    _light(leo_b / "Lights" / "x.fit", "2026-03-29T21:00:00", exp=120.0, focal=792.0, camera="Canon EOS 7D MarkII")
    _light(leo_b / "Lights" / "copy_of_l0.fit", "2026-03-22T20:00:00", focal=752.7)   # same light as l0.fit
    _fits(leo_b / "loose_stack.fit", NAXIS3=3, EXPTIME=120.0, IMAGETYP="Light", **{"DATE-OBS": "2026-03-29T21:00:00"})
    # StarAberrationDiagnostics results next to the lights of leo_a
    sad = leo_a / "Lights" / "SAD"
    sad.mkdir()
    (sad / "StarAberrationSeries.csv").write_text(
        "File,Side,SideSource,TimeUTC,HourAngle_h,Altitude_deg,FocusPos,Temp_C,Stars,FWHMCenter_px,FWHMEdge_px,"
        "TiltRel,TiltDir_deg,TrackingEllipticity,TrackingAngle_deg,ComaK,Error\n"
        "l0.fit,West,x,2026-03-22T20:00:00Z,-2,40,100,,900,1.20,1.90,0.40,180,0.22,150,-1,\n"
        "l1.fit,West,x,2026-03-22T21:00:00Z,-1,45,100,,950,1.25,1.95,0.42,181,0.20,151,-1,\n"
        "l2.fit,East,x,2026-03-22T22:00:00Z,0,50,100,,800,2.40,2.90,0.41,182,0.21,152,-1,\n")
    Image.new("RGB", (300, 200), (10, 10, 40)).save(sad / "l0_Mosaic.png")
    Image.new("RGB", (300, 200), (40, 10, 10)).save(sad / "l0_Size.png")
    (sad / "StarAberrationAI.json").write_text(
        '{"format": "StarAberrationDiagnostics AI reviews", "version": 1, "reviews": [{"created": "2026-03-23T10:00:00Z",'
        ' "model": "test", "series": ["l0.fit"], "review": {"data": {"labels": {"verdict": "Gesamturteil"},'
        ' "verdict": "Alles gut bis auf die Nachführung.", "order": ["Nachführung prüfen"],'
        ' "findings": [{"topic": "Nachführung", "status": "action", "observation": "gestreckt"}]}}}]}')
    # PHD2 guide log (local time CET = UTC+1 on 2026-03-22): guiding 21:00-21:40 local = 20:00-20:40 UTC
    rows = "\n".join(f'{i},{i * 2.0:.3f},"Mount",0,0,{0.2 if i % 2 else -0.2:.3f},{0.1:.3f},0,0,0,,0,,,,8000,50,0'
                     for i in range(1, 121))
    (leo_a / "Logs").mkdir()
    (leo_a / "Logs" / "PHD2_GuideLog_2026-03-22_205900.txt").write_text(
        "PHD2 version 2.6.13, Log version 2.5. Log enabled at 2026-03-22 20:59:00\n\n"
        "Guiding Begins at 2026-03-22 21:00:00\n"
        "Pixel scale = 6.45 arc-sec/px, Binning = 1, Focal length = 120 mm\n"
        "Camera = ZWO ASI120MM Mini, gain = 97\nExposure = 500 ms\n"
        "Dec = 13.0 deg, Hour angle = -1.00 hr, Pier side = West, Rotator pos = Unknown\n"
        "Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,"
        "RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode\n"
        "INFO: SETTLING STATE CHANGE, Settling started\n"
        '0,0.100,"Mount",0,0,5.000,5.000,0,0,0,,0,,,,8000,50,0\n'
        "INFO: SETTLING STATE CHANGE, Settling complete\n" + rows + "\n"
        "INFO: DITHER by 1.0, 1.0, new lock pos = 1, 2\n"
        "Guiding Ends at 2026-03-22 21:40:00\n")
    # ASIAIR autorun log of the same night (local time CET): sequence, two exposures, guide star lost
    (leo_a / "Logs" / "Autorun_Log_2026-03-22_205500.txt").write_text(
        "2026/03/22 20:55:00 [Autorun|Begin] M 65 Start\n"
        "2026/03/22 20:55:05 Shooting 3 light frames, exposure 180s\n"
        "2026/03/22 21:00:00 Exposure 180.0s image 1#\n"
        "2026/03/22 21:03:10 Exposure 180.0s image 2#\n"
        "2026/03/22 21:20:00 [Guide] Guide star lost\n"
        "2026/03/22 21:45:00 [Autorun|End] M 65 End\n")
    # WBPP log: l0 and l1 stacked (l2 missing), one warning, master light written
    def ln(t):
        return f"[2026-03-23 10:00:00] {t}"
    base = "/Users/x/Astro/Leo/Processing"
    wbpp_lines = [
        "*" * 20, "PixInsight Core 1.9.6 Lockhart (arm64)", "*" * 20, "Weighted Batch Preprocessing 3.2.0",
        # flats are calibrated with the same message - they must not count as lights
        f"Calibration frame 0: /Users/x/Astro/Leo/Flats/f0.fit ---> {base}/calibrated/Flat_BIN-1_SESSION-1/f0_c.xisf",
        f"Calibration frame 0: /Users/x/Astro/Leo/Flats/Flat_1.fit ---> {base}/calibrated/Flat_1_c.xisf",
        f"Calibration frame 0: /Users/x/Astro/Leo/Lights/l0.fit ---> {base}/calibrated/l0_c.xisf",
        f"Calibration frame 1: /Users/x/Astro/Leo/Lights/l1.fit ---> {base}/calibrated/l1_c.xisf",
        "** Warning: negative or insignificant pixel sample values detected after calibration.",
        f"* Loading target file: {base}/calibrated/l0_c.xisf",
        "ch 0 : TFlux = 1.0e+03, TMeanFlux = 6.0e+01, M* = 2.0e-03, N* = 3.0e-03, 1400 PSF fits",
        "ch 0 : sigma_n = 3.0e-03, 74.77% pixels (MRS)",
        "Integration of 2 images:", "Pixel rejection counts:",
        f"{base}/registered/l0_c_d_r.xisf", "     1 :    523785   2.906% (   303141 +    220644 =   1.682% +   1.224%)",
        f"{base}/registered/l1_c_d_r.xisf", "     2 :    100000   0.500% (   303141 +    220644 =   1.682% +   1.224%)",
        "******************** LIGHT FRAMES CALIBRATION ********************",
        "Group of 2 Light frames (2 active)", "Filter   : NoFilter", "Exposure : 180.00s",
        f"Master dark: {base}/master/masterDark_BIN-1_EXPOSURE-180.00s.xisf", "Master flat: none",
        "Calibration completed: 2 Light frames calibrated.", "*" * 66,
        "******************** IMAGE INTEGRATION ********************", "Group of 2 Light frames (2 active)",
        f"Integration completed: master Light saved at path {base}/master/masterLight_BIN-1_EXPOSURE-180.00s_FILTER-NoFilter_RGB.xisf",
        "*" * 60, "* Weighted Batch Preprocessing: 05:53.36"]
    (leo_a / "Processing" / "logs").mkdir(parents=True)
    (leo_a / "Processing" / "logs" / "20260323100000.log").write_text("\n".join(ln(x) for x in wbpp_lines) + "\n")
    wb = leo_a / "Processing" / "master"
    wb.mkdir(parents=True)
    for n in ("masterLight_BIN-1_5202x3465_EXPOSURE-180.00s_FILTER-NoFilter_RGB.xisf",
              "masterLight_BIN-1_5202x3465_EXPOSURE-180.00s_FILTER-NoFilter_RGB_autocrop.xisf",
              "masterDark_BIN-1_5202x3465_EXPOSURE-180.00s.xisf",
              "masterLight_BIN-1_5202x3465_EXPOSURE-180.00s_FILTER-NoFilter_RGB_autocrop_ABE.xisf",
              "Leo_Final_V1.xisf"):                       # steps and finals are allowed in Processing/master
        (wb / n).write_bytes(b"XISF0100")
    (leo_a / "Processing" / "registered").mkdir()
    (leo_a / "Processing" / "registered" / "l0_c_d_r.xisf").write_bytes(b"XISF0100")
    (leo_b / "PI" / "master").mkdir(parents=True)
    (leo_b / "PI" / "master" / "masterLight.xisf").write_bytes(b"XISF0100")      # misplaced intermediate
    (leo_b / "PI" / "master" / "masterDark_BIN-1_EXPOSURE-180.00s.xisf").write_bytes(b"XISF0100")
    (leo_a / "Finals").mkdir(parents=True)
    (leo_a / "Finals" / "leo_session.xisf").write_bytes(b"XISF0100")             # <session>/Finals is allowed
    Image.new("RGB", (64, 48), (20, 20, 30)).save(leo_a / "Finals" / "leo.jpg")
    moon = arc / "Mineral Moon" / "2026-05-25-100"
    _light(moon / "m.fit", "2026-05-25T20:17:00", exp=0.01, ra=178.4, dec=-1.9)
    (arc / "Leer").mkdir(parents=True)
    with open(arc / "M65, M66, NGC 3628 (Leo-Triplett)" / "kaputt.fit", "wb") as fh:
        fh.write(b"not a fits file")

    class TestConfig(Config):
        ARCHIVE_PATH = str(arc)
        DATA_PATH = str(tmp_path / "data")
        SQLALCHEMY_DATABASE_URI = None
        TESTING = True

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        run = run_once(app)
        assert run.phase == "done", run.message
    return app


def test_index_result(app):
    with app.app_context():
        targets = {t.name: t for t in db.session.query(Target)}
        assert set(targets) == {"M 65", "Mineral Moon"}
        leo = targets["M 65"]
        assert leo.common_name == "Leo-Triplett"
        assert leo.catalog_ids == ["M 65", "NGC 3623", "PGC 34612", "UGC 6328", "M 66", "NGC 3628"]
        assert leo.lights == 4                              # 3 + 1, rejected one not counted
        assert leo.exposure_s == pytest.approx(3 * 180 + 120)
        assert leo.constellation == "Leo"
        # catalog (OpenNGC)
        assert leo.catalog_name == "NGC 3623"
        assert leo.object_type == "Galaxie"
        assert leo.magnitude == pytest.approx(9.3, abs=0.1)
        assert leo.catalog_ids[:2] == ["M 65", "NGC 3623"] and "NGC 3628" in leo.catalog_ids
        assert leo.fov_w == pytest.approx(101.6, abs=0.5) and leo.fov_h == pytest.approx(67.7, abs=0.5)
        in_field = {o["id"]: o for o in leo.objects_in_field}
        assert in_field["M 66"]["inside"] and "NGC 3628" in in_field
        assert leo.objects_in_field[0]["id"] in ("M 66", "NGC 3628")   # folder objects first
        assert leo.warnings == []
        sessions = {s.label: s for s in db.session.query(Session).filter_by(target_id=leo.id)}
        assert set(sessions) == {"2026-03-22", ""}
        s = sessions["2026-03-22"]
        assert (s.lights, s.rejected, s.flats, s.darks) == (3, 1, 1, 1)
        assert s.calibrated
        assert len(s.datasets) == 1                          # 752.7 … 755 mm -> one group
        assert s.datasets[0]["focal"] == 755
        assert sessions[""].datasets[0]["focal"] == 792
        assert sessions[""].duplicates == 1
        assert sessions[""].duplicate_of == "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
        # processed / broken files
        assert db.session.query(Frame).filter(Frame.frame_type.is_(None), Frame.error.is_(None)).count() == 1  # loose stack
        assert db.session.query(Frame).filter(Frame.error.is_not(None)).count() == 1
        v = db.session.query(Version).one()
        assert v.thumb and os.path.isfile(os.path.join(app.config["DATA_PATH"], v.thumb))
        assert v.target_id == leo.id
        moon = targets["Mineral Moon"]
        assert moon.catalog_name is None and moon.objects_in_field == []


def test_structure_checks(app):
    from webapp.models import Issue
    with app.app_context():
        codes = {(i.code, i.session_root) for i in db.session.query(Issue)}
        leo_a = "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
        leo_b = "M66, M65, NGC (Leo-Triplett)"
        assert ("darks_bias_present", leo_a) in codes            # darks are reported
        msg = db.session.query(Issue).filter_by(code="darks_bias_present", session_root=leo_a).one().message
        assert "Darks (Canon EOS 1200D)" in msg                  # with the camera the darks belong to
        assert ("no_session_folder", leo_b) in codes             # Lights directly in the target folder
        assert ("duplicate", leo_b) in codes                     # copy of l0.fit
        dup = db.session.query(Issue).filter_by(code="duplicate").one()
        assert f"„{leo_b}“" in dup.message and f"„{leo_a}“" in dup.message      # both folders, complete
        assert f"{leo_b}/Lights/copy_of_l0.fit  ⇄  {leo_a}/Lights/l0.fit" in dup.hint   # both files, complete
        assert dup.path == f"{leo_b}/Lights/copy_of_l0.fit"
        assert ("no_flats", leo_b) in codes
        assert {i.severity for i in db.session.query(Issue).filter_by(code="no_flats")} == {"hint"}   # only a hint
        assert ("loose_frames", "Mineral Moon/2026-05-25-100") in codes
        assert ("no_flats", "Mineral Moon/2026-05-25-100") not in codes   # no flats needed for the Moon
        assert ("dark_exposure", leo_a) not in codes             # 180 s darks for 180 s lights
        assert any(c == "unreadable" for c, _ in codes)
        places = {i.path for i in db.session.query(Issue).filter_by(code="intermediate_location")}
        assert any(p.startswith(f"{leo_b}/PI/master/") for p in places)      # PI/master instead of Processing
        assert not any("/Finals/" in p for p in places)                      # Finals (any format) is fine
        assert f"{leo_b}/loose_stack.fit" in places                          # stack next to raw data
        assert not any("/Processing/" in p for p in places)                  # Processing is fine
    c = app.test_client()
    page = c.get("/checks").data.decode()
    assert "Lights liegen direkt im Zielordner" in page
    assert "Doppelte Bilder" in page
    assert c.get("/checks?g=doppelt&s=warning").status_code == 200


def test_pages_render(app):
    c = app.test_client()
    with app.app_context():
        leo = db.session.query(Target).filter_by(name="M 65").one()
        v = db.session.query(Version).one()
    for url in ["/", "/sessions", "/equipment", "/statistics", "/status", "/healthz",
                f"/target/{leo.id}", "/targets?q=leo", "/targets?focal=792&camera=Canon+EOS+7D+MarkII",
                f"/media/thumbs/{v.id}", f"/media/previews/{v.id}"]:
        r = c.get(url)
        assert r.status_code == 200, url
    r = c.get(f"/target/{leo.id}", headers={"HX-Request": "true"})
    assert b"Leo-Triplett" in r.data and b"<html" not in r.data
    assert b"Mineral Moon" not in c.get("/targets?q=leo").data
    r = c.post(f"/target/{leo.id}/cover/{v.id}")
    assert r.status_code == 200
    assert b"Objekte im Bildfeld" in c.get(f"/target/{leo.id}").data
    assert "52,37° N".encode() in c.get("/statistics").data


def test_phd2_guiding(app):
    from datetime import datetime

    from webapp.models import PhdLog, PhdSection
    with app.app_context():
        lg = db.session.query(PhdLog).one()
        sec = db.session.query(PhdSection).one()
        assert sec.start_utc == datetime(2026, 3, 22, 20, 0, 0)            # local CET -> UTC
        assert sec.end_utc == datetime(2026, 3, 22, 20, 40, 0)
        assert sec.frames == 121 and sec.dithers == 1
        # settling frame (5 px) excluded: RA ±0.2 px, Dec 0.1 px at 6.45″/px
        assert sec.rms_ra == pytest.approx(0.2 * 6.45, abs=0.01)
        assert sec.rms_dec == pytest.approx(0.1 * 6.45, abs=0.01)
        assert lg.version == "2.6.13"
        sid = db.session.query(Session).filter(Session.root.like("%2026-03-22")).one().id
    c = app.test_client()
    page = c.get(f"/session/{sid}").data.decode()
    assert "Guiding (PHD2)" in page and "1,44″" in page                    # RMS total = hypot(1.29, 0.645)
    graph = c.get(f"/session/{sid}/guiding").data.decode()
    assert "<polyline" in graph and "pl-dither" in graph and "pl-exp" in graph
    # the other session (no overlap in time) shows no guiding
    with app.app_context():
        other = db.session.query(Session).filter_by(root="Mineral Moon/2026-05-25-100").one().id
    assert "Guiding (PHD2)" not in c.get(f"/session/{other}").data.decode()
    # status dots in the session overview: green (log, no problems) / orange (no log) / red (problems)
    from webapp.main.phd_view import guiding_status
    with app.app_context():
        st = guiding_status()
        assert st["M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"]["status"] == "ok"
        assert st["Mineral Moon/2026-05-25-100"]["status"] == "none"
        sec = db.session.query(PhdSection).one()
        sec.settle_failed = 3
        db.session.commit()
        bad = guiding_status()["M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"]
        assert bad["status"] == "error" and "Settling" in bad["reasons"][0]
    page = c.get("/sessions").data.decode()
    assert "pl-dot-error" in page and "pl-dot-none" in page


def test_night_log_with_airgus(app):
    from webapp.models import NightAnalysis, NightLog
    with app.app_context():
        kinds = {n.kind for n in db.session.query(NightLog)}
        assert kinds == {"asiair", "phd2"}
        a = db.session.query(NightAnalysis).filter_by(
            session_root="M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22").one()
        assert db.session.query(NightAnalysis).filter_by(session_root="Mineral Moon/2026-05-25-100").count() == 0
        assert a.summary["exposures"] == 2 and a.summary["targets"] == ["M 65"]
        lost = [al for al in a.alerts if al["rule"] == "star-lost"]
        assert lost and lost[0]["time"].startswith("2026-03-22T20:20")      # 21:20 CET -> 20:20 UTC
        sid = db.session.query(Session).filter_by(root=a.session_root).one().id
    c = app.test_client()
    page = c.get(f"/session/{sid}").data.decode()
    assert "Nachtprotokoll" in page and "schwere Meldung" in page
    night = c.get(f"/session/{sid}/night").data.decode()
    assert "Leitstern verloren" in night and "<svg" in night
    sessions = c.get("/sessions").data.decode()
    assert sessions.count("#night") >= 2                                   # a dot per session
    assert "ASIAIR/NINA-Log mit Fehlern" in sessions and "kein ASIAIR/NINA-Log" in sessions


def test_wbpp_log(app):
    from webapp.archive import wbpp
    from webapp.models import Issue, WbppRun
    with app.app_context():
        w = db.session.query(WbppRun).one()
        assert w.target_folder == "M65, M66, NGC 3628 (Leo-Triplett)"
        assert (w.pi_version, w.wbpp_version, w.duration_s) == ("1.9.6 Lockhart (arm64)", "3.2.0", 353.36)
        assert w.lights_used == ["l0", "l1"] and wbpp.master_written(w)
        assert w.frames["l0"] == {"noise": 0.003, "psf_fits": 1400, "rejected_pct": 2.906}
        assert w.warnings == {"negative or insignificant pixel sample values detected after calibration.": 1}
        codes = {i.code for i in db.session.query(Issue)}
        # the target also has a light from 29.03. (copy folder): newer than the run -> master outdated
        assert {"wbpp_outdated", "wbpp_calibration"} <= codes
        leo = db.session.query(Target).filter_by(name="M 65").one().id
        sid = db.session.query(Session).filter_by(root=f"{w.target_folder}/2026-03-22").one().id
    c = app.test_client()
    assert "WBPP-Läufe" not in c.get(f"/target/{leo}").data.decode()        # shown on the session page only
    page = c.get(f"/session/{sid}").data.decode()
    assert '<details id="wbpp" class="pl-phd">' in page                     # collapsed when the page loads
    assert "WBPP-Läufe" in page and "masterLight erzeugt" in page and "masterDark_BIN-1_EXPOSURE-180.00s.xisf" in page
    assert "nicht in diesem Stack" in page and "2,91 %" in page and "2 von 3 Lights dieser Session" in page


def test_wbpp_checks(app):
    from webapp.models import Issue
    with app.app_context():
        found: dict[str, str] = {}
        for i in db.session.query(Issue).filter(Issue.code.in_(("wbpp_steps", "wbpp_calibration_masters", "wbpp_other"))):
            found[i.code] = found.get(i.code, "") + "\n" + i.hint
    assert set(found) == {"wbpp_steps", "wbpp_calibration_masters", "wbpp_other"}
    assert "masterDark_" in found["wbpp_calibration_masters"]          # in PI/master, not Processing/master
    assert "masterLight.xisf" in found["wbpp_other"]          # no WBPP keywords -> not a WBPP master light
    # Processing/master may hold steps and finals
    assert "_autocrop_ABE" not in found["wbpp_other"] and "Leo_Final_V1" not in " ".join(found.values())
    assert "l0_c_d_r.xisf" in found["wbpp_steps"]
    hints = " ".join(found.values())
    assert "FILTER-NoFilter_RGB.xisf" not in hints and "FILTER-NoFilter_RGB_autocrop.xisf\n" not in hints + "\n"
    assert "WBPP" in app.test_client().get("/checks?g=wbpp").data.decode()


def test_star_aberration_diagnostics(app):
    from webapp.models import Issue, SadFrame, SadSeries
    with app.app_context():
        sr = db.session.query(SadSeries).one()
        assert sr.session_root == "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
        assert len(sr.frames) == 3 and all(f.frame_id for f in sr.frames)      # linked to the lights
        assert sr.ai_reviews[0]["verdict"].startswith("Alles gut")
        f0 = next(f for f in sr.frames if f.name == "l0.fit")
        assert set(f0.maps) == {"Mosaic", "Size"}
        codes = {i.code for i in db.session.query(Issue)}
        assert {"sad_tilt", "sad_edge", "sad_tracking", "sad_outliers", "sad_missing"} <= codes
        assert {i.severity for i in db.session.query(Issue).filter(Issue.code.like("sad_%"))} == {"hint"}
        sess = db.session.query(Session).filter_by(root=sr.session_root).one()
        sid, fid = sess.id, f0.id
        outlier = db.session.query(SadFrame).filter_by(name="l2.fit").one().id
    c = app.test_client()
    page = c.get(f"/session/{sid}").data.decode()
    assert "Optik-Diagnose" in page and "Verlauf über die Nacht" in page and "Alles gut" in page
    assert f"f={outlier}" in page                                   # outlier listed and clickable
    r = c.get(f"/sad/map/{fid}/Mosaic")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert c.get(f"/sad/map/{fid}/Coma").status_code == 404
    assert c.get(f"/session/{sid}/sad-map?f={fid}&k=Size").status_code == 200
    assert '<details id="sad" class="pl-phd pl-sad">' in page               # collapsed when the page loads
    sad_head = page.split('<details id="sad"', 1)[1].split("</summary>", 1)[0]
    assert "pl-dot pl-dot-hint" in sad_head                         # poor results are hints, not errors
    overview = c.get("/sessions").data.decode()
    assert ">SAD</th>" in overview and f"/session/{sid}#sad" in overview     # SAD column after WBPP
    assert overview.index(">WBPP</th>") < overview.index(">SAD</th>")


def test_file_tables(app):
    c = app.test_client()
    for kind in ("light", "flat", "dark", "bias", "ignored", "intermediate", "error"):
        assert c.get(f"/files?kind={kind}").status_code == 200, kind
    assert c.get("/files?kind=nonsense").status_code == 404
    darks = c.get("/files?kind=dark").data.decode()
    assert "2026-03-22/Darks/" in darks and "d.fit" in darks
    ignored = c.get("/files?kind=ignored").data.decode()
    assert "loose_stack.fit" in ignored and "Processing/</span>stack.fit" not in ignored
    inter = c.get("/files?kind=intermediate").data.decode()
    assert "stack.fit" in inter and "masterLight.xisf" in inter
    assert "Nicht in „Processing“" in inter and "In „Processing“ – in Ordnung" in inter      # groups
    bad = c.get("/files?kind=intermediate&rule=not_in_processing").data.decode()
    assert "masterLight.xisf" in bad and "Processing/</span>stack.fit" not in bad
    ok = c.get("/files?kind=intermediate&rule=ok").data.decode()
    assert "Processing/</span>stack.fit" in ok and "masterLight.xisf" not in ok
    cal = c.get("/files?kind=intermediate&rule=wbpp_calibration_master").data.decode()
    assert "masterDark_" in cal and "Kalibrier-Master" in cal and "Processing/master/masterDark" not in cal
    pm = c.get("/files?kind=intermediate&rule=processing_master").data.decode()
    assert "_autocrop_ABE" in pm and "masterDark_BIN-1_5202x3465" in pm
    assert "Leo_Final_V1" in c.get("/files?kind=intermediate&rule=final").data.decode()
    sf = c.get("/files?kind=intermediate&rule=finals").data.decode()
    assert "leo_session.xisf" in sf
    assert "leo_session.xisf" not in c.get("/files?kind=intermediate&rule=not_in_processing").data.decode()
    lights = c.get("/files?kind=light&q=Leo+copy").data.decode()
    assert "copy_of_l0.fit" in lights and "l1.fit" not in lights
    assert "doppelt" in c.get("/files?kind=light&state=duplicate").data.decode()
    assert "r.fit" in c.get("/files?kind=light&state=rejected").data.decode()
    assert "kaputt.fit" in c.get("/files?kind=error").data.decode()
    status = c.get("/status").data.decode()
    assert "/files?kind=bias" in status and "/files?kind=ignored" in status


def test_indexer_status_and_trigger(app):
    import json
    import time
    from pathlib import Path

    from webapp import indexer
    cfg = app.config
    data = Path(cfg["DATA_PATH"])
    c = app.test_client()
    page = c.get("/status").data.decode()
    assert "Kein Indexer-Dienst aktiv" in page and "alle 15 min" in page and "Jetzt scannen" in page
    assert "Letzter Lauf" in page and "nur von Hand" in page
    # a running indexer service (fresh heartbeat): the button only leaves a request for it
    (data / "indexer.heartbeat").write_text(json.dumps(dict(pid=1, interval=900, state="waiting", updated=time.time(),
                                                           next_run="2030-01-01T12:00:00")))
    assert "Indexer-Dienst aktiv" in c.get("/status/state").data.decode()
    r = c.post("/status/scan")
    assert r.status_code == 302 and (data / "indexer.request").exists()
    assert "Lauf angefordert" in c.get("/status/state").data.decode()
    assert indexer._take_request(cfg) and not (data / "indexer.request").exists()
    # never two runs at once
    with indexer.run_lock(cfg) as got:
        assert got and indexer.is_running(cfg)
        with app.app_context():
            assert indexer.run_once(app) is None
    assert not indexer.is_running(cfg)


def test_unreadable_folder_is_not_deleted(app):
    import os
    import stat
    arc = app.config["ARCHIVE_PATH"]
    folder = os.path.join(arc, "M66, M65, NGC (Leo-Triplett)")
    with app.app_context():
        before = db.session.query(Frame).filter(Frame.target_folder == "M66, M65, NGC (Leo-Triplett)").count()
        assert before > 0
        os.chmod(folder, 0)                       # like an SMB hiccup: the folder cannot be listed
        try:
            run = run_once(app)
        finally:
            os.chmod(folder, stat.S_IRWXU)
        assert run.phase == "done" and "nicht lesbar" in run.message
        after = db.session.query(Frame).filter(Frame.target_folder == "M66, M65, NGC (Leo-Triplett)").count()
        assert after == before                    # nothing below the unreadable folder was removed


def test_orphaned_thumbnails_are_removed(app):
    import os
    import time
    data = app.config["DATA_PATH"]
    orphan = os.path.join(data, "thumbs", "zz", "orphan.jpg")
    os.makedirs(os.path.dirname(orphan), exist_ok=True)
    with open(orphan, "wb") as fh:
        fh.write(b"x")
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    fresh = os.path.join(data, "sad", "zz", "being_written.tmp")       # younger than 5 min: kept
    os.makedirs(os.path.dirname(fresh), exist_ok=True)
    open(fresh, "wb").close()
    with app.app_context():
        v = db.session.query(Version).one()
        run = run_once(app)
        assert "verwaiste" in (run.message or "")
        assert not os.path.exists(orphan) and not os.path.isdir(os.path.dirname(orphan))
        assert os.path.exists(fresh)
        assert os.path.isfile(os.path.join(data, v.thumb)) and os.path.isfile(os.path.join(data, v.preview))


def test_rescan_is_incremental_and_removes_deleted(app):
    with app.app_context():
        run = run_once(app)
        assert run.files_read == 0                   # nothing changed
        arc = app.config["ARCHIVE_PATH"]
        os.remove(os.path.join(arc, "Mineral Moon", "2026-05-25-100", "m.fit"))
        run_once(app)
        assert db.session.query(Target).filter_by(name="Mineral Moon").count() == 0


def test_airgus_status(app, monkeypatch):
    import importlib
    import importlib.metadata as md

    from webapp.archive import nightlog
    c = app.test_client()
    page = c.get("/status/state").data.decode()
    assert "Airgus" in page and "aktiv" in page and "79d6afd" in page and "ASIAIR" in page
    with app.app_context():
        i = nightlog.info()
    assert i["installed"] and not i["broken"] and not i["mismatch"] and i["commit"] == "79d6afd"
    # a module that can no longer be imported (Airgus renamed something)
    real = importlib.import_module

    def fake(name, *a, **k):
        if name == "airgus.timeline":
            raise ImportError("renamed")
        return real(name, *a, **k)
    monkeypatch.setattr(importlib, "import_module", fake)
    page = c.get("/status/state").data.decode()
    assert "nicht nutzbar" in page and "airgus.timeline" in page
    monkeypatch.setattr(importlib, "import_module", real)
    # not installed
    def missing(name):
        raise md.PackageNotFoundError(name)
    monkeypatch.setattr(md, "distribution", missing)
    page = c.get("/status/state").data.decode()
    assert "nicht installiert" in page and "WITH_AIRGUS=1" in page


def test_wbpp_session_dot(app):
    from webapp.main.wbpp_view import session_status
    leo_a = "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
    with app.app_context():
        st = session_status()
    assert st[leo_a]["status"] == "ok"
    assert any(n.startswith("2 von ") for n in st[leo_a]["notes"])
    page = app.test_client().get("/sessions").data.decode()
    assert ">WBPP</th>" in page and "mit WBPP gestackt, keine Fehler" in page and "#wbpp" in page


def test_reorganised_archive_is_taken_over_without_reading(app):
    """Moving folders (e.g. removing the year level) keeps the rows; only the paths change."""
    import shutil

    from webapp.models import Camera, Issue, Optic, Rig, RigSessionOverride
    arc = app.config["ARCHIVE_PATH"]
    old_root = "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
    with app.app_context():
        ids = {f.path.rsplit("/", 1)[-1]: f.id for f in db.session.query(Frame).filter_by(session_root=old_root)}
        sid = db.session.query(Session).filter_by(root=old_root).one().id
        v = db.session.query(Version).one()
        t = db.session.get(Target, v.target_id)
        t.cover_path = v.path
        cam, opt = Camera(name="Cam"), Optic(name="Scope", kind="telescope", focal_mm=750)
        rig = Rig(name="Rig", camera=cam, optic=opt)
        db.session.add_all([cam, opt, rig])
        db.session.flush()
        db.session.add(RigSessionOverride(session_root=old_root, rig_id=rig.id))
        db.session.commit()
        old_cover, tid = v.path, t.id
    # put the target into a year folder (old layout) - like a reorganisation in the other direction
    os.makedirs(os.path.join(arc, "2026"))
    shutil.move(os.path.join(arc, "M65, M66, NGC 3628 (Leo-Triplett)"), os.path.join(arc, "2026"))
    with app.app_context():
        run = run_once(app)
        assert run.phase == "done" and run.files_read == 0      # nothing read again
        new_root = "2026/" + old_root
        moved = {f.path.rsplit("/", 1)[-1]: f.id for f in db.session.query(Frame).filter_by(session_root=new_root)}
        assert moved == ids                                     # same rows, new paths
        assert db.session.query(Session).filter_by(root=new_root).one().id == sid   # session page keeps its URL
        assert db.session.get(Target, tid).cover_path == "2026/" + old_cover
        assert db.session.query(RigSessionOverride).one().session_root == new_root
        hints = {(i.code, i.message) for i in db.session.query(Issue).filter_by(code="year_folder")}
        assert any("2026/M65" in m for _, m in hints)           # the year folder is reported


def test_archive_not_mounted(app):
    """Unmounted archive: the run fails without deleting anything, the pages keep working from the database."""
    import shutil

    from webapp.models import SadFrame
    c = app.test_client()
    with app.app_context():
        fid = db.session.query(SadFrame).filter_by(name="l0.fit").one().id
        frames = db.session.query(Frame).count()
        sid = db.session.query(Session).filter(Session.lights > 0).first().id
    assert c.get(f"/sad/map/{fid}/Mosaic").status_code == 200        # rendered into the cache
    arc = app.config["ARCHIVE_PATH"]
    shutil.move(arc, arc + ".unmounted")                             # like an SMB share that is gone
    try:
        with app.app_context():
            run = run_once(app)
            assert run.phase == "failed" and run.message.startswith("Archiv nicht gefunden")
            assert db.session.query(Frame).count() == frames            # nothing deleted
        for url in ("/", "/sessions", f"/session/{sid}", "/checks", "/status", "/statistics"):
            page = c.get(url)
            assert page.status_code == 200, url
            assert "Letzter Indexlauf fehlgeschlagen" in page.data.decode(), url   # visible on every page
        status = c.get("/status/state").data.decode()
        assert "nicht gefunden – Mount bzw. ARCHIVE_PATH prüfen" in status and "Archiv nicht gefunden" in status
        assert c.get(f"/sad/map/{fid}/Mosaic").status_code == 200    # served from the cache
        assert c.get(f"/sad/map/{fid}/Size").status_code == 404      # never rendered, source missing
    finally:
        shutil.move(arc + ".unmounted", arc)
    with app.app_context():
        assert run_once(app).phase == "done"
    assert "Letzter Indexlauf fehlgeschlagen" not in c.get("/").data.decode()   # gone after a good run
    assert "erreichbar" in c.get("/status/state").data.decode()


def test_live_update_version(app):
    """Pages poll /status/version: the run id changes with every finished run, the data part only when
    the indexed data changed."""
    c = app.test_client()
    v1 = c.get("/status/version").get_json()
    assert v1["run"] > 0 and v1["phase"] == "done" and v1["running"] is False
    page = c.get("/sessions").data.decode()
    assert f'data-run="{v1["run"]}"' in page and f'data-data="{v1["data"]}"' in page
    with app.app_context():
        run_once(app)                                    # nothing changed in the archive
    v2 = c.get("/status/version").get_json()
    assert v2["run"] == v1["run"] + 1 and v2["data"] == v1["data"]
    os.remove(os.path.join(app.config["ARCHIVE_PATH"], "Mineral Moon", "2026-05-25-100", "m.fit"))
    with app.app_context():
        run_once(app)
    assert c.get("/status/version").get_json()["data"] != v1["data"]


def test_calibration_descriptions():
    """Messages about darks/bias name the frame type's camera and its exposure, ISO/gain and temperature."""
    from types import SimpleNamespace as F

    from webapp.archive.checks import _cal_props, _cams
    bias = [F(camera="Canon EOS 550D", exptime=0.00025, iso=None, gain=None, temp=19.4,
              path="x/Bias_0.25ms_Bin1_ISO800_20260806-1.fit"),
            F(camera="Canon EOS 550D", exptime=0.00025, iso=None, gain=None, temp=22.0,
              path="x/Bias_0.25ms_Bin1_ISO800_20260806-2.fit")]
    assert _cams(bias) == "Canon EOS 550D"
    assert _cal_props(bias) == " (0.25 ms, ISO 800, 19–22 °C)"          # ISO from the file name
    dwarf = [F(camera=None, exptime=30.0, iso=None, gain=40.0, temp=None, path="d/dark.fits")]
    assert _cal_props(dwarf) == " (30 s, Gain 40)"
    assert _cams(dwarf, [F(camera="DWARF 3")]) == "Kamera nicht im Header, Lights: DWARF 3"
    assert _cams(dwarf) == "Kamera unbekannt"


def test_copied_calibration_reported_once(app):
    """Darks copied into a second session folder: one duplicate message, no second "contains darks" hint."""
    import shutil

    from webapp.models import Issue
    arc = app.config["ARCHIVE_PATH"]
    leo_a = "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
    leo_b = "M66, M65, NGC (Leo-Triplett)"
    os.makedirs(os.path.join(arc, leo_b, "Darks"))
    shutil.copy2(os.path.join(arc, leo_a, "Darks", "d.fit"), os.path.join(arc, leo_b, "Darks", "d.fit"))
    with app.app_context():
        run_once(app)
        present = {i.session_root for i in db.session.query(Issue).filter_by(code="darks_bias_present")}
        assert leo_a in present and leo_b not in present           # the copy is not reported again here
        copies = db.session.query(Issue).filter_by(code="calibration_copies").one()
        assert "1 Darks (Canon EOS 1200D)" in copies.message and leo_b in copies.message


def test_partial_calibration_copies_merged(app):
    """Two darks copied into one folder, one of them also into a third: one message for the whole set."""
    import shutil
    from pathlib import Path

    from webapp.models import Issue
    arc = Path(app.config["ARCHIVE_PATH"])
    leo_a = arc / "M65, M66, NGC 3628 (Leo-Triplett)" / "2026-03-22"
    _light(leo_a / "Darks" / "d2.fit", "2026-03-23T01:20:00", imagetyp="Dark")
    for dest, names in ((arc / "M66, M65, NGC (Leo-Triplett)", ("d.fit", "d2.fit")),
                        (arc / "Mineral Moon" / "2026-05-25-100", ("d.fit",))):
        (dest / "Darks").mkdir(parents=True, exist_ok=True)
        for n in names:
            shutil.copy2(leo_a / "Darks" / n, dest / "Darks" / n)
    with app.app_context():
        run_once(app)
        copies = db.session.query(Issue).filter_by(code="calibration_copies").one()
        assert "2 Darks" in copies.message and "3 Ordnern" in copies.message
        assert copies.message.index("2026-03-22") < copies.message.index("Mineral Moon")   # oldest folder first
        assert copies.hint.count("  ⇄  ") == 3


def test_same_second_shot_is_not_a_copy(app):
    """"x (2).raf" from a second shot in the same second differs in size; only a real copy is reported."""
    import shutil
    from pathlib import Path

    from webapp.models import Issue
    flats = Path(app.config["ARCHIVE_PATH"]) / "M65, M66, NGC 3628 (Leo-Triplett)" / "2026-03-22" / "Flats"
    _light(flats / "f.fit", "2026-03-23T02:00:00", exp=0.5, imagetyp="Flat")
    shutil.copy2(flats / "f.fit", flats / "f (2).fit")                       # other shot, same second
    with open(flats / "f (2).fit", "ab") as fh:
        fh.write(b"\0" * 2880)
    with app.app_context():
        run_once(app)
        assert not db.session.query(Issue).filter_by(code="duplicate_in_folder").count()
    shutil.copy2(flats / "f.fit", flats / "f (3).fit")                       # real copy
    with app.app_context():
        run_once(app)
        dup = db.session.query(Issue).filter_by(code="duplicate_in_folder").one()
        assert dup.count == 1 and "f (3).fit  ⇄  " in dup.hint and dup.hint.rstrip().endswith("/f.fit")


def test_several_archives(app, tmp_path):
    """Switching to named archives takes the rows over (paths get the name, ids stay), targets with the
    same catalogue number are merged across archives, a missing archive keeps its rows."""
    import shutil

    from webapp.models import IndexRun, SadFrame
    arc = app.config["ARCHIVE_PATH"]
    dwarf = tmp_path / "dwarf"
    _light(dwarf / "M66 (Leo)" / "2026-04-02 DWARF3" / "Lights" / "d1.fit", "2026-04-02T22:00:00", exp=15.0,
           camera="DWARF3", focal=150.0)
    leo_a = "M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22"
    with app.app_context():
        sid = db.session.query(Session).filter_by(root=leo_a).one().id
        n_frames = db.session.query(Frame).count()
    app.config["ARCHIVES"] = {"Astro": arc, "Dwarf": str(dwarf)}
    with app.app_context():
        run = run_once(app)
        assert run.phase == "done", run.message
        assert run.files_read == 1                                   # only the new DWARF light was read
        paths = [f.path for f in db.session.query(Frame)]
        assert len(paths) == n_frames + 1 and all(p.startswith(("Astro/", "Dwarf/")) for p in paths)
        s = db.session.get(Session, sid)
        assert s.root == "Astro/" + leo_a                            # same session, new root
        d = db.session.query(Session).filter_by(root="Dwarf/M66 (Leo)/2026-04-02 DWARF3").one()
        assert d.target_id == s.target_id                            # merged by catalogue number (M 66)
        assert not db.session.query(Issue).filter_by(code="year_folder").count()
        fid = db.session.query(SadFrame).first().id
        dsid = d.id
    c = app.test_client()
    assert str(dwarf / "M66 (Leo)" / "2026-04-02 DWARF3") in c.get(f"/session/{dsid}").data.decode()
    assert c.get(f"/sad/map/{fid}/Mosaic").status_code == 200
    status = c.get("/status").data.decode()
    assert "Archiv „Astro“" in status and "Archiv „Dwarf“" in status

    shutil.rmtree(dwarf)                                             # mount gone
    with app.app_context():
        run = run_once(app)
        assert run.phase == "done" and "Archiv „Dwarf“ nicht gefunden" in run.message
        assert db.session.query(Frame).filter(Frame.path.startswith("Dwarf/")).count() == 1   # kept
        assert db.session.query(IndexRun).count() >= 3
    assert "nicht gefunden – Mount bzw. ARCHIVES prüfen" in c.get("/status").data.decode()
    dwarf.mkdir()                                                    # empty mount point (Docker creates it)
    with app.app_context():
        run = run_once(app)
        assert run.phase == "done" and "Archiv „Dwarf“ ist leer" in run.message
        assert db.session.query(Frame).filter(Frame.path.startswith("Dwarf/")).count() == 1


def test_fits_with_raw_extension(app):
    """A FITS file renamed to .raf is read by its content (exposure counted) and reported; a .raf that is
    neither RAF nor FITS is unreadable instead of a light without header."""
    from pathlib import Path

    lights = Path(app.config["ARCHIVE_PATH"]) / "Perseiden" / "2026-08-12" / "Lights"
    _light(lights / "2026-08-13_03-50-32.raf", "2026-08-14T20:39:11.086170", camera="Canon EOS 550D")
    lights.joinpath("kaputt.raf").write_bytes(b"\0" * 4096)
    with app.app_context():
        run_once(app)
        f = db.session.query(Frame).filter(Frame.path.endswith("03-50-32.raf")).one()
        assert f.exptime == 180.0 and f.date_obs is not None and f.camera == "Canon EOS 550D"
        assert f.frame_type == "light" and f.error is None
        bad = db.session.query(Frame).filter(Frame.path.endswith("kaputt.raf")).one()
        assert "keine RAF-Datei" in bad.error
        codes = {i.code: i for i in db.session.query(Issue).filter_by(session_root="Perseiden/2026-08-12")}
        assert "missing_header" not in codes
        w = codes["wrong_extension"]
        assert w.message == "1 Datei(en) mit Endung „.raf“ sind FITS-Dateien" and "03-50-32.raf" in w.hint


def test_foreign_lights(app):
    """A light whose header names another object (or points elsewhere) than its folder is reported."""
    from pathlib import Path

    arc = Path(app.config["ARCHIVE_PATH"])
    leo_a = arc / "M65, M66, NGC 3628 (Leo-Triplett)" / "2026-03-22" / "Lights"
    _fits(leo_a / "m31.fit", IMAGETYP="Light", EXPTIME=180.0, **{"DATE-OBS": "2026-03-23T02:30:00"},
          INSTRUME="Canon EOS 1200D", FOCALLEN=755.0, RA=10.68, DEC=41.27, OBJECT="M 31")
    _fits(leo_a / "named.fit", IMAGETYP="Light", EXPTIME=180.0, **{"DATE-OBS": "2026-03-23T02:40:00"},
          INSTRUME="Canon EOS 1200D", FOCALLEN=755.0, RA=170.08, DEC=12.95, OBJECT="Leo Triplet")
    per = arc / "Perseiden" / "2026-08-12"
    _fits(per / "Lights" / "p.fit", IMAGETYP="Light", EXPTIME=180.0, **{"DATE-OBS": "2026-08-14T20:39:11"},
          INSTRUME="Canon EOS 550D", RA=11.05, DEC=41.4, OBJECT="M 31")
    with app.app_context():
        run_once(app)
        found = {(i.session_root, i.severity): i for i in db.session.query(Issue).filter_by(code="foreign_lights")}
        leo = found[("M65, M66, NGC 3628 (Leo-Triplett)/2026-03-22", "warning")]
        assert leo.count == 1 and "OBJECT „M 31“" in leo.message and leo.hint.endswith("/m31.fit")
        assert "named.fit" not in leo.hint                       # OBJECT without catalogue id, on target
        per_i = found[("Perseiden/2026-08-12", "hint")]          # folder without catalogue number: hint
        assert "OBJECT „M 31“" in per_i.message


def test_acknowledge_issue(app):
    """A acknowledged issue is hidden (also from the badge) until its message changes."""
    from webapp.models import IssueAck
    c = app.test_client()
    with app.app_context():
        i = db.session.query(Issue).filter_by(severity="error").first()
        iid, msg, key = i.id, i.message, i.key
    before = c.get("/checks").data.decode()
    assert msg in before
    r = c.post(f"/checks/{iid}/ack", data={"next": "/checks"})
    assert r.status_code == 302
    after = c.get("/checks").data.decode()
    assert msg not in after and "1 quittiert" in after
    assert msg in c.get("/checks?ack=1").data.decode()
    with app.app_context():
        run_once(app)                                            # survives a new index run
        assert db.session.query(Issue).filter_by(message=msg).one().key == key
        db.session.query(IssueAck).one().message = "älterer Text"  # message changed -> back again
        db.session.commit()
    assert msg in c.get("/checks").data.decode()
    with app.app_context():
        iid = db.session.query(Issue).filter_by(message=msg).one().id
    c.post(f"/checks/{iid}/ack")
    c.post(f"/checks/{iid}/unack")
    assert msg in c.get("/checks").data.decode()


def test_daily_backup(app):
    """The first index run of a day copies the database; old copies beyond BACKUP_KEEP are removed."""
    import sqlite3
    from datetime import date

    from webapp import backup
    folder = backup.backup_dir(app.config)
    today = folder / f"photonledger-{date.today().isoformat()}.db"
    assert today.is_file()                                        # made by the fixture's index run
    with sqlite3.connect(today) as con:
        assert con.execute("select count(*) from frame").fetchone()[0] == 0   # state before that run
    assert backup.daily(app.config) is None                       # once per day
    app.config["BACKUP_KEEP"] = 2
    for d in (date(2026, 1, 1), date(2026, 1, 2)):
        assert backup.daily(app.config, today=d)
    assert [b["name"] for b in backup.listing(app.config)] == [today.name, "photonledger-2026-01-02.db"]
    status = app.test_client().get("/status").data.decode()
    assert "2 von 2 Tageskopien" in status


def test_version(app):
    """The version comes from the VERSION file (or APPVERSION of the image) and is shown on the indexer page."""
    from pathlib import Path

    from webapp.config import VERSION
    assert VERSION == (Path(__file__).parents[1] / "VERSION").read_text().strip()
    assert VERSION.count(".") == 2
    assert app.config["APPVERSION"] in app.test_client().get("/status").data.decode()


def test_severity_counts_follow_group(app):
    """The severity buttons count the issues of the selected group, not of all groups."""
    import re
    c = app.test_client()
    with app.app_context():
        from webapp.main.routes import _check_group
        hints = [i for i in db.session.query(Issue).filter_by(severity="hint")]
        group = _check_group(hints[0].code)
        n = sum(1 for i in hints if _check_group(i.code) == group)
    page = c.get(f"/checks?g={group}").data.decode()
    assert re.search(r"Hinweis \((\d+)\)", page).group(1) == str(n)
    shown = c.get(f"/checks?g={group}&s=hint").data.decode().count('class="pl-issue pl-issue-hint"')
    assert shown == n
