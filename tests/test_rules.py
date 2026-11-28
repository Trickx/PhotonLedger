"""Folder rules, tested against real folder names from the archive."""
from datetime import date, datetime

import pytest

from webapp.archive import rules
from webapp.archive.headers import normalize


@pytest.mark.parametrize("path, kind, target, root, ftype, rejected", [
    # year / target / date / Lights
    ("M97, M108 (Surfboard, Owl)/2026-04-28/Lights/Light_0061.fit",
     "frame", "M97, M108 (Surfboard, Owl)", "M97, M108 (Surfboard, Owl)/2026-04-28", "light", False),
    ("M97, M108 (Surfboard, Owl)/2026-04-28/Lights/REJECTED/x.fit",
     "frame", "M97, M108 (Surfboard, Owl)", "M97, M108 (Surfboard, Owl)/2026-04-28", "light", True),
    ("M97, M108 (Surfboard, Owl)/2026-04-28/Flats/Rejected/x.fit",
     "frame", "M97, M108 (Surfboard, Owl)", "M97, M108 (Surfboard, Owl)/2026-04-28", "flat", True),
    ("M97, M108 (Surfboard, Owl)/2026-04-28/Flats/Manual/x.fit",
     "frame", "M97, M108 (Surfboard, Owl)", "M97, M108 (Surfboard, Owl)/2026-04-28", "flat", False),
    # no session folder
    ("M51 (Whirlpool Galaxy)/Lights/x.fit",
     "frame", "M51 (Whirlpool Galaxy)", "M51 (Whirlpool Galaxy)", "light", False),
    ("M51 (Whirlpool Galaxy)/Biases/x.fit",
     "frame", "M51 (Whirlpool Galaxy)", "M51 (Whirlpool Galaxy)", "bias", False),
    # session folder with equipment suffix / brackets
    ("IC1396 (Elephant's Trunk Nebula)/2026-07-08 DWARF3/Lights/a.fits",
     "frame", "IC1396 (Elephant's Trunk Nebula)", "IC1396 (Elephant's Trunk Nebula)/2026-07-08 DWARF3", "light", False),
    ("NGC7000 (NA Nebula)/NGC7000 (NA Nebula)[Canon]/Darks/d.fit",
     "frame", "NGC7000 (NA Nebula)", "NGC7000 (NA Nebula)/NGC7000 (NA Nebula)[Canon]", "dark", False),
    # lower case and singular frame folders
    ("M42 (Orion Nebula)/2026-02-27/lights/l.fits",
     "frame", "M42 (Orion Nebula)", "M42 (Orion Nebula)/2026-02-27", "light", False),
    ("NGC 7380 (Wizard Nebula) SH2-142/2026-10-04/FLAT/f.fits",
     "frame", "NGC 7380 (Wizard Nebula) SH2-142", "NGC 7380 (Wizard Nebula) SH2-142/2026-10-04", "flat", False),
    # loose sub frames: type comes from the header
    ("Mineral Moon/2026-05-25-100/m.fit",
     "frame", "Mineral Moon", "Mineral Moon/2026-05-25-100", None, False),
    ("Moon (Standard)/m.fit", "frame", "Moon (Standard)", "Moon (Standard)", None, False),
    ("Perseiden/DSCF1.RAF", "frame", "Perseiden", "Perseiden", None, False),
    # versions
    ("M51 (Whirlpool Galaxy)/Finals/M51.jpg", "version", "M51 (Whirlpool Galaxy)", None, None, False),
    ("NGC 7635 (Bubble Nebula)/Processing/master/DXO/b.tif", "version", "NGC 7635 (Bubble Nebula)", None, None, False),
    ("IC1396 (Elephant's Trunk Nebula)/PI-Dwarf/master/x.jpg", "version", "IC1396 (Elephant's Trunk Nebula)", None, None, False),
    ("Blutmond 2025-09-07/b.jpg", "version", "Blutmond 2025-09-07", None, None, False),
    ("Solar Eclipse/2026-08-12/Lights/s.jpg", "frame", "Solar Eclipse", "Solar Eclipse/2026-08-12", "light", False),
    # annotation overlays in Finals (also one level below)
    ("M51 (Whirlpool Galaxy)/Finals/m51.svg", "intermediate", "M51 (Whirlpool Galaxy)", None, None, False),
    ("M51 (Whirlpool Galaxy)/2026-04-14/Finals/Overlays/a.svg", "intermediate", "M51 (Whirlpool Galaxy)", None, None, False),
])
def test_classify(path, kind, target, root, ftype, rejected):
    c = rules.classify(path)
    assert c is not None
    assert (c.kind, c.target_folder, c.session_root, c.frame_type, c.rejected) == (kind, target, root, ftype, rejected)


