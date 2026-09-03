"""
Enhetstester for agent.py sin regional energi-port (regional_wp_min/max i
spots.yaml, sjekket i score_hour()). Bruker ekte spot-definisjoner fra
spots.yaml via load_spots() og evaluate_class_ab() for aa faa et gyldig
`computed`-objekt - se ensemble.evaluate() sine krav til feltene der,
enklere aa bygge riktig via den ekte kjeden enn aa gjette strukturen.
"""

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


# ---------------------------------------------- window_factor i q_size (klasse A/B)
#
# ordre 2026-09-03 (rettet reell bug, se rapport til bruker): score_hour()
# regnet wf (physics.window_factor()) men brukte den aldri - kun window_ok
# (haard 0/1-grense ved vinduskanten) styrte q_size, mens ensemble.py sin
# per-medlem-scoring (som driver p_surf/stars) ALLTID har brukt window_factor()
# sin glatte taper. Testene under dekker fiksen: wf ganges inn i q_size sitt
# hs-grunnlag, og window_ok styrer ikke lenger en haard nullstilling.


def _computed_med_retning(spot, wave_dir, hs=2.0, tp=7.0):
    """Som _favorable_computed(), men med selvvalgt boelgeretning - for aa
    teste oppforsel naer/utenfor swell_window sin kant."""
    ts = "2026-11-14T09:00:00+00:00"
    wind = {ts: {"wind_speed": 1.0, "wind_from_direction": 0.0}}
    waves = {ts: {"hs": hs, "tp": tp, "wave_from_direction": wave_dir}}
    computed = A.evaluate_class_ab(spot, [ts], wind, waves)
    return ts, wind[ts], waves[ts], computed[0][1]


def test_score_hour_retning_god_innenfor_vinduet_gir_full_q_size():
    spot = _saltstein()  # swell_window (170, 260)
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=215)
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is True
    assert h["q_size"] > 0.0


def test_score_hour_retning_like_utenfor_vinduet_gir_delvis_q_size_ikke_null():
    """Kjernen i fiksen: 5 grader utenfor vindkanten skal IKKE lenger gi
    q_size=0 (den gamle haarde window_ok-grensen) - window_factor() sin
    taper skal gi en delvis, men positiv, verdi (matcher ensemble.py)."""
    spot = _saltstein()
    innenfor_ts, innenfor_wind, innenfor_waves, innenfor_computed = _computed_med_retning(spot, wave_dir=215)
    h_innenfor = A.score_hour(spot, innenfor_ts, innenfor_wind, innenfor_waves, None, innenfor_computed)

    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=265)  # 5 grader forbi 260
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is False
    assert 0.0 < h["q_size"] < h_innenfor["q_size"]


def test_score_hour_retning_langt_utenfor_vinduet_gir_null_q_size():
    """Utenfor window_factor() sin 15-graders taper skal q_size fortsatt
    vaere 0 - fiksen fjerner den haarde KANT-grensen, ikke all filtrering."""
    spot = _saltstein()
    ts, wind, waves, computed = _computed_med_retning(spot, wave_dir=290)  # 30 grader forbi 260
    h = A.score_hour(spot, ts, wind, waves, None, computed)
    assert h["window_ok"] is False
    assert h["q_size"] == 0.0


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
