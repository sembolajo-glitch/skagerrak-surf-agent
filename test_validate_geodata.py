"""Enhetstester for validate_geodata.py - ingen nettverk, syntetisk geometri."""

import math

import pytest

pytest.importorskip("shapely")
pytest.importorskip("yaml")

import validate_geodata as V
import geo_utils as G
from shapely.geometry import LineString


def test_project_nord_opp_og_ost_hoyre():
    lon_min, lat_max = 10.0, 59.5
    scale = 1000.0
    cos_lat = math.cos(math.radians(59.0))
    x0, y0 = V._project(lon_min, lat_max, lon_min, lat_max, scale, cos_lat)
    assert x0 == 0.0 and y0 == 0.0
    # 0.1 grad ost -> positiv x
    x1, y1 = V._project(lon_min + 0.1, lat_max, lon_min, lat_max, scale, cos_lat)
    assert x1 > x0
    assert y1 == y0
    # 0.1 grad sor (lavere breddegrad) -> positiv y (nedover i SVG)
    x2, y2 = V._project(lon_min, lat_max - 0.1, lon_min, lat_max, scale, cos_lat)
    assert y2 > y0
    assert x2 == x0


def _tree_from_lines(lines_utm):
    return G.build_strtree(lines_utm), lines_utm


def test_validate_reference_points_alle_bestaar():
    # Bygg en kunstig kystlinje rundt hvert referansepunkt slik at alle
    # bestaar sin egen terskel.
    lines = []
    for _pid, _name, lat, lon, cmp, threshold_m in V.REFERENCE_POINTS:
        ox, oy = G.to_utm(lon, lat)
        if cmp == "lt":
            offset = threshold_m * 0.5  # godt innenfor kravet
        else:
            offset = threshold_m * 1.5  # godt utenfor kravet
        lines.append(LineString([(ox - 1, oy + offset), (ox + 1, oy + offset)]))
    tree, line_geoms = _tree_from_lines(lines)
    ok, rows = V.validate_reference_points(tree, line_geoms)
    assert ok is True
    assert len(rows) == len(V.REFERENCE_POINTS)
    assert all(r[5] for r in rows)


def test_validate_reference_points_ett_feiler():
    # Alle kystlinjer rett ved siden av punktene (naer null avstand) -
    # "gt"-punktet (Vestfjorden, skal vaere LANGT fra land) feiler da.
    lines = []
    for _pid, _name, lat, lon, _cmp, _threshold_m in V.REFERENCE_POINTS:
        ox, oy = G.to_utm(lon, lat)
        lines.append(LineString([(ox - 1, oy + 1), (ox + 1, oy + 1)]))
    tree, line_geoms = _tree_from_lines(lines)
    ok, rows = V.validate_reference_points(tree, line_geoms)
    assert ok is False
    by_id = {r[0]: r[5] for r in rows}
    assert by_id["vestfjorden_midt"] is False
    assert by_id["slagen"] is True  # "lt"-krav, naer land er OK


def test_validate_reference_points_tomt_tre_feiler_alt():
    ok, rows = V.validate_reference_points(None, [])
    assert ok is False
    assert all(r[4] is None and r[5] is False for r in rows)


def test_render_svg_produserer_gyldig_struktur(tmp_path):
    lines = [LineString([(9.5, 58.8), (9.6, 58.9), (9.7, 58.85)])]
    spots = [{"id": "test1", "name": "Testspot", "lat": 58.85, "lon": 9.6}]
    rows = [("slagen", "Slagen", 59.320, 10.500, 229.0, True)]
    out_path, bounds = V.render_svg(lines, spots, rows, tmp_path / "test.svg")
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert content.startswith("<svg")
    assert "Testspot" in content
    assert "Slagen" in content
    assert bounds == (9.5, 58.8, 9.7, 58.9)


def test_render_svg_haandterer_polygon_via_boundary_lines(tmp_path):
    """render_svg tar shapely LineString direkte - to_boundary_lines()
    haandterer konvertering fra Polygon/Multi* for kalleren, saa denne
    testen bekrefter kjeden funker, ikke bare LineString-tilfellet."""
    from shapely.geometry import Polygon
    poly = Polygon([(9.5, 58.8), (9.6, 58.8), (9.6, 58.9), (9.5, 58.9)])
    lines = G.to_boundary_lines([poly])
    out_path, bounds = V.render_svg(lines, [], [], tmp_path / "poly.svg")
    assert out_path.exists()
    assert "coast" in out_path.read_text(encoding="utf-8")
