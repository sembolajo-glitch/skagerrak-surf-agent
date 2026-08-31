#!/usr/bin/env python3
"""
Bygg et rutenett av kandidat-surfpunkter over Skagerrak-kysten, fra de
samme Kartverket-geodataene build_fetch.py bruker (data/kystkontur.geojson,
data/dybdekurve.geojson).

Formaalet: spot-testeren i frontenden spurte brukeren om ting vi allerede
kan regne ut fra kystkontur/dybdekurve. Dette skriptet regner dem ut paa
forhaand, for et rutenett over 58.7-59.5 N, 9.3-11.2 O med 0.01 graders
oppløsning (ca. 1.1 km) - se BBOX/RES_DEG.

For hvert rutepunkt som ligger i SJOEN og er naermere enn NEAR_LAND_MAX_KM
(2 km) fra land, skrives:
  d20, d30, d50   avstand (km) til hhv. 20-, 30- og 50-meterskoten langs
                  `ar` (apen_retning under) - samme metode som
                  build_fetch.py sin compute_depth_profile(), gjenbrukt
                  direkte herfra.
  as (apen_sektor)  bredden i grader paa den aapne sektoren mellom
                  SECTOR_LO_DEG og SECTOR_HI_DEG (135-250, SO til VSV -
                  den generelle retningen mot aapent Skagerrak for denne
                  kysten), maalt som antall retninger der fetch overstiger
                  SECTOR_OPEN_KM (20 km).
  ar (apen_retning) midten (gjennomsnittet) av de aapne retningene over -
                  foreslaatt `facing` for spotten. null hvis ingen retning
                  i sektoren er aapen (as == 0) - da er d20/d30/d50 ogsaa
                  null, siden det ikke finnes noen retning aa maale langs.

Punkter paa land, eller mer enn 2 km fra kystkontur, tas ikke med - det
holder filen liten (maalet er under 2 MB) og luker bort aapent hav der en
"foreslaatt facing" uansett ikke er nyttig.

Sjoe/land-klassifisering (in_sea()): kystkontur.geojson er KUN
linjestykker, ikke lukkede polygoner (bekreftet - se geo_utils.py sin
ray_crossing_count()-docstring), saa et vanlig point-in-polygon-oppslag er
ikke mulig. I stedet: tell antall kryssinger langs en fast straale rett
sør (180 grader) fra punktet. Hele omraadet syd for hele det nedlastede
utsnittet (58.7-59.5 N) er aapent Skagerrak, godt utenfor noe som kan
forveksles med land paa disse lengdegradene (naermeste danske kyst,
Skagen, ligger paa ca. 57.7 N - SEA_RAY_KM er valgt til aa naa forbi hele
utsnittets sydkant fra ethvert rutepunkt, med god margin, uten aa naa saa
langt som Skagen). Et PARTALL kryssinger (inkludert null) betyr punktet er
paa samme side som det aapne havet syd for utsnittet, altsaa sjoe; et
ODDETALL betyr land.

    pip install -r requirements-geodata.txt
    python build_spotgrid.py

Engangs-/geodata-skript - IKKE en del av den daglige varselkjøringen
(forecast.yml kjoerer aldri dette). Kjoeres kun av geodata.yml, naar
geodataene endres.
"""

import argparse
import json
import sys
from pathlib import Path

