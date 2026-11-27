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
    page = client.get(f"/session/{sid}").data.decode()
    assert "für diese Session gewählt" in page and "Rig · Zoom 7D" in page
    assert "Öffnungsverh." in page and "Bildmaßstab" in page and "Abtastung" in page   # rig data as on the rigs page
    assert "Rig ändern" in page and "Automatisch zuordnen" in page
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


def test_session_without_rig_warning(app, client):  # noqa: F811
    """A session whose data set no rig covers is a warning; it disappears right after the assignment."""
    from webapp.archive import checks
    from webapp.models import Issue

    def warnings():
        with app.app_context():
            return {i.session_root: i.message for i in db.session.query(Issue).filter_by(code="no_rig")}

    assert not warnings()                                  # no rigs defined: rigs not in use, no warnings
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
    client.post("/rigs/rig", data=dict(name="Zoom 7D", camera_id=ids["Canon 7D Mark II"],
                                       optic_id=ids["Canon EF 70-300mm L IS USM"], active="1"))
    w = warnings()                                         # refreshed at once, not only at the next run
    leo = next(r for r in w if r.endswith("2026-03-22"))
    assert "kein Rig für Canon EOS 1200D 755 mm" in w[leo]
    with app.app_context():
        checks.run(app.config)                             # also part of every index run
    assert leo in warnings()
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    assert "Canon EOS 1200D 755 mm" not in " ".join(warnings().values())
    page = client.get("/checks?g=rigs").data.decode()
    assert "Rigs" in page


