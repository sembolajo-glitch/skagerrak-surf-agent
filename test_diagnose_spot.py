"""Enhetstester for diagnose_spot.py - ingen nettverk, syntetisk geometri."""

import math
import xml.dom.minidom as minidom

import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("yaml")

import diagnose_spot as D
import geo_utils as G
from shapely.geometry import LineString

BASE_SPOT = {
    "id": "test", "name": "Test", "klasse": "A",
    "lat": 59.0, "lon": 10.0,
    "facing": 180, "swell_window": [160, 200],
    "min_hs": 1.0, "ideal_hs": 2.0, "max_hs": 3.0,
    "wind_weight": 1.0, "kalibrert": True,
}


# ------------------------------------------------------------- projeksjon


def test_project_nord_opp_og_ost_hoyre():
    lon_min, lat_max = 10.0, 59.5
    scale = 1000.0
    cos_lat = math.cos(math.radians(59.0))
    x0, y0 = D._project(lon_min, lat_max, lon_min, lat_max, scale, cos_lat)
    assert x0 == 0.0 and y0 == 0.0
    x1, y1 = D._project(lon_min + 0.1, lat_max, lon_min, lat_max, scale, cos_lat)
    assert x1 > x0 and y1 == y0
    x2, y2 = D._project(lon_min, lat_max - 0.1, lon_min, lat_max, scale, cos_lat)
    assert y2 > y0 and x2 == x0


def test_bearing_offset_px_nord_er_opp_ost_er_hoyre():
    dx, dy = D._bearing_offset_px(0, 10)   # nord
    assert math.isclose(dx, 0, abs_tol=1e-9) and dy < 0
    dx, dy = D._bearing_offset_px(90, 10)  # ost
    assert dx > 0 and math.isclose(dy, 0, abs_tol=1e-9)
    dx, dy = D._bearing_offset_px(180, 10)  # sor
    assert math.isclose(dx, 0, abs_tol=1e-9) and dy > 0


def test_local_bbox_kvadratisk_i_projisert_rom():
    lat_min, lon_min, lat_max, lon_max, cos_lat, scale = D.local_bbox(59.0, 10.0, half_km=2.0)
    width_deg_scaled = (lon_max - lon_min) * cos_lat
    height_deg = lat_max - lat_min
    assert math.isclose(width_deg_scaled, height_deg, rel_tol=1e-9)


def test_sector_points_uten_wrap():
    pts = D._sector_points(160, 200, step=10)
    assert pts[0] == 160
    assert pts[-1] == 200
    assert all(160 <= p <= 200 for p in pts)


def test_sector_points_med_wrap_over_0():
    pts = D._sector_points(350, 30, step=10)
    assert pts[0] == 350
    assert pts[-1] == 30 + 360
    # monotont stigende (ikke modulert - se docstring)
    assert all(a < b for a, b in zip(pts, pts[1:]))


# ------------------------------------------------------------------ flagg


def test_compute_flags_ingen_flagg_for_normalt_spot():
    spot = dict(BASE_SPOT, dybde_20m_km=1.0, dybde_50m_km=2.0)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=185,
                             offshore_dist_km=2.0, coast_dist_m=500)
    assert flags == []


def test_compute_flags_offshore_utenfor_vindu():
    spot = dict(BASE_SPOT)
    flags = D.compute_flags(spot, offshore_bearing=90, depth_bearing=180,
                             offshore_dist_km=2.0, coast_dist_m=500)
    assert any("utenfor swell_window" in f for f in flags)


def test_compute_flags_facing_avviker_over_45_grader():
    spot = dict(BASE_SPOT, facing=180)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=90,
                             offshore_dist_km=2.0, coast_dist_m=500)
    assert any("avviker" in f for f in flags)


def test_compute_flags_facing_innenfor_45_grader_ingen_flagg():
    spot = dict(BASE_SPOT, facing=180)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=200,
                             offshore_dist_km=2.0, coast_dist_m=500)
    assert not any("avviker" in f for f in flags)


def test_compute_flags_d50_naermere_enn_d20():
    spot = dict(BASE_SPOT, dybde_20m_km=2.0, dybde_50m_km=0.5)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=2.0, coast_dist_m=500)
    assert any("d50" in f and "naermere" in f for f in flags)


