"""
Enhetstester for agent.py sin regional energi-port (regional_wp_min/max i
spots.yaml, sjekket i score_hour()). Bruker ekte spot-definisjoner fra
spots.yaml via load_spots() og evaluate_class_ab() for aa faa et gyldig
`computed`-objekt - se ensemble.evaluate() sine krav til feltene der,
enklere aa bygge riktig via den ekte kjeden enn aa gjette strukturen.
"""

import agent as A


def _hvasser():
    """Klasse B, kun regional_wp_min satt (38.7)."""
    spots, _ = A.load_spots()
    return next(s for s in spots if s["id"] == "hvasser_sando")


def _saltstein():
    """Klasse A, kun regional_wp_max satt (32.6)."""
    spots, _ = A.load_spots()
    return next(s for s in spots if s["id"] == "saltstein")


def _favorable_computed(spot, hs=2.0, tp=7.0):
    """Et `computed`-objekt som ELLERS ville gitt god score - innenfor
    swell_window, hs mellom min_hs og max_hs, glassy vind (wind_speed<2
    -> q_wind=1.0 uansett retning, se physics.wind_quality)."""
    ts = "2026-11-14T09:00:00+00:00"
    wave_dir = spot["swell_window"][0] + 5  # godt innenfor vinduet
    wind = {ts: {"wind_speed": 1.0, "wind_from_direction": 0.0}}
    waves = {ts: {"hs": hs, "tp": tp, "wave_from_direction": wave_dir}}
    computed = A.evaluate_class_ab(spot, [ts], wind, waves)
    return ts, wind[ts], waves[ts], computed[0][1]


def test_regional_gate_lukker_under_min():
    """Hvasser: regional_wp under regional_wp_min (38.7) skal tvinge
    scoren til 0, selv om alt lokalt er gunstig."""
    spot = _hvasser()
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed, regional_wp=30.0)
    assert h["regional_gate_closed"] is True
    assert h["score"] == 0.0
    assert h["regional_wp"] == 30.0


def test_regional_gate_apen_over_min():
    """Regional_wp over min: porten skal IKKE lukke, og scoren skal
    reflektere den (gunstige) lokale beregningen som normalt."""
    spot = _hvasser()
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed, regional_wp=50.0)
    assert h["regional_gate_closed"] is False
    assert h["score"] > 50.0


def test_regional_gate_lukker_over_max():
    """Saltstein: regional_wp over regional_wp_max (32.6) skal tvinge
    scoren til 0."""
    spot = _saltstein()
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed, regional_wp=40.0)
    assert h["regional_gate_closed"] is True
    assert h["score"] == 0.0


def test_regional_gate_apen_under_max():
    spot = _saltstein()
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed, regional_wp=20.0)
    assert h["regional_gate_closed"] is False
    assert h["score"] > 50.0


def test_ukjent_regional_wp_porter_aldri():
    """regional_wp=None (ukjent for denne timen) skal ALDRI lukke porten -
    samme forsiktige default som window_ok naar retning mangler."""
    spot = _saltstein()
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed, regional_wp=None)
    assert h["regional_gate_closed"] is False
    assert h["score"] > 50.0


def test_spot_uten_terskler_porter_aldri():
    """Et spot uten regional_wp_min/max satt (de aller fleste) skal
    aldri lukkes av porten, uansett hvor ekstrem regional_wp er."""
    spots, _ = A.load_spots()
    spot = next(s for s in spots if s["id"] == "orekroken")
    assert spot.get("regional_wp_min") is None
    assert spot.get("regional_wp_max") is None
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed, regional_wp=999.0)
    assert h["regional_gate_closed"] is False
