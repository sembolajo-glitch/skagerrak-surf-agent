"""
Enhetstester for backtest_sessions.py. Ingen nettverk - _get_json()
erstattes med en fake, samme monkeypatch-konvensjon som test_sources.py
bruker for sources._get().

ordre 2026-09-02 (regional-energi-ombygging): testene for den gamle
spot-fysikk-pipelinen (build_hours_window/pick_target_hour/
backtest_session/backtest_all/print_session_table) er FJERNET - den
koden finnes ikke lenger, se backtest_sessions.py sin docstring for
hvorfor (ERA5-Ocean sitt grid er for grovt for lokal boelgehoyde her).
Erstattet med tester for gridcelle-rapporten og regional-energi-
pipelinen som tok over.
"""

import datetime as dt

import pytest
import requests

import agent as A
import backtest_sessions as B
import physics as P


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """RATE_LIMIT_PAUSE_S/RETRY_DELAYS_S skal ikke gjore testsuiten
    treg - vi tester ATFERDEN (antall kall, retries), ikke faktisk
    forloept tid. Autouse: gjelder hele denne testfila."""
    monkeypatch.setattr(B.time, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def _reset_http_stats():
    B.reset_http_stats()
    yield
    B.reset_http_stats()


# --------------------------------------------------------------- tidsvindu


def test_parse_time_window_vage_kategorier():
    assert B.parse_time_window("morgen") == (6, 10, False)
    assert B.parse_time_window("lunsj") == (11, 14, False)
    assert B.parse_time_window("ettermiddag") == (14, 19, False)
    assert B.parse_time_window("  LUNSJ  ") == (11, 14, False)


def test_parse_time_window_presist_klokkeslett_er_ikke_et_vindu():
    """exact=True - dette skal IKKE 'beste time i vindu'-velges, se
    pick_target_hour_regional()."""
    assert B.parse_time_window("15:30") == (15, 15, True)
    assert B.parse_time_window("07:30") == (7, 7, True)


def test_parse_time_window_ukjent_verdi_gir_feil():
    with pytest.raises(ValueError):
        B.parse_time_window("kveld")


def test_parse_time_window_ugyldig_klokkeslett_gir_feil():
    with pytest.raises(ValueError):
        B.parse_time_window("25:00")


# ------------------------------------------------------------ maalvelging


def test_pick_target_hour_regional_velger_hoyest_wp_i_vinduet():
    series = {
        "2018-08-18T06:00:00+00:00": {"hs": 1.0, "tp": 6.0, "wp": 10.0},
        "2018-08-18T08:00:00+00:00": {"hs": 1.8, "tp": 7.0, "wp": 55.0},
        "2018-08-18T10:00:00+00:00": {"hs": 1.3, "tp": 6.5, "wp": 30.0},
        "2018-08-18T14:00:00+00:00": {"hs": 2.5, "tp": 8.0, "wp": 90.0},  # utenfor morgen-vinduet
    }
    ts, row = B.pick_target_hour_regional(series, "2018-08-18", 6, 10)
    assert ts == "2018-08-18T08:00:00+00:00"
    assert row["wp"] == 55.0


def test_pick_target_hour_regional_exact_reduserer_til_en_time():
    series = {
        "2018-08-18T14:00:00+00:00": {"hs": 2.5, "tp": 8.0, "wp": 90.0},
        "2018-08-18T15:00:00+00:00": {"hs": 1.0, "tp": 6.0, "wp": 10.0},
    }
    ts, row = B.pick_target_hour_regional(series, "2018-08-18", 15, 15)
    assert ts == "2018-08-18T15:00:00+00:00"
    assert row["wp"] == 10.0


def test_pick_target_hour_regional_ingen_treff_gir_none():
    series = {"2018-08-18T08:00:00+00:00": {"hs": 1.0, "tp": 6.0, "wp": 10.0}}
    assert B.pick_target_hour_regional(series, "2018-08-19", 6, 10) is None
    assert B.pick_target_hour_regional(series, "2018-08-18", 12, 13) is None


def test_pick_target_hour_regional_ignorerer_andre_dager():
    series = {
        "2018-08-17T08:00:00+00:00": {"hs": 9.0, "tp": 20.0, "wp": 999.0},
        "2018-08-18T08:00:00+00:00": {"hs": 1.0, "tp": 6.0, "wp": 10.0},
    }
    ts, row = B.pick_target_hour_regional(series, "2018-08-18", 6, 10)
    assert ts == "2018-08-18T08:00:00+00:00"
    assert row["wp"] == 10.0


# ------------------------------------------------------------- skjevhet


def test_apply_bias_deler_hs_og_tp_lar_annet_urort():
    waves = {"t0": {"hs": 1.8, "tp": 6.6, "wave_from_direction": 190}}
    out = B._apply_bias(waves, {"hs": 1.8, "tp": 1.1})
    assert out["t0"]["hs"] == pytest.approx(1.0)
    assert out["t0"]["tp"] == pytest.approx(6.0)
    assert out["t0"]["wave_from_direction"] == 190
    assert waves["t0"]["hs"] == 1.8, "originalen skal ikke muteres"


def test_apply_bias_haandterer_manglende_verdier():
    waves = {"t0": {"hs": None, "tp": 6.0}}
    out = B._apply_bias(waves, {"hs": 1.8, "tp": 1.1})
    assert out["t0"]["hs"] is None
    assert out["t0"]["tp"] == pytest.approx(6.0 / 1.1)


def test_sample_bias_dates_inkluderer_oppgitte_datoer_og_er_reproduserbar():
    session_dates = ["2025-08-05", "2025-10-03", "2026-04-05"]
    dates = B.sample_bias_dates(session_dates, n=10, seed=42)
    assert set(session_dates) <= set(dates)
    assert len(dates) == len(session_dates) + 10
    assert dates == sorted(dates)
    assert all(d >= "2023-12-01" for d in dates)

    again = B.sample_bias_dates(session_dates, n=10, seed=42)
    assert dates == again, "samme seed skal gi samme utvalg"

    other_seed = B.sample_bias_dates(session_dates, n=10, seed=1)
    assert dates != other_seed


def _fake_get_json_bias(era5_hs, std_hs):
    """era5_hs/std_hs: konstante Hs-verdier for hhv. era5_ocean og
    standardmodellen, samme for alle 24 timer/dato - nok til aa teste
    at forholdet regnes riktig."""
    def get(url, params, timeout=30):
        n = 24
        hs = era5_hs if params.get("models") == "era5_ocean" else std_hs
        return {"hourly": {
            "time": [f"{params['start_date']}T{h:02d}:00" for h in range(n)],
            "wave_height": [hs] * n,
            "wave_direction": [190.0] * n,
            "wave_period": [7.0] * n,
        }}
    return get


def test_quantify_bias_regner_riktig_median_forhold(monkeypatch):
    monkeypatch.setattr(B, "_get_json", _fake_get_json_bias(era5_hs=1.8, std_hs=0.9))
    result = B.quantify_bias(58.930, 9.830, ["2024-01-01", "2024-01-02"])
    assert result["hs"]["median"] == pytest.approx(2.0)
    assert result["hs"]["n"] == 48
    assert result["n_dates_with_data"] == 2


def test_quantify_bias_null_par_gir_feil(monkeypatch):
    def get(url, params, timeout=30):
        return {"hourly": {"time": [], "wave_height": [], "wave_direction": [], "wave_period": []}}
    monkeypatch.setattr(B, "_get_json", get)
    with pytest.raises(RuntimeError):
        B.quantify_bias(58.930, 9.830, ["2024-01-01"])


def test_quantify_bias_fortsetter_naar_en_dato_feiler_helt(monkeypatch, capsys):
    """ordre 2026-09-02: et kall som feiler etter alle _get_json()-forsokene
    skal IKKE velte hele maalingen - datoen hoppes over og telles i
    n_calls_failed/failed_dates, resten av datoene brukes som normalt."""
    def get(url, params, timeout=30):
        if params["start_date"] == "2024-01-02":
            raise requests.Timeout("boom")
        n = 24
        hs = 1.8 if params.get("models") == "era5_ocean" else 0.9
        return {"hourly": {
            "time": [f"{params['start_date']}T{h:02d}:00" for h in range(n)],
            "wave_height": [hs] * n,
            "wave_direction": [190.0] * n,
            "wave_period": [7.0] * n,
        }}
    monkeypatch.setattr(B, "_get_json", get)

    result = B.quantify_bias(58.930, 9.830, ["2024-01-01", "2024-01-02", "2024-01-03"])
    assert result["n_calls_failed"] == 1
    assert result["failed_dates"][0][0] == "2024-01-02"
    assert result["n_dates_with_data"] == 2
    assert result["hs"]["n"] == 48  # 2 gode datoer x 24 timer
    assert result["hs"]["median"] == pytest.approx(2.0)


def test_quantify_bias_advarer_men_fortsetter_under_ti_par(monkeypatch, capsys):
    """Faerre enn 10 par skal IKKE lenger stoppe (raise) - kun en
    advarsel til stderr, se run_report()/print_bias_report() for hvor
    dette ogsaa vises til brukeren."""
    def get(url, params, timeout=30):
        hs = 1.8 if params.get("models") == "era5_ocean" else 0.9
        return {"hourly": {
            "time": [f"{params['start_date']}T00:00"],
            "wave_height": [hs], "wave_direction": [190.0], "wave_period": [7.0],
        }}
    monkeypatch.setattr(B, "_get_json", get)
    result = B.quantify_bias(58.930, 9.830, ["2024-01-01"])
    assert result["hs"]["n"] == 1
    assert "ADVARSEL" in capsys.readouterr().err


# ---------------------------------------------------------- HTTP-robusthet


def test_get_json_gir_opp_etter_alle_forsok(monkeypatch):
    calls = []

    def raising_get(url, params=None, timeout=None):
        calls.append(1)
        raise requests.Timeout("treg")

    monkeypatch.setattr(requests, "get", raising_get)
    with pytest.raises(requests.Timeout):
        B._get_json(B.WAVE_URL, {"start_date": "2024-01-01"})
    assert len(calls) == 1 + len(B.RETRY_DELAYS_S), "forste forsok + alle retries"
    assert B._http_stats["n_calls"] == 1
    assert B._http_stats["n_retried"] == 0, "ingen av forsokene lyktes"


def test_get_json_lykkes_etter_to_feil_telles_som_retried(monkeypatch):
    attempts = {"n": 0}

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"ok": True}

    def flaky_get(url, params=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise requests.ConnectionError("midlertidig")
        return FakeResp()

    monkeypatch.setattr(requests, "get", flaky_get)
    result = B._get_json(B.WAVE_URL, {})
    assert result == {"ok": True}
    assert attempts["n"] == 3
    assert B._http_stats["n_calls"] == 1
    assert B._http_stats["n_retried"] == 1


def test_get_json_lykkes_forste_gang_telles_ikke_som_retried(monkeypatch):
    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResp())
    B._get_json(B.WAVE_URL, {})
    assert B._http_stats["n_calls"] == 1
    assert B._http_stats["n_retried"] == 0


