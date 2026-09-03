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

Regional energi-port (ordre 2026-09-02, se agent.py sin score_hour() for
resten av bildet): evaluate() tar en valgfri regional_wp og sjekker den
mot spot["regional_wp_min"/"regional_wp_max"]. Naar porten er raa-lukket
bypasses den med en KONTINUERLIG vekt (bypass_weight()) regnet PER
ENSEMBLEMEDLEM fra klasse C sine egne (perturberte) lokal-/propagert-
energikomponenter - se _member_state(). Det er med vilje: en boolsk
bypass flipper hardt naer paritet, mens en per-medlem kontinuerlig vekt
lar spredningen i p_surf falle ut av selve ensemblet.
"""

import math
import random
import statistics as st

import physics as P

N_MEMBERS = 40
SEED = 20261114


# --------------------------------------------------------------- usikkerhet


# Open-Meteo sin globale boelgemodell (GWAM, ~25-28 km rutenett) er 4-5x
# grovere enn EWAM (~5 km, Europa - se sources.py sin openmeteo_waves()).
# Den opploser ikke Skagerrak-kysten/fjordmunningene i det hele tatt paa
# den skalaen - hele det relevante omraadet er bare noen faa rutenett-
# celler, saa lokal skjerming/eksponering (akkurat det spotene her lever
# av) er strukturelt usynlig for modellen, ikke bare "stoyete".
#
# 0.15 er et STARTPUNKT, ikke en maalt verdi - vi har ingen EWAM-vs-GWAM-
# kalibreringslogg aa maale mot ennaa (se README om aa kalibrere mot egen
# logg). Valgt til aa vaere paa storrelse med selve grunn-usikkerheten i
# hs_rel (default 0.15): "det aa vaere paa den globale modellen er
# omtrent like mye grunn til tvil som modellens egen generiske Hs-
# usikkerhet". Kombineres i kvadratur (samme metode som model_spread)
# siden det er en uavhengig feilkilde - grov geometri, ikke modell-
# uenighet innad i samme time. Juster naar det finnes data til aa
# etterproeve det mot.
GLOBAL_MODEL_HS_REL_PENALTY = 0.15


def sigmas(lead_h, spot, model_spread=0.0, global_model=False):
    """
    Standardavvik for hver usikkerhetskilde, gitt varslingslengde i timer.

    Tallene er kalibrert mot typisk MEPS/ECMWF-ytelse i Skagerrak:
    vindfeil rundt 10 % paa analysetidspunktet, voksende til 25-30 %
    paa fem dogn. Juster i spots.yaml under `uncertainty` hvis din
    egen logg sier noe annet.

    `global_model`: True naar timens boelgepartisjon kom fra GWAM i
    stedet for EWAM (se GLOBAL_MODEL_HS_REL_PENALTY over) - legger paa
    ekstra usikkerhet paa `gate_scale` fordi DATAGRUNNLAGET er
    daarligere for den timen, ikke bare fordi den ligger langt fram.
    """
    u = spot.get("uncertainty", {})
    lead = max(0.0, lead_h)
    strukturell = 1.0 if spot.get("kalibrert") else 2.0
    global_penalty = GLOBAL_MODEL_HS_REL_PENALTY if global_model else 0.0

    return {
        "wind_scale": u.get("wind_rel", 0.10) + 0.0013 * lead,
        "wind_dir": u.get("wind_dir_deg", 8.0) + 0.22 * lead,
        "gate_scale": math.hypot(
            u.get("hs_rel", 0.15) + 0.0013 * lead, model_spread, global_penalty
        ),
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


# ------------------------------------------------- regional energi-port
#
# score_hour() (agent.py) sin regional_wp_min/max-port (PR #16) var
# boolsk: local_hs > prop_hs bypasset porten helt, ellers ikke. Det
# flipper fram og tilbake naer paritet - baade mellom timer og mellom
# ensemblemedlemmer - og gir hopp i p_surf som ikke reflekterer reell
# usikkerhet. Hoyde alene er ogsaa feil metrikk: lokal 0.9 m/4.2 s og
# fjordswell 0.9 m/7 s er ikke samme bolge (~2x forskjell i effekt, se
# physics.wave_power()). Erstattet (ordre 2026-09-02) med en myk vekt
# basert paa energifluks (Hs^2 * Tp, samme byggestein som wave_power()
# - proporsjonalitetskonstanten trengs ikke her, det er bare et forhold)
# og en rampe i log-rom.

# Startverdi, IKKE en maalt konstant - kalibreres senere mot faktiske
# utfall (se calibrate.py sin model_rev-grupperte rapport, shadow_schema.py).
# Bredden paa overgangssonen i log-energiforhold: r i [-ramp, +ramp] gir
# en lineaer rampe fra w=0 til w=1, utenfor klippes den til 0 hhv. 1.
BYPASS_RAMP_LOG = 0.35


def log_energy_margin(local_hs, local_tp, prop_hs, prop_tp):
    """
    r = log(E_lokal / E_prop), E = Hs^2 * Tp (energifluks, se
    physics.wave_power() for hvorfor det er riktig storrelse - ikke bare
    Hs). None hvis en av sidene mangler energi helt (Hs eller Tp <= 0) -
    se bypass_weight() for hvordan de randtilfellene haandteres i selve
    porten (de trenger ikke r, kun fortegnet paa hvilken side som er 0).
    """
    e_local = local_hs ** 2 * local_tp
    e_prop = prop_hs ** 2 * prop_tp
    if e_prop <= 0 or e_local <= 0:
        return None
    return math.log(e_local / e_prop)


def bypass_weight(local_hs, local_tp, prop_hs, prop_tp, ramp=BYPASS_RAMP_LOG):
    """
    0-1: hvor mye spottens EGEN lokale sjo (uavhengig av regional_wp)
    skal bypasse regional-energi-porten. 1.0 = lokal sjo dominerer helt
    (porten skal ikke stenge), 0.0 = regional/propagert swell dominerer
    helt (porten skal virke som normalt). Kontinuerlig mellom - IKKE
    boolsk - slik at en ensemble-spredning naer paritet gir en genuin
    spredning i utfall, ikke et hardt hopp.
    """
    e_local = local_hs ** 2 * local_tp
    e_prop = prop_hs ** 2 * prop_tp
    if e_prop <= 0:
        return 1.0
    if e_local <= 0:
        return 0.0
    r = math.log(e_local / e_prop)
    return min(1.0, max(0.0, (r + ramp) / (2 * ramp)))


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
    """
    Regn hs/tp for ett ensemblemedlem, gitt uforstyrret utgangspunkt.

    Returnerer (hs, tp, wdir, loc_hs, loc_tp, prop_hs, prop_tp) - de
    fire siste (raa lokal/propagert-komponenter FOR de kombineres) er
    None for klasse A/B, der bypass_weight() ikke brukes (se evaluate()
    - klasse A/B bypasses via base["source"]=="local_fetch" i stedet,
    en base-niva-flagg, ikke noe som varierer per medlem).
    """
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
        return hs, tp, wdir, loc_hs, loc_tp, prop_hs, prop_tp
    else:
        hs = base["model_hs"] * member["gate_scale"]
        tp = base["model_tp"]
        wdir = (base["model_dir"] + member["gate_dir"]) if base["model_dir"] is not None else None
        return hs, tp, wdir, None, None, None, None


def evaluate(spot, base, wind, lead_h, water_cm, wave_rec, n=N_MEMBERS, regional_wp=None):
    """
    Returnerer dict med:
      p_surf     0-100, sannsynlighet for surfbare forhold
      p_good     0-100, sannsynlighet for >= 6 stjerner
      stars      1-10, forventet kvalitet GITT at det blir surf (median)
      stars_p10 / stars_p90   spennet i ensemblet
      confidence "hoy" / "middels" / "lav"

    p_surf og stars kan bevege seg i motsatt retning ved parameterendringer.
    Det er forventet - stars er betinget paa at surf inntreffer (medianen av
    KUN medlemmene med score > 0), saa flere marginale medlemmer over
    terskelen senker medianen samtidig som sannsynligheten stiger.
    Aritmetikk, ikke usikkerhet.

    regional_wp: bolgeeffekt (kW/m) ved Saltsteins offshore_point for
    denne timen (se agent.py sin run()), eller None hvis ukjent - porter
    da aldri igjen, se under. Sjekkes mot spot["regional_wp_min"/
    "regional_wp_max"] (spots.yaml) for en "raa" apen/lukket-tilstand
    (gate_raw), DELT av alle medlemmer (samme regional_wp, samme
    terskler - den varierer ikke per medlem). Det som VARIERER per
    medlem er bypass_weight() sin myke vekt (w) naar porten er raa-
    lukket: klasse C bruker medlemmets EGNE (perturberte) lokal/
    propagert-komponenter fra _member_state(), saa spredningen i hvor
    naer partitet lokal/regional energi er faller naturlig ut som
    spredning i p_surf - IKKE et hardt, samlet hopp for alle medlemmer
    samtidig. Klasse A/B bypasses fortsatt boolsk (w=1.0) naar
    base["source"]=="local_fetch" - uendret oppforsel der, se
    score_hour() i agent.py.
    """
    spread = model_spread(wave_rec or {})
    global_model = (wave_rec or {}).get("partisjon_kilde") == "global"
    sig = sigmas(lead_h, spot, spread, global_model)
    members = draw_members(sig, n)

    ws = wind.get("wind_speed") or 0.0
    wfrom = wind.get("wind_from_direction")

    wp_min = spot.get("regional_wp_min")
    wp_max = spot.get("regional_wp_max")
    gate_would_close = regional_wp is not None and (
        (wp_min is not None and regional_wp < wp_min)
        or (wp_max is not None and regional_wp > wp_max)
    )
    gate_raw = 0.0 if gate_would_close else 1.0

    surf, scores = 0, []
    for m in members:
        hs, tp, wdir, loc_hs, loc_tp, prop_hs, prop_tp = _member_state(spot, m, base)

        if spot["klasse"] in ("A", "B") and wdir is not None:
            hs *= P.window_factor(wdir, spot)

        if base.get("source") == "local_fetch":
            w = 1.0
        elif spot["klasse"] == "C" and None not in (loc_hs, loc_tp, prop_hs, prop_tp):
            w = bypass_weight(loc_hs, loc_tp, prop_hs, prop_tp)
        else:
            w = 0.0
        gate = gate_raw + w * (1.0 - gate_raw)

        q_size = P.size_quality(hs, spot["min_hs"], spot["ideal_hs"], spot["max_hs"]) * gate
        q_period = P.period_quality(tp, spot["min_tp"])
        q_wind_raw, _ = (
            P.wind_quality(ws * m["wind_scale"], (wfrom or 0) + m["wind_dir"], spot["facing"])
            if wfrom is not None else (0.5, "")
        )
        q_wind = P.apply_wind_weight(q_wind_raw, spot.get("wind_weight", 1.0))
        # wind_floor - samme mekanisme som agent.py sin score_hour() bruker
        # (se der og physics.apply_wind_floor() for hvorfor dette maa
        # matche NOEYAKTIG: score_hour() og evaluate() sin per-medlem-
        # scoring maa regne fra samme retningsmodell, ellers gjentar vi
        # nettopp bugen window_factor hadde helt til 2026-09-03, se rapport
        # til bruker - score/q_wind (logget) og stars/p_surf (denne
        # funksjonen) fra to ULIKE modeller av samme vindstraff).
        q_wind = P.apply_wind_floor(q_wind, spot.get("wind_floor", 0.10))
        q_water = P.water_level_quality(
            water_cm if water_cm is not None else spot["water_optimal_cm"],
            spot["water_optimal_cm"], spot["water_sensitivity_cm"],
        )
        score = 100.0 * q_size * q_period * q_wind * q_water
        # et medlem telles som "surf" naar HELE scoren er positiv, ikke
        # bare naar bolgehoyden passerer min_hs - ellers kan p_surf vise
        # hoy sannsynlighet mens stars er null (f.eks. naar q_period er 0).
        if score <= 0:
            continue
        surf += 1
        scores.append(score)

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
