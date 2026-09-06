"""
Strukturelle sjekker paa spots.yaml sine dybdeprofil-felt
(dybde_20m_km/_30m_km/_50m_km) - uavhengig av om verdiene kom fra
build_fetch.py eller ble lagt inn for haand.

ordre 2026-09-05 (se rapport til bruker): Molen odden sin tidligere
notes-profil (20 m paa 10,21 km, 30 m paa 8,05 km, 50 m paa 6,35 km) var
ikke-monoton i det den ble motbevist - 50 m LENGRE UTE enn 20 m er
umulig langs en rett straale fra land og ut. Signaturen paa en straale
som lop langs kysten i stedet for ut fra den, ikke en reell maaling.
Denne testen fanger akkurat den feilklassen automatisk, for noe spot,
i stedet for aa stole paa at noen ser det i et notes-felt eller i
diagnose_spot.py sitt (kun visuelle, ikke-blokkerende) rodt flagg.
"""

import agent as A

# Kjente, allerede dokumenterte unntak - IKKE en generell tillatelse.
# Fjern en id fra denne lista i samme commit som den faktisk rettes opp -
# test_ingen_andre_ukjente_avvik() under tvinger lista til aa holde seg
# noyaktig i sync med virkeligheten, og
# test_allowlist_refererer_kun_til_eksisterende_spots() feiler hvis en
# oppforing peker paa et spot som ikke lenger finnes (se orekroken,
# ordre 2026-09-06 - slettet, men blir IKKE fjernet automatisk her -
# feilen skal vaere synlig, ikke tyst reparert).
#
# ordre 2026-09-06 (se rapport til bruker): orekroken er slettet (fjernet
# herfra i samme commit). sletteroyene sitt gamle brudd (20 m paa 13,03 km,
# 50 m paa 11,88 km) gjaldt et koordinat 5,84 km unna det naavaerende -
# build_fetch.py sin nye maaling for det RETTEDE koordinatet er monoton
# (se spots.yaml sin egen notes for sletteroyene), saa den er OGSAA
# fjernet herfra.
KJENTE_IKKE_MONOTONE_UNNTAK = set()


def _ikke_monotone_par(spot):
    """Par (mindre_kote, storre_kote) der spot sin maalte avstand til
    den STORRE koten er kortere enn avstanden til den MINDRE - fysisk
    umulig langs en rett straale ut fra land."""
    dybder = {
        20: spot.get("dybde_20m_km"),
        30: spot.get("dybde_30m_km"),
        50: spot.get("dybde_50m_km"),
    }
    brudd = []
    kotter = sorted(k for k, v in dybder.items() if v is not None)
    for i, a in enumerate(kotter):
        for b in kotter[i + 1:]:
            if dybder[a] > dybder[b]:
                brudd.append((a, b, dybder[a], dybder[b]))
    return brudd


def test_dybdeprofil_er_monoton_for_alle_andre_spots():
    """20 m skal aldri ligge lenger ute enn 30 m, som aldri skal ligge
    lenger ute enn 50 m - for ethvert spot som IKKE er et allerede kjent,
    dokumentert unntak (se KJENTE_IKKE_MONOTONE_UNNTAK over)."""
    spots, _ = A.load_spots()
    feil = []
    for spot in spots:
        if spot["id"] in KJENTE_IKKE_MONOTONE_UNNTAK:
            continue
        brudd = _ikke_monotone_par(spot)
        if brudd:
            feil.append((spot["id"], brudd))
    assert not feil, (
        "ikke-monoton dybdeprofil (grunnere kote lenger ute enn dypere) "
        f"funnet: {feil}"
    )


def test_ingen_andre_ukjente_avvik():
    """KJENTE_IKKE_MONOTONE_UNNTAK skal vaere noyaktig de spottene som
    faktisk bryter monotonitet i dag - ikke mer (en fiktiv oppforing
    ville skjult en fremtidig regresjon paa akkurat det spottet), og
    ikke mindre (fanges allerede av testen over, men gjentas her for et
    tydelig feilsignal naar noen fikser ett av unntakene: fjern IDen fra
    lista i samme commit)."""
    spots, _ = A.load_spots()
    faktiske = {s["id"] for s in spots if _ikke_monotone_par(s)}
    assert faktiske == KJENTE_IKKE_MONOTONE_UNNTAK


def test_manuelle_transekter_havner_i_notes_ikke_i_dybde_feltene():
    """ordre 2026-09-05/06 (se rapport til bruker): manuelle Norgeskart-
    transekter (Molen sin 20 m/290 m + 30 m/412 m langs peiling 203, og
    Sletteroeyene sin 7/10/59/100 m langs peiling 196) skal IKKE skrives
    inn i dybde_20m_km/_30m_km/_50m_km - de feltene eies av build_fetch.py
    og bruker uansett andre maaldyp (20/30/50 m) enn transektene (som
    dekker andre dyp, f.eks. 0-30 m for Molen). Feltene har begge fatt
    EKTE build_fetch.py-tall i denne commiten (ordre 2026-09-06) - denne
    testen sjekker bare at de IKKE ble forvekslet med de manuelle
    malingene i notes."""
    spots, _ = A.load_spots()
    by_id = {s["id"]: s for s in spots}

    molen = by_id["molen_odden"]
    # build_fetch.py sitt eget tall (0.25), IKKE den manuelle transektens
    # 20 m/290 m = 0.29
    assert molen.get("dybde_20m_km") == 0.25
    assert "412" in molen.get("notes", "")
    assert "build_fetch.py" in molen.get("notes", "")

    sletteroyene = by_id["sletteroyene"]
    # build_fetch.py sitt eget tall (1.0), IKKE den manuelle transektens
    # egne maaldyp (7/10/59/100 m - ikke engang samme kotesett)
    assert sletteroyene.get("dybde_20m_km") == 1.0
    assert "2,02 km" in sletteroyene.get("notes", "")
    assert "build_fetch.py" in sletteroyene.get("notes", "")


def test_allowlist_refererer_kun_til_eksisterende_spots():
    """ordre 2026-09-06 (se rapport til bruker): en allowlist-oppforing
    som peker paa et spot som ikke lenger finnes (f.eks. fordi spotet ble
    slettet uten aa fjerne oppforingen) skal feile hoeyt - IKKE forsvinne
    stille inn i "ingen brudd funnet noensinne igjen"."""
    spots, _ = A.load_spots()
    kjente_ider = {s["id"] for s in spots}
    ukjente = KJENTE_IKKE_MONOTONE_UNNTAK - kjente_ider
    assert not ukjente, f"allowlist peker paa spot(ter) som ikke finnes: {ukjente}"
