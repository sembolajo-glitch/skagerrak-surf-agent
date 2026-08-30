#!/usr/bin/env python3
"""
Valider ferdig nedlastet data/kystkontur.geojson foer vi stoler paa tallene.

To lag:
  1. Referansepunkter - fire punkter med kjent, grovt avmaalt avstand til
     land. Fanger opp den farligste feilklassen: en speilvendt eller
     akse-byttet kystkontur. "Midt i Vestfjorden" er viktigst - havner det
     punktet paa/naer land, er konturen feil, uansett hvor riktig de andre
     punktene ser ut isolert.
  2. SVG-forhaandsvisning - tegner hele kystkonturen pluss spots.yaml sine
     spot som punkter, saa en kan se med egne oyne at det ligner
     Oslofjorden og at skjaergaarden overlevde forenklingen.

    python validate_geodata.py
    python validate_geodata.py --out-svg out/kystkontur_preview.svg

Feiler (exit 1) hvis noe referansepunkt bryter sin terskel. SVG-en tegnes
uansett - ogsaa ved feil - siden det er akkurat da den er mest nyttig for
feilsoeking (er konturen synlig speilvendt i bildet?).
"""

import argparse
import math
import sys
from pathlib import Path

import yaml

import geo_utils as G

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SPOTS_YAML = ROOT / "spots.yaml"
DEFAULT_OUT_SVG = ROOT / "out" / "kystkontur_preview.svg"

# id, navn, lat, lon, "lt"/"gt", terskel_m
REFERENCE_POINTS = [
    ("slagen", "Slagen", 59.320, 10.500, "lt", 500),
    ("faerder_fyr", "Faerder fyr", 59.027, 10.524, "lt", 200),
    ("bastoy_sorspiss", "Bastoy sorspiss", 59.365, 10.530, "lt", 300),
    ("vestfjorden_midt", "Midt i Vestfjorden", 59.200, 10.600, "gt", 3000),
]


def log(*a):
    print(*a, file=sys.stderr)


# ------------------------------------------------------- referansepunkter


def validate_reference_points(kyst_tree, kyst_lines):
    log("\n" + "=" * 70)
    log("REFERANSEPUNKTER")
    log("=" * 70)
    all_ok = True
    rows = []
    for pid, name, lat, lon, cmp, threshold_m in REFERENCE_POINTS:
        d_km = G.nearest_distance_km(lon, lat, kyst_tree, kyst_lines)
        d_m = d_km * 1000 if d_km is not None else None
        if d_m is None:
            ok = False
            rel = f"({cmp} {threshold_m} m)"
        elif cmp == "lt":
            ok = d_m < threshold_m
            rel = f"< {threshold_m} m"
        else:
            ok = d_m > threshold_m
            rel = f"> {threshold_m} m"
        status = "OK" if ok else "FEIL"
        d_txt = f"{d_m:.0f} m" if d_m is not None else "(ingen kystkontur i det hele tatt)"
        log(f"  [{status}] {name:<20} ({lat},{lon}): {d_txt} fra kystkontur (krav: {rel})")
        rows.append((pid, name, lat, lon, d_m, ok))
        if not ok:
            all_ok = False

    if not all_ok:
        log("\nEN ELLER FLERE REFERANSEPUNKTER FEILET.")
        log("Sjekk saerlig 'Midt i Vestfjorden' - havner DEN innenfor 3 km, "
            "er kystkonturen sannsynligvis speilvendt eller akse-byttet, "
            "uansett hvor riktig de andre punktene ser ut isolert.")
    return all_ok, rows


# ------------------------------------------------------------------- SVG


def _project(lon, lat, lon_min, lat_max, scale, cos_lat):
    x = (lon - lon_min) * cos_lat * scale
    y = (lat_max - lat) * scale  # nord opp: hoyere breddegrad -> mindre y
    return x, y


