#!/usr/bin/env python3
"""
Tegn ett diagnosekart (SVG) per spot i spots.yaml, pluss en oversiktsside
med alle spots side om side i mindre format.

Leser spots.yaml direkte (de allerede beregnede fetch_km_72/dybde_Xm_km/
dybde_Xm_status-feltene fra build_fetch.py, IKKE regnet paa nytt her) og
data/kystkontur.geojson + data/dybdekurve.geojson. Kjores i geodata.yml,
ETTER build_fetch.py (som har baade fersk geodata og nettverk allerede) -
se den workflowen for rekkefolgen.

Per spot, ca. 4x4 km rundt spotkoordinatet:
  - kystkontur som bakgrunn
  - dybdekoter 20/30/50 m i ulike graatoner
  - spot-koordinatet (fylt sirkel)
  - offshore_point (aapen sirkel + linje + avstand i km), klasse A/B
  - facing som en pil, merket med gradtall
  - dybdeprofil-peilingen (build_fetch.depth_bearing_for_spot()) som en
    stiplet pil, merket med kilde (offshore_point/gate/facing)
  - kryss der dybdestraalen traff 20/30/50 m-koten, med avstand paaskrevet.
    Status "ingen_kote"/"data_slutt": straalen tegnes til der soeket ga
    opp i stedet (se depth_search_cap_km() - build_fetch.py sin egen
    effective_cap er ikke eksponert, saa den er rederivert her KUN for
    tegningen, samme to bestanddeler som build_fetch.compute_depth_profile()
    selv bruker)
  - swell_window som en gjennomsiktig vifte
  - fetch_km/local_fetch_km (16-punkts kompasstabell) som en liten,
    logaritmisk skalert polardiagram-rose i et hjorne (ordre 2026-09-03:
    en enkelt urimelig retning skal vaere synlig med en gang, uten aa
    lese tallene) - skalert til SPOTTENS EGEN maksverdi (ikke en delt
    skala paa tvers av spots), fordi poenget er aa se AVVIK INNAD i denne
    ene tabellen, ikke sammenligne absolutt fetch mellom spots
  - tekstboks med klasse, min/ideal/max_hs, wind_weight, transmission/
    sector_half_width (kun klasse C, fra gate), skjaergaard_indeks,
    regional_wp_min/max, kalibrert, de tre dybdetallene, og MET sitt
    faktiske gridpunkt (grid_lat/grid_lon/grid_avstand_km, se under) -
    alt som ren tekst (i tillegg til krysset paa selve kartet for
    dybdetallene - et dybdemaal langt utenfor 4x4 km-vinduet ville
    ellers vaert usynlig, men skal fortsatt kunne leses)

Syv automatiske sjekker (se compute_flags()), listet i roedt i tekstboksen
og speilet i fargen paa det aktuelle elementet paa selve kartet:
  1. peiling til offshore_point utenfor swell_window
  2. over 45 graders avvik mellom facing og dybdepeilingen
  3. dybdeprofil der d50 er naermere enn d20
  4. offshore_point naermere enn 500 m eller lenger enn 8 km unna
  5. spot-koordinat naermere enn 50 m fra kystkontur (sannsynligvis paa land)
  6. MET sitt faktiske gridpunkt (fra forecast.json, se load_grid_info())
     ligger mer enn 5 km fra det spurte punktet
  7. offshore_point sitt gridpunkt ligger under 2 km fra spot-koordinatet -
     sannsynligvis samme gridcelle som spottet selv ville truffet, altsaa
     ingen reell funksjon (ordre 2026-09-03, se rapport til bruker: tre
     runder med gjetting om nettopp dette rundt Saltstein/Hvasser sitt
     offshore_point kunne vaert avgjort direkte med dette gridpunktet)

Sjekk 6/7 leser grid_lat/grid_lon/grid_avstand_km fra en tidligere
forecast.json (agent.py sin gather() logger dem na, fra MET sitt eget
API-svar - se sources.met_waves()) - IKKE regnet ut her. Uten en
forecast.json aa lese (se --forecast-json) hoppes disse to sjekkene
bare over, resten av kartet tegnes som vanlig.

    python diagnose_spot.py
    python diagnose_spot.py --data-dir data --spots-yaml spots.yaml --out-dir out/diagnose \\
        --forecast-json out/forecast.json
"""

import argparse
import json
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape as _esc

import yaml
from shapely.geometry import box

import build_fetch as BF
import geo_utils as G
import physics as P

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SPOTS_YAML = ROOT / "spots.yaml"
DEFAULT_OUT_DIR = ROOT / "out" / "diagnose"
DEFAULT_FORECAST_JSON = ROOT / "out" / "forecast.json"

HALF_EXTENT_KM = 2.0          # ~4x4 km synlig vindu rundt hvert spot
KM_PER_DEG_LAT = 111.32       # grov, jevnt over hele omraadet (9.3-11.2 O, 58.8-59.4 N)

CANVAS_PX = 900               # FAST intern koordinatramme - se render_spot_document()/
                               # render_overview() for hvorfor overviktssiden kan
                               # gjenbruke akkurat samme kropp uendret, bare i mindre <svg>
MARGIN_PX = 24

FACING_ARROW_KM = 1.3
DEPTH_BEARING_ARROW_KM = 1.3
SWELL_FAN_RADIUS_KM = 1.85

