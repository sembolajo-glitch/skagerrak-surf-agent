#!/usr/bin/env python3
"""
Reanalyse mot faktiske okter (sessions_historisk.csv).

VIKTIG FORBEHOLD, les foer du tolker noe: dette er REANALYSE, ikke
PROGNOSE. Vi bruker ERA5/ERA5-Ocean sitt beste etterpaaklokre estimat
for hva vaeret/sjoen FAKTISK var den dagen, ikke det en prognose ville
sagt paa forhaand. Vi tester derfor om FYSIKKEN (score_hour(),
size_quality(), regional_wp-porten osv.) stemmer mot faktiske okter -
IKKE om varselet ville truffet (det trenger et helt annet datasett:
faktiske MET/Open-Meteo-PROGNOSER slik de saa ut PAA FORHAAND, som
ikke finnes arkivert tilbake til 2018). lead_h settes derfor til 0.0
for alle timer her - riktig for en reanalyse, meningslost for en
prognose (se build_hours_window()).

Enda et forbehold: alle elleve oktene i datasettet er POSITIVE (noen
brukte, kvalitet >= 2 av 5) - ingen bomturer. Denne testen kan derfor
KUN avsloere for HOYE terskler (dager som burde scoret, men fikk
naer-null). Den kan IKKE si noe om for LAVE terskler (dager som burde
vaert null, men scoret hoyt) - det datasettet finnes ikke her.

Datakilder (se rapport til bruker, samtalene 2026-09-02):
  - Vind: Open-Meteo Historical Weather API (ERA5-reanalyse,
    archive-api.open-meteo.com/v1/archive), dekker 1940- og fremover.
  - Bolger: Open-Meteo Marine API i historisk modus
    (marine-api.open-meteo.com/v1/marine, start_date/end_date),
    models=era5_ocean. Standardmodellen (EWAM/GWAM, samme familie som
    agent.py bruker i produksjon via sources.openmeteo_waves()) har
    IKKE historikk foer desember 2023 (bekreftet med
    .github/workflows/probe-marine-archive.yml) - ERA5 brukes derfor
    for ALLE elleve oktene her, ikke bare de fra foer 2023, for AA FAA
    ÉN konsistent kilde i stedet for en blanding.

Kjent skjevhet mellom modellene (samme probe, 2023-06-01 og
2025-08-05): ERA5 ligger 50-130 % HOYERE i Hs enn standardmodellen paa
overlappende datoer. spots.yaml sine terskler (min_hs, regional_wp_min/
max osv.) er kalibrert mot surf-forecast sitt tall, som antas naermere
standardmodellen (samme WW3-familie som EWAM/GWAM) enn ERA5-Ocean. Raa
ERA5 rett inn i eksisterende scoring ville derfor systematisk
OVERVURDERE. quantify_bias() under maaler skjevheten empirisk FOR noe
annet kjores (kalt forst i main()), og backtest_all() kjores TO ganger
- raa ERA5 og skjevhetskorrigert (delt paa median-forholdet) - begge
rapportert, se run_report().

IKKE NY FYSIKK: all scoring gaar via agent.py sin evaluate_class_ab()/
evaluate_class_c()/score_hour()/explain() UENDRET. Dette skriptet
bygger bare wind/waves-dict-strukturene (samme form gather() selv
produserer, se sources.py) fra ERA5 i stedet for sanntidskildene, og
kjorer eksisterende scoring paa dem.

Kjores manuelt (ikke i noen workflow, ikke koblet inn i forecast.yml).
Dette miljoet hadde ingen utgaaende nettverkstilgang til Open-Meteo i
det hele tatt da dette ble skrevet - se .github/workflows/ for
motstykket som faktisk KAN naa nettet (ab-test.yml, probe-marine-
archive.yml). Kjor dette skriptet et sted med nettverkstilgang.

    python backtest_sessions.py
    python backtest_sessions.py --bias-sample-n 30 --seed 20260902
    python backtest_sessions.py --skip-bias-quantification --bias-hs 1.9 --bias-tp 1.1
"""

