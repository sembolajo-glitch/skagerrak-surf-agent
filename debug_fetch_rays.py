#!/usr/bin/env python3
"""
Diagnoseskript (engangs, IKKE i workflowen): for klasse C-spottene, skriv ut
de 72 raa fetch_km_72-verdiene per retning i en tabell, og beregn hvor mye
av hver straale som faktisk er en maaling mot ekte kystkontur versus bare
en straale som gikk tom fordi den forlot det nedlastede bbox-utsnittet foer
den fant noe aa treffe.

Kjoeres etter build_fetch.py har skrevet fetch_km_72 til spots.yaml:

    python debug_fetch_rays.py

Bakgrunn: FETCH_MAX_KM=300 i build_fetch.py, men bbox-utsnittet
(9.3-11.2 O, 58.7-59.5 N) er bare ca. 100x89 km - diagonalen er godt under
300 km. Kystkontur.geojson daekker KUN dette utsnittet. En straale som
forlater bbox-en uten aa ha truffet land finner ingenting mer aa treffe
(dataene stopper der), og faller derfor tilbake paa 300-kilometer-taket -
IKKE fordi det er 300 km reelt aapent vann, men fordi vi ikke har data
lenger ut i den retningen. compute_edge_km() regner ut avstanden til
bbox-kanten i samme retning; er straalens lengde (naer) lik denne, er den
klassifisert "kant" - ikke en reell maaling. Alt annet er "kyst": et
faktisk treff mot nedlastet kystkontur innenfor utsnittet.

For hvert klasse C-spot tegnes ogsaa alle 72 straalene oppaa kystkonturen,
saa en kan se hvor de "kyst"-klassifiserte lange straalene faktisk gaar -
en reell fjordaapning, eller et smett mellom skjaer som ikke burde telle.
"""

import argparse
import math
import sys
from pathlib import Path

import yaml

import build_fetch as B
import geo_utils as G

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SPOTS_YAML = ROOT / "spots.yaml"

FETCH_STEP_DEG = B.FETCH_STEP_DEG
N_RAYS = B.N_RAYS

CLASS_C_IDS = ["slagen", "skallevold", "sletteroyene", "bastoy_odden", "larkollen"]

COMPASS_16 = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
              "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"]


def log(*a):
    print(*a, file=sys.stderr)


def classify_rays(lon, lat, fetch_km_72, edge_tree, edge_lines):
    """
    For hver av de 72 retningene: (bearing, fetch_km, kant_km, kategori).
    kategori (her forenklet til "kant"/"kyst" for tabell/SVG-formaal) bruker
    SAMME regel som build_fetch.classify_ray_category() - den kanoniske
    klassifiseringen som ogsaa fetch_km_72_endelig bygger paa, saa denne
    visualiseringen aldri kan komme i utakt med det som faktisk skrives til
    spots.yaml.
    """
    rows = []
    for i, d in enumerate(fetch_km_72):
        bearing = i * FETCH_STEP_DEG
        edge_km = G.cast_ray_km(lon, lat, bearing, 1000.0, edge_tree, edge_lines)
        category = "kant" if B.classify_ray_category(d, edge_km) == "bbox_kant" else "kyst"
        rows.append((bearing, d, edge_km, category))
    return rows


def print_table(spot_id, rows):
    log(f"\n=== {spot_id}: 72 raa straaler ===")
    log(f"  {'grader':>6}  {'fetch_km':>9}  {'kant_km':>8}  kategori")
    for bearing, d, edge_km, category in rows:
        flag = "  <- ikke reelt (bbox for lite)" if category == "kant" else ""
        log(f"  {bearing:>6.0f}  {d:>9.1f}  {edge_km:>8.1f}  {category:<5}{flag}")
    n_kant = sum(1 for r in rows if r[3] == "kant")
    kyst_long = sorted((r for r in rows if r[3] == "kyst"), key=lambda r: -r[1])[:5]
    log(f"  {n_kant}/{len(rows)} retninger er 'kant' (bbox for lite, IKKE reelle maalinger)")
    if kyst_long:
        log("  lengste reelle 'kyst'-treff: " +
            ", ".join(f"{b:.0f} grader={d:.1f} km" for b, d, _e, _c in kyst_long))


# ------------------------------------------------------------------- SVG


def _project(lon, lat, lon_min, lat_max, scale, cos_lat):
    x = (lon - lon_min) * cos_lat * scale
    y = (lat_max - lat) * scale
    return x, y