ROSE_CENTER = (CANVAS_PX - 110, CANVAS_PX - 110)
ROSE_RADIUS_PX = 68

DEPTH_COLORS = {20: "#cfcfcf", 30: "#8f8f8f", 50: "#4a4a4a"}
FLAG_COLOR = "#e03131"
OK_COLOR = "#1c3d5a"

COMPASS_16 = BF.COMPASS_16  # ["N","NNO","NO",...,"NNV"] - fra N, med klokka, 22.5 grader


def log(*a):
    print(*a, file=sys.stderr)


# ------------------------------------------------------------------ geometri


def _project(lon, lat, lon_min, lat_max, scale, cos_lat):
    x = (lon - lon_min) * cos_lat * scale
    y = (lat_max - lat) * scale
    return x, y


def _bearing_offset_px(bearing_deg, length_px):
    """Retningsvektor i SKJERM-piksler (y NED) for en kompasspeiling (0=N,
    med klokka) - motsatt fortegn paa y av geo_utils.bearing_vector(), som
    jobber i easting/northing (y OPP)."""
    rad = math.radians(bearing_deg)
    return length_px * math.sin(rad), -length_px * math.cos(rad)


def _utm_dist_km(lon1, lat1, lon2, lat2):
    x1, y1 = G.to_utm(lon1, lat1)
    x2, y2 = G.to_utm(lon2, lat2)
    return math.hypot(x2 - x1, y2 - y1) / 1000.0


def local_bbox(lat, lon, half_km=HALF_EXTENT_KM):
    """(lat_min, lon_min, lat_max, lon_max, cos_lat, scale) for en firkant
    paa 2*half_km rundt (lat, lon) - kvadratisk i PROJISERT rom (lon skalert
    med cos_lat), saa CANVAS_PX x CANVAS_PX alltid blir en gyldig kvadratisk
    ramme uansett spottens breddegrad."""
    cos_lat = math.cos(math.radians(lat))
    half_lat_deg = half_km / KM_PER_DEG_LAT
    half_lon_deg = half_km / (KM_PER_DEG_LAT * cos_lat)
    lat_min, lat_max = lat - half_lat_deg, lat + half_lat_deg
    lon_min, lon_max = lon - half_lon_deg, lon + half_lon_deg
    width_deg_scaled = (lon_max - lon_min) * cos_lat
    scale = (CANVAS_PX - 2 * MARGIN_PX) / width_deg_scaled
    return lat_min, lon_min, lat_max, lon_max, cos_lat, scale


def _clip_to_box(line, box_poly):
    """Klipp en WGS84-linje mot en synlig boks. SVG ville uansett ikke vist
    det som stikker utenfor viewBox, men eksplisitt klipping her holder
    path-dataene smaa (en linje paa mange km utenfor vinduet gir ellers
    unodvendig store koordinater) og er deterministisk paa tvers av
    SVG-visere, i motsetning til aa stole paa overflow-oppforsel."""
    inter = line.intersection(box_poly)
    if inter.is_empty:
        return []
    gt = inter.geom_type
    if gt == "LineString":
        return [inter]
    if gt == "MultiLineString":
        return list(inter.geoms)
    if gt == "GeometryCollection":
        return [g for g in inter.geoms if g.geom_type == "LineString"]
    return []  # Point/MultiPoint - tangentielt treff, ingen tegnbar strek


def clip_lines_near(lon, lat, half_km, tree_utm, lines_utm, lines_wgs84, view_box_wgs84):
    """Linjer (WGS84, klippet mot view_box_wgs84) fra et linjelag naer
    (lon, lat) - `tree_utm`/`lines_utm` brukes KUN til det raske STRtree-
    oppslaget (indeksene er 1:1 med lines_wgs84, se group_depth_lines()/
    load_kystkontur() for hvorfor det holder)."""
    if tree_utm is None:
        return []
    cx, cy = G.to_utm(lon, lat)
    half_m = half_km * 1000.0 * 1.5  # litt margin s.a. linjer som krysser kanten ikke mangler et hjorne
    query_box = box(cx - half_m, cy - half_m, cx + half_m, cy + half_m)
    idx = tree_utm.query(query_box, predicate="intersects")
    out = []
    for i in idx:
        out.extend(_clip_to_box(lines_wgs84[i], view_box_wgs84))
    return out


def group_depth_lines(dybde_raw, targets, tol=BF.DEPTH_TOLERANCE_M):
    """{ target: (tree_utm, lines_utm, lines_wgs84) } - samme filter som
    build_fetch.group_by_depth(), men beholder OGSAA WGS84-linjene (den
    kaster dem - build_fetch trenger bare UTM til straaleskyting, denne
    tegner i WGS84)."""
    out = {}
    for target in targets:
        feats_wgs84 = [g for g, p in dybde_raw if "depth_m" in p and abs(p["depth_m"] - target) <= tol]
        lines_wgs84 = G.to_boundary_lines(feats_wgs84)
        feats_utm = [G.reproject_geom(g, G.WGS84, G.UTM32) for g in feats_wgs84]
        lines_utm = G.to_boundary_lines(feats_utm)
        out[target] = (G.build_strtree(lines_utm), lines_utm, lines_wgs84)
    return out


