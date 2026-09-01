#!/usr/bin/env python3
"""
Skagerrak surf agent.

  python agent.py --mock              kjor mot syntetisk S-kuling (ingen nett)
  python agent.py                     kjor mot ekte data
  python agent.py --shadow            regn og logg, men ikke varsle
  python agent.py --spot slagen       bare ett spot
  python agent.py --explain slagen    full parameterutskrift for ett spot

Output:
  out/forecast.json   oversikt til frontend - alt utenom hours (Lovable henter denne).
                      Inkluderer regional_wp: bolgeeffekt (kW/m, wave_power())
                      ved Saltsteins offshore_point per time - ETT tall for
                      hele regionen, ikke per spot (se run()). Tom liste
                      hvis --spot filtrerer bort saltstein.
  out/spots/<id>.json  fullt hours-array per spot - hentes bare naar detaljer slaas ut
  out/shadow.csv      en rad per spot per time per kjoring - benchmarkgrunnlag
"""

import argparse
import concurrent.futures
import csv
import datetime as dt
import io
import json
import math
import os
import pathlib
import sys
import zoneinfo

import yaml

import describe as D
import ensemble as E
import physics as P
import shadow_schema

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
SPOTS_OUT = OUT / "spots"
SPOTS_OUT.mkdir(exist_ok=True)

OSLO = zoneinfo.ZoneInfo("Europe/Oslo")

# Ren sikkerhetsgrense, ikke den reelle begrensningen i dag - kildene gir
# selv opp lenge for dette. MET Locationforecast faller fra timesoppløsning
# til 6-timers etter ca. 55 timer, og Open-Meteo sine swell/vindsjø-
# partisjoner (EWAM) stopper aa levere tall etter ca. 66 timer, selv om
# forecast_days=7 er det som spørres om (se sources.py sin
# openmeteo_waves() og agent.py sin gather()) - EWAM sitt eget modellrun
# rekker rett og slett ikke saa langt, Open-Meteo fyller bare inn null for
# resten av det spurte vinduet. 200 er derfor bare et tak mot en fremtidig
# kildeoppgradering som faktisk leverer lenger enn ca. 8-9 dager, ikke noe
# som binder med dagens kilder.
MAX_HOURS = 200


# --------------------------------------------------------------- konfig


def load_spots(path=None):
    cfg = yaml.safe_load((path or ROOT / "spots.yaml").read_text())
    defaults = cfg.get("defaults", {})
    spots = []
    for s in cfg["spots"]:
        merged = dict(defaults)
        merged.update(s)
        spots.append(merged)
    return spots, defaults


# --------------------------------------------------------------- scoring


