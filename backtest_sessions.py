#!/usr/bin/env python3
"""
Regional boelgeenergi (ERA5-Ocean) mot faktiske okter (sessions_historisk.csv).

VIKTIG - LES FOER DU TOLKER NOE: dette skriptet kjorer IKKE lenger noen
spot-fysikk (evaluate_class_ab/evaluate_class_c/score_hour) paa ERA5-
data. Det gjorde tidligere versjoner, og resultatet (hs_eff/tp_eff/
stars per okt) er FORKASTET - se begrunnelsen under. Skriptet gjor na
KUN ETT tall: regional boelgeeffekt (regional_wp, kW/m) fra ERA5-Ocean,
paret mot hver okts observerte kvalitet. Ingenting annet.

  OPPLOSNINGSFUNNET (ordre 2026-09-02, diagnose_saltstein_20181218.py,
  se rapport til bruker) - HVORFOR spot-fysikk er droppet:

  ERA5-Ocean via Open-Meteo Marine API har et grid paa ~0,25 grader
  (~28 km ved 59N). For Saltstein ble tre kandidatpunkter testet -
  offshore_point, spot-koordinatet selv, og et punkt 5 km paa facing -
  og ALLE TRE traff NOYAKTIG SAMME gridcelle, sentrert 49-53 km unna
  Saltstein, ute i aapent Skagerrak. Punktvalg innenfor noen faa
  kilometer (det eneste en spot-korreksjon eller en bedre offshore_point
  kan justere) forandrer INGENTING - griddet er for grovt til aa
  oppdage det i det hele tatt.

  Konklusjon: ERA5-Ocean (denne kilden) kan IKKE brukes til LOKAL
  boelgehoyde naer kysten i denne regionen. Ethvert tall denne kilden
  har gitt for min_hs/ideal_hs/max_hs/min_tp (per-spot terskler) ved en
  navngitt spot er upaalitelig og skal IKKE brukes til aa kalibrere de
  tersklene - de maaler forholdene i en fjern, aapen gridcelle, ikke
  ved spotten.

  MEN: den samme fjerne, aapne gridcellen er nettopp det regional_wp
  (agent.py sin region-dekkende port, se score_hour()) er MENT aa
  maale - regional energi i aapent Skagerrak, IKKE lokal boelgehoyde
  ved en bestemt spot. Her er ERA5-Ocean en rimelig proxy. Derfor:
  dette skriptet regner regional_wp fra Saltsteins offshore_point (den
  ENE referansen agent.py alltid bruker for alle spots, se
  BIAS_REFERENCE_ID/regional_wp_by_time i agent.py sin run()) og parer
  det tallet mot observert kvalitet, INGENTING annet - se
  regional_energy_for_session()/print_regional_table().

  GRIDCELLE-KOLLISJONER (report_spot_grid_cells(), kjort forst i
  run_report()): siden griddet er saa grovt, kan flere spotters egne
  bolge-hentepunkter treffe SAMME celle. De fem klasse C-spotene
  (indre Oslofjord) deler allerede AV DESIGN eksakt samme gate-punkt
  (Faerder), saa det er ingen overraskelse der - men A/B-spotene sine
  offshore_point kan ogsaa kollidere uten at det er tilsiktet. Kolliderer
  to spotters punkter, er ERA5-seriene deres IKKE uavhengige
  datapunkter for noe som helst - se rapporten skriptet skriver ut.

VIDERE FORBEHOLD (uendret fra tidligere versjoner):
  - Reanalyse, ikke prognose: ERA5 er et etterpaaklokt beste-estimat
    for hva sjoen FAKTISK var, ikke hva en prognose ville sagt paa
    forhaand. lead_h-begrepet fra agent.py er ikke i bruk her i det
    hele tatt lenger (ingen score_hour()-kall).
  - Alle elleve oktene er POSITIVE (kvalitet >= 2 av 5) - ingen
    bomturer. Denne testen kan derfor vise hvilken regional_wp positive
    okter FAKTISK hadde (og dermed en EMPIRISK nedre grense-kandidat
    for regional_wp_min), men kan IKKE bekrefte at en LAVERE regional_wp
    faktisk betyr daarlig surf - det datasettet finnes ikke her.
  - 'tid'-kolonnen i sessions_historisk.csv har aldri hatt dokumentert
    tidssone. parse_time_window()/regional_energy_for_session() matcher
    presise klokkeslett direkte mot ERA5 sine UTC-stemplede timer -
    testet for Saltstein 2018-12-18 (diagnose_saltstein_20181218.py):
    UTC-tolkning vs. norsk lokaltid (UTC+1 i desember) ga kun 0,04 kW/m
    forskjell der, altsaa neppe avgjorende, men fortsatt en udokumentert
    antagelse verdt aa rette uavhengig av dette.

Datakilder:
  - Bolger: Open-Meteo Marine API i historisk modus
    (marine-api.open-meteo.com/v1/marine, start_date/end_date),
    models=era5_ocean (eneste modell med historikk foer desember 2023 -
    bekreftet med .github/workflows/probe-marine-archive.yml).
  - Vind hentes IKKE lenger (fetch_era5_wind() star igjen i modulen,
    ubrukt av rapporten - regional_wp er en ren bolgestorrelse, se
    physics.wave_power()).

Kjent modellskjevhet mellom ERA5-Ocean og standardmodellen (samme probe,
2023-06-01 og 2025-08-05): ERA5 ligger 50-130 % HOYERE i Hs. Produksjonens
regional_wp (agent.py, sanntid) bruker standardmodellen, ikke ERA5-Ocean.
quantify_bias() maaler forholdet empirisk FOR resten av rapporten (kalt
forst i run_report()), og regional_energy_all() kjores TO ganger - raa
ERA5 og skjevhetskorrigert (delt paa median-forholdet) - begge
rapportert, se run_report().

Kjores manuelt (ikke i noen workflow, ikke koblet inn i forecast.yml).
Dette miljoet hadde ingen utgaaende nettverkstilgang til Open-Meteo i
det hele tatt da dette ble skrevet - se .github/workflows/ for
motstykket som faktisk KAN naa nettet (backtest-sessions.yml).

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
import time

import requests

import agent as A
import physics as P

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "out"

WIND_URL = "https://archive-api.open-meteo.com/v1/archive"
WAVE_URL = "https://marine-api.open-meteo.com/v1/marine"

# ordre 2026-09-02 (produksjonskjoring i backtest-sessions.yml feilet med
# ReadTimeout): opptil ~60 kall raskt etter hverandre (quantify_bias(),
# 2 modeller x ca. 33 datoer), sannsynligvis der Open-Meteo sin rate-
# grense treffes. HTTP_TIMEOUT_S/RETRY_DELAYS_S gjelder ALLE kall via
# _get_json() under.
HTTP_TIMEOUT_S = 60
RETRY_DELAYS_S = (2, 5, 15)  # ventetid FOR hvert av de tre nye forsokene
RATE_LIMIT_PAUSE_S = 0.5  # mellom HVERT kall i quantify_bias() sin lokke

# Vage tidspunkt tolkes som vinduer (hel-timer, begge ender inkludert) -
# "de surfet sannsynligvis naar det var best" - her: hoyest regional_wp
# i vinduet, se pick_target_hour_regional(). Presise klokkeslett
# ("HH:MM") i CSV-en tolkes IKKE som et vindu - de ER observasjonen,
# se parse_time_window().
TIME_WINDOWS = {
    "morgen": (6, 10),
    "lunsj": (11, 14),
    "ettermiddag": (14, 19),
}

# ETT referansepunkt for BAADE skjevhetsmaalingen OG selve regional_wp-
# tallet (Saltsteins offshore_point) - IKKE per-spot. Samme referanse
# agent.py sin run() alltid bruker for regional_wp_by_time, uansett
# hvilken spot som scores. Se modulens docstring for gridcelle-funnet
# som gjor akkurat dette punktet til en brukbar REGIONAL (ikke lokal)
# maaling.
BIAS_REFERENCE_ID = "saltstein"

# Vilkaarlig dato for report_spot_grid_cells() - griddet ERA5-Ocean
# bruker er tidsuavhengig (samme celleinndeling for alle datoer), bare
# STEDET avgjor hvilken celle som treffes. Godt innenfor ERA5-historikk.
GRID_PROBE_DATE = "2015-06-01"


# ------------------------------------------------------------- tidsvindu


def parse_time_window(tid):
    """
    Tolk 'tid'-kolonnen i sessions_historisk.csv.

    Returnerer (lo_hour, hi_hour, exact). exact=True betyr lo == hi og
    at dette IKKE skal "beste time i vinduet"-velges - det presise
    klokkeslettet ER observasjonen. exact=False for de tre vage
    kategoriene (TIME_WINDOWS) - der velges timen med hoyest regional_wp
    i vinduet, se pick_target_hour_regional().
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
    forespoerselen) -> normalisert iso-form (samme konvensjon som
    sources.py sin _iso())."""
    d = dt.datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()


def _at(arr, i):
    return arr[i] if arr and i < len(arr) else None


# ordre 2026-09-02: tellere for HTTP-robustheten under, IKKE en del av
# scoringen. n_calls = totalt antall _get_json()-kall denne prosessen
# har gjort; n_retried = hvor mange av dem som trengte MINST ett nytt
# forsok foer de lyktes. run_report() rapporterer disse til slutt - se
# ogsaa reset_http_stats() (brukt av testene, saa tellerne ikke lekker
# mellom testfunksjoner som deler denne modulens tilstand).
_http_stats = {"n_calls": 0, "n_retried": 0}


def reset_http_stats():
    _http_stats["n_calls"] = 0
    _http_stats["n_retried"] = 0


def _get_json(url, params, timeout=HTTP_TIMEOUT_S):
    """
    GET -> .json(), med inntil tre NYE forsok (fire totalt) ved
    forbigaaende feil (ReadTimeout, tilkoblingsfeil osv.) - ventetid FOR
    hvert nytt forsok fra RETRY_DELAYS_S (2, 5, 15 s).

    Gir opp og lar unntaket forplante seg naar alle forsokene er brukt
    opp - IKKE fanget her. quantify_bias() fanger det per kall (fortsetter
    med faerre par i stedet for aa velte hele kjoringen), mens
    regional_energy_for_session() lar det forplante seg til sitt eget
    try/except - samme "feiler mykt per enhet"-prinsipp som sources.py
    sin docstring.
    """
    _http_stats["n_calls"] += 1
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
        if attempt > 0:
            _http_stats["n_retried"] += 1
        return r.json()
    raise last_exc


def fetch_era5_wind(lat, lon, date_from, date_to):
    """ERA5 vind - IKKE brukt av regional-energi-rapporten (regional_wp
    er en ren bolgestorrelse, se physics.wave_power()). Star igjen som
    generell hjelpefunksjon, samme dict-form som sources.met_wind()."""
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
    som sources.met_waves()/openmeteo_waves() (hs, tp, wave_from_direction).

    model=None ber om standardmodellen (EWAM/GWAM, "best_match") - kun
    brukt i quantify_bias(), IKKE i regional-energi-rapporten (som
    alltid bruker era5_ocean, se modulens docstring for hvorfor).
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
    pluss de raa parvise radene og en feiltelling.

    IKKE et scorekomponent - rent maalt, brukes til aa lage
    korreksjonsfaktoren regional_energy_all(..., bias=...) deler ERA5-
    verdier paa. Kjores FOR resten av rapporten (se modulens docstring/
    run_report()).

    Robusthet (ordre 2026-09-02, etter ReadTimeout i produksjon): dette
    er ~2 x len(dates) kall raskt etter hverandre - den mest sannsynlige
    plassen til aa treffe Open-Meteo sin rate-grense. To tiltak:
      1. RATE_LIMIT_PAUSE_S pause FOR hvert kall (proaktivt, ikke bare
         reaktivt - _get_json() sin retry-logikk haandterer forbigaaende
         feil, denne pausen er ment aa forebygge dem i utgangspunktet).
      2. Ett kall som feiler (alle _get_json()-forsokene brukt opp)
         STOPPER IKKE hele maalingen lenger - den datoen/det kallet
         hoppes over, telles i `n_calls_failed`, og maalingen fortsetter
         med det den fikk. Stopper (raise) KUN hvis null par i det hele
         tatt ble samlet inn (kan ikke regne en median av ingenting) -
         "faerre par enn haapet" er noe aa RAPPORTERE (se
         n_calls_failed/n_dates_partial under, og run_report() sin
         utskrift), ikke noe aa avbryte for.
    """
    pairs = {"hs": [], "tp": []}
    per_date = []
    n_calls_failed = 0
    failed_dates = []
    for date in dates:
        try:
            time.sleep(RATE_LIMIT_PAUSE_S)
            era5 = fetch_era5_waves(lat, lon, date, date, model="era5_ocean")
            time.sleep(RATE_LIMIT_PAUSE_S)
            std = fetch_era5_waves(lat, lon, date, date, model=None)
        except requests.RequestException as exc:
            n_calls_failed += 1
            failed_dates.append((date, str(exc)))
            per_date.append((date, 0))
            continue

        n_before = len(pairs["hs"])
        for ts in sorted(set(era5) & set(std)):
            e, s = era5[ts], std[ts]
            if e.get("hs") is not None and s.get("hs") not in (None, 0):
                pairs["hs"].append((date, ts, e["hs"], s["hs"], e["hs"] / s["hs"]))
            if e.get("tp") is not None and s.get("tp") not in (None, 0):
                pairs["tp"].append((date, ts, e["tp"], s["tp"], e["tp"] / s["tp"]))
        per_date.append((date, len(pairs["hs"]) - n_before))

    if not pairs["hs"]:
        raise RuntimeError(
            f"Null Hs-par mellom era5_ocean og standardmodellen funnet over "
            f"{len(dates)} datoer ({n_calls_failed} kall feilet helt) - kan ikke "
            f"regne noen median. Sjekk om standardmodellen faktisk har data i "
            f"perioden (se probe-marine-archive.yml), og se failed_dates for hva "
            f"som feilet."
        )
    if len(pairs["hs"]) < 10:
        print(f"  ADVARSEL: bare {len(pairs['hs'])} Hs-par samlet inn (av forventet "
              f"~{24 * len(dates)}) - medianen under er mindre paalitelig enn normalt.",
              file=sys.stderr)

    def summarize(vals):
        """n=0 (f.eks. Tp mangler helt en periode Hs finnes for) ->
        alle felt None, IKKE en krasjet st.quantiles()/st.median() paa
        en tom liste. n=1 -> kvartiler udefinerbare, faller tilbake til
        selve verdien for alle fem tallene i stedet for aa kreve n>=2
        (st.quantiles() sin egen grense)."""
        ratios = sorted(v[4] for v in vals)
        n = len(ratios)
        if n == 0:
            return {"n": 0, "median": None, "p25": None, "p75": None, "min": None, "max": None}
        med = st.median(ratios)
        q1, q3 = (st.quantiles(ratios, n=4)[0], st.quantiles(ratios, n=4)[2]) if n >= 2 \
            else (ratios[0], ratios[0])
        return {
            "n": n, "median": round(med, 3),
            "p25": round(q1, 3), "p75": round(q3, 3),
            "min": round(ratios[0], 3), "max": round(ratios[-1], 3),
        }

    return {
        "hs": summarize(pairs["hs"]),
        "tp": summarize(pairs["tp"]),
        "n_dates_with_data": sum(1 for _, n in per_date if n > 0),
        "n_dates_requested": len(dates),
        "n_calls_failed": n_calls_failed,
        "failed_dates": failed_dates,
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


# ------------------------------------------------------- gridcelle-rapport


def _spot_wave_point(spot):
    """Samme punkt-logikk agent.py sin run()/gamle build_hours_window()
    brukte for aa hente en spots EGEN boelgeserie: gate for klasse C
    (fjordspots - swell kommer inn via en delt terskel, se spots.yaml),
    offshore_point ellers. IKKE brukt til aa regne regional_wp (den
    kommer alltid fra Saltsteins offshore_point, se BIAS_REFERENCE_ID) -
    kun til aa se hvilken ERA5-gridcelle HVER spots EGEN referansepunkt
    lander i, se report_spot_grid_cells()."""
    if spot["klasse"] == "C":
        return (spot["gate"]["lat"], spot["gate"]["lon"])
    return tuple(spot["offshore_point"])


def fetch_grid_cell(lat, lon):
    """Det FAKTISKE gridpunktet Open-Meteo brukte for (lat, lon) -
    ligger paa toppniva i svaret (ikke i den timevise strukturen).
    GRID_PROBE_DATE er vilkaarlig, se den sin egen kommentar."""
    data = _get_json(WAVE_URL, {
        "latitude": lat, "longitude": lon,
        "start_date": GRID_PROBE_DATE, "end_date": GRID_PROBE_DATE,
        "hourly": "wave_height",
        "timezone": "UTC",
        "models": "era5_ocean",
    })
    return (data.get("latitude"), data.get("longitude"))


def report_spot_grid_cells(spots):
    """
    For hver spot: hvilken ERA5-Ocean-gridcelle treffer spottens EGEN
    boelge-hentepunkt (_spot_wave_point())? Grupperer etterpaa paa
    gridcelle - havner flere spots i SAMME celle, er ERA5-seriene deres
    IKKE uavhengige datapunkter for noe som helst (se modulens
    docstring). De fem klasse C-spotene deler AV DESIGN allerede samme
    gate-koordinat (Faerder) - forventet, ingen overraskelse. A/B-
    spotenes egne offshore_point kan kollidere UTEN aa vaere tilsiktet -
    det er DET denne rapporten skal avsloere.
    """
    print(f"\n{'='*90}\nERA5-OCEAN GRIDCELLE PER SPOT (era5_ocean, probedato {GRID_PROBE_DATE})\n{'='*90}")
    rows = []
    for spot in spots:
        lat, lon = _spot_wave_point(spot)
        try:
            grid = fetch_grid_cell(lat, lon)
        except requests.RequestException as exc:
            print(f"  {spot['id']:<16} klasse {spot['klasse']}  FEIL: henting feilet: {exc}")
            continue
        rows.append({"id": spot["id"], "klasse": spot["klasse"], "punkt": [lat, lon], "grid": list(grid)})
        print(f"  {spot['id']:<16} klasse {spot['klasse']}  spurt {lat},{lon} -> gridcelle {grid[0]},{grid[1]}")

    by_grid = {}
    for r in rows:
        by_grid.setdefault(tuple(r["grid"]), []).append(r["id"])

    print(f"\n  Grupper per gridcelle (samme celle = IKKE uavhengige boelgedatapunkter):")
    for grid, ids in by_grid.items():
        merknad = "  <- FLERE SPOTS I SAMME CELLE" if len(ids) > 1 else ""
        print(f"    {grid[0]},{grid[1]}: {', '.join(ids)}{merknad}")

    return rows


# ---------------------------------------------------------- regional energi


def fetch_regional_wave_series(lat, lon, date, bias=None):
    """
    Regional boelgeeffekt (kW/m, physics.wave_power()) for HVER time i
    ett doegn ved (lat, lon) - INGEN spot-fysikk, ingen vind, ingen
    lookback-doegn (det trengtes kun for evaluate_class_c() sin
    build_local_sea(), som ikke kjores her lenger). bias: None (raa
    ERA5) eller {"hs": faktor, "tp": faktor} fra quantify_bias(), delt
    inn FOER wave_power() regnes (samme rekkefolge som den gamle
    build_hours_window() brukte).
    """
    waves = fetch_era5_waves(lat, lon, date, date, model="era5_ocean")
    if bias:
        waves = _apply_bias(waves, bias)
    out = {}
    for ts, w in waves.items():
        hs, tp = w.get("hs"), w.get("tp")
        if hs is not None and tp is not None:
            out[ts] = {"hs": hs, "tp": tp, "wp": round(P.wave_power(hs, tp), 2)}
    return out


def pick_target_hour_regional(series, day, lo_hour, hi_hour):
    """Timen med HOYEST regional_wp paa `day` innenfor [lo_hour, hi_hour]
    - for et EXACT klokkeslett (parse_time_window) er lo==hi, saa dette
    reduserer til aa plukke akkurat den ene timen. None hvis ingen
    kandidattime finnes (manglende data den dagen). Returnerer
    (tidsstempel, {"hs":, "tp":, "wp":}) eller None."""
    candidates = [
        (ts, row) for ts, row in series.items()
        if ts[:10] == day and lo_hour <= int(ts[11:13]) <= hi_hour
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1]["wp"])