import argparse
import csv
import datetime as dt
import json
import pathlib
import random
import statistics as st
import sys

import requests

import agent as A
import physics as P

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "out"

WIND_URL = "https://archive-api.open-meteo.com/v1/archive"
WAVE_URL = "https://marine-api.open-meteo.com/v1/marine"

# Vage tidspunkt tolkes som vinduer (hel-timer, begge ender inkludert) -
# "de surfet sannsynligvis naar det var best", se pick_target_hour().
# Presise klokkeslett ("HH:MM") i CSV-en tolkes IKKE som et vindu - de
# ER observasjonen, se parse_time_window().
TIME_WINDOWS = {
    "morgen": (6, 10),
    "lunsj": (11, 14),
    "ettermiddag": (14, 19),
}

# ETT referansepunkt for skjevhetsmaalingen (Saltsteins offshore_point,
# samme punkt regional_wp alltid regnes fra) - IKKE per-spot. Modell-
# skjevheten antas regional (samme to modeller, samme del av Skagerrak),
# ikke spot-spesifikk. Eksplisitt forenkling - se quantify_bias().
BIAS_REFERENCE_ID = "saltstein"


# ------------------------------------------------------------- tidsvindu


def parse_time_window(tid):
    """
    Tolk 'tid'-kolonnen i sessions_historisk.csv.

    Returnerer (lo_hour, hi_hour, exact). exact=True betyr lo == hi og
    at dette IKKE skal "beste time i vinduet"-velges - det presise
    klokkeslettet ER observasjonen. exact=False for de tre vage
    kategoriene (TIME_WINDOWS) - der velges beste scorede time i
    vinduet, se pick_target_hour().
    """
    t = tid.strip().lower()
    if t in TIME_WINDOWS:
        lo, hi = TIME_WINDOWS[t]
        return lo, hi, False
    if ":" in t:
        hh = int(t.split(":")[0])
        if not 0 <= hh <= 23:
            raise ValueError(f"ugyldig klokkeslett i sessions-CSV: {tid!r}")
        return hh, hh, True
    raise ValueError(f"ukjent 'tid'-verdi i sessions-CSV: {tid!r} "
                      f"(vent en av {list(TIME_WINDOWS)} eller HH:MM)")


# --------------------------------------------------------------- henting


def _to_iso(t):
    """Open-Meteo sine tidsstempel ('2023-06-01T14:00', timezone=UTC i
    forespoerselen) -> samme normaliserte iso-form som sources.py sin
    _iso() produserer, saa wind/waves-dict-noklene stemmer overens."""
    d = dt.datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()


def _at(arr, i):
    return arr[i] if arr and i < len(arr) else None


def _get_json(url, params, timeout=30):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_era5_wind(lat, lon, date_from, date_to):
    """ERA5 vind - samme dict-form som sources.met_wind() (delmengde:
    kun feltene score_hour()/build_local_sea() faktisk leser)."""
    data = _get_json(WIND_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": date_from, "end_date": date_to,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "timezone": "UTC",
    })
    h = data["hourly"]
    out = {}
    for i, t in enumerate(h["time"]):
        out[_to_iso(t)] = {
            "wind_speed": _at(h.get("wind_speed_10m"), i),
            "wind_from_direction": _at(h.get("wind_direction_10m"), i),
        }
    return out


