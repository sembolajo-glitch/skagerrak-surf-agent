"""
Valider at klasse A/B sine offshore_point faktisk ligger i aapent,
dypt vann - ikke bare en tilfeldig koordinat noen hundre meter unna.

ordre 2026-09-06 (se rapport til bruker): Molen odden sitt forrige
offshore_point (0,71 km ute) laa dypt inne i shoalingsonen - 30 m-koten
var allerede naadd paa 412 m. En 10 s donning har bunnkontakt fra 78 m
dyp, saa verdien MET/Open-Meteo leser paa et saa naert punkt er allerede
delvis refraktert av grunt vann FOR modellen selv refrakterer den en
gang til (physics.window_factor()) - punktet mister sin egen forutsetning
(aa representere UBERORT offshore-swell).

To krav, begge maa holde for et offshore_point aa telle som reelt
offshore:
  1. Mer enn 1 km fra spotkoordinatet (ren avstand - fanger et punkt
     som bare er "litt" flyttet fra stranda, uavhengig av dybde).
  2. Lenger ute enn dybde_50m_km (naar den er kjent/maalt) - selve
     grunnen til at et punkt skal vaere "offshore" i det hele tatt: swell
     skal representere DYPT vann, og 50 m er terskelen build_fetch.py sin
     egen dybdeprofil allerede bruker.
"""

import agent as A

# Kjente, allerede dokumenterte unntak - IKKE en generell tillatelse (se
# monotonitet-allowlisten i test_spots_yaml.py for samme monster).
#
# jomfruland_ost: offshore_point er 0,99 km fra spotkoordinatet (under
# 1 km) og innenfor egen dybde_50m_km (1,4 km) - punktet deler i praksis
# MET-gridcelle med spotet selv (samme ~4 km-oppløsning som gapet mellom
# Saltstein/Molen sine gridceller i forrige PR sin dekomponering), saa
# aa flytte det lenger ut ville trolig IKKE endre hvilken raa modell-
# verdi som faktisk leses - annerledes enn Molen, der flyttingen beviselig
# hoppet til et sted forbi shoalingsonen. Ikke fikset her - egen sak.
#
# verdens_ende, hvasser_sando: dukket opp 2026-09-07 (ikke i forrige
# PR - se rapport til bruker) da PR #32 (geodata-resultat) ga dem ekte
# dybde_50m_km for forste gang. verdens_ende sitt offshore_point (3,13 km)
# er godt INNENFOR egen 50 m-kote (4,47 km) - samme shoaling-problem som
# Molen hadde. hvasser_sando (1,68 km mot 1,71 km) er marginalt innenfor -
# 30 m kort. Ingen av dem er roert i denne runden (utenfor scope for
# oppgaven som ble gitt), men brudd er brudd - allowlistet aapent, ikke
# stille ignorert.
KJENTE_AVVIK = {"jomfruland_ost", "verdens_ende", "hvasser_sando"}


def test_allowlist_refererer_kun_til_eksisterende_spots():
    spots, _ = A.load_spots()
    kjente_ider = {s["id"] for s in spots}
    ukjente = KJENTE_AVVIK - kjente_ider
    assert not ukjente, f"allowlist peker paa spot(ter) som ikke finnes: {ukjente}"


def test_offshore_point_er_reelt_offshore_for_klasse_ab():
    """Mer enn 1 km fra spotkoordinatet OG (naar dybde_50m_km er kjent)
    lenger ute enn 50-meterskoten - for ethvert klasse A/B-spot med
    offshore_point satt, utenom kjente, dokumenterte unntak."""
    spots, _ = A.load_spots()
    feil = []
    for spot in spots:
        if spot["klasse"] not in ("A", "B"):
            continue
        if spot["id"] in KJENTE_AVVIK:
            continue
        op = spot.get("offshore_point")
        if not op:
            continue
        dist_km = A.P.haversine_km(spot["lat"], spot["lon"], op[0], op[1])
        if dist_km <= 1.0:
            feil.append((spot["id"], "offshore_point kun "
                         f"{dist_km:.2f} km fra spotkoordinatet (krav: >1 km)"))
            continue
        d50 = spot.get("dybde_50m_km")
        if d50 is not None and dist_km <= d50:
            feil.append((spot["id"], f"offshore_point ({dist_km:.2f} km) er "
                         f"INNENFOR egen dybde_50m_km ({d50} km) - punktet "
                         "ligger foran, ikke forbi, 50-meterskoten"))
    assert not feil, feil


def test_ingen_andre_ukjente_avvik():
    """KJENTE_AVVIK skal vaere noyaktig de spottene som faktisk bryter
    kravet i dag - samme begrunnelse som den tilsvarende testen i
    test_spots_yaml.py sin monotonitet-allowlist."""
    spots, _ = A.load_spots()
    faktiske = set()
    for spot in spots:
        if spot["klasse"] not in ("A", "B"):
            continue
        op = spot.get("offshore_point")
        if not op:
            continue
        dist_km = A.P.haversine_km(spot["lat"], spot["lon"], op[0], op[1])
        d50 = spot.get("dybde_50m_km")
        if dist_km <= 1.0 or (d50 is not None and dist_km <= d50):
            faktiske.add(spot["id"])
    assert faktiske == KJENTE_AVVIK


def test_molen_odden_offshore_point_flyttet_forbi_shoalingsonen():
    """ordre 2026-09-06 (se rapport til bruker): eksplisitt regresjonstest
    for selve fiksen - Molen skal IKKE lenger vaere i KJENTE_AVVIK (den
    var det, implisitt, foer denne commiten: gammelt offshore_point var
    0,71 km ute, godt innenfor shoalingsonen)."""
    spots, _ = A.load_spots()
    molen = next(s for s in spots if s["id"] == "molen_odden")
    op = molen["offshore_point"]
    dist_km = A.P.haversine_km(molen["lat"], molen["lon"], op[0], op[1])
    assert dist_km > 1.0
    assert dist_km > molen["dybde_50m_km"]
    assert "molen_odden" not in KJENTE_AVVIK