def test_reset_http_stats():
    B._http_stats["n_calls"] = 7
    B._http_stats["n_retried"] = 3
    B.reset_http_stats()
    assert B._http_stats == {"n_calls": 0, "n_retried": 0}


# ------------------------------------------------------------- henting


def test_fetch_era5_wind_normaliserer_tidsstempel(monkeypatch):
    def get(url, params, timeout=30):
        assert url == B.WIND_URL
        return {"hourly": {
            "time": ["2018-08-18T00:00", "2018-08-18T01:00"],
            "wind_speed_10m": [5.0, 6.0],
            "wind_direction_10m": [180.0, 190.0],
        }}
    monkeypatch.setattr(B, "_get_json", get)
    out = B.fetch_era5_wind(59.0, 10.0, "2018-08-18", "2018-08-18")
    assert out["2018-08-18T00:00:00+00:00"] == {"wind_speed": 5.0, "wind_from_direction": 180.0}


def test_fetch_era5_waves_default_model_omits_models_param(monkeypatch):
    seen = {}
    def get(url, params, timeout=30):
        seen["models"] = params.get("models")
        return {"hourly": {"time": [], "wave_height": [], "wave_direction": [], "wave_period": []}}
    monkeypatch.setattr(B, "_get_json", get)
    B.fetch_era5_waves(59.0, 10.0, "2024-01-01", "2024-01-01", model=None)
    assert "models" not in seen or seen["models"] is None
    B.fetch_era5_waves(59.0, 10.0, "2024-01-01", "2024-01-01", model="era5_ocean")
    assert seen["models"] == "era5_ocean"