def depth_search_cap_km(lon, lat, bearing, edge_tree, edge_lines, kyst_tree_utm, kyst_lines_utm, label=None):
    """Hvor langs `bearing` dybdesoeket i build_fetch.compute_depth_profile()
    ga opp (effective_cap der) - IKKE eksponert av den funksjonen, saa
    rederivert her fra de to samme bestanddelene (samme rekkefolge, samme
    DEPTH_MAX_KM-tak), KUN for aa vite hvor straalen skal tegnes til for en
    "ingen_kote"/"data_slutt"-status. Endrer ingenting i build_fetch.py."""
    edge_km = G.cast_ray_km(lon, lat, bearing, BF.DEPTH_MAX_KM, edge_tree, edge_lines)
    land_km = BF.substantial_land_crossing_km(lon, lat, bearing, kyst_tree_utm, kyst_lines_utm,
                                               max_km=BF.DEPTH_MAX_KM, label=label)
    return min(BF.DEPTH_MAX_KM, edge_km, land_km if land_km is not None else BF.DEPTH_MAX_KM)


# --------------------------------------------------------------- flagg


GRID_AVSTAND_MAX_KM = 5.0

# MET/WW3 sin oppgitte opplosning er ~4 km (se sources.met_waves() sin
# docstring) - halve cellebredden er en rimelig terskel for "det faktiske
# gridpunktet ligger sannsynligvis i SAMME celle spottets eget koordinat
# ville truffet". Se render_spot_body() for hvordan grid_spot_dist_km
# regnes (fra det EKTE returnerte gridpunktet, ikke et gjettet snap).
GRID_SAME_CELL_KM = 2.0


def compute_flags(spot, offshore_bearing, depth_bearing, offshore_dist_km, coast_dist_m,
                   grid_avstand_km=None, grid_spot_dist_km=None):
    """De syv automatiske sjekkene (se modulens docstring) - ren logikk,
    ingen SVG/geometrifiler involvert, saa den kan testes isolert. Peilinger
    og avstander regnes av kalleren (render_spot_body()) fra de samme
    geo_utils-funksjonene build_fetch.py selv bruker.

    grid_avstand_km/grid_spot_dist_km (ordre 2026-09-03, se rapport til
    bruker) kommer fra forecast.json, IKKE fra geodataene her - se
    load_grid_info() og main() for hvordan de leses inn. None naar
    forecast.json mangler eller ikke har data for dette spotet (MET
    svarte ikke den kjoringen) - begge sjekkene hoppes da over, i stedet
    for aa gjette."""
    flags = []
    window = tuple(spot["swell_window"])
    if offshore_bearing is not None and not P.in_window(offshore_bearing, window):
        flags.append(f"peiling til offshore_point ({offshore_bearing:.0f}°) ligger "
                      f"utenfor swell_window {list(window)}")
    if depth_bearing is not None:
        diff = P.ang_diff(spot["facing"], depth_bearing)
        if diff > 45:
            flags.append(f"facing ({spot['facing']:.0f}°) avviker {diff:.0f}° fra "
                          f"dybdepeilingen ({depth_bearing:.0f}°)")
    d20 = spot.get("dybde_20m_km")
    d50 = spot.get("dybde_50m_km")
    if d20 is not None and d50 is not None and d50 < d20:
        flags.append(f"d50 ({d50:.2f} km) er naermere enn d20 ({d20:.2f} km)")
    if offshore_dist_km is not None:
        if offshore_dist_km < 0.5:
            flags.append(f"offshore_point kun {offshore_dist_km * 1000:.0f} m unna (under 500 m)")
        elif offshore_dist_km > 8.0:
            flags.append(f"offshore_point {offshore_dist_km:.1f} km unna (over 8 km)")
    if coast_dist_m is not None and coast_dist_m < 50:
        flags.append(f"spot-koordinat kun {coast_dist_m:.0f} m fra kystkontur - sannsynligvis paa land")
    if grid_avstand_km is not None and grid_avstand_km > GRID_AVSTAND_MAX_KM:
        flags.append(f"MET sitt gridpunkt ligger {grid_avstand_km:.1f} km fra det spurte "
                      f"punktet (over {GRID_AVSTAND_MAX_KM:.0f} km)")
    if grid_spot_dist_km is not None and grid_spot_dist_km < GRID_SAME_CELL_KM:
        flags.append(f"offshore_point sitt gridpunkt er kun {grid_spot_dist_km:.1f} km fra "
                      f"spot-koordinatet - sannsynligvis samme gridcelle, ingen funksjon")
    return flags


# ------------------------------------------------------------------- SVG