def render_svg(kyst_lines_wgs84, spots, reference_rows, out_path, width_px=1400, margin_px=40):
    """
    Tegn kystkonturen (alle linjestykker, ingen ytterligere forenkling -
    dataene er allerede simplify()-et i fetch_geodata.py) pluss spots.yaml
    sine spot og de fire referansepunktene, som ren SVG. Ingen eksterne
    avhengigheter (matplotlib etc.) - bare tekst-templating, saa skriptet
    ikke trenger flere pakker enn resten av geodata-pipelinen.

    `kyst_lines_wgs84`: liste av shapely LineString i WGS84 (lon,lat) - se
    geo_utils.to_boundary_lines(), som ogsaa haandterer Polygon/Multi*
    hvis tjenesten en dag leverer det for dette laget i stedet for
    LineString.
    """
    all_lons = [c[0] for line in kyst_lines_wgs84 for c in line.coords]
    all_lats = [c[1] for line in kyst_lines_wgs84 for c in line.coords]
    lon_min, lon_max = min(all_lons), max(all_lons)
    lat_min, lat_max = min(all_lats), max(all_lats)

    cos_lat = math.cos(math.radians((lat_min + lat_max) / 2))
    width_deg_scaled = (lon_max - lon_min) * cos_lat
    height_deg = lat_max - lat_min
    scale = (width_px - 2 * margin_px) / width_deg_scaled
    height_px = height_deg * scale + 2 * margin_px

    def proj(lon, lat):
        x, y = _project(lon, lat, lon_min, lat_max, scale, cos_lat)
        return x + margin_px, y + margin_px

    # -- kystkontur: ett samlet <path> med flere M/L-subpaths (langt mindre
    # XML-overhead enn ett <path> per feature naar det er 60 000+ av dem)
    path_parts = []
    for line in kyst_lines_wgs84:
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        pts = [proj(lon, lat) for lon, lat in coords]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        path_parts.append(d)
    coastline_path = " ".join(path_parts)

    # -- spots
    spot_svg = []
    for s in spots:
        x, y = proj(s["lon"], s["lat"])
        label = s.get("name", s.get("id", "?"))
        spot_svg.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="spot-dot"/>'
            f'<text x="{x + 7:.1f}" y="{y + 4:.1f}" class="spot-label">{label}</text>'
        )

    # -- referansepunkter (kryss, farget etter OK/FEIL)
    ref_svg = []
    for pid, name, lat, lon, d_m, ok in reference_rows:
        x, y = proj(lon, lat)
        cls = "ref-ok" if ok else "ref-fail"
        d_txt = f"{d_m:.0f} m" if d_m is not None else "?"
        ref_svg.append(
            f'<g class="{cls}">'
            f'<line x1="{x-6:.1f}" y1="{y-6:.1f}" x2="{x+6:.1f}" y2="{y+6:.1f}"/>'
            f'<line x1="{x-6:.1f}" y1="{y+6:.1f}" x2="{x+6:.1f}" y2="{y-6:.1f}"/>'
            f'<text x="{x + 9:.1f}" y="{y - 8:.1f}" class="ref-label">{name} ({d_txt})</text>'
            f'</g>'
        )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width_px:.0f} {height_px:.0f}"
     font-family="system-ui, sans-serif">
  <style>
    .sea {{ fill: #eaf3f8; }}
    .coast {{ fill: none; stroke: #2b6a8f; stroke-width: 0.6; stroke-linejoin: round; }}
    .spot-dot {{ fill: #d9480f; stroke: #ffffff; stroke-width: 1; }}
    .spot-label {{ fill: #1a1a1a; font-size: 11px; }}
    .ref-ok line {{ stroke: #2f9e44; stroke-width: 2; }}
    .ref-fail line {{ stroke: #e03131; stroke-width: 2.5; }}
    .ref-label {{ font-size: 10px; fill: #1a1a1a; font-weight: 600; }}
  </style>
  <rect class="sea" x="0" y="0" width="{width_px:.0f}" height="{height_px:.0f}"/>
  <path class="coast" d="{coastline_path}"/>
  {''.join(spot_svg)}
  {''.join(ref_svg)}
</svg>'''

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    return out_path, (lon_min, lat_min, lon_max, lat_max)


# ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--spots-yaml", default=str(SPOTS_YAML))
    ap.add_argument("--out-svg", default=str(DEFAULT_OUT_SVG))
    args = ap.parse_args()

    kyst_path = Path(args.data_dir) / "kystkontur.geojson"
    if not kyst_path.exists():
        log(f"FEIL: {kyst_path} finnes ikke. Kjor fetch_geodata.py foerst.")
        sys.exit(1)

    log(f"Laster {kyst_path} ...")
    kyst_raw = [g for g, _ in G.load_geojson(kyst_path)]
    kyst_utm = [G.reproject_geom(g, G.WGS84, G.UTM32) for g in kyst_raw]
    kyst_lines = G.to_boundary_lines(kyst_utm)
    kyst_tree = G.build_strtree(kyst_lines)
    log(f"  {len(kyst_raw)} features -> {len(kyst_lines)} linjestykker")

    all_ok, rows = validate_reference_points(kyst_tree, kyst_lines)

    with open(args.spots_yaml, encoding="utf-8") as f:
        spots_doc = yaml.safe_load(f)
    spots = spots_doc["spots"]

    log("\n" + "=" * 70)
    log("SVG-FORHAANDSVISNING")
    log("=" * 70)
    kyst_lines_wgs84 = G.to_boundary_lines(kyst_raw)
    out_path, bounds = render_svg(kyst_lines_wgs84, spots, rows, Path(args.out_svg))
    log(f"  {len(spots)} spot, bounds kystkontur (lon,lat): {bounds}")
    log(f"  skrev {out_path} ({out_path.stat().st_size / 1024:.0f} kB)")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