def ray_endpoint(lon, lat, bearing_deg, distance_km):
    ox, oy = G.to_utm(lon, lat)
    dx, dy = G.bearing_vector(bearing_deg)
    ex = ox + dx * distance_km * 1000.0
    ey = oy + dy * distance_km * 1000.0
    return G.to_wgs84_xy(ex, ey)


def build_coast_path(kyst_lines_wgs84, proj):
    parts = []
    for line in kyst_lines_wgs84:
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        pts = [proj(lon, lat) for lon, lat in coords]
        parts.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts))
    return " ".join(parts)


def render_ray_svg(spot_id, spot_lat, spot_lon, rows, proj, bbox, width_px, height_px):
    lat_min, lon_min, lat_max, lon_max = bbox
    bx0, by0 = proj(lon_min, lat_min)
    bx1, by1 = proj(lon_max, lat_max)

    sx, sy = proj(spot_lon, spot_lat)

    ray_svg = []
    label_svg = []
    for bearing, d, edge_km, category in rows:
        draw_km = edge_km if category == "kant" else d
        elon, elat = ray_endpoint(spot_lon, spot_lat, bearing, draw_km)
        ex, ey = proj(elon, elat)
        cls = "ray-kant" if category == "kant" else "ray-kyst"
        ray_svg.append(
            f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" class="{cls}">'
            f'<title>{bearing:.0f} grader: {d:.1f} km ({category})</title></line>'
        )
        if category == "kyst" and d > 15:
            label_svg.append(
                f'<text x="{ex + 4:.1f}" y="{ey:.1f}" class="ray-label">{bearing:.0f} grader, {d:.0f} km</text>'
            )

    return f'''<svg viewBox="0 0 {width_px:.0f} {height_px:.0f}" font-family="'IBM Plex Mono', ui-monospace, monospace">
    <rect class="sea" x="0" y="0" width="{width_px:.0f}" height="{height_px:.0f}"/>
    <use href="#coastpath"/>
    <rect class="bbox-edge" x="{bx0:.1f}" y="{by1:.1f}" width="{bx1 - bx0:.1f}" height="{by0 - by1:.1f}"/>
    {''.join(ray_svg)}
    <circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" class="spot-dot"/>
    {''.join(label_svg)}
  </svg>'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--spots-yaml", default=str(SPOTS_YAML))
    ap.add_argument("--out-dir", default=str(ROOT / "out"))
    args = ap.parse_args()

    kyst_path = Path(args.data_dir) / "kystkontur.geojson"
    if not kyst_path.exists():
        log(f"FEIL: {kyst_path} finnes ikke. Kjor fetch_geodata.py foerst.")
        sys.exit(1)

    log(f"Laster {kyst_path} ...")
    kyst_raw = [g for g, _ in G.load_geojson(kyst_path)]
    kyst_lines_wgs84 = G.to_boundary_lines(kyst_raw)
    log(f"  {len(kyst_raw)} features -> {len(kyst_lines_wgs84)} linjestykker")

    all_lons = [c[0] for line in kyst_lines_wgs84 for c in line.coords]
    all_lats = [c[1] for line in kyst_lines_wgs84 for c in line.coords]
    bbox = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
    lat_min, lon_min, lat_max, lon_max = bbox
    edge_tree, edge_lines = G.bbox_edge_tree(bbox)

    with open(args.spots_yaml, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    spots_by_id = {s["id"]: s for s in doc["spots"]}

    cos_lat = math.cos(math.radians((lat_min + lat_max) / 2))
    width_px = 1400.0
    margin_px = 40.0
    scale = (width_px - 2 * margin_px) / ((lon_max - lon_min) * cos_lat)
    height_px = (lat_max - lat_min) * scale + 2 * margin_px

    def proj(lon, lat):
        x, y = _project(lon, lat, lon_min, lat_max, scale, cos_lat)
        return x + margin_px, y + margin_px

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    coast_path = build_coast_path(kyst_lines_wgs84, proj)
    (out_dir / "_rays_coastpath.svg").write_text(coast_path, encoding="utf-8")

    results = {}
    for spot_id in CLASS_C_IDS:
        spot = spots_by_id[spot_id]
        fetch_km_72 = spot["fetch_km_72"]
        rows = classify_rays(spot["lon"], spot["lat"], fetch_km_72, edge_tree, edge_lines)
        print_table(spot_id, rows)
        svg = render_ray_svg(spot_id, spot["lat"], spot["lon"], rows, proj, bbox, width_px, height_px)
        out_path = out_dir / f"rays_{spot_id}.svg"
        out_path.write_text(svg, encoding="utf-8")
        results[spot_id] = (rows, svg)
        log(f"  skrev {out_path}")

    return results, coast_path, (width_px, height_px)


if __name__ == "__main__":
    main()
