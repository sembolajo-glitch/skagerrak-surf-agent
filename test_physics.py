"""Enhetstester. Kjor: python -m pytest test_physics.py -v"""

import math
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
