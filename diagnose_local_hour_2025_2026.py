#!/usr/bin/env python3
"""
Engangsdiagnose (ordre 2026-09-03, retter timevalget fra forrige runde):
den forrige EWAM/GWAM-sjekken brukte UTC-timen valgt for regional_wp ved
Saltsteins offshore_point (hoyest regional energi i oktens tidsvindu) -
ikke oktens EGEN spot sin beste time. Bruker peker paa en reell fysisk
grunn til at det kan bomme: paa en Slagen-dag topper Saltstein typisk
FLERE TIMER FOR Slagen - Saltstein tar selve kulingen, Slagen den
paafolgende etterdonningen naar vinden har dreid. Slagen sin lave
stjernescore (1,3 mot observert kvalitet 4) kan derfor vaere et
timevalg-artefakt, ikke et kalibreringsfunn.

Retting: for hver av de tre oktene (2025-08-05 jomfruland_ost/lunsj,
2025-10-03 slagen/ettermiddag, 2026-04-05 slagen/ettermiddag) - hent
EWAM-boelger fra SPOTTENS EGET punkt (samme som forrige runde), kjor
EKSISTERENDE spot-fysikk (agent.evaluate_class_ab/evaluate_class_c/
score_hour, kode UENDRET) for HVER time i doegnet, plukk timen med
HOYEST SCORE innenfor oktens eget tidsvindu (samme lo/hi-logikk som
backtest_sessions.py sin gamle pick_target_hour() brukte - lokalt per
spot denne gangen, ikke regionalt fra Saltstein), og rapporter BAADE
den valgte timen OG hele vinduets serie (hs_eff/tp_eff/stars/svakeste
ledd/score per time) - saa brukeren selv kan se om en bedre time
finnes.

Frittstaaende engangsscript (samme muster som
diagnose_saltstein_20181218.py/diagnose_standard_model_2025_2026.py) -
IKKE en gjeninnforing av den retirerte ERA5-pipelinen i
backtest_sessions.py. IKKE juster noen terskler her.
"""

import datetime as dt

import agent as A
import backtest_sessions as B

# (dato, spot_id, lo_hour, hi_hour) - vinduene fra sessions_historisk.csv
# sin 'tid'-kolonne via backtest_sessions.TIME_WINDOWS
SESSIONS = [
    ("2025-08-05", "jomfruland_ost", 11, 14),   # lunsj
    ("2025-10-03", "slagen", 14, 19),           # ettermiddag
    ("2026-04-05", "slagen", 14, 19),           # ettermiddag
]


def weakest_led(h):
    """Samme min()-logikk som agent.explain()/den tidligere
    backtest_sessions._weakest_led() brukte."""
    return min(
        [("storrelse", h["q_size"]), ("periode", h["q_period"]),
         ("vind", h["q_wind"]), ("vannstand", h["q_water"])],
        key=lambda x: x[1],
    )


def build_ewam_hours(spot, date):
    """Samme struktur som den gamle build_hours_window() bygget, men
    boelger fra EWAM (models=ewam) i stedet for era5_ocean - se
    modulens docstring. 48-timers vindu (doegnet FOR + doegnet selv)
    for evaluate_class_c() sin lookback, samme som foer."""
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

    times = sorted(set(wind) & set(waves))
    if spot["klasse"] == "C":
        computed = A.evaluate_class_c(spot, times, wind, waves)
    else:
        computed = A.evaluate_class_ab(spot, times, wind, waves)

    hours = []
    for ts, c in computed:
        hours.append(A.score_hour(spot, ts, wind.get(ts, {}), waves.get(ts, {}), None, c, lead_h=0.0))
    return hours


def main():
    spots, _ = A.load_spots()
    by_id = {s["id"]: s for s in spots}

    print(f"\n{'='*112}\nLOKALT TIMEVALG PAA EWAM - HOYEST SCORE I VINDUET, IKKE REGIONAL ENERGI\n{'='*112}")

    for date, spot_id, lo, hi in SESSIONS:
        spot = by_id[spot_id]
        hours = build_ewam_hours(spot, date)
        window = [h for h in hours if h["time"][:10] == date and lo <= int(h["time"][11:13]) <= hi]

        print(f"\n{'-'*112}\n{date}  {spot_id}  vindu [{lo:02d}:00-{hi:02d}:00 UTC]\n{'-'*112}")
        if not window:
            print("  INGEN timer i vinduet - ingen data.")
            continue

        header = f"{'time (UTC)':<22}{'score':>8}{'stars':>7}{'p_surf':>8}{'hs_eff':>8}{'tp_eff':>8}  {'svakeste ledd':<20}"
        print(header)
        for h in sorted(window, key=lambda h: h["time"]):
            led, led_v = weakest_led(h)
            print(f"{h['time']:<22}{h['score']:>8.1f}{str(h.get('stars')):>7}"
                  f"{h.get('p_surf', 0):>8.0f}{h.get('hs_eff'):>8}{h.get('tp_eff'):>8}  "
                  f"{led+f' ({led_v:.2f})':<20}")

        best = max(window, key=lambda h: h["score"])
        led, led_v = weakest_led(best)
        print(f"\n  BESTE TIME I VINDUET: {best['time']}  score={best['score']:.1f}  "
              f"stars={best.get('stars')}  p_surf={best.get('p_surf'):.0f}  "
              f"hs_eff={best.get('hs_eff')}  tp_eff={best.get('tp_eff')}  "
              f"svakeste ledd={led} ({led_v:.2f})")

    print(f"\n{'='*112}\nHTTP: {B._http_stats['n_calls']} kall, {B._http_stats['n_retried']} med retry.\n{'='*112}")


if __name__ == "__main__":
    main()
