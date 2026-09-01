#!/usr/bin/env python3
"""
Kalibrering. Sammenligner agentens skyggelogg mot faktiske okter og
foreslaar justeringer av parameterne i spots.yaml.

  python calibrate.py                   rapport for alle spots
  python calibrate.py --spot slagen     bare ett

Du trenger to filer:

  out/shadow.csv    skrives automatisk av agent.py hver kjoring
  sessions.csv      fyller du ut selv, ett innslag per okt ELLER per
                    bomtur. Bomturene er de mest verdifulle radene.

sessions.csv-format (se sessions.example.csv):
  time,spot,rating,hs_observed_m,tp_observed_s,ekstern_wp,notes
  2026-11-14T09:00Z,slagen,4,1.8,6,,"rein NV, dode etter 3 t"
  2026-11-14T09:00Z,sletteroyene,0,0.3,,,"flatt - agenten sa 62"

rating: 0 = flatt/usurfbart, 1 = sowbart, 2 = ok, 3 = bra, 4 = veldig bra, 5 = beste i sesongen

ekstern_wp: valgfritt. Boelgeeffekt-/energitall slik surf-forecast (eller
  tilsvarende ekstern tjeneste) viser det for samme tid/spot, HVIS du har
  det for haanden naar du fyller ut okten. IKKE det samme som var egen
  wave_power() (physics.py) - enheten/skalaen er ukjent og faktoren
  mellom dem er IKKE konstant (varierer med spot/forhold), saa det finnes
  ingen fast omregning. Loggingen finnes for aa etablere sammenhengen
  EMPIRISK over tid - se report() sin "ekstern vs. egen wp"-blokk.

Rapporten er gruppert paa (spot, model_rev) - ordre 2026-09-02, se
shadow_schema.py sitt model_rev-felt. En scoring-endring (som
regional_wp-porten i PR #16) faar da sin egen seksjon, i stedet for aa
drukne i statistikk fra rader skrevet med den gamle scoringen. Rader fra
FOR feltet fantes havner samlet i "pre-instrumentering", ikke gjettet
bakover til en bestemt revisjon.
"""

import argparse
import csv
import datetime as dt
import pathlib
import statistics as st

import physics as P

ROOT = pathlib.Path(__file__).parent
SHADOW = ROOT / "out" / "shadow.csv"
SESSIONS = ROOT / "sessions.csv"


def load_shadow():
    rows = []
    if not SHADOW.exists():
        return rows
    with SHADOW.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def load_sessions():
    if not SESSIONS.exists():
        return []
    with SESSIONS.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("spot")]


def norm_time(s):
    return s.replace("Z", "+00:00")[:13]  # til hel time


