#!/usr/bin/env python3
"""
Kjor selve scoring-kjeden (agent.py/physics.py/ensemble.py - IMPORTERT,
IKKE duplisert) mot ARKIVERTE Open-Meteo-data for en eller flere
historiske datoer.

HVORFOR OPEN-METEO, IKKE MET (les foer du tolker noe resultat herfra):
MET Locationforecast (sources.met_wind/met_waves) har INGEN historikk -
kun "hva sier prognosen naa". Dette skriptet bruker i stedet to
Open-Meteo-endepunkt som begge tar start_date/end_date:

  - Historical Weather API (archive-api.open-meteo.com/v1/archive) -
    vind, samme variabler (wind_speed_10m/wind_direction_10m) som
    backtest_sessions.py sin fetch_era5_wind() alt bruker der.
  - Marine API (marine-api.open-meteo.com/v1/marine) - boelger
    (wave_height/wave_period/wave_direction), UTEN models-parameter
    (standardmodellen/"best_match" - IKKE models=era5_ocean). Se
    backtest_sessions.py sin docstring for hvorfor era5_ocean sitt
    ~0,25-graders grid (~28 km) er for grovt til aa se lokale forskjeller
    mellom spots naer kysten her - akkurat det denne backtesten skal
    vise (Moelen odden sitt gamle vs. nye offshore_point). Standard-
    modellen har historikk fra desember 2023 (bekreftet i
    .github/workflows/probe-marine-archive.yml) - god margin for
    2026-09-05/06.

Resultatet er IKKE en MET-rekjoring. Hver utskrift/rapport sier det
eksplisitt - forveksle det aldri med "hva MET faktisk sa den dagen".

INGEN duplisert scoring: wind/wave-seriene formateres til NOYAKTIG samme
dict-form sources.py sine funksjoner allerede returnerer
({"wind_speed":, "wind_from_direction":} / {"hs":, "tp":,
"wave_from_direction":}), og sendes rett inn i agent.evaluate_class_ab()/
evaluate_class_c()/score_hour() - de faktiske spot-modellene endres ikke
her. Punktkonvensjonen (hvilket punkt vind/boelger hentes fra per klasse)
er agent.wave_wind_points() - importert, ikke gjenskrevet.

CACHING: hver dato caches raatt (IKKE ferdigscoret - scoring kjores paa
nytt fra raadataene hver gang, saa en fremtidig endring i scoring-koden
aldri gir stille utdaterte resultat) under
tests/fixtures/backtest/<dato>.json. Finnes fila, hentes INGENTING paa
nytt (--refresh overstyrer) - gjor en gitt dato til en fast, offline-
reproduserbar referanse. Mangler fila EN bestemt (lat,lon)-noekkel som
en senere kjoring trenger (f.eks. et nytt spot lagt til), hentes KUN den
manglende noekkelen, resten av fila roeres ikke.

    python scripts/backtest.py 2026-09-05
    python scripts/backtest.py 2026-09-05 2026-09-06
    python scripts/backtest.py 2026-09-05 --spots saltstein,molen_odden
    python scripts/backtest.py 2026-09-05 --refresh
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent as A  # noqa: E402
import physics as P  # noqa: E402

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "backtest"

WIND_URL = "https://archive-api.open-meteo.com/v1/archive"
WAVE_URL = "https://marine-api.open-meteo.com/v1/marine"

DATAKILDE = (
    "Open-Meteo (IKKE MET) - archive-api.open-meteo.com/v1/archive (vind) + "
    "marine-api.open-meteo.com/v1/marine, standardmodell/best_match, "
    "IKKE models=era5_ocean (bolger). Arkiverte prognoser, ikke reanalyse - "
    "se scripts/backtest.py sin docstring."
)
# Kort tag brukt i HVER tabell/rapportoverskrift (se print_timetable() osv.) -
# DATAKILDE sin fulle forklaring skrives KUN en gang, forrest i main() sin
# utskrift. AA gjenta hele forklaringen i hver overskrift ville druknet selve
# tabellene i repetert tekst - men ordren ("si eksplisitt at det er Open-
# Meteo, ikke MET") skal likevel vaere synlig PAA HVER tabell, ikke bare en
# gang i toppen noen kan bla forbi.
KILDE_TAG = "Open-Meteo, IKKE MET"

HTTP_TIMEOUT_S = 60
RETRY_DELAYS_S = (2, 5, 15)

# Moelens offshore_point ble flyttet to ganger (se git-historikken paa
# spots.yaml): 0,71 km ute (58.970, 9.805, til 2026-09-06) -> 1,1 km ->
# dagens 4,0 km (58.94226, 9.78844). Dette er den FORRIGE, motbeviste
# plasseringen backtesten sammenligner mot - se rapport til bruker.
# Saltstein sitt offshore_point (58.930, 9.830) er IKKE i denne lista -
# det har ALDRI flyttet (sjekket mot git-historikken, ikke antatt) - se
# report_old_vs_new_point() for hvordan det rapporteres i stedet.
OLD_OFFSHORE_POINTS = {
    "molen_odden": (58.970, 9.805),
}


def log(*a):
    print(*a, file=sys.stderr)


# --------------------------------------------------------------- henting


def _get_json(url, params, timeout=HTTP_TIMEOUT_S):
    """GET -> .json(), inntil tre nye forsok ved forbigaaende feil - samme
    retry-kontrakt som backtest_sessions.py sin _get_json()."""
    last_exc = None
    for attempt, delay in enumerate((0.0,) + RETRY_DELAYS_S):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
            continue
        return r.json()
    raise last_exc


def _to_iso(t):
    d = dt.datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()


def _at(arr, i):
    return arr[i] if arr and i < len(arr) else None


def fetch_openmeteo_wind(lat, lon, date):
    """{iso_ts: {"wind_speed":, "wind_from_direction":}} for ETT doegn -
    samme dict-form som sources.met_wind()."""
    data = _get_json(WIND_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": date, "end_date": date,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "timezone": "UTC",
    })
    h = data.get("hourly") or {}
    out = {}
    for i, t in enumerate(h.get("time", [])):
        out[_to_iso(t)] = {
            "wind_speed": _at(h.get("wind_speed_10m"), i),
            "wind_from_direction": _at(h.get("wind_direction_10m"), i),
        }
    return out


def fetch_openmeteo_waves(lat, lon, date):
    """{iso_ts: {"hs":, "tp":, "wave_from_direction":}} for ETT doegn -
    samme dict-form (delmengde) som sources.met_waves()/openmeteo_waves().
    INGEN models-parameter - se modulens docstring for hvorfor
    (era5_ocean sitt grid er for grovt for dette formaalet)."""
    data = _get_json(WAVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": date, "end_date": date,
        "hourly": "wave_height,wave_direction,wave_period",
        "timezone": "UTC",
    })
    h = data.get("hourly") or {}
    out = {}
    for i, t in enumerate(h.get("time", [])):
        out[_to_iso(t)] = {
            "hs": _at(h.get("wave_height"), i),
            "tp": _at(h.get("wave_period"), i),
            "wave_from_direction": _at(h.get("wave_direction"), i),
        }
    return out


# ------------------------------------------------------------------ cache


def _point_key(lat, lon):
    return f"{float(lat):.5f},{float(lon):.5f}"


def fixture_path(date, fixtures_dir=FIXTURES_DIR):
    return Path(fixtures_dir) / f"{date}.json"


def load_fixture(date, fixtures_dir=FIXTURES_DIR):
    path = fixture_path(date, fixtures_dir)
    if not path.exists():
        return {"date": date, "source": DATAKILDE, "wind_by_point": {}, "wave_by_point": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("wind_by_point", {})
    data.setdefault("wave_by_point", {})
    return data


def save_fixture(cache, fixtures_dir=FIXTURES_DIR):
    path = fixture_path(cache["date"], fixtures_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def get_wind(cache, lat, lon, refresh=False):
    """Cache-eller-hent for vind ved (lat, lon) paa cache["date"]. Muterer
    `cache` in-place (kalleren avgjor om/naar den skal lagres til disk -
    se save_fixture())."""
    key = _point_key(lat, lon)
    if not refresh and key in cache["wind_by_point"]:
        return cache["wind_by_point"][key]
    series = fetch_openmeteo_wind(lat, lon, cache["date"])
    cache["wind_by_point"][key] = series
    return series


def get_waves(cache, lat, lon, refresh=False):
    key = _point_key(lat, lon)
    if not refresh and key in cache["wave_by_point"]:
        return cache["wave_by_point"][key]
    series = fetch_openmeteo_waves(lat, lon, cache["date"])
    cache["wave_by_point"][key] = series
    return series


def merge_caches(caches):
    """Slaa sammen flere per-dato-cacher (kronologisk) til EN serie per
    punkt, brukt til aa score en sammenhengende flerdogns-periode (se
    Saltstein sin holdbarhets-analyse i main()) UTEN aa blande cache-
    filene selv paa disk - hver dato beholder sin egen fil."""
    wind_by_point, wave_by_point = {}, {}
    for cache in caches:
        for key, series in cache["wind_by_point"].items():
            wind_by_point.setdefault(key, {}).update(series)
        for key, series in cache["wave_by_point"].items():
            wave_by_point.setdefault(key, {}).update(series)
    return {"wind_by_point": wind_by_point, "wave_by_point": wave_by_point}


# --------------------------------------------------------------- scoring


def regional_series(merged, saltstein_spot, refresh=False, cache_for_fetch=None):
    """regional_wp/hs/tp_by_time fra Saltsteins EGET offshore_point - samme
    ETT-tall-for-hele-regionen-konstruksjon som agent.run() gjor (se der),
    her fra den ALLEREDE hentede/cachede boelgeserien for det punktet.
    `cache_for_fetch` er cachen aa hente INN i hvis punktet av en eller
    annen grunn ikke alt ligger i `merged` (skal normalt ikke skje, siden
    main() alltid henter Saltstein foerst)."""
    lat, lon = saltstein_spot["offshore_point"]
    key = _point_key(lat, lon)
    waves = merged["wave_by_point"].get(key)
    if waves is None:
        if cache_for_fetch is None:
            raise KeyError(f"ingen boelgedata for Saltstein sitt punkt ({key}) i cachen")
        waves = get_waves(cache_for_fetch, lat, lon, refresh=refresh)
        merged["wave_by_point"][key] = waves

    wp, hs_by_time, tp_by_time = {}, {}, {}
    for ts, w in waves.items():
        hs, tp = w.get("hs"), w.get("tp")
        if hs is not None and tp is not None:
            wp[ts] = round(P.wave_power(hs, tp), 1)
            hs_by_time[ts] = hs
            tp_by_time[ts] = tp
    return wp, hs_by_time, tp_by_time


def score_spot(spot, merged, regional_wp_by_time, regional_hs_by_time, regional_tp_by_time,
               wave_point_override=None):
    """
    Kjor evaluate_class_ab()/evaluate_class_c() + score_hour() (agent.py,
    IMPORTERT - se modulens docstring) for ETT spot mot allerede hentet/
    sammenslaatt Open-Meteo-data. Returnerer en liste av score_hour() sine
    egne dicts, EN per time - ingen felt omdopt eller lagt til her.

    `wave_point_override`: (lat, lon) aa hente boelgeserien fra i stedet
    for spotens normale punkt (wave_wind_points()) - brukt til aa score
    samme spot mot et ANNET (typisk et gammelt) offshore_point, se
    report_old_vs_new_point().

    lead_h settes til 0.0 for alle timer - "tid fram i tid fra en
    kjoring" er ikke et meningsfullt begrep i en retrospektiv backtest
    (feltet er kun beskrivende i score_hour(), se den sin docstring).
    water_cm sendes alltid None - Kartverket-vannstand historikk er
    utenfor scope her (score_hour() faller da tilbake paa spotens eget
    water_optimal_cm, samme forsiktige default agent.py sin egen
    gather()-feilhaandtering allerede bruker).
    """
    wind_pt, wave_pt = A.wave_wind_points(spot)
    if wave_point_override is not None:
        wave_pt = wave_point_override

    wind_series = merged["wind_by_point"].get(_point_key(*wind_pt), {})
    wave_series = merged["wave_by_point"].get(_point_key(*wave_pt), {})

    times = sorted(set(wind_series) & set(wave_series))
    if not times:
        return []

    if spot["klasse"] == "C":
        computed = A.evaluate_class_c(spot, times, wind_series, wave_series)
    else:
        computed = A.evaluate_class_ab(spot, times, wind_series, wave_series)

    hours = []
    for ts, c in computed:
        hours.append(A.score_hour(
            spot, ts, wind_series.get(ts, {}), wave_series.get(ts, {}), None, c,
            lead_h=0.0,
            regional_wp=regional_wp_by_time.get(ts),
            regional_hs=regional_hs_by_time.get(ts),
            regional_tp=regional_tp_by_time.get(ts),
        ))
    return hours


# --------------------------------------------------------------- rapport


TIMETABELL_FELT = ("time", "hs_eff", "tp_eff", "dir_eff", "stars", "p_surf")


def print_timetable(spot_id, hours, title=None):
    print(f"\n{'='*90}\n{title or spot_id}  [{KILDE_TAG}]\n{'='*90}")
    print("".join(f"{f:>12}" for f in TIMETABELL_FELT))
    for h in hours:
        row = []
        for f in TIMETABELL_FELT:
            v = h.get(f)
            if f == "time":
                v = v[11:16]
            row.append(f"{('-' if v is None else v)!s:>12}")
        print("".join(row))


def report_old_vs_new_point(spot_id, spot, merged, regional, title_extra=""):
    """
    Kjernen i backtesten: score SAMME spot to ganger for samme doegn -
    en gang mot dagens offshore_point, en gang mot det gamle (se
    OLD_OFFSHORE_POINTS) - og rapporter differansen i hs_eff time for
    time. EN konsistent kilde (Open-Meteo) paa begge punkter isolerer
    effekten av flyttingen alene, selv om kilden ikke er MET (se
    modulens docstring - hver utskrift sier det eksplisitt).

    Mangler spotten et kjent gammelt punkt (se OLD_OFFSHORE_POINTS),
    rapporteres KUN dagens konfigurasjon, med en eksplisitt merknad om
    hvorfor det ikke finnes noe aa sammenligne mot - IKKE en gjettet
    "gammel" verdi (se rapporten til bruker om Saltstein, som aldri har
    flyttet sitt offshore_point)."""
    regional_wp, regional_hs, regional_tp = regional
    old_point = OLD_OFFSHORE_POINTS.get(spot_id)

    hours_new = score_spot(spot, merged, regional_wp, regional_hs, regional_tp)
    print_timetable(spot_id, hours_new, title=f"{spot['name']} - dagens offshore_point "
                     f"{tuple(spot['offshore_point'])}{title_extra}")

    if old_point is None:
        print(f"\n  {spot_id}: offshore_point er UENDRET i hele spots.yaml sin git-historikk "
              f"({tuple(spot['offshore_point'])}) - ingen gammelt punkt aa sammenligne mot. "
              f"Tabellen over er dagens (eneste) konfigurasjon, vist som referanse.")
        return {"spot": spot_id, "old_point": None, "hours_new": hours_new}

    hours_old = score_spot(spot, merged, regional_wp, regional_hs, regional_tp,
                            wave_point_override=old_point)
    print_timetable(spot_id, hours_old, title=f"{spot['name']} - GAMMELT offshore_point {old_point}"
                     f"{title_extra}")

    by_time_new = {h["time"]: h for h in hours_new}
    by_time_old = {h["time"]: h for h in hours_old}
    print(f"\n  {spot_id}: differanse hs_eff (nytt - gammelt punkt)  [{KILDE_TAG}]")
    print(f"  {'time':<8}{'nytt':>8}{'gammelt':>10}{'diff':>8}")
    diffs = []
    for ts in sorted(set(by_time_new) & set(by_time_old)):
        n, o = by_time_new[ts]["hs_eff"], by_time_old[ts]["hs_eff"]
        d = round(n - o, 2)
        diffs.append(d)
        print(f"  {ts[11:16]:<8}{n:>8}{o:>10}{d:>+8}")
    if diffs:
        print(f"\n  gj.snitt diff: {sum(diffs)/len(diffs):+.2f} m   "
              f"storst: {max(diffs, key=abs):+.2f} m")
    return {"spot": spot_id, "old_point": list(old_point), "hours_new": hours_new,
            "hours_old": hours_old, "diffs": diffs}


# ------------------------------------------------------ Saltstein-holdbarhet


def saltstein_persistence(hours, peak_min_hs=None):
    """
    'Erfaringsbasert hypotese fra brukeren' (se rapport til bruker):
    Saltstein holder seg naer 36 timer paa litt store dager. Finner
    toppen (hoyeste hs_eff) i `hours` (allerede sortert kronologisk,
    kan spenne flere sammenhengende doegn - se main() sin bruk av
    merge_caches()), og maaler:
      - hvor mange timer etter toppen hs_eff fortsatt er >= halve
        topp-verdien (halveringstid, None hvis den ALDRI halveres
        innenfor de hentede timene - rapporteres eksplisitt som det,
        IKKE ekstrapolert),
      - siste time i `hours` der hs_eff fortsatt er >= `peak_min_hs`
        (spotens eget min_hs - "fortsatt surfbart", ikke et nytt,
        oppfunnet tall).
    Returnerer None hvis `hours` er tom eller ingen time har hs_eff.
    """
    valid = [h for h in hours if h.get("hs_eff") is not None]
    if not valid:
        return None
    peak = max(valid, key=lambda h: h["hs_eff"])
    peak_idx = valid.index(peak)
    after = valid[peak_idx:]

    half = peak["hs_eff"] / 2.0
    half_life_h = None
    for i, h in enumerate(after):
        if h["hs_eff"] < half:
            half_life_h = i  # timer siden toppen (0-indeksert avstand)
            break

    last_surfable = None
    if peak_min_hs is not None:
        for h in after:
            if h["hs_eff"] >= peak_min_hs:
                last_surfable = h["time"]

    return {
        "peak_time": peak["time"], "peak_hs_eff": peak["hs_eff"],
        "n_hours_after_peak": len(after) - 1,
        "half_life_h": half_life_h,
        "last_surfable_time": last_surfable,
        "hs_eff_at_window_end": after[-1]["hs_eff"],
    }


def print_persistence_report(spot_id, result):
    print(f"\n{'='*90}\nSaltstein-holdbarhet - {spot_id}  [{KILDE_TAG}]\n{'='*90}")
    if result is None:
        print("  ingen gyldige hs_eff-timer aa analysere")
        return
    print(f"  topp: {result['peak_hs_eff']} m @ {result['peak_time']}")
    print(f"  timer hentet ETTER toppen: {result['n_hours_after_peak']}")
    if result["half_life_h"] is None:
        print(f"  halveringstid: IKKE naadd innenfor det hentede vinduet - "
              f"hs_eff er fortsatt {result['hs_eff_at_window_end']} m "
              f"({'>=' if result['hs_eff_at_window_end'] >= result['peak_hs_eff']/2 else '<'} "
              f"halve toppen) ved vinduets slutt. Modellen underdriver IKKE holdbarheten "
              f"innenfor 36 t hvis dette vinduet daekker minst saa lenge - se timer hentet over.")
    else:
        vurdering = ("raskere enn 36 t - se hypotesen i rapporten til bruker"
                     if result["half_life_h"] < 36 else "36 t eller mer, i traad med hypotesen")
        print(f"  halveringstid: {result['half_life_h']} t etter toppen ({vurdering})")
    if result["last_surfable_time"] is not None:
        print(f"  siste time med hs_eff >= spotens eget min_hs: {result['last_surfable_time']}")
    else:
        print("  aldri (igjen) over spotens eget min_hs etter toppen, innenfor vinduet")


# ------------------------------------------------------------------ main


def process_date(date, spots_by_id, spot_ids, refresh, fixtures_dir):
    """Hent/last cache for EEN dato, kun for punktene `spot_ids` (pluss
    Saltstein sitt offshore_point, alltid - se regional_series())
    trenger. Lagrer fixturen til slutt (uendret hvis alt allerede laa
    der - save_fixture() skriver uansett, men samme innhold gir samme
    fil). Returnerer cachen."""
    cache = load_fixture(date, fixtures_dir)
    saltstein = spots_by_id["saltstein"]

    needed_points = set()
    for sid in spot_ids:
        spot = spots_by_id[sid]
        wind_pt, wave_pt = A.wave_wind_points(spot)
        needed_points.add(("wind", wind_pt))
        needed_points.add(("wave", wave_pt))
        if sid in OLD_OFFSHORE_POINTS:
            needed_points.add(("wave", OLD_OFFSHORE_POINTS[sid]))
    needed_points.add(("wave", tuple(saltstein["offshore_point"])))

    for kind, (lat, lon) in sorted(needed_points):
        if kind == "wind":
            get_wind(cache, lat, lon, refresh=refresh)
        else:
            get_waves(cache, lat, lon, refresh=refresh)

    save_fixture(cache, fixtures_dir)
    return cache


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dates", nargs="+", help="en eller flere datoer, YYYY-MM-DD")
    ap.add_argument("--spots", default=None,
                     help="kommaseparert liste spot-id-er (default: alle i spots.yaml)")
    ap.add_argument("--compare-old-point", default="molen_odden,saltstein",
                     help="kommaseparert liste spot-id-er aa kjore gammelt-vs-nytt-"
                          "offshore_point-sammenligningen for (tom streng slaar den av)")
    ap.add_argument("--refresh", action="store_true",
                     help="ignorer cache, hent alt paa nytt fra Open-Meteo")
    ap.add_argument("--fixtures-dir", default=str(FIXTURES_DIR))
    args = ap.parse_args()

    fixtures_dir = Path(args.fixtures_dir)
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    spot_ids = [s.strip() for s in args.spots.split(",")] if args.spots else list(spots_by_id)
    compare_ids = [s.strip() for s in args.compare_old_point.split(",") if s.strip()]

    print(f"Datakilde: {DATAKILDE}\n")

    caches = []
    for date in args.dates:
        log(f"Henter/laster {date} ...")
        cache = process_date(date, spots_by_id, set(spot_ids) | set(compare_ids), args.refresh, fixtures_dir)
        caches.append(cache)
        log(f"  -> {fixture_path(date, fixtures_dir)}")

    merged = merge_caches(caches)
    saltstein = spots_by_id["saltstein"]
    regional = regional_series(merged, saltstein)

    # compare_ids scores BAADE med og uten override i report_old_vs_new_point()
    # - gjenbruk "hours_new" derfra i stedet for aa score disse spotene en
    # gang til under (samme resultat, halverer antall score_spot()-kall for
    # dem).
    all_hours_by_spot = {}
    for spot_id in compare_ids:
        result = report_old_vs_new_point(spot_id, spots_by_id[spot_id], merged, regional)
        all_hours_by_spot[spot_id] = result["hours_new"]

    for spot_id in spot_ids:
        if spot_id in all_hours_by_spot:
            continue
        hours = score_spot(spots_by_id[spot_id], merged, *regional)
        all_hours_by_spot[spot_id] = hours
        print_timetable(spot_id, hours, title=spots_by_id[spot_id]["name"])

    if "saltstein" in all_hours_by_spot:
        result = saltstein_persistence(all_hours_by_spot["saltstein"], peak_min_hs=saltstein["min_hs"])
        print_persistence_report("saltstein", result)


if __name__ == "__main__":
    main()