def test_rig_photo(app, client):  # noqa: F811
    """A rig photo is uploaded, resized to JPEG in DATA_PATH/rigs, shown on the rigs page, replaced and removed."""
    import io
    from pathlib import Path

    from PIL import Image
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    with app.app_context():
        rid = db.session.query(Rig).filter_by(name="Newton").one().id

    def png(w, h):
        b = io.BytesIO()
        Image.new("RGB", (w, h), (200, 30, 30)).save(b, "PNG")
        b.seek(0)
        return b
    r = client.post(f"/rigs/rig/{rid}/photo", data={"photo": (png(4000, 3000), "newton.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        first = db.session.get(Rig, rid).photo
    f = Path(app.config["DATA_PATH"]) / first
    with Image.open(f) as im:
        assert im.format == "JPEG" and max(im.size) == 1600
    assert f"/rigs/rig/{rid}/photo" in client.get("/rigs").data.decode()
    assert client.get(f"/rigs/rig/{rid}/photo").mimetype == "image/jpeg"
    r = client.post(f"/rigs/rig/{rid}/photo", data={"photo": (io.BytesIO(b"kein Bild"), "x.jpg")},
                    content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(Rig, rid).photo == first             # not an image: old photo stays
    client.post(f"/rigs/rig/{rid}/photo", data={"photo": (png(300, 200), "neu.png")}, content_type="multipart/form-data")
    with app.app_context():
        second = db.session.get(Rig, rid).photo
    assert second != first and not f.exists()                      # replaced, old file removed
    client.post(f"/rigs/rig/{rid}/photo/delete")
    assert client.get(f"/rigs/rig/{rid}/photo").status_code == 404
    assert not (Path(app.config["DATA_PATH"]) / second).exists()


def test_catalog_names_rig(app, client):  # noqa: F811
    """The catalog page names the rig of each data set and session, not only the camera."""
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
        from webapp.models import Target
        tid = db.session.query(Target).filter_by(name="M 65").one().id
    page = client.get(f"/target/{tid}", headers={"HX-Request": "true"}).data.decode()
    assert "kein Rig" in page and "<th>Rig</th>" in page
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    page = client.get(f"/target/{tid}", headers={"HX-Request": "true"}).data.decode()
    assert page.count(">Newton</span>") >= 2                       # data set card and sessions table
    assert "Canon EOS 1200D" in page                               # camera stays visible under the rig


def test_rig_mount_and_guiding(app, client):  # noqa: F811
    """A rig has a mount and optionally a guide camera, shown on the rigs and session pages; both stay
    protected against deletion while a rig uses them."""
    from webapp.models import Mount
    _setup(client)
    client.post("/rigs/camera", data=dict(name="ASI120MM Mini", header_name="ZWO ASI120MM Mini", pixel_um="3,75"))
    client.post("/rigs/mount", data=dict(name="EQ6-R Pro", kind="eq", payload_kg="20"))
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
        mid = db.session.query(Mount).one().id
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"],
                                       mount_id=mid, guide_camera_id=ids["ASI120MM Mini"], active="1"))
    with app.app_context():
        r = db.session.query(Rig).filter_by(name="Newton").one()
        assert r.mount.name == "EQ6-R Pro" and r.guide_camera.name == "ASI120MM Mini"
        sid = db.session.query(Session).filter(Session.root.like("%2026-03-22")).one().id
    page = client.get("/rigs").data.decode()
    assert "Montierungen" in page and "20,0 kg" in page and "<dd>EQ6-R Pro</dd>" in page and "<dd>ASI120MM Mini</dd>" in page
    session = client.get(f"/session/{sid}").data.decode()
    assert "<dd>EQ6-R Pro</dd>" in session and "<dd>ASI120MM Mini</dd>" in session
    client.post(f"/rigs/mount/{mid}/delete")
    client.post(f"/rigs/camera/{ids['ASI120MM Mini']}/delete")
    with app.app_context():
        assert db.session.query(Mount).count() == 1                 # in use: not deleted
        assert db.session.get(Camera, ids["ASI120MM Mini"]) is not None


def test_guide_camera_from_headers(app, client):  # noqa: F811
    """GUIDECAM in the lights of a rig is offered as its guide camera; adopting creates the camera once."""
    from pathlib import Path

    from tests.test_app import _fits
    from webapp.indexer import run_once
    leo = Path(app.config["ARCHIVE_PATH"]) / "M65, M66, NGC 3628 (Leo-Triplett)" / "2026-03-22" / "Lights"
    _fits(leo / "g.fit", IMAGETYP="Light", EXPTIME=180.0, **{"DATE-OBS": "2026-03-23T03:10:00"},
          INSTRUME="Canon EOS 1200D", FOCALLEN=755.0, NAXIS1=5202, NAXIS2=3465, GUIDECAM="ZWO ASI120MM Mini")
    with app.app_context():
        run_once(app)
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    page = client.get("/rigs").data.decode()
    assert "Laut Header geführt mit <b>ZWO ASI120MM Mini</b>" in page and "übernehmen" in page
    with app.app_context():
        rid = db.session.query(Rig).filter_by(name="Newton").one().id
    client.post(f"/rigs/rig/{rid}/guide-camera", data={"name": "ZWO ASI120MM Mini"})
    with app.app_context():
        r = db.session.get(Rig, rid)
        assert r.guide_camera.header_name == "ZWO ASI120MM Mini"
        n = db.session.query(Camera).count()
    assert "Laut Header geführt" not in client.get("/rigs").data.decode()     # done: no suggestion left
    client.post(f"/rigs/rig/{rid}/guide-camera", data={"name": "zwo asi120mm mini"})
    with app.app_context():
        assert db.session.query(Camera).count() == n                         # existing camera reused


def test_guide_camera_list(app, client):  # noqa: F811
    """Guide cameras have their own list; GUIDECAM names from the headers can be taken over at once."""
    from pathlib import Path

    from tests.test_app import _fits
    from webapp.indexer import run_once
    leo = Path(app.config["ARCHIVE_PATH"]) / "M65, M66, NGC 3628 (Leo-Triplett)" / "2026-03-22" / "Lights"
    _fits(leo / "g.fit", IMAGETYP="Light", EXPTIME=180.0, **{"DATE-OBS": "2026-03-23T03:10:00"},
          INSTRUME="Canon EOS 1200D", FOCALLEN=755.0, NAXIS1=5202, NAXIS2=3465, GUIDECAM="ZWO ASI120MM Mini")
    with app.app_context():
        run_once(app)
    page = client.get("/rigs").data.decode()
    sec = page.split('id="guide-cameras"', 1)[1].split("</section>", 1)[0]
    assert "noch nicht angelegt: ZWO ASI120MM Mini" in sec
    client.post("/rigs/guide-cameras-from-headers")
    with app.app_context():
        cam = db.session.query(Camera).filter_by(name="ZWO ASI120MM Mini").one()
        assert cam.role == "guide"
    page = client.get("/rigs").data.decode()
    guides = page.split('id="guide-cameras"', 1)[1].split("</section>", 1)[0]
    cams = page.split('id="cameras"', 1)[1].split("</section>", 1)[0]
    assert "<td>ZWO ASI120MM Mini</td>" in guides and "<td>ZWO ASI120MM Mini</td>" not in cams
    form = page.split('id="rig-form"', 1)[1].split("</form>", 1)[0]
    guide_select = form.split('name="guide_camera_id"', 1)[1].split("</select>", 1)[0]
    main_select = form.split('name="camera_id"', 1)[1].split("</select>", 1)[0]
    assert "ZWO ASI120MM Mini" in guide_select and "ZWO ASI120MM Mini" not in main_select


def test_catalog_rig_thumbnail(app, client):  # noqa: F811
    """The data set card in the catalog shows the small photo of its rig."""
    import io

    from PIL import Image
    from webapp.models import Target
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
        tid = db.session.query(Target).filter_by(name="M 65").one().id
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    with app.app_context():
        rid = db.session.query(Rig).filter_by(name="Newton").one().id
    page = client.get(f"/target/{tid}", headers={"HX-Request": "true"}).data.decode()
    assert "pl-rig-ph" in page and f"/rigs/rig/{rid}/photo" not in page    # no photo: generic image
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (10, 120, 200)).save(b, "PNG")
    b.seek(0)
    client.post(f"/rigs/rig/{rid}/photo", data={"photo": (b, "n.png")}, content_type="multipart/form-data")
    page = client.get(f"/target/{tid}", headers={"HX-Request": "true"}).data.decode()
    assert "pl-dataset-thumb" in page and f"/rigs/rig/{rid}/photo" in page


def test_mount_driver_as_default(app, client):  # noqa: F811
    """Without a mount in the rig, the mount driver from the headers (TELESCOP of ASIAIR) is shown."""
    from webapp.models import Mount
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
        sid = db.session.query(Session).filter(Session.root.like("%2026-03-22")).one().id
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    page = client.get(f"/session/{sid}").data.decode()
    assert "Treiber laut FITS-Header" in page and ">EQMod Mount</dd>" in page
    assert ">EQMod Mount</dd>" in client.get("/rigs").data.decode()
    client.post("/rigs/mount", data=dict(name="EQ6-R Pro", kind="eq"))
    with app.app_context():
        rid = db.session.query(Rig).filter_by(name="Newton").one().id
        mid = db.session.query(Mount).one().id
    client.post("/rigs/rig", data=dict(id=rid, name="Newton", camera_id=ids["Canon 1200D"],
                                       optic_id=ids["Skywatcher 150P"], mount_id=mid, active="1"))
    page = client.get(f"/session/{sid}").data.decode()
    assert "<dd>EQ6-R Pro</dd>" in page and "EQMod Mount</dd>" not in page


def test_smart_telescope_rig(app, client):  # noqa: F811
    """A smart telescope seen in the headers is offered as a rig; one click creates camera, optic and the
    integrated mount, and its sessions are assigned automatically."""
    from pathlib import Path

    from tests.test_app import _fits
    from webapp.indexer import run_once
    from webapp.models import Issue, Mount
    dwarf = Path(app.config["ARCHIVE_PATH"]) / "M66 (Leo)" / "2026-04-02 DWARF3" / "Lights"
    _fits(dwarf / "d1.fit", IMAGETYP="Light", EXPTIME=60.0, **{"DATE-OBS": "2026-04-02T22:00:00"}, INSTRUME="DWARF 3",
          TELESCOP="DWARF 3", FOCALLEN=150.0, XPIXSZ=2.0, NAXIS1=3856, NAXIS2=2180, RA=170.06, DEC=12.99)
    with app.app_context():
        run_once(app)
    _setup(client)                                              # some rig exists, so rig warnings are on
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    page = client.get("/rigs").data.decode()
    assert "DWARF 3 als Smart-Teleskop anlegen" in page
    client.post("/rigs/smart", data={"preset": "dwarf3"})
    with app.app_context():
        r = db.session.query(Rig).filter_by(name="DWARF 3").one()
        assert r.mount.kind == "smart" and r.optic.focal_mm == 150 and r.optic.aperture_mm == 35
        assert r.camera.header_name == "DWARF 3" and r.camera.pixel_um == 2.0
        root = "M66 (Leo)/2026-04-02 DWARF3"
        assert not db.session.query(Issue).filter_by(code="no_rig", session_root=root).count()
        sid = db.session.query(Session).filter_by(root=root).one().id
        n_mounts = db.session.query(Mount).count()
    assert "DWARF 3 als Smart-Teleskop anlegen" not in client.get("/rigs").data.decode()
    session = client.get(f"/session/{sid}").data.decode()
    assert "Rig · DWARF 3" in session and "Smart-Teleskop" in session and "f/4,3" in session
    client.post("/rigs/smart", data={"preset": "dwarf3"})                # twice: nothing duplicated
    with app.app_context():
        assert db.session.query(Rig).filter_by(name="DWARF 3").count() == 1 and db.session.query(Mount).count() == n_mounts


def test_optional_extender(app, client):  # noqa: F811
    """An optional extender lets one rig cover both focal lengths; two rigs that differ only in the
    extender are merged into one."""
    from pathlib import Path

    from tests.test_app import _light
    from webapp.indexer import run_once
    from webapp.models import Corrector
    arc = Path(app.config["ARCHIVE_PATH"])
    _light(arc / "M51 (Whirlpool Galaxy)" / "2026-04-03" / "Lights" / "b.fit", "2026-04-03T22:00:00", focal=1510.0,
           ra=202.47, dec=47.2)
    with app.app_context():
        run_once(app)
    _setup(client)
    client.post("/rigs/corrector", data=dict(name="Barlow 2x", kind="extender", factor="2"))
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
        barlow = db.session.query(Corrector).filter_by(name="Barlow 2x").one().id
    client.post("/rigs/rig", data=dict(name="Newton", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    client.post("/rigs/rig", data=dict(name="Newton Barlow", camera_id=ids["Canon 1200D"],
                                       optic_id=ids["Skywatcher 150P"], corrector_id=barlow, active="1"))
    page = client.get("/rigs").data.decode()
    assert "„Newton“ in „Newton Barlow“ zusammenfassen" in page
    with app.app_context():
        keep = db.session.query(Rig).filter_by(name="Newton Barlow").one().id
        drop = db.session.query(Rig).filter_by(name="Newton").one().id
    client.post(f"/rigs/merge/{keep}/{drop}")
    with app.app_context():
        assert [r.name for r in db.session.query(Rig)] == ["Newton Barlow"]
        r = db.session.get(Rig, keep)
        assert r.corrector_optional and r.factors == [1.0, 2.0]
        from webapp.main.rigs import Resolver
        res = Resolver(0.03)
        assert res.resolve("x", "Canon EOS 1200D", 755)[0].id == keep          # without the Barlow
        assert res.resolve("x", "Canon EOS 1200D", 1510)[0].id == keep         # with it
        s_with = db.session.query(Session).filter_by(root="M51 (Whirlpool Galaxy)/2026-04-03").one().id
        s_without = db.session.query(Session).filter(Session.root.like("%2026-03-22")).one().id
    page = client.get("/rigs").data.decode()
    assert "750 / 1500 mm" in page and "optional</span>" in page and "zusammenfassen" not in page
    assert "mit Barlow 2x" in client.get(f"/session/{s_with}").data.decode()
    assert "ohne Barlow 2x" in client.get(f"/session/{s_without}").data.decode()


def test_rigs_grouped_by_optic(app, client):  # noqa: F811
    """The rigs page groups rigs by optic (one telescope, several cameras) with the totals of the group."""
    _setup(client)
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
    client.post("/rigs/rig", data=dict(name="Newton 1200D", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"], active="1"))
    client.post("/rigs/rig", data=dict(name="Newton 7D", camera_id=ids["Canon 7D Mark II"], optic_id=ids["Skywatcher 150P"], active="1"))
    client.post("/rigs/rig", data=dict(name="Zoom 7D", camera_id=ids["Canon 7D Mark II"],
                                       optic_id=ids["Canon EF 70-300mm L IS USM"], active="1"))
    page = client.get("/rigs").data.decode()
    groups = page.split('class="pl-rig-group"')[1:]
    assert len(groups) == 2
    newton = next(g for g in groups if "Skywatcher 150P</span>" in g)
    assert "Newton 1200D" in newton and "Newton 7D" in newton and "Zoom 7D" not in newton
    assert "2 Rigs" in newton and "Lights" in newton


def test_duplicate_rig(app, client):  # noqa: F811
    """Duplicating opens the new-rig form filled with the components of the rig; saving creates a new rig."""
    from webapp.models import Mount
    _setup(client)
    client.post("/rigs/mount", data=dict(name="EQ6-R Pro", kind="eq"))
    with app.app_context():
        ids = {c.name: c.id for c in db.session.query(Camera)} | {o.name: o.id for o in db.session.query(Optic)}
        mid = db.session.query(Mount).one().id
    client.post("/rigs/rig", data=dict(name="Newton 1200D", camera_id=ids["Canon 1200D"], optic_id=ids["Skywatcher 150P"],
                                       corrector_id="1", mount_id=mid, active="1"))
    with app.app_context():
        rid = db.session.query(Rig).filter_by(name="Newton 1200D").one().id
    page = client.get(f"/rigs?copy={rid}").data.decode()
    form = page.split('id="rig-form"', 1)[1].split("</form>", 1)[0]
    assert "Kopie von „Newton 1200D“" in form and 'value="Newton 1200D (Kopie)"' in form
    assert 'name="id"' not in form and "Rig anlegen" in form
    assert f'<option value="{mid}" selected>' in form and f'<option value="{ids["Skywatcher 150P"]}" selected>' in form
    client.post("/rigs/rig", data=dict(name="Newton 7D", camera_id=ids["Canon 7D Mark II"], optic_id=ids["Skywatcher 150P"],
                                       corrector_id="1", mount_id=mid, active="1"))
    with app.app_context():
        assert {r.name for r in db.session.query(Rig)} == {"Newton 1200D", "Newton 7D"}
