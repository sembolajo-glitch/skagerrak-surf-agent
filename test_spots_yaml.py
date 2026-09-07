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
# Begge kommer fra build_fetch.py sitt eget kjente forbehold (se
# compute_depth_profile() sin docstring): naermeste treff langs straalen
# kan vaere en liten, LUKKET dybdering (en isolert grop/pinnacle) i
# stedet for den brede kystnaere isobaten - straalen ser ingen forskjell
# paa de to. Oppdaget her (ikke fikset her - utenfor scope for denne
# PR-en, som gjelder Molen/Saltstein), se rapport til bruker. Fjern en
# id fra denne lista i samme commit som den faktisk rettes opp -
# test_ingen_andre_ukjente_avvik() under tvinger lista til aa holde seg
# noyaktig i sync med virkeligheten.
KJENTE_IKKE_MONOTONE_UNNTAK = {"orekroken", "sletteroyene"}


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


def test_molen_odden_har_ingen_maskinlesbar_dybdeprofil_ennaa():
    """ordre 2026-09-05 (se rapport til bruker): den nye, faktisk maalte
    profilen (20 m/290 m, 30 m/412 m langs peiling 203 grader) skal IKKE
    skrives inn i dybde_20m_km/_30m_km/_50m_km - de feltene eies av
    build_fetch.py. Den nye maalingen star i notes med kilde og dato
    inntil build_fetch.py kan kjores paa nytt. Denne testen dokumenterer
    det bevisste valget - feiler den, er trolig noen paa vei til aa
    bryte den avtalen."""
    spots, _ = A.load_spots()
    molen = next(s for s in spots if s["id"] == "molen_odden")
    assert molen.get("dybde_20m_km") is None
    assert molen.get("dybde_30m_km") is None
    assert molen.get("dybde_50m_km") is None
    assert "412" in molen.get("notes", "")
    assert "build_fetch.py" in molen.get("notes", "")
