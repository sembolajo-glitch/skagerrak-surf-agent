"""Enhetstester. Kjor: python -m pytest test_physics.py -v"""

import math

import pytest

import physics as P


def approx(a, b, tol=0.05):
    return abs(a - b) <= tol * max(1.0, abs(b))


def test_jonswap_matcher_handregning():
    # 20 m/s over 145 km (Faerder -> Skagen), rikelig varighet
    hs, tp, lim = P.jonswap_growth(20, 145, 24)
    assert approx(hs, 3.89), hs
    assert approx(tp, 8.90), tp
    assert lim == "fetch"


def test_jonswap_15ms():
    hs, tp, lim = P.jonswap_growth(15, 145, 24)
    assert approx(hs, 2.91), hs
    assert approx(tp, 8.1), tp


def test_lokal_fjordfetch():
    # 15 m/s over 33 km (Slagen -> Faerder) - dette er "lokalsjoen" alene
    hs, tp, _ = P.jonswap_growth(15, 33, 24)
    assert approx(hs, 1.39), hs
    assert approx(tp, 4.9), tp


def test_varighetsbegrensning_biter():
    """Kort kuling skal gi mindre enn fetchgrensa."""
    hs_long, _, lim_long = P.jonswap_growth(20, 145, 24)
    hs_short, _, lim_short = P.jonswap_growth(20, 145, 4)
    assert lim_short == "duration"
    assert lim_long == "fetch"
    assert hs_short < hs_long * 0.75


def test_gruppehastighet_og_forsinkelse():
    assert approx(P.group_velocity(8.0), 6.24)
    # 33 km ved 8 s
    assert approx(P.travel_time_h(33, 8.0), 1.47)


def test_retningsfiltrering_slipper_lite_gjennom():
    """S-sektoren opp fjorden skal ta en klar bit, men ikke alt."""
    frac = P.directional_energy_fraction(
        peak_dir=190, sector_center=181, sector_half_width=20, s=5
    )
    assert 0.15 < frac < 0.60, frac
    # skjev sjo slipper mindre gjennom enn en som peker rett opp fjorden
    frac_aligned = P.directional_energy_fraction(181, 181, 20, s=5)
    assert frac_aligned > frac


def test_sv_swell_lekker_mindre_inn_enn_s_swell():
    """Kjernen i hele fjordanalysen: SV-swell slipper darligere opp enn S."""
    s_peak = P.directional_energy_fraction(190, 181, 20, s=5)
    sv_peak = P.directional_energy_fraction(225, 181, 20, s=5)
    assert sv_peak < s_peak * 0.7, (sv_peak, s_peak)


def test_bred_spredning_hjelper_nar_toppen_ligger_utenfor_sektoren():
    """
    Med toppen utenfor fjordaksen er det HALEN i spekteret som kommer opp,
    sa bredere spredning gir mer. Dette er mekanismen som gjor at Slagen
    gar pa SV-dager i det hele tatt.
    """
    smal = P.directional_energy_fraction(230, 181, 20, s=12)
    bred = P.directional_energy_fraction(230, 181, 20, s=2)
    assert bred > smal, (bred, smal)


def test_window_factor_er_sqrt_av_directional_energy_fraction():
    """Byttet ut (ordre 2026-09-03, se rapport til bruker) fra en flat/
    lineaer-taper-modell til cos^(2s) - samme spredning
    directional_energy_fraction() bruker for klasse C sin gate. Denne
    testen laaser selve koblingen: window_factor() er sqrt() av
    energiandelen for sektoren [senter-halvbredde, senter+halvbredde],
    der senter er midtpunktet av swell_window."""
    spot = {"swell_window": (170, 260)}
    frac = P.directional_energy_fraction(200, 215, 45, s=5)
    assert P.window_factor(200, spot) == math.sqrt(frac)


def test_window_factor_hoyest_ved_senteret_ikke_flat_1():
    """I motsetning til den gamle modellen er IKKE vekten 1.0 gjennom hele
    vinduet - cos^(2s) taper glatt ogsaa INNENFOR sektoren, hoyest praesist
    ved midtpunktet."""
    spot = {"swell_window": (170, 260)}  # senter 215
    senter = P.window_factor(215, spot)
    kant = P.window_factor(170, spot)  # 45 grader fra senter, fortsatt i vinduet
    assert 0.0 < kant < senter < 1.0


def test_window_factor_symmetrisk_om_senteret():
    spot = {"swell_window": (170, 260)}  # senter 215
    assert P.window_factor(175, spot) == pytest.approx(P.window_factor(255, spot))


def test_window_factor_avtar_monotont_med_avstand_fra_senter():
    spot = {"swell_window": (170, 260)}  # senter 215
    verdier = [P.window_factor(d, spot) for d in (215, 240, 265, 300)]
    assert verdier == sorted(verdier, reverse=True)