# ------------------------------------------------------------ gridcelle-rapport


def test_spot_wave_point_klasse_c_bruker_gate_ellers_offshore_point():
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    assert B._spot_wave_point(spots_by_id["saltstein"]) == (58.930, 9.830)
    slagen = spots_by_id["slagen"]
    assert B._spot_wave_point(slagen) == (slagen["gate"]["lat"], slagen["gate"]["lon"])


def test_fetch_grid_cell_leser_toppniva_lat_lon(monkeypatch):
    def get(url, params, timeout=30):
        assert params["models"] == "era5_ocean"
        assert params["start_date"] == B.GRID_PROBE_DATE
        return {"latitude": 58.5, "longitude": 10.0, "hourly": {"time": [], "wave_height": []}}
    monkeypatch.setattr(B, "_get_json", get)
    assert B.fetch_grid_cell(58.930, 9.830) == (58.5, 10.0)


def test_report_spot_grid_cells_grupperer_kolliderende_spots(monkeypatch, capsys):
    """To vilkaarlige punkter skal havne i SAMME (avrundede) celle,
    resten spres - grupperingen skal fange den kollisjonen uansett
    hvilke id-er som faktisk kolliderer."""
    def get(url, params, timeout=30):
        lat = float(params["latitude"])
        # alt under 59.0 havner i EN celle, alt over i en annen - vilkaarlig,
        # bare for aa faa en faktisk kollisjon aa teste grupperingen paa.
        grid = (58.5, 10.0) if lat < 59.0 else (59.5, 10.0)
        return {"latitude": grid[0], "longitude": grid[1], "hourly": {"time": [], "wave_height": []}}
    monkeypatch.setattr(B, "_get_json", get)

    spots, _ = A.load_spots()
    rows = B.report_spot_grid_cells(spots)
    assert len(rows) == len(spots)

    by_grid = {}
    for r in rows:
        by_grid.setdefault(tuple(r["grid"]), []).append(r["id"])
    # De fem klasse C-spotene deler samme gate-koordinat (59.03, 10.52,
    # lat >= 59.0) AV DESIGN - skal havne i samme celle i denne faken.
    klasse_c_ids = {s["id"] for s in spots if s["klasse"] == "C"}
    assert any(klasse_c_ids <= set(ids) for ids in by_grid.values())

    out = capsys.readouterr().out
    assert "FLERE SPOTS I SAMME CELLE" in out


