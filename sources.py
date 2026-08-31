"""
Datakilder. Alle returnerer timeserier paa formen:
    {iso8601_utc: {parameter: verdi}}

Designprinsipp: hver kilde er valgfri og feiler mykt. Agenten kjorer videre
med det den faar, og logger hvilke kilder som svarte. Det gjor at du kan
sammenligne MET mot DMI mot EWAM pa samme spot og se hvor de er uenige -
uenighet er i seg selv nyttig informasjon.
"""

import os
import time
import datetime as dt
import xml.etree.ElementTree as ET

import requests

# ---------------------------------------------------------------------------
# MET Norway krever en unik, identifiserbar User-Agent med kontaktinfo.
# Uten den far du 403. Sett SURF_AGENT_UA i miljoet.
# ---------------------------------------------------------------------------
USER_AGENT = os.environ.get(
    "SURF_AGENT_UA",
    "skagerrak-surf-agent/0.1 (bytt-meg@example.com)",
)
TIMEOUT = 10


def _iso(t):
    """Normaliser tidsstempel til hel time i UTC."""
    if isinstance(t, str):
        t = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t.astimezone(dt.timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).isoformat()


def _get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", {})
    kw["headers"].setdefault("User-Agent", USER_AGENT)
    r = requests.get(url, **kw)
    r.raise_for_status()
    return r


# ============================================================ MET Locationforecast


def met_wind(lat, lon):
    """Vind og trykk fra MEPS/ECMWF via Locationforecast 2.0. Gratis, ingen nokkel."""
    url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    data = _get(url, params={"lat": round(lat, 4), "lon": round(lon, 4)}).json()

    out = {}
    for entry in data["properties"]["timeseries"]:
        d = entry["data"]["instant"]["details"]
        out[_iso(entry["time"])] = {
            "wind_speed": d.get("wind_speed"),
            "wind_from_direction": d.get("wind_from_direction"),
            "wind_gust": d.get("wind_speed_of_gust"),
            "pressure": d.get("air_pressure_at_sea_level"),
            "air_temp": d.get("air_temperature"),
        }
    return out


# ============================================================ MET Oceanforecast


def met_waves(lat, lon):
    """
    Bolger fra WAVEWATCHIII 4 km (Nordsjoen/Norskehavet), forsert med MEPS 2.5 km.

    Merk: 2.0 bruker METEOROLOGISK konvensjon for bolgeretning
    (sea_surface_wave_from_direction). 0.9 brukte oseanografisk - gammel
    eksempelkode kan vaere 180 grader feil.

    Merk ogsa at API-et ikke alltid leverer periode. Da faller vi tilbake pa
    Open-Meteo for Tp. Kryss av i output hvilken kilde perioden kom fra.
    """
    url = "https://api.met.no/weatherapi/oceanforecast/2.0/complete"
    data = _get(url, params={"lat": round(lat, 4), "lon": round(lon, 4)}).json()

    out = {}
    for entry in data["properties"]["timeseries"]:
        d = entry["data"]["instant"]["details"]
        rec = {
            "hs": d.get("sea_surface_wave_height"),
            "wave_from_direction": d.get("sea_surface_wave_from_direction"),
            "sea_temp": d.get("sea_water_temperature"),
            "current_speed": d.get("sea_water_speed"),
            "current_to_direction": d.get("sea_water_to_direction"),
        }
        for key in ("sea_surface_wave_period_at_variance_spectral_density_maximum",
                    "sea_surface_wave_significant_period"):
            if d.get(key) is not None:
                rec["tp"] = d[key]
                break
        out[_iso(entry["time"])] = rec
    return out


# ============================================================ Open-Meteo Marine

# EWAM (DWD): Europa (30-66 N, 10.5 V-42 O), ~5 km rutenett (0.05 grader),
# rekker ca 3 dogn fram (~72 t, kortere i praksis avhengig av hvor i
# modellsyklusen forespoerselen treffer - se agent.py sin MAX_HOURS).
# GWAM (DWD): globalt, ~25-28 km rutenett (0.25 grader) - 4-5x grovere enn
# EWAM og opploser ikke Skagerrak-kysten/fjordmunningene i det hele tatt,
# men rekker til gjengjeld ca 7.5 dogn fram. Kilde: Open-Meteo sin egen
# dokumentasjon (open-meteo.com/en/docs/marine-weather-api).
#
# openmeteo_waves() bruker EWAM der den har data, GWAM der den ikke har
# det - se der for hvordan og for partisjon_kilde-feltet. ensemble.py sin
# GLOBAL_MODEL_HS_REL_PENALTY bruker partisjon_kilde til aa oke
# usikkerheten naar en time faktisk kommer fra det grove globale rutenettet.
_OPENMETEO_FIELDS = (
    "hs", "tp", "wave_from_direction",
    "windsea_hs", "windsea_tp", "windsea_dir",
    "swell_hs", "swell_tp", "swell_dir",
)