def match(sessions, shadow):
    """
    Koble hver okt til den ferskeste prognosen for det tidspunktet.
    Vi bruker den SISTE kjoringen for okten - det er det du faktisk
    hadde tilgjengelig da du bestemte deg.
    """
    index = {}
    for r in shadow:
        key = (r["spot"], norm_time(r["time"]))
        prev = index.get(key)
        if prev is None or r["run_at"] > prev["run_at"]:
            index[key] = r

    out = []
    for s in sessions:
        r = index.get((s["spot"], norm_time(s["time"])))
        if r:
            out.append((s, r))
    return out


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def report(pairs, spot_filter=None):
    # gruppert paa (spot, model_rev) - ordre 2026-09-02. Rader skrevet FOR
    # model_rev fantes (shadow_schema.py) har ikke feltet i det hele tatt,
    # og row.get() gir da None for dem - samlet i EN epoke,
    # "pre-instrumentering", i stedet for aa gjette en versjon bakover.
    # Uten dette skillet drukner effekten av en scoring-endring (som
    # regional_wp-porten i PR #16) i rader skrevet med den GAMLE scoringen
    # naar de blandes sammen i samme statistikk.
    by_spot_epoch = {}
    for s, r in pairs:
        if spot_filter and s["spot"] not in spot_filter:
            continue
        epoch = r.get("model_rev") or "pre-instrumentering"
        by_spot_epoch.setdefault((s["spot"], epoch), []).append((s, r))

    if not by_spot_epoch:
        print("Ingen treff mellom sessions.csv og out/shadow.csv enna.")
        print("Kjor agenten daglig og logg okter (og bomturer) i sessions.csv.")
        return

    for (spot, epoch), items in sorted(by_spot_epoch.items()):
        print(f"\n{'='*72}\n{spot}  [{epoch}]   ({len(items)} okter)\n{'='*72}")

        rated = [(int(s["rating"]), fnum(r["score"]), s, r) for s, r in items
                 if s.get("rating") not in (None, "")]
        if len(rated) < 3:
            print("  For fa okter til statistikk. Trenger minst 3, helst 15+.")
            continue

        # traff agenten?
        skunk = [x for x in rated if x[0] <= 1 and x[1] >= 55]     # varslet, var flatt
        missed = [x for x in rated if x[0] >= 3 and x[1] < 55]     # var bra, ikke varslet
        hits = [x for x in rated if x[0] >= 3 and x[1] >= 55]

        print(f"  Treff:     {len(hits):>3}   (bra okt, agenten varslet)")
        print(f"  Bomturer:  {len(skunk):>3}   (agenten varslet, det var flatt)  <- juster OPP min_hs")
        print(f"  Missa:     {len(missed):>3}   (bra okt, agenten var stille)    <- juster NED min_hs")

        # storrelsesbias
        obs = [(fnum(s.get("hs_observed_m")), fnum(r["hs_eff"]))
               for _, _, s, r in rated
               if fnum(s.get("hs_observed_m")) and fnum(r["hs_eff"])]
        if len(obs) >= 3:
            ratios = [m / o for o, m in obs]
            bias = st.median(ratios)
            print(f"\n  Hs-bias:   modell / observert = {bias:.2f}  (n={len(obs)})")
            if bias > 1.15:
                print(f"             -> agenten OVERVURDERER. Senk gate.transmission "
                      f"til ca {1/bias:.2f} av naavaerende verdi.")
            elif bias < 0.87:
                print(f"             -> agenten UNDERVURDERER. Ok gate.transmission "
                      f"eller gate.sector_half_width.")
            else:
                print("             -> innenfor stoyen. La transmission staa.")

        # ekstern_wp (surf-forecast e.l.) vs. vaar egen wave_power(hs_eff,
        # tp_eff) paa samme tidspunkt. IKKE en omregningsfaktor som skrives
        # tilbake noe sted - bare et rapportert forhold, siden faktoren
        # ikke er konstant (den varierer med spot/forhold - se
        # modulens docstring). Hensikten er aa la deg SE sammenhengen
        # etter hvert som flere okter samler seg opp, ikke aa late som om
        # den er kjent fra n=faa observasjoner.
        wp_pairs = [(fnum(s.get("ekstern_wp")), P.wave_power(fnum(r["hs_eff"]), fnum(r["tp_eff"])))
                    for _, _, s, r in rated
                    if fnum(s.get("ekstern_wp")) is not None
                    and fnum(r.get("hs_eff")) is not None and fnum(r.get("tp_eff")) is not None]
        if wp_pairs:
            ratios = [ekstern / egen for ekstern, egen in wp_pairs if egen > 0]
            print(f"\n  Ekstern wp vs. egen wp (wave_power(hs_eff, tp_eff)):  n={len(wp_pairs)}")
            for ekstern, egen in wp_pairs:
                if egen > 0:
                    print(f"    ekstern={ekstern:>7.1f}   egen={egen:>7.1f} kW/m   "
                          f"forhold={ekstern/egen:.2f}")
                else:
                    print(f"    ekstern={ekstern:>7.1f}   egen=0 (udefinert forhold)")
            if len(ratios) >= 3:
                print(f"    median forhold (ekstern/egen) = {st.median(ratios):.2f}  "
                      f"spredning {min(ratios):.2f}-{max(ratios):.2f}")
                print("    -> IKKE en fast omregningsfaktor - bruk kun som "
                      "sanity-sjekk, se docstring.")
            else:
                print("    for fa parvise observasjoner til aa si noe om forholdet enna "
                      "(trenger minst 3).")

        # foreslatt min_hs: hoyeste hs_eff som ga rating <= 1
        flat_hs = [fnum(r["hs_eff"]) for x, _, s, r in rated
                   if x <= 1 and fnum(r["hs_eff"]) is not None]
        good_hs = [fnum(r["hs_eff"]) for x, _, s, r in rated
                   if x >= 3 and fnum(r["hs_eff"]) is not None]
        if flat_hs and good_hs:
            suggested = (max(flat_hs) + min(good_hs)) / 2
            print(f"\n  Hoyeste hs_eff som var flatt:  {max(flat_hs):.2f} m")
            print(f"  Laveste hs_eff som var bra:    {min(good_hs):.2f} m")
            print(f"  -> foreslatt min_hs:           {suggested:.2f} m")

        # hvilket ledd bommer oftest
        for key, label in [("q_size", "storrelse"), ("q_wind", "vind"),
                           ("q_period", "periode")]:
            vals_good = [fnum(r[key]) for x, _, s, r in rated if x >= 3 and fnum(r[key])]
            vals_bad = [fnum(r[key]) for x, _, s, r in rated if x <= 1 and fnum(r[key])]
            if len(vals_good) >= 2 and len(vals_bad) >= 2:
                sep = st.mean(vals_good) - st.mean(vals_bad)
                verdict = "skiller godt" if sep > 0.2 else (
                    "skiller darlig - vurder aa senke vekten" if sep < 0.05 else "middels")
                print(f"  {label:<10} bra={st.mean(vals_good):.2f} "
                      f"flatt={st.mean(vals_bad):.2f}  {verdict}")

        # EWAM vs global boelgemodell - underlaget for aa etterproeve
        # ensemble.GLOBAL_MODEL_HS_REL_PENALTY mot faktiske utfall. Bruker
        # samme treff/bomtur/missa-definisjon som over, men delt paa
        # partisjon_kilde. "score" her er den DETERMINISTISKE
        # kontroll-scoren (upaavirket av ensemble-spredningen selv), saa en
        # forskjell i bomtur-rate mellom kildene er et reelt signal om at
        # selve Hs-ESTIMATET er mindre til aa stole paa fra den ene kilden
        # - ikke sirkulaert med straffen den skal begrunne.
        by_kilde = {"ewam": [], "global": [], "ukjent": []}
        for x in rated:
            kilde = (x[3].get("partisjon_kilde") or "").strip()
            by_kilde[kilde if kilde in ("ewam", "global") else "ukjent"].append(x)

        if by_kilde["ewam"] or by_kilde["global"]:
            print("\n  EWAM vs global modell (partisjon_kilde):")
            rates = {}
            for kilde in ("ewam", "global"):
                group = by_kilde[kilde]
                n = len(group)
                if n < 3:
                    print(f"    {kilde:<7} n={n:<3}  for fa til statistikk")
                    continue
                g_hits = sum(1 for x in group if x[0] >= 3 and x[1] >= 55)
                g_skunk = sum(1 for x in group if x[0] <= 1 and x[1] >= 55)
                g_missed = sum(1 for x in group if x[0] >= 3 and x[1] < 55)
                called_good = g_hits + g_skunk
                skunk_rate = g_skunk / called_good if called_good else None
                rates[kilde] = skunk_rate
                rate_txt = f"{100*skunk_rate:.0f} %" if skunk_rate is not None else "-"
                print(f"    {kilde:<7} n={n:<3}  treff={g_hits:>2}  bomtur={g_skunk:>2}  "
                      f"missa={g_missed:>2}  bomtur-rate av 'agenten sa god'={rate_txt}")
            if by_kilde["ukjent"]:
                print(f"    ukjent  n={len(by_kilde['ukjent']):<3}  "
                      f"(ingen partisjon_kilde i loggen - eldre rader eller ingen Open-Meteo-data)")

            if "ewam" in rates and "global" in rates and rates["ewam"] is not None and rates["global"] is not None:
                diff = rates["global"] - rates["ewam"]
                if diff > 0.15:
                    print(f"    -> global bommer {100*diff:.0f} prosentpoeng oftere enn EWAM "
                          f"naar den sier 'god' - systematisk verre. OK "
                          f"ensemble.GLOBAL_MODEL_HS_REL_PENALTY.")
                else:
                    print("    -> global bommer ikke tydelig mer enn EWAM her - like god. "
                          "Vurder aa senke eller fjerne ensemble.GLOBAL_MODEL_HS_REL_PENALTY.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot", nargs="*")
    args = ap.parse_args()

    shadow, sessions = load_shadow(), load_sessions()
    print(f"shadow.csv:  {len(shadow)} rader")
    print(f"sessions.csv: {len(sessions)} okter")
    report(match(sessions, shadow), args.spot)


if __name__ == "__main__":
    main()
