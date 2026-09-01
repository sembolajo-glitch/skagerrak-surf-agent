"""Enhetstester for ensemble.py. Kjor: python -m pytest test_ensemble.py -v"""

import math

import pytest

import ensemble as E


def test_model_spread_null_uten_nok_kilder():
    assert E.model_spread({}) == 0.0
    assert E.model_spread({"hs_met": 2.0}) == 0.0


def test_model_spread_gir_uenighet_naar_to_kilder_spriker():
    """
    MET og Open-Meteo uenige (2.0 m vs 3.0 m) skal gi et konkret,
    positivt spredningstall - ikke 0. relativt sprik her er
    pstdev([2,3]) / mean([2,3]) = 0.5 / 2.5 = 0.2.
    """
    spread = E.model_spread({"hs_met": 2.0, "hs_openmeteo": 3.0})
    assert 0.15 < spread < 0.25, spread


def test_model_spread_null_naar_modellene_enige():
    assert E.model_spread({"hs_met": 2.0, "hs_openmeteo": 2.0}) == 0.0


def test_model_spread_bruker_dmi_som_tredje_kilde():
    spread = E.model_spread({"hs_met": 2.0, "hs_openmeteo": 2.0, "hs_dmi": 3.0})
    assert spread > 0.0


# ----------------------------------------- global-modell-paaslag (GWAM)


def test_sigmas_global_model_default_false_uendret():
    """Bakoverkompatibilitet: uten global_model skal gate_scale vaere
    uendret fra foer paaslaget ble lagt til."""
    sig = E.sigmas(0, {})
    assert sig["gate_scale"] == pytest.approx(0.15)


def test_sigmas_global_model_oker_gate_scale():
    sig_ewam = E.sigmas(0, {}, model_spread=0.0, global_model=False)
    sig_global = E.sigmas(0, {}, model_spread=0.0, global_model=True)
    assert sig_global["gate_scale"] > sig_ewam["gate_scale"]


def test_sigmas_global_model_penalty_kombineres_i_kvadratur():
    sig = E.sigmas(0, {}, model_spread=0.0, global_model=True)
    expected = math.hypot(0.15, 0.0, E.GLOBAL_MODEL_HS_REL_PENALTY)
    assert sig["gate_scale"] == pytest.approx(expected)


def test_sigmas_global_model_penalty_uavhengig_av_model_spread():
    """Paaslaget skal vaere en TREDJE, uavhengig feilkilde - kombinert i
    kvadratur baade med grunn-hs_rel og med model_spread, ikke erstatte
    noen av dem."""
    sig = E.sigmas(0, {}, model_spread=0.3, global_model=True)
    expected = math.hypot(0.15, 0.3, E.GLOBAL_MODEL_HS_REL_PENALTY)
    assert sig["gate_scale"] == pytest.approx(expected)


def _spot(**overrides):
    spot = {
        "klasse": "A",
        "kalibrert": True,
        "facing": 225,
        "swell_window": [170, 260],
        "min_hs": 1.2,
        "ideal_hs": 2.5,
        "max_hs": 4.5,
        "min_tp": 6.0,
        "wind_weight": 1.0,
        "water_optimal_cm": 0,
        "water_sensitivity_cm": 0,
        "uncertainty": {},
    }
    spot.update(overrides)
    return spot


def test_evaluate_global_partisjon_senker_p_surf_ved_grensehoyde():
    """Kjernen i ordren: p_surf skal falle naar timen faktisk kommer fra
    den globale modellen (daarligere datagrunnlag), ikke bare fordi
    varslingslengden er lang. Bruker en Hs like over min_hs, saa den
    ekstra spredningen faktisk flytter noen medlemmer under terskelen."""
    spot = _spot()
    base = {"model_hs": 1.3, "model_tp": 7.0, "model_dir": 200}
    wind = {"wind_speed": 3.0, "wind_from_direction": 45}

    ewam = E.evaluate(spot, base, wind, lead_h=48, water_cm=None,
                       wave_rec={"partisjon_kilde": "ewam"})
    glob = E.evaluate(spot, base, wind, lead_h=48, water_cm=None,
                       wave_rec={"partisjon_kilde": "global"})

    assert glob["p_surf"] < ewam["p_surf"], (glob["p_surf"], ewam["p_surf"])


