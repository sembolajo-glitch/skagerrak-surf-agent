"""Enhetstester for debug_fetch_rays.py - ingen nettverk, syntetisk geometri."""

import pytest

pytest.importorskip("shapely")

import debug_fetch_rays as R
import geo_utils as G
from shapely.geometry import LineString


def test_classify_rays_skiller_kant_fra_kyst():
    bbox = (58.0, 9.0, 59.0, 10.0)
    tree, lines = G.bbox_edge_tree(bbox)
    lon0, lat0 = 9.5, 58.5

    edge_km_ost = G.cast_ray_km(lon0, lat0, 90, 1000.0, tree, lines)
    fetch = [0.0] * R.N_RAYS
    fetch[0] = 2.0                        # N: reelt kysttreff, godt innenfor kanten
    fetch[18] = round(edge_km_ost, 1)      # O (bearing=90, index 90/5=18): naar helt ut til kanten -> "kant"

    rows = R.classify_rays(lon0, lat0, fetch, tree, lines)
    assert rows[0][3] == "kyst"
    assert rows[18][3] == "kant"


def test_classify_rays_300km_tak_er_alltid_kant_i_liten_bbox():
    """Kjernen i brukerens funn: naar taket (300 km) er langt storre enn
    bbox-diagonalen, betyr en 300 km-verdi at straalen gikk tom av data,
    ikke at det er 300 km reelt aapent vann."""
    bbox = (58.0, 9.0, 59.0, 10.0)  # diagonal godt under 300 km
    tree, lines = G.bbox_edge_tree(bbox)
    lon0, lat0 = 9.5, 58.5
    fetch = [300.0] * R.N_RAYS
    rows = R.classify_rays(lon0, lat0, fetch, tree, lines)
    assert all(r[3] == "kant" for r in rows)


def test_ray_endpoint_nord():
    lon0, lat0 = 10.0, 59.0
    elon, elat = R.ray_endpoint(lon0, lat0, 0, 10.0)  # 10 km nord
    assert elat > lat0
    assert abs(elon - lon0) < 0.01  # naer rett nord, liten avvik fra UTM-projeksjon ok


def test_ray_endpoint_null_avstand_er_origo():
    lon0, lat0 = 10.0, 59.0
    elon, elat = R.ray_endpoint(lon0, lat0, 45, 0.0)
    assert abs(elon - lon0) < 1e-6
    assert abs(elat - lat0) < 1e-6


def test_build_coast_path_inneholder_moveto_per_linje():
    def proj(lon, lat):
        return lon * 100, lat * 100
    lines = [LineString([(9.0, 58.0), (9.1, 58.1)]), LineString([(9.5, 58.5), (9.6, 58.6)])]
    path = R.build_coast_path(lines, proj)
    assert path.count("M") == 2
    assert "900.0,5800.0" in path


def test_build_coast_path_hopper_over_degenererte_linjer():
    def proj(lon, lat):
        return lon, lat
    too_short = type("Fake", (), {"coords": []})()
    path = R.build_coast_path([too_short], proj)
    assert path == ""
