#!/usr/bin/env python3
"""
Engangsdiagnose (ordre 2026-09-03, avsluttende sjekk foer backtest-
runden lukkes): duger standardmodellen (EWAM ~5 km/0,05 grader, GWAM
~25-28 km/0,25 grader) bedre enn ERA5-Ocean (~28 km/0,25 grader, viste
seg upaalitelig for lokal boelgehoyde her - se backtest_sessions.py sin
docstring/rapport til bruker) for de TRE NYESTE oktene?

  2025-08-05  jomfruland_ost  kvalitet 4  lunsj
  2025-10-03  slagen          kvalitet 4  ettermiddag
  2026-04-05  slagen          kvalitet 5  ettermiddag

Disse tre (og KUN disse) er etter des. 2023 - standardmodellen (EWAM/
GWAM) har ingen historikk foer det, bekreftet med probe-marine-
archive.yml. De aatte eldre oktene (2018-2019) kan ALDRI faa denne
sjekken - ERA5-Ocean er eneste kilde med data der.

For hver okt: hent boelger fra SPOTTENS EGET punkt (samme punkt
backtest_sessions.py sin _spot_wave_point() bruker - offshore_point for
klasse A/B, gate for klasse C) med TRE separate Open-Meteo-kall -
models=ewam, models=gwam, models=era5_ocean - paa den UTC-timen som ble
valgt for regional_wp-rapporten (rapport til bruker, forrige runde) -
"samme time" brukeren ba om aa sammenligne. Rapporterer per modell:
gridcellen Open-Meteo faktisk brukte (toppniva i svaret), avstand til
spottens eget punkt, og Hs/Tp den timen. "Modellen i bruk" fastslaas
EMPIRISK, samme regel som sources.py sin openmeteo_waves() bruker i
PRODUKSJON (ikke i denne backtesten): EWAM foretrukket der den har ekte
(ikke-null) data, GWAM som reserve.

Er EWAM sin gridcelle innenfor 10 km av spotten (brukerens grense):
kjor EKSISTERENDE spot-fysikk (agent.evaluate_class_ab/evaluate_class_c/
score_hour, KODE UENDRET) paa EWAM-boelgedataene for den timen, og
rapporter hs_eff/tp_eff/stars/svakeste ledd. Dette er IKKE en
gjeninnforing av den retirerte ERA5-baserte pipelinen i
backtest_sessions.py (den er borte med god grunn, se dens docstring) -
et eget, frittstaaende engangsscript, samme prinsipp som
diagnose_saltstein_20181218.py (kjort en gang, resultatet rapportert,
scriptet fjernet etterpaa).

IKKE juster noen terskler her - ren diagnose av datakildens kvalitet.
"""

import datetime as dt
import math

import agent as A
import backtest_sessions as B
import geo_utils as G

# (dato, spot_id, UTC-time valgt i forrige regional-energi-kjoring - se
# rapport til bruker, tabellen "Regional energi - raa ERA5")
SESSIONS = [
    ("2025-08-05", "jomfruland_ost", "2025-08-05T12:00:00+00:00"),
    ("2025-10-03", "slagen", "2025-10-03T19:00:00+00:00"),
    ("2026-04-05", "slagen", "2026-04-05T16:00:00+00:00"),
]

NEAR_ENOUGH_KM = 10.0


def dist_km(lat1, lon1, lat2, lon2):
    x1, y1 = G.to_utm(lon1, lat1)
    x2, y2 = G.to_utm(lon2, lat2)
    return math.hypot(x2 - x1, y2 - y1) / 1000.0


def fetch_model_day(lat, lon, date, model):
    """EN dags boelgedata for EN modell (ewam/gwam/era5_ocean).
    Returnerer (grid_lat, grid_lon, {ts: {hs, tp}})."""
    data = B._get_json(B.WAVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": date, "end_date": date,
        "hourly": "wave_height,wave_period",
        "timezone": "UTC",
        "models": model,
    })
    grid = (data.get("latitude"), data.get("longitude"))
    h = data["hourly"]
    out = {}
    for i, t in enumerate(h["time"]):
        out[B._to_iso(t)] = {"hs": B._at(h.get("wave_height"), i), "tp": B._at(h.get("wave_period"), i)}
    return grid, out