def regional_energy_for_session(session, spots_by_id, saltstein_pt, bias=None):
    """
    EN rad: observert kvalitet mot regional_wp den timen (kun det -
    ingen spot-fysikk kjores, se modulens docstring). `spots_by_id`
    brukes KUN til aa validere at 'spot' i CSV-en faktisk finnes (fanger
    CSV-skrivefeil) - selve regional_wp er uavhengig av hvilken spot
    okten gjaldt, se BIAS_REFERENCE_ID.
    """
    if session["spot"] not in spots_by_id:
        return {**session, "feil": f"ukjent spot-id '{session['spot']}'"}
    try:
        lo, hi, exact = parse_time_window(session["tid"])
    except ValueError as exc:
        return {**session, "feil": str(exc)}

    try:
        series = fetch_regional_wave_series(*saltstein_pt, session["dato"], bias)
    except requests.RequestException as exc:
        return {**session, "feil": f"henting feilet: {exc}"}

    target = pick_target_hour_regional(series, session["dato"], lo, hi)
    if target is None:
        return {**session, "feil": "ingen regional boelgedata for gitt dato/vindu (Open-Meteo svarte, men uten treff)"}

    ts, row = target
    return {**session, "exact_time": exact, "valgt_tid_utc": ts,
            "hs": row["hs"], "tp": row["tp"], "wp": row["wp"]}


