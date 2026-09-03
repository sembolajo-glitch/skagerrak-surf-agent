"""
Enhetstester for agent.py sin regional energi-port (regional_wp_min/max i
spots.yaml, sjekket i score_hour()). Bruker ekte spot-definisjoner fra
spots.yaml via load_spots() og evaluate_class_ab() for aa faa et gyldig
`computed`-objekt - se ensemble.evaluate() sine krav til feltene der,
enklere aa bygge riktig via den ekte kjeden enn aa gjette strukturen.
"""

import argparse
import json

import pytest

import agent as A


def _with_regional_wp(spot_id, **thresholds):
    """
    Ekte spot fra spots.yaml, MED regional_wp_min/max overstyrt lokalt
    for testen (ordre 2026-09-02: porten er deaktivert paa alle spots i
    selve spots.yaml etter backtest_sessions.py - se rapport til bruker
    - saa disse testene kan ikke lenger lese terskler derfra og maa
    sette dem selv). Dette tester PORT-MEKANISMEN i score_hour(), IKKE
    dagens produksjonskalibrering - de to skal ikke vaere koblet sammen.
    """
    spots, _ = A.load_spots()
    spot = dict(next(s for s in spots if s["id"] == spot_id))
    spot.update(thresholds)
    return spot


def _hvasser():
    """Klasse B - regional_wp_min satt til 38.7 for denne testen (se
    _with_regional_wp())."""
    return _with_regional_wp("hvasser_sando", regional_wp_min=38.7)


def _saltstein():
    """Klasse A - regional_wp_min=12.2/regional_wp_max=32.6 satt for
    denne testen (se _with_regional_wp())."""
    return _with_regional_wp("saltstein", regional_wp_min=12.2, regional_wp_max=32.6)


def _slagen():
    """Klasse C - regional_wp_min=65.1 satt for denne testen (se
    _with_regional_wp())."""
    return _with_regional_wp("slagen", regional_wp_min=65.1)


def _favorable_computed(spot, hs=2.0, tp=7.0):
    """Et `computed`-objekt som ELLERS ville gitt god score - innenfor
    swell_window, hs mellom min_hs og max_hs, glassy vind (wind_speed<2
    -> q_wind=1.0 uansett retning, se physics.wind_quality).

    wave_dir er satt til MIDTPUNKTET av swell_window, ikke bare "5 grader
    innenfor kanten" (som den var foer ordre 2026-09-03) - cos^(2s)
    (physics.window_factor(), se rapport til bruker) taper glatt ogsaa
    INNENFOR vinduet og er hoyest praesist ved senteret, saa denne
    fixturen maa treffe senteret for fortsatt aa vaere det beste tilfellet
    testene under forutsetter (de tester regional_wp-porten, IKKE
    retningsvinduets form)."""
    ts = "2026-11-14T09:00:00+00:00"
    lo, hi = spot["swell_window"]
    width = (hi - lo) if hi >= lo else (hi + 360 - lo)
    wave_dir = (lo + width / 2.0) % 360  # midtpunktet - se docstring over
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

    spot = _hvasser()
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


