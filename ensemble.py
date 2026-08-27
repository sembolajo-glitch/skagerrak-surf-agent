"""
Ensemble: sannsynlighet for surf, og forventet kvalitet gitt at det blir surf.

Hvorfor to tall og ikke ett:

  SANNSYNLIGHET svarer paa "blir det i det hele tatt bolger her".
  Den drives av USIKKERHET - hvor langt fram i tid varselet er, hvor mye
  modellene er uenige, og hvor mye slark det er i spotens egne parametere.

  KVALITET svarer paa "hvis det blir bolger, hvor bra blir de".
  Den er BETINGET - regnet bare over de ensemblemedlemmene som faktisk
  gir surf. Et spot kan godt ha 25 % sannsynlighet og 9 stjerner: sjelden,
  men episk naar det treffer.

Aa slaa disse sammen til ett tall ville skjult nettopp den forskjellen,
og det er den forskjellen som avgjor om du setter deg i bilen.

Usikkerhetskildene som samples:
  1. vindstyrke og -retning        vokser med varslingslengde
  2. Hs og retning ved munningen   vokser med varslingslengde, OG med
                                   hvor uenige MET / EWAM / DMI er
  3. transmission og sektorbredde  strukturell, uavhengig av tid.
                                   Storre for ukalibrerte spots.

Ensemblet er deterministisk (fast seed). Samme inndata gir samme
sannsynlighet hver kjoring - ellers ville tallet flakke mellom kjoringer
paa uendret varsel, og du ville sluttet aa stole paa det.
"""

import math
import random
import statistics as st

import physics as P

N_MEMBERS = 40
SEED = 20261114


# --------------------------------------------------------------- usikkerhet


def sigmas(lead_h, spot, model_spread=0.0):
    """
    Standardavvik for hver usikkerhetskilde, gitt varslingslengde i timer.

    Tallene er kalibrert mot typisk MEPS/ECMWF-ytelse i Skagerrak:
    vindfeil rundt 10 % paa analysetidspunktet, voksende til 25-30 %
    paa fem dogn. Juster i spots.yaml under `uncertainty` hvis din
    egen logg sier noe annet.
    """
    u = spot.get("uncertainty", {})
    lead = max(0.0, lead_h)
    strukturell = 1.0 if spot.get("kalibrert") else 2.0

    return {
        "wind_scale": u.get("wind_rel", 0.10) + 0.0013 * lead,
        "wind_dir": u.get("wind_dir_deg", 8.0) + 0.22 * lead,
        "gate_scale": math.hypot(u.get("hs_rel", 0.15) + 0.0013 * lead, model_spread),
        "gate_dir": u.get("wave_dir_deg", 10.0) + 0.18 * lead,
        "transmission": u.get("transmission_rel", 0.20) * strukturell,
        "sector": u.get("sector_deg", 3.5) * strukturell,
    }


def model_spread(wave_rec):
    """
    Relativt sprik mellom modellene i samme time. Hvis MET sier 2.0 m og
    EWAM sier 3.0 m, er det ekte informasjon om usikkerhet - da skal
    sannsynligheten ned, uansett hva medianen er.
    """
    vals = [wave_rec.get(k) for k in ("hs_met", "hs_openmeteo", "hs_dmi")]
    vals = [v for v in vals if isinstance(v, (int, float)) and v > 0]
    if len(vals) < 2:
        return 0.0
    m = st.mean(vals)
    return min(0.6, st.pstdev(vals) / m) if m > 0 else 0.0


# --------------------------------------------------------------- medlemmer


