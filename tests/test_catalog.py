import pytest

from webapp.archive.catalog import default_catalog, display_id, norm_key, separation


@pytest.fixture(scope="module")
def cat():
    return default_catalog()


@pytest.mark.parametrize("raw, key, shown", [
    ("NGC0224", "NGC224", "NGC 224"), ("M 31", "M31", "M 31"), ("IC 1396", "IC1396", "IC 1396"),
    ("SH 2-155", "SH2155", "Sh2-155"), ("Sh2-142", "SH2142", "Sh2-142"), ("PGC 034695", "PGC34695", "PGC 34695"),
    ("NGC1530A", "NGC1530A", "NGC 1530A"), ("Mel022", "MEL22", "Mel 22"),
])
def test_ids(raw, key, shown):
    assert norm_key(raw) == key
    assert display_id(raw) == shown


@pytest.mark.parametrize("query, cid, messier, typ", [
    ("M 66", "NGC 3627", "M 66", "Galaxie"),
    ("M 45", "Mel 22", "M 45", "Offener Sternhaufen"),
    ("M 102", "NGC 5457", "M 101", "Galaxie"),            # duplicate entry is followed
    ("LBN 373", "NGC 7000", None, "Emissionsnebel"),      # found via cross identifier
    ("M 97", "NGC 3587", "M 97", "Planetarischer Nebel"),
    ("NGC 6992", "NGC 6992", None, "Supernovaüberrest"),
])
def test_lookup(cat, query, cid, messier, typ):
    e = cat.lookup(query)
    assert (e.id, e.messier, e.type_de) == (cid, messier, typ)


def test_m66_cross_ids_and_field(cat):
    e = cat.lookup("M 66")
    assert e.all_ids == ["M 66", "NGC 3627", "PGC 34695", "UGC 6346"]
    near = [x.id for _, x in cat.within(e.ra, e.dec, 0.6)]
    assert near[0] == "NGC 3627" and "NGC 3623" in near


def test_separation():
    assert separation(10, 20, 10, 21) == pytest.approx(1.0)
    assert separation(0, 0, 180, 0) == pytest.approx(180.0)