def score_hour(spot, ts, wind, waves, water_cm, computed, lead_h=0.0, regional_wp=None):
    """
    Regn score for ett spot i en time. Returnerer en dict med ALLE
    mellomregninger, ikke bare totalen - det er hele poenget med
    skyggemodus. Du skal kunne se hvorfor den bommet.

    regional_wp: bolgeeffekt (kW/m) ved Saltsteins offshore_point for
    DENNE timen, eller None hvis ukjent (se run() sin regional_wp_by_time).
    Sjekkes mot spot["regional_wp_min"/"regional_wp_max"] (spots.yaml,
    valgfrie) - EN EGEN, UAVHENGIG port paa toppen av min_hs/ideal_hs/
    max_hs over, ikke en erstatning. Ukjent regional_wp porter ALDRI
    igjen (portes ikke), samme forsiktige default som window_ok under.

    Unntak (ordre 2026-09-02, etter backtest mot ekte historikk - se
    rapport til bruker): porten skal IKKE stenge naar spottens EGEN
    lokale sjo dominerer over det regional_wp faktisk maaler (Saltsteins
    offshore-swell). Hvasser sin egen SO-fetch mot Koster/Skagen bygger
    en lokal sjo uavhengig av Skagerrak-swellen - fem historiske timer
    med regional_wp=13.4 (godt under Hvassers egen 38.7) hadde likevel
    6.8 stjerner lokalt derfra. Bypasses naar `source` (klasse A/B) er
    "local_fetch", ELLER (klasse C) local_hs > prop_hs - se
    evaluate_class_ab()/evaluate_class_c() for feltene.
    """
    hs = computed["hs_eff"]
    tp = computed["tp_eff"]
    wdir = computed["dir_eff"]

    ws = wind.get("wind_speed") or 0.0
    wfrom = wind.get("wind_from_direction")

    # retningsvindu (klasse A/B - for klasse C er filtreringen alt gjort i gate)
    if spot["klasse"] in ("A", "B") and wdir is not None:
        window_ok = P.in_window(wdir, spot["swell_window"])
        wf = P.window_factor(wdir, spot) if hasattr(P, "window_factor") else 1.0
    else:
        window_ok, wf = True, 1.0

    q_size = P.size_quality(hs, spot["min_hs"], spot["ideal_hs"], spot["max_hs"])
    q_period = P.period_quality(tp, spot["min_tp"])
    q_wind_raw, wind_label = (
        P.wind_quality(ws, wfrom, spot["facing"]) if wfrom is not None else (0.5, "ukjent")
    )
    q_wind = P.apply_wind_weight(q_wind_raw, spot.get("wind_weight", 1.0))
    q_water = P.water_level_quality(
        water_cm if water_cm is not None else spot["water_optimal_cm"],
        spot["water_optimal_cm"],
        spot["water_sensitivity_cm"],
    )

    if not window_ok:
        q_size = 0.0

    wp_min = spot.get("regional_wp_min")
    wp_max = spot.get("regional_wp_max")
    regional_gate_would_close = regional_wp is not None and (
        (wp_min is not None and regional_wp < wp_min)
        or (wp_max is not None and regional_wp > wp_max)
    )
    local_hs, prop_hs = computed.get("local_hs"), computed.get("prop_hs")
    local_dominant = (
        computed.get("source") == "local_fetch"
        or (local_hs is not None and prop_hs is not None and local_hs > prop_hs)
    )
    regional_gate_bypassed = regional_gate_would_close and local_dominant
    regional_gate_closed = regional_gate_would_close and not local_dominant
    if regional_gate_closed:
        q_size = 0.0

    score = 100.0 * q_size * q_period * q_wind * q_water

    # beskrivende, ikke en scorekomponent - se physics.swell_fraction() for
    # forbeholdet: andelen er tvetydig naar sjoen er nesten flat (nevneren
    # kollapser), saa funksjonen returnerer None (IKKE 0.0 - det ville
    # paastaatt "0 % swell") under hs_eff 0.5 m eller uten partisjonsdata.
    # tidy() lenger ned i run() lar None vaere None, og JSON/CSV-skriving
    # haandterer det uten aa krasje (json.dumps -> null, csv -> tomt felt)
    # - bruk swell_hs_abs for aa faktisk skille stor swell fra rester.
    swell_andel = P.swell_fraction(computed.get("swell_hs"), computed.get("windsea_hs"), hs)

    ens = E.evaluate(spot, computed, wind, lead_h, water_cm, waves)

    return {
        "time": ts,
        "lead_h": round(lead_h, 1),
        "score": round(score, 1),
        # de to tallene som vises i UI-et
        "p_surf": ens["p_surf"],
        "p_good": ens["p_good"],
        "stars": ens["stars"],
        "stars_p10": ens["stars_p10"],
        "stars_p90": ens["stars_p90"],
        "confidence": ens["confidence"],
        "model_spread": ens["model_spread"],
        # resultat
        "hs_eff": round(hs, 2),
        "tp_eff": round(tp, 1),
        "dir_eff": round(wdir, 0) if wdir is not None else None,
        # beskrivende, ikke en scorekomponent - se physics.wave_power()
        "power_kw": round(P.wave_power(hs, tp)),
        # beskrivende, ikke en scorekomponent - se physics.swell_fraction()
        "swell_andel": swell_andel,
        # raa swell-hoyde i meter, UTEN normalisering - i motsetning til
        # swell_andel er denne entydig naar sjoen er nesten flat (2.1 m mot
        # 0.24 m er ikke det samme, selv om andelen kan bli lik)
        "swell_hs_abs": computed.get("swell_hs"),
        # delscorer - dette er knappene du skrur pa
        "q_size": round(q_size, 3),
        "q_period": round(q_period, 3),
        "q_wind": round(q_wind, 3),
        "q_wind_raw": round(q_wind_raw, 3),
        "wind_weight": spot.get("wind_weight", 1.0),
        "q_water": round(q_water, 3),
        "window_ok": window_ok,
        # regional energi-port (se spot["regional_wp_min"/"regional_wp_max"]
        # i spots.yaml og docstringen over) - regional_wp er None naar den
        # ikke er kjent for denne timen (porter da aldri igjen)
        "regional_wp": regional_wp,
        "regional_gate_closed": regional_gate_closed,
        # porten VILLE stengt, men lokal sjo dominerer (se docstringen) -
        # egen telling for aa se hvor ofte unntaket faktisk brukes
        "regional_gate_bypassed": regional_gate_bypassed,
        # inngangsdata
        "wind_speed": ws,
        "wind_from": wfrom,
        "wind_label": wind_label,
        "water_cm": water_cm,
        # kilder / mellomregning
        **{k: v for k, v in computed.items() if k not in ("hs_eff", "tp_eff", "dir_eff")},
    }


