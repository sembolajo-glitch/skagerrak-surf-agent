"""
Enhetstester for geo_utils.py - ren geometri, ingen nettverk.

Krever shapely/pyproj (requirements-geodata.txt). Hoppes over automatisk hvis
de ikke er installert, slik at hoved-testsuiten (test_physics.py osv.) kjorer
uendret i CI uten aa dra inn geodata-avhengigheter.
"""

import math

import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")

import geo_utils as G
from shapely.geometry import LineString, Polygon


def test_bearing_vector_kompassretninger():
    dx, dy = G.bearing_vector(0)
    assert math.isclose(dx, 0, abs_tol=1e-9) and math.isclose(dy, 1, abs_tol=1e-9)
    dx, dy = G.bearing_vector(90)
    assert math.isclose(dx, 1, abs_tol=1e-9) and math.isclose(dy, 0, abs_tol=1e-9)
    dx, dy = G.bearing_vector(180)
    assert math.isclose(dx, 0, abs_tol=1e-9) and math.isclose(dy, -1, abs_tol=1e-9)


def test_reproject_roundtrip():
    lon, lat = 10.0, 59.0
    x, y = G.to_utm(lon, lat)
    lon2, lat2 = G.to_wgs84_xy(x, y)
    assert math.isclose(lon, lon2, abs_tol=1e-9)
    assert math.isclose(lat, lat2, abs_tol=1e-9)


def test_cast_ray_treffer_rett_nord():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    coast = LineString([(ox - 50000, oy + 10000), (ox + 50000, oy + 10000)])
    tree = G.build_strtree([coast])
    d = G.cast_ray_km(lon0, lat0, 0, 300, tree, [coast])
    assert math.isclose(d, 10.0, abs_tol=0.05)


def test_cast_ray_ingen_treff_gir_tak():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    coast = LineString([(ox - 50000, oy + 10000), (ox + 50000, oy + 10000)])
    tree = G.build_strtree([coast])
    d = G.cast_ray_km(lon0, lat0, 180, 50, tree, [coast])
    assert d == 50.0


def test_cast_ray_skraa_vinkel():
    """45 grader mot en linje 10 km unna -> avstand 10/cos(45) = 14.14 km."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    coast = LineString([(ox - 50000, oy + 10000), (ox + 50000, oy + 10000)])
    tree = G.build_strtree([coast])
    d = G.cast_ray_km(lon0, lat0, 45, 300, tree, [coast])
    assert math.isclose(d, 14.142, abs_tol=0.1)


def test_cast_ray_tomt_tre_gir_tak():
    d = G.cast_ray_km(10.0, 59.0, 90, 123.0, None, [])
    assert d == 123.0


def test_nearest_distance_km():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    coast = LineString([(ox - 50000, oy + 10000), (ox + 50000, oy + 10000)])
    tree = G.build_strtree([coast])
    d = G.nearest_distance_km(lon0, lat0, tree, [coast])
    assert math.isclose(d, 10.0, abs_tol=0.05)


def test_nearest_distance_km_finner_naermeste_av_flere():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    far = LineString([(ox - 50000, oy + 50000), (ox + 50000, oy + 50000)])
    near = LineString([(ox - 50000, oy + 2000), (ox + 50000, oy + 2000)])
    tree = G.build_strtree([far, near])
    d = G.nearest_distance_km(lon0, lat0, tree, [far, near])
    assert math.isclose(d, 2.0, abs_tol=0.05)


def test_nearest_distance_km_tomt_tre_gir_none():
    assert G.nearest_distance_km(10.0, 59.0, None, []) is None


def test_bbox_edge_tree_finner_kant_i_alle_retninger():
    bbox = (58.0, 9.0, 59.0, 10.0)  # lat_min, lon_min, lat_max, lon_max
    tree, lines = G.bbox_edge_tree(bbox)
    lon0, lat0 = 9.5, 58.5  # midt i boksen
    for bearing in (0, 90, 180, 270):
        d = G.cast_ray_km(lon0, lat0, bearing, 1000.0, tree, lines)
        assert 0 < d < 200  # boksen er ~111x60 km, straalen MAA treffe en kant lenge for 1000 km


def test_bbox_edge_tree_punkt_naer_kant_gir_kort_avstand():
    bbox = (58.0, 9.0, 59.0, 10.0)
    tree, lines = G.bbox_edge_tree(bbox)
    d = G.cast_ray_km(9.99, 58.5, 90, 1000.0, tree, lines)  # naer ostkanten
    assert d < 2.0


def test_to_boundary_lines_polygon_gir_yttergrense():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    lines = G.to_boundary_lines([poly])
    assert len(lines) == 1
    assert lines[0].geom_type == "LineString"
    # yttergrensa skal vaere lukket og ga rundt hele polygonet
    assert lines[0].is_ring


def test_to_boundary_lines_beholder_linjer():
    line = LineString([(0, 0), (1, 1)])
    lines = G.to_boundary_lines([line])
    assert len(lines) == 1
    assert lines[0].equals(line)


def test_geojson_roundtrip(tmp_path):
    path = tmp_path / "test.geojson"
    line = LineString([(9.3, 58.7), (10.0, 59.0), (11.2, 59.5)])
    G.write_geojson(path, [(line, {"depth_m": 20.0})])
    loaded = G.load_geojson(path)
    assert len(loaded) == 1
    geom, props = loaded[0]
    assert geom.equals(line)
    assert props["depth_m"] == 20.0