def test_regional_gate_bypasses_naar_lokal_energifluks_dominerer_klasse_c():
    """Klasse C (Slagen): lokal energifluks (Hs^2*Tp) langt over den
    propagerte skal bypasse porten - samme rolle som source=="local_fetch"
    for klasse A/B, men na kontinuerlig (se ensemble.bypass_weight()),
    ikke boolsk local_hs > prop_hs. Haandlagd `computed` (samme felt som
    evaluate_class_c() bygger) fremfor aa kjore hele gate-simuleringen,
    siden score_hour() bare leser feltene."""
    spot = _slagen()
    ts = "2026-11-14T09:00:00+00:00"
    computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0, "dir_eff": 180.0,
        # e_lokal = 2.0^2*8.0=32, e_prop = 0.5^2*5.0=1.25 - forhold 25.6,
        # r=ln(25.6)=3.24, langt over +ramp(0.35) -> w klippes til 1.0
        "local_hs": 2.0, "local_tp": 8.0, "prop_hs": 0.5, "prop_tp": 5.0,
        "swell_hs": 0.5, "windsea_hs": 2.0,
        # resten er hva ensemble.evaluate() sin _member_state() (klasse C)
        # trenger - se der. gate_hs=0.0 hopper over propagerings-grenen.
        "local_wind_mean": 15.0, "local_fetch_km": 20.0, "local_duration_h": 8,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed,
                      regional_wp=10.0)  # under Slagens egen min=65.1
    assert h["bypass_weight"] == 1.0
    assert h["regional_gate_bypassed"] is True
    assert h["regional_gate_closed"] is False
    assert h["q_size"] > 0.0


def test_regional_gate_ikke_bypass_naar_propagert_energifluks_dominerer_klasse_c():
    """Samme spot, men propagert energifluks langt over lokal - da skal
    porten lukke som normalt, IKKE bypasses."""
    spot = _slagen()
    ts = "2026-11-14T09:00:00+00:00"
    computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0, "dir_eff": 180.0,
        # e_lokal = 0.3^2*4.0=0.36, e_prop = 2.0^2*8.0=32 - forhold
        # 0.01125, r=ln(0.01125)=-4.49, langt under -ramp -> w=0.0
        "local_hs": 0.3, "local_tp": 4.0, "prop_hs": 2.0, "prop_tp": 8.0,
        "swell_hs": 2.0, "windsea_hs": 0.3,
        "local_wind_mean": 3.0, "local_fetch_km": 20.0, "local_duration_h": 2,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed,
                      regional_wp=10.0)
    assert h["bypass_weight"] == 0.0
    assert h["regional_gate_bypassed"] is False
    assert h["regional_gate_closed"] is True
    assert h["score"] == 0.0


def test_regional_gate_delvis_bypass_naer_paritet_klasse_c():
    """r naer 0 (lokal og propagert energi omtrent like store) skal gi
    en MELLOMLIGGENDE vekt, ikke et hardt 0/1-hopp - det er hele poenget
    med aa bytte fra boolsk til kontinuerlig (se score_hour() sin
    docstring). q_size skal vaere delvis, men ikke fullt, redusert."""
    spot = _slagen()
    ts = "2026-11-14T09:00:00+00:00"
    # e_lokal = e_prop (samme hs OG tp) -> r=0 -> w=0.5 noyaktig
    computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0, "dir_eff": 180.0,
        "local_hs": 1.0, "local_tp": 6.0, "prop_hs": 1.0, "prop_tp": 6.0,
        "swell_hs": 1.0, "windsea_hs": 1.0,
        "local_wind_mean": 8.0, "local_fetch_km": 20.0, "local_duration_h": 4,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed,
                      regional_wp=10.0)  # under Slagens egen min=65.1
    assert h["bypass_weight"] == pytest.approx(0.5)
    assert h["log_energy_margin"] == pytest.approx(0.0)
    # w=0.5 -> ikke > 0.5 -> teller (saa vidt) som "lukket", ikke bypasset -
    # se score_hour() sin docstring for hvorfor grensa er streng ulikhet
    assert h["regional_gate_bypassed"] is False
    assert h["regional_gate_closed"] is True
    # men q_size skal IKKE vaere tvunget helt til 0 (gate=0.5, ikke 0.0) -
    # nettopp den mykheten som er poenget
    assert h["q_size"] > 0.0


