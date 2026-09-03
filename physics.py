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


def window_factor(direction, spot):
    """
    Retningsvekt for klasse A/B - amplitudefaktor (IKKE energiandel) paa
    hs, brukt av agent.py sin score_hour() og ensemble.py sin evaluate().

    Byttet ut (ordre 2026-09-03, se rapport til bruker) fra en flat
    1.0-inne/lineaer-taper-15-grader-utenfor-modell til SAMME cos^(2s)-
    spredning directional_energy_fraction() allerede bruker for klasse C
    sin gate (physics.propagate_through_gate()) - EN retningsmodell i
    hele systemet i stedet for to. Sektoren er spotens swell_window
    (senter = midtpunkt, halvbredde = halve vinduet, wrap over 0
    haandtert som i in_window()); s er spot["spread_s"] - samme
    feltnavn og standardverdi-monster (5) som gate.spread_s for klasse C,
    se spots.yaml sin topptekst ved det feltet.

    sqrt(), ikke selve energiandelen: directional_energy_fraction()
    returnerer hvor stor ANDEL AV ENERGIEN (Hs^2-skala) som havner
    innenfor sektoren - hs skal skaleres med amplitude, altsaa
    sqrt(energiandel), noyaktig samme mønster som
    propagate_through_gate() bruker (`hs = gate_hs * math.sqrt(energy)`).

    Simulert mot 45 234 historiske rader i shadow.csv foer denne byttet
    (se rapport til bruker): 10,8 % av klasse A/B-radene endrer score,
    median -4,1 poeng (0-100-skala), ingen rad krysser til/fra null -
    mye mildere enn en cos^1-variant som ble vurdert og forkastet (drepte
    64 % av alt som scoret positivt).

    INGEN hard grense her lenger: i motsetning til den gamle modellen har
    cos^(2s) ikke noe punkt der vekten er eksakt 0.0 (bortsett fra noyaktig
    180 grader fra sektorsenteret) - retningsavvik gir alltid en glatt,
    kontinuerlig avtagende vekt, aldri et hardt kutt. window_ok (se
    agent.py) er uendret av dette - den er fortsatt en hard in/ut-flagg,
    kun informativ, styrer ikke lenger q_size (se den fiksen fra 2026-09-03).

    KJENT BEGRENSNING (ordre 2026-09-03, se rapport til bruker): cos^(2s)
    beskriver spredning i det INNKOMMENDE boelgespekteret, ikke refraksjon
    over bunnen. Ekte refraksjon boyer boelgene mot land og reduserer det
    effektive vinkelavviket i grunt vann - denne modellen overvurderer
    derfor retningstapet for spots med slak bunn helt inn, og undervurderer
    det for spots med dypt vann helt inn. Kjent forenkling, ikke en feil.
    """
    lo, hi = spot["swell_window"]
    width = (hi - lo) if hi >= lo else (hi + 360 - lo)
    center = (lo + width / 2.0) % 360
    s = spot.get("spread_s", 5)
    frac = directional_energy_fraction(direction, center, width / 2.0, s=s)
    return math.sqrt(frac)


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


# ------------------------------------------------------- swell/vindsjo-andel

# Under denne hoyden er forholdet mellom swell/vindsjo-partisjonene
# meningslost uansett - se swell_fraction()-docstringen for hvorfor.
MIN_HS_FOR_SWELL_FRACTION_M = 0.5


def swell_fraction(swell_hs, windsea_hs, hs_eff):
    """
    Swellens andel av boelgeenergien, 0-1: swell_hs^2 / (swell_hs^2 +
    windsea_hs^2) - de to Open-Meteo-partisjonene (se sources.py sin
    openmeteo_waves() og agent.py sin gather()).

    Rent beskrivende, IKKE en scorekomponent (se agent.py) - vi trenger
    kalibreringsdata foerst for aa se om hoy swell-andel faktisk
    korrelerer med gode okter.

    Returnerer None (ALDRI 0.0) naar andelen ikke er meningsfull aa regne
    ut i det hele tatt:

    1. hs_eff < MIN_HS_FOR_SWELL_FRACTION_M (0.5 m) - VIKTIG FORBEHOLD:
       andelen er TVETYDIG naar sjoen er nesten flat. Naermer begge
       partisjonene seg null, kollapser nevneren, og en andel naer 1.0
       kan da bety enten "stor ren swell" ELLER "nesten ingenting igjen
       av noen av delene" - to helt forskjellige forhold som samme tall
       ikke skiller mellom (0.24 m ren "swell" mot en reell 2.1 m-swell
       gir samme andel naer 1.0). Bruk swell_hs (raa meter, ikke
       normalisert - se agent.py sitt swell_hs_abs-felt) for aa faktisk
       skille "stor ren swell" fra "rester": 2.1 m mot 0.24 m er entydig
       der andelen ikke er det.
    2. begge partisjonene er None/0 (Open-Meteo leverer ikke alltid tall,
       uavhengig av om hs_eff selv er meningsfull - hs_eff kan komme fra
       en annen kilde, se agent.py) - da er det ingen partisjonsdata aa
       regne en andel av.

    I begge tilfeller er 0.0 FEIL svar: det er en PAASTAND om "0 % swell"
    i grensesnittet, mens det vi faktisk mener er "ikke meningsfullt" /
    "vet ikke". None tvinger frontenden til aa vise tankestrek i stedet
    for et tall som garantert blir feiltolket.
    """
    if hs_eff is None or hs_eff < MIN_HS_FOR_SWELL_FRACTION_M:
        return None
    s = swell_hs or 0.0
    w = windsea_hs or 0.0
    total = s * s + w * w
    if total <= 0:
        return None
    return round(s * s / total, 2)


