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