def evaluate_class_ab(spot, times, wind_series, wave_series):
    """Klasse A og B: les modellen direkte, sjekk lokal fetch som supplement."""
    rows = []
    wind_list = [wind_series.get(t, {}) for t in times]

    for i, ts in enumerate(times):
        w = wave_series.get(ts, {})
        model_hs = w.get("hs") or 0.0
        model_tp = w.get("tp") or 0.0
        model_dir = w.get("wave_from_direction")

        computed = {
            "source": "model",
            "model_hs": model_hs,
            "model_tp": model_tp,
            "model_dir": model_dir,
            "hs_eff": model_hs,
            "tp_eff": model_tp,
            "dir_eff": model_dir,
            "tp_source": w.get("tp_source"),
            "hs_met": w.get("hs_met"),
            "hs_openmeteo": w.get("hs_openmeteo"),
            "hs_dmi": w.get("hs_dmi"),
            "swell_hs": w.get("swell_hs"),
            "swell_tp": w.get("swell_tp"),
            "swell_dir": w.get("swell_dir"),
            "windsea_hs": w.get("windsea_hs"),
            "windsea_tp": w.get("windsea_tp"),
            "windsea_dir": w.get("windsea_dir"),
            "partisjon_kilde": w.get("partisjon_kilde"),
            "local_hs": None,
            "local_tp": None,
            "local_wind_mean": 0.0,
            "local_fetch_km": 0.0,
            "local_duration_h": 0,
            "local_dir": None,
            "gate_hs": 0.0,
            "gate_tp": 0.0,
            "gate_dir": None,
        }

        # klasse B: modellen oppløser ikke skjaergarden - sjekk om lokal
        # fetch gir mer enn modellen sier
        if spot.get("local_fetch_km"):
            loc = P.build_local_sea(wind_list, spot["local_fetch_km"], i)
            computed["local_hs"] = round(loc["hs"], 2)
            computed["local_tp"] = round(loc["tp"], 1)
            computed["local_fetch_km"] = round(loc.get("fetch_km", 0), 1)
            computed["local_duration_h"] = loc.get("duration_h")
            computed["local_limited_by"] = loc.get("limited_by")
            if loc["hs"] > model_hs and P.in_window(loc["direction"], spot["swell_window"]):
                computed.update(
                    source="local_fetch",
                    hs_eff=loc["hs"],
                    tp_eff=loc["tp"],
                    dir_eff=loc["direction"],
                )
        rows.append((ts, computed))
    return rows


def evaluate_class_c(spot, times, wind_series, gate_wave_series):
    """
    Klasse C: modellene duger ikke inne i fjorden. Regn selv.

      lokal vindsjo (fetch + varighet)
      + propagert S-komponent fra munningen (retningsfiltrert, forsinket)
    """
    rows = []
    wind_list = [wind_series.get(t, {}) for t in times]
    index_of = {t: i for i, t in enumerate(times)}

    for i, ts in enumerate(times):
        loc = P.build_local_sea(wind_list, spot["fetch_km"], i)
        w = gate_wave_series.get(ts, {})

        # finn den timen ved munningen hvis energi ankommer NA
        gate_hs = gate_tp = 0.0
        gate_dir = None
        prop_hs = prop_tp = 0.0
        energy_frac = 0.0
        delay = 0.0
        gate_time = None

        for back in range(0, 8):
            j = i - back
            if j < 0:
                break
            g = gate_wave_series.get(times[j], {})
            g_tp = g.get("tp") or 0.0
            if g_tp <= 0:
                continue
            d = P.travel_time_h(spot["gate"]["distance_km"], g_tp)
            if abs(d - back) <= 0.6:
                gate_hs = g.get("hs") or 0.0
                gate_tp = g_tp
                gate_dir = g.get("wave_from_direction")
                gate_time = times[j]
                if gate_dir is not None:
                    prop_hs, prop_tp, energy_frac, delay = P.propagate_through_gate(
                        gate_hs, gate_tp, gate_dir, spot
                    )
                break

        hs_eff, tp_eff = P.combine(loc["hs"], loc["tp"], prop_hs, prop_tp)
        dir_eff = loc["direction"] if loc["hs"] >= prop_hs else spot["gate"]["bearing_deg"]

        rows.append((ts, {
            "source": "local+gate",
            "model_hs": 0.0,
            "model_tp": 0.0,
            "model_dir": None,
            "hs_eff": hs_eff,
            "tp_eff": tp_eff,
            "dir_eff": dir_eff,
            "tp_source": w.get("tp_source"),
            "hs_met": w.get("hs_met"),
            "hs_openmeteo": w.get("hs_openmeteo"),
            "hs_dmi": w.get("hs_dmi"),
            # hvilken Open-Meteo-modell GATENS lesning kom fra denne
            # timen ("ewam"/"global"/None) - IKKE det samme som
            # swell_hs/windsea_hs under (de er fysisk utledet, ikke
            # hentet direkte herfra), men relevant for hvor mye aa stole
            # paa den PROPAGERTE komponenten - se ensemble.py.
            "partisjon_kilde": w.get("partisjon_kilde"),
            # lokal vindsjo
            "local_hs": round(loc["hs"], 2),
            "local_tp": round(loc["tp"], 1),
            "local_dir": loc["direction"],
            "local_fetch_km": loc.get("fetch_km", 0) or 0.0,
            "local_wind_mean": loc.get("mean_wind", 0) or 0.0,
            "local_duration_h": loc.get("duration_h"),
            "local_limited_by": loc.get("limited_by"),
            # propagert fra munningen
            "gate_time": gate_time,
            "gate_hs": round(gate_hs, 2),
            "gate_tp": round(gate_tp, 1),
            "gate_dir": gate_dir,
            "gate_energy_frac": round(energy_frac, 3),
            "gate_delay_h": round(delay, 1),
            "prop_hs": round(prop_hs, 2),
            # samme feltnavn som klasse A/B (evaluate_class_ab), saa
            # frontenden kan lese swell_hs/windsea_hs uansett klasse -
            # her er prop_hs swellkomponenten (fra munningen) og local_hs
            # vindsjoen. Originalene (prop_hs/local_hs) beholdes uendret.
            "swell_hs": round(prop_hs, 2),
            "windsea_hs": round(loc["hs"], 2),
        }))
    return rows