def draw_members(sig, n=N_MEMBERS, seed=SEED):
    """Fast seed -> reproduserbart ensemble."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append({
            "wind_scale": max(0.35, rng.gauss(1.0, sig["wind_scale"])),
            "wind_dir": rng.gauss(0.0, sig["wind_dir"]),
            "gate_scale": max(0.25, rng.gauss(1.0, sig["gate_scale"])),
            "gate_dir": rng.gauss(0.0, sig["gate_dir"]),
            "transmission": max(0.3, rng.gauss(1.0, sig["transmission"])),
            "sector": rng.gauss(0.0, sig["sector"]),
        })
    # legg kontrollmedlemmet forst - uforstyrret
    out[0] = {"wind_scale": 1.0, "wind_dir": 0.0, "gate_scale": 1.0,
              "gate_dir": 0.0, "transmission": 1.0, "sector": 0.0}
    return out


# --------------------------------------------------------------- evaluering


def _member_state(spot, member, base):
    """Regn hs/tp for ett ensemblemedlem, gitt uforstyrret utgangspunkt."""
    if spot["klasse"] == "C":
        # lokal vindsjo skalerer med vinden
        u = base["local_wind_mean"] * member["wind_scale"]
        loc_hs, loc_tp, _ = P.jonswap_growth(
            u, base["local_fetch_km"], base["local_duration_h"] or 1
        )
        # propagert komponent
        prop_hs = prop_tp = 0.0
        if base["gate_hs"] > 0 and base["gate_dir"] is not None:
            gate = dict(spot["gate"])
            gate["transmission"] = gate.get("transmission", 1.0) * member["transmission"]
            gate["sector_half_width"] = max(
                6.0, gate["sector_half_width"] + member["sector"]
            )
            prop_hs, prop_tp, _, _ = P.propagate_through_gate(
                base["gate_hs"] * member["gate_scale"],
                base["gate_tp"],
                base["gate_dir"] + member["gate_dir"],
                {"gate": gate},
            )
        hs, tp = P.combine(loc_hs, loc_tp, prop_hs, prop_tp)
        wdir = base["gate_dir"] if prop_hs > loc_hs else base["local_dir"]
    else:
        hs = base["model_hs"] * member["gate_scale"]
        tp = base["model_tp"]
        wdir = (base["model_dir"] + member["gate_dir"]) if base["model_dir"] is not None else None

    return hs, tp, wdir


def evaluate(spot, base, wind, lead_h, water_cm, wave_rec, n=N_MEMBERS):
    """
    Returnerer dict med:
      p_surf     0-100, sannsynlighet for surfbare forhold
      p_good     0-100, sannsynlighet for >= 6 stjerner
      stars      1-10, forventet kvalitet GITT at det blir surf (median)
      stars_p10 / stars_p90   spennet i ensemblet
      confidence "hoy" / "middels" / "lav"
    """
    spread = model_spread(wave_rec or {})
    sig = sigmas(lead_h, spot, spread)
    members = draw_members(sig, n)

    ws = wind.get("wind_speed") or 0.0
    wfrom = wind.get("wind_from_direction")

    surf, scores = 0, []
    for m in members:
        hs, tp, wdir = _member_state(spot, m, base)

        if spot["klasse"] in ("A", "B") and wdir is not None:
            hs *= P.window_factor(wdir, spot)

        q_size = P.size_quality(hs, spot["min_hs"], spot["ideal_hs"], spot["max_hs"])
        if q_size <= 0:
            continue

        q_period = P.period_quality(tp, spot["min_tp"])
        q_wind_raw, _ = (
            P.wind_quality(ws * m["wind_scale"], (wfrom or 0) + m["wind_dir"], spot["facing"])
            if wfrom is not None else (0.5, "")
        )
        q_wind = P.apply_wind_weight(q_wind_raw, spot.get("wind_weight", 1.0))
        q_water = P.water_level_quality(
            water_cm if water_cm is not None else spot["water_optimal_cm"],
            spot["water_optimal_cm"], spot["water_sensitivity_cm"],
        )
        surf += 1
        scores.append(100.0 * q_size * q_period * q_wind * q_water)

    total = len(members)
    p_surf = 100.0 * surf / total

    if not scores:
        return {"p_surf": 0.0, "p_good": 0.0, "stars": None,
                "stars_p10": None, "stars_p90": None,
                "confidence": confidence_label(0.0, sig, spot),
                "model_spread": round(spread, 3), "n_members": total}

    scores.sort()
    stars = P.score_to_stars(st.median(scores))
    p_good = 100.0 * sum(1 for s in scores if (P.score_to_stars(s) or 0) >= 6) / total

    def pct(q):
        return P.score_to_stars(scores[min(len(scores) - 1, int(q * len(scores)))])

    return {
        "p_surf": round(p_surf, 0),
        "p_good": round(p_good, 0),
        "stars": stars,
        "stars_p10": pct(0.10),
        "stars_p90": pct(0.90),
        "confidence": confidence_label(p_surf, sig, spot),
        "model_spread": round(spread, 3),
        "n_members": total,
    }


def confidence_label(p_surf, sig, spot):
    """
    Konfidens handler om hvor mye vi stoler paa selve tallet, ikke om
    hvor bra det blir. Et ukalibrert spot med stort modellsprik faar
    lav konfidens selv om sannsynligheten er hoy.
    """
    penalty = 0
    if not spot.get("kalibrert"):
        penalty += 2
    if sig["gate_scale"] > 0.30:
        penalty += 1
    if sig["wind_dir"] > 25:
        penalty += 1
    if 25 < p_surf < 75:
        penalty += 1
    return ["hoy", "hoy", "middels", "middels", "lav"][min(4, penalty)]
