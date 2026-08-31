"""Enhetstester for build_spotgrid.py."""

import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")

import build_fetch as B
import build_spotgrid as S
import geo_utils as G
from shapely.geometry import LineString, box


def test_grid_coords_teller_riktig():
    lats, lons = S.grid_coords((58.70, 9.30, 58.72, 9.32), 0.01)
    assert lats == [58.70, 58.71, 58.72]
    assert lons == [9.30, 9.31, 9.32]


def test_grid_coords_full_bbox_riktig_antall_uten_flyttallsdrift():
    lats, lons = S.grid_coords(S.BBOX, S.RES_DEG)
    assert len(lats) == 81
    assert len(lons) == 191
    assert lats[0] == 58.7 and lats[-1] == 59.5
    assert lons[0] == 9.3 and lons[-1] == 11.2


def test_in_sea_null_kryssinger_er_sjo():
    lon0, lat0 = 10.0, 59.0
    assert S.in_sea(lon0, lat0, None, []) is True


def test_in_sea_ett_kryss_sorover_er_land():
    """Kystlinje rett SOR for punktet: straalen sorover (mot aapent hav
    lenger syd, se modulens docstring) krysser den en gang -> land paa
    feil side."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    coast = LineString([(ox - 50000, oy - 5000), (ox + 50000, oy - 5000)])
    tree = G.build_strtree([coast])
    assert S.in_sea(lon0, lat0, tree, [coast]) is False


def test_in_sea_to_kryss_sorover_er_fortsatt_sjo():
    """To kystlinjer sor for punktet (straalen passerer tvers gjennom en
    "oy" og ut igjen) -> partall kryssinger -> fortsatt sjoe."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    near = LineString([(ox - 50000, oy - 5000), (ox + 50000, oy - 5000)])
    far = LineString([(ox - 50000, oy - 15000), (ox + 50000, oy - 15000)])
    tree = G.build_strtree([near, far])
    assert S.in_sea(lon0, lat0, tree, [near, far]) is True


def test_open_sector_ingen_kystlinje_gir_hele_sektoren_apen():
    lon0, lat0 = 10.0, 59.0
    width, center = S.open_sector(lon0, lat0, None, [])
    n_bearings = len(range(S.SECTOR_LO_DEG, S.SECTOR_HI_DEG + 1, S.SECTOR_STEP_DEG))
    assert width == n_bearings * S.SECTOR_STEP_DEG
    # symmetrisk sektor -> midtpunktet av alle (like fordelte) retninger er
    # sektorens eget midtpunkt
    assert center == pytest.approx((S.SECTOR_LO_DEG + S.SECTOR_HI_DEG) / 2)


def test_open_sector_blokkert_innenfor_terskel_gir_ingen_apen_retning():
    """En "boks" tett rundt punktet (10 km halvbredde, altsaa <= 14.1 km i
    verste (diagonale) fall) blokkerer alle retninger under
    SECTOR_OPEN_KM=20 - ingen retning skal telle som apen."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)
    poly = box(ox - 10000, oy - 10000, ox + 10000, oy + 10000)
    lines = G.to_boundary_lines([poly])
    tree = G.build_strtree(lines)
    width, center = S.open_sector(lon0, lat0, tree, lines)
    assert width == 0
    assert center is None


def test_build_points_integrasjon_sjopunkt_naer_land():
    """Ett rutepunkt (bbox er akkurat dette ene punktet): kystlinje 300 m
    NORD for punktet (sjoe, naer land - se in_sea-testene over for hvorfor
    kysten maa ligge nord for at straalen sorover skal telle 0 kryssinger),
    og en 20 m-dybdekote lagt langs den apne sektoren (rett i syd, innenfor
    135-250) 5 km unna."""
    lon0, lat0 = 10.0, 59.0
    ox, oy = G.to_utm(lon0, lat0)

    coast = LineString([(ox - 50000, oy + 300), (ox + 50000, oy + 300)])
    kyst_tree = G.build_strtree([coast])
    kyst_lines = [coast]

    depth20 = LineString([(ox - 50000, oy - 5000), (ox + 50000, oy - 5000)])
    depth_trees = {20: (G.build_strtree([depth20]), [depth20])}

    points = S.build_points(
        kyst_tree, kyst_lines, depth_trees,
        bbox=(lat0, lon0, lat0, lon0), res_deg=S.RES_DEG,
    )

    assert len(points) == 1
    p = points[0]
    assert p["lo"] == lon0 and p["la"] == lat0
    assert p["as"] == len(range(S.SECTOR_LO_DEG, S.SECTOR_HI_DEG + 1, S.SECTOR_STEP_DEG)) * S.SECTOR_STEP_DEG
    assert p["ar"] == pytest.approx((S.SECTOR_LO_DEG + S.SECTOR_HI_DEG) / 2)
    assert p["d20"] is not None and 4.5 < p["d20"] < 5.5
    assert p["d30"] is None  # ingen 30 m-kote i depth_trees
    assert p["d50"] is None


def test_build_points_ekskluderer_land_og_fjernt_fra_land():
    """To rutepunkter, 0.05 grader (~2.9 km) fra hverandre: ett rett NORD
    for en kort kystlinje (land - se in_sea-testene over), ett langt unna
    enhver kystlinje (over NEAR_LAND_MAX_KM). Kystlinja er kort nok (600 m)
    til ikke aa naa det andre punktet. Ingen av dem skal havne i
    resultatet."""
    lat0 = 59.00
    lon_land, lon_far = 10.00, 10.05
    ox, oy = G.to_utm(lon_land, lat0)
    coast = LineString([(ox - 300, oy - 300), (ox + 300, oy - 300)])
    kyst_tree = G.build_strtree([coast])

    points = S.build_points(
        kyst_tree, [coast], depth_trees={},
        bbox=(lat0, lon_land, lat0, lon_far), res_deg=0.05,
    )
    assert points == []