def fetch_era5_waves(lat, lon, date_from, date_to, model="era5_ocean"):
    """
    Bolger fra Open-Meteo Marine API i historisk modus - samme dict-form
    som sources.met_waves()/openmeteo_waves() (delmengden score_hour()/
    evaluate_class_ab()/evaluate_class_c() faktisk leser: hs, tp,
    wave_from_direction).

    model=None ber om standardmodellen (EWAM/GWAM, "best_match") - kun
    brukt i quantify_bias(), IKKE i selve backtesten (som alltid bruker
    era5_ocean, se modulens docstring for hvorfor).
    """
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": date_from, "end_date": date_to,
        "hourly": "wave_height,wave_direction,wave_period",
        "timezone": "UTC",
    }
    if model:
        params["models"] = model
    data = _get_json(WAVE_URL, params)
    h = data["hourly"]
    out = {}
    for i, t in enumerate(h["time"]):
        out[_to_iso(t)] = {
            "hs": _at(h.get("wave_height"), i),
            "tp": _at(h.get("wave_period"), i),
            "wave_from_direction": _at(h.get("wave_direction"), i),
        }
    return out


# -------------------------------------------------------- modellskjevhet


def sample_bias_dates(session_dates_after_2023, n=30, start="2023-12-01",
                       end=None, seed=20260902):
    """
    De faktiske oktedatoene fra 2023- (garantert med) pluss n tilfeldig
    trukne doegn i samme periode (fast seed - reproduserbart, samme
    konvensjon som ensemble.py). end default: 5 dager for i dag, for
    aa ikke treffe datoer Open-Meteo ennaa ikke har reanalysert ferdig.
    """
    end = end or (dt.date.today() - dt.timedelta(days=5)).isoformat()
    start_d, end_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    span = (end_d - start_d).days
    if span < 1:
        raise ValueError(f"ugyldig periode for skjevhetsutvalg: {start}..{end}")

    rng = random.Random(seed)
    picked = set(session_dates_after_2023)
    while len(picked) < len(session_dates_after_2023) + n:
        d = start_d + dt.timedelta(days=rng.randrange(span + 1))
        picked.add(d.isoformat())
    return sorted(picked)


def quantify_bias(lat, lon, dates):
    """
    Hent BEGGE modellene (era5_ocean og standardmodellen) for hver dato
    i `dates`, regn forholdet ERA5/standardmodell time for time for Hs
    og Tp separat. Returnerer median, kvartiler og min/max for begge,
    pluss de raa parvise radene (til --dump-bias-csv).

    IKKE et scorekomponent - rent maalt, brukes til aa lage
    korreksjonsfaktoren backtest_all(..., bias=...) deler ERA5-verdier
    paa. Kjores FOR resten av backtesten (se modulens docstring/
    run_report()) - stopper (raise) hvis IKKE NOK par ble funnet, i
    stedet for aa stille returnere en tallverdi fra et for lite utvalg.
    """
    pairs = {"hs": [], "tp": []}
    per_date = []
    for date in dates:
        era5 = fetch_era5_waves(lat, lon, date, date, model="era5_ocean")
        std = fetch_era5_waves(lat, lon, date, date, model=None)
        n_before = len(pairs["hs"])
        for ts in sorted(set(era5) & set(std)):
            e, s = era5[ts], std[ts]
            if e.get("hs") is not None and s.get("hs") not in (None, 0):
                pairs["hs"].append((date, ts, e["hs"], s["hs"], e["hs"] / s["hs"]))
            if e.get("tp") is not None and s.get("tp") not in (None, 0):
                pairs["tp"].append((date, ts, e["tp"], s["tp"], e["tp"] / s["tp"]))
        per_date.append((date, len(pairs["hs"]) - n_before))

    if len(pairs["hs"]) < 10:
        raise RuntimeError(
            f"Bare {len(pairs['hs'])} Hs-par mellom era5_ocean og standardmodellen "
            f"funnet over {len(dates)} datoer - for lite til aa stole paa en median. "
            f"Sjekk om standardmodellen faktisk har data i perioden (se "
            f"probe-marine-archive.yml)."
        )

    def summarize(vals):
        ratios = [v[4] for v in vals]
        ratios.sort()
        q1, med, q3 = st.quantiles(ratios, n=4)[0], st.median(ratios), st.quantiles(ratios, n=4)[2]
        return {
            "n": len(ratios), "median": round(med, 3),
            "p25": round(q1, 3), "p75": round(q3, 3),
            "min": round(ratios[0], 3), "max": round(ratios[-1], 3),
        }

    return {
        "hs": summarize(pairs["hs"]),
        "tp": summarize(pairs["tp"]),
        "n_dates_with_data": sum(1 for _, n in per_date if n > 0),
        "n_dates_requested": len(dates),
        "raw_pairs": pairs,
    }