def daily_summary(hours, days=7):
    """
    Slaa sammen timene til en post per dogn, lokal tid (Europe/Oslo).
    Det er alt 7-dagersgrafen i frontenden trenger, saa den slipper aa
    laste hele hours-arrayet bare for aa tegne en oversiktsgraf.
    """
    buckets = {}
    for h in hours:
        local_date = dt.datetime.fromisoformat(h["time"]).astimezone(OSLO).date()
        b = buckets.setdefault(local_date, {
            "max_p_surf": 0.0, "max_stars": 0.0, "max_hs": 0.0, "max_power_kw": 0.0,
        })
        b["max_p_surf"] = max(b["max_p_surf"], h.get("p_surf") or 0.0)
        b["max_stars"] = max(b["max_stars"], h.get("stars") or 0.0)
        b["max_hs"] = max(b["max_hs"], h.get("hs_eff") or 0.0)
        b["max_power_kw"] = max(b["max_power_kw"], h.get("power_kw") or 0.0)
    return [
        {
            "date": d.isoformat(),
            "max_p_surf": buckets[d]["max_p_surf"],
            "max_stars": buckets[d]["max_stars"],
            "max_hs": round(buckets[d]["max_hs"], 1),
            "max_power_kw": round(buckets[d]["max_power_kw"]),
        }
        for d in sorted(buckets)[:days]
    ]


def best_day(daily):
    """
    Dognet i uka med hoyest stars x p_surf - det frontenden trenger for
    aa sortere spots pa "beste dag", ikke bare "beste time noensinne".
    """
    if not daily:
        return None
    d = max(daily, key=lambda x: x["max_stars"] * x["max_p_surf"])
    return {"date": d["date"], "p_surf": d["max_p_surf"],
            "stars": d["max_stars"], "hs": d["max_hs"]}


# --------------------------------------------------------------- vinduer


def find_windows(hours, spot):
    """
    Finn sammenhengende perioder som passerer BEGGE terskler:
    sannsynlighet for surf, og forventet kvalitet i stjerner.

    Aa kreve begge er poenget. Hoy sannsynlighet for daarlige bolger er
    ikke et varsel verdt aa sende, og 9 stjerner med 15 % sannsynlighet
    er en rekognosering, ikke en plan.
    """
    min_p = spot["alert_min_p_surf"]
    min_stars = spot["alert_min_stars"]
    min_len = spot["min_window_hours"]

    windows, cur = [], []
    for h in hours:
        if h["p_surf"] >= min_p and (h["stars"] or 0) >= min_stars:
            cur.append(h)
        else:
            if len(cur) >= min_len:
                windows.append(cur)
            cur = []
    if len(cur) >= min_len:
        windows.append(cur)

    out = []
    for w in windows:
        best = max(w, key=lambda x: ((x["stars"] or 0) * x["p_surf"]))
        out.append({
            "id": f"{w[0]['time'][:13]}_{len(w)}h",
            "start": w[0]["time"],
            "end": w[-1]["time"],
            "hours": len(w),
            "stars": best["stars"],
            "stars_p10": best["stars_p10"],
            "stars_p90": best["stars_p90"],
            "p_surf": best["p_surf"],
            "p_good": best["p_good"],
            "confidence": best["confidence"],
            "lead_h": best["lead_h"],
            "peak_score": best["score"],
            "peak_time": best["time"],
            "peak_hs": best["hs_eff"],
            "peak_tp": best["tp_eff"],
            "max_power_kw": max(h.get("power_kw") or 0 for h in w),
            "peak_wind": f"{best['wind_speed']:.0f} m/s {best['wind_from']:.0f}deg ({best['wind_label']})"
            if best["wind_from"] is not None else "?",
            "why": explain(best),
            "bullets": D.describe({
                "name": spot["name"], "klasse": spot["klasse"],
                "kalibrert": spot.get("kalibrert", False),
                "access_warning": spot.get("access_warning"),
                "params": {"facing": spot["facing"],
                           "swell_window": spot["swell_window"],
                           "gate": spot.get("gate")},
            }, None, w),
        })
    return out