def test_window_ok_er_none_for_klasse_c_uansett_retning():
    """ordre 2026-09-03, runde 2 (se rapport til bruker): window_ok var
    tidligere hardkodet True for klasse C uansett dir_eff - et EKTE funn
    (Baastoey odden: 10/80 timer med dir_eff klart utenfor swell_window,
    men window_ok=True likevel), fordi klasse C aldri har filtrert retning
    via swell_window/window_ok - det skjer i gate (gate_energy_frac). Na
    None (ikke anvendelig), ikke en paastand - uansett om dir_eff faktisk
    ligger innenfor Slagens eget swell_window [160,200] (180) eller klart
    utenfor (30), siden feltet ikke skal bety noe for klasse C i det hele
    tatt."""
    spot = _slagen()  # swell_window [160, 200]
    ts = "2026-11-14T09:00:00+00:00"
    base_computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0,
        "local_hs": 1.0, "local_tp": 6.0, "prop_hs": 1.0, "prop_tp": 6.0,
        "swell_hs": 1.0, "windsea_hs": 1.0,
        "local_wind_mean": 8.0, "local_fetch_km": 20.0, "local_duration_h": 4,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    for dir_eff in (180.0, 30.0):  # innenfor vinduet, og 130+ grader utenfor det
        computed = dict(base_computed, dir_eff=dir_eff)
        h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed, regional_wp=10.0)
        assert h["window_ok"] is None, (dir_eff, h["window_ok"])


def test_window_ok_fortsatt_bool_for_klasse_ab():
    """Uendret av runde 2 (se testen over) - klasse A/B skal fortsatt faa
    en ekte bool, ikke None."""
    spot = _saltstein()  # klasse A
    ts, wind, waves, computed = _favorable_computed(spot)
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] in (True, False)


# ---------------------------------------------- window_factor i q_size (klasse A/B)
#
# ordre 2026-09-03 (rettet reell bug, se rapport til bruker): score_hour()
# regnet wf (physics.window_factor()) men brukte den aldri - kun window_ok
# (haard 0/1-grense ved vinduskanten) styrte q_size, mens ensemble.py sin
# per-medlem-scoring (som driver p_surf/stars) ALLTID har brukt window_factor().
# Testene under dekker fiksen: wf ganges inn i q_size sitt hs-grunnlag, og
# window_ok styrer ikke lenger en haard nullstilling.
#
# ordre 2026-09-03, runde 2 (se rapport til bruker): window_factor() sin FORM
# er ogsaa byttet ut - fra flat/lineaer-taper til cos^(2s) (samme spredning
# gate.spread_s allerede brukte for klasse C). Testene under er oppdatert for
# den nye formen: den er IKKE flat=1.0 gjennom hele vinduet (hoyest praesist
# ved senteret), og har IKKE noe hardt 0.0-kutt utenfor (kun naer 180 grader
# fra senteret) - se test_score_hour_retning_langt_utenfor_vinduet_* under
# for begge disse.


def _computed_med_retning(spot, wave_dir, hs=2.0, tp=7.0):
    """Som _favorable_computed(), men med selvvalgt boelgeretning - for aa
    teste oppforsel naer/utenfor swell_window sin kant."""
    ts = "2026-11-14T09:00:00+00:00"
    wind = {ts: {"wind_speed": 1.0, "wind_from_direction": 0.0}}
    waves = {ts: {"hs": hs, "tp": tp, "wave_from_direction": wave_dir}}
    computed = A.evaluate_class_ab(spot, [ts], wind, waves)
    return ts, wind[ts], waves[ts], computed[0][1]


def test_score_hour_retning_ved_vindussenter_gir_positiv_q_size():
    spot = _saltstein()  # swell_window (170, 260), senter 215
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=215)
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is True
    assert h["q_size"] > 0.0


def test_score_hour_retning_like_utenfor_vinduet_gir_delvis_q_size_ikke_null():
    """Kjernen i den ORIGINALE fiksen (window_ok styrer ikke lenger en haard
    nullstilling): 5 grader utenfor vindkanten skal fortsatt gi en delvis,
    men positiv, verdi - lavere enn ved senteret, siden cos^(2s) avtar
    monotont med avstand fra senteret."""
    spot = _saltstein()
    innenfor_ts, innenfor_wind, innenfor_waves, innenfor_computed = _computed_med_retning(spot, wave_dir=215)
    h_innenfor = A.score_hour(spot, innenfor_ts, innenfor_wind, innenfor_waves, None, innenfor_computed)

    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=265)  # 5 grader forbi 260
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is False
    assert 0.0 < h["q_size"] < h_innenfor["q_size"]