def test_evaluate_ingen_partisjon_kilde_oppforer_seg_som_ewam():
    """Mangler wave_rec/partisjon_kilde helt (t.d. mock-data uten Open-
    Meteo-partisjoner) skal IKKE utlose paaslaget - kun eksplisitt
    "global" skal gjore det."""
    spot = _spot()
    base = {"model_hs": 1.3, "model_tp": 7.0, "model_dir": 200}
    wind = {"wind_speed": 3.0, "wind_from_direction": 45}

    ewam = E.evaluate(spot, base, wind, lead_h=48, water_cm=None,
                       wave_rec={"partisjon_kilde": "ewam"})
    ukjent = E.evaluate(spot, base, wind, lead_h=48, water_cm=None, wave_rec={})
    ingen = E.evaluate(spot, base, wind, lead_h=48, water_cm=None, wave_rec=None)

    assert ukjent["p_surf"] == ewam["p_surf"]
    assert ingen["p_surf"] == ewam["p_surf"]


# --------------------------------------------------- bypass_weight() / r


def test_bypass_weight_r_langt_under_minus_ramp_gir_0():
    """Lokal energi ubetydelig mot propagert - w skal klippes til 0.0,
    IKKE ga negativ."""
    w = E.bypass_weight(local_hs=0.2, local_tp=3.0, prop_hs=3.0, prop_tp=10.0)
    assert w == 0.0


def test_bypass_weight_r_naer_0_gir_omtrent_halvveis():
    """Lik energifluks paa begge sider (r=0) skal gi w=0.5 noyaktig -
    midtpunktet i rampa."""
    w = E.bypass_weight(local_hs=1.0, local_tp=6.0, prop_hs=1.0, prop_tp=6.0)
    assert w == pytest.approx(0.5)


def test_bypass_weight_r_langt_over_pluss_ramp_gir_1():
    """Lokal energi langt over propagert - w skal klippes til 1.0, IKKE
    over."""
    w = E.bypass_weight(local_hs=3.0, local_tp=10.0, prop_hs=0.2, prop_tp=3.0)
    assert w == 1.0


def test_bypass_weight_e_prop_null_gir_1_uansett_e_lokal():
    """Ingen propagert energi i det hele tatt - lokal sjo er per
    definisjon ALT som finnes, w=1.0. Egen gren i bypass_weight() (unngaar
    ZeroDivisionError/log(0) - se funksjonen)."""
    assert E.bypass_weight(local_hs=0.01, local_tp=2.0, prop_hs=0.0, prop_tp=5.0) == 1.0
    assert E.bypass_weight(local_hs=5.0, local_tp=10.0, prop_hs=0.0, prop_tp=0.0) == 1.0


def test_bypass_weight_e_lokal_null_gir_0_naar_e_prop_positiv():
    """Ingen lokal sjo i det hele tatt, men propagert energi finnes -
    w=0.0 (ingenting aa bypasse porten med)."""
    assert E.bypass_weight(local_hs=0.0, local_tp=5.0, prop_hs=2.0, prop_tp=8.0) == 0.0
    assert E.bypass_weight(local_hs=3.0, local_tp=0.0, prop_hs=2.0, prop_tp=8.0) == 0.0


def test_bypass_weight_lineaer_i_rampen():
    """Innenfor [-ramp, +ramp] skal w vaere en LINEAER funksjon av r, ikke
    bare klippet i endene - halvparten inn i rampa skal gi 0.75."""
    ramp = E.BYPASS_RAMP_LOG
    # konstruer hs/tp som gir r = ramp/2 noyaktig: e_lokal/e_prop = e^(ramp/2)
    import math as _math
    forhold = _math.exp(ramp / 2)
    w = E.bypass_weight(local_hs=1.0, local_tp=forhold, prop_hs=1.0, prop_tp=1.0)
    assert w == pytest.approx(0.75, abs=1e-6)


def test_log_energy_margin_matcher_bypass_weight_sin_formel():
    r = E.log_energy_margin(local_hs=2.0, local_tp=8.0, prop_hs=0.5, prop_tp=5.0)
    assert r == pytest.approx(math.log((2.0**2 * 8.0) / (0.5**2 * 5.0)))


def test_log_energy_margin_none_naar_en_side_mangler_energi():
    assert E.log_energy_margin(0.0, 5.0, 2.0, 8.0) is None
    assert E.log_energy_margin(2.0, 8.0, 0.0, 5.0) is None
    assert E.log_energy_margin(2.0, 0.0, 2.0, 8.0) is None


# ------------------------------------------ per-medlem myk port (klasse C)


def _spot_c(**overrides):
    spot = {
        "klasse": "C",
        "kalibrert": True,
        "facing": 120,
        "min_hs": 1.6,
        "ideal_hs": 2.4,
        "max_hs": 3.4,
        "min_tp": 4.5,
        "wind_weight": 1.0,
        "water_optimal_cm": 0,
        "water_sensitivity_cm": 0,
        "uncertainty": {},
        "gate": {"name": "test", "lat": 0, "lon": 0, "distance_km": 33,
                 "bearing_deg": 181, "sector_half_width": 20,
                 "spread_s": 5, "transmission": 1.0},
        "regional_wp_min": 65.1,
    }
    spot.update(overrides)
    return spot


