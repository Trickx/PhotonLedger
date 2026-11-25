"""The examples of docs/ordnerstruktur.md must match the rules (keeps documentation and code in sync)."""
import pytest

from webapp.archive import rules

T = "2026/M97, M108 (Owl, Surfboard)"
S = f"{T}/2026-04-28"


@pytest.mark.parametrize("path, expected", [
    (f"{S}/Lights/Light_M97_180.0s_Bin1_ISO1600_20260428-221502_0001.fit", ("frame", S, "light", False)),
    (f"{S}/Lights/REJECTED/x.fit", ("frame", S, "light", True)),
    (f"{S}/Flats/f.fit", ("frame", S, "flat", False)),
    (f"{S}/Darks/d.fit", ("frame", S, "dark", False)),
    (f"{S}/Biases/b.fit", ("frame", S, "bias", False)),
    (f"{S}/DarkFlats/d.fit", ("frame", S, "darkflat", False)),
    (f"{T}/2026-07-08 DWARF3/Lights/l.fits", ("frame", f"{T}/2026-07-08 DWARF3", "light", False)),
    (f"{T}/Processing/master/masterLight_BIN-1_5496x3670_EXPOSURE-180.00s_FILTER-NoFilter_RGB.xisf", ("intermediate",)),
    (f"{T}/Processing/01_GraXpert.xisf", ("intermediate",)),
    (f"{S}/Processing/stack.fit", ("intermediate",)),
    (f"{T}/Finals/M97_M108_final.jpg", ("version",)),
    (f"{T}/Finals/stack.xisf", ("intermediate",)),                 # FITS/XISF in Finals: intermediate
    (f"{S}/Lights/SAD/x_Size.png", None),                          # read by the SAD importer, not as file
    (f"{S}/Logs/PHD2_GuideLog_2026-04-28_222144.txt", None),       # read by the log importers
    ("2026/Mineral Moon/2026-05-25/Lights/m.fit", ("frame", "2026/Mineral Moon/2026-05-25", "light", False)),
])
def test_documented_paths(path, expected):
    c = rules.classify(path)
    got = None if c is None else (
        (c.kind, c.session_root, c.frame_type, c.rejected) if c.kind == "frame" else (c.kind,))
    assert got == expected


@pytest.mark.parametrize("name, findings", [
    ("Processing/master/masterLight_BIN-1_5496x3670_EXPOSURE-180.00s_FILTER-NoFilter_RGB_autocrop.xisf", ["wbpp_ok"]),
    ("Processing/master/masterLight_BIN-1_x_EXPOSURE-180.00s_FILTER-NoFilter_RGB_drizzle_2x_autocrop.xisf", ["wbpp_ok"]),
    ("Processing/master/masterDark_BIN-1_x_EXPOSURE-180.00s.xisf", ["processing_master"]),
    ("Processing/master/LN_Reference_Light_BIN-1_x.xisf", ["processing_master"]),
    ("Processing/master/masterLight_BIN-1_x_EXPOSURE-180.00s_FILTER-NoFilter_RGB_autocrop_ABE.xisf", ["processing_master"]),
    ("Processing/master/IC405_Final_V1.xisf", ["final"]),
    ("2026-04-28/Processing/master/masterDark_x.xisf", ["processing_master"]),
    ("PI/master/masterDark_x.xisf", ["wbpp_calibration_master", "not_in_processing"]),
    ("Processing/registered/l_c_d_r.xisf", ["wbpp_step"]),
    ("Processing/01_GraXpert.xisf", ["ok"]),
    ("PI/master/x.xisf", ["not_in_processing", "wbpp_other"]),
])
def test_documented_processing_rules(name, findings):
    assert rules.intermediate_findings(f"{T}/{name}") == findings


@pytest.mark.parametrize("path, findings", [
    (f"{S}/Finals/M97_final.xisf", ["session_finals"]),           # Finals directly in a session folder
    (f"{S}/Finals/v2/M97.xisf", ["session_finals"]),
    (f"{T}/Finals/stack.xisf", ["not_in_processing"]),            # Finals of the target: images only
    (f"{S}/finals/M97.xisf", ["not_in_processing"]),              # exactly "Finals"
])
def test_documented_session_finals(path, findings):
    assert rules.intermediate_findings(path, {S}) == findings


def test_documented_names():
    assert rules.parse_target_folder("M97, M108 (Owl, Surfboard)").catalog_ids == ("M 97", "M 108")
    assert rules.parse_target_folder("NGC 7380 (Wizard Nebula) SH2-142").catalog_ids == ("NGC 7380", "Sh2-142")
    assert str(rules.folder_date("2026-07-08 DWARF3")) == "2026-07-08"
    assert str(rules.folder_date("2026-05-25-100")) == "2026-05-25"