def _openmeteo_marine_single(lat, lon, model):
    """
    Ett Open-Meteo Marine-kall for ÉN modell ("ewam" eller "gwam").
    Returnerer {tidspunkt: {hs, tp, wave_from_direction, windsea_*,
    swell_*}} - se openmeteo_waves() for hvordan EWAM og GWAM kombineres
    til én tidsserie derfra.
    """
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": ",".join([
            "wave_height", "wave_direction", "wave_period",
            "wind_wave_height", "wind_wave_direction", "wind_wave_period",
            "swell_wave_height", "swell_wave_direction", "swell_wave_period",
        ]),
        "timezone": "UTC",
        "forecast_days": 7,
        "models": model,
    }
    h = _get(url, params=params).json()["hourly"]

    out = {}
    for i, t in enumerate(h["time"]):
        ts = _iso(t + "+00:00" if "+" not in t and "Z" not in t else t)

        def v(k):
            arr = h.get(k) or []
            return arr[i] if i < len(arr) else None

        out[ts] = {
            "hs": v("wave_height"),
            "tp": v("wave_period"),
            "wave_from_direction": v("wave_direction"),
            "windsea_hs": v("wind_wave_height"),
            "windsea_tp": v("wind_wave_period"),
            "windsea_dir": v("wind_wave_direction"),
            "swell_hs": v("swell_wave_height"),
            "swell_tp": v("swell_wave_period"),
            "swell_dir": v("swell_wave_direction"),
        }
    return out


def openmeteo_waves(lat, lon):
    """
    Gratis, ingen nokkel. Bruk EWAM (DWD) der den har data - 0.05 grader
    oppløsning over Europa, altsa ca 5 km, sammenlignbart med MET sin
    WW3 - og fall tilbake til GWAM (globalt, ~25-28 km) for timene lenger
    fram enn EWAM rekker. Gir partisjonert swell og vindsjo separat, noe
    MET ikke gjor. Bruk den til Tp og til kryssjekk.

    `partisjon_kilde` per time forteller HVILKEN av de to modellene som
    faktisk ble brukt for den timen: "ewam", "global", eller None (ingen
    av dem hadde data). Se modulhodet over for opplosning/rekkevidde per
    modell, og ensemble.py sin GLOBAL_MODEL_HS_REL_PENALTY for hvorfor
    dette feltet finnes: en time paa det grove globale rutenettet er
    mindre til aa stole paa enn en EWAM-time med samme varslingslengde,
    ikke bare fordi den ligger langt fram.

    De to modellkallene feiler UAVHENGIG av hverandre - i traad med
    modulens eget prinsipp om at hver kilde er valgfri og feiler mykt
    (se modulens docstring). Feiler kun ett av dem, brukes det andre
    alene i stedet for aa kaste bort begge. Feiler BEGGE, kastes
    unntaket videre (i stedet for aa late som Open-Meteo svarte tomt),
    saa S.safe() fortsatt fanger og logger det som en reell feilkilde.
    """
    try:
        ewam = _openmeteo_marine_single(lat, lon, "ewam")
    except Exception as exc:  # noqa: BLE001
        ewam, ewam_exc = {}, exc
    else:
        ewam_exc = None

    try:
        gwam = _openmeteo_marine_single(lat, lon, "gwam")
    except Exception as exc:  # noqa: BLE001
        gwam, gwam_exc = {}, exc
    else:
        gwam_exc = None

    if ewam_exc is not None and gwam_exc is not None:
        raise ewam_exc

    out = {}
    for ts in sorted(set(ewam) | set(gwam)):
        e = ewam.get(ts)
        g = gwam.get(ts)
        if e and e.get("hs") is not None:
            rec, kilde = e, "ewam"
        elif g and g.get("hs") is not None:
            rec, kilde = g, "global"
        else:
            rec, kilde = {k: None for k in _OPENMETEO_FIELDS}, None
        out[ts] = dict(rec, partisjon_kilde=kilde)
    return out


