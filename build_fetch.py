#!/usr/bin/env python3
"""
Beregn fetch og dybdeprofil fra maalte Kartverket-geodata, og skriv
resultatet inn i spots.yaml.

Forutsetter at fetch_geodata.py er kjoert foerst, slik at
data/kystkontur.geojson og data/dybdekurve.geojson finnes.

For hvert spot i spots.yaml:
  - Steg 2: skyter straaler i 72 retninger (0, 5, 10, ..., 355 grader,
    kompass/med klokka) fra spotens (lat, lon) til foerste skjaering med
    kystkontur, tak 300 km. Skrevet som `fetch_km_72`.
  - Steg 3: for spotens `facing`-retning, finner avstanden til 20-, 30- og
    50-meterskoten i dybdekurve-laget. Skrevet som `dybde_20m_km`,
    `dybde_30m_km`, `dybde_50m_km` (null hvis koten ikke finnes/treffes
    innenfor taket).

Eksisterende 16-punkts fetch-tabeller (`fetch_km` / `local_fetch_km`)
BEHOLDES uendret - agent.py/physics.py bruker dem fortsatt. En kopi
legges ved siden av som `fetch_km_manuell` slik at de to kan sammenlignes.
Dette skriptet endrer ALDRI physics.py, ensemble.py eller agent.py.

    pip install -r requirements-geodata.txt
    python build_fetch.py
"""

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

import geo_utils as G

ROOT = Path(__file__).resolve().parent
SPOTS_YAML = ROOT / "spots.yaml"
DATA_DIR = ROOT / "data"

FETCH_MAX_KM = 300.0
FETCH_STEP_DEG = 5
N_RAYS = 360 // FETCH_STEP_DEG  # 72

DEPTH_TARGETS_M = (20, 30, 50)
DEPTH_MAX_KM = 100.0
DEPTH_TOLERANCE_M = 0.5

# rekkefoelgen brukt i de haandlagde 16-punkts tabellene (se spots.yaml-header)
COMPASS_16 = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
              "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"]


def log(*a):
    print(*a, file=sys.stderr)


def interp_table(table, step_deg, direction):
    """Lineaer interpolasjon i en jevnt fordelt kompasstabell (grader, med klokka fra N)."""
    n = len(table)
    pos = (direction % 360) / step_deg
    i = int(pos) % n
    j = (i + 1) % n
    w = pos - int(pos)
    return table[i] * (1 - w) + table[j] * w


# --------------------------------------------------------------- steg 2


def compute_fetch_72(lon, lat, kyst_tree, kyst_lines):
    return [
        round(G.cast_ray_km(lon, lat, i * FETCH_STEP_DEG, FETCH_MAX_KM, kyst_tree, kyst_lines), 1)
        for i in range(N_RAYS)
    ]


def report_deviation(spot_id, manual, measured_72):
    log(f"\n  {spot_id}: manuell vs maalt (72 pkt interpolert til 16 pkt)")
    log(f"  {'ret':<5}{'manuell':>9}{'maalt':>9}{'avvik':>9}")
    deltas = []
    for j, label in enumerate(COMPASS_16):
        bearing = j * 22.5
        m = manual[j]
        measured = interp_table(measured_72, FETCH_STEP_DEG, bearing)
        d = measured - m
        deltas.append(d)
        log(f"  {label:<5}{m:>9.1f}{measured:>9.1f}{d:>+9.1f}")
    mean_abs = sum(abs(d) for d in deltas) / len(deltas)
    worst_i = max(range(len(deltas)), key=lambda k: abs(deltas[k]))
    log(f"  gj.snitt |avvik|: {mean_abs:.1f} km   "
        f"storst avvik: {COMPASS_16[worst_i]} ({deltas[worst_i]:+.1f} km, "
        f"manuell {manual[worst_i]:.1f} -> maalt {interp_table(measured_72, FETCH_STEP_DEG, worst_i*22.5):.1f})")
    return mean_abs, deltas[worst_i], COMPASS_16[worst_i]


# --------------------------------------------------------------- steg 3


