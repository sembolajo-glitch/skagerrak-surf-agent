"""
Bølgefysikk for Skagerrak / Oslofjorden.

Alt her er rent numerisk og testbart uten nettverk.

Konvensjoner:
  - Alle retninger er METEOROLOGISKE ("kommer fra"), grader, 0 = nord.
    MET Oceanforecast 2.0 bruker samme konvensjon (sea_surface_wave_from_direction).
    Merk at 0.9-versjonen brukte oseanografisk konvensjon - gammel kode kan
    vaere 180 grader feil.
  - "facing" for et spot er retningen brytningen SER MOT, altsaa retningen
    paalandsvind kommer fra. Slagen facing=120 betyr at vika aapner mot OSO.
"""

import functools
import math

G = 9.81

# ---------------------------------------------------------------- retninger


def ang_diff(a, b):
    """Minste vinkelforskjell mellom to retninger, 0-180."""
    return abs(((a - b + 180) % 360) - 180)


def in_window(direction, window):
    """Er retningen innenfor [lo, hi]? Haandterer wrap over 0."""
    lo, hi = window
    if lo <= hi:
        return lo <= direction <= hi
    return direction >= lo or direction <= hi


def window_factor(direction, spot, taper=15.0):
    """
    1.0 inne i spotens swellvindu, glatt nedtrapping over `taper` grader
    utenfor, 0 lenger ute. Unngaar at scoren hopper mellom 0 og full verdi
    naar modellretningen vaker rundt kanten av vinduet.
    """
    lo, hi = spot["swell_window"]
    if in_window(direction, (lo, hi)):
        return 1.0
    d = min(ang_diff(direction, lo), ang_diff(direction, hi))
    return max(0.0, 1.0 - d / taper)


def fetch_for_direction(fetch_table, direction):
    """
    Interpoler fetch (km) fra en 16-punkts kompasstabell.
    fetch_table er en liste med 16 verdier fra N og med klokka (22.5 grader).
    """
    if len(fetch_table) != 16:
        raise ValueError("fetch_table maa ha 16 verdier")
    pos = (direction % 360) / 22.5
    i = int(math.floor(pos)) % 16
    j = (i + 1) % 16
    w = pos - math.floor(pos)
    return fetch_table[i] * (1 - w) + fetch_table[j] * w


# ------------------------------------------------- fetch/varighetsbegrenset


def jonswap_growth(u10, fetch_km, duration_h):
    """
    Fetch- og varighetsbegrenset vindsjoevekst (SPM/CEM-parametrisering).

    Dimensjonsloese groesser:
        X* = g F / U^2
        H* = 0.0016 X*^0.5      Hs = H* U^2/g
        T* = 0.286 X*^(1/3)     Tp = T* U/g
        t* = 68.8 X*^(2/3)      t  = t* U/g

    Varighetsbegrensning: gitt t, inverter t* for aa finne den effektive
    fetchen vinden rakk aa bygge over, og bruk den minste av de to.

    Returnerer (hs_m, tp_s, limited_by) der limited_by er
    "fetch", "duration" eller "fully_developed".
    """
    if u10 < 0.5 or fetch_km <= 0 or duration_h <= 0:
        return 0.0, 0.0, "no_wind"

    fetch_m = fetch_km * 1000.0
    x_fetch = G * fetch_m / (u10 ** 2)

    t_star = G * (duration_h * 3600.0) / u10
    x_duration = (t_star / 68.8) ** 1.5

    if x_duration < x_fetch:
        x, limited_by = x_duration, "duration"
    else:
        x, limited_by = x_fetch, "fetch"

    # fullt utviklet sjoe
    h_star = 0.0016 * math.sqrt(x)
    t_star_p = 0.286 * x ** (1.0 / 3.0)
    if h_star >= 0.243 or t_star_p >= 8.134:
        h_star = min(h_star, 0.243)
        t_star_p = min(t_star_p, 8.134)
        limited_by = "fully_developed"

    hs = h_star * u10 ** 2 / G
    tp = t_star_p * u10 / G
    return hs, tp, limited_by


def _wind(rec):
    """Normaliser vindpost. Godtar MET-navn og korte navn."""
    rec = rec or {}
    return {
        "speed": rec.get("speed", rec.get("wind_speed")) or 0.0,
        "direction": rec.get("direction", rec.get("wind_from_direction")) or 0.0,
    }