def test_report_spot_grid_cells_fortsetter_naar_en_spot_feiler(monkeypatch, capsys):
    def get(url, params, timeout=30):
        if abs(float(params["latitude"]) - 58.930) < 1e-6:
            raise requests.Timeout("boom")
        return {"latitude": 58.5, "longitude": 10.0, "hourly": {"time": [], "wave_height": []}}
    monkeypatch.setattr(B, "_get_json", get)

    spots, _ = A.load_spots()
    rows = B.report_spot_grid_cells(spots)
    assert len(rows) == len(spots) - 1, "saltstein sin feilende henting skal hoppes over, ikke velte resten"
    assert "FEIL" in capsys.readouterr().out


# -------------------------------------------------------------- regional energi


def _fake_get_json_regional(hs_by_hour=None, tp=6.0):
    """Fake for regional-energi-pipelinen: konstant Tp, valgfri per-time
    Hs (default stigende fra 0.5 til 1.7 gjennom doegnet - nok til aa
    teste at "hoyest wp i vinduet" faktisk skiller timer fra hverandre)."""
    def get(url, params, timeout=30):
        d0 = params["start_date"]
        n = 24
        hs = hs_by_hour or [0.5 + 0.05 * h for h in range(n)]
        return {"hourly": {
            "time": [f"{d0}T{h:02d}:00" for h in range(n)],
            "wave_height": hs,
            "wave_direction": [190.0] * n,
            "wave_period": [tp] * n,
        }}
    return get