def test_score_hour_retning_langt_utenfor_vinduet_gir_null_q_size_via_min_hs():
    """Med default-fixturens hs=2.0 (min_hs=1.2) blir q_size fortsatt 0 ved
    30 grader forbi kanten - MEN av en annen grunn enn foer 2026-09-03: det
    er IKKE lenger window_factor() selv som gir 0 (cos^(2s) har ikke noe
    hardt kutt der, se testen under) - det er det redusterte hs*wf som
    faller under spottens EGEN min_hs-grense (physics.size_quality()).
    Tilfeldig sammenfall av grenseverdier for akkurat denne hs-en, ikke et
    generelt "utenfor vinduet = null"-utsagn - se testen under for det."""
    spot = _saltstein()
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=290, hs=2.0)  # 30 grader forbi 260
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is False
    assert h["q_size"] == 0.0


def test_score_hour_retning_langt_utenfor_vinduet_gir_ikke_lenger_null_naar_hs_er_hoy_nok():
    """Selve poenget med byttet til cos^(2s) (ordre 2026-09-03, se rapport
    til bruker): window_factor() har IKKE lenger noe hardt 0.0-kutt utenfor
    vinduet. Med en hs hoy nok til at hs*wf fortsatt klarer min_hs selv 30
    grader forbi kanten, blir q_size na POSITIV - noe den ALDRI kunne bli
    foer byttet, uansett hvor hoy hs var, siden wf selv var eksakt 0.0 der."""
    spot = _saltstein()  # min_hs=1.2
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=290, hs=5.0)  # 30 grader forbi 260
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is False
    assert h["q_size"] > 0.0


def test_score_hour_retning_naer_180_grader_fra_senter_gir_null_q_size():
    """Den eneste retningen cos^(2s) faktisk gir (naer) 0.0 - motsatt av
    vindussenteret - selv med hoy hs."""
    spot = _saltstein()  # swell_window (170, 260), senter 215
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=215 + 180, hs=10.0)
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["q_size"] == 0.0


# ----------------------------------------------------------- wind_floor


def test_score_hour_wind_floor_binder_for_saltstein_i_dodrett_paalandsvind():
    """spots.yaml setter wind_floor: 0.40 for saltstein (ordre 2026-09-03,
    se rapport til bruker - dyptvannsrev, jekker opp uavhengig av
    vindretning). Dodrett paalandsvind (d=0) gir raate 0.15, vektet
    (weight=0.55) rundt 0.35 - UNDER gulvet paa 0.40, som derfor skal
    bestemme q_wind i stedet for kurven."""
    spot = _saltstein()
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=215, hs=2.0)
    wind = dict(wind, wind_speed=10.0, wind_from_direction=float(spot["facing"]))  # d=0
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    vektet_uten_gulv = 0.15 ** spot["wind_weight"]
    assert vektet_uten_gulv < spot["wind_floor"]  # forutsetning for at gulvet faktisk binder her
    assert h["q_wind"] == pytest.approx(spot["wind_floor"])