def _apply_bias(waves, bias):
    """Ny dict, samme form - Hs/Tp DELT paa bias-forholdet (skalerer
    raa ERA5 NED mot hva standardmodellen antas ville vist). None-
    verdier og manglende felt passerer uendret."""
    out = {}
    for ts, w in waves.items():
        w2 = dict(w)
        if w2.get("hs") is not None:
            w2["hs"] = w2["hs"] / bias["hs"]
        if w2.get("tp") is not None:
            w2["tp"] = w2["tp"] / bias["tp"]
        out[ts] = w2
    return out


# -------------------------------------------------------------- scoring


def build_hours_window(spot, day, saltstein_spot, bias=None):
    """
    Hent vind og bolger for doegnet FOR `day` og `day` selv (48 t -
    lookback build_local_sea()/evaluate_class_c() trenger, se
    agent.py), bygg samme hours-struktur run() selv bygger, og kjor
    EKSISTERENDE scoring (evaluate_class_ab/evaluate_class_c +
    score_hour) uendret.

    lead_h=0.0 for ALLE timer - dette er reanalyse (vi VET hva sjoen
    var), ikke prognose. ensemble.py sin usikkerhet vokser med lead_h
    for aa modellere at en PROGNOSE blir mindre paalitelig lenger fram
    - det gjelder ikke her, og aa la lead_h vaere den store, VARIERENDE
    verdien den ville hatt i en ekte kjoring (tid fra "naa" til okten,
    ofte AAR) ville blaast opp usikkerheten meningslost og senket
    p_surf/stars uten noen fysisk grunn.

    bias: None (raa ERA5) eller dict {"hs": faktor, "tp": faktor} fra
    quantify_bias() - se modulens docstring.
    """
    day_before = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()

    wind_pt = (spot["lat"], spot["lon"])
    if spot["klasse"] == "C":
        wave_pt = (spot["gate"]["lat"], spot["gate"]["lon"])
    else:
        wave_pt = tuple(spot["offshore_point"])

    wind = fetch_era5_wind(*wind_pt, day_before, day)
    waves = fetch_era5_waves(*wave_pt, day_before, day, model="era5_ocean")

    sal_pt = tuple(saltstein_spot["offshore_point"])
    sal_waves = waves if sal_pt == wave_pt else \
        fetch_era5_waves(*sal_pt, day_before, day, model="era5_ocean")

    if bias:
        waves = _apply_bias(waves, bias)
        sal_waves = _apply_bias(sal_waves, bias)

    regional_wp_by_time, regional_hs_by_time, regional_tp_by_time = {}, {}, {}
    for ts, w in sal_waves.items():
        hs, tp = w.get("hs"), w.get("tp")
        if hs is not None and tp is not None:
            regional_wp_by_time[ts] = round(P.wave_power(hs, tp), 1)
            regional_hs_by_time[ts] = hs
            regional_tp_by_time[ts] = tp

    times = sorted(set(wind) & set(waves))
    if spot["klasse"] == "C":
        computed = A.evaluate_class_c(spot, times, wind, waves)
    else:
        computed = A.evaluate_class_ab(spot, times, wind, waves)

    hours = []
    for ts, c in computed:
        hours.append(A.score_hour(
            spot, ts, wind.get(ts, {}), waves.get(ts, {}), None, c,
            lead_h=0.0,
            regional_wp=regional_wp_by_time.get(ts),
            regional_hs=regional_hs_by_time.get(ts),
            regional_tp=regional_tp_by_time.get(ts),
        ))
    return hours