def explain(h):
    """Setningen som forklarer HVORFOR agenten tror det blir surf."""
    parts = []
    if h.get("stars"):
        parts.append(
            f"{h['stars']}/10 forventet (spenn {h.get('stars_p10')}-{h.get('stars_p90')}), "
            f"{h['p_surf']:.0f} % sannsynlighet, konfidens {h.get('confidence')}"
        )
    if h.get("source") == "local+gate":
        loc, prop = h.get("local_hs", 0) or 0, h.get("prop_hs", 0) or 0
        if prop > loc:
            parts.append(
                f"drevet av swell fra munningen ({h.get('gate_hs')} m -> {prop} m, "
                f"{int(100 * (h.get('gate_energy_frac') or 0))}% av energien slipper opp)"
            )
        else:
            parts.append(
                f"drevet av lokal fetch ({h.get('local_wind_mean')} m/s over "
                f"{h.get('local_fetch_km')} km i {h.get('local_duration_h')} t, "
                f"{h.get('local_limited_by')}-begrenset)"
            )
    parts.append(
        f"vind {h['wind_speed']:.0f} m/s fra {h['wind_from']:.0f} grader = "
        f"{h['wind_label']}" if h.get("wind_from") is not None else "vind ukjent"
    )
    weakest = min(
        [("storrelse", h["q_size"]), ("periode", h["q_period"]),
         ("vind", h["q_wind"]), ("vannstand", h["q_water"])],
        key=lambda x: x[1],
    )
    parts.append(f"svakeste ledd: {weakest[0]} ({weakest[1]:.2f})")
    if (h.get("model_spread") or 0) > 0.20:
        parts.append(f"modellene er uenige ({100*h['model_spread']:.0f} % sprik) "
                     f"- sjekk pa nytt naermere tida")
    return "; ".join(parts)


# --------------------------------------------------------------- varsling


STATE_FILE = OUT / "alert_state.json"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def should_notify(state, spot, window):
    """
    Dedup. Send en gang naar vinduet dukker opp, og en gang til hvis
    kvaliteten har flyttet seg med minst 1.5 stjerner eller
    sannsynligheten med 20 prosentpoeng. Ellers hold kjeft.
    """
    key = f"{spot['id']}:{window['id']}"
    prev = state.get(key)
    if prev is None:
        return True, key, "nytt vindu"
    d_stars = abs((window["stars"] or 0) - (prev.get("stars") or 0))
    d_p = abs(window["p_surf"] - prev.get("p_surf", 0))
    if d_stars >= 1.5:
        return True, key, f"kvalitet endret {prev.get('stars')} -> {window['stars']}"
    if d_p >= 20:
        return True, key, f"sannsynlighet endret {prev.get('p_surf'):.0f} -> {window['p_surf']:.0f} %"
    return False, key, None


def notify(spot, window, state, dry_run=False):
    send, key, reason = should_notify(state, spot, window)
    state[key] = {"stars": window["stars"], "p_surf": window["p_surf"],
                  "seen_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if not send:
        return

    stars_txt = "*" * int(round(window["stars"] or 0))
    title = f"{spot['name']} {window['stars']}/10  {window['p_surf']:.0f}%"
    body = "\n".join(x for x in [
        f"{stars_txt}",
        f"{window['start'][5:16].replace('T', ' ')}-{window['end'][11:16]} UTC "
        f"({window['hours']} t) - {window['lead_h']:.0f} t fram i tid",
        f"Sannsynlighet for surf: {window['p_surf']:.0f} %   "
        f"(for 6+ stjerner: {window['p_good']:.0f} %)",
        f"Forventet: {window['stars']}/10, spenn {window['stars_p10']}-{window['stars_p90']}",
        f"Hs {window['peak_hs']} m @ {window['peak_tp']} s, vind {window['peak_wind']}",
        "",
        window["why"],
        "",
        (f"OBS: {spot['access_warning'].strip()}" if spot.get("access_warning") else ""),
        (f"({reason})" if reason != "nytt vindu" else ""),
    ] if x is not None).strip()

    topic = os.environ.get("NTFY_TOPIC")
    if dry_run or not topic:
        print(f"\n[VARSEL{' (skygge)' if dry_run else ''}] {title}\n{body}\n")
        return

    import requests
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "high" if (window["stars"] or 0) >= 7 else "default",
                "Tags": "ocean",
            }, timeout=15)
    except Exception as exc:  # noqa: BLE001
        print(f"varsling feilet: {exc}")


# --------------------------------------------------------------- kjoring