@pytest.mark.parametrize("path, ok", [
    ("M51 (Whirlpool Galaxy)/Processing/stack.fit", True),
    ("NGC 6888 (Crescent Nebula)/2026-08-06/Processing/master/x.fits", True),
    ("M 31 (Andromeda Galaxy)/PI/master/m.xisf", False),
    ("IC1396 (Elephant's Trunk Nebula)/PI-Dwarf/master/m.xisf", False),
    ("M42 (Orion Nebula)/2026-02-27/PixInsight/s.fits", False),
    ("M42 (Orion Nebula)/processing/s.fit", False),              # wrong case
])
def test_intermediates(path, ok):
    c = rules.classify(path)
    assert c.kind == "intermediate"
    assert rules.in_processing(path) is ok


@pytest.mark.parametrize("name, kind", [
    ("Processing/master/masterLight_BIN-1_5202x3465_EXPOSURE-300.00s_FILTER-IDAS NB1_RGB.xisf", "ok"),
    ("Processing/master/masterLight_BIN-1_5202x3465_EXPOSURE-300.00s_FILTER-IDAS NB1_RGB_autocrop.xisf", "ok"),
    ("PI-Dwarf/master/masterLight_BIN-1_3856x2180_EXPOSURE-60.00s_FILTER-Duo-Band_RGB_drizzle_1x_autocrop.xisf", "ok"),
    ("PI/master/masterLight_BIN-1_5202x3465_EXPOSURE-120.00s_FILTER-NoFilter_RGB_fastIntegration.xisf", "ok"),
    ("Processing/master/masterDark_BIN-1_5202x3465_EXPOSURE-300.00s_CAL-TEMP-20.xisf", "calibration_master"),
    ("Processing/master/masterFlat_BIN-1_5202x3465_FILTER-IDAS LPS-P2_CFA.xisf", "calibration_master"),
    ("Processing/master/masterBias_BIN-1_5202x3465.xisf", "calibration_master"),
    ("Processing/master/masterLight_BIN-1_x_EXPOSURE-300.00s_FILTER-NB1_RGB_autocrop_AW10_starsu.fits", "other"),
    ("PI/master/MasterLight_IDAS_LPS_P21_120s_ADBE.xisf", "other"),
    ("Processing/registered/Light_0001_c_cc_d_r.xisf", "step"),
    ("Processing/calibrated/Light_0001_c.xisf", "step"),
    ("Processing/local_normalization/Light_0001_c_cc_d_r.xnml", "step"),
    ("PI-Dwarf/master/LN_Reference_Light_BIN-1_3856x2180_EXPOSURE-60.00s_FILTER-Duo-Band_RGB.xisf", "step"),
    ("Processing/stack.fit", None),
])
def test_wbpp_kind(name, kind):
    path = "M 31 (Andromeda Galaxy)/" + name
    assert rules.wbpp_kind(path) == kind
    assert rules.classify(path).kind == "intermediate"


