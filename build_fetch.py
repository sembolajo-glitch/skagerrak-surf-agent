#!/usr/bin/env python3
"""
Beregn fetch og dybdeprofil fra maalte Kartverket-geodata, og skriv
resultatet inn i spots.yaml.

Forutsetter at fetch_geodata.py er kjoert foerst, slik at
data/kystkontur.geojson og data/dybdekurve.geojson finnes.

For hvert spot i spots.yaml:
  - Steg 2: skyter straaler i 72 retninger (0, 5, 10, ..., 355 grader,
    kompass/med klokka) fra spotens (lat, lon) til foerste skjaering med
    kystkontur, tak 300 km. Skrevet som `fetch_km_72` (raa enkeltstraale).

    `fetch_km_72_effektiv` er medianen av de fem straalene i en +-10 grader
    sektor rundt hver retning (se compute_fetch_72_effektiv()) - forsokte
    opprinnelig aa fikse straaler som smetter gjennom trange passasjer, men
    debug_fetch_rays.py (2026-08-31) viste at den egentlige aarsaken til de
    fleste 300 km-verdiene var noe medianen ikke kan fikse: FETCH_MAX_KM
    (300 km) er langt storre enn det nedlastede bbox-utsnittets egen
    diagonal (~150 km), saa en straale som ikke finner land FOER den
    forlater utsnittet finner rett og slett ikke mer data - det er ikke en
    reell fjordaapning eller et smett, bare fravaer av data.

    `fetch_km_72_endelig` er derfor den egentlige fiksen for bbox-kant-
    problemet: hver straale klassifiseres som "kyst" (traff ekte kystkontur
    - bruk den maalte lengden) eller "bbox_kant" (forlot utsnittet uten aa
    treffe noe - bruk en kjent ANALYTISK avstand til land, se
    ANALYTIC_SECTORS). Se compute_fetch_72_endelig().

    `fetch_km_72_kjegle` (2026-08-31) fikser et ANNET problem, fysisk
    begrunnet: en enkeltstraale er ren geometri og stoppes av en holme paa
    200 m, men en boelge diffrakterer rundt en slik holme og bygger seg
    videre - straalekasting maaler noe annet enn boelgefetch. Kjeglekasting
    (21 delstraaler over +-10 grader per hovedretning) tar 80-persentilen
    av KUN kyst-klassifiserte delstraaler, saa en enkelt holme rett i
    siktelinjen ikke lenger stopper hele retningen, mens en sammenhengende
    kystlinje fortsatt gjor det. Se compute_fetch_72_kjegle().
  - Steg 3: for spotens `facing`-retning, finner avstanden til 20-, 30- og
    50-meterskoten i dybdekurve-laget. Skrevet som `dybde_20m_km`,
    `dybde_30m_km`, `dybde_50m_km` (null hvis koten ikke finnes/treffes
    innenfor taket). Disse trenger ingen etterbehandling tilsvarende
    fetch_km_72_effektiv - skrives rett inn som de maales.

Eksisterende 16-punkts fetch-tabeller (`fetch_km` / `local_fetch_km`)
BEHOLDES uendret - agent.py/physics.py bruker dem fortsatt. En kopi
legges ved siden av som `fetch_km_manuell` slik at de to kan sammenlignes.
Dette skriptet endrer ALDRI physics.py, ensemble.py eller agent.py.

    pip install -r requirements-geodata.txt
    python build_fetch.py
"""

import argparse
import math
import statistics
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

# En straale klassifiseres "bbox_kant" (ikke en reell maaling) naar dens
# lengde er innenfor denne toleransen av avstanden til kanten av det
# nedlastede bbox-utsnittet i samme retning - se compute_fetch_72_endelig().
EDGE_TOL_KM = 1.0

# Analytiske fetch-verdier for "bbox_kant"-straaler sorover fra Ytre
# Oslofjord/Skagerrak-munningen - IKKE maalt fra nedlastet kystkontur.
# Unodvendig aa laste ned Jyllands kystlinje for tall som allerede er kjent
# (ordre 2026-08-31). (lo, hi, km): halvaapent intervall [lo, hi) i grader.
ANALYTIC_SECTORS = [
    (160, 200, 145.0),  # Skagen (Danmark)
    (200, 230, 200.0),  # Hirtshals
    (230, 250, 240.0),  # Skagerrak-aapningen mot Nordsjoen
]
ANALYTIC_DEFAULT_KM = 60.0  # andre bbox_kant-retninger - usikkert, markert som saadan

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