def test_fetch_regional_wave_series_regner_wp_per_time(monkeypatch):
    monkeypatch.setattr(B, "_get_json", _fake_get_json_regional(hs_by_hour=[1.0] * 24, tp=6.0))
    series = B.fetch_regional_wave_series(58.930, 9.830, "2018-08-18")
    assert len(series) == 24
    row = series["2018-08-18T12:00:00+00:00"]
    assert row["hs"] == 1.0 and row["tp"] == 6.0
    assert row["wp"] == pytest.approx(round(P.wave_power(1.0, 6.0), 2))


def test_fetch_regional_wave_series_bias_reduserer_wp(monkeypatch):
    monkeypatch.setattr(B, "_get_json", _fake_get_json_regional(hs_by_hour=[2.0] * 24, tp=8.0))
    raw = B.fetch_regional_wave_series(58.930, 9.830, "2018-08-18")
    corrected = B.fetch_regional_wave_series(58.930, 9.830, "2018-08-18", bias={"hs": 2.0, "tp": 1.0})
    raw_wp = raw["2018-08-18T12:00:00+00:00"]["wp"]
    corr_wp = corrected["2018-08-18T12:00:00+00:00"]["wp"]
    assert corr_wp < raw_wp, "halvert Hs skal gi lavere wave_power"


def test_regional_energy_for_session_happy_path(monkeypatch):
    monkeypatch.setattr(B, "_get_json", _fake_get_json_regional())
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    session = {"dato": "2018-08-18", "tid": "morgen", "spot": "saltstein", "kvalitet": "4"}
    r = B.regional_energy_for_session(session, spots_by_id, (58.930, 9.830))
    assert "feil" not in r
    assert r["exact_time"] is False
    assert 6 <= int(r["valgt_tid_utc"][11:13]) <= 10
    assert r["wp"] > 0


def test_regional_energy_for_session_ukjent_spot():
    r = B.regional_energy_for_session(
        {"dato": "2020-01-01", "tid": "morgen", "spot": "ikke_finnes", "kvalitet": "3"},
        {}, (58.930, 9.830))
    assert "feil" in r
    assert "ikke_finnes" in r["feil"]


def test_regional_energy_for_session_ugyldig_tid():
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    r = B.regional_energy_for_session(
        {"dato": "2020-01-01", "tid": "kveld", "spot": "saltstein", "kvalitet": "3"},
        spots_by_id, (58.930, 9.830))
    assert "feil" in r


def test_regional_energy_for_session_nettverksfeil_fanges(monkeypatch):
    def raising(*a, **kw):
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(B, "_get_json", raising)
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    r = B.regional_energy_for_session(
        {"dato": "2020-01-01", "tid": "morgen", "spot": "saltstein", "kvalitet": "3"},
        spots_by_id, (58.930, 9.830))
    assert "feil" in r
    assert "boom" in r["feil"]


def test_regional_energy_for_session_ingen_data_for_dato(monkeypatch):
    def get(url, params, timeout=30):
        return {"hourly": {"time": [], "wave_height": [], "wave_direction": [], "wave_period": []}}
    monkeypatch.setattr(B, "_get_json", get)
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    r = B.regional_energy_for_session(
        {"dato": "2020-01-01", "tid": "morgen", "spot": "saltstein", "kvalitet": "3"},
        spots_by_id, (58.930, 9.830))
    assert "feil" in r


def test_regional_energy_all_kjorer_alle_oktene(monkeypatch):
    monkeypatch.setattr(B, "_get_json", _fake_get_json_regional())
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    sessions = [
        {"dato": "2018-08-18", "tid": "morgen", "spot": "saltstein", "kvalitet": "4"},
        {"dato": "2018-08-18", "tid": "16:00", "spot": "slagen", "kvalitet": "5"},
    ]
    rows = B.regional_energy_all(sessions, spots_by_id, (58.930, 9.830))
    assert len(rows) == 2
    assert all("feil" not in r for r in rows)


# -------------------------------------------------------------------- CSV


def test_sessions_historisk_csv_har_elleve_rader_og_riktige_kolonner():
    rows = B.load_sessions(str(B.ROOT / "sessions_historisk.csv"))
    assert len(rows) == 11
    assert set(rows[0].keys()) == {"dato", "tid", "spot", "kvalitet"}
    for row in rows:
        dt.date.fromisoformat(row["dato"])  # kaster hvis ugyldig
        B.parse_time_window(row["tid"])  # kaster hvis ugyldig
        assert 1 <= int(row["kvalitet"]) <= 5