@pytest.mark.parametrize("path", [
    "IC 1805 (Heart & Soul Nebula)/Lights/SAD/p.png",                 # analysis output
    "IC 1805 (Heart & Soul Nebula)/Processing/master/H.pxiproject/project.data/x",
    "M45 (Pleiades)/PI/logs/l.jpg",
    "M97, M108 (Surfboard, Owl)/2026-04-28/Processing/Light_Pane 1_180.0s_Bin1_ISO1600_0043.tif",
    "x.fit",                                                              # directly in the archive root
    "2026/x.fit",                                                         # directly in a (legacy) year folder
    "M51 (Whirlpool Galaxy)/Lights/notes.txt",
    "M51 (Whirlpool Galaxy)/.DS_Store",
    "M51 (Whirlpool Galaxy)/Processing/diagram.svg",                     # SVG outside Finals
])
def test_ignored(path):
    assert rules.classify(path) is None


def test_ignore_dirs_option():
    p = "NGC 7380 (Wizard Nebula) SH2-142/2026-09-10/Kollimation/k.fit"
    assert rules.classify(p) is not None
    assert rules.classify(p, ignore_dirs=["kollimation"]) is None


@pytest.mark.parametrize("folder, ids, common", [
    ("M97, M108 (Surfboard, Owl)", ("M 97", "M 108"), "Surfboard, Owl"),
    ("M 31 (Andromeda Galaxy)", ("M 31",), "Andromeda Galaxy"),
    ("NGC7000 (NA Nebula)", ("NGC 7000",), "NA Nebula"),
    ("NGC 7380 (Wizard Nebula) SH2-142", ("NGC 7380", "Sh2-142"), "Wizard Nebula"),
    ("M81, M82, NGC3077 (M81 Group)", ("M 81", "M 82", "NGC 3077"), "M81 Group"),
    ("M66, M65, NGC (Leo-Triplett)", ("M 66", "M 65"), "Leo-Triplett"),
    ("IC1396 (Elephant's Trunk Nebula)", ("IC 1396",), "Elephant's Trunk Nebula"),
    ("Mineral Moon", (), None),
    ("Blutmond 2025-09-07", (), None),
])
def test_parse_target(folder, ids, common):
    t = rules.parse_target_folder(folder)
    assert t.catalog_ids == ids
    assert t.common_name == common


def test_group_folders_merges_same_object():
    folders = {
        "M66, M65, NGC (Leo-Triplett)": "M66, M65, NGC (Leo-Triplett)",
        "M65, M66, NGC 3628 (Leo-Triplett)": "M65, M66, NGC 3628 (Leo-Triplett)",
        "M42": "M42",
        "M42 (Orion Nebula)": "M42 (Orion Nebula)",
        "Moon/": "Moon",
        "Moon (Standard)": "Moon (Standard)",
        "Mineral Moon": "Mineral Moon",
        "M51 (Whirlpool Galaxy)": "M51 (Whirlpool Galaxy)",
    }
    groups = sorted(sorted(g) for g in rules.group_folders(folders))
    assert ["M65, M66, NGC 3628 (Leo-Triplett)", "M66, M65, NGC (Leo-Triplett)"] in groups
    assert ["M42", "M42 (Orion Nebula)"] in groups
    assert ["Moon (Standard)", "Moon/"] in groups
    assert ["Mineral Moon"] in groups
    assert len(groups) == 5


def test_focal_clusters_with_3_percent():
    vals = [751.857, 752.694, 753.645, 754.0, 754.36, 755.0, 756.0, 792.0,
            148.044, 148.052, 150.0, 198.76, 199.0, 292.0, 293.0]
    m = rules.cluster_focal_lengths(vals, 0.03)
    assert len({m[v] for v in vals if 740 < v < 760}) == 1
    assert m[792.0] != m[755.0]
    assert m[148.044] == m[150.0]
    assert m[292.0] == m[293.0]
    assert m[198.76] == m[199.0]


