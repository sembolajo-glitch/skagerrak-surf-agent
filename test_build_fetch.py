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


def test_percentile_kjente_verdier():
    xs = [10, 20, 30, 40, 50]
    assert B.percentile(xs, 0) == 10
    assert B.percentile(xs, 100) == 50
    assert B.percentile(xs, 50) == 30


def test_percentile_interpolerer_lineaert():
    xs = [0, 10]
    assert B.percentile(xs, 25) == 2.5
    assert B.percentile(xs, 80) == 8.0


def test_percentile_ett_element():
    assert B.percentile([42], 80) == 42


def test_compute_fetch_72_kjegle_drukner_enkelt_skjaer_i_flertallet():
    """Kjernen i ordren 2026-08-31: en holme rett i siktelinjen skal ikke
    stoppe hele retningen naar resten av kjeglen ser lenger. p80 av
    kyst-delstraalene skal ligge naer den fjerne, sammenhengende kysten
    (~50 km), IKKE naer den lille holmen (2 km)."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)

    # "bbox"-kant lagt kjempelangt unna - ingen delstraale skal klassifiseres bbox_kant her
    huge_edge = LineString([(ox - 5_000_000, oy - 5_000_000), (ox + 5_000_000, oy - 5_000_000)])
    edge_tree, edge_lines = G.build_strtree([huge_edge]), [huge_edge]

    far_coast = LineString([(ox - 30000, oy + 50000), (ox + 30000, oy + 50000)])  # ~50 km nord, bred
    skerry = LineString([(ox - 40, oy + 2000), (ox + 40, oy + 2000)])  # liten holme, 2 km nord
    kyst_lines = [far_coast, skerry]
    kyst_tree = G.build_strtree(kyst_lines)

    values, categories, skew = B.compute_fetch_72_kjegle(lon0, lat0, kyst_tree, kyst_lines, edge_tree, edge_lines)

    assert categories[0] == "kyst"  # retning 0 = nord, midt i kjeglen som ser holmen
    assert values[0] > 30.0  # naer den fjerne kysten, IKKE naer 2 km-holmen
    assert skew[0] is not None


def test_compute_fetch_72_kjegle_flertall_bbox_kant_gir_apent_hav():
    """Er over halvparten av kjeglen bbox_kant, skal hovedretningen
    markeres apent_hav og fylles analytisk - ikke persentil av et
    mindretall kyst-delstraaler."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)

    # Bitteliten bbox - de aller fleste delstraaler i kjeglen forlater den
    # innenfor faa hundre meter og finner aldri kyst_lines (som ligger langt unna)
    tiny_edge = LineString([(ox - 100, oy + 100), (ox + 100, oy + 100)])
    edge_tree, edge_lines = G.build_strtree([tiny_edge]), [tiny_edge]

    far_coast = LineString([(ox - 30000, oy + 200000), (ox + 30000, oy + 200000)])
    kyst_tree = G.build_strtree([far_coast])

    values, categories, skew = B.compute_fetch_72_kjegle(lon0, lat0, kyst_tree, [far_coast], edge_tree, edge_lines)

    assert categories[0] in ("apent_hav", "apent_hav_usikker")
    assert skew[0] is None
    # bearing=0 (N) er ikke i noen ANALYTIC_SECTORS -> usikker default
    assert values[0] == B.ANALYTIC_DEFAULT_KM
    assert categories[0] == "apent_hav_usikker"