def group_by_depth(dybde_features, target_depths, tolerance_m=DEPTH_TOLERANCE_M):
    """{ depth: (tree, line_geoms) } for hver maaldybde som faktisk finnes i datasettet."""
    all_depths = sorted({
        p["depth_m"] for _, p in dybde_features if "depth_m" in p
    })
    log(f"  dybdekoter tilgjengelig i datasettet: {all_depths}")

    out = {}
    for target in target_depths:
        lines = [
            g for g, p in dybde_features
            if "depth_m" in p and abs(p["depth_m"] - target) <= tolerance_m
        ]
        line_geoms = G.to_boundary_lines(lines)
        if not line_geoms:
            log(f"  ADVARSEL: ingen {target} m-kote funnet (toleranse {tolerance_m} m) - "
                f"dybde_{target}m_km blir null for alle spots")
            continue
        out[target] = (G.build_strtree(line_geoms), line_geoms)
    return out


def compute_depth_profile(lon, lat, facing, depth_trees):
    out = {}
    for target in DEPTH_TARGETS_M:
        if target not in depth_trees:
            out[target] = None
            continue
        tree, lines = depth_trees[target]
        d = G.cast_ray_km(lon, lat, facing, DEPTH_MAX_KM, tree, lines)
        out[target] = round(d, 2) if d < DEPTH_MAX_KM else None
    return out


# ------------------------------------------------------------------ main


def make_flow_seq(values):
    seq = CommentedSeq(values)
    seq.fa.set_flow_style()
    return seq


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--spots-yaml", default=str(SPOTS_YAML))
    ap.add_argument("--dry-run", action="store_true", help="Ikke skriv til spots.yaml, bare rapporter")
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
    depth_trees = group_by_depth(dybde_utm, DEPTH_TARGETS_M)

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 100000
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.allow_unicode = True
    with open(args.spots_yaml, encoding="utf-8") as f:
        doc = yaml.load(f)

    log("\n" + "=" * 70)
    log("STEG 2 - fetch, 72 retninger")
    log("=" * 70)

    all_mean_abs = []
    for spot in doc["spots"]:
        lon, lat = spot["lon"], spot["lat"]
        measured_72 = compute_fetch_72(lon, lat, kyst_tree, kyst_lines)
        spot["fetch_km_72"] = make_flow_seq(measured_72)

        manual = spot.get("fetch_km") or spot.get("local_fetch_km")
        if manual:
            spot["fetch_km_manuell"] = make_flow_seq(list(manual))
            mean_abs, worst_delta, worst_label = report_deviation(spot["id"], manual, measured_72)
            all_mean_abs.append((spot["id"], mean_abs, worst_delta, worst_label))
        else:
            log(f"\n  {spot['id']}: ingen haandlaget tabell - ingen sammenligning")

    log("\n" + "-" * 70)
    log("Oppsummering avvik (manuell - haandlaget vs maalt fra kystkontur):")
    for spot_id, mean_abs, worst_delta, worst_label in sorted(all_mean_abs, key=lambda x: -x[1]):
        log(f"  {spot_id:<16} gj.snitt |avvik| {mean_abs:6.1f} km   "
            f"storst {worst_delta:+7.1f} km ({worst_label})")

    log("\n" + "=" * 70)
    log("STEG 3 - dybdeprofil langs facing")
    log("=" * 70)
    for spot in doc["spots"]:
        lon, lat, facing = spot["lon"], spot["lat"], spot["facing"]
        profile = compute_depth_profile(lon, lat, facing, depth_trees)
        for target in DEPTH_TARGETS_M:
            spot[f"dybde_{target}m_km"] = profile[target]
        log(f"  {spot['id']:<16} facing={facing:<4} "
            + "  ".join(f"{t}m={profile[t]}" for t in DEPTH_TARGETS_M))

    if args.dry_run:
        log("\n--dry-run: spots.yaml IKKE endret")
        return

    with open(args.spots_yaml, "w", encoding="utf-8") as f:
        yaml.dump(doc, f)
    log(f"\nSkrev {args.spots_yaml}")


if __name__ == "__main__":
    main()