def pick_target_hour(hours, day, lo_hour, hi_hour):
    """Beste (hoyest score()) time paa `day` innenfor [lo_hour, hi_hour]
    - for et EXACT klokkeslett (parse_time_window) er lo==hi, saa dette
    reduserer til aa plukke akkurat den ene timen. None hvis ingen
    kandidattime finnes i det hele tatt (manglende data den dagen)."""
    candidates = [
        h for h in hours
        if h["time"][:10] == day and lo_hour <= int(h["time"][11:13]) <= hi_hour
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h["score"])


def backtest_session(session, spots_by_id, bias=None):
    spot = spots_by_id.get(session["spot"])
    if spot is None:
        return {**session, "feil": f"ukjent spot-id '{session['spot']}'"}
    try:
        lo, hi, exact = parse_time_window(session["tid"])
    except ValueError as exc:
        return {**session, "feil": str(exc)}

    try:
        hours = build_hours_window(spot, session["dato"], spots_by_id[BIAS_REFERENCE_ID], bias)
    except requests.RequestException as exc:
        return {**session, "feil": f"henting feilet: {exc}"}

    target = pick_target_hour(hours, session["dato"], lo, hi)
    if target is None:
        return {**session, "feil": "ingen data for gitt dato/vindu (Open-Meteo svarte, men uten treff)"}

    return {**session, "exact_time": exact, "hour": target}


def backtest_all(sessions, spots_by_id, bias=None):
    return [backtest_session(s, spots_by_id, bias) for s in sessions]


# --------------------------------------------------------------- rapport