STYLE_BLOCK = f'''<style>
  .sea {{ fill: #eaf3f8; }}
  .coast {{ fill: none; stroke: #2b6a8f; stroke-width: 1.2; stroke-linejoin: round; }}
  .depth-20 {{ fill: none; stroke: {DEPTH_COLORS[20]}; stroke-width: 1.4; }}
  .depth-30 {{ fill: none; stroke: {DEPTH_COLORS[30]}; stroke-width: 1.4; }}
  .depth-50 {{ fill: none; stroke: {DEPTH_COLORS[50]}; stroke-width: 1.4; }}
  .swell-fan {{ fill: #f08c00; fill-opacity: 0.16; stroke: #f08c00; stroke-opacity: 0.4; stroke-width: 1; }}
  .spot-dot {{ fill: {OK_COLOR}; stroke: #ffffff; stroke-width: 1.5; }}
  .spot-dot.flag {{ fill: {FLAG_COLOR}; }}
  .offshore-dot {{ fill: none; stroke: {OK_COLOR}; stroke-width: 2; }}
  .offshore-dot.flag {{ stroke: {FLAG_COLOR}; }}
  .offshore-line {{ stroke: {OK_COLOR}; stroke-width: 1.2; stroke-dasharray: 3 2; }}
  .offshore-line.flag {{ stroke: {FLAG_COLOR}; }}
  .facing-arrow {{ stroke: #2f9e44; stroke-width: 2.2; }}
  .facing-arrow.flag {{ stroke: {FLAG_COLOR}; }}
  .depth-bearing-arrow {{ stroke: #7048e8; stroke-width: 2; stroke-dasharray: 7 4; }}
  .depth-bearing-arrow.flag {{ stroke: {FLAG_COLOR}; }}
  .depth-cross {{ stroke-width: 2; }}
  .depth-cross.flag {{ stroke: {FLAG_COLOR} !important; }}
  .depth-ray {{ stroke-width: 1.4; stroke-dasharray: 2 3; }}
  .label {{ font-family: system-ui, sans-serif; font-size: 12px; fill: #1a1a1a; }}
  .label-small {{ font-family: system-ui, sans-serif; font-size: 10px; fill: #1a1a1a; }}
  .title {{ font-family: system-ui, sans-serif; font-size: 18px; font-weight: 700; fill: #1a1a1a; }}
  .box-bg {{ fill: #ffffff; fill-opacity: 0.88; stroke: #ccc; stroke-width: 1; }}
  .flag-text {{ fill: {FLAG_COLOR}; font-weight: 600; }}
  .rose-fill {{ fill: #1971c2; fill-opacity: 0.25; stroke: #1971c2; stroke-width: 1.3; }}
  .rose-spoke {{ stroke: #adb5bd; stroke-width: 0.6; }}
  .rose-ring {{ fill: none; stroke: #ced4da; stroke-width: 0.6; stroke-dasharray: 2 2; }}
</style>
<defs>
  <marker id="arrow-green" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="#2f9e44"/>
  </marker>
  <marker id="arrow-purple" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="#7048e8"/>
  </marker>
  <marker id="arrow-red" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="{FLAG_COLOR}"/>
  </marker>
</defs>'''


def _sector_points(lo, hi, step=2.0):
    """Grader fra lo til hi, MED KLOKKA, med wrap over 0/360 haandtert ved
    aa telle hi opp med 360 hvis den er mindre enn lo - samme konvensjon som
    physics.in_window()."""
    hi_eff = hi if hi >= lo else hi + 360.0
    n_steps = max(1, int(round((hi_eff - lo) / step)))
    return [lo + (hi_eff - lo) * k / n_steps for k in range(n_steps + 1)]


def _fetch_rose_svg(table, flagged=False):
    """Liten logaritmisk polardiagram-rose (16 retninger, N og med klokka)
    skalert til TABELLENS EGEN maks - se modulens docstring for hvorfor
    (poenget er en enkelt urimelig retning INNAD i denne tabellen, ikke en
    skala som er sammenlignbar paa tvers av spots)."""
    if not table:
        return ('<g class="fetch-rose">'
                f'<text x="{ROSE_CENTER[0]:.0f}" y="{ROSE_CENTER[1]:.0f}" class="label-small" text-anchor="middle">'
                'ingen fetch_km-tabell</text></g>')
    cx, cy = ROSE_CENTER
    vmax = max(max(table), 1.0)
    denom = math.log10(vmax + 1.0)

    def r_of(v):
        return ROSE_RADIUS_PX * (math.log10(v + 1.0) / denom) if denom > 0 else 0.0

    parts = [f'<g class="fetch-rose">']
    # referanseringer ved 25/50/75/100 % av skalaens ytterkant (i km, ikke lineaert - se r_of)
    for frac in (0.25, 0.5, 0.75, 1.0):
        parts.append(f'<circle class="rose-ring" cx="{cx:.1f}" cy="{cy:.1f}" r="{ROSE_RADIUS_PX * frac:.1f}"/>')
    for i in range(16):
        deg = i * 22.5
        dx, dy = _bearing_offset_px(deg, ROSE_RADIUS_PX)
        parts.append(f'<line class="rose-spoke" x1="{cx:.1f}" y1="{cy:.1f}" '
                     f'x2="{cx + dx:.1f}" y2="{cy + dy:.1f}"/>')
    pts = []
    for i, v in enumerate(table):
        deg = i * 22.5
        dx, dy = _bearing_offset_px(deg, r_of(v))
        pts.append((cx + dx, cy + dy))
    path_d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
    cls = "rose-fill flag" if flagged else "rose-fill"
    parts.append(f'<path class="{cls}" d="{path_d}"/>')
    parts.append(f'<text x="{cx:.0f}" y="{cy - ROSE_RADIUS_PX - 6:.0f}" class="label-small" '
                 f'text-anchor="middle">fetch_km (log, maks {vmax:.0f} km)</text>')
    parts.append('</g>')
    return "".join(parts)


