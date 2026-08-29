"""
Punktvis beskrivelse av hvorfor et vindu ser bra ut.

Bevisst DETERMINISTISK, ikke LLM-generert. Grunnen: vi har allerede hvert
eneste tall teksten skal inneholde - vindstyrke, varighet, fetch,
energiandel gjennom munningen, forsinkelse, delscorer. En sprakmodell
ville bare formulert de samme tallene om igjen, og lagt til en risiko
for aa finne paa noe som ikke stemmer med regnestykket.

Teksten er ogsaa en revisjon av modellen. Naar du star pa stranda og det
er flatt, vil du vite NOYAKTIG hva agenten trodde og hvorfor - ikke lese
en velformulert omskrivning av det.

Vil du likevel ha en LLM-polering paa toppen: send `bullets` gjennom en
modell med instruks om aa omformulere UTEN aa endre tall. Aldri la den
generere tallene selv.
"""

import math

KOMPASS = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ",
           "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"]


def kompass(deg):
    if deg is None:
        return "?"
    return KOMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def _grader(deg):
    return f"{kompass(deg)} ({deg:.0f}\u00b0)" if deg is not None else "ukjent retning"


def _tid(iso):
    return iso[11:16] if iso else "?"


def describe(spot, window, hours):
    """
    Returnerer en liste med punkter. Hvert punkt er en dict:
        {"ikon": "wind"|"swell"|"local"|"spot"|"result"|"caveat",
         "tekst": "..."}

    `hours` er timene som inngaar i vinduet, `window` er sammendraget.
    """
    peak = max(hours, key=lambda h: (h.get("stars") or 0) * h["p_surf"])
    first = hours[0]
    out = []

    # ---------------------------------------------------------------- vind
    ws, wd = peak.get("wind_speed"), peak.get("wind_from")
    if ws is not None and wd is not None:
        varighet = peak.get("local_duration_h")
        snitt = peak.get("local_wind_mean")
        if varighet and snitt:
            out.append({"ikon": "wind", "tekst":
                f"Vind {_grader(wd)} {ws:.0f} m/s i vinduet. Sjøen er bygget av "
                f"{snitt:.0f} m/s i {varighet} timer før dette."})
        else:
            out.append({"ikon": "wind", "tekst":
                f"Vind {_grader(wd)} {ws:.0f} m/s i vinduet."})

    # ------------------------------------------------------- swell/munning
    gate_hs = peak.get("gate_hs") or 0
    prop_hs = peak.get("prop_hs") or 0
    if gate_hs > 0 and spot.get("klasse") == "C":
        frac = (peak.get("gate_energy_frac") or 0) * 100
        delay = peak.get("gate_delay_h") or 0
        out.append({"ikon": "swell", "tekst":
            f"Ved fjordmunningen: {gate_hs:.1f} m @ {peak.get('gate_tp', 0):.1f} s "
            f"fra {_grader(peak.get('gate_dir'))}. Bare {frac:.0f} % av energien "
            f"ligger innenfor fjordaksen og slipper opp - det ankommer "
            f"{spot['name']} {delay:.1f} t senere som {prop_hs:.1f} m."})
    elif spot.get("klasse") in ("A", "B"):
        mh, mt, md = peak.get("model_hs"), peak.get("model_tp"), peak.get("model_dir")
        if mh:
            out.append({"ikon": "swell", "tekst":
                f"Modellen gir {mh:.1f} m @ {mt or 0:.1f} s fra {_grader(md)} "
                f"rett utenfor spotten. Svellvinduet er "
                f"{spot['params']['swell_window'][0]}\u2013"
                f"{spot['params']['swell_window'][1]}\u00b0."})

    # ------------------------------------------------------ lokal vindsjoe
    loc_hs = peak.get("local_hs") or 0
    if loc_hs > 0.2:
        fetch = peak.get("local_fetch_km") or 0
        grense = {"fetch": "fetchbegrenset", "duration": "varighetsbegrenset",
                  "fully_developed": "fullt utviklet"}.get(
                      peak.get("local_limited_by"), "")
        dominans = "Dette dominerer" if loc_hs > prop_hs else "Dette kommer i tillegg"
        out.append({"ikon": "local", "tekst":
            f"Lokalt bygger vinden {loc_hs:.1f} m @ {peak.get('local_tp', 0):.1f} s "
            f"over {fetch:.0f} km fetch"
            + (f" ({grense})" if grense else "") + f". {dominans}."})

    # ------------------------------------------------------- spotegenskap
    facing = spot["params"]["facing"]
    vind_kar = peak.get("wind_label", "")
    spot_tekst = (f"{spot['name']} vender {kompass(facing)}. "
                  f"Vinden i vinduet er {vind_kar}.")
    if spot.get("klasse") == "C":
        gate = spot["params"].get("gate") or {}
        spot_tekst += (f" Fjorden slipper bare gjennom \u00b1"
                       f"{gate.get('sector_half_width', 20)}\u00b0 rundt aksen, "
                       f"og det er derfor spotten trenger mye mer enn åpen kyst.")
    elif spot.get("klasse") == "A":
        spot_tekst += (" Norskerenna gir dypt vann helt inn, så bølgen jekker "
                       "brått uten å tape energi på veien.")
    out.append({"ikon": "spot", "tekst": spot_tekst})

    # ------------------------------------------------------------ resultat
    ledd = {"størrelse": peak.get("q_size", 0), "periode": peak.get("q_period", 0),
            "vind": peak.get("q_wind", 0), "vannstand": peak.get("q_water", 0)}
    svakest = min(ledd.items(), key=lambda x: x[1])
    out.append({"ikon": "result", "tekst":
        f"Sum: {peak.get('hs_eff', 0):.1f} m @ {peak.get('tp_eff', 0):.1f} s gir "
        f"{peak.get('stars')}/10 med {peak['p_surf']:.0f} % sannsynlighet "
        f"(spenn {peak.get('stars_p10')}\u2013{peak.get('stars_p90')} stjerner). "
        f"Svakeste ledd er {svakest[0]} ({svakest[1]:.2f})."})

    # ----------------------------------------------------------- forbehold
    forbehold = []
    if not spot.get("kalibrert"):
        forbehold.append("Spotten er ikke kalibrert mot faktiske økter - "
                         "terskelen er et estimat, ikke en målt verdi")
    if (peak.get("model_spread") or 0) > 0.20:
        forbehold.append(f"modellene spriker {100 * peak['model_spread']:.0f} % "
                         f"på bølgehøyde - sjekk igjen nærmere tida")
    if peak.get("lead_h", 0) > 72:
        forbehold.append(f"varselet går {peak['lead_h'] / 24:.0f} døgn fram, "
                         f"så vinduet kan flytte seg flere timer")
    if peak.get("confidence") == "lav":
        forbehold.append("samlet konfidens er lav")
    if forbehold:
        out.append({"ikon": "caveat",
                    "tekst": forbehold[0][0].upper() + forbehold[0][1:] +
                             ("; " + "; ".join(forbehold[1:]) if len(forbehold) > 1 else "") + "."})

    if spot.get("access_warning"):
        out.append({"ikon": "caveat",
                    "tekst": "Ferdsel: " + " ".join(spot["access_warning"].split())})

    return out
