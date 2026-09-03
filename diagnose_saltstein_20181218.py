#!/usr/bin/env python3
"""
Engangsdiagnostikk (ordre 2026-09-02): Saltstein 2018-12-18 fikk hs_eff
0.78 m i backtesten (se rapport til bruker) - urimelig lavt for en okt
rapportert som "sesongens beste" (kvalitet 5/5). Foer noen terskel
justeres: er tallet EKTE, eller et artefakt av feil punkt, feil
klokkeslett, eller for grovt grid?

Tre sjekker, alle mot ekte Open-Meteo-data (krever nettverkstilgang -
kjores via .github/workflows/diagnose-saltstein.yml, se den):

  1. PUNKT: Saltsteins spot-koordinat ble rettet 1,24 km (ordre
     2026-09-02, se spots.yaml), men offshore_point [58.930, 9.830] ble
     IKKE flyttet med. Avstand/peiling fra det NYE spot-koordinatet dit
     regnes (ren geometri, geo_utils - ingen nettverk trengs for dette
     ene tallet). Deretter hentes ERA5 (era5_ocean) for 2018-12-18 fra
     TRE punkter: dagens offshore_point, spot-koordinatet selv, og et
     punkt 5 km rett paa facing-retningen (225, "ut i havet") fra
     spot-koordinatet - for aa se om Hs varierer nok mellom dem til aa
     forklare 0.78 m.

  2. TID: sessions_historisk.csv sin 'tid'-kolonne har ALDRI hatt en
     dokumentert tidssone. parse_time_window()/build_hours_window() i
     backtest_sessions.py matcher klokkeslettet DIREKTE mot ERA5 sine
     UTC-stemplede timer (fetch_era5_waves() ber om timezone=UTC) - en
     stilltiende antagelse om at CSV-en allerede er UTC. Er tidene i
     stedet norsk lokaltid (rimelig - et menneske skriver ned egen
     klokke), er desember normaltid (UTC+1), og "16:45" skulle vaert
     UTC 15:45, ikke UTC 16 som koden i dag plukker. Skriver ut hele
     doegnets Hs-serie for 2018-12-18 (UTC-tider) slik at begge
     tolkningene kan sjekkes mot hverandre.

  3. OPPLOSNING: ERA5 sitt grid er ~0.25 grader (~28 km ved 59N). Open-
     Meteo returnerer det faktiske gridpunktet den brukte (latitude/
     longitude paa toppniva i svaret, ikke i den timevise strukturen).
     Skrives ut for alle tre punktene i sjekk 1 - ligger de langt fra
     det vi spurte om, eller er de identiske paa tvers av punkter bare
     4-9 km fra hverandre, forklarer det alt uten aa trenge sjekk 1/2.

IKKE en del av backtest_sessions.py sin faste pipeline - engangsscript,
kjort en gang, resultatet rapporteres og scriptet kan fjernes etterpaa.
"""

import datetime as dt
import math

import geo_utils as G
import backtest_sessions as B

DATE = "2018-12-18"
SPOT_LAT, SPOT_LON = 58.965643, 9.848614          # dagens rettede spot-koordinat
OFFSHORE_LAT, OFFSHORE_LON = 58.930, 9.830        # ikke flyttet med (se spots.yaml)
FACING_DEG = 225                                   # spots.yaml: facing
ALT_DISTANCE_KM = 5.0


def offset_point(lat, lon, bearing_deg, distance_km):
    """Punkt distance_km fra (lat,lon) i retning bearing_deg, regnet i
    UTM32 (meter) - samme metriske plan som resten av geo_utils, presist
    nok paa denne skalaen (noen faa km)."""
    x, y = G.to_utm(lon, lat)
    dx, dy = G.bearing_vector(bearing_deg)
    x2, y2 = x + dx * distance_km * 1000.0, y + dy * distance_km * 1000.0
    lon2, lat2 = G.to_wgs84_xy(x2, y2)
    return lat2, lon2