def render_spot_body(spot, ctx):
    """SVG-elementene for ETT spot (ingen ytre &lt;svg&gt;-tag) i det faste
    CANVAS_PX x CANVAS_PX-koordinatsystemet - render_spot_document() og
    render_overview() pakker denne inn i hhv. en fullstor og en nedskalert
    &lt;svg&gt; UTEN aa regne noe om, se der. Returnerer (svg_str, flags)."""
    lat0, lon0 = spot["lat"], spot["lon"]
    lat_min, lon_min, lat_max, lon_max, cos_lat, scale = local_bbox(lat0, lon0)
    px_per_km = scale / KM_PER_DEG_LAT
    view_box_wgs84 = box(lon_min, lat_min, lon_max, lat_max)

    def proj(lon, lat):
        x, y = _project(lon, lat, lon_min, lat_max, scale, cos_lat)
        return x + MARGIN_PX, y + MARGIN_PX

    cx, cy = proj(lon0, lat0)  # == CANVAS_PX/2, CANVAS_PX/2 ved konstruksjon

    parts = [STYLE_BLOCK, f'<rect class="sea" x="0" y="0" width="{CANVAS_PX}" height="{CANVAS_PX}"/>']

    # -- kystkontur
    coast_local = clip_lines_near(lon0, lat0, HALF_EXTENT_KM * 1.2,
                                   ctx["kyst_tree_utm"], ctx["kyst_lines_utm"], ctx["kyst_lines_wgs84"],
                                   view_box_wgs84)
    coast_d = " ".join(
        "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (proj(lo, la) for lo, la in line.coords))
        for line in coast_local if len(line.coords) >= 2
    )
    if coast_d:
        parts.append(f'<path class="coast" d="{coast_d}"/>')

    # -- dybdekoter (bakgrunn, lysest til morkest)
    for target in (20, 30, 50):
        tree_utm, lines_utm, lines_wgs84 = ctx["depth_groups"].get(target, (None, [], []))
        local = clip_lines_near(lon0, lat0, HALF_EXTENT_KM * 1.2, tree_utm, lines_utm, lines_wgs84, view_box_wgs84)
        d = " ".join(
            "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (proj(lo, la) for lo, la in line.coords))
            for line in local if len(line.coords) >= 2
        )
        if d:
            parts.append(f'<path class="depth-{target}" d="{d}"/>')

    # -- peilinger og avstander (delt mellom flagg-beregning og tegning)
    offshore = spot.get("offshore_point")
    offshore_bearing = offshore_dist_km = None
    if offshore:
        off_lat, off_lon = offshore[0], offshore[1]
        offshore_bearing = G.bearing_between(lon0, lat0, off_lon, off_lat)
        offshore_dist_km = _utm_dist_km(lon0, lat0, off_lon, off_lat)

    depth_bearing, depth_source = BF.depth_bearing_for_spot(spot)
    coast_dist_km = G.nearest_distance_km(lon0, lat0, ctx["kyst_tree_utm"], ctx["kyst_lines_utm"])
    coast_dist_m = coast_dist_km * 1000.0 if coast_dist_km is not None else None

    # gridcelle (ordre 2026-09-03, se rapport til bruker) - fra forecast.json,
    # se load_grid_info()/main(). grid_spot_dist_km regnes KUN for spot MED
    # offshore_point (klasse C sitt wave_pt er gate, ikke ment aa vaere
    # "samme som spottet" i utgangspunktet, saa sjekken gir ikke mening der).
    grid = ctx.get("grid_by_id", {}).get(spot["id"])
    grid_avstand_km = grid.get("grid_avstand_km") if grid else None
    grid_lat = grid.get("grid_lat") if grid else None
    grid_lon = grid.get("grid_lon") if grid else None
    grid_spot_dist_km = None
    if offshore and grid_lat is not None and grid_lon is not None:
        grid_spot_dist_km = _utm_dist_km(lon0, lat0, grid_lon, grid_lat)

    flags = compute_flags(spot, offshore_bearing, depth_bearing, offshore_dist_km, coast_dist_m,
                           grid_avstand_km=grid_avstand_km, grid_spot_dist_km=grid_spot_dist_km)
    flag_cls = "flag" if flags else ""

    # -- swell_window-vifte
    lo, hi = spot["swell_window"]
    fan_r = SWELL_FAN_RADIUS_KM * px_per_km
    fan_pts = [(cx, cy)]
    for deg in _sector_points(lo, hi):
        dx, dy = _bearing_offset_px(deg, fan_r)
        fan_pts.append((cx + dx, cy + dy))
    fan_d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in fan_pts) + " Z"
    parts.append(f'<path class="swell-fan" d="{fan_d}"/>')

    # -- facing-pil
    facing = spot["facing"]
    fdx, fdy = _bearing_offset_px(facing, FACING_ARROW_KM * px_per_km)
    parts.append(f'<line class="facing-arrow {flag_cls}" x1="{cx:.1f}" y1="{cy:.1f}" '
                 f'x2="{cx + fdx:.1f}" y2="{cy + fdy:.1f}" '
                 f'marker-end="url(#arrow-{"red" if flags else "green"})"/>')
    parts.append(f'<text x="{cx + fdx + 4:.1f}" y="{cy + fdy:.1f}" class="label-small">facing {facing:.0f}°</text>')

    # -- dybdepeiling (stiplet pil)
    ddx, ddy = _bearing_offset_px(depth_bearing, DEPTH_BEARING_ARROW_KM * px_per_km)
    parts.append(f'<line class="depth-bearing-arrow {flag_cls}" x1="{cx:.1f}" y1="{cy:.1f}" '
                 f'x2="{cx + ddx:.1f}" y2="{cy + ddy:.1f}" '
                 f'marker-end="url(#arrow-{"red" if flags else "purple"})"/>')
    parts.append(f'<text x="{cx + ddx + 4:.1f}" y="{cy + ddy + 12:.1f}" class="label-small">'
                 f'peiling ({depth_source}) {depth_bearing:.0f}°</text>')

    # -- offshore_point
    if offshore:
        off_lat, off_lon = offshore[0], offshore[1]
        ox, oy = proj(off_lon, off_lat)
        off_flag = "flag" if (offshore_bearing is not None and not P.in_window(offshore_bearing, tuple(spot["swell_window"]))) \
            or (offshore_dist_km is not None and (offshore_dist_km < 0.5 or offshore_dist_km > 8.0)) else ""
        parts.append(f'<line class="offshore-line {off_flag}" x1="{cx:.1f}" y1="{cy:.1f}" x2="{ox:.1f}" y2="{oy:.1f}"/>')
        parts.append(f'<circle class="offshore-dot {off_flag}" cx="{ox:.1f}" cy="{oy:.1f}" r="6"/>')
        mx, my = (cx + ox) / 2, (cy + oy) / 2
        parts.append(f'<text x="{mx + 4:.1f}" y="{my - 4:.1f}" class="label-small">{offshore_dist_km:.2f} km</text>')

    # -- dybdekryss/-straaler langs depth_bearing
    d50_lt_d20 = spot.get("dybde_50m_km") is not None and spot.get("dybde_20m_km") is not None \
        and spot["dybde_50m_km"] < spot["dybde_20m_km"]
    for i, target in enumerate((20, 30, 50)):
        value = spot.get(f"dybde_{target}m_km")
        status = spot.get(f"dybde_{target}m_status")
        cross_flag = flags and d50_lt_d20 and target in (20, 50)
        color = FLAG_COLOR if cross_flag else DEPTH_COLORS[target]
        # tre koter langs SAMME peiling havner ofte bare noen titalls meter
        # fra hverandre (naer spotten) - stigende sideveis forskyvning per
        # target (perp_dx/perp_dy, vinkelrett paa peilingen) sprer etikettene
        # nok til aa ikke overlappe, selv naar krysspunktene selv nesten
        # sammenfaller.
        perp_dx, perp_dy = _bearing_offset_px(depth_bearing + 90, 10 + i * 16)
        if status == "maalt" and value is not None:
            tdx, tdy = _bearing_offset_px(depth_bearing, value * px_per_km)
            tx, ty = cx + tdx, cy + tdy
            s = 5
            parts.append(f'<g class="depth-cross{" flag" if cross_flag else ""}" stroke="{color}">'
                         f'<line x1="{tx - s:.1f}" y1="{ty - s:.1f}" x2="{tx + s:.1f}" y2="{ty + s:.1f}"/>'
                         f'<line x1="{tx - s:.1f}" y1="{ty + s:.1f}" x2="{tx + s:.1f}" y2="{ty - s:.1f}"/>'
                         f'</g>')
            parts.append(f'<text x="{tx + perp_dx:.1f}" y="{ty + perp_dy:.1f}" class="label-small">'
                         f'{target}m {value:.2f} km</text>')
        elif status in ("ingen_kote", "data_slutt"):
            cap_km = depth_search_cap_km(lon0, lat0, depth_bearing, ctx["edge_tree"], ctx["edge_lines"],
                                          ctx["kyst_tree_utm"], ctx["kyst_lines_utm"], label=f"{spot['id']}/{target}m")
            tdx, tdy = _bearing_offset_px(depth_bearing, cap_km * px_per_km)
            tx, ty = cx + tdx, cy + tdy
            parts.append(f'<line class="depth-ray" x1="{cx:.1f}" y1="{cy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" stroke="{color}"/>')
            parts.append(f'<text x="{tx + perp_dx:.1f}" y="{ty + perp_dy:.1f}" class="label-small">'
                         f'{target}m {status} ({cap_km:.1f} km)</text>')

    # -- spotkoordinat
    spot_cross_flag = coast_dist_m is not None and coast_dist_m < 50
    parts.append(f'<circle class="spot-dot{" flag" if spot_cross_flag else ""}" cx="{cx:.1f}" cy="{cy:.1f}" r="6"/>')

    # -- fetch-rose
    fetch_table = spot.get("fetch_km") or spot.get("local_fetch_km")
    parts.append(_fetch_rose_svg(fetch_table, flagged=bool(flags)))

    # -- tittel + tekstboks
    title_txt = _esc(f'{spot.get("name", spot["id"])} ({spot["id"]})')
    parts.append(f'<text x="{MARGIN_PX}" y="24" class="title">{title_txt}</text>')
    parts.append(_info_box_svg(spot, flags, grid_lat, grid_lon, grid_avstand_km))

    return "".join(parts), flags