def report_models_per_session():
    spots, _ = A.load_spots()
    by_id = {s["id"]: s for s in spots}
    results = []

    print(f"\n{'='*100}\nSTANDARDMODELL (EWAM/GWAM) MOT ERA5-OCEAN - DE TRE NYESTE OKTENE\n{'='*100}")
    for date, spot_id, target_ts in SESSIONS:
        spot = by_id[spot_id]
        lat, lon = B._spot_wave_point(spot)
        print(f"\n{date}  {spot_id}  (spurt punkt {lat},{lon}, time {target_ts})")
        row = {"dato": date, "spot": spot_id, "punkt": (lat, lon), "valgt_tid_utc": target_ts}
        for model in ("ewam", "gwam", "era5_ocean"):
            grid, series = fetch_model_day(lat, lon, date, model)
            d = dist_km(lat, lon, grid[0], grid[1]) if grid[0] is not None else None
            hour = series.get(target_ts, {})
            row[model] = {"grid": grid, "dist_km": d, "hs": hour.get("hs"), "tp": hour.get("tp")}
            print(f"    {model:<11} gridcelle {grid[0]},{grid[1]}  "
                  f"avstand {d:.1f} km  Hs={hour.get('hs')}  Tp={hour.get('tp')}"
                  if d is not None else
                  f"    {model:<11} INGEN gridrespons")

        row["modell_i_bruk"] = ("ewam" if row["ewam"]["hs"] is not None else
                                 "gwam" if row["gwam"]["hs"] is not None else None)
        print(f"    -> modell i bruk (EWAM foretrukket der den har data, samme regel "
              f"som sources.py sin openmeteo_waves()): {row['modell_i_bruk']}")
        results.append(row)

    print(f"\n{'-'*100}\nGRIDCELLER - ULIKE FOR DE TRE OKTENE?\n{'-'*100}")
    ewam_grids = {f"{r['dato']} {r['spot']}": r["ewam"]["grid"] for r in results}
    for k, g in ewam_grids.items():
        print(f"  {k}: EWAM-gridcelle {g}")
    n_distinct = len(set(ewam_grids.values()))
    print(f"  -> {n_distinct}/{len(ewam_grids)} distinkte EWAM-gridceller blant de tre oktene.")

    return results


def run_spot_physics(row, by_id):
    """Kjorer EKSISTERENDE agent.py-fysikk (uendret) paa EWAM-
    boelgedata for aakkurat den UTC-timen som allerede ble valgt - se
    modulens docstring. Ingen ny scoring-logikk her."""
    spot = by_id[row["spot"]]
    date = row["dato"]
    target_ts = row["valgt_tid_utc"]
    day_before = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    lat, lon = B._spot_wave_point(spot)

    wind = B.fetch_era5_wind(spot["lat"], spot["lon"], day_before, date)

    data = B._get_json(B.WAVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": day_before, "end_date": date,
        "hourly": "wave_height,wave_direction,wave_period",
        "timezone": "UTC",
        "models": "ewam",
    })
    h = data["hourly"]
    waves = {}
    for i, t in enumerate(h["time"]):
        ts = B._to_iso(t)
        waves[ts] = {
            "hs": B._at(h.get("wave_height"), i),
            "tp": B._at(h.get("wave_period"), i),
            "wave_from_direction": B._at(h.get("wave_direction"), i),
        }

    if waves.get(target_ts, {}).get("hs") is None:
        print(f"  {date} {row['spot']}: EWAM mangler data paa {target_ts} sjol om gridcellen "
              f"er naer nok - ingen fysikk kjort.")
        return

    times = sorted(set(wind) & set(waves))
    if spot["klasse"] == "C":
        computed = dict(A.evaluate_class_c(spot, times, wind, waves))
    else:
        computed = dict(A.evaluate_class_ab(spot, times, wind, waves))

    if target_ts not in computed:
        print(f"  {date} {row['spot']}: ingen beregnet time for {target_ts} - ingen fysikk kjort.")
        return

    result = A.score_hour(spot, target_ts, wind.get(target_ts, {}), waves.get(target_ts, {}),
                           None, computed[target_ts], lead_h=0.0)
    weakest = min(
        [("storrelse", result["q_size"]), ("periode", result["q_period"]),
         ("vind", result["q_wind"]), ("vannstand", result["q_water"])],
        key=lambda x: x[1],
    )
    print(f"  {date} {row['spot']} ({target_ts}): hs_eff={result.get('hs_eff')} "
          f"tp_eff={result.get('tp_eff')} stars={result.get('stars')} "
          f"p_surf={result.get('p_surf')} score={result.get('score')} "
          f"svakeste ledd={weakest[0]} ({weakest[1]:.2f})")


def main():
    results = report_models_per_session()

    print(f"\n{'='*100}\nSPOT-FYSIKK PAA EWAM (kun der gridcellen er innenfor {NEAR_ENOUGH_KM} km)\n{'='*100}")
    spots, _ = A.load_spots()
    by_id = {s["id"]: s for s in spots}
    any_near = False
    for row in results:
        d = row["ewam"]["dist_km"]
        if d is not None and d <= NEAR_ENOUGH_KM:
            any_near = True
            run_spot_physics(row, by_id)
        else:
            print(f"  {row['dato']} {row['spot']}: EWAM-gridcelle {d} km unna, over grensa - "
                  f"ingen fysikk kjort.")
    if not any_near:
        print("  Ingen av de tre oktene hadde EWAM-gridcelle innenfor grensa - ingen fysikk kjort.")

    print(f"\n{'='*100}\nHTTP: {B._http_stats['n_calls']} kall, {B._http_stats['n_retried']} med retry.\n{'='*100}")


if __name__ == "__main__":
    main()