def test_score_hour_wind_floor_default_binder_ikke_for_spot_uten_override():
    """Spot uten eksplisitt wind_floor bruker standardverdien 0.10 (se
    defaults i spots.yaml) - langt under det en normal vindvekting
    normalt gir, saa gulvet skal IKKE paavirke resultatet her (kjernen i
    "ingenting endres for spots uten feltet")."""
    spots, _ = A.load_spots()
    spot = next(s for s in spots if s["id"] == "svenner")
    assert spot["wind_floor"] == 0.10  # standardverdien fra defaults - ingen per-spot-override
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=190, hs=2.0)
    wind = dict(wind, wind_speed=10.0, wind_from_direction=float(spot["facing"]))  # d=0
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    vektet_uten_gulv = 0.15 ** spot["wind_weight"]
    assert vektet_uten_gulv > spot.get("wind_floor", 0.10)  # gulvet binder ikke
    assert h["q_wind"] == pytest.approx(vektet_uten_gulv, abs=0.001)  # h["q_wind"] er avrundet til 3 desimaler


# --------------------------------------- per-spot detaljfil (out/spots/<id>.json)


def test_run_skriver_name_klasse_kalibrert_params_til_detaljfil(tmp_path, monkeypatch):
    """ordre 2026-09-03 (se rapport til bruker): detaljfila inneholdt
    tidligere KUN id/generated_at/hours - en klient som leser den ALENE
    (som chatten i appen tydeligvis gjor) hadde ingen kalibrert-nokkel aa
    lese og falt stille tilbake til False, selv for spots som faktisk er
    kalibrert (feil FIL, ikke feil verdi - forecast.json sitt "spots"-
    array hadde alltid riktig verdi). name/klasse/kalibrert/params tas na
    med, saa detaljfila kan tolkes alene."""
    monkeypatch.setattr(A, "OUT", tmp_path)
    monkeypatch.setattr(A, "SPOTS_OUT", tmp_path / "spots")
    (tmp_path / "spots").mkdir()

    args = argparse.Namespace(mock="storm", shadow=False, spot=["saltstein"], explain=[])
    A.run(args)

    data = json.loads((tmp_path / "spots" / "saltstein.json").read_text())
    assert set(data.keys()) == {"id", "generated_at", "name", "klasse", "kalibrert", "params", "hours"}
    assert data["name"] == "Saltstein"
    assert data["klasse"] == "A"
    assert data["kalibrert"] is True  # spots.yaml sier kalibrert: true for saltstein
    assert data["params"]["swell_window"] == [170, 260]
    assert len(data["hours"]) > 0


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


def test_append_shadow_log_reparerer_header_som_har_blitt_forbigatt_av_fields(tmp_path, monkeypatch):
    """Simulerer funnet paa data-grenen 2026-09-03: en fil MED header, men
    en gammel, kortere en - FIELDS i shadow_schema.py vokste (append-only)
    etter at headeren sist ble skrevet, saa headeren ble et strikt prefiks
    av dagens FIELDS i stedet for aa matche. csv.DictReader (og calibrate.py
    sin) mapper posisjonelt mot headeren, saa de nyeste feltene ble
    usynlige for enhver leser. Headerlinja skal byttes ut med en fersk,
    full header - den gamle dataraden (skrevet med det korte feltsettet)
    skal overleve uendret."""
    monkeypatch.setattr(A, "OUT", tmp_path)
    path = tmp_path / "shadow.csv"
    gammel_felt_lengde = len(A.shadow_schema.FIELDS) - 5  # for de 5 nyeste feltene
    gammel_header = ",".join(A.shadow_schema.FIELDS[:gammel_felt_lengde])
    gammel_rad = ",".join(["v"] * gammel_felt_lengde)
    path.write_text(f"{gammel_header}\r\n{gammel_rad}\r\n")

    A.append_shadow_log(_payload(spot_id="hvasser_sando"))

    lines = path.read_text().splitlines()
    assert lines[0] == ",".join(A.shadow_schema.FIELDS)  # full, fersk header
    assert lines[1] == gammel_rad  # den gamle dataraden er urort
    assert "hvasser_sando" in lines[2]  # den nye raden kom etter
    assert len(lines) == 3