def test_window_factor_ingen_hardt_kutt_utenfor_vinduet():
    """Kjernen i forskjellen fra den gamle modellen: det finnes IKKE noe
    punkt utenfor vinduet der vekten er eksakt 0.0 (bortsett fra ~180
    grader fra senteret) - selv langt utenfor gir cos^(2s) en liten, men
    positiv vekt. Den gamle modellen kuttet haardt til 0.0 15 grader
    utenfor kanten (se git-historikken til denne testen)."""
    spot = {"swell_window": (170, 260)}  # senter 215, vinduskant 260
    langt_utenfor = P.window_factor(300, spot)  # 40 grader forbi kanten
    assert langt_utenfor > 0.0


def test_window_factor_naer_null_kun_motsatt_av_senteret():
    spot = {"swell_window": (170, 260)}  # senter 215
    motsatt = P.window_factor(215 + 180, spot)
    langt_utenfor = P.window_factor(300, spot)
    assert motsatt < 0.01 < langt_utenfor


def test_window_factor_bruker_spot_sin_spread_s_med_fallback_5():
    """spread_s (samme feltnavn/standardverdi-monster som gate.spread_s
    for klasse C) styrer hvor smal spredningen er - lavere s = bredere =
    mer slipper inn langt fra senteret."""
    smal = {"swell_window": (170, 260), "spread_s": 20}
    bred = {"swell_window": (170, 260), "spread_s": 2}
    uten_felt = {"swell_window": (170, 260)}  # skal falle tilbake til 5

    langt_fra_senter = 300
    assert P.window_factor(langt_fra_senter, bred) > P.window_factor(langt_fra_senter, uten_felt)
    assert P.window_factor(langt_fra_senter, uten_felt) > P.window_factor(langt_fra_senter, smal)


def test_window_factor_haandterer_vindu_som_wrapper_over_0():
    """Senter/halvbredde-beregningen bruker samme wrap-konvensjon som
    in_window() naar lo > hi."""
    spot = {"swell_window": (350, 30)}  # senter 10, halvbredde 20
    assert P.window_factor(10, spot) > P.window_factor(200, spot)


def test_vindkvalitet():
    # Slagen facing=120 (OSO). NV-vind (315) skal vaere fralands.
    q_nv, lbl = P.wind_quality(8, 315, 120)
    assert q_nv >= 0.85, (q_nv, lbl)
    # SO-vind (135) rett paaland
    q_so, _ = P.wind_quality(12, 135, 120)
    assert q_so <= 0.2
    # stille = glassy uansett
    assert P.wind_quality(1, 120, 120)[0] == 1.0


def test_saltstein_trenger_no():
    # Saltstein facing=225 (SV). NO (45) er fralands.
    assert P.wind_quality(8, 45, 225)[0] >= 0.85
    # V (270) er cross-onshore - bare 45 grader av paalandsretningen
    q, label = P.wind_quality(8, 270, 225)
    assert 0.2 < q < 0.5, (q, label)
    # O (90) er cross-offshore og klart bedre
    assert P.wind_quality(8, 90, 225)[0] > q


def test_wind_quality_er_glatt_cos_kurve_ikke_botter():
    """Byttet ut (ordre 2026-09-03, se rapport til bruker) fra fem faste
    boetter til q = 0.15 + 0.85*(1-cos(d))/2 - denne testen laaser selve
    formelen, ikke bare grove terskler."""
    for d in (0, 15, 45, 68.7, 90, 103.6, 135, 165, 180):
        wind_from = 120 + d  # facing=120, vilkaarlig men entydig d
        q, _ = P.wind_quality(8, wind_from, 120)
        expected = 0.15 + 0.85 * (1 - math.cos(math.radians(d))) / 2
        assert q == pytest.approx(expected), (d, q, expected)


def test_wind_quality_ingen_hopp_ved_gamle_botteterskler():
    """Kjernen i saken som utloeste byttet (Jomfruland, 2026-09-03): 68 og
    71 grader laa paa hver sin side av 70-graders-terskelen og fikk 0.28
    mot 0.50 - et sprang stort nok til aa avgjoere rangeringen mellom
    spots. Sjekker alle fire gamle terskler (40/70/115/150) - differansen
    over EN grad skal na vaere liten, ikke et sprang."""
    facing = 120
    for terskel in (40, 70, 115, 150):
        q_under, _ = P.wind_quality(8, facing + terskel - 1, facing)
        q_over, _ = P.wind_quality(8, facing + terskel + 1, facing)
        assert abs(q_over - q_under) < 0.02, (terskel, q_under, q_over)