def test_nights_and_dates():
    assert rules.night_of(datetime(2026, 4, 29, 1, 37)) == date(2026, 4, 28)
    assert rules.night_of(datetime(2026, 4, 28, 21, 0)) == date(2026, 4, 28)
    assert rules.folder_date("2026-07-08 DWARF3") == date(2026, 7, 8)
    assert rules.folder_date("2026-05-25-300") == date(2026, 5, 25)
    assert rules.folder_date("NGC7000 (NA Nebula)[Canon]") is None


def test_frame_type_and_raw_check():
    assert rules.frame_type_from_header("Light") == "light"
    assert rules.frame_type_from_header("FLAT") == "flat"
    assert rules.frame_type_from_header("Master Dark") is None
    stacked = normalize({"NAXIS3": "3", "EXPTIME": "120.", "DATE-OBS": "2026-03-22T22:00:34", "IMAGETYP": "Light"})
    assert not rules.is_raw_frame(stacked)
    raw = normalize({"EXPTIME": "120.", "DATE-OBS": "2026-03-22T22:00:34", "IMAGETYP": "Light"})
    assert rules.is_raw_frame(raw)


def test_normalize_header_variants():
    asiair = normalize({"IMAGETYP": "Light", "EXPOSURE": "180.", "DATE-OBS": "2026-04-07T22:27:32.190",
                        "INSTRUME": "Canon EOS 1200D", "TELESCOP": "EQMod Mount", "FOCALLEN": "755",
                        "RA": "151.2993", "DEC": "69.0012"})
    assert asiair["exptime"] == 180 and asiair["focal"] == 755 and asiair["ra"] == pytest.approx(151.2993)
    assert rules.clean_telescope(asiair["telescope"], asiair["camera"]) is None
    nina = normalize({"OBJCTRA": "06 22 02", "OBJCTDEC": "+51 02 07", "SWCREATE": "N.I.N.A. 3.2"})
    assert nina["ra"] == pytest.approx(95.508, abs=1e-3)
    assert nina["dec"] == pytest.approx(51.035, abs=1e-3)
    assert rules.clean_telescope("DWARF 3", "DWARF 3") is None
    assert rules.clean_telescope("XF50-140 + XF1,4TC", "Fujifilm X-T4") == "XF50-140 + XF1,4TC"
    assert rules.clean_filter("NoFilter") == "Ohne Filter"


@pytest.mark.parametrize("name, findings", [
    ("Processing/stack.fit", ["ok"]),
    ("Processing/master/masterLight_BIN-1_x_EXPOSURE-60.00s_FILTER-L_Mono.xisf", ["wbpp_ok"]),
    ("PI/master/masterLight_BIN-1_x_EXPOSURE-60.00s_FILTER-L_Mono.xisf", ["not_in_processing"]),
    ("PI/master/masterDark_BIN-1_x_EXPOSURE-60.00s.xisf", ["wbpp_calibration_master", "not_in_processing"]),
    ("Processing/registered/l_c_r.xisf", ["wbpp_step"]),
    ("PI/master/bge.xisf", ["not_in_processing", "wbpp_other"]),
])
def test_intermediate_findings(name, findings):
    assert rules.intermediate_findings("M 31 (Andromeda Galaxy)/" + name) == findings


def test_legacy_year_folder_still_understood():
    """Old layout <year>/<target>/…: the year folder is not a target of its own."""
    c = rules.classify("2026/M51 (Whirlpool Galaxy)/2026-04-14/Lights/x.fit")
    assert (c.target_folder, c.session_root) == ("2026/M51 (Whirlpool Galaxy)", "2026/M51 (Whirlpool Galaxy)/2026-04-14")
    c = rules.classify("M51 (Whirlpool Galaxy)/2026-04-14/Lights/x.fit")
    assert (c.target_folder, c.session_root) == ("M51 (Whirlpool Galaxy)", "M51 (Whirlpool Galaxy)/2026-04-14")