def test_per_medlem_vekt_varierer_naer_paritet_ikke_hardt_hopp():
    """Kjernen i ordren: naer paritet mellom lokal og propagert energi
    skal ULIKE ensemblemedlemmer (perturbert wind_scale/gate_scale) faa
    ULIKE w-verdier - IKKE alle 0 eller alle 1 samtidig. Det er dette som
    gjor at spredningen i p_surf skal falle ut naturlig av ensemblet i
    stedet for aa komme fra et eksternt paaslag."""
    spot = _spot_c()
    base = {
        "source": "local+gate",
        # tunet (ikke vilkaarlig) saa kontroll-medlemmet ligger naer
        # paritet (r ~ -0.25) mellom lokal og propagert energifluks -
        # ellers ville hele ensemblet havnet paa samme side av rampen
        "local_wind_mean": 21.0, "local_fetch_km": 20.0, "local_duration_h": 6,
        "local_dir": 181.0,
        "gate_hs": 2.0, "gate_tp": 8.0, "gate_dir": 185.0,
    }
    sig = E.sigmas(24, spot)
    members = E.draw_members(sig, n=40)

    ws = []
    for m in members:
        _, _, _, loc_hs, loc_tp, prop_hs, prop_tp = E._member_state(spot, m, base)
        ws.append(E.bypass_weight(loc_hs, loc_tp, prop_hs, prop_tp))

    # baade rene-lukk (w<0.3) og rene-aapne (w>0.7) medlemmer, OG noen
    # imellom - en genuin spredning, ikke et samlet hopp
    assert any(w < 0.3 for w in ws)
    assert any(w > 0.7 for w in ws)
    assert len(set(round(w, 2) for w in ws)) > 3


def test_regional_gate_lukker_delvis_class_c_reduserer_p_surf_gradvis():
    """End-til-ende: en raa-lukket port (regional_wp under terskel) skal
    IKKE brakk-lande p_surf til 0 for klasse C naar lokal/propagert energi
    er naer paritet - noen medlemmer bypasses, andre ikke, saa p_surf
    havner et sted MELLOM "porten var aapen" og "porten stengte alt"."""
    spot = _spot_c()
    base = {
        "source": "local+gate",
        "hs_eff": 2.0, "tp_eff": 6.0,
        # tunet (ikke vilkaarlig) saa kontroll-medlemmet ligger naer
        # paritet (r ~ -0.25) mellom lokal og propagert energifluks -
        # ellers ville hele ensemblet havnet paa samme side av rampen
        "local_wind_mean": 21.0, "local_fetch_km": 20.0, "local_duration_h": 6,
        "local_dir": 181.0,
        "gate_hs": 2.0, "gate_tp": 8.0, "gate_dir": 185.0,
    }
    wind = {"wind_speed": 5.0, "wind_from_direction": 300}

    aapen = E.evaluate(spot, base, wind, lead_h=24, water_cm=None,
                        wave_rec={}, regional_wp=100.0)  # over min - aapen
    lukket = E.evaluate(spot, base, wind, lead_h=24, water_cm=None,
                         wave_rec={}, regional_wp=10.0)  # under min - raa-lukket

    assert 0.0 < lukket["p_surf"] < aapen["p_surf"], (lukket["p_surf"], aapen["p_surf"])


def test_klasse_ab_local_fetch_uendret_w_1_uansett_medlem():
    """Klasse A/B skal FORTSATT bypasses fullt (w=1.0) naar
    source=="local_fetch" - uendret oppforsel, IKKE per-medlem-varierende
    energifluks (den bypass-veien gjelder bare klasse C, se evaluate())."""
    spot = _spot(regional_wp_min=999.0)  # umulig hoy terskel - garantert raa-lukket
    base = {"source": "local_fetch", "model_hs": 2.0, "model_tp": 8.0, "model_dir": 200}
    wind = {"wind_speed": 5.0, "wind_from_direction": 45}

    med_port = E.evaluate(spot, base, wind, lead_h=24, water_cm=None,
                           wave_rec={}, regional_wp=1.0)  # under terskel
    uten_port = E.evaluate(spot, base, wind, lead_h=24, water_cm=None,
                            wave_rec={}, regional_wp=None)  # ingen port aktiv

    assert med_port["p_surf"] == uten_port["p_surf"]
    assert med_port["stars"] == uten_port["stars"]