def test_wind_quality_monotont_voksende_mot_fraland():
    facing = 225
    verdier = [P.wind_quality(8, facing + d, facing)[0] for d in (0, 30, 60, 90, 120, 150, 180)]
    assert verdier == sorted(verdier)


def test_wind_label_bruker_samme_terskler_som_foer_kun_navngiving():
    """_wind_label() er en RENT tekstlig kategorisering (describe.py/
    loggen) - selve q-verdien er uavhengig av den na, se wind_quality()
    sin docstring."""
    assert P._wind_label(0) == "onshore"
    assert P._wind_label(39.9) == "onshore"
    assert P._wind_label(40) == "cross-onshore"
    assert P._wind_label(69.9) == "cross-onshore"
    assert P._wind_label(70) == "cross-shore"
    assert P._wind_label(114.9) == "cross-shore"
    assert P._wind_label(115) == "cross-offshore"
    assert P._wind_label(149.9) == "cross-offshore"
    assert P._wind_label(150) == "offshore"
    assert P._wind_label(180) == "offshore"


def test_wind_quality_sterk_fralandsvind_river_opp_ansiktet_uendret():
    """Tilleggsstraffen for kraftig (naer-)fralandsvind - uendret logikk,
    virker fortsatt paa det kontinuerlige d-et."""
    svak, _ = P.wind_quality(10, 225 + 130, 225)  # d=130, under 14 m/s
    sterk, label = P.wind_quality(20, 225 + 130, 225)  # d=130, over 14 m/s
    assert sterk < svak
    assert "kraftig" in label


def test_wind_quality_svak_paalandsvind_ikke_kritisk_uendret():
    hard, _ = P.wind_quality(10, 225, 225)  # d=0, sterk vind
    svak, label = P.wind_quality(3, 225, 225)  # d=0, svak vind
    assert svak > hard
    assert "svak" in label


# ------------------------------------------------------------ vindgulv


def test_apply_wind_floor_loefter_under_gulvet():
    assert P.apply_wind_floor(0.20, 0.40) == 0.40


def test_apply_wind_floor_roerer_ikke_verdier_over_gulvet():
    assert P.apply_wind_floor(0.60, 0.40) == 0.60


def test_apply_wind_floor_none_er_ingen_gulv():
    assert P.apply_wind_floor(0.05, None) == 0.05


def test_apply_wind_weight_og_apply_wind_floor_er_uavhengige_mekanismer():
    """Kjernen i skillet (se rapport til bruker, ordre 2026-09-03):
    apply_wind_weight() er HELNINGEN (paavirker hele kurven, ogsaa
    mellomliggende verdier), apply_wind_floor() er et GULV (paavirker
    KUN verdier under grensa). Sjekker at et gulv ikke endrer en verdi
    som allerede er over det, mens weight endrer den uansett (med mindre
    weight=1)."""
    raw = 0.6
    weighted = P.apply_wind_weight(raw, 0.5)
    assert weighted != raw  # helningen roerer selv en midt-i-kurven-verdi
    floored = P.apply_wind_floor(weighted, 0.10)
    assert floored == weighted  # gulvet (0.10) er under - roerer ikke


def test_storrelsesscore_har_hard_nedre_grense():
    assert P.size_quality(1.5, 1.6, 2.4, 3.4) == 0.0
    assert P.size_quality(2.4, 1.6, 2.4, 3.4) == 1.0
    assert P.size_quality(3.4, 1.6, 2.4, 3.4) < 0.2


def test_fetch_interpolasjon():
    tbl = [10] * 16
    tbl[8] = 33          # S
    assert P.fetch_for_direction(tbl, 180) == 33
    mid = P.fetch_for_direction(tbl, 191.25)  # halvveis S -> SSV
    assert 20 < mid < 33


def test_energibudsjett_slagen_reproduserer_dokumentet():
    """
    Sjekk at kjeden reproduserer overslaget i analysedokumentet:
    Hs 3.9 m @ 185 grader ved munningen + 20 m/s lokal fetch
    skal gi rundt 2.5-3.0 m ved Slagen.
    """
    spot = {"gate": {"distance_km": 33, "bearing_deg": 181,
                     "sector_half_width": 20, "spread_s": 5,
                     "transmission": 1.0}}
    prop_hs, prop_tp, frac, delay = P.propagate_through_gate(3.9, 8.9, 185, spot)
    local_hs, local_tp, _ = P.jonswap_growth(20, 33, 12)
    hs, tp = P.combine(local_hs, local_tp, prop_hs, prop_tp)
    assert 2.2 < hs < 3.2, (hs, prop_hs, local_hs, frac)
    assert 5.0 < tp < 8.0, tp
    assert 1.0 < delay < 2.0


def test_wave_power_2_4m_7_5s_gir_cirka_22_kw_per_meter():
    assert approx(P.wave_power(2.4, 7.5), 22, tol=0.1)