import geo_utils as G
from build_fetch import (
    DEPTH_MAX_KM,
    DEPTH_TARGETS_M,
    DEPTH_TOLERANCE_M,
    FETCH_STEP_DEG,
    compute_depth_profile,
    group_by_depth,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_PATH = ROOT / "out" / "spotgrid.json"

# lat_min, lon_min, lat_max, lon_max - oppgitt i oppgaven
BBOX = (58.7, 9.3, 59.5, 11.2)
RES_DEG = 0.01

NEAR_LAND_MAX_KM = 2.0

# rett sor - se in_sea()-docstringen i modulen over for hvorfor dette er
# trygt over hele BBOX.
SEA_RAY_BEARING_DEG = 180
SEA_RAY_KM = 150.0

# SO til VSV - den aapne sektoren mot Skagerrak for denne kysten.
SECTOR_LO_DEG = 135
SECTOR_HI_DEG = 250
SECTOR_STEP_DEG = FETCH_STEP_DEG  # 5 - samme konvensjon som fetch_km_72
SECTOR_OPEN_KM = 20.0
# Rett over aapen-terskelen: vi trenger bare vite OM fetch overstiger 20
# km i en retning, ikke noeyaktig hvor langt - en kortere straale er
# billigere aa skyte.
SECTOR_RAY_MAX_KM = 25.0


def log(*a):
    print(*a, file=sys.stderr)


def grid_coords(bbox, res_deg):
    """Rutenettets (lat, lon)-verdier, avrundet til 2 desimaler (matcher
    res_deg=0.01) for aa unngaa flyttallsdrift i arange-stil lokker."""
    lat_min, lon_min, lat_max, lon_max = bbox
    n_lat = round((lat_max - lat_min) / res_deg)
    n_lon = round((lon_max - lon_min) / res_deg)
    lats = [round(lat_min + i * res_deg, 2) for i in range(n_lat + 1)]
    lons = [round(lon_min + j * res_deg, 2) for j in range(n_lon + 1)]
    return lats, lons


def in_sea(lon, lat, kyst_tree, kyst_lines):
    """Se modulens docstring for paritetsresonnementet."""
    n = G.ray_crossing_count(lon, lat, SEA_RAY_BEARING_DEG, SEA_RAY_KM, kyst_tree, kyst_lines)
    return n % 2 == 0


def open_sector(lon, lat, kyst_tree, kyst_lines):
    """
    (apen_sektor_grader, apen_retning) for punktet: se modulens docstring.
    apen_retning er None hvis ingen retning i sektoren har fetch > 20 km.
    """
    bearings = range(SECTOR_LO_DEG, SECTOR_HI_DEG + 1, SECTOR_STEP_DEG)
    open_bearings = [
        b for b in bearings
        if G.cast_ray_km(lon, lat, b, SECTOR_RAY_MAX_KM, kyst_tree, kyst_lines) > SECTOR_OPEN_KM
    ]
    if not open_bearings:
        return 0, None
    width_deg = len(open_bearings) * SECTOR_STEP_DEG
    center_deg = sum(open_bearings) / len(open_bearings)
    return width_deg, round(center_deg, 1)


def build_points(kyst_tree, kyst_lines, depth_trees, bbox=BBOX, res_deg=RES_DEG):
    lats, lons = grid_coords(bbox, res_deg)
    log(f"rutenett: {len(lats)} x {len(lons)} = {len(lats) * len(lons)} punkter "
        f"({bbox}, {res_deg} grader oppløsning)")

    points = []
    n_checked = 0
    for lat in lats:
        for lon in lons:
            n_checked += 1
            d_land = G.nearest_distance_km(lon, lat, kyst_tree, kyst_lines)
            if d_land is None or d_land > NEAR_LAND_MAX_KM:
                continue
            if not in_sea(lon, lat, kyst_tree, kyst_lines):
                continue

            sektor, retning = open_sector(lon, lat, kyst_tree, kyst_lines)
            if retning is None:
                profile = {t: None for t in DEPTH_TARGETS_M}
            else:
                profile = compute_depth_profile(lon, lat, retning, depth_trees)

            points.append({
                "lo": lon,
                "la": lat,
                "d20": profile[20],
                "d30": profile[30],
                "d50": profile[50],
                "as": sektor,
                "ar": retning,
            })

    log(f"  {n_checked} rutepunkter sjekket -> {len(points)} i sjøen og "
        f"< {NEAR_LAND_MAX_KM} km fra land")
    return points


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    kyst_path = data_dir / "kystkontur.geojson"
    dybde_path = data_dir / "dybdekurve.geojson"
    for p in (kyst_path, dybde_path):
        if not p.exists():
            log(f"FEIL: {p} finnes ikke. Kjor fetch_geodata.py foerst.")
            sys.exit(1)

    log(f"Laster {kyst_path} ...")
    kyst_raw = [g for g, _ in G.load_geojson(kyst_path)]
    kyst_utm = [G.reproject_geom(g, G.WGS84, G.UTM32) for g in kyst_raw]
    kyst_lines = G.to_boundary_lines(kyst_utm)
    kyst_tree = G.build_strtree(kyst_lines)
    log(f"  {len(kyst_raw)} features -> {len(kyst_lines)} linjestykker")

    log(f"Laster {dybde_path} ...")
    dybde_raw = G.load_geojson(dybde_path)
    dybde_utm = [(G.reproject_geom(g, G.WGS84, G.UTM32), p) for g, p in dybde_raw]
    depth_trees = group_by_depth(dybde_utm, DEPTH_TARGETS_M, DEPTH_TOLERANCE_M)

    points = build_points(kyst_tree, kyst_lines, depth_trees)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "bbox": list(BBOX),
            "res_deg": RES_DEG,
            "near_land_km": NEAR_LAND_MAX_KM,
            "sector_deg": [SECTOR_LO_DEG, SECTOR_HI_DEG],
            "sector_step_deg": SECTOR_STEP_DEG,
            "fetch_open_km": SECTOR_OPEN_KM,
            "depth_max_km": DEPTH_MAX_KM,
            "n": len(points),
        },
        "pts": points,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))

    size_bytes = out_path.stat().st_size
    log(f"\nSkrev {out_path}: {len(points)} punkter, {size_bytes} bytes ({size_bytes / 1024 / 1024:.2f} MB)")
    if size_bytes > 2 * 1024 * 1024:
        log("ADVARSEL: over 2 MB-maalet.")


if __name__ == "__main__":
    main()