def _fmt(v, unit="", digits=1):
    return "-" if v is None else f"{v:.{digits}f}{unit}"


def _info_box_svg(spot, flags, grid_lat=None, grid_lon=None, grid_avstand_km=None):
    klasse = spot.get("klasse", "?")
    gate = spot.get("gate")
    lines = [
        f'klasse {klasse}',
        f'hs min/ideal/max: {_fmt(spot.get("min_hs"))}/{_fmt(spot.get("ideal_hs"))}/{_fmt(spot.get("max_hs"))} m',
        f'wind_weight: {_fmt(spot.get("wind_weight"), digits=2)}',
    ]
    if gate:
        lines.append(f'transmission: {_fmt(gate.get("transmission"), digits=2)}   '
                     f'sector_half_width: {_fmt(gate.get("sector_half_width"), "°", 0)}')
    lines.append(f'skjaergaard_indeks: {_fmt(spot.get("skjaergaard_indeks"), digits=2)}')
    wp_min, wp_max = spot.get("regional_wp_min"), spot.get("regional_wp_max")
    wp_txt = "deaktivert/ikke satt" if wp_min is None and wp_max is None else f'{_fmt(wp_min)}-{_fmt(wp_max)} kW/m'
    lines.append(f'regional_wp: {wp_txt}')
    lines.append(f'kalibrert: {"ja" if spot.get("kalibrert") else "nei"}')
    lines.append('dybde: ' + '  '.join(
        f'{t}m {_fmt(spot.get(f"dybde_{t}m_km"), digits=2)} ({spot.get(f"dybde_{t}m_status", "?")})'
        for t in (20, 30, 50)))
    # MET-gridpunkt (ordre 2026-09-03, se rapport til bruker) - fra siste
    # forecast.json paa data-grenen, IKKE regnet ut her (se load_grid_info()).
    # "ukjent" naar forecast.json manglet/ikke hadde data for dette spotet -
    # ikke tolket som 0 km unna.
    grid_txt = ("ukjent (forecast.json mangler)" if grid_lat is None
                else f'{grid_lat:.4f},{grid_lon:.4f} ({_fmt(grid_avstand_km, " km")} fra spurt punkt)')
    lines.append(f'MET-grid: {grid_txt}')

    box_w = 320
    line_h = 15
    n_lines = len(lines) + len(flags)
    box_h = 14 + line_h * n_lines + 8
    box_x, box_y = MARGIN_PX, 32
    parts = [f'<rect class="box-bg" x="{box_x}" y="{box_y}" width="{box_w}" height="{box_h}" rx="4"/>']
    ty = box_y + 16
    for line in lines:
        parts.append(f'<text x="{box_x + 8}" y="{ty}" class="label">{_esc(line)}</text>')
        ty += line_h
    for flag in flags:
        parts.append(f'<text x="{box_x + 8}" y="{ty}" class="label flag-text">⚠ {_esc(flag)}</text>')
        ty += line_h
    return "".join(parts)


