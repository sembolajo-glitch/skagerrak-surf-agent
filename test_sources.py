"""
Enhetstester for sources.py sin Open-Meteo EWAM/GWAM-sammenslaaing.
Ingen nettverk - _get() erstattes med en fake som svarer ut fra
models-parameteret i forespoerselen.
"""

import pytest

import sources as S

TIMES = ["2026-08-31T12:00", "2026-08-31T13:00", "2026-08-31T14:00", "2026-08-31T15:00"]


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _hourly(heights, times=None):
    """Minimal hourly-payload: wave_height satt fra heights, resten None.
    time-arrayet folger heights sin lengde med mindre times er gitt."""
    n = len(heights)
    times = times if times is not None else TIMES[:n]
    none_arr = [None] * n
    return {
        "time": times,
        "wave_height": heights,
        "wave_period": none_arr,
        "wave_direction": none_arr,
        "wind_wave_height": none_arr,
        "wind_wave_period": none_arr,
        "wind_wave_direction": none_arr,
        "swell_wave_height": none_arr,
        "swell_wave_period": none_arr,
        "swell_wave_direction": none_arr,
    }


def _fake_get(ewam_hourly, gwam_hourly):
    """Fake _get() som svarer ewam_hourly for models=ewam, gwam_hourly for
    models=gwam - resten av signaturen (url, timeout osv.) ignoreres."""
    def get(url, params=None, **kw):
        model = (params or {}).get("models")
        if model == "ewam":
            return _FakeResponse({"hourly": ewam_hourly})
        if model == "gwam":
            return _FakeResponse({"hourly": gwam_hourly})
        raise AssertionError(f"uventet models-parameter: {model!r}")
    return get


def test_openmeteo_waves_bruker_ewam_der_den_finnes(monkeypatch):
    ewam = _hourly([1.0, 1.1, None, None])
    gwam = _hourly([2.0, 2.1, 2.2, 2.3])
    monkeypatch.setattr(S, "_get", _fake_get(ewam, gwam))

    out = S.openmeteo_waves(59.0, 10.0)
    assert len(out) == 4

    t0 = "2026-08-31T12:00:00+00:00"
    t1 = "2026-08-31T13:00:00+00:00"
    assert out[t0]["hs"] == 1.0
    assert out[t0]["partisjon_kilde"] == "ewam"
    assert out[t1]["hs"] == 1.1
    assert out[t1]["partisjon_kilde"] == "ewam"


def test_openmeteo_waves_faller_tilbake_til_gwam_naar_ewam_mangler(monkeypatch):
    ewam = _hourly([1.0, 1.1, None, None])
    gwam = _hourly([2.0, 2.1, 2.2, 2.3])
    monkeypatch.setattr(S, "_get", _fake_get(ewam, gwam))

    out = S.openmeteo_waves(59.0, 10.0)
    t2 = "2026-08-31T14:00:00+00:00"
    t3 = "2026-08-31T15:00:00+00:00"
    assert out[t2]["hs"] == 2.2
    assert out[t2]["partisjon_kilde"] == "global"
    assert out[t3]["hs"] == 2.3
    assert out[t3]["partisjon_kilde"] == "global"


def test_openmeteo_waves_ingen_kilde_gir_none_hs_og_none_kilde(monkeypatch):
    ewam = _hourly([None, None])
    gwam = _hourly([None, None])
    monkeypatch.setattr(S, "_get", _fake_get(ewam, gwam))

    out = S.openmeteo_waves(59.0, 10.0)
    t0 = "2026-08-31T12:00:00+00:00"
    assert out[t0]["hs"] is None
    assert out[t0]["partisjon_kilde"] is None
    # alle partisjonsfelt skal ogsaa vaere None, ikke bare hs
    assert out[t0]["swell_hs"] is None
    assert out[t0]["windsea_hs"] is None