def test_compute_flags_d50_over_d20_ingen_flagg():
    spot = dict(BASE_SPOT, dybde_20m_km=0.5, dybde_50m_km=2.0)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=2.0, coast_dist_m=500)
    assert not any("d50" in f for f in flags)


def test_compute_flags_offshore_point_for_naer():
    spot = dict(BASE_SPOT)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=0.2, coast_dist_m=500)
    assert any("kun" in f and "unna" in f for f in flags)


def test_compute_flags_offshore_point_for_langt_unna():
    spot = dict(BASE_SPOT)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=9.0, coast_dist_m=500)
    assert any("9.0 km unna" in f for f in flags)


def test_compute_flags_offshore_point_grensetilfeller_ikke_flagget():
    """Grensene selv (500 m og 8 km) skal IKKE flagges - kun strengt under/over."""
    spot = dict(BASE_SPOT)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=0.5, coast_dist_m=500)
    assert not any("unna" in f for f in flags)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=8.0, coast_dist_m=500)
    assert not any("unna" in f for f in flags)


def test_compute_flags_spot_naer_land():
    spot = dict(BASE_SPOT)
    flags = D.compute_flags(spot, offshore_bearing=180, depth_bearing=180,
                             offshore_dist_km=2.0, coast_dist_m=10)
    assert any("sannsynligvis paa land" in f for f in flags)


def test_compute_flags_flere_flagg_samtidig():
    spot = dict(BASE_SPOT, facing=180, dybde_20m_km=2.0, dybde_50m_km=0.5)
    flags = D.compute_flags(spot, offshore_bearing=90, depth_bearing=270,
                             offshore_dist_km=0.1, coast_dist_m=5)
    # vindu + facing/dybde-avvik + d50<d20 + for naer offshore + naer land
    assert len(flags) == 5


def test_compute_flags_ingen_offshore_point_hopper_over_de_relevante_sjekkene():
    spot = dict(BASE_SPOT)
    flags = D.compute_flags(spot, offshore_bearing=None, depth_bearing=180,
                             offshore_dist_km=None, coast_dist_m=500)
    assert flags == []


# --------------------------------------------------------------- dybdegrupper


def test_group_depth_lines_filtrerer_paa_toleranse():
    close = LineString([(10.0, 59.0), (10.01, 59.0)])
    far = LineString([(10.0, 59.0), (10.01, 59.0)])
    dybde_raw = [(close, {"depth_m": 20.3}), (far, {"depth_m": 25.0})]
    groups = D.group_depth_lines(dybde_raw, (20, 30, 50))
    tree, lines_utm, lines_wgs84 = groups[20]
    assert len(lines_wgs84) == 1
    assert len(lines_utm) == 1
    tree30, lines_utm30, lines_wgs8430 = groups[30]
    assert lines_wgs8430 == []


def test_group_depth_lines_utm_og_wgs84_svarer_til_samme_linjer():
    line = LineString([(10.0, 59.0), (10.02, 59.01)])
    groups = D.group_depth_lines([(line, {"depth_m": 20.0})], (20,))
    tree, lines_utm, lines_wgs84 = groups[20]
    assert len(lines_utm) == len(lines_wgs84) == 1
    # UTM-versjonen skal tilsvare geo_utils sin egen reprojeksjon av samme linje
    expected_utm = G.to_boundary_lines([G.reproject_geom(line, G.WGS84, G.UTM32)])
    assert lines_utm[0].equals(expected_utm[0])


# ---------------------------------------------------------------- rendering