def compute_fetch_72_effektiv(fetch_km_72, window=2):
    """
    "Effektiv" fetch: medianen av straalen selv og `window` naboer paa hver
    side (window=2 -> +-2*5 grader = +-10 grader, siden fetch_km_72 er
    samplet hver 5. grad - de fem straalene DEKKER akkurat +-10 grader).
    Median (ikke gjennomsnitt) gjor at en enkelt smett-gjennom-straale
    (avviker sterkt fra naboene) ikke drar verdien med seg.
    """
    n = len(fetch_km_72)
    return [
        round(statistics.median(fetch_km_72[(i + k) % n] for k in range(-window, window + 1)), 1)
        for i in range(n)
    ]


def classify_ray_category(fetch_km, edge_km, tol_km=EDGE_TOL_KM):
    """"kyst" (ekte treff mot nedlastet kystkontur) eller "bbox_kant"
    (straalen forlot bbox-utsnittet uten aa treffe noe - fetch_km er da
    (naer) lik avstanden til bbox-kanten, edge_km, eller rett og slett
    FETCH_MAX_KM-taket)."""
    return "bbox_kant" if fetch_km >= edge_km - tol_km else "kyst"


def analytic_fill_km(bearing):
    """Kjent, analytisk avstand til land for en "bbox_kant"-straale i en
    gitt retning - se ANALYTIC_SECTORS. Returnerer (km, usikker: bool)."""
    b = bearing % 360
    for lo, hi, km in ANALYTIC_SECTORS:
        if lo <= b < hi:
            return km, False
    return ANALYTIC_DEFAULT_KM, True


def compute_fetch_72_endelig(lon, lat, fetch_km_72, edge_tree, edge_lines):
    """
    Endelig fetch: for hver av de 72 retningene, bruk den maalte lengden
    for "kyst"-straaler, og en analytisk avstand (se analytic_fill_km) for
    "bbox_kant"-straaler i stedet for aa stole paa FETCH_MAX_KM-taket.

    Returnerer (values, categories) - to 72-lister. categories er
    "kyst", "bbox_kant" (analytisk sektor-treff) eller "bbox_kant_usikker"
    (ANALYTIC_DEFAULT_KM brukt, ingen sektor matchet).
    """
    values, categories = [], []
    for i, d in enumerate(fetch_km_72):
        bearing = i * FETCH_STEP_DEG
        edge_km = G.cast_ray_km(lon, lat, bearing, 1000.0, edge_tree, edge_lines)
        if classify_ray_category(d, edge_km) == "kyst":
            values.append(d)
            categories.append("kyst")
        else:
            km, usikker = analytic_fill_km(bearing)
            values.append(km)
            categories.append("bbox_kant_usikker" if usikker else "bbox_kant")
    return values, categories


CONE_HALF_WIDTH_DEG = 10.0
CONE_N_RAYS = 21
# 21 straaler jevnt fordelt over +-10 grader gir aritmetisk 1 grad mellomrom
# ((2*10)/(21-1) = 1.0), IKKE en halv grad. Ordren 2026-08-31 ba om baade
# "21 straaler over +-10 grader" og "hver halve grad", som er selvmotsigende
# for den vifta (0.5 graders steg over +-10 grader ville krevd 41 straaler).
# Valgte det eksplisitte straaletallet (21); se rapport til bruker.
CONE_STEP_DEG = (2 * CONE_HALF_WIDTH_DEG) / (CONE_N_RAYS - 1)
CONE_PERCENTILE = 80
CONE_OPEN_SEA_FRACTION = 0.5  # over halvparten av kjeglen bbox_kant -> "apent_hav"


