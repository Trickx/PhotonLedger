"""Rigs: components, automatic and manual assignment, statistics."""
import pytest

from tests.test_app import app  # noqa: F401  (fixture: fake archive, indexed)
from webapp import db
from webapp.models import Camera, Optic, Rig, RigMatch, RigSessionOverride, Session


@pytest.fixture()
def client(app):  # noqa: F811
    return app.test_client()


def _setup(c):
    c.post("/rigs/camera", data=dict(name="Canon 1200D", header_name="Canon EOS 1200D", pixel_um="4,29",
                                     width_px="5202", height_px="3465"))
    c.post("/rigs/camera", data=dict(name="Canon 7D Mark II", header_name="Canon EOS 7D MarkII", pixel_um="4,1",
                                     width_px="5496", height_px="3670"))
    c.post("/rigs/optic", data=dict(name="Skywatcher 150P", kind="telescope", focal_mm="750", aperture_mm="150"))
    c.post("/rigs/optic", data=dict(name="Canon EF 70-300mm L IS USM", kind="zoom", focal_mm="70",
                                    focal_max_mm="300", f_number="4", f_number_max="5,6"))
    c.post("/rigs/corrector", data=dict(name="Komakorrektor", kind="flattener", factor="1"))


def test_components_and_rig_specs(app, client):  # noqa: F811
    _setup(client)
    with app.app_context():
        cam = db.session.query(Camera).filter_by(name="Canon 1200D").one()
        tele = db.session.query(Optic).filter_by(name="Skywatcher 150P").one()
        zoom = db.session.query(Optic).filter_by(kind="zoom").one()
        assert (zoom.focal_mm, zoom.focal_max_mm, zoom.f_number_max) == (70, 300, 5.6)
        ids = (cam.id, tele.id, zoom.id)
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids[0], optic_id=ids[1], corrector_id="1", active="1"))
    with app.app_context():
        r = db.session.query(Rig).filter_by(name="Newton").one()
        assert r.focal_range == (750, 750)
        assert r.f_ratio_range == (5.0, 5.0)
        assert r.scale_at(750) == pytest.approx(1.18, abs=0.01)
        assert r.fov_at(750)[0] == pytest.approx(102.3, abs=0.2)
    page = client.get("/rigs").data.decode()
    assert "Newton" in page and "1,18″/px" in page and "Festbrennweite" in page


def test_auto_manual_and_session_assignment(app, client):  # noqa: F811
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    client.post("/rigs/rig", data=dict(name="Zoom 7D", camera_id=ids["Canon 7D Mark II"],
                                       optic_id=ids["Canon EF 70-300mm L IS USM"], active="1"))
    eq = client.get("/equipment").data.decode()
    assert "automatisch" in eq                      # 1200D @ 755 mm -> Newton
    assert "keinem Rig zugeordnet" in eq            # 7D @ 792 mm is outside 70-300 mm
    with app.app_context():
        newton = db.session.query(Rig).filter_by(name="Newton").one().id
        zoom = db.session.query(Rig).filter_by(name="Zoom 7D").one().id
    # manual assignment of the 792 mm data set
    client.post("/equipment/assign", data=dict(camera="Canon EOS 7D MarkII", focal="792", rig_id=zoom))
    with app.app_context():
        assert db.session.query(RigMatch).one().rig_id == zoom
    eq = client.get("/equipment").data.decode()
    assert "keinem Rig zugeordnet" not in eq and "manuell" in eq
    # session override
    with app.app_context():
        sid = db.session.query(Session).filter(Session.root.like("%2026-03-22")).one().id
    client.post(f"/session/{sid}/rig", data=dict(rig_id=zoom))
    with app.app_context():
        assert db.session.query(RigSessionOverride).one().rig_id == zoom
    assert "abweichend für diese Session" in client.get(f"/session/{sid}").data.decode()
    client.post(f"/session/{sid}/rig", data=dict(rig_id=""))
    with app.app_context():
        assert db.session.query(RigSessionOverride).count() == 0
    # a component used by a rig cannot be deleted
    client.post(f"/rigs/optic/{ids['Skywatcher 150P']}/delete")
    with app.app_context():
        assert db.session.get(Optic, ids["Skywatcher 150P"]) is not None
    client.post(f"/rigs/rig/{newton}/delete")
    with app.app_context():
        assert db.session.get(Rig, newton) is None