def build_local_sea(wind_series, fetch_table, now_index, max_lookback_h=24):
    """
    Bygg lokal vindsjoe ved et punkt fra vindhistorikken.

    wind_series: liste av dicts time for time (indeks 0 = eldst). Godtar bade
                 {speed, direction} og MET sitt {wind_speed, wind_from_direction}.
    now_index:   indeksen vi vurderer
    Returnerer dict med hs, tp, direction, duration_h, fetch_km, limited_by.

    Metode: gaa bakover i tid saa lenge vindretningen holder seg innenfor
    +/-45 grader av naavaerende retning og styrken ikke faller under 40 %
    av gjennomsnittet. Bruk snittvind og varigheten paa den perioden.
    """
    if now_index < 0 or now_index >= len(wind_series):
        return dict(hs=0.0, tp=0.0, direction=0.0, duration_h=0.0,
                    fetch_km=0.0, limited_by="no_data")

    cur = _wind(wind_series[now_index])
    ref_dir = cur["direction"]
    if cur["speed"] < 1.0:
        return dict(hs=0.0, tp=0.0, direction=ref_dir, duration_h=0.0,
                    fetch_km=0.0, limited_by="calm")

    speeds = [cur["speed"]]
    dirs = [ref_dir]
    hours = 1

    for k in range(1, min(max_lookback_h, now_index + 1)):
        w = _wind(wind_series[now_index - k])
        mean_speed = sum(speeds) / len(speeds)
        if ang_diff(w["direction"], ref_dir) > 45:
            break
        if w["speed"] < 0.4 * mean_speed or w["speed"] < 3.0:
            break
        speeds.append(w["speed"])
        dirs.append(w["direction"])
        hours += 1

    u_mean = sum(speeds) / len(speeds)
    # vektet middelretning
    sx = sum(math.sin(math.radians(d)) for d in dirs)
    cy = sum(math.cos(math.radians(d)) for d in dirs)
    d_mean = math.degrees(math.atan2(sx, cy)) % 360

    fetch_km = fetch_for_direction(fetch_table, d_mean)
    hs, tp, limited_by = jonswap_growth(u_mean, fetch_km, hours)

    return dict(hs=hs, tp=tp, direction=d_mean, duration_h=hours,
                mean_wind=u_mean, fetch_km=fetch_km, limited_by=limited_by)


# ------------------------------------------------------- retningsspredning


def directional_energy_fraction(peak_dir, sector_center, sector_half_width, s=5):
    """Cachet wrapper - integralet avhenger bare av vinkelforskjellen."""
    delta = round(((peak_dir - sector_center + 180) % 360) - 180)
    return _dir_frac(delta, round(sector_half_width), round(s, 1))


@functools.lru_cache(maxsize=200_000)
def _dir_frac(delta, sector_half_width, s):
    """
    Andel av boelgeenergien som ligger innenfor en gitt retningssektor,
    gitt cos^(2s)-spredning rundt topretningen.

    Dette er den viktigste (og mest usikre) parameteren for fjordspottene:
    den avgjoer hvor mye av Skagerrak-sjoeen som faktisk kommer opp fjorden.
    s er tunbar per spot. Lav s = bred spredning = mer slipper inn.
    """
    n = 721
    total = inside = 0.0
    for i in range(n):
        theta = -180 + 360.0 * i / (n - 1)
        d = math.cos(math.radians(theta) / 2.0) ** (2 * s) if abs(theta) < 180 else 0.0
        total += d
        # theta er avvik fra toppretningen; delta er toppretning minus sektorsenter
        if abs(((delta + theta + 180) % 360) - 180) <= sector_half_width:
            inside += d
    return inside / total if total > 0 else 0.0


def group_velocity(tp):
    """Gruppehastighet i dypvann, m/s."""
    return G * tp / (4 * math.pi) if tp > 0 else 0.0


def travel_time_h(distance_km, tp):
    cg = group_velocity(tp)
    return (distance_km * 1000.0 / cg) / 3600.0 if cg > 0 else 0.0


def propagate_through_gate(gate_hs, gate_tp, gate_dir, spot):
    """
    Ta boelgetilstanden ved fjordmunningen og regn ut hva som naar spotten.

    Tre tap:
      1. retningsfiltrering (bare sektoren langs fjordaksen slipper opp)
      2. transmisjonskoeffisient (skjaergaard, refraksjon - kalibreres per spot)
      3. ingen friksjon (dyprenna gjoer at vi kan se bort fra det)

    Returnerer (hs, tp, energy_fraction, delay_h).
    """
    gate = spot["gate"]
    frac = directional_energy_fraction(
        gate_dir,
        gate["bearing_deg"],
        gate["sector_half_width"],
        s=gate.get("spread_s", 5),
    )
    energy = frac * gate.get("transmission", 1.0)
    hs = gate_hs * math.sqrt(energy)
    delay = travel_time_h(gate["distance_km"], gate_tp)
    return hs, gate_tp, energy, delay