def _tiny_ctx():
    """En bitteliten, selvstendig kontekst (ingen filer) - kystlinje og
    tre dybdekoter noen faa km fra BASE_SPOT sitt koordinat."""
    lat0, lon0 = BASE_SPOT["lat"], BASE_SPOT["lon"]
    coast = LineString([(lon0 - 0.05, lat0 - 0.02), (lon0 + 0.05, lat0 - 0.02)])
    coast_utm = [G.reproject_geom(coast, G.WGS84, G.UTM32)]
    coast_lines_utm = G.to_boundary_lines(coast_utm)
    coast_tree = G.build_strtree(coast_lines_utm)
    bbox_actual = (lat0 - 0.5, lon0 - 0.5, lat0 + 0.5, lon0 + 0.5)
    edge_tree, edge_lines = G.bbox_edge_tree(bbox_actual)
    dybde_raw = [
        (LineString([(lon0 - 0.05, lat0 - 0.005), (lon0 + 0.05, lat0 - 0.005)]), {"depth_m": 20.0}),
        (LineString([(lon0 - 0.05, lat0 - 0.008), (lon0 + 0.05, lat0 - 0.008)]), {"depth_m": 30.0}),
        (LineString([(lon0 - 0.05, lat0 - 0.012), (lon0 + 0.05, lat0 - 0.012)]), {"depth_m": 50.0}),
    ]
    depth_groups = D.group_depth_lines(dybde_raw, D.BF.DEPTH_TARGETS_M)
    return {
        "kyst_lines_wgs84": [coast],
        "kyst_lines_utm": coast_lines_utm,
        "kyst_tree_utm": coast_tree,
        "edge_tree": edge_tree,
        "edge_lines": edge_lines,
        "depth_groups": depth_groups,
    }


def test_render_spot_body_gyldig_xml_og_inneholder_forventet_innhold():
    spot = dict(BASE_SPOT, gate=None)
    ctx = _tiny_ctx()
    body, flags = D.render_spot_body(spot, ctx)
    doc = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {D.CANVAS_PX} {D.CANVAS_PX}">{body}</svg>'
    minidom.parseString(doc)  # kaster hvis ikke velformet XML
    assert "swell-fan" in body
    assert "spot-dot" in body
    assert "fetch-rose" in body


def test_render_spot_body_escaper_flagg_med_angle_brackets_i_teksten():
    """Regresjonstest: 'offshore_point kun X m unna (under 500 m)'-varianten
    brukte tidligere bokstavelig '<'/'>' i flaggteksten, som gjorde SVG-en
    ugyldig XML (ExpatError: not well-formed). Tvinger frem et
    for-naer-offshore_point-flagg og sjekker at output fortsatt er gyldig."""
    lat0, lon0 = BASE_SPOT["lat"], BASE_SPOT["lon"]
    spot = dict(BASE_SPOT, offshore_point=[lat0 + 0.001, lon0 + 0.001])
    ctx = _tiny_ctx()
    body, flags = D.render_spot_body(spot, ctx)
    assert any("unna" in f for f in flags)
    doc = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {D.CANVAS_PX} {D.CANVAS_PX}">{body}</svg>'
    minidom.parseString(doc)


def test_render_spot_body_flagger_element_faarger_roedt_ved_flagg():
    lat0, lon0 = BASE_SPOT["lat"], BASE_SPOT["lon"]
    spot = dict(BASE_SPOT, offshore_point=[lat0 + 0.001, lon0 + 0.001])
    ctx = _tiny_ctx()
    body, flags = D.render_spot_body(spot, ctx)
    assert flags
    assert 'class="offshore-line flag"' in body
    assert 'class="offshore-dot flag"' in body


def test_render_spot_body_navn_med_spesialtegn_escapes():
    spot = dict(BASE_SPOT, name="Test & <Spesial>")
    ctx = _tiny_ctx()
    body, flags = D.render_spot_body(spot, ctx)
    doc = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {D.CANVAS_PX} {D.CANVAS_PX}">{body}</svg>'
    minidom.parseString(doc)
    assert "Test &amp; &lt;Spesial&gt;" in body


def test_render_spot_document_er_selvstendig_gyldig_svg(tmp_path):
    spot = dict(BASE_SPOT)
    ctx = _tiny_ctx()
    svg, flags = D.render_spot_document(spot, ctx)
    assert svg.startswith("<svg")
    minidom.parseString(svg)


def test_fetch_rose_svg_uten_tabell():
    svg = D._fetch_rose_svg(None)
    assert "ingen fetch_km-tabell" in svg