def render_spot_document(spot, ctx, width_px=CANVAS_PX):
    body, flags = render_spot_body(spot, ctx)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_PX} {CANVAS_PX}" '
           f'width="{width_px}" height="{width_px}">{body}</svg>')
    return svg, flags


# ------------------------------------------------------------------- oversikt


def render_overview(bodies_flags, tile_px=230, cols=5):
    """bodies_flags: liste av (spot, body_svg, flags). Nestede &lt;svg&gt; med
    SAMME viewBox som fullformatet - hver kropp gjenbrukes helt uendret, kun
    pakket i en mindre visningsstorrelse (ren SVG-skalering, ingen
    koordinater regnes om her)."""
    n = len(bodies_flags)
    rows = math.ceil(n / cols)
    header_h = 20
    cell_w = tile_px
    cell_h = tile_px + header_h
    pad = 6
    total_w = cols * (cell_w + pad) + pad
    total_h = rows * (cell_h + pad) + pad

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" '
             f'width="{total_w}" height="{total_h}">',
             '<style>.ov-title{font-family:system-ui,sans-serif;font-size:12px;fill:#1a1a1a;}'
             '.ov-cell{stroke:#ccc;stroke-width:1;fill:#fff;}'
             '.ov-cell.flag{stroke:#e03131;stroke-width:2.5;}</style>',
             f'<rect x="0" y="0" width="{total_w}" height="{total_h}" fill="#f8f9fa"/>']
    for i, (spot, body, flags) in enumerate(bodies_flags):
        row, col = divmod(i, cols)
        gx = pad + col * (cell_w + pad)
        gy = pad + row * (cell_h + pad)
        cls = "ov-cell flag" if flags else "ov-cell"
        title = f'{spot["id"]} ⚠{len(flags)}' if flags else spot["id"]
        parts.append(f'<g transform="translate({gx},{gy})">')
        parts.append(f'<rect class="{cls}" x="0" y="0" width="{cell_w}" height="{cell_h}"/>')
        parts.append(f'<text x="4" y="14" class="ov-title">{_esc(title)}</text>')
        parts.append(f'<svg x="0" y="{header_h}" width="{cell_w}" height="{tile_px}" '
                     f'viewBox="0 0 {CANVAS_PX} {CANVAS_PX}">{body}</svg>')
        parts.append('</g>')
    parts.append('</svg>')
    return "".join(parts)