def load_sessions(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _weakest_led(h):
    """Gjenbruker EKSAKT samme min()-logikk som agent.explain() - se
    den for den fulle forklaringssetningen. Egen liten wrapper her
    fordi vi bare vil ha (navn, verdi), ikke hele setningen, i
    tabellraden."""
    return min(
        [("storrelse", h["q_size"]), ("periode", h["q_period"]),
         ("vind", h["q_wind"]), ("vannstand", h["q_water"])],
        key=lambda x: x[1],
    )


def print_bias_report(bias, label):
    print(f"\n{'='*78}\nMODELLSKJEVHET: ERA5-Ocean / standardmodell (EWAM/GWAM) - {label}\n{'='*78}")
    print(f"  datoer med begge modeller: {bias['n_dates_with_data']}/{bias['n_dates_requested']}")
    for var in ("hs", "tp"):
        b = bias[var]
        print(f"  {var.upper():<3} forhold ERA5/standard: median {b['median']}  "
              f"(kvartiler {b['p25']}-{b['p75']}, spenn {b['min']}-{b['max']}, n={b['n']})")


def print_session_table(results, title):
    print(f"\n{'='*100}\n{title}\n{'='*100}")
    header = (f"{'dato':<11}{'spot':<16}{'kval':>5}  {'stars':>6}{'p_surf':>8}"
              f"{'regional_wp':>13}  {'port':<10}{'svakeste ledd':<20}{'score':>7}")
    print(header)
    n_zero = 0
    for r in results:
        if "feil" in r:
            print(f"{r['dato']:<11}{r['spot']:<16}{r['kvalitet']:>5}  FEIL: {r['feil']}")
            continue
        h = r["hour"]
        score = h["score"]
        if score == 0.0:
            n_zero += 1
        port = ("bypasset" if h.get("regional_gate_bypassed") else
                "STENGT" if h.get("regional_gate_closed") else "aapen")
        led, led_v = _weakest_led(h)
        print(f"{r['dato']:<11}{r['spot']:<16}{r['kvalitet']:>5}  "
              f"{str(h.get('stars')):>6}{h.get('p_surf'):>8.0f}"
              f"{str(h.get('regional_wp')):>13}  {port:<10}"
              f"{led+f' ({led_v:.2f})':<20}{score:>7.1f}")
    n_total = sum(1 for r in results if "feil" not in r)
    print(f"\n  {n_zero}/{n_total} okter fikk score 0.0 (porten stengt og/eller under min_hs) - "
          f"dager modellen ville vaert TAUS om, selv om de faktisk ble surfet.")
    return n_zero, n_total


def run_report(sessions_path, bias_sample_n, seed, skip_bias, manual_bias):
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    sessions = load_sessions(sessions_path)
    print(f"{len(sessions)} okter lest fra {sessions_path}")

    session_dates_after_2023 = sorted({
        s["dato"] for s in sessions
        if dt.date.fromisoformat(s["dato"]) >= dt.date(2023, 12, 1)
    })

    if skip_bias:
        if not manual_bias:
            sys.exit("--skip-bias-quantification krever --bias-hs og --bias-tp")
        bias = {"hs": {"median": manual_bias[0]}, "tp": {"median": manual_bias[1]}}
        print(f"\nHopper over maaling - bruker oppgitt skjevhet: Hs x{manual_bias[0]}, Tp x{manual_bias[1]}")
    else:
        dates = sample_bias_dates(session_dates_after_2023, n=bias_sample_n, seed=seed)
        sal = spots_by_id[BIAS_REFERENCE_ID]
        lat, lon = sal["offshore_point"]
        bias = quantify_bias(lat, lon, dates)
        print_bias_report(bias, f"{bias['n_dates_requested']} datoer, referansepunkt {BIAS_REFERENCE_ID}")

    bias_factor = {"hs": bias["hs"]["median"], "tp": bias["tp"]["median"]}

    raw = backtest_all(sessions, spots_by_id, bias=None)
    corrected = backtest_all(sessions, spots_by_id, bias=bias_factor)

    n_zero_raw, n_total = print_session_table(raw, "RAA ERA5 (ukorrigert)")
    n_zero_corr, _ = print_session_table(corrected, f"SKJEVHETSKORRIGERT (Hs/{bias_factor['hs']:.2f}, Tp/{bias_factor['tp']:.2f})")

    print(f"\n{'='*78}\nOPPSUMMERING\n{'='*78}")
    print(f"  Raa ERA5:            {n_zero_raw}/{n_total} okter fikk score 0.0")
    print(f"  Skjevhetskorrigert:  {n_zero_corr}/{n_total} okter fikk score 0.0")
    print(f"\n  Reanalyse, ikke prognose (se modulens docstring) - dette kan KUN vise")
    print(f"  for HOYE terskler (alle elleve oktene var positive). Ingen terskler er")
    print(f"  justert her.")

    OUT.mkdir(exist_ok=True)
    report_path = OUT / "backtest_report.json"
    report_path.write_text(json.dumps({
        "bias": {k: v for k, v in bias.items() if k != "raw_pairs"} if not skip_bias else bias,
        "raw": raw, "corrected": corrected,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n-> {report_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions-csv", default=str(ROOT / "sessions_historisk.csv"))
    ap.add_argument("--bias-sample-n", type=int, default=30,
                     help="antall tilfeldige doegn (i tillegg til okt-datoene fra 2023-) for skjevhetsmaalingen")
    ap.add_argument("--seed", type=int, default=20260902)
    ap.add_argument("--skip-bias-quantification", action="store_true",
                     help="ikke hent/maal skjevheten paa nytt - krever --bias-hs/--bias-tp")
    ap.add_argument("--bias-hs", type=float, default=None)
    ap.add_argument("--bias-tp", type=float, default=None)
    args = ap.parse_args()

    manual_bias = (args.bias_hs, args.bias_tp) if args.bias_hs and args.bias_tp else None
    run_report(args.sessions_csv, args.bias_sample_n, args.seed,
               args.skip_bias_quantification, manual_bias)


if __name__ == "__main__":
    main()