def test_append_shadow_log_lar_uventet_header_vaere_urort(tmp_path, monkeypatch):
    """En header som IKKE er et strikt prefiks av dagens FIELDS (feltnavn
    endret/fjernet - strider mot append-only-kontrakten) skal ikke roeres.
    Det er ikke noe trygt aa gjette seg til her."""
    monkeypatch.setattr(A, "OUT", tmp_path)
    path = tmp_path / "shadow.csv"
    path.write_text("run_at,spot,helt_ukjent_felt\r\nv,v,v\r\n")

    A.append_shadow_log(_payload())

    lines = path.read_text().splitlines()
    assert lines[0] == "run_at,spot,helt_ukjent_felt"  # urort
    assert lines[1] == "v,v,v"


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


# --------------------------------------------------------------- gather() grid


def _fake_met_waves_med_grid(grid_lat, grid_lon):
    """Fake sources.met_waves() som fyller grid_out slik det ekte svaret
    ville gjort (se sources.met_waves() sin docstring) - kalt av gather()
    med posisjonelle (lat, lon) og grid_out som keyword."""
    def fn(lat, lon, grid_out=None):
        if grid_out is not None:
            grid_out["lat"] = grid_lat
            grid_out["lon"] = grid_lon
        return {}
    return fn


def test_gather_regner_grid_avstand_km_fra_faktisk_returnert_gridpunkt(monkeypatch):
    """ordre 2026-09-03 (se rapport til bruker): gather() skal bruke det
    FAKTISKE gridpunktet MET returnerer (via sources.met_waves() sin
    grid_out) til aa regne avstanden fra det spurte punktet
    (offshore_point for klasse A/B) - ikke gjette."""
    import sources

    spot = dict(_saltstein())
    spot["offshore_point"] = [58.930, 9.830]
    grid_lat, grid_lon = 58.95, 9.85

    monkeypatch.setattr(sources, "met_waves", _fake_met_waves_med_grid(grid_lat, grid_lon))
    monkeypatch.setattr(sources, "met_wind", lambda lat, lon: {})
    monkeypatch.setattr(sources, "openmeteo_waves", lambda lat, lon: {})
    monkeypatch.setattr(sources, "kartverket_water_level", lambda lat, lon: {})

    wind, waves, water, errors, grid = A.gather(spot)

    assert grid["lat"] == grid_lat
    assert grid["lon"] == grid_lon
    expected_km = A.P.haversine_km(58.930, 9.830, grid_lat, grid_lon)
    assert grid["avstand_km"] == round(expected_km, 2)


def test_gather_grid_none_naar_met_ikke_svarer(monkeypatch):
    """MET feiler (eller returnerer uten geometry) denne kjoeringen -
    grid skal vaere None, ikke krasje eller late som et gridpunkt fantes."""
    import sources

    spot = dict(_saltstein())
    monkeypatch.setattr(sources, "met_waves", lambda lat, lon, grid_out=None: {})
    monkeypatch.setattr(sources, "met_wind", lambda lat, lon: {})
    monkeypatch.setattr(sources, "openmeteo_waves", lambda lat, lon: {})
    monkeypatch.setattr(sources, "kartverket_water_level", lambda lat, lon: {})

    _, _, _, _, grid = A.gather(spot)
    assert grid is None


def test_gather_bruker_gate_koordinat_for_klasse_c(monkeypatch):
    """Klasse C har ikke offshore_point - grid_avstand_km skal regnes fra
    gate sitt koordinat i stedet (samme punkt gather() faktisk spoerr
    MET om for klasse C, se der)."""
    import sources

    spot = dict(_slagen())
    grid_lat, grid_lon = spot["gate"]["lat"] + 0.02, spot["gate"]["lon"] + 0.02

    monkeypatch.setattr(sources, "met_waves", _fake_met_waves_med_grid(grid_lat, grid_lon))
    monkeypatch.setattr(sources, "met_wind", lambda lat, lon: {})
    monkeypatch.setattr(sources, "openmeteo_waves", lambda lat, lon: {})
    monkeypatch.setattr(sources, "kartverket_water_level", lambda lat, lon: {})

    _, _, _, _, grid = A.gather(spot)
    expected_km = A.P.haversine_km(spot["gate"]["lat"], spot["gate"]["lon"], grid_lat, grid_lon)
    assert grid["avstand_km"] == round(expected_km, 2)


