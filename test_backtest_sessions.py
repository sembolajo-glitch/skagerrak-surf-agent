"""
Enhetstester for backtest_sessions.py. Ingen nettverk - _get_json()
erstattes med en fake, samme monkeypatch-konvensjon som test_sources.py
bruker for sources._get().
"""

import datetime as dt

import pytest
import requests

import agent as A
import backtest_sessions as B


# --------------------------------------------------------------- tidsvindu


def test_parse_time_window_vage_kategorier():
    assert B.parse_time_window("morgen") == (6, 10, False)
    assert B.parse_time_window("lunsj") == (11, 14, False)
    assert B.parse_time_window("ettermiddag") == (14, 19, False)
    assert B.parse_time_window("  LUNSJ  ") == (11, 14, False)


def test_parse_time_window_presist_klokkeslett_er_ikke_et_vindu():
    """exact=True - dette skal IKKE 'beste time i vindu'-velges, se
    pick_target_hour()."""
    assert B.parse_time_window("15:30") == (15, 15, True)
    assert B.parse_time_window("07:30") == (7, 7, True)


def test_parse_time_window_ukjent_verdi_gir_feil():
    with pytest.raises(ValueError):
        B.parse_time_window("kveld")


def test_parse_time_window_ugyldig_klokkeslett_gir_feil():
    with pytest.raises(ValueError):
        B.parse_time_window("25:00")


# ------------------------------------------------------------ maalvelging


def test_pick_target_hour_velger_hoyest_score_i_vinduet():
    hours = [
        {"time": "2018-08-18T06:00:00+00:00", "score": 10.0},
        {"time": "2018-08-18T08:00:00+00:00", "score": 55.0},
        {"time": "2018-08-18T10:00:00+00:00", "score": 30.0},
        {"time": "2018-08-18T14:00:00+00:00", "score": 90.0},  # utenfor morgen-vinduet
    ]
    best = B.pick_target_hour(hours, "2018-08-18", 6, 10)
    assert best["time"] == "2018-08-18T08:00:00+00:00"


def test_pick_target_hour_exact_reduserer_til_en_time():
    hours = [
        {"time": "2018-08-18T14:00:00+00:00", "score": 90.0},
        {"time": "2018-08-18T15:00:00+00:00", "score": 10.0},
    ]
    assert B.pick_target_hour(hours, "2018-08-18", 15, 15)["score"] == 10.0


def test_pick_target_hour_ingen_treff_gir_none():
    hours = [{"time": "2018-08-18T08:00:00+00:00", "score": 10.0}]
    assert B.pick_target_hour(hours, "2018-08-19", 6, 10) is None
    assert B.pick_target_hour(hours, "2018-08-18", 12, 13) is None


def test_pick_target_hour_ignorerer_andre_dager_i_arrayet():
    """hours inneholder ofte doegnet FOR ogsaa (lookback-kontekst, se
    build_hours_window) - den skal ikke plukkes som kandidat."""
    hours = [
        {"time": "2018-08-17T08:00:00+00:00", "score": 999.0},
        {"time": "2018-08-18T08:00:00+00:00", "score": 10.0},
    ]
    assert B.pick_target_hour(hours, "2018-08-18", 6, 10)["score"] == 10.0


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


def test_quantify_bias_for_faa_par_gir_feil(monkeypatch):
    def get(url, params, timeout=30):
        return {"hourly": {"time": [], "wave_height": [], "wave_direction": [], "wave_period": []}}
    monkeypatch.setattr(B, "_get_json", get)
    with pytest.raises(RuntimeError):
        B.quantify_bias(58.930, 9.830, ["2024-01-01"])


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


# ---------------------------------------------------------- full pipeline


def _fake_get_json_full(hs_by_model=None):
    """Generisk fake for build_hours_window(): samme konstante vaerdag
    for enhver lat/lon/dato, nok til aa kjore hele evaluate_class_ab/
    evaluate_class_c/score_hour-kjeden uten aa krasje. hs_by_model lar
    testen skille era5_ocean fra standardmodellen naar det trengs."""
    hs_by_model = hs_by_model or {"era5_ocean": 2.0, None: 1.0}

    def get(url, params, timeout=30):
        d0 = dt.date.fromisoformat(params["start_date"])
        d1 = dt.date.fromisoformat(params["end_date"])
        times = []
        d = d0
        while d <= d1:
            times += [f"{d.isoformat()}T{h:02d}:00" for h in range(24)]
            d += dt.timedelta(days=1)
        n = len(times)
        if url == B.WIND_URL:
            return {"hourly": {
                "time": times,
                "wind_speed_10m": [3.0] * n,
                "wind_direction_10m": [40.0] * n,
            }}
        assert url == B.WAVE_URL
        hs = hs_by_model.get(params.get("models"), 1.5)
        return {"hourly": {
            "time": times,
            "wave_height": [hs] * n,
            "wave_direction": [190.0] * n,
            "wave_period": [8.0] * n,
        }}
    return get