def test_wave_power_1_2m_6_0s_gir_cirka_4_kw_per_meter():
    assert approx(P.wave_power(1.2, 6.0), 4, tol=0.1)


def test_wave_power_bruker_faktor_64_ikke_32():
    """
    Laas ned selve formelen (rho, g, 64*pi), ikke bare de to
    referanseverdiene over - en feil paa 64 -> 32 dobler alle tallene uten
    aa vaere aapenbar bare fra "cirka riktig stoerrelsesorden"-sjekker.
    """
    hs, tp = 2.0, 8.0
    expected_w_per_m = 1025.0 * 9.81 ** 2 * hs ** 2 * tp / (64 * math.pi)
    assert math.isclose(P.wave_power(hs, tp), expected_w_per_m / 1000.0, rel_tol=1e-9)


def test_wave_power_null_ved_flatt_vann():
    assert P.wave_power(0.0, 8.0) == 0.0


def test_swell_fraction_ren_swell():
    assert P.swell_fraction(2.0, 0.0, hs_eff=2.0) == 1.0


def test_swell_fraction_ren_vindsjo():
    assert P.swell_fraction(0.0, 2.0, hs_eff=2.0) == 0.0


def test_swell_fraction_likt_bidrag_gir_halvparten():
    assert P.swell_fraction(1.0, 1.0, hs_eff=1.4) == 0.5


def test_swell_fraction_kvadrert_ikke_lineaer():
    # 2x swell-hoyde mot lik vindsjo skal gi mer enn dobbel andel
    # (energien gaar som hoyde i annen) - 4/(4+1) = 0.8, ikke 2/3
    assert P.swell_fraction(2.0, 1.0, hs_eff=2.2) == 0.8


def test_swell_fraction_begge_none_gir_none_ikke_nan_eller_null():
    """Open-Meteo kan levere null/mangle begge partisjonene paa flate
    dager - skal gi None (ingen partisjonsdata aa regne en andel av),
    ikke NaN, ikke ZeroDivisionError, og IKKE 0.0 (det ville paastaatt
    "0 % swell" i stedet for "vet ikke")."""
    assert P.swell_fraction(None, None, hs_eff=1.0) is None


def test_swell_fraction_begge_null_gir_none():
    assert P.swell_fraction(0.0, 0.0, hs_eff=1.0) is None


def test_swell_fraction_en_manglende_en_reell_verdi_gir_ekte_null():
    """Forskjell fra testen over: her MANGLER bare EN partisjon, den andre
    er en reell maalt verdi - da vet vi faktisk at andelen er 0 %
    (henholdsvis 100 %), det er ikke "ingen data" lenger."""
    assert P.swell_fraction(None, 2.0, hs_eff=2.0) == 0.0
    assert P.swell_fraction(2.0, None, hs_eff=2.0) == 1.0


def test_swell_fraction_rundes_til_to_desimaler():
    val = P.swell_fraction(1.0, 3.0, hs_eff=3.2)
    assert val == round(val, 2)


def test_swell_fraction_under_terskel_gir_none_selv_med_rent_swell_forhold():
    """Kjernen i forbeholdet: naar hs_eff < 0.5 m er forholdet mellom
    komponentene meningslost uansett - andelen skal vaere None (IKKE 0.0,
    som ville paastaatt "0 % swell") selv om swell_hs/windsea_hs alene
    ville gitt "ren swell" (1.0)."""
    assert P.swell_fraction(0.3, 0.0, hs_eff=0.3) is None
    assert P.swell_fraction(0.24, 0.02, hs_eff=0.49) is None


def test_swell_fraction_rett_paa_terskelen_slipper_gjennom():
    assert P.swell_fraction(1.0, 0.0, hs_eff=P.MIN_HS_FOR_SWELL_FRACTION_M) == 1.0


def test_swell_fraction_hs_eff_none_gir_none():
    assert P.swell_fraction(2.0, 0.0, hs_eff=None) is None


def test_swell_fraction_tvetydigheten_forbeholdet_dekker():
    """0.24 m 'swell' og en tenkt 2.1 m swell kan gi samme andel naar
    vindsjoen er tilsvarende liten - det er NETTOPP dette som gjor andelen
    tvetydig under terskelen, og hvorfor swell_hs_abs (raa meter) trengs
    ved siden av. Over terskelen (der andelen faktisk brukes) er de ulike
    absoluttverdiene fortsatt tilgjengelige i swell_hs_abs."""
    liten = P.swell_fraction(0.24, 0.02, hs_eff=0.49)  # under terskel -> None
    stor = P.swell_fraction(2.1, 0.18, hs_eff=2.1)      # over terskel -> reell andel
    assert liten is None
    assert stor > 0.9