def test_compute_fetch_72_kjegle_analytisk_sektor_riktig_verdi():
    """bearing=180 (S) faller i Skagen-sektoren (160-200) naar hele
    kjeglen er apent hav."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    tiny_edge = LineString([(ox - 100, oy - 100), (ox + 100, oy - 100)])
    edge_tree, edge_lines = G.build_strtree([tiny_edge]), [tiny_edge]
    far_coast = LineString([(ox - 30000, oy - 200000), (ox + 30000, oy - 200000)])
    kyst_tree = G.build_strtree([far_coast])

    values, categories, skew = B.compute_fetch_72_kjegle(lon0, lat0, kyst_tree, [far_coast], edge_tree, edge_lines)
    idx_180 = 180 // B.FETCH_STEP_DEG
    assert categories[idx_180] == "apent_hav"
    assert values[idx_180] == 145.0


def test_report_kjegle_skew_beregner_gjennomsnitt_og_verste():
    skew = [1.0, -2.0, None, 5.0, None, -0.5]
    mean_abs, worst = B.report_kjegle_skew("test", skew)
    assert mean_abs == pytest.approx((1.0 + 2.0 + 5.0 + 0.5) / 4)
    assert worst == 5.0


def test_report_kjegle_skew_alle_none_gir_none():
    mean_abs, worst = B.report_kjegle_skew("test", [None] * 5)
    assert mean_abs is None
    assert worst is None


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


def test_depth_bearing_for_spot_bruker_offshore_point():
    """Peiling mot offshore_point, IKKE facing, naar feltet finnes -
    se rapporten om Moelen odden (27 grader avvik mellom de to)."""
    spot = {"lat": 58.975217, "lon": 9.812139, "facing": 175,
            "offshore_point": [58.970, 9.808]}
    bearing, source = B.depth_bearing_for_spot(spot)
    assert source == "offshore_point"
    assert 195 < bearing < 210          # ~202 grader, IKKE 175 (facing)


def test_depth_bearing_for_spot_bruker_gate_naar_ingen_offshore_point():
    """Skallevold (klasse C, ekte spots.yaml-koordinater): facing=115
    peker inn i bukta. Den GEOMETRISKE peilingen fra spotens (lat, lon)
    til gate-punktet (59.030, 10.520, mot Faerder) er ~173 grader - IKKE
    identisk med gate.bearing_deg=185 lagret i spots.yaml (det tallet er
    sektorsenteret for retningsfiltrering VED gate-punktet, en annen,
    fysisk uavhengig storrelse - se depth_bearing_for_spot() sin
    docstring). Begge peker uansett mot aapent vann, ikke inn i bukta."""
    spot = {"lat": 59.290, "lon": 10.470, "facing": 115,
            "gate": {"lat": 59.030, "lon": 10.520}}
    bearing, source = B.depth_bearing_for_spot(spot)
    assert source == "gate"
    assert 168 < bearing < 178


def test_depth_bearing_for_spot_offshore_point_foran_gate():
    """Prioritet: offshore_point slaar gate naar begge finnes."""
    spot = {"lat": 59.0, "lon": 10.0, "facing": 190,
            "offshore_point": [58.98, 9.99],
            "gate": {"lat": 59.03, "lon": 10.52}}
    bearing, source = B.depth_bearing_for_spot(spot)
    assert source == "offshore_point"


def test_depth_bearing_for_spot_faller_tilbake_til_facing():
    spot = {"lat": 59.0, "lon": 10.0, "facing": 190}
    bearing, source = B.depth_bearing_for_spot(spot)
    assert (bearing, source) == (190, "facing")


def test_depth_bearing_for_spot_ingen_offshore_point_eller_gate_naar_null():
    spot = {"lat": 59.0, "lon": 10.0, "facing": 190, "offshore_point": None, "gate": None}
    bearing, source = B.depth_bearing_for_spot(spot)
    assert (bearing, source) == (190, "facing")


def test_offshore_point_bearing_check_flagger_utenfor_vindu():
    """Jomfruland-saken, gjenskapt: det GAMLE offshore_point-et stemte kun
    med det forkastede koordinatet - peiling utenfor swell_window derfra."""
    spots = [
        {"id": "old_style", "lat": 58.840144, "lon": 9.565695,
         "swell_window": [100, 230], "offshore_point": [58.845, 9.650]},
    ]
    rows = B.offshore_point_bearing_check(spots)
    assert len(rows) == 1
    spot_id, bearing, window, ok = rows[0]
    assert spot_id == "old_style"
    assert ok is False
    assert not (100 <= bearing <= 230)


def test_offshore_point_bearing_check_ok_innenfor_vindu():
    spots = [
        {"id": "fixed", "lat": 58.840144, "lon": 9.565695,
         "swell_window": [100, 230], "offshore_point": [58.8330, 9.5760]},
    ]
    rows = B.offshore_point_bearing_check(spots)
    assert rows[0][3] is True


def test_offshore_point_bearing_check_hopper_over_spot_uten_feltet():
    spots = [{"id": "class_c", "lat": 59.0, "lon": 10.0, "swell_window": [160, 200]}]
    assert B.offshore_point_bearing_check(spots) == []


def test_compute_depth_profile_manglende_dybde_gir_none():
    profile = B.compute_depth_profile(10.0, 59.0, 180, depth_trees={},
                                       edge_tree=None, edge_lines=[],
                                       kyst_tree=None, kyst_lines=[])
    assert profile == {
        20: (None, "ingen_kote"), 30: (None, "ingen_kote"), 50: (None, "ingen_kote"),
    }


def test_compute_depth_profile_finner_avstand():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    contour = LineString([(ox - 5000, oy - 5000), (ox + 5000, oy - 5000)])  # sor for origo
    tree = G.build_strtree([contour])
    depth_trees = {20: (tree, [contour])}
    profile = B.compute_depth_profile(lon0, lat0, 180, depth_trees,  # facing sor
                                       edge_tree=None, edge_lines=[],
                                       kyst_tree=None, kyst_lines=[])
    value, status = profile[20]
    assert status == "maalt"
    assert 4.9 < value < 5.1
    assert profile[30] == (None, "ingen_kote")


def test_compute_depth_profile_bbox_kant_gir_data_slutt_ikke_ingen_kote():
    """Straalen forlater det nedlastede utsnittet FOER den ville naadd
    DEPTH_MAX_KM - status skal skille dette fra et reelt "fant ingenting"."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    # bbox-kant kun 5 km unna sorover - ingen dybdekote i det hele tatt der
    edge = LineString([(ox - 50000, oy - 5000), (ox + 50000, oy - 5000)])
    edge_tree = G.build_strtree([edge])
    # en 20 m-kote finnes, men langt bak (50 km) kanten - skal ikke naas
    contour_far = LineString([(ox - 50000, oy - 50000), (ox + 50000, oy - 50000)])
    depth_trees = {20: (G.build_strtree([contour_far]), [contour_far])}
    profile = B.compute_depth_profile(lon0, lat0, 180, depth_trees,
                                       edge_tree=edge_tree, edge_lines=[edge],
                                       kyst_tree=None, kyst_lines=[])
    assert profile[20] == (None, "data_slutt")


