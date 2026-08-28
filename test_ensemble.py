"""Enhetstester for ensemble.py. Kjor: python -m pytest test_ensemble.py -v"""

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