def test_gather_mock_gir_grid_none():
    mock = {"wind": {}, "waves": {}, "water": {}}
    _, _, _, errors, grid = A.gather(_saltstein(), mock=mock)
    assert grid is None
    assert errors == []


# --------------------------------------------------------------- model_rev


def test_model_rev_bruker_github_sha_hvis_satt(monkeypatch):
    A._model_rev.cache_clear()
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789fulllength")
    try:
        assert A._model_rev() == "abcdef012345"  # forste 12 tegn
    finally:
        A._model_rev.cache_clear()


def test_model_rev_faller_tilbake_til_git_rev_parse_uten_github_sha(monkeypatch):
    A._model_rev.cache_clear()
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    class _FakeCompleted:
        returncode = 0
        stdout = "deadbeef1234\n"

    def fake_run(cmd, **kw):
        assert cmd[:2] == ["git", "rev-parse"]
        return _FakeCompleted()

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    try:
        assert A._model_rev() == "deadbeef1234"
    finally:
        A._model_rev.cache_clear()


def test_model_rev_unknown_naar_baade_github_sha_og_git_mangler(monkeypatch):
    A._model_rev.cache_clear()
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def fake_run(cmd, **kw):
        raise FileNotFoundError("git ikke installert")

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    try:
        assert A._model_rev() == "unknown"
    finally:
        A._model_rev.cache_clear()


def test_model_rev_unknown_naar_git_rev_parse_feiler(monkeypatch):
    """Git finnes, men kommandoen feiler (f.eks. ikke i et git-repo) -
    ogsaa da "unknown", ikke en krasjende exception."""
    A._model_rev.cache_clear()
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    class _FakeFailed:
        returncode = 128
        stdout = ""

    def fake_run(cmd, **kw):
        return _FakeFailed()

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    try:
        assert A._model_rev() == "unknown"
    finally:
        A._model_rev.cache_clear()


def test_model_rev_caches_shelles_ikke_ut_flere_ganger(monkeypatch):
    A._model_rev.cache_clear()
    monkeypatch.setenv("GITHUB_SHA", "cachetest0123456789")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        raise AssertionError("skal ikke kalles - GITHUB_SHA er satt")

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    try:
        first = A._model_rev()
        second = A._model_rev()
        assert first == second == "cachetest012"
        assert calls == []  # subprocess aldri kalt, GITHUB_SHA vant
    finally:
        A._model_rev.cache_clear()


def test_append_shadow_log_skriver_model_rev_per_rad(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "OUT", tmp_path)
    A._model_rev.cache_clear()
    monkeypatch.setenv("GITHUB_SHA", "rowtest0123456789")
    try:
        A.append_shadow_log(_payload())
    finally:
        A._model_rev.cache_clear()

    import csv as _csv
    with (tmp_path / "shadow.csv").open() as f:
        rows = list(_csv.DictReader(f))
    assert rows[0]["model_rev"] == "rowtest01234"


# --------------------------------------------------------------- hs_vektet
#
# ordre 2026-09-03 (se rapport til bruker): hs_vektet mangler - tallet
# q_size faktisk regner paa (hs_eff * wf), i motsetning til hs_eff som er
# raatallet UTEN retningsvekting. Symptomet var at "tabellen" (se
# describe.py sin "Sum:"-linje og agent.py sin print_explain()) viste
# hs_eff, ikke tallet scoren faktisk brukte.