def test_zoom_matches_any_focal_in_range(app):  # noqa: F811
    from webapp.main.rigs import candidates
    with app.app_context():
        cam = Camera(name="7D", header_name="Canon EOS 7D MarkII", pixel_um=4.1)
        zoom = Optic(name="70-300", kind="zoom", focal_mm=70, focal_max_mm=300)
        rig = Rig(name="z", camera=cam, optic=zoom)
        assert candidates([rig], "Canon EOS 7D MarkII", 200, 0.03) == [rig]
        assert candidates([rig], "canon eos 7d markii", 305, 0.03) == [rig]      # within tolerance
        assert candidates([rig], "Canon EOS 7D MarkII", 792, 0.03) == []
        assert candidates([rig], "Canon EOS 550D", 200, 0.03) == []


def test_import_cameras_from_headers(app, client):  # noqa: F811
    client.post("/rigs/cameras-from-headers")
    with app.app_context():
        cams = {c.header_name: c for c in db.session.query(Camera)}
        assert cams["Canon EOS 1200D"].pixel_um == pytest.approx(4.29)
        assert cams["Canon EOS 1200D"].width_px == 5202


def test_usage_statistics(app, client):  # noqa: F811
    from webapp.main.usage import NO_RIG, usage_statistics
    from webapp.models import Frame
    _setup(client)
    with app.app_context():
        cam = db.session.query(Camera).filter_by(name="Canon 1200D").one()
        tele = db.session.query(Optic).filter_by(name="Skywatcher 150P").one()
        ids = (cam.id, tele.id)
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids[0], optic_id=ids[1], active="1"))
    with app.app_context():
        u = usage_statistics(app.config["FOCAL_TOLERANCE"])
        usable = db.session.query(Frame).filter_by(frame_type="light", rejected=False, duplicate=False, error=None).count()
        for key in ("rigs", "cameras", "optics", "filters"):         # every usable light counted once per table
            assert sum(r["lights"] for r in u[key]) == usable, key
            assert abs(sum(r["share"] for r in u[key]) - 1) < 1e-6
        names = [r["name"] for r in u["rigs"]]
        assert "Newton" in names
        assert names[-1] == NO_RIG or NO_RIG not in names                    # lights without rig come last
        cams = [r["name"] for r in u["cameras"]]
        assert "Canon 1200D" in cams and "Canon EOS 1200D" not in cams       # header name maps to the camera
        assert len(cams) == len(set(cams))
    page = client.get("/statistics").data.decode()
    assert "Nutzung der Ausrüstung" in page and "Newton" in page and "Teleskope &amp; Objektive" in page


def test_sessions_show_rig_and_session_page_full_path(app, client):  # noqa: F811
    page = client.get("/sessions").data.decode()
    assert "<th>Rig</th>" in page and "<th>Ordner</th>" not in page and "kein Rig" in page
    _setup(client)
    with app.app_context():
        cam = db.session.query(Camera).filter_by(name="Canon 1200D").one()
        tele = db.session.query(Optic).filter_by(name="Skywatcher 150P").one()
        ids = (cam.id, tele.id)
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids[0], optic_id=ids[1], active="1"))
    assert ">Newton</span>" in client.get("/sessions").data.decode()
    with app.app_context():
        s = db.session.query(Session).filter(Session.lights > 0).first()
        sid, full = s.id, app.config["ARCHIVE_PATH"].rstrip("/") + "/" + s.root
    assert full in client.get(f"/session/{sid}").data.decode()
