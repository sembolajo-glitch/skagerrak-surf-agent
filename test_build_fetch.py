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


def test_compute_fetch_72_effektiv_flat_tabell_uendret():
    tbl = [10.0] * 72
    eff = B.compute_fetch_72_effektiv(tbl)
    assert eff == tbl


def test_compute_fetch_72_effektiv_drukner_enkelt_smett():
    """Kjernen i brukerens funn: en enkelt straale som smetter gjennom et
    trangt skjaer gir 300 km midt i en retning der naboene er blokkert paa
    under 1 km - medianen skal droppe utliggeren, ikke ta den med."""
    tbl = [0.5] * 72
    tbl[10] = 300.0  # naboene (idx 8,9,11,12) er fortsatt 0.5
    eff = B.compute_fetch_72_effektiv(tbl)
    assert eff[10] == 0.5
    # punktene rundt paavirkes ikke av en enkelt nabo-utligger heller
    assert eff[8] == 0.5 and eff[12] == 0.5


def test_compute_fetch_72_effektiv_bruker_riktig_vindu():
    """window=2 med 5-graders steg -> +-10 grader (idx-2..idx+2, 5 verdier)."""
    tbl = [1.0] * 72
    tbl[10] = 100.0  # innenfor +-2 av idx 10,11,12 (avstand <=2)
    eff = B.compute_fetch_72_effektiv(tbl)
    # idx 10 selv: median([1,1,100,1,1]) = 1.0 (kun 1 utligger av 5)
    assert eff[10] == 1.0
    # idx 12 (2 unna): median([1,1,1,1,100]) fortsatt 1.0
    assert eff[12] == 1.0
    # idx 13 (3 unna, utenfor vinduet): helt upaavirket
    assert eff[13] == 1.0


def test_compute_fetch_72_effektiv_wrap_rundt_0():
    tbl = [5.0] * 72
    tbl[0] = 300.0
    eff = B.compute_fetch_72_effektiv(tbl)
    # idx 70, 71 er innenfor +-2 av idx 0 naar man teller med wrap
    assert eff[71] == 5.0
    assert eff[1] == 5.0


def test_classify_ray_category_kyst_vs_bbox_kant():
    assert B.classify_ray_category(2.0, 50.0) == "kyst"
    assert B.classify_ray_category(49.5, 50.0) == "bbox_kant"  # innenfor toleranse
    assert B.classify_ray_category(300.0, 50.0) == "bbox_kant"  # taket, langt over kanten


def test_analytic_fill_km_sektorer():
    assert B.analytic_fill_km(180) == (145.0, False)  # Skagen
    assert B.analytic_fill_km(160) == (145.0, False)  # nedre grense inkludert
    assert B.analytic_fill_km(215) == (200.0, False)  # Hirtshals
    assert B.analytic_fill_km(240) == (240.0, False)  # Skagerrak-aapningen
    assert B.analytic_fill_km(250) == (60.0, True)     # ovre grense IKKE inkludert -> usikker
    assert B.analytic_fill_km(0) == (60.0, True)
    assert B.analytic_fill_km(90) == (60.0, True)


def test_compute_fetch_72_endelig_bruker_maalt_for_kyst_analytisk_for_kant():
    bbox = (58.0, 9.0, 59.0, 10.0)
    tree, lines = G.bbox_edge_tree(bbox)
    lon0, lat0 = 9.5, 58.5

    fetch = [2.0] * B.N_RAYS  # alt "kyst" i utgangspunktet (godt innenfor kanten)
    idx_180 = 180 // B.FETCH_STEP_DEG
    idx_0 = 0
    edge_km_180 = G.cast_ray_km(lon0, lat0, 180, 1000.0, tree, lines)
    fetch[idx_180] = round(edge_km_180, 1)  # -> "bbox_kant" i retning 180 (Skagen-sektor)

    values, categories = B.compute_fetch_72_endelig(lon0, lat0, fetch, tree, lines)
    assert categories[idx_0] == "kyst"
    assert values[idx_0] == 2.0
    assert categories[idx_180] == "bbox_kant"
    assert values[idx_180] == 145.0  # analytisk Skagen-verdi, ikke edge_km/300


def test_report_deviation_kyst_only_hopper_over_bbox_kant(capsys):
    manual = [10.0] * 16
    measured_72 = [10.0] * B.N_RAYS
    categories = ["kyst"] * B.N_RAYS
    # bearing=180 (retning j=8 i 16-tabellen) sine to nabo-raastraaler (idx 35,36) er bbox_kant
    categories[35] = "bbox_kant"
    categories[36] = "bbox_kant"

    mean_abs, worst_delta, worst_label, n_skipped = B.report_deviation_kyst_only(
        "test", manual, measured_72, categories)
    assert n_skipped == 1
    assert mean_abs is not None


def test_report_deviation_kyst_only_ingen_kyst_gir_none():
    manual = [10.0] * 16
    measured_72 = [10.0] * B.N_RAYS
    categories = ["bbox_kant"] * B.N_RAYS
    mean_abs, worst_delta, worst_label, n_skipped = B.report_deviation_kyst_only(
        "test", manual, measured_72, categories)
    assert mean_abs is None
    assert n_skipped == 16


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