def sjekk_1_og_3_punkt_og_opplosning():
    print(f"\n{'='*78}\n1+3. PUNKT OG OPPLOSNING\n{'='*78}")

    x1, y1 = G.to_utm(SPOT_LON, SPOT_LAT)
    x2, y2 = G.to_utm(OFFSHORE_LON, OFFSHORE_LAT)
    dist_km = math.hypot(x2 - x1, y2 - y1) / 1000.0
    bearing = G.bearing_between(SPOT_LON, SPOT_LAT, OFFSHORE_LON, OFFSHORE_LAT)
    print(f"  Saltstein spot -> offshore_point: {dist_km:.3f} km, peiling {bearing:.1f} grader")
    print(f"  (facing={FACING_DEG}, swell_window=[170,260] - peilingen er "
          f"{'INNENFOR' if 170 <= bearing <= 260 else 'UTENFOR'} vinduet)")

    alt_lat, alt_lon = offset_point(SPOT_LAT, SPOT_LON, FACING_DEG, ALT_DISTANCE_KM)
    print(f"  Alternativt punkt ({ALT_DISTANCE_KM} km paa facing {FACING_DEG} fra spot): "
          f"{alt_lat:.6f}, {alt_lon:.6f}")

    punkter = [
        ("dagens offshore_point", OFFSHORE_LAT, OFFSHORE_LON),
        ("spot-koordinatet selv", SPOT_LAT, SPOT_LON),
        (f"{ALT_DISTANCE_KM} km SV for spot (facing)", alt_lat, alt_lon),
    ]

    resultater = {}
    for navn, lat, lon in punkter:
        data = B._get_json(B.WAVE_URL, {
            "latitude": lat, "longitude": lon,
            "start_date": DATE, "end_date": DATE,
            "hourly": "wave_height,wave_period,wave_direction",
            "timezone": "UTC",
            "models": "era5_ocean",
        })
        grid_lat, grid_lon = data.get("latitude"), data.get("longitude")
        hs_series = data["hourly"]["wave_height"]
        tp_series = data["hourly"]["wave_period"]
        times = data["hourly"]["time"]
        max_hs = max((h for h in hs_series if h is not None), default=None)
        max_i = hs_series.index(max_hs) if max_hs is not None else None
        print(f"\n  [{navn}] spurt om {lat:.6f},{lon:.6f} -> Open-Meteo brukte gridpunkt {grid_lat},{grid_lon}")
        print(f"    Hs-doegnserie (m): {[round(h, 2) if h is not None else None for h in hs_series]}")
        print(f"    Tp-doegnserie (s): {[round(t, 1) if t is not None else None for t in tp_series]}")
        if max_i is not None:
            print(f"    Maks Hs: {max_hs:.2f} m ved {times[max_i]} UTC (Tp={tp_series[max_i]:.1f} s)")
        resultater[navn] = {"grid": (grid_lat, grid_lon), "hs": hs_series, "tp": tp_series, "times": times}

    grids = {v["grid"] for v in resultater.values()}
    if len(grids) == 1:
        print(f"\n  -> Alle tre punktene traff SAMME ERA5-gridcelle ({grids.pop()}). "
              f"Punktvalget (offshore_point vs. spot-koordinat vs. 5 km SV) kan IKKE "
              f"forklare hs_eff=0.78 m - de ville gitt identisk tall.")
    else:
        print(f"\n  -> Punktene traff FORSKJELLIGE gridceller: {grids}. Punktvalget PAAVIRKER "
              f"hvilken Hs som hentes - se seriene over for hvor mye.")
    return resultater


def sjekk_2_tidspunkt(offshore_resultat):
    print(f"\n{'='*78}\n2. TIDSPUNKT\n{'='*78}")
    times = offshore_resultat["times"]
    hs = offshore_resultat["hs"]
    tp = offshore_resultat["tp"]

    print("  Hele doegnets Hs/Tp ved offshore_point (UTC-tider, som fetch_era5_waves() henter dem):")
    for t, h, p in zip(times, hs, tp):
        print(f"    {t} UTC   Hs={h:.2f} m   Tp={p:.1f} s" if h is not None else f"    {t} UTC   (mangler)")

    utc_valgt = 16  # parse_time_window("16:45") -> hh=16, exact=True - dagens kode
    utc_om_lokal = 15  # 16:45 CET (UTC+1, desember=normaltid) -> UTC 15:45 -> time 15

    def hs_ved(hour):
        for t, h in zip(times, hs):
            if t.endswith(f"{hour:02d}:00"):
                return h
        return None

    hs_dagens = hs_ved(utc_valgt)
    hs_hvis_lokal = hs_ved(utc_om_lokal)
    print(f"\n  Dagens kode (parse_time_window behandler '16:45' som UTC 16): Hs={hs_dagens}")
    print(f"  Hvis '16:45' er NORSK LOKALTID (CET=UTC+1 i desember) -> skulle vaert UTC 15: Hs={hs_hvis_lokal}")
    if hs_dagens is not None and hs_hvis_lokal is not None and hs_dagens != hs_hvis_lokal:
        diff = hs_hvis_lokal - hs_dagens
        print(f"  -> Forskjell: {diff:+.2f} m. {'Tidssone-antagelsen KAN forklare avviket.' if abs(diff) > 0.1 else 'Forskjellen er liten - tidssone forklarer neppe mye alene.'}")
    else:
        print("  -> Ingen av timene mangler data, eller de er like - se serien over for hele bildet.")


def main():
    resultater = sjekk_1_og_3_punkt_og_opplosning()
    sjekk_2_tidspunkt(resultater["dagens offshore_point"])
    print(f"\n{'='*78}\nFERDIG\n{'='*78}")
    print(f"  HTTP: {B._http_stats['n_calls']} kall totalt, {B._http_stats['n_retried']} matte proeve paa nytt.")


if __name__ == "__main__":
    main()