def gather(spot, mock=None):
    """Hent alle datakilder for ett spot. Returnerer (wind, waves, water, errors)."""
    if mock is not None:
        return mock["wind"], mock["waves"], mock.get("water", {}), []

    import sources as S

    errors = []
    if spot["klasse"] == "C":
        wind_pt = (spot["lat"], spot["lon"])
        wave_pt = (spot["gate"]["lat"], spot["gate"]["lon"])
    else:
        wind_pt = (spot["lat"], spot["lon"])
        wave_pt = tuple(spot["offshore_point"])

    wind, e = S.safe(S.met_wind, *wind_pt, label="met_wind")
    if e:
        errors.append(e)

    met_w, e = S.safe(S.met_waves, *wave_pt, label="met_waves")
    if e:
        errors.append(e)
    om_w, e = S.safe(S.openmeteo_waves, *wave_pt, label="openmeteo")
    if e:
        errors.append(e)

    # MET er primaerkilde for Hs/retning, Open-Meteo fyller inn Tp
    waves = {}
    for ts in set(met_w) | set(om_w):
        m, o = met_w.get(ts, {}), om_w.get(ts, {})
        hs = m.get("hs") if m.get("hs") is not None else o.get("hs")
        tp = m.get("tp") if m.get("tp") is not None else o.get("tp")
        if m.get("tp") is not None:
            tp_source = "met"
        elif o.get("tp") is not None:
            tp_source = "openmeteo"
        else:
            tp_source = None
        # MET Oceanforecast 2.0 leverer ikke alltid periode, og Open-Meteo
        # kan mangle den samme timen. Estimer fra Hs fremfor aa la tp falle
        # til 0 (som ville nullstilt period_quality og dermed hele scoren).
        # Gulv paa 4.5 s: lav Hs med lang periode er swell, ikke vindsjo -
        # den rene Hs-baserte formelen undervurderer systematisk der.
        if tp is None and hs is not None:
            tp = max(4.5, 4.6 * hs ** 0.4)
            tp_source = "estimated"
        waves[ts] = {
            "hs": hs,
            "tp": tp,
            "wave_from_direction": (
                m.get("wave_from_direction")
                if m.get("wave_from_direction") is not None
                else o.get("wave_from_direction")
            ),
            "hs_met": m.get("hs"),
            "hs_openmeteo": o.get("hs"),
            "tp_source": tp_source,
            # partisjonert swell/vindsjo - kun Open-Meteo leverer dette (MET
            # gjor det ikke, se met_waves()). Kan vaere None paa flate dager.
            "swell_hs": o.get("swell_hs"),
            "swell_tp": o.get("swell_tp"),
            "swell_dir": o.get("swell_dir"),
            "windsea_hs": o.get("windsea_hs"),
            "windsea_tp": o.get("windsea_tp"),
            "windsea_dir": o.get("windsea_dir"),
            # "ewam", "global" eller None - hvilken Open-Meteo-modell
            # partisjonene over faktisk kom fra, se sources.py sin
            # openmeteo_waves() og ensemble.py sin
            # GLOBAL_MODEL_HS_REL_PENALTY.
            "partisjon_kilde": o.get("partisjon_kilde"),
        }

    if os.environ.get("DMI_API_KEY"):
        dmi, e = S.safe(S.dmi_waves, *wave_pt, label="dmi")
        if e:
            errors.append(e)
        for ts, rec in dmi.items():
            if ts in waves:
                waves[ts]["hs_dmi"] = rec.get("hs")
                waves[ts]["tp_dmi"] = rec.get("tp")

    water, e = S.safe(S.kartverket_water_level, spot["lat"], spot["lon"], label="kartverket")
    if e:
        errors.append(e)

    return wind, waves, water, errors