def test_hs_vektet_er_hs_eff_ganger_window_factor_for_klasse_ab():
    """Klasse A/B, retning et stykke fra vindussenteret - wf skal da vaere
    < 1.0 (cos^(2s) avtar monotont, se physics.window_factor()), og
    hs_vektet skal vaere NOYAKTIG hs_eff * wf, ikke hs_eff selv."""
    spot = _saltstein()  # swell_window (170, 260), senter 215
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=240, hs=2.0)
    h = A.score_hour(spot, ts, wind, waves, None, computed)

    wf = A.P.window_factor(computed["dir_eff"], spot)
    assert 0.0 < wf < 1.0, wf  # forutsetning for at testen skiller de to feltene
    assert h["hs_vektet"] == round(h["hs_eff"] * wf, 2)
    assert h["hs_vektet"] < h["hs_eff"]


def test_hs_vektet_hoyere_ved_vindussenter_enn_naer_kanten():
    """wf (og dermed hs_vektet, siden hs_eff er lik i begge tilfeller) skal
    vaere hoyest ved vindussenteret - cos^(2s) er hoyest praesist der og
    avtar monotont mot kanten (se physics.window_factor()). IKKE wf ~ 1.0
    ved senteret - directional_energy_fraction() returnerer andelen av en
    spredt energifordeling som havner innenfor sektoren, som er < 1.0 selv
    ved perfekt treff naar sektoren er smalere enn hele halvkula."""
    spot = _saltstein()  # swell_window (170, 260), senter 215
    ts_c, wind_c, waves_c, computed_c = _computed_med_retning(spot, wave_dir=215, hs=2.0)
    h_senter = A.score_hour(spot, ts_c, wind_c, waves_c, None, computed_c)

    ts_k, wind_k, waves_k, computed_k = _computed_med_retning(spot, wave_dir=250, hs=2.0)
    h_kant = A.score_hour(spot, ts_k, wind_k, waves_k, None, computed_k)

    assert h_senter["hs_eff"] == h_kant["hs_eff"] == 2.0
    assert h_senter["hs_vektet"] > h_kant["hs_vektet"]


def test_hs_vektet_lik_hs_eff_for_klasse_c_uansett_retning():
    """Klasse C: wf er hardkodet 1.0 fordi hs_eff for denne klassen ALLEREDE
    har gaatt gjennom gate sin retningsfiltrering (directional_energy_fraction()
    i propagate_through_gate()) - en ny vekting her ville dobbeltfiltrert.
    hs_vektet == hs_eff skal derfor holde uansett dir_eff, samme oppsett som
    test_window_ok_er_none_for_klasse_c_uansett_retning()."""
    spot = _slagen()  # swell_window [160, 200]
    ts = "2026-11-14T09:00:00+00:00"
    base_computed = {
        "source": "local+gate", "hs_eff": 2.0, "tp_eff": 5.0,
        "local_hs": 1.0, "local_tp": 6.0, "prop_hs": 1.0, "prop_tp": 6.0,
        "swell_hs": 1.0, "windsea_hs": 1.0,
        "local_wind_mean": 8.0, "local_fetch_km": 20.0, "local_duration_h": 4,
        "local_dir": 180.0, "gate_hs": 0.0, "gate_tp": 0.0, "gate_dir": None,
    }
    for dir_eff in (180.0, 30.0):  # innenfor vinduet, og klart utenfor det
        computed = dict(base_computed, dir_eff=dir_eff)
        h = A.score_hour(spot, ts, {"wind_speed": 1.0}, {}, None, computed, regional_wp=10.0)
        assert h["hs_vektet"] == h["hs_eff"], (dir_eff, h["hs_vektet"], h["hs_eff"])


def test_hs_vektet_i_shadow_fields():
    """hs_vektet skal vaere en del av shadow_schema.FIELDS, bakerst (se
    APPEND-ONLY-kontrakten i shadow_schema.py) - ellers logges ikke feltet
    til out/shadow.csv i det hele tatt."""
    import shadow_schema
    assert shadow_schema.FIELDS[-1] == "hs_vektet"
