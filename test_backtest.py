"""
Enhetstester for scripts/backtest.py. Ingen nettverk - fetch_openmeteo_wind/
fetch_openmeteo_waves erstattes med fakes, samme monkeypatch-konvensjon
som test_backtest_sessions.py bruker for backtest_sessions._get_json().

Scoring-funksjonene i agent.py (evaluate_class_ab/evaluate_class_c/
score_hour) testes IKKE her - de har sine egne tester i test_agent.py.
Dette testes at backtest.py FAKTISK KALLER dem riktig (samme punkt-
konvensjon, riktig dict-form paa vind/boelgedata), ikke at scoringen selv
er riktig.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import agent as A
import backtest as BT


def _wind_series(base_speed=8.0, base_dir=190, n=24):
    return {
        f"2026-09-05T{h:02d}:00": {"wind_speed": base_speed, "wind_from_direction": base_dir}
        for h in range(n)
    }


def _wave_series(base_hs=2.0, base_tp=8.0, base_dir=190, n=24):
    return {
        f"2026-09-05T{h:02d}:00": {"hs": base_hs, "tp": base_tp, "wave_from_direction": base_dir}
        for h in range(n)
    }


# --------------------------------------------------------------- cache


def test_load_fixture_manglende_fil_gir_tom_cache(tmp_path):
    cache = BT.load_fixture("2026-09-05", fixtures_dir=tmp_path)
    assert cache["date"] == "2026-09-05"
    assert cache["wind_by_point"] == {}
    assert cache["wave_by_point"] == {}


def test_save_and_load_fixture_roundtrip(tmp_path):
    cache = BT.load_fixture("2026-09-05", fixtures_dir=tmp_path)
    cache["wind_by_point"]["59.00000,10.00000"] = _wind_series()
    path = BT.save_fixture(cache, fixtures_dir=tmp_path)
    assert path.exists()

    reloaded = BT.load_fixture("2026-09-05", fixtures_dir=tmp_path)
    assert reloaded["wind_by_point"]["59.00000,10.00000"] == _wind_series()


def test_get_wind_bruker_cache_uten_aa_hente_paa_nytt(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(BT, "fetch_openmeteo_wind", lambda lat, lon, date: calls.append(1) or _wind_series())

    cache = BT.load_fixture("2026-09-05", fixtures_dir=tmp_path)
    BT.get_wind(cache, 59.0, 10.0)
    assert len(calls) == 1
    BT.get_wind(cache, 59.0, 10.0)  # samme punkt igjen - skal IKKE hente på nytt
    assert len(calls) == 1


def test_get_wind_refresh_henter_paa_nytt_selv_om_cachet(monkeypatch):
    calls = []
    monkeypatch.setattr(BT, "fetch_openmeteo_wind", lambda lat, lon, date: calls.append(1) or _wind_series())
    cache = {"date": "2026-09-05", "wind_by_point": {}, "wave_by_point": {}}
    BT.get_wind(cache, 59.0, 10.0)
    BT.get_wind(cache, 59.0, 10.0, refresh=True)
    assert len(calls) == 2


def test_process_date_null_nettverkskall_naar_fixture_alt_daekker(tmp_path, monkeypatch):
    """Kjernen i offline-kontrakten: finnes fixturen og daekker den alt
    som trengs, skal INGEN av fetch-funksjonene kalles."""
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    spot_ids = {"saltstein", "molen_odden"}

    # bygg en fixture som allerede daekker alt process_date() ville trengt
    cache = BT.load_fixture("2026-09-05", fixtures_dir=tmp_path)
    needed = set()
    for sid in spot_ids:
        wind_pt, wave_pt = A.wave_wind_points(spots_by_id[sid])
        needed.add(("wind", wind_pt))
        needed.add(("wave", wave_pt))
    needed.add(("wave", BT.OLD_OFFSHORE_POINTS["molen_odden"]))
    needed.add(("wave", tuple(spots_by_id["saltstein"]["offshore_point"])))
    for kind, (lat, lon) in needed:
        key = BT._point_key(lat, lon)
        if kind == "wind":
            cache["wind_by_point"][key] = _wind_series()
        else:
            cache["wave_by_point"][key] = _wave_series()
    BT.save_fixture(cache, fixtures_dir=tmp_path)

    def _boom(*a, **kw):
        raise AssertionError("nettverk kalt selv om fixturen alt daekket alt")

    monkeypatch.setattr(BT, "fetch_openmeteo_wind", _boom)
    monkeypatch.setattr(BT, "fetch_openmeteo_waves", _boom)

    BT.process_date("2026-09-05", spots_by_id, spot_ids, refresh=False, fixtures_dir=tmp_path)


# --------------------------------------------------------------- merge


def test_merge_caches_slaar_sammen_flere_datoer():
    c1 = {"wind_by_point": {"p": {"t1": {"a": 1}}}, "wave_by_point": {}}
    c2 = {"wind_by_point": {"p": {"t2": {"a": 2}}}, "wave_by_point": {}}
    merged = BT.merge_caches([c1, c2])
    assert merged["wind_by_point"]["p"] == {"t1": {"a": 1}, "t2": {"a": 2}}


# ----------------------------------------------------------- regional


def test_regional_series_regner_wave_power_fra_saltsteins_punkt():
    spots, _ = A.load_spots()
    saltstein = next(s for s in spots if s["id"] == "saltstein")
    lat, lon = saltstein["offshore_point"]
    key = BT._point_key(lat, lon)
    merged = {"wave_by_point": {key: {"2026-09-05T12:00": {"hs": 2.0, "tp": 8.0}}}}

    wp, hs, tp = BT.regional_series(merged, saltstein)
    assert wp["2026-09-05T12:00"] == round(A.P.wave_power(2.0, 8.0), 1)
    assert hs["2026-09-05T12:00"] == 2.0
    assert tp["2026-09-05T12:00"] == 8.0


# ------------------------------------------------------------- scoring


def test_score_spot_klasse_ab_bruker_offshore_point():
    spots, _ = A.load_spots()
    saltstein = next(s for s in spots if s["id"] == "saltstein")
    wind_pt, wave_pt = A.wave_wind_points(saltstein)
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): _wind_series()},
        "wave_by_point": {BT._point_key(*wave_pt): _wave_series()},
    }
    hours = BT.score_spot(saltstein, merged, {}, {}, {})
    assert len(hours) == 24
    assert all(h["hs_eff"] == 2.0 for h in hours)
    assert "stars" in hours[0] and "p_surf" in hours[0]


def test_score_spot_wave_point_override_bruker_annet_punkt():
    spots, _ = A.load_spots()
    molen = next(s for s in spots if s["id"] == "molen_odden")
    wind_pt, wave_pt_new = A.wave_wind_points(molen)
    wave_pt_old = BT.OLD_OFFSHORE_POINTS["molen_odden"]
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): _wind_series()},
        "wave_by_point": {
            BT._point_key(*wave_pt_new): _wave_series(base_hs=3.0),
            BT._point_key(*wave_pt_old): _wave_series(base_hs=0.5),
        },
    }
    hours_new = BT.score_spot(molen, merged, {}, {}, {})
    hours_old = BT.score_spot(molen, merged, {}, {}, {}, wave_point_override=wave_pt_old)
    assert hours_new[0]["hs_eff"] == 3.0
    assert hours_old[0]["hs_eff"] == 0.5


def test_score_spot_klasse_c_bruker_gate():
    spots, _ = A.load_spots()
    slagen = next(s for s in spots if s["id"] == "slagen")
    wind_pt, wave_pt = A.wave_wind_points(slagen)
    assert wave_pt == (slagen["gate"]["lat"], slagen["gate"]["lon"])
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): _wind_series()},
        "wave_by_point": {BT._point_key(*wave_pt): _wave_series()},
    }
    hours = BT.score_spot(slagen, merged, {}, {}, {})
    # 23, ikke 24: time 0 har ingen vindhistorikk aa se bakover paa (cold
    # start), saa build_local_sea() sin varighet blir 1 t der - fysisk
    # umulig kort Tp (se _er_gyldig_sjo(), ordre 2026-09-08), hoppet over.
    assert len(hours) == 23
    assert hours[0]["source"] == "local+gate"


def test_score_spot_ingen_overlappende_tider_gir_tom_liste():
    spots, _ = A.load_spots()
    saltstein = next(s for s in spots if s["id"] == "saltstein")
    merged = {"wind_by_point": {}, "wave_by_point": {}}
    assert BT.score_spot(saltstein, merged, {}, {}, {}) == []


# --------------------------------------------------- gammelt vs nytt punkt


def test_report_old_vs_new_point_molen_odden(capsys):
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    molen = spots_by_id["molen_odden"]
    wind_pt, wave_pt_new = A.wave_wind_points(molen)
    wave_pt_old = BT.OLD_OFFSHORE_POINTS["molen_odden"]
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): _wind_series()},
        "wave_by_point": {
            BT._point_key(*wave_pt_new): _wave_series(base_hs=3.0),
            BT._point_key(*wave_pt_old): _wave_series(base_hs=0.5),
        },
    }
    result = BT.report_old_vs_new_point("molen_odden", molen, merged, ({}, {}, {}))
    assert result["old_point"] == list(wave_pt_old)
    assert all(d == pytest.approx(2.5) for d in result["diffs"])
    assert "GAMMELT offshore_point" in capsys.readouterr().out


def test_report_old_vs_new_point_saltstein_har_ingen_gammelt_punkt(capsys):
    """ordre 2026-09-07 (se rapport til bruker): Saltstein sitt
    offshore_point har ALDRI flyttet (verifisert mot git-historikken,
    ikke antatt) - ingen oppdiktet 'gammelt punkt' skal brukes."""
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    saltstein = spots_by_id["saltstein"]
    wind_pt, wave_pt = A.wave_wind_points(saltstein)
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): _wind_series()},
        "wave_by_point": {BT._point_key(*wave_pt): _wave_series()},
    }
    result = BT.report_old_vs_new_point("saltstein", saltstein, merged, ({}, {}, {}))
    assert result["old_point"] is None
    assert "hours_old" not in result
    captured = capsys.readouterr()
    assert "ingen gammelt punkt" in captured.out


# --------------------------------------------------------- holdbarhet


def test_saltstein_persistence_finner_halveringstid_paa_energi():
    """ordre 2026-09-08 (se rapport til bruker): halvering maales na paa
    P = wave_power(hs, tp), ikke hs alene - konstant Tp her isolerer at
    halveringen faktisk styres av P (som med konstant Tp er ren hs^2, saa
    halve P tilsvarer halve hs^2, IKKE halve hs - se tallene under)."""
    hours = [
        {"time": f"h{i}", "hs_eff": hs, "tp_eff": 8.0}
        for i, hs in enumerate([1.0, 2.0, 4.0, 3.5, 2.5, 1.9, 1.0])
    ]
    result = BT.saltstein_persistence(hours)
    assert result["peak_hs_eff"] == 4.0
    assert result["peak_time"] == "h2"
    assert result["peak_p"] == pytest.approx(A.P.wave_power(4.0, 8.0), abs=0.1)
    # halve topp-P (konstant Tp) tilsvarer hs = 4.0/sqrt(2) = 2.83 - forste
    # verdi ETTER toppen under det er 2.5, 2 timer etter toppen (idx 4, peak idx 2)
    assert result["half_life_h_energy"] == 2
    assert result["half_point_tp_eff"] == 8.0
    assert result["peak_tp_eff"] == 8.0


def test_saltstein_persistence_ulik_tp_skiller_seg_fra_hs_alene():
    """Kjernen i fiksen: samme hs ved topp og ved 'halveringspunktet',
    men Tp har falt kraftig - skal telle som halvert PAA ENERGI selv om
    hs alene aldri halveres."""
    hours = [
        {"time": "h0", "hs_eff": 1.3, "tp_eff": 7.9},
        {"time": "h1", "hs_eff": 1.2, "tp_eff": 6.0},
        {"time": "h2", "hs_eff": 0.96, "tp_eff": 4.7},  # naer samme hs, mye kortere tp
    ]
    result = BT.saltstein_persistence(hours)
    assert result["half_life_h_hs"] is None  # hs alene halveres aldri (1.3 -> 0.65)
    assert result["half_life_h_energy"] == 2  # men energien har falt under halve
    assert result["half_point_tp_eff"] == 4.7
    assert result["peak_tp_eff"] == 7.9


def test_saltstein_persistence_ingen_halvering_innenfor_vinduet():
    hours = [{"time": f"h{i}", "hs_eff": v, "tp_eff": 8.0} for i, v in enumerate([1.0, 4.0, 3.9, 3.8])]
    result = BT.saltstein_persistence(hours)
    assert result["half_life_h_energy"] is None
    assert result["p_at_window_end"] == pytest.approx(A.P.wave_power(3.8, 8.0), abs=0.1)


def test_saltstein_persistence_siste_surfbare_time():
    hours = [
        {"time": f"h{i}", "hs_eff": v, "tp_eff": 8.0}
        for i, v in enumerate([1.0, 4.0, 3.0, 1.5, 0.5])
    ]
    result = BT.saltstein_persistence(hours, peak_min_hs=1.9)
    assert result["last_surfable_time"] == "h2"  # 3.0 >= 1.9, 1.5 er ikke


def test_saltstein_persistence_mangler_tp_eff_telles_som_ugyldig():
    """En time med hs_eff men uten tp_eff (f.eks. filtrert bort av
    _er_gyldig_sjo() lenger opp i kjeden) skal ikke kunne bli 'toppen' -
    persistensanalysen trenger begge for aa regne P."""
    hours = [
        {"time": "h0", "hs_eff": 9.0, "tp_eff": None},  # ville vaert "toppen" paa hs alene
        {"time": "h1", "hs_eff": 1.0, "tp_eff": 8.0},
    ]
    result = BT.saltstein_persistence(hours)
    assert result["peak_time"] == "h1"


def test_saltstein_persistence_tom_liste_gir_none():
    assert BT.saltstein_persistence([]) is None


def test_saltstein_persistence_kontrollregning_5_til_6_sept():
    """ordre 2026-09-08: den eksplisitte kontrollregningen fra rapporten
    til bruker, reprodusert her som en fast regresjonstest - IKKE bare
    manuelt sjekket i en interaktiv kjoring."""
    hours = [
        {"time": "2026-09-05T15:00", "hs_eff": 1.30, "tp_eff": 7.9},
        {"time": "2026-09-06T00:00", "hs_eff": 0.94, "tp_eff": 7.0},
    ]
    result = BT.saltstein_persistence(hours)
    assert result["peak_p"] == pytest.approx(6.5, abs=0.1)
    assert result["half_point_p"] == pytest.approx(3.0, abs=0.1)
    assert result["half_life_h_energy"] == 1  # eneste punkt etter toppen i dette utvalget


# ------------------------------------------------------------- validering


def test_er_gyldig_sjo_avviser_kort_periode():
    """ordre 2026-09-08 (se rapport til bruker): Tp under 2 s finnes ikke
    i naturen - fysisk umulig, skal telle som manglende data."""
    assert BT._er_gyldig_sjo({"hs_eff": 1.0, "tp_eff": 1.9}) is False
    assert BT._er_gyldig_sjo({"hs_eff": 1.0, "tp_eff": 2.0}) is True


def test_er_gyldig_sjo_avviser_naermest_flatt():
    assert BT._er_gyldig_sjo({"hs_eff": 0.04, "tp_eff": 8.0}) is False
    assert BT._er_gyldig_sjo({"hs_eff": 0.05, "tp_eff": 8.0}) is True


def test_er_gyldig_sjo_avviser_manglende_verdier():
    assert BT._er_gyldig_sjo({"hs_eff": None, "tp_eff": 8.0}) is False
    assert BT._er_gyldig_sjo({"hs_eff": 1.0, "tp_eff": None}) is False


def test_score_spot_hopper_over_fysisk_umulige_rader(capsys):
    """Kjernen i fiksen (ordre 2026-09-08, se rapport til bruker): en rad
    med Tp under 2 s (JONSWAP-vekst fra en naermest fetch-los retning,
    se ordren ved MIN_TP_S i backtest.py) skal IKKE naa score_hour() i
    det hele tatt - hoppes over, ikke scores som om den var ekte.

    Reproduserer det ekte funnet fra rapporten: svaert lav vind (3,5 m/s)
    fra 309 grader - en retning med naermest null lokal fetch i slagen
    sin egen fetch_km-tabell (~0,1 km, VNV/NV-sektoren) - gir en fysisk
    umulig, fetch-begrenset "sjo" (Tp under et sekund)."""
    spots, _ = A.load_spots()
    slagen = next(s for s in spots if s["id"] == "slagen")
    wind_pt, wave_pt = A.wave_wind_points(slagen)

    wind = _wind_series(base_speed=3.5, base_dir=309, n=1)
    wave = _wave_series(n=1)  # klasse C bruker ikke denne direkte til hs_eff, men trengs for `times`
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): wind},
        "wave_by_point": {BT._point_key(*wave_pt): wave},
    }
    hours = BT.score_spot(slagen, merged, {}, {}, {})
    assert hours == []  # eneste time i utvalget var den ugyldige - ingenting igjen aa score
    assert "hoppet over" in capsys.readouterr().err


def test_score_spot_beholder_gyldige_rader_ved_siden_av_ugyldige():
    """Motsatt av testen over: en gyldig time skal IKKE forsvinne bare
    fordi en ANNEN time samme kjoring var ugyldig."""
    spots, _ = A.load_spots()
    saltstein = next(s for s in spots if s["id"] == "saltstein")
    wind_pt, wave_pt = A.wave_wind_points(saltstein)
    wave = {
        "2026-09-05T00:00": {"hs": 0.02, "tp": 0.4, "wave_from_direction": 190},  # ugyldig
        "2026-09-05T01:00": {"hs": 2.0, "tp": 8.0, "wave_from_direction": 190},  # gyldig
    }
    wind = _wind_series(n=2)
    merged = {
        "wind_by_point": {BT._point_key(*wind_pt): wind},
        "wave_by_point": {BT._point_key(*wave_pt): wave},
    }
    hours = BT.score_spot(saltstein, merged, {}, {}, {})
    assert len(hours) == 1
    assert hours[0]["time"] == "2026-09-05T01:00"
    assert hours[0]["hs_eff"] == 2.0


def test_main_kjorer_helt_offline_naar_fixture_daekker_alt(tmp_path, monkeypatch, capsys):
    """Integrasjonstest: hele main()-loepet (CLI-parsing -> process_date ->
    scoring -> rapportering) for en dato der fixturen ALT daekker alt som
    trengs, skal ikke gjore ett eneste nettverkskall - kjernen i
    "cache som fixture"-kontrakten (del B, punkt 3)."""
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}

    date = "2026-09-05"
    cache = BT.load_fixture(date, fixtures_dir=tmp_path)
    for sid in ("saltstein", "molen_odden"):
        wind_pt, wave_pt = A.wave_wind_points(spots_by_id[sid])
        cache["wind_by_point"][BT._point_key(*wind_pt)] = _wind_series()
        cache["wave_by_point"][BT._point_key(*wave_pt)] = _wave_series()
    cache["wave_by_point"][BT._point_key(*BT.OLD_OFFSHORE_POINTS["molen_odden"])] = _wave_series(base_hs=0.3)
    BT.save_fixture(cache, fixtures_dir=tmp_path)

    def _boom(*a, **kw):
        raise AssertionError("main() gjorde et nettverkskall selv om fixturen daekket alt")

    monkeypatch.setattr(BT, "fetch_openmeteo_wind", _boom)
    monkeypatch.setattr(BT, "fetch_openmeteo_waves", _boom)
    monkeypatch.setattr(sys, "argv", [
        "backtest.py", date,
        "--spots", "saltstein,molen_odden",
        "--compare-old-point", "molen_odden,saltstein",
        "--fixtures-dir", str(tmp_path),
    ])

    BT.main()
    out = capsys.readouterr().out
    assert "Datakilde: Open-Meteo" in out
    assert "Saltstein-holdbarhet" in out
    assert "GAMMELT offshore_point" in out  # molen_odden
    assert "ingen gammelt punkt" in out  # saltstein


def test_saltstein_persistence_spenner_flere_cacher_via_merge():
    """Persistensanalysen skal kunne bruke en sammenhengende serie bygd
    fra FLERE datoers cache (merge_caches()), ikke bare en enkelt dags
    24 timer - se main() sin bruk naar flere datoer er oppgitt."""
    spots, _ = A.load_spots()
    saltstein = next(s for s in spots if s["id"] == "saltstein")
    wind_pt, wave_pt = A.wave_wind_points(saltstein)

    day1_wave = {f"2026-09-05T{h:02d}:00": {"hs": 1.0 + h * 0.3, "tp": 8.0} for h in range(24)}
    day2_wave = {f"2026-09-06T{h:02d}:00": {"hs": max(0.5, 7.0 - h * 0.3), "tp": 7.0} for h in range(24)}
    day1_wind = _wind_series(n=24)
    day2_wind = {f"2026-09-06T{h:02d}:00": {"wind_speed": 8.0, "wind_from_direction": 190} for h in range(24)}

    c1 = {"wind_by_point": {BT._point_key(*wind_pt): day1_wind},
          "wave_by_point": {BT._point_key(*wave_pt): day1_wave}}
    c2 = {"wind_by_point": {BT._point_key(*wind_pt): day2_wind},
          "wave_by_point": {BT._point_key(*wave_pt): day2_wave}}
    merged = BT.merge_caches([c1, c2])

    hours = BT.score_spot(saltstein, merged, {}, {}, {})
    assert len(hours) == 48
    result = BT.saltstein_persistence(hours, peak_min_hs=saltstein["min_hs"])
    assert result["peak_time"].startswith("2026-09-05T23")  # hs stiger hele dag 1
    assert result["n_hours_after_peak"] == 24