def test_compute_depth_profile_substansiell_kystkryssing_kapper_soeket():
    """Kjernen i fiksen: en 30 m-kote som ligger BAK (lenger unna enn) en
    substansiell kystkryssing skal IKKE rapporteres - den kan hoere til en
    helt annen, adskilt bukt (se Skallevold/Sletteroeyene-rapporten)."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    # solid kystlinje (200 m) 3 km unna
    coast = LineString([(ox - 100, oy - 3000), (ox + 100, oy - 3000)])
    kyst_tree = G.build_strtree([coast])
    # en 30 m-kote paa den andre siden, 10 km unna
    contour = LineString([(ox - 5000, oy - 10000), (ox + 5000, oy - 10000)])
    depth_trees = {30: (G.build_strtree([contour]), [contour])}

    profile = B.compute_depth_profile(lon0, lat0, 180, depth_trees,
                                       edge_tree=None, edge_lines=[],
                                       kyst_tree=kyst_tree, kyst_lines=[coast])
    assert profile[30] == (None, "ingen_kote")


def test_compute_depth_profile_lite_skjaer_stopper_ikke_soeket():
    """Motsatt av testen over: et skjaer UNDER substansialitetsgrensa (her
    40 m, grensa er 100 m) skal ikke kappe soeket - koten bak det skal
    fortsatt finnes."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    tiny_reef = LineString([(ox - 20, oy - 3000), (ox + 20, oy - 3000)])  # 40 m
    kyst_tree = G.build_strtree([tiny_reef])
    contour = LineString([(ox - 5000, oy - 10000), (ox + 5000, oy - 10000)])
    depth_trees = {30: (G.build_strtree([contour]), [contour])}

    profile = B.compute_depth_profile(lon0, lat0, 180, depth_trees,
                                       edge_tree=None, edge_lines=[],
                                       kyst_tree=kyst_tree, kyst_lines=[tiny_reef])
    value, status = profile[30]
    assert status == "maalt"
    assert 9.9 < value < 10.1


def test_substantial_land_crossing_km_ignorerer_smaa_skjaer():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    tiny = LineString([(ox - 20, oy - 2000), (ox + 20, oy - 2000)])       # 40 m, ignoreres
    solid = LineString([(ox - 200, oy - 8000), (ox + 200, oy - 8000)])   # 400 m, teller
    tree = G.build_strtree([tiny, solid])
    d = B.substantial_land_crossing_km(lon0, lat0, 180, tree, [tiny, solid])
    assert d == pytest.approx(8.0, abs=0.01)


def test_substantial_land_crossing_km_ingen_substansiell_gir_none():
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    tiny = LineString([(ox - 20, oy - 2000), (ox + 20, oy - 2000)])  # 40 m
    tree = G.build_strtree([tiny])
    assert B.substantial_land_crossing_km(lon0, lat0, 180, tree, [tiny]) is None


def test_substantial_land_crossing_km_tomt_tre_gir_none():
    assert B.substantial_land_crossing_km(10.0, 59.0, 180, None, []) is None