def run(args):
    spots, defaults = load_spots()
    if args.spot:
        spots = [s for s in spots if s["id"] in args.spot]
        if not spots:
            sys.exit(f"Fant ingen spots med id {args.spot}")

    mock = build_mock(args.mock) if args.mock else None
    state = load_state()
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    results = []

    # gather() er nettverksbundet og uavhengig per spot - hent parallelt.
    # ThreadPoolExecutor.map beholder rekkefolgen paa resultatene uansett
    # hvilken traad som blir ferdig forst. Feilhaandtering skjer fortsatt
    # inne i gather() via S.safe(), saa dette endrer bare hvor raskt
    # kallene skytes av.
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        gathered = list(pool.map(lambda spot: gather(spot, mock), spots))

    # ETT tall for hele regionen (ikke per spot) - bolgeeffekt ved Saltsteins
    # offshore_point, samme referanse brukerne allerede kjenner fra
    # surf-forecast sitt Saltstein-tall (se regional_wp i forecast.json og
    # regional_wp_min/max i spots.yaml). Bygges i en EGEN forhaands-passering,
    # UAVHENGIG av rekkefolgen spots kommer i under - skulle den regnes ut
    # inne i hovedloekka naar Saltstein sitt spot behandles der, ville
    # spots FOR Saltstein i lista aldri faa noen regional_wp aa porte mot.
    # Tom dict (og dermed apen port for alle) hvis --spot filtrerer bort
    # saltstein - ingen data aa hente den fra da.
    regional_wp_by_time = {}
    for spot, (wind, waves, water, errors) in zip(spots, gathered):
        if spot["id"] != "saltstein":
            continue
        for ts, w in waves.items():
            hs, tp = w.get("hs"), w.get("tp")
            if hs is not None and tp is not None:
                regional_wp_by_time[ts] = round(P.wave_power(hs, tp), 1)
        break

    # payload-feltet - forblir tom liste hvis saltstein ikke er blant spots
    # (se merknaden over regional_wp_by_time)
    regional_wp = []

    for spot, (wind, waves, water, errors) in zip(spots, gathered):
        if not wind:
            results.append({"id": spot["id"], "name": spot["name"],
                            "error": "ingen vinddata", "sources": errors})
            continue

        times = sorted(set(wind) & set(waves)) if waves else sorted(wind)
        times = [t for t in times if t >= now.isoformat()][:MAX_HOURS]
        if not times:
            times = sorted(wind)[:MAX_HOURS]

        if spot["klasse"] == "C":
            computed = evaluate_class_c(spot, times, wind, waves)
        else:
            computed = evaluate_class_ab(spot, times, wind, waves)

        hours = []
        for ts, c in computed:
            lead = (dt.datetime.fromisoformat(ts) - now).total_seconds() / 3600.0
            hours.append(score_hour(
                spot, ts, wind.get(ts, {}), waves.get(ts, {}),
                (water.get(ts) or {}).get("level_cm"), c, lead_h=lead,
                regional_wp=regional_wp_by_time.get(ts)))
        windows = find_windows(hours, spot)
        daily = daily_summary(hours)

        if spot["id"] == "saltstein":
            # samme tall som regional_wp_by_time - bygd som en egen liste
            # (ikke bare dict.items()) fordi forecast.json trenger den
            # begrenset til OG i samme rekkefolge som Saltstein sine egne
            # (now-filtrerte, MAX_HOURS-kappede) times, ikke ALT som matte
            # ligge i waves-dataen.
            regional_wp = [
                {"time": ts, "wp": regional_wp_by_time[ts]}
                for ts in times if ts in regional_wp_by_time
            ]

        results.append({
            "id": spot["id"],
            "name": spot["name"],
            "klasse": spot["klasse"],
            "lat": spot["lat"],
            "lon": spot["lon"],
            "kalibrert": spot.get("kalibrert", False),
            "boat": spot.get("boat", False),
            "drive_min": spot.get("drive_min"),
            "access_warning": spot.get("access_warning"),
            "max_score": max((h["score"] for h in hours), default=0),
            "best_stars": max((h["stars"] or 0 for h in hours), default=0) or None,
            "best_p_surf": max((h["p_surf"] for h in hours), default=0),
            "windows": windows,
            "daily": daily,
            "best_day": best_day(daily),
            "hours": hours,
            "sources": errors,
            "params": {k: spot.get(k) for k in
                       ("facing", "swell_window", "min_hs", "ideal_hs", "max_hs",
                        "min_tp", "wind_weight", "alert_min_p_surf",
                        "alert_min_stars", "gate")},
        })

        for w in windows:
            notify(spot, w, state, dry_run=args.shadow or args.mock)

        if args.explain and spot["id"] in args.explain:
            print_explain(spot, hours)

    def tidy(o):
        if isinstance(o, float):
            return round(o, 3)
        if isinstance(o, dict):
            return {k: tidy(v) for k, v in o.items()}
        if isinstance(o, list):
            return [tidy(v) for v in o]
        return o

    payload = {
        "generated_at": now.isoformat(),
        "mode": "mock" if args.mock else ("shadow" if args.shadow else "live"),
        # regional bolgeeffekt (kW/m) ved Saltsteins offshore_point, ETT
        # tall for hele regionen - ikke en per-spot verdi. Se merknaden ved
        # regional_wp lenger opp i denne funksjonen.
        "regional_wp": regional_wp,
        "spots": sorted(results, key=lambda r: -(
            (r.get("best_stars") or 0) * (r.get("best_p_surf") or 0))),
    }
    payload = tidy(payload)

    if not args.mock:
        STATE_FILE.write_text(json.dumps(state, indent=2))
        append_shadow_log(payload)

    # fullt hours-array per spot til egen fil - hovedfila (og dermed
    # nedlastingen i frontenden) trenger den ikke, bare detaljvisningen gjor
    for spot in payload["spots"]:
        hours = spot.pop("hours", None)
        if hours is None:
            continue
        (SPOTS_OUT / f"{spot['id']}.json").write_text(json.dumps(
            {"id": spot["id"], "generated_at": payload["generated_at"], "hours": hours},
            indent=2, ensure_ascii=False))

    (OUT / "forecast.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"\n{'SPOT':<28}{'STJ':>5}{'P%':>5}  VINDUER")
    for r in payload["spots"]:
        flag = "*" if r.get("kalibrert") else " "
        wins = ", ".join(
            f"{w['start'][5:16].replace('T',' ')} {w['hours']}t "
            f"{w['stars']}/10 {w['p_surf']:.0f}% [{w['confidence']}]"
            for w in r.get("windows", [])) or "-"
        print(f"{flag}{r['name']:<27}{(r.get('best_stars') or 0):>5.1f}"
              f"{r.get('best_p_surf', 0):>5.0f}  {wins}")
    print(f"\n-> {OUT/'forecast.json'}\n-> {OUT/'shadow.csv'}")


def print_explain(spot, hours):
    print(f"\n{'='*78}\n{spot['name']}  (klasse {spot['klasse']})\n{'='*78}")
    keys = ["time", "score", "hs_eff", "tp_eff", "wind_speed", "wind_from",
            "wind_label", "local_hs", "prop_hs", "gate_hs", "gate_energy_frac",
            "local_fetch_km", "local_duration_h", "local_limited_by",
            "q_size", "q_period", "q_wind"]
    print("  ".join(f"{k[:11]:>11}" for k in keys))
    for h in hours[:48]:
        row = []
        for k in keys:
            v = h.get(k)
            if k == "time":
                v = v[5:16]
            row.append(f"{str(v)[:11]:>11}" if v is not None else f"{'-':>11}")
        print("  ".join(row))


def append_shadow_log(payload):
    """En rad per spot per time. Dette er benchmarkgrunnlaget ditt.

    Header-deteksjon (ordre 2026-09-02 - funn: 28 532 rader paa
    data-grenen manglet header helt). Rotaarsak: forecast.yml sitt
    "Hent tilstand fra data-grenen"-steg gjor
    `git show data:out/shadow.csv > out/shadow.csv 2>/dev/null || true` -
    `>`-omdirigeringen OPPRETTER/TOMMER out/shadow.csv FOR kommandoen
    kjorer, selv naar git show feiler (f.eks. forste kjoring noensinne,
    for data-grenen fantes). Path.exists() var derfor True paa en tom,
    headerlos fil helt fra dag 1, og hver kjoring siden har arvet og
    forlenget den samme headerlose fila. Sjekker na FORSTE LINJE i
    stedet for bare om filen finnes - fanger baade "helt tom fil" og
    "fil med data, men uten header" (reparerer sistnevnte ved aa sette
    inn header forrest, i stedet for aa hoppe over den for alltid).

    Feltlista er FIELDS i shadow_schema.py, ikke lokal her lenger - se
    den modulens docstring for append-only-kontrakten
    (test_shadow_schema.py haandhever den)."""
    path = OUT / "shadow.csv"
    fields = shadow_schema.FIELDS

    existing = path.read_bytes() if path.exists() else b""
    has_header = existing[:7] == b"run_at,"
    if existing and not has_header:
        # data uten header - sett headeren FORREST, ikke bakerst
        header_buf = io.StringIO()
        csv.DictWriter(header_buf, fieldnames=fields).writeheader()
        path.write_bytes(header_buf.getvalue().encode("utf-8") + existing)

    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not existing:
            w.writeheader()
        for spot in payload["spots"]:
            for h in spot.get("hours", []):
                w.writerow({"run_at": payload["generated_at"],
                            "spot": spot["id"], **h})


# --------------------------------------------------------------- mockdata


def build_mock(scenario="storm"):
    """
    Syntetisk scenario: klassisk S-kuling med frontpassasje.
    T+0..T+13   S 18 m/s, bygger
    T+14        dreier til V, loyer
    T+16        NV 8 m/s   <- her skal fjordspottene score
    T+22        N 4 m/s, sjoen dod
    Brukes til aa teste hele kjeden uten nettverk.
    """
    start = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    wind, waves = {}, {}
    # "moderat" = typisk vinterdag: SV 11 m/s, dreier NV. Slik ser 80 % av
    # surfbare dager ut. "storm" = den store S-kulingen som vekker fjorden.
    peak = 18.0 if scenario == "storm" else 11.0
    hs_peak = 4.0 if scenario == "storm" else 1.9
    src_dir = 185 if scenario == "storm" else 215

    for h in range(0, 72):
        ts = (start + dt.timedelta(hours=h)).isoformat()
        if h < 14:
            spd, d = min(peak, 6 + h * 1.2), src_dir
        elif h < 16:
            spd, d = 14, 250
        elif h < 22:
            spd, d = 9, 305
        else:
            spd, d = 4, 340
        wind[ts] = {"wind_speed": spd, "wind_from_direction": d,
                    "pressure": 985 + max(0, h - 14) * 1.5}

        # bolger ved munningen: bygger med vinden, faller etter dreiningen
        if h < 16:
            hs = min(hs_peak, 0.4 + h * hs_peak / 15)
            tp = min(9.0 if scenario == "storm" else 7.0, 4.5 + h * 0.32)
            wdir = src_dir + 2
        else:
            hs = max(0.4, hs_peak - (h - 16) * hs_peak * 0.11)
            tp = max(5.0, (9.0 if scenario == "storm" else 7.0) - (h - 16) * 0.3)
            wdir = src_dir + 10
        waves[ts] = {"hs": hs, "tp": tp, "wave_from_direction": wdir}

    return {"wind": wind, "waves": waves, "water": {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", nargs="?", const="storm", choices=["storm", "moderat"],
                    help="kjor mot syntetisk scenario uten nettverk")
    ap.add_argument("--shadow", action="store_true", help="regn og logg, ikke varsle")
    ap.add_argument("--spot", nargs="*", help="begrens til gitte spot-id-er")
    ap.add_argument("--explain", nargs="*", default=[], help="full parametertabell")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