def regional_energy_all(sessions, spots_by_id, saltstein_pt, bias=None):
    return [regional_energy_for_session(s, spots_by_id, saltstein_pt, bias) for s in sessions]


# --------------------------------------------------------------- rapport


def load_sessions(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def print_bias_report(bias, label):
    print(f"\n{'='*78}\nMODELLSKJEVHET: ERA5-Ocean / standardmodell (EWAM/GWAM) - {label}\n{'='*78}")
    print(f"  datoer med begge modeller: {bias['n_dates_with_data']}/{bias['n_dates_requested']}")
    n_failed = bias.get("n_calls_failed", 0)
    if n_failed:
        print(f"  ADVARSEL: {n_failed} dato(er) manglet helt - alle forsokene (se "
              f"RETRY_DELAYS_S) feilet for dem. Maalingen fortsatte med resten:")
        for date, err in bias.get("failed_dates", []):
            print(f"    {date}: {err}")
    for var in ("hs", "tp"):
        b = bias[var]
        print(f"  {var.upper():<3} forhold ERA5/standard: median {b['median']}  "
              f"(kvartiler {b['p25']}-{b['p75']}, spenn {b['min']}-{b['max']}, n={b['n']})")


def print_regional_table(rows, title):
    """
    Sortert STIGENDE paa regional_wp - gjor "gulvet" (den laveste
    regional_wp en positiv okt faktisk hadde) direkte synlig, som er
    akkurat tallet en empirisk regional_wp_min-kalibrering trenger. Se
    modulens docstring for forbeholdet om at datasettet KUN har positive
    okter (kan vise et gulv, ikke bekrefte at lavere er daarlig).
    """
    print(f"\n{'='*90}\n{title}\n{'='*90}")
    ok_rows = [r for r in rows if "feil" not in r]
    header = f"{'dato':<11}{'spot':<16}{'kval':>5}{'tid':>12}  {'valgt (UTC)':<20}{'hs':>7}{'tp':>7}{'regional_wp':>13}"
    print(header)
    pairs = []
    for r in sorted(ok_rows, key=lambda r: r["wp"]):
        print(f"{r['dato']:<11}{r['spot']:<16}{r['kvalitet']:>5}{r['tid']:>12}  "
              f"{r['valgt_tid_utc']:<20}{r['hs']:>7.2f}{r['tp']:>7.1f}{r['wp']:>13.1f}")
        pairs.append((r["wp"], int(r["kvalitet"])))
    for r in rows:
        if "feil" in r:
            print(f"{r['dato']:<11}{r['spot']:<16}{r['kvalitet']:>5}  FEIL: {r['feil']}")

    n = len(pairs)
    if n:
        wps = [p[0] for p in pairs]
        print(f"\n  n={n}  regional_wp (kW/m): min={min(wps):.1f}  median={st.median(wps):.1f}  max={max(wps):.1f}")
        print(f"  Gulvet blant disse {n} positive oktene: {min(wps):.1f} kW/m "
              f"(alle var kvalitet {min(p[1] for p in pairs)}-{max(p[1] for p in pairs)} av 5).")
        if n >= 3:
            try:
                r_coef = st.correlation(wps, [p[1] for p in pairs])
                print(f"  Pearson r (regional_wp vs. kvalitet): {r_coef:.2f} "
                      f"(n={n} - svakt datagrunnlag, KUN positive okter, se docstring)")
            except st.StatisticsError:
                pass
    return pairs


def run_report(sessions_path, bias_sample_n, seed, skip_bias, manual_bias):
    reset_http_stats()
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    sessions = load_sessions(sessions_path)
    print(f"{len(sessions)} okter lest fra {sessions_path}")

    grid_rows = report_spot_grid_cells(spots)

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
    for var, factor in list(bias_factor.items()):
        if factor is None:
            print(f"  ADVARSEL: ingen {var.upper()}-par i det hele tatt - "
                  f"korreksjonsfaktoren settes til 1.0 (ingen korreksjon) for {var}.")
            bias_factor[var] = 1.0

    saltstein_pt = tuple(spots_by_id[BIAS_REFERENCE_ID]["offshore_point"])
    raw = regional_energy_all(sessions, spots_by_id, saltstein_pt, bias=None)
    corrected = regional_energy_all(sessions, spots_by_id, saltstein_pt, bias=bias_factor)

    print_regional_table(raw, "REGIONAL ENERGI - RAA ERA5")
    print_regional_table(corrected, f"REGIONAL ENERGI - SKJEVHETSKORRIGERT (Hs/{bias_factor['hs']:.2f}, Tp/{bias_factor['tp']:.2f})")

    print(f"\n{'='*78}\nOPPSUMMERING\n{'='*78}")
    print(f"  Ingen spot-fysikk kjort (ERA5-Ocean er ikke paalitelig for lokal boelgehoyde")
    print(f"  naer kysten her - se modulens docstring). KUN regional_wp mot observert kvalitet.")
    print(f"  Reanalyse, ikke prognose. Alle elleve oktene var positive - dette kan vise et")
    print(f"  GULV for regional_wp, ikke bekrefte at lavere betyr daarlig. Ingen terskler justert.")

    print(f"\n  HTTP: {_http_stats['n_calls']} kall totalt, "
          f"{_http_stats['n_retried']} maatte proeve paa nytt minst en gang "
          f"(RETRY_DELAYS_S={RETRY_DELAYS_S}).")

    OUT.mkdir(exist_ok=True)
    report_path = OUT / "backtest_report.json"
    report_path.write_text(json.dumps({
        "bias": {k: v for k, v in bias.items() if k != "raw_pairs"} if not skip_bias else bias,
        "grid_cells": grid_rows,
        "raw": raw, "corrected": corrected,
        "http_stats": dict(_http_stats),
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