# ------------------------------------------------------------- vindkvalitet


def _wind_label(d):
    """Menneskelesbar kategori for et vinkelavvik - KUN for tekst/visning
    (describe.py sine punkter, wind_label i loggen) - se wind_quality() sin
    docstring for hvorfor selve q-VERDIEN ikke lenger bruker disse
    terskelen. Terskelverdiene (40/70/115/150) er bevisst de samme som de
    gamle bøttegrensene var, ren kontinuitet i navngivningen, ingen
    beregning avhenger av dem lenger."""
    if d >= 150:
        return "offshore"
    if d >= 115:
        return "cross-offshore"
    if d >= 70:
        return "cross-shore"
    if d >= 40:
        return "cross-onshore"
    return "onshore"


def wind_quality(wind_speed, wind_from, facing):
    """
    0-1. facing = retningen spotten ser mot (= paalandsvind).
    d = 0   -> rett paaland (daarlig)
    d = 180 -> rett fraland (bra)

    Byttet ut (ordre 2026-09-03, se rapport til bruker) fra fem faste
    bøtter (harde terskler ved 40/70/115/150 grader) til en glatt
    cos-kurve: q = 0.15 + 0.85*(1-cos(d))/2 - 0.15 ved d=0 (rett paaland),
    0.5 ved d=90, 1.0 ved d=180 (rett fraland), UTEN hopp ved noen vinkel.
    Bøttene hadde et reelt, dokumentert problem: to naesten identiske
    vinkelavvik paa hver sin side av en terskel (68 mot 71 grader) kunne
    faa 0.28 mot 0.50 - et sprang stort nok til aa avgjore rangeringen
    mellom spots i UI-en (se rapport til bruker, Jomfruland-saken
    2026-09-03: 68.7 grader falt saa vidt i "cross-onshore" i stedet for
    "cross-shore"). Vilkaarlige kanter er verre enn ingen kanter.

    `_wind_label()` gir fortsatt en av de fem gamle kategorinavnene for
    TEKST/visning (samme terskler som foer, kun navngiving - se der) -
    selve q-verdien er uavhengig av den kategoriseringen na.

    Simulert mot 47 446 historiske rader foer bygging: selve kurvebyttet
    (uten wind_floor) endret 6.2 % av radene, median +0.8-0.9 poeng (0-100).
    Hjelper spot med MODERATE vinkelavvik mest (der bøttegrensene traff
    tilfeldig), naesten ingenting naer d=0 (cosinus er flat der) - IKKE
    en fiks for et spot som staar naer rett paaland, se apply_wind_floor()
    for den saken.
    """
    if wind_speed < 2.0:
        return 1.0, "glassy"
    d = ang_diff(wind_from, facing)
    q = 0.15 + 0.85 * (1.0 - math.cos(math.radians(d))) / 2.0
    label = _wind_label(d)

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
    Hvor hardt daarlig vind skal straffes, per spot - HELNINGEN, altsaa hvor
    fort kvaliteten faller naar vinkelavviket oker. IKKE samme egenskap som
    et gulv (se apply_wind_floor()) - en ren eksponent gaar alltid gjennom
    de samme to ankerpunktene (verste og beste tilfelle er uendret av
    weight), og kan bare loefte verste-tilfelle ved aa flate ut HELE kurven
    i samme slag. Se rapport til bruker (2026-09-03) for hvorfor det ikke
    er nok til aa uttrykke "et kraftig dyptvannsrev blir aldri helt
    ubrukelig i paalandsvind" alene.

    weight < 1  -> spotten taaler chop (kraftig dyptvannsrev, f.eks. Saltstein)
    weight = 1  -> noytral
    weight > 1  -> vindfolsom (slak sandstrand som blir soppel med en gang)
    """
    if weight is None or weight == 1.0:
        return q_wind
    return max(0.0, min(1.0, q_wind ** weight))


def apply_wind_floor(q_wind, floor):
    """
    GULVET - strukturelt ulik apply_wind_weight() (helningen). Et gulv
    uttrykker at et kraftig dyptvannsrev (Saltstein: "surfes rutinemessig
    i onshore chop", boelgen jekker opp fra dypt vann uavhengig av
    vindretning) aldri blir helt ubrukelig i paalandsvind - en egen,
    uavhengig minsteverdi, ikke en justering av helningen paa resten av
    kurven. Se rapport til bruker (2026-09-03) for hvorfor en ren
    eksponent (weight) ikke kan uttrykke dette selv: weight<1 loefter HELE
    kurven, og et lite nok weight for aa naa et oensket gulv ved d=0 ville
    ogsaa flate ut mellomliggende vinkelavvik spotten faktisk boer skille
    mellom - floor beroerer KUN verste-tilfelle-enden.

    floor=None behandles som 0.0 (ingen gulv - historisk oppforsel for
    spots uten feltet, se spot.get("wind_floor", 0.10) hos kallerne).
    """
    return max(floor or 0.0, q_wind)


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


# --------------------------------------------------------- brattheit/gate-port

# Startpunkter, IKKE malte konstanter - se gate_threshold_factor() sin
# docstring for kalibreringsstatus.
STEEPNESS_FRESH = 0.035   # fersk vindsjo
STEEPNESS_CLEAN = 0.015   # fullt avtagende donning
GATE_CLEAN_FLOOR = 0.40   # ren donning trenger 40 % av energien


def wave_steepness(hs, tp):
    """
    Hs / L0, der L0 = 1.56 * tp**2 (dypvanns boelgelengde, m, med
    g/(2*pi) ~ 1.56). Hoy verdi = krapp vindsjo, lav = ren avtagende
    donning - se gate_threshold_factor() for hvorfor dette skiller
    "stor rotete dag" fra "lav lang donning dagen etter".

    Returnerer None naar hs/tp mangler eller tp <= 0 (boelgelengde
    udefinert) - ALDRI 0.0, av samme grunn som swell_fraction(): en
    krapphet paa 0.0 ville paastaatt "helt flat sjo", ikke "vet ikke".
    """
    if hs is None or tp is None or tp <= 0:
        return None
    l0 = 1.56 * tp ** 2
    return hs / l0


def gate_threshold_factor(steepness):
    """
    Skalerer regional_wp_min etter hvor ren sjoen er (se score_hour() i
    agent.py: regional_wp_min ganges med denne FOR porten sjekkes).

    Bakgrunn: boelgeeffekt (wave_power(), Hs^2 * Tp) overvekter hoyde.
    Etter en stor dag faller effekten mye mer enn surfbarheten, fordi
    den gjenvaerende donningen er lav og lang (empirisk: Saltstein
    starter normalt paa surf-forecast 150, men gaar paa 50-70 dagen
    etter en stor dag - 35-45 % av normalterskelen). En ren, avtagende
    donning trenger derfor MINDRE maalt effekt for aa telle som "aapen
    port" enn en fersk, kort vindsjo med samme effekttall gjor.

    Lineaer rampe mellom STEEPNESS_CLEAN (returnerer GATE_CLEAN_FLOOR)
    og STEEPNESS_FRESH (returnerer 1.0), klippet utenfor:
      steepness <= STEEPNESS_CLEAN  -> GATE_CLEAN_FLOOR
      steepness >= STEEPNESS_FRESH  -> 1.0
      der imellom                   -> lineaer interpolasjon

    GATE_CLEAN_FLOOR = 0.40 er en startverdi som skal kalibreres, ikke
    et maalt tall - valgt til aa ligge midt i det empiriske 35-45 %-
    spennet over. STEEPNESS_FRESH/STEEPNESS_CLEAN er ogsaa startpunkter.
    Returnerer 1.0 (ingen endring - porten er like streng som foer) naar
    steepness er None, samme forsiktige default som resten av porten:
    ukjent tilstand skal aldri gjore porten STRENGERE enn den var.
    """
    if steepness is None:
        return 1.0
    if steepness >= STEEPNESS_FRESH:
        return 1.0
    if steepness <= STEEPNESS_CLEAN:
        return GATE_CLEAN_FLOOR
    frac = (steepness - STEEPNESS_CLEAN) / (STEEPNESS_FRESH - STEEPNESS_CLEAN)
    return GATE_CLEAN_FLOOR + (1.0 - GATE_CLEAN_FLOOR) * frac


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