def combine(hs_a, tp_a, hs_b, tp_b):
    """Kvadratisk summering av to boelgekomponenter, energiveid periode."""
    e_a, e_b = hs_a ** 2, hs_b ** 2
    if e_a + e_b == 0:
        return 0.0, 0.0
    hs = math.sqrt(e_a + e_b)
    tp = (tp_a * e_a + tp_b * e_b) / (e_a + e_b)
    return hs, tp


# --------------------------------------------------------------- boelgeeffekt

RHO_SEAWATER = 1025.0  # kg/m^3


def wave_power(hs, tp):
    """
    Boelgeeffekt i kW per meter boelgefront, dypvann.
    P = rho * g^2 * Hs^2 * Tp / (64 * pi), i watt per meter.
    Deles paa 1000 for kW.

    Merk faktoren 64, ikke 32: 32-varianten gjelder boelgehoyde H,
    mens vi bruker signifikant boelgehoyde Hs. For en Rayleigh-fordelt
    sjoe er Hs = sqrt(2) * H_rms, og det gir en faktor 2 i nevneren.
    Feil faktor dobler alle tallene.

    Beskrivende tall, IKKE en scorekomponent - se agent.py. Effekt sier
    ingenting om kvalitet: en stor rotete dag med paalandsvind gir hoy
    effekt og elendig surf.
    """
    return RHO_SEAWATER * G ** 2 * hs ** 2 * tp / (64 * math.pi) / 1000.0


# ------------------------------------------------------------- vindkvalitet


def wind_quality(wind_speed, wind_from, facing):
    """
    0-1. facing = retningen spotten ser mot (= paalandsvind).
    d = 0   -> rett paaland (daarlig)
    d = 180 -> rett fraland (bra)
    """
    if wind_speed < 2.0:
        return 1.0, "glassy"
    d = ang_diff(wind_from, facing)

    if d >= 150:
        q, label = 1.0, "offshore"
    elif d >= 115:
        q, label = 0.85, "cross-offshore"
    elif d >= 70:
        q, label = 0.50, "cross-shore"
    elif d >= 40:
        q, label = 0.28, "cross-onshore"
    else:
        q, label = 0.15, "onshore"

    # sterk fralandsvind river opp ansiktet
    if d >= 115 and wind_speed > 14:
        q *= max(0.5, 1.0 - (wind_speed - 14) * 0.04)
        label += " (kraftig)"
    # svak paalandsvind er ikke kritisk
    if d < 70 and wind_speed < 5:
        q = min(1.0, q + 0.35)
        label += " (svak)"

    return q, label


def apply_wind_weight(q_wind, weight):
    """
    Hvor hardt daarlig vind skal straffes, per spot.
    weight < 1  -> spotten taaler chop (kraftig dyptvannsrev, f.eks. Saltstein)
    weight = 1  -> noytral
    weight > 1  -> vindfolsom (slak sandstrand som blir soppel med en gang)
    """
    if weight is None or weight == 1.0:
        return q_wind
    return max(0.0, min(1.0, q_wind ** weight))


def size_quality(hs, min_hs, ideal_hs, max_hs):
    """0-1, med hard nedre grense."""
    if hs < min_hs:
        return 0.0
    if hs <= ideal_hs:
        return 0.35 + 0.65 * (hs - min_hs) / max(1e-6, ideal_hs - min_hs)
    if hs >= max_hs:
        return 0.15
    return 1.0 - 0.85 * (hs - ideal_hs) / max(1e-6, max_hs - ideal_hs)


def period_quality(tp, min_tp):
    if tp <= 0:
        return 0.0
    if tp < min_tp:
        return max(0.15, 0.6 * tp / min_tp)
    return min(1.0, 0.75 + 0.25 * (tp - min_tp) / 3.0)


def water_level_quality(level_cm, optimal_cm, sensitivity_cm):
    """Gaussisk straff. sensitivity=0 -> ingen effekt."""
    if sensitivity_cm <= 0:
        return 1.0
    z = (level_cm - optimal_cm) / sensitivity_cm
    return max(0.4, math.exp(-0.5 * z * z))


# ------------------------------------------------------------------ stjerner


def score_to_stars(score):
    """
    0-100 score -> 1-10 stjerner.

    Ankere: 35 -> 3 (så vidt surfbart), 55 -> 5 (verdt turen),
            75 -> 7.5 (bra), 95 -> 9.5 (sesongens beste).
    Bevisst lineaer - en ikke-lineaer mapping ville bare skjule at
    underliggende score er usikker.
    """
    if score is None or score <= 0:
        return None
    return max(1.0, min(10.0, round(1.0 + 9.0 * score / 100.0, 1)))
