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

BEGRENSNINGER (ordre 2026-09-08, se rapport til bruker - LES foer du
trekker noen konklusjon fra et backtest-tall):

Open-Meteo sitt boelgegrid er GROVERE enn METs (som produksjonen faktisk
kjorer paa, se sources.met_waves()/EWAM ~5 km). I kjoringen 2026-09-07
ga gammelt og nytt offshore_point for molen_odden IDENTISKE verdier alle
48 timer (+0,00 m gjennomsnitts- OG storste diff, se
report_old_vs_new_point()) - og molen_odden og saltstein hadde IDENTISK
hs_eff/tp_eff time for time gjennom hele vinduet. Begge punktene (0,71
og 4,0 km fra Moelen, pluss Saltsteins eget punkt et godt stykke unna
igjen) leser med andre ord samme underliggende Open-Meteo-gridcelle.
Backtesten kan derfor IKKE brukes til aa avgjore gridcelle-spoersmaal
(feilfunnet som faktisk motiverte aa flytte molen_odden sitt
offshore_point i utgangspunktet - se PR-historikken) eller til aa
sammenligne nabospotters paadrag mot hverandre - begge oppgavene krever
noeyaktig den lokale oppløsningen denne kilden ikke har.

Den ga ogsaa 0,86 m kl. 12 den 5. sept for Saltstein, der MET (samme
kilde produksjonen bruker, se out/shadow.csv paa data-grenen) ga 1,4 m -
med toppen omtrent tre timer senere i METs egen serie. Sammenligning mot
sessions.csv (faktiske observerte okter) skal derfor bruke MET-verdiene
i shadow.csv, IKKE denne backtestens tall - de to kildene er ikke
utskiftbare, selv om begge kalles "hs_eff".

Backtesten egner seg til aa sammenligne KONFIGURASJONER mot hverandre
INNENFOR samme kjoring (samme kilde paa begge sider isolerer effekten av
en endring - se report_old_vs_new_point()), IKKE til aa maale mot
virkeligheten.

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


# ordre 2026-09-08 (se rapport til bruker): backtesten produserte rader med
# fysisk umulige verdier for flere klasse C-spots (Tp 0,4-1,3 s, Hs
# 0,02-0,09 m, retninger 30-100 grader utenfor det spotten ellers viser).
# ROTAARSAK, verifisert mot raadataene i tests/fixtures/backtest/: IKKE
# manglende/tomme input (vinden var reell, f.eks. 3,5 m/s fra 309 grader) -
# physics.build_local_sea()/jonswap_growth() sin fetch-begrensede
# SPM/CEM-vekstformel har ingen nedre grense for hvor kort en fetch den vil
# ekstrapolere fra. Blaaser vinden fra en retning med naermest null lokal
# fetch (her: slagen sin 16-punkts fetch_km-tabell gir ca. 0,1 km for 309
# grader - en fralands/tvers-retning), regner formelen ut en matematisk
# gyldig, men fysisk meningslos "sjo" (Hs under en centimeter, Tp under et
# sekund) i stedet for aa returnere naer null. Dette skjer UANSETT varighet
# (bekreftet algebraisk: x_fetch << x_duration her, saa resultatet er
# fetch-begrenset, ikke varighetsbegrenset - en lengre vindhistorikk ville
# IKKE fikset det) - synligst ved doegnstart i denne backtesten fordi
# varigheten uansett er kappet til 1-2 t der, men den underliggende
# feilklassen er generell, ikke en cold-start-artefakt.
#
# IKKE fikset i physics.py (ville vaert en endring i scoring-logikken,
# utenfor scope her - se ordren). I stedet: valider FOER score_hour()
# kalles (se _er_gyldig_sjo()/score_spot() under) - en rad som SER ut som
# data, men beskriver en boelge kortere enn naturen tillater, er verre enn
# en rad som mangler, se rapporten til bruker.
MIN_TP_S = 2.0
MIN_HS_M = 0.05


