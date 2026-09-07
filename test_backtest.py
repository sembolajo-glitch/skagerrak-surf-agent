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
    assert len(hours) == 24
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
            BT._point_key(*wave_pt_old): _wave_series(base_hs=0.0),
        },
    }
    result = BT.report_old_vs_new_point("molen_odden", molen, merged, ({}, {}, {}))
    assert result["old_point"] == list(wave_pt_old)
    assert all(d == pytest.approx(3.0) for d in result["diffs"])
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


def test_saltstein_persistence_finner_halveringstid():
    hours = [
        {"time": f"h{i}", "hs_eff": v}
        for i, v in enumerate([1.0, 2.0, 4.0, 3.5, 2.5, 1.9, 1.0])
    ]
    result = BT.saltstein_persistence(hours)
    assert result["peak_hs_eff"] == 4.0
    assert result["peak_time"] == "h2"
    # halve toppen er 2.0 - forste verdi ETTER toppen under 2.0 er 1.9, 3 timer etter toppen (idx 5, peak idx 2)
    assert result["half_life_h"] == 3


def test_saltstein_persistence_ingen_halvering_innenfor_vinduet():
    hours = [{"time": f"h{i}", "hs_eff": v} for i, v in enumerate([1.0, 4.0, 3.9, 3.8])]
    result = BT.saltstein_persistence(hours)
    assert result["half_life_h"] is None
    assert result["hs_eff_at_window_end"] == 3.8


def test_saltstein_persistence_siste_surfbare_time():
    hours = [
        {"time": f"h{i}", "hs_eff": v}
        for i, v in enumerate([1.0, 4.0, 3.0, 1.5, 0.5])
    ]
    result = BT.saltstein_persistence(hours, peak_min_hs=1.9)
    assert result["last_surfable_time"] == "h2"  # 3.0 >= 1.9, 1.5 er ikke


def test_saltstein_persistence_tom_liste_gir_none():
    assert BT.saltstein_persistence([]) is None


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