@pytest.mark.parametrize("spot_id", ["saltstein", "jomfruland_ost", "hvasser_sando", "slagen"])
def test_build_hours_window_kjorer_for_alle_klasser(monkeypatch, spot_id):
    """Klasse A (saltstein/jomfruland_ost), B (hvasser_sando) og C
    (slagen) - samme kode, ingen ny fysikk. Verifiserer bare at
    pipelinen produserer 48 timer med de standardfeltene score_hour()
    alltid returnerer, uansett klasse."""
    monkeypatch.setattr(B, "_get_json", _fake_get_json_full())
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}

    hours = B.build_hours_window(spots_by_id[spot_id], "2018-08-18", spots_by_id["saltstein"])
    assert len(hours) == 48
    for key in ("time", "score", "stars", "p_surf", "regional_wp", "regional_gate_closed"):
        assert key in hours[0]
    assert all(h["lead_h"] == 0.0 for h in hours), "reanalyse - lead_h skal alltid vaere 0"


def test_build_hours_window_bias_reduserer_hs(monkeypatch):
    monkeypatch.setattr(B, "_get_json", _fake_get_json_full())
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}

    raw = B.build_hours_window(spots_by_id["saltstein"], "2018-08-18", spots_by_id["saltstein"])
    corrected = B.build_hours_window(spots_by_id["saltstein"], "2018-08-18", spots_by_id["saltstein"],
                                      bias={"hs": 2.0, "tp": 1.0})
    raw_hs = next(h["hs_eff"] for h in raw if h["time"].startswith("2018-08-18T12"))
    corr_hs = next(h["hs_eff"] for h in corrected if h["time"].startswith("2018-08-18T12"))
    assert corr_hs == pytest.approx(raw_hs / 2.0, rel=0.05)


# -------------------------------------------------------------- feilstier


def test_backtest_session_ukjent_spot():
    r = B.backtest_session({"dato": "2020-01-01", "tid": "morgen", "spot": "ikke_finnes", "kvalitet": "3"}, {})
    assert "feil" in r
    assert "ikke_finnes" in r["feil"]


def test_backtest_session_ugyldig_tid():
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    r = B.backtest_session({"dato": "2020-01-01", "tid": "kveld", "spot": "saltstein", "kvalitet": "3"}, spots_by_id)
    assert "feil" in r


def test_backtest_session_nettverksfeil_fanges(monkeypatch):
    def raising(*a, **kw):
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(B, "_get_json", raising)
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    r = B.backtest_session({"dato": "2020-01-01", "tid": "morgen", "spot": "saltstein", "kvalitet": "3"}, spots_by_id)
    assert "feil" in r
    assert "boom" in r["feil"]


def test_backtest_session_ingen_data_for_dato(monkeypatch):
    def get(url, params, timeout=30):
        return {"hourly": {"time": [], "wave_height": [], "wave_direction": [],
                            "wave_period": [], "wind_speed_10m": [], "wind_direction_10m": []}}
    monkeypatch.setattr(B, "_get_json", get)
    spots, _ = A.load_spots()
    spots_by_id = {s["id"]: s for s in spots}
    r = B.backtest_session({"dato": "2020-01-01", "tid": "morgen", "spot": "saltstein", "kvalitet": "3"}, spots_by_id)
    assert "feil" in r


# -------------------------------------------------------------------- CSV


def test_sessions_historisk_csv_har_elleve_rader_og_riktige_kolonner():
    rows = B.load_sessions(str(B.ROOT / "sessions_historisk.csv"))
    assert len(rows) == 11
    assert set(rows[0].keys()) == {"dato", "tid", "spot", "kvalitet"}
    for row in rows:
        dt.date.fromisoformat(row["dato"])  # kaster hvis ugyldig
        B.parse_time_window(row["tid"])  # kaster hvis ugyldig
        assert 1 <= int(row["kvalitet"]) <= 5