def _er_gyldig_sjo(computed):
    """False for en fysisk umulig sjotilstand (se ordre 2026-09-08 over) -
    Tp under MIN_TP_S eller Hs under MIN_HS_M. None-verdier telles ogsaa
    som ugyldige (mangler data helt), ikke som "0 er greit"."""
    hs, tp = computed.get("hs_eff"), computed.get("tp_eff")
    if hs is None or tp is None:
        return False
    return tp >= MIN_TP_S and hs >= MIN_HS_M


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

    Rader der `computed` sin hs_eff/tp_eff er fysisk umulig (se
    _er_gyldig_sjo() og ordre 2026-09-08 over) hoppes over FOER
    score_hour() kalles - de telles og logges (stderr), men havner
    aldri i den returnerte lista.
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
    skipped = []
    for ts, c in computed:
        if not _er_gyldig_sjo(c):
            skipped.append((ts, c.get("hs_eff"), c.get("tp_eff")))
            continue
        hours.append(A.score_hour(
            spot, ts, wind_series.get(ts, {}), wave_series.get(ts, {}), None, c,
            lead_h=0.0,
            regional_wp=regional_wp_by_time.get(ts),
            regional_hs=regional_hs_by_time.get(ts),
            regional_tp=regional_tp_by_time.get(ts),
        ))
    if skipped:
        log(f"  {spot['id']}: {len(skipped)} rad(er) hoppet over (Tp < {MIN_TP_S} s eller "
            f"Hs < {MIN_HS_M} m - manglende data, se ordre 2026-09-08):")
        for ts, hs, tp in skipped:
            log(f"    {ts}: hs_eff={hs} tp_eff={tp}")
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
    Saltstein holder seg naer 36 timer paa litt store dager.

    ordre 2026-09-08 (se rapport til bruker): FORRIGE versjon maalte dette
    paa hs_eff alene, og konkluderte "ikke naadd paa 32 timer" fordi hs_eff
    fortsatt sto i 0,96 m - men Tp hadde i samme vindu falt fra 8,2 til
    4,7 s. Det er ikke den samme sjoen: donningen doede og en kortperiodisk
    lokal vindsjo fylte plassen med omtrent samme hoyde. Hs alene kan ikke
    skille de to - energifluks (P = rho*g^2*Hs^2*Tp/(64*pi), se
    physics.wave_power(), IMPORTERT herfra - ikke duplisert selv om
    P = 0,49*Hs^2*Tp er den samme formelen skrevet ut) kan, siden Tp gaar
    inn direkte: en kort periode med samme Hs gir markert LAVERE P.

    Finner toppen i `hours` (allerede sortert kronologisk, kan spenne
    flere sammenhengende doegn - se main() sin bruk av merge_caches()) -
    FORTSATT definert som hoyeste hs_eff (samme utvalg som forrige
    versjon, tie-break til FORSTE forekomst - "toppen av donningen" er
    fortsatt et Hs-begrep, se kontrollregningen under: den ekte P-
    maksimum i datasettet ligger faktisk 6 t SENERE, kl. 21:00, med
    marginalt hoyere Tp - men kontrollregningen forankrer eksplisitt i
    15:00. Det som ENDRER SEG her er ikke hvilken time som telles som
    toppen, men HVORDAN holdbarheten derfra maales), og maaler:
      - halveringstid PAA ENERGI: timer til P < halve topp-P (None hvis
        ALDRI naadd innenfor de hentede timene - rapporteres eksplisitt
        som det, IKKE ekstrapolert),
      - Tp ved toppen OG ved halveringspunktet, saa det er synlig om det
        fortsatt er samme sjo eller en ny en har tatt over,
      - halveringstid paa Hs ALENE, beholdt som egen linje for
        sammenligning (se print_persistence_report()) - IKKE hovedtallet.
      - siste time i `hours` der hs_eff fortsatt er >= `peak_min_hs`
        (spotens eget min_hs - "fortsatt surfbart", ikke et nytt,
        oppfunnet tall).
    Returnerer None hvis `hours` er tom eller ingen time har baade
    hs_eff og tp_eff.

    Kontrollregning (5.-6. sept 2026, Saltstein - se rapport til bruker):
    topp 15:00 (hs 1,30/tp 7,9 -> P=6,5 kW/m), 00:00 neste doegn
    (hs 0,94/tp 7,0 -> P=3,0 kW/m, under halve) - halveringstid PAA ENERGI
    ca. 9 timer, IKKE 32 (som var hs-halveringstiden i forrige versjon).
    """
    valid = [h for h in hours if h.get("hs_eff") is not None and h.get("tp_eff") is not None]
    if not valid:
        return None
    peak_h = max(valid, key=lambda h: h["hs_eff"])
    peak_idx = valid.index(peak_h)
    peak_p = P.wave_power(peak_h["hs_eff"], peak_h["tp_eff"])
    after = [(h, P.wave_power(h["hs_eff"], h["tp_eff"])) for h in valid[peak_idx:]]

    half_p = peak_p / 2.0
    half_life_h_energy, half_point = None, None
    for i, (h, p) in enumerate(after):
        if p < half_p:
            half_life_h_energy = i  # timer siden toppen (0-indeksert avstand)
            half_point = h
            break

    half_hs = peak_h["hs_eff"] / 2.0
    half_life_h_hs = None
    for i, (h, _) in enumerate(after):
        if h["hs_eff"] < half_hs:
            half_life_h_hs = i
            break

    last_surfable = None
    if peak_min_hs is not None:
        for h, _ in after:
            if h["hs_eff"] >= peak_min_hs:
                last_surfable = h["time"]

    return {
        "peak_time": peak_h["time"], "peak_hs_eff": peak_h["hs_eff"],
        "peak_tp_eff": peak_h["tp_eff"], "peak_p": round(peak_p, 1),
        "n_hours_after_peak": len(after) - 1,
        "half_life_h_energy": half_life_h_energy,
        "half_point_time": half_point["time"] if half_point else None,
        "half_point_tp_eff": half_point["tp_eff"] if half_point else None,
        "half_point_p": round(P.wave_power(half_point["hs_eff"], half_point["tp_eff"]), 1)
        if half_point else None,
        "half_life_h_hs": half_life_h_hs,
        "p_at_window_end": round(after[-1][1], 1),
        "last_surfable_time": last_surfable,
    }


def print_persistence_report(spot_id, result):
    print(f"\n{'='*90}\nSaltstein-holdbarhet, paa ENERGI (P = 0,49*Hs^2*Tp) - {spot_id}  [{KILDE_TAG}]\n{'='*90}")
    if result is None:
        print("  ingen gyldige hs_eff/tp_eff-timer aa analysere")
        return
    print(f"  topp: P={result['peak_p']} kW/m (hs {result['peak_hs_eff']} m, "
          f"tp {result['peak_tp_eff']} s) @ {result['peak_time']}")
    print(f"  timer hentet ETTER toppen: {result['n_hours_after_peak']}")
    if result["half_life_h_energy"] is None:
        print(f"  halveringstid (ENERGI): IKKE naadd innenfor det hentede vinduet - "
              f"P er fortsatt {result['p_at_window_end']} kW/m "
              f"({'>=' if result['p_at_window_end'] >= result['peak_p']/2 else '<'} "
              f"halve toppen) ved vinduets slutt. Modellen underdriver IKKE holdbarheten "
              f"innenfor 36 t hvis dette vinduet daekker minst saa lenge - se timer hentet over.")
    else:
        vurdering = ("raskere enn 36 t - se hypotesen i rapporten til bruker"
                     if result["half_life_h_energy"] < 36 else "36 t eller mer, i traad med hypotesen")
        print(f"  halveringstid (ENERGI): {result['half_life_h_energy']} t etter toppen ({vurdering})")
        print(f"  Tp ved halveringspunktet ({result['half_point_time']}): "
              f"{result['half_point_tp_eff']} s (mot {result['peak_tp_eff']} s ved toppen - "
              f"sjekk om dette fortsatt er samme sjo eller en ny, kortperiodisk vindsjo)")
    if result["half_life_h_hs"] is None:
        print(f"  halveringstid (Hs ALENE, kun til sammenligning - IKKE hovedtallet): "
              f"ikke naadd innenfor vinduet")
    else:
        print(f"  halveringstid (Hs ALENE, kun til sammenligning - IKKE hovedtallet): "
              f"{result['half_life_h_hs']} t etter toppen")
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