# ------------------------------------------------------------------ konfig


def load_spots(spots_yaml):
    cfg = yaml.safe_load(Path(spots_yaml).read_text(encoding="utf-8"))
    defaults = cfg.get("defaults", {})
    spots = []
    for s in cfg["spots"]:
        merged = dict(defaults)
        merged.update(s)
        spots.append(merged)
    return spots


def load_grid_info(forecast_json):
    """{spot_id: {"grid_lat":..., "grid_lon":..., "grid_avstand_km":...}}
    fra en tidligere forecast.json (ordre 2026-09-03, se rapport til
    bruker) - IKKE regnet ut her, kun lest inn. Tolerant: manglende fil,
    ugyldig JSON, eller et spot uten feltene (MET svarte ikke den
    kjoringen - agent.py sin gather() setter da grid_lat/grid_lon/
    grid_avstand_km til None, se der) gir tomt/manglende oppslag i
    stedet for en feil - de to gridcelle-flaggene i compute_flags() blir
    da bare hoppet over for det spotet, se ogsaa .github/workflows/
    geodata.yml sitt "Hent siste forecast.json"-steg (tolerant paa
    samme maate)."""
    path = Path(forecast_json)
    if not path.exists():
        log(f"  ingen forecast.json funnet ({path}) - hopper over gridcelle-flaggene")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"  klarte ikke lese {path} ({exc}) - hopper over gridcelle-flaggene")
        return {}
    out = {}
    for s in payload.get("spots", []):
        sid = s.get("id")
        if sid and s.get("grid_lat") is not None and s.get("grid_lon") is not None:
            out[sid] = {
                "grid_lat": s["grid_lat"],
                "grid_lon": s["grid_lon"],
                "grid_avstand_km": s.get("grid_avstand_km"),
            }
    log(f"  {len(out)}/{len(payload.get('spots', []))} spot har gridpunkt i {path}")
    return out


def build_context(data_dir):
    kyst_path = Path(data_dir) / "kystkontur.geojson"
    dybde_path = Path(data_dir) / "dybdekurve.geojson"
    for p in (kyst_path, dybde_path):
        if not p.exists():
            log(f"FEIL: {p} finnes ikke. Kjor fetch_geodata.py (og build_fetch.py) foerst.")
            sys.exit(1)

    log(f"Laster {kyst_path} ...")
    kyst_raw = [g for g, _ in G.load_geojson(kyst_path)]
    kyst_lines_wgs84 = G.to_boundary_lines(kyst_raw)
    kyst_utm = [G.reproject_geom(g, G.WGS84, G.UTM32) for g in kyst_raw]
    kyst_lines_utm = G.to_boundary_lines(kyst_utm)
    kyst_tree_utm = G.build_strtree(kyst_lines_utm)
    log(f"  {len(kyst_raw)} features -> {len(kyst_lines_utm)} linjestykker")

    all_lons = [c[0] for line in kyst_lines_wgs84 for c in line.coords]
    all_lats = [c[1] for line in kyst_lines_wgs84 for c in line.coords]
    bbox_actual = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
    edge_tree, edge_lines = G.bbox_edge_tree(bbox_actual)

    log(f"Laster {dybde_path} ...")
    dybde_raw = G.load_geojson(dybde_path)
    depth_groups = group_depth_lines(dybde_raw, BF.DEPTH_TARGETS_M)

    return {
        "kyst_lines_wgs84": kyst_lines_wgs84,
        "kyst_lines_utm": kyst_lines_utm,
        "kyst_tree_utm": kyst_tree_utm,
        "edge_tree": edge_tree,
        "edge_lines": edge_lines,
        "depth_groups": depth_groups,
    }


# ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--spots-yaml", default=str(SPOTS_YAML))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--forecast-json", default=str(DEFAULT_FORECAST_JSON),
                     help="forrige forecast.json, kun for grid_lat/grid_lon/grid_avstand_km "
                          "(ordre 2026-09-03) - tolerant hvis den mangler, se load_grid_info()")
    args = ap.parse_args()

    spots = load_spots(args.spots_yaml)
    ctx = build_context(args.data_dir)
    ctx["grid_by_id"] = load_grid_info(args.forecast_json)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bodies_flags = []
    n_flagged = 0
    for spot in spots:
        body, flags = render_spot_body(spot, ctx)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_PX} {CANVAS_PX}" '
               f'width="{CANVAS_PX}" height="{CANVAS_PX}">{body}</svg>')
        (out_dir / f"{spot['id']}.svg").write_text(svg, encoding="utf-8")
        bodies_flags.append((spot, body, flags))
        status = f"{len(flags)} flagg" if flags else "OK"
        log(f"  {spot['id']:<16} {status}")
        if flags:
            n_flagged += 1
            for f in flags:
                log(f"    - {f}")

    overview_svg = render_overview(bodies_flags)
    (out_dir / "oversikt.svg").write_text(overview_svg, encoding="utf-8")

    log(f"\nSkrev {len(spots)} spot-kart + oversikt.svg til {out_dir}")
    log(f"{n_flagged}/{len(spots)} spot har minst ett automatisk flagg.")


if __name__ == "__main__":
    main()