# ============================================================ DMI Open Data


DMI_EDR = "https://dmigw.govcloud.dk/v1/forecastedr/collections"


def dmi_list_collections(api_key=None):
    """Hjelper: se hvilke WAM-collections som finnes. Kjor en gang."""
    key = api_key or os.environ.get("DMI_API_KEY")
    r = _get(DMI_EDR, params={"api-key": key})
    return [c["id"] for c in r.json().get("collections", [])]


def dmi_waves(lat, lon, collection="wam_nsb", api_key=None):
    """
    DMI sin WAM-modell via EDR-API-et. Gratis nokkel fra dmi.dk/friedata.

    'wam_nsb' er Nordsjoen/Ostersjoen-domenet, som dekker hele Skagerrak.
    Kjor dmi_list_collections() forst hvis navnet har endret seg.

    NB: DMI har rate limit rundt 1 request/sekund.
    """
    key = api_key or os.environ.get("DMI_API_KEY")
    if not key:
        raise RuntimeError("DMI_API_KEY mangler")

    url = f"{DMI_EDR}/{collection}/position"
    params = {
        "coords": f"POINT({lon:.4f} {lat:.4f})",
        "crs": "crs84",
        "f": "GeoJSON",
        "api-key": key,
    }
    data = _get(url, params=params).json()
    time.sleep(1.1)  # respekter rate limit

    # EDR CoverageJSON/GeoJSON: rekkefolgen paa parameternavn varierer
    # mellom modellkjoringer, saa vi mapper defensivt.
    alias = {
        "significant-wave-height": "hs",
        "significant-wave-period": "tp",
        "peak-wave-period": "tp",
        "mean-wave-dir": "wave_from_direction",
        "significant-wave-dir": "wave_from_direction",
    }

    out = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        ts = props.get("step") or props.get("datetime")
        if not ts:
            continue
        rec = out.setdefault(_iso(ts), {})
        for k, v in props.items():
            if k in alias and v is not None:
                rec[alias[k]] = v
    return out


# ============================================================ Kartverket


def kartverket_water_level(lat, lon, hours=72):
    """
    Vannstandsvarsel (inkludert stormflo) fra Kartverket, i cm relativt
    middelvann. Returnerer {} hvis tjenesten ikke svarer - agenten kjorer
    videre uten vannstandsjustering.

    Merk: api.sehavniva.no ble avviklet av Kartverket - tjenesten flyttet
    til vannstand.kartverket.no (samme sti/parametre/XML-format).
    """
    now = dt.datetime.now(dt.timezone.utc)
    url = "https://vannstand.kartverket.no/tideapi.php"
    params = {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "fromtime": now.strftime("%Y-%m-%dT%H:%M"),
        "totime": (now + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M"),
        "datatype": "all",
        "refcode": "msl",       # middelvann, ikke sjokartnull
        "place": "",
        "file": "",
        "lang": "nb",
        "interval": 60,
        "dst": 0,
        "tzone": 0,
        "tide_request": "locationdata",
    }
    root = ET.fromstring(_get(url, params=params).text)

    out = {}
    for series in root.iter("data"):
        kind = series.attrib.get("type", "")
        for wl in series.iter("waterlevel"):
            ts = _iso(wl.attrib["time"])
            try:
                val = float(wl.attrib["value"])
            except (KeyError, ValueError):
                continue
            rec = out.setdefault(ts, {})
            # "forecast" inkluderer vaerbidraget (stormflo) - den vil vi ha
            if kind.lower().startswith("fore") or "level_cm" not in rec:
                rec["level_cm"] = val
            rec[f"level_{kind or 'unknown'}_cm"] = val
    return out


# ============================================================ sammenstilling


def merge_series(*series):
    """Slaa sammen flere timeserier paa felles tidsstempler."""
    out = {}
    for s in series:
        for ts, rec in (s or {}).items():
            out.setdefault(ts, {}).update(rec)
    return out


def safe(fn, *args, label=None, **kwargs):
    """Kjor en kilde, returner (data, feilmelding)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # noqa: BLE001
        return {}, f"{label or fn.__name__}: {type(exc).__name__}: {exc}"