def test_openmeteo_waves_alle_partisjonsfelt_folger_med_fra_ewam(monkeypatch):
    ewam_hourly = {
        "time": TIMES[:1],
        "wave_height": [1.5], "wave_period": [7.0], "wave_direction": [200.0],
        "wind_wave_height": [0.4], "wind_wave_period": [3.0], "wind_wave_direction": [230.0],
        "swell_wave_height": [1.3], "swell_wave_period": [8.5], "swell_wave_direction": [195.0],
    }
    gwam_hourly = _hourly([9.9], times=TIMES[:1])
    monkeypatch.setattr(S, "_get", _fake_get(ewam_hourly, gwam_hourly))

    out = S.openmeteo_waves(59.0, 10.0)
    rec = out["2026-08-31T12:00:00+00:00"]
    assert rec["partisjon_kilde"] == "ewam"
    assert rec["hs"] == 1.5
    assert rec["tp"] == 7.0
    assert rec["wave_from_direction"] == 200.0
    assert rec["swell_hs"] == 1.3
    assert rec["swell_tp"] == 8.5
    assert rec["swell_dir"] == 195.0
    assert rec["windsea_hs"] == 0.4
    assert rec["windsea_tp"] == 3.0
    assert rec["windsea_dir"] == 230.0


def test_openmeteo_waves_gwam_feiler_bruker_ewam_alene(monkeypatch):
    """Feiler kun ETT av de to modellkallene, skal det andre fortsatt
    brukes - ikke kaste bort ogsaa den dataen som faktisk kom fram (se
    S.safe() i agent.py: den fanger unntak paa HELE openmeteo_waves(),
    saa den robustheten maa ligge her)."""
    ewam = _hourly([1.0, 1.1])

    def flaky_get(url, params=None, **kw):
        if (params or {}).get("models") == "gwam":
            raise ConnectionError("gwam nede")
        return _FakeResponse({"hourly": ewam})

    monkeypatch.setattr(S, "_get", flaky_get)

    out = S.openmeteo_waves(59.0, 10.0)
    assert len(out) == 2
    assert out["2026-08-31T12:00:00+00:00"]["partisjon_kilde"] == "ewam"
    assert out["2026-08-31T12:00:00+00:00"]["hs"] == 1.0


def test_openmeteo_waves_ewam_feiler_bruker_gwam_alene(monkeypatch):
    gwam = _hourly([2.0, 2.1])

    def flaky_get(url, params=None, **kw):
        if (params or {}).get("models") == "ewam":
            raise ConnectionError("ewam nede")
        return _FakeResponse({"hourly": gwam})

    monkeypatch.setattr(S, "_get", flaky_get)

    out = S.openmeteo_waves(59.0, 10.0)
    assert len(out) == 2
    assert out["2026-08-31T12:00:00+00:00"]["partisjon_kilde"] == "global"
    assert out["2026-08-31T12:00:00+00:00"]["hs"] == 2.0


def test_openmeteo_waves_begge_feiler_kaster_videre(monkeypatch):
    """Motsatt av forrige to: feiler BEGGE modellene, skal feilen kastes
    videre - IKKE svelges som et tomt (men "vellykket") resultat, ellers
    mister S.safe() i agent.py signalet om at Open-Meteo faktisk var
    nede den runden."""
    def always_fails(url, params=None, **kw):
        raise ConnectionError("open-meteo nede")

    monkeypatch.setattr(S, "_get", always_fails)

    with pytest.raises(ConnectionError):
        S.openmeteo_waves(59.0, 10.0)


def test_openmeteo_waves_unionen_av_tidspunkter_naar_kildene_ikke_matcher(monkeypatch):
    """EWAM og GWAM trenger ikke daekke akkurat samme tidspunkt-sett -
    resultatet skal vaere unionen, ikke snittet."""
    ewam = _hourly([1.0], times=TIMES[:1])
    gwam = _hourly([2.0], times=TIMES[2:3])
    monkeypatch.setattr(S, "_get", _fake_get(ewam, gwam))

    out = S.openmeteo_waves(59.0, 10.0)
    assert set(out) == {"2026-08-31T12:00:00+00:00", "2026-08-31T14:00:00+00:00"}
    assert out["2026-08-31T12:00:00+00:00"]["partisjon_kilde"] == "ewam"
    assert out["2026-08-31T14:00:00+00:00"]["partisjon_kilde"] == "global"
