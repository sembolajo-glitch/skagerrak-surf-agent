"""Enhetstester for build_fetch.py - interpolasjon og dybdegruppering."""

import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("ruamel.yaml")

import build_fetch as B
import geo_utils as G
from shapely.geometry import LineString


def test_interp_table_pa_rutenett():
    tbl = list(range(72))  # 5-graders steg
    assert B.interp_table(tbl, 5, 0) == 0
    assert B.interp_table(tbl, 5, 25) == 5
    assert B.interp_table(tbl, 5, 355) == 71


def test_interp_table_mellom_punkter():
    tbl = [10.0] * 72
    tbl[9] = 30.0  # 45 grader
    mid = B.interp_table(tbl, 5, 21.25)  # 4.25 av steget mellom idx 4 (20) og idx5(25deg)... sjekk lin.interp
    assert 10.0 <= mid <= 30.0


def test_interp_table_wrap_rundt_0():
    tbl = [0.0] * 72
    tbl[0] = 100.0
    tbl[71] = 0.0
    mid = B.interp_table(tbl, 5, 358)  # mellom idx 71 (355deg) og idx 0 (0deg/360)
    assert 0.0 < mid < 100.0


def test_compass_16_matcher_spots_yaml_konvensjon():
    assert B.COMPASS_16 == ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
                             "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"]
    assert len(B.COMPASS_16) == 16


def test_group_by_depth_filtrerer_pa_toleranse():
    close = LineString([(0, 0), (100, 0)])
    far = LineString([(0, 100), (100, 100)])
    feats = [(close, {"depth_m": 20.3}), (far, {"depth_m": 25.0})]
    groups = B.group_by_depth(feats, target_depths=(20,), tolerance_m=0.5)
    assert 20 in groups
    tree, lines = groups[20]
    assert len(lines) == 1
    assert lines[0].equals(close)


def test_group_by_depth_ingen_treff_utelates():
    feats = [(LineString([(0, 0), (1, 1)]), {"depth_m": 100.0})]
    groups = B.group_by_depth(feats, target_depths=(20, 30, 50), tolerance_m=0.5)
    assert groups == {}


def test_compute_depth_profile_manglende_dybde_gir_none():
    profile = B.compute_depth_profile(10.0, 59.0, 180, depth_trees={})
    assert profile == {20: None, 30: None, 50: None}


def test_compute_depth_profile_finner_avstand():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    contour = LineString([(ox - 5000, oy - 5000), (ox + 5000, oy - 5000)])  # sor for origo
    tree = G.build_strtree([contour])
    depth_trees = {20: (tree, [contour])}
    profile = B.compute_depth_profile(lon0, lat0, 180, depth_trees)  # facing sor
    assert profile[20] is not None
    assert 4.9 < profile[20] < 5.1
    assert profile[30] is None