def test_fetch_rose_svg_med_tabell_gyldig_xml():
    svg = D._fetch_rose_svg([1, 2, 3, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 4, 3, 2])
    minidom.parseString(f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>')
    assert "rose-fill" in svg


# ---------------------------------------------------------------- oversikt


def test_render_overview_flagger_celle_med_flagg():
    spot_ok = dict(BASE_SPOT, id="ok_spot")
    spot_flag = dict(BASE_SPOT, id="flag_spot")
    ctx = _tiny_ctx()
    body_ok, flags_ok = D.render_spot_body(spot_ok, ctx)
    body_flag, flags_flag = D.render_spot_body(
        dict(spot_flag, offshore_point=[BASE_SPOT["lat"] + 0.001, BASE_SPOT["lon"] + 0.001]), ctx)
    overview = D.render_overview([(spot_ok, body_ok, flags_ok), (spot_flag, body_flag, flags_flag)])
    minidom.parseString(overview)
    assert "ov-cell flag" in overview
    assert "ok_spot" in overview and "flag_spot" in overview


# -------------------------------------------------------------------- main


def _write_fixture_geodata(data_dir, lat0, lon0):
    data_dir.mkdir(parents=True, exist_ok=True)
    coast = LineString([(lon0 - 0.05, lat0 - 0.02), (lon0 + 0.05, lat0 - 0.02)])
    G.write_geojson(data_dir / "kystkontur.geojson", [(coast, {})])
    depth = [
        (LineString([(lon0 - 0.05, lat0 - 0.005), (lon0 + 0.05, lat0 - 0.005)]), {"depth_m": 20.0}),
        (LineString([(lon0 - 0.05, lat0 - 0.008), (lon0 + 0.05, lat0 - 0.008)]), {"depth_m": 30.0}),
        (LineString([(lon0 - 0.05, lat0 - 0.012), (lon0 + 0.05, lat0 - 0.012)]), {"depth_m": 50.0}),
    ]
    G.write_geojson(data_dir / "dybdekurve.geojson", depth)


def test_main_skriver_ett_svg_per_spot_pluss_oversikt(tmp_path, capsys):
    lat0, lon0 = 59.0, 10.0
    data_dir = tmp_path / "data"
    _write_fixture_geodata(data_dir, lat0, lon0)
    spots_yaml = tmp_path / "spots.yaml"
    spots_yaml.write_text(f"""
defaults:
  wind_weight: 1.0
spots:
  - id: alfa
    name: Alfa
    klasse: A
    lat: {lat0}
    lon: {lon0}
    facing: 180
    swell_window: [160, 200]
    min_hs: 1.0
    ideal_hs: 2.0
    max_hs: 3.0
    kalibrert: true
  - id: bravo
    name: Bravo
    klasse: C
    lat: {lat0 + 0.01}
    lon: {lon0 + 0.01}
    facing: 180
    swell_window: [160, 200]
    min_hs: 1.0
    ideal_hs: 2.0
    max_hs: 3.0
    kalibrert: false
    gate:
      name: G
      lat: {lat0 - 0.3}
      lon: {lon0}
      distance_km: 33
      bearing_deg: 180
      sector_half_width: 20
      spread_s: 5
      transmission: 0.9
""", encoding="utf-8")
    out_dir = tmp_path / "out"

    import sys
    old_argv = sys.argv
    sys.argv = ["diagnose_spot.py", "--data-dir", str(data_dir),
                "--spots-yaml", str(spots_yaml), "--out-dir", str(out_dir)]
    try:
        D.main()
    finally:
        sys.argv = old_argv

    assert (out_dir / "alfa.svg").exists()
    assert (out_dir / "bravo.svg").exists()
    assert (out_dir / "oversikt.svg").exists()
    minidom.parse(str(out_dir / "alfa.svg"))
    minidom.parse(str(out_dir / "bravo.svg"))
    minidom.parse(str(out_dir / "oversikt.svg"))


def test_main_feiler_tydelig_uten_geodata(tmp_path):
    spots_yaml = tmp_path / "spots.yaml"
    spots_yaml.write_text("spots: []\n", encoding="utf-8")
    empty_data_dir = tmp_path / "data"
    empty_data_dir.mkdir()

    import sys
    old_argv = sys.argv
    sys.argv = ["diagnose_spot.py", "--data-dir", str(empty_data_dir),
                "--spots-yaml", str(spots_yaml), "--out-dir", str(tmp_path / "out")]
    try:
        with pytest.raises(SystemExit) as exc:
            D.main()
        assert exc.value.code == 1
    finally:
        sys.argv = old_argv
