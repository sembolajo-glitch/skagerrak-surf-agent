"""
Haandhever append-only-kontrakten shadow_schema.py sin docstring lover:
hver tidligere feltliste (FIELDS_HISTORY) skal vaere et STRIKT PREFIKS
av dagens FIELDS. Header-reparasjonen i PR #16 (og all lesing av eldre
rader via csv.DictReader) hviler paa akkurat dette - se modulens
docstring for hvorfor et innsatt eller ombyttet felt midt i lista
stille feiltolker alle eldre rader.
"""

import shadow_schema as S


def test_fields_har_ingen_duplikater():
    assert len(S.FIELDS) == len(set(S.FIELDS)), "duplikat(er) i FIELDS"


def test_fields_history_er_ikke_tom():
    """En tom historikk ville gjort testen under meningslos - den skal
    faktisk sjekke noe."""
    assert len(S.FIELDS_HISTORY) >= 1


def test_hver_historisk_liste_er_et_strengt_prefiks_av_fields():
    for old in S.FIELDS_HISTORY:
        assert S.FIELDS[:len(old)] == old, (
            f"FIELDS har blitt reorganisert - de forste {len(old)} feltene "
            f"matcher ikke lenger denne historiske lista: {old}"
        )


def test_fields_history_er_selv_strengt_voksende():
    """De historiske listene skal danne en prefiks-kjede seg imellom
    ogsaa (i den rekkefolgen de star i FIELDS_HISTORY) - ellers stemmer
    ikke "vekstrekkefolge"-pastanden i docstringen."""
    for shorter, longer in zip(S.FIELDS_HISTORY, S.FIELDS_HISTORY[1:]):
        assert len(shorter) < len(longer)
        assert longer[:len(shorter)] == shorter


def test_fields_er_minst_saa_lang_som_nyeste_historiske_liste():
    if S.FIELDS_HISTORY:
        assert len(S.FIELDS) >= len(S.FIELDS_HISTORY[-1])


def test_water_cm_lagt_til_bakerst_2026_09_05():
    """ordre 2026-09-05 (se rapport til bruker): vannstand kunne ikke
    sjekkes for okten 5. sept fordi feltet ikke var logget, selv om
    verdien allerede fantes i score_hour() sitt returdict. Lagt til
    bakerst i FIELDS - denne testen gjor kravet eksplisitt, utover den
    generiske prefiks-sjekken over."""
    assert S.FIELDS[-1] == "water_cm"
