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


# ------------------------------------------- unntak: dominerende lokal sjo


def test_regional_gate_bypasses_naar_lokal_fetch_dominerer():
    """Hvasser (klasse B, egen SO-fetch mot Koster/Skagen - se
    local_fetch_km i spots.yaml): sterk, vedvarende SSO-vind bygger en
    lokal sjo som overgaar den (lave) offshore-modellhoyden - source blir
    "local_fetch". regional_wp under Hvassers egen min (38.7) skal da
    IKKE lukke porten, bare bypasses den - dette er noyaktig de fem
    historiske timene backtesten fant (se rapport til bruker 2026-09-02)."""
    import datetime as dt

    spots, _ = A.load_spots()
    spot = next(s for s in spots if s["id"] == "hvasser_sando")
    start = dt.datetime.fromisoformat("2026-11-14T00:00:00+00:00")
    times = [(start + dt.timedelta(hours=i)).isoformat() for i in range(10)]
    ts = times[-1]
    # 10 timer vedvarende sterk SSO-vind (157 grader - lengste fetch i
    # tabellen, 120 km) FOR maalepunktet, saa build_local_sea() faar
    # varighet nok til aa bygge en reell sjo (duration-limited med bare
    # eet tidssteg gir naermet null - se dens docstring).
    wind = {t: {"wind_speed": 18.0, "wind_from_direction": 157.0} for t in times}
    waves = {t: {"hs": 0.3, "tp": 4.0, "wave_from_direction": 157.0} for t in times}
    computed = dict(A.evaluate_class_ab(spot, times, wind, waves))[ts]

    assert computed["source"] == "local_fetch", computed  # forutsetning for testen
    assert computed["local_hs"] > 0.3

    h = A.score_hour(spot, ts, wind[ts], waves[ts], None, computed,
                      regional_wp=13.4)  # under Hvassers egen min=38.7
    assert h["regional_gate_bypassed"] is True
    assert h["regional_gate_closed"] is False
    # gate-bypasset skal IKKE tvinge q_size til 0 - resten av regningen
    # (her: sterk paalandsvind for Hvasser) faar fortsatt lov til aa
    # bestemme scoren normalt
    assert h["q_size"] > 0.0


def test_regional_gate_bypasses_naar_local_hs_over_prop_hs_klasse_c():
    """Klasse C (Slagen): local_hs > prop_hs skal bypasse porten paa
    samme maate som source=="local_fetch" gjor for klasse A/B - haandlagd
    `computed` (samme felt som evaluate_class_c() bygger) fremfor aa
    kjore hele gate-simuleringen, siden score_hour() bare leser feltene."""
    spots, _ = A.load_spots()
    spot = next(s for s in spots if s["id"] == "slagen")
    ts = "2026-11-14T09:00:00+00:00"
    computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0, "dir_eff": 180.0,
        "local_hs": 2.0, "prop_hs": 0.5, "swell_hs": 0.5, "windsea_hs": 2.0,
        # resten er hva ensemble.evaluate() sin _member_state() (klasse C)
        # trenger - se der. gate_hs=0.0 hopper over propagerings-grenen.
        "local_wind_mean": 15.0, "local_fetch_km": 20.0, "local_duration_h": 8,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed,
                      regional_wp=10.0)  # under Slagens egen min=65.1
    assert h["regional_gate_bypassed"] is True
    assert h["regional_gate_closed"] is False
    assert h["q_size"] > 0.0


def test_regional_gate_ikke_bypass_naar_modell_dominerer_klasse_c():
    """Samme spot, men prop_hs > local_hs (regional swell dominerer) -
    da skal porten lukke som normalt, IKKE bypasses."""
    spots, _ = A.load_spots()
    spot = next(s for s in spots if s["id"] == "slagen")
    ts = "2026-11-14T09:00:00+00:00"
    computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0, "dir_eff": 180.0,
        "local_hs": 0.3, "prop_hs": 2.0, "swell_hs": 2.0, "windsea_hs": 0.3,
        "local_wind_mean": 3.0, "local_fetch_km": 20.0, "local_duration_h": 2,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed,
                      regional_wp=10.0)
    assert h["regional_gate_bypassed"] is False
    assert h["regional_gate_closed"] is True
    assert h["score"] == 0.0


# ------------------------------------------------- append_shadow_log()


def _payload(spot_id="saltstein", **hour_overrides):
    hour = {"time": "2026-11-14T09:00:00+00:00", "score": 70.0, "hs_eff": 2.0}
    hour.update(hour_overrides)
    return {"generated_at": "2026-11-14T09:00:00+00:00",
            "spots": [{"id": spot_id, "hours": [hour]}]}


def test_append_shadow_log_skriver_header_paa_ny_fil(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "OUT", tmp_path)
    A.append_shadow_log(_payload())
    lines = (tmp_path / "shadow.csv").read_text().splitlines()
    assert lines[0].startswith("run_at,")
    assert len(lines) == 2


def test_append_shadow_log_dupliserer_ikke_header_paa_eksisterende_fil(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "OUT", tmp_path)
    A.append_shadow_log(_payload())
    A.append_shadow_log(_payload())
    lines = (tmp_path / "shadow.csv").read_text().splitlines()
    assert lines.count([l for l in lines if l.startswith("run_at,")][0]) == 1
    assert len(lines) == 3  # 1 header + 2 datarader


def test_append_shadow_log_reparerer_eksisterende_fil_uten_header(tmp_path, monkeypatch):
    """Simulerer funnet paa data-grenen: en fil med ekte data, men uten
    header (se docstringen i append_shadow_log() for rotaarsaken -
    git show-omdirigeringen i forecast.yml sitt hente-steg). Headeren
    skal settes INN FORREST, de eksisterende radene skal overleve."""
    monkeypatch.setattr(A, "OUT", tmp_path)
    path = tmp_path / "shadow.csv"
    path.write_text("2026-08-28T10:00:00+00:00,saltstein,2026-08-28T10:00:00+00:00,0.0,1.2\r\n")

    A.append_shadow_log(_payload(spot_id="hvasser_sando"))

    lines = path.read_text().splitlines()
    assert lines[0].startswith("run_at,")
    assert "saltstein" in lines[1]  # den gamle, headerlose raden overlevde
    assert "hvasser_sando" in lines[2]  # den nye raden kom etter
    assert len(lines) == 3


def test_append_shadow_log_tom_eksisterende_fil_regnes_som_ny(tmp_path, monkeypatch):
    """0-byte fil (nettopp det git show-omdirigeringen produserer naar
    kilden mangler) skal oppfore seg som om filen ikke fantes - IKKE
    hoppe over headeren slik den gamle `not path.exists()`-sjekken gjorde."""
    monkeypatch.setattr(A, "OUT", tmp_path)
    path = tmp_path / "shadow.csv"
    path.write_text("")

    A.append_shadow_log(_payload())

    lines = path.read_text().splitlines()
    assert lines[0].startswith("run_at,")
    assert len(lines) == 2