def percentile(values, pct):
    """
    Lineaer interpolert persentil (0-100) - samme metode ("linear") som
    numpy sin default. Implementert eksplisitt i stedet for
    statistics.quantiles(), som har hatt ulik standard interpolasjonsmetode
    mellom Python-versjoner og dermed ikke er deterministisk paa tvers av
    miljoer.
    """
    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return xs[0]
    rank = (pct / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return xs[lo]
    frac = rank - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def compute_fetch_72_kjegle(lon, lat, kyst_tree, kyst_lines, edge_tree, edge_lines):
    """
    Kjeglekasting: fysisk begrunnet i at en holme stopper en GEOMETRISK
    straale, men ikke en boelge - den diffrakterer rundt og bygger seg
    videre. Enkeltstraale maaler geometri; boelgefetch er noe annet.

    For hver av de 72 hovedretningene, skyt CONE_N_RAYS (21) delstraaler
    jevnt fordelt over +-CONE_HALF_WIDTH_DEG (10) grader rundt retningen.
    Klassifiser hver delstraale som "kyst" eller "bbox_kant" (samme regel
    som compute_fetch_72_endelig - se classify_ray_category()).

    Er MER ENN halvparten av de 21 delstraalene "bbox_kant", er hele
    hovedretningen i praksis aapent hav innenfor kjeglen - marker den
    "apent_hav" og fyll analytisk (samme sektorer som bbox_kant, se
    analytic_fill_km()). Ellers er verdien CONE_PERCENTILE-persentilen
    (80.) av lengdene til KUN kyst-delstraalene: en enkelt holme rett i
    siktelinjen (kort delstraale, i mindretall) druknes da av de andre 20,
    mens en sammenhengende kystlinje (de fleste/alle delstraaler korte)
    fortsatt gir en kort persentilverdi.

    Returnerer (values, categories, p80_minus_median) - tre 72-lister.
    p80_minus_median er None for "apent_hav"-retninger (ingen meningsfull
    kyst-fordeling der aa sammenligne median/p80 for).
    """
    values, categories, skew = [], [], []
    for i in range(N_RAYS):
        bearing = i * FETCH_STEP_DEG
        kyst_lengths = []
        n_kant = 0
        for k in range(CONE_N_RAYS):
            sub_bearing = bearing - CONE_HALF_WIDTH_DEG + k * CONE_STEP_DEG
            d = G.cast_ray_km(lon, lat, sub_bearing, FETCH_MAX_KM, kyst_tree, kyst_lines)
            edge_km = G.cast_ray_km(lon, lat, sub_bearing, 1000.0, edge_tree, edge_lines)
            if classify_ray_category(d, edge_km) == "kyst":
                kyst_lengths.append(d)
            else:
                n_kant += 1

        if n_kant > CONE_N_RAYS * CONE_OPEN_SEA_FRACTION:
            km, usikker = analytic_fill_km(bearing)
            values.append(km)
            categories.append("apent_hav_usikker" if usikker else "apent_hav")
            skew.append(None)
        else:
            p80 = percentile(kyst_lengths, CONE_PERCENTILE)
            med = statistics.median(kyst_lengths)
            values.append(round(p80, 1))
            categories.append("kyst")
            skew.append(round(p80 - med, 2))
    return values, categories, skew


def report_kjegle_skew(spot_id, skew):
    """
    Hvor mye 80-persentilen avviker fra medianen i kjeglekastingen, per
    hovedretning, aggregert per spot. Se ordre 2026-08-31: er de like,
    spiller skjaerene i kjeglen liten rolle for denne spotten (fordelingen
    av delstraale-lengder er jevn). Er de svaert ulike, er skjaergaarden
    dominerende - noen delstraaler er mye kortere enn andre - og resultatet
    boer vurderes paa nytt, ikke tas for gitt.
    """
    vals = [s for s in skew if s is not None]
    if not vals:
        log(f"  {spot_id}: ingen retninger med kyst-flertall i kjeglen - kan ikke vurdere p80 vs median")
        return None, None
    mean_abs = sum(abs(v) for v in vals) / len(vals)
    worst_i = max((i for i, s in enumerate(skew) if s is not None), key=lambda i: abs(skew[i]))
    log(f"  {spot_id}: p80 vs median i kjeglen - gj.snitt avvik {mean_abs:.2f} km, "
        f"storst {skew[worst_i]:+.2f} km ved {worst_i * FETCH_STEP_DEG} grader "
        f"({len(vals)}/{N_RAYS} retninger vurdert)")
    return mean_abs, skew[worst_i]


def report_deviation_kyst_only(spot_id, manual, measured_72, categories, label="kyst-straaler"):
    """
    Manuell vs maalt, KUN for de 16-punktsretningene der begge 5-graders
    naboraastraalene som interpoleres mellom er klassifisert "kyst" (eller
    en annen "reell maaling"-kategori - alt annet enn eksakt "kyst" hoppes
    over). En sammenligning mot en analytisk fylt verdi maaler ingenting -
    den er ikke en maaling i utgangspunktet. Se ordre 2026-08-31.
    """
    log(f"\n  {spot_id}: manuell vs {label} (kun retninger med rene kyst-treff i nabolaget)")
    log(f"  {'ret':<5}{'manuell':>9}{'maalt':>9}{'avvik':>9}")
    n = len(measured_72)
    deltas = []
    worst = None
    n_skipped = 0
    for j, label in enumerate(COMPASS_16):
        bearing = j * 22.5
        pos = (bearing % 360) / FETCH_STEP_DEG
        i0, i1 = int(pos) % n, (int(pos) + 1) % n
        if categories[i0] != "kyst" or categories[i1] != "kyst":
            log(f"  {label:<5}{'-':>9}{'-':>9}   hoppet over (bbox_kant i nabolaget)")
            n_skipped += 1
            continue
        m = manual[j]
        measured = interp_table(measured_72, FETCH_STEP_DEG, bearing)
        d = measured - m
        deltas.append(d)
        log(f"  {label:<5}{m:>9.1f}{measured:>9.1f}{d:>+9.1f}")
        if worst is None or abs(d) > abs(worst[1]):
            worst = (label, d, m, measured)

    if not deltas:
        log("  ingen retninger med rene kyst-treff aa sammenligne")
        return None, None, None, n_skipped

    mean_abs = sum(abs(d) for d in deltas) / len(deltas)
    log(f"  gj.snitt |avvik| ({len(deltas)}/16 retninger, {n_skipped} hoppet over): {mean_abs:.1f} km   "
        f"storst avvik: {worst[0]} ({worst[1]:+.1f} km, manuell {worst[2]:.1f} -> maalt {worst[3]:.1f})")
    return mean_abs, worst[1], worst[0], n_skipped


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

    # Bbox brukt til aa klassifisere "kyst" vs "bbox_kant" er det FAKTISK
    # nedlastede utsnittets egen ytterkant, ikke en antatt konstant - regnes
    # ut fra dataene selv.
    kyst_lines_wgs84 = G.to_boundary_lines(kyst_raw)
    all_lons = [c[0] for line in kyst_lines_wgs84 for c in line.coords]
    all_lats = [c[1] for line in kyst_lines_wgs84 for c in line.coords]
    bbox_actual = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
    edge_tree, edge_lines = G.bbox_edge_tree(bbox_actual)
    log(f"  bbox (faktisk nedlastet utsnitt): {bbox_actual}")

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
    all_mean_abs_kjegle = []
    for spot in doc["spots"]:
        lon, lat = spot["lon"], spot["lat"]
        measured_72 = compute_fetch_72(lon, lat, kyst_tree, kyst_lines)
        measured_72_eff = compute_fetch_72_effektiv(measured_72)
        endelig, categories = compute_fetch_72_endelig(lon, lat, measured_72, edge_tree, edge_lines)
        kjegle, categories_kjegle, skew = compute_fetch_72_kjegle(
            lon, lat, kyst_tree, kyst_lines, edge_tree, edge_lines)
        spot["fetch_km_72"] = make_flow_seq(measured_72)
        spot["fetch_km_72_effektiv"] = make_flow_seq(measured_72_eff)
        spot["fetch_km_72_endelig"] = make_flow_seq(endelig)
        spot["fetch_km_72_kjegle"] = make_flow_seq(kjegle)

        n_kyst = categories.count("kyst")
        n_kant = categories.count("bbox_kant")
        n_kant_usikker = categories.count("bbox_kant_usikker")
        n_apent = categories_kjegle.count("apent_hav") + categories_kjegle.count("apent_hav_usikker")
        log(f"\n  {spot['id']}: {n_kyst} kyst, {n_kant} bbox_kant (analytisk sektor), "
            f"{n_kant_usikker} bbox_kant_usikker ({ANALYTIC_DEFAULT_KM:.0f} km default)   "
            f"[kjegle: {N_RAYS - n_apent} kyst-flertall, {n_apent} apent_hav]")
        report_kjegle_skew(spot["id"], skew)

        manual = spot.get("fetch_km") or spot.get("local_fetch_km")
        if manual:
            spot["fetch_km_manuell"] = make_flow_seq(list(manual))
            mean_abs, worst_delta, worst_label, n_skipped = report_deviation_kyst_only(
                spot["id"], manual, measured_72, categories, label="kyst-straaler (fetch_km_72_endelig)")
            if mean_abs is not None:
                all_mean_abs.append((spot["id"], mean_abs, worst_delta, worst_label, n_skipped))

            mean_abs_kj, worst_delta_kj, worst_label_kj, n_skipped_kj = report_deviation_kyst_only(
                spot["id"], manual, kjegle, categories_kjegle, label="kjeglekasting (fetch_km_72_kjegle)")
            if mean_abs_kj is not None:
                all_mean_abs_kjegle.append((spot["id"], mean_abs_kj, worst_delta_kj, worst_label_kj, n_skipped_kj))
        else:
            log("  ingen haandlaget tabell - ingen sammenligning")

    log("\n" + "-" * 70)
    log("Oppsummering avvik, fetch_km_72_endelig (manuell vs kyst-straaler):")
    for spot_id, mean_abs, worst_delta, worst_label, n_skipped in sorted(all_mean_abs, key=lambda x: -x[1]):
        log(f"  {spot_id:<16} gj.snitt |avvik| {mean_abs:6.1f} km   "
            f"storst {worst_delta:+7.1f} km ({worst_label})   {n_skipped}/16 hoppet over")

    log("\n" + "-" * 70)
    log("Oppsummering avvik, fetch_km_72_kjegle (manuell vs kjeglekasting - den eneste sammenligningen som betyr noe):")
    for spot_id, mean_abs, worst_delta, worst_label, n_skipped in sorted(all_mean_abs_kjegle, key=lambda x: -x[1]):
        log(f"  {spot_id:<16} gj.snitt |avvik| {mean_abs:6.1f} km   "
            f"storst {worst_delta:+7.1f} km ({worst_label})   {n_skipped}/16 hoppet over")

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
