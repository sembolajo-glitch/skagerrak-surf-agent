"""
Enhetstester for calibrate.py sin EWAM-vs-global-rapport. Bygger
(sesjon, shadow-rad)-par direkte (samme form som match() returnerer) og
fanger stdout - resten av report() er allerede indirekte dekket av disse.
"""

import calibrate as C


def _row(score, partisjon_kilde="", **extra):
    """Ei shadow.csv-rad, som csv.DictReader ville gitt den (alt strenger)."""
    row = {
        "score": str(score), "hs_eff": "1.5", "q_size": "0.8",
        "q_wind": "0.7", "q_period": "0.9", "partisjon_kilde": partisjon_kilde,
    }
    row.update(extra)
    return row


def _pair(spot, rating, score, partisjon_kilde=""):
    s = {"spot": spot, "rating": str(rating), "time": "2026-08-31T12:00Z"}
    r = _row(score, partisjon_kilde)
    return s, r


def test_global_bommer_oftere_gir_anbefaling_om_a_oke_straffen(capsys):
    """EWAM treffer hver gang det varsles godt vaer; global bommer
    (varsler godt, men flatt) storparten av gangene - skal utlose
    "OK ensemble.GLOBAL_MODEL_HS_REL_PENALTY"."""
    pairs = (
        [_pair("saltstein", 4, 70, "ewam") for _ in range(5)]
        + [_pair("saltstein", 0, 70, "global") for _ in range(5)]
    )
    C.report(pairs)
    out = capsys.readouterr().out

    assert "EWAM vs global modell" in out
    assert "OK ensemble.GLOBAL_MODEL_HS_REL_PENALTY" in out


def test_global_like_god_gir_anbefaling_om_a_senke_eller_fjerne(capsys):
    """Begge kilder treffer like godt - ingen grunn til straff."""
    pairs = (
        [_pair("saltstein", 4, 70, "ewam") for _ in range(5)]
        + [_pair("saltstein", 4, 70, "global") for _ in range(5)]
    )
    C.report(pairs)
    out = capsys.readouterr().out

    assert "Vurder aa senke eller fjerne ensemble.GLOBAL_MODEL_HS_REL_PENALTY" in out


def test_for_fa_sesjoner_i_en_gruppe_gir_ikke_statistikk(capsys):
    pairs = (
        [_pair("saltstein", 4, 70, "ewam") for _ in range(5)]
        + [_pair("saltstein", 0, 70, "global") for _ in range(2)]  # kun 2 - under grensen
    )
    C.report(pairs)
    out = capsys.readouterr().out

    assert "global  n=2    for fa til statistikk" in out
    # ingen anbefaling naar en av gruppene mangler statistikk
    assert "GLOBAL_MODEL_HS_REL_PENALTY" not in out.split("EWAM vs global modell")[1]


def test_ukjent_kilde_telles_for_seg_ikke_med_i_ewam_global(capsys):
    """Rader uten partisjon_kilde (eldre logg, eller ingen Open-Meteo-data)
    skal havne i en egen "ukjent"-gruppe, ikke smitte ewam/global-tallene."""
    pairs = (
        [_pair("saltstein", 4, 70, "ewam") for _ in range(5)]
        + [_pair("saltstein", 4, 70, "global") for _ in range(3)]
        + [_pair("saltstein", 2, 40, "") for _ in range(4)]
    )
    C.report(pairs)
    out = capsys.readouterr().out

    assert "ukjent  n=4" in out


def test_ingen_partisjon_kilde_i_det_hele_tatt_hopper_over_seksjonen(capsys):
    """Ingen av radene har partisjon_kilde satt - seksjonen skal ikke
    printes i det hele tatt (ingenting aa vise)."""
    pairs = [_pair("saltstein", r, 70, "") for r in (4, 4, 4, 0, 0, 0)]
    C.report(pairs)
    out = capsys.readouterr().out

    assert "EWAM vs global modell" not in out


def test_bomtur_rate_regnes_av_kalte_god_ikke_av_alle():
    """bomtur-rate = bomtur / (treff + bomtur), ikke / n - en gruppe med
    mange stille (rating lav, score ogsaa lav - verken treff eller
    bomtur) skal ikke fortynne raten."""
    ewam = (
        [_pair("saltstein", 4, 70, "ewam")] * 3       # treff
        + [_pair("saltstein", 0, 70, "ewam")]         # bomtur
        + [_pair("saltstein", 0, 30, "ewam")] * 10     # verken/eller - agenten var stille
    )
    global_ = [_pair("saltstein", 4, 70, "global")] * 5  # alle treff
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        C.report(ewam + global_)
    out = buf.getvalue()
    # 1 bomtur av (3 treff + 1 bomtur) = 25 %, ikke 1/14
    assert "bomtur-rate av 'agenten sa god'=25 %" in out


# ------------------------------------------------------- ekstern_wp-rapport


def _wp_pair(rating, hs_eff, tp_eff, ekstern_wp=None):
    """Samme (sesjon, shadow-rad)-par som _pair(), men med hs_eff/tp_eff/
    ekstern_wp satt eksplisitt - trengs for wave_power()-sammenligningen,
    som _pair() ikke setter opp (den bruker faste "1.5"/ingen tp_eff)."""
    s = {"spot": "saltstein", "rating": str(rating), "time": "2026-08-31T12:00Z"}
    if ekstern_wp is not None:
        s["ekstern_wp"] = str(ekstern_wp)
    r = _row(70, hs_eff=str(hs_eff), tp_eff=str(tp_eff))
    return s, r


def test_ekstern_wp_rapporteres_med_forhold_til_egen_wp(capsys):
    """Tre par med ekstern_wp satt: seksjonen skal vise n=3, hvert
    forhold, og en median - IKKE en fast omregningsfaktor (se docstring)."""
    import physics as P
    pairs = [
        _wp_pair(3, 2.0, 8.0, ekstern_wp=P.wave_power(2.0, 8.0) * 1.5),
        _wp_pair(3, 1.5, 7.0, ekstern_wp=P.wave_power(1.5, 7.0) * 1.5),
        _wp_pair(2, 1.0, 6.0, ekstern_wp=P.wave_power(1.0, 6.0) * 1.5),
    ]
    C.report(pairs)
    out = capsys.readouterr().out

    assert "Ekstern wp vs. egen wp" in out
    assert "n=3" in out
    assert "median forhold (ekstern/egen) = 1.50" in out
    assert "IKKE en fast omregningsfaktor" in out


def test_ekstern_wp_mangler_hopper_over_seksjonen(capsys):
    """Ingen sesjoner har ekstern_wp - seksjonen skal ikke printes."""
    pairs = [_wp_pair(r, 1.5, 7.0) for r in (4, 4, 4, 0, 0, 0)]
    C.report(pairs)
    out = capsys.readouterr().out

    assert "Ekstern wp vs. egen wp" not in out


def test_ekstern_wp_for_faa_par_gir_melding_ikke_median(capsys):
    """Under tre PARVISE observasjoner (ekstern_wp+egen begge kjent) - ikke
    nok til aa regne median, skal si det rett ut i stedet for aa late som
    om et tall betyr noe. Tredje sesjon (uten ekstern_wp) er bare med for
    aa passere det generelle "minst 3 okter"-kravet i report()."""
    import physics as P
    pairs = [
        _wp_pair(3, 2.0, 8.0, ekstern_wp=P.wave_power(2.0, 8.0) * 1.5),
        _wp_pair(3, 1.5, 7.0, ekstern_wp=P.wave_power(1.5, 7.0) * 1.3),
        _wp_pair(2, 1.0, 6.0),
    ]
    C.report(pairs)
    out = capsys.readouterr().out

    assert "Ekstern wp vs. egen wp" in out
    assert "for fa parvise observasjoner" in out
    assert "median forhold" not in out


# --------------------------------------------------------------- model_rev


def _pair_rev(spot, rating, score, model_rev=None):
    s = {"spot": spot, "rating": str(rating), "time": "2026-08-31T12:00Z"}
    r = _row(score, model_rev=model_rev or "")
    return s, r


def test_ulike_model_rev_gir_egne_seksjoner_i_rapporten(capsys):
    """Samme spot, to ulike model_rev - skal IKKE blandes i en pott
    (se calibrate.py sin report() for hvorfor: en scoring-endring ville
    druknet i eldre rader)."""
    pairs = (
        [_pair_rev("saltstein", 4, 70, "revA111111") for _ in range(3)]
        + [_pair_rev("saltstein", 4, 70, "revB222222") for _ in range(3)]
    )
    C.report(pairs)
    out = capsys.readouterr().out

    assert "saltstein  [revA111111]" in out
    assert "saltstein  [revB222222]" in out
    # begge seksjonene skal ha statistikk (3 okter er nok til aa passere
    # "for fa okter"-terskelen), ikke slaas sammen til 6 i en seksjon
    assert out.count("Treff:") == 2


def test_rader_uten_model_rev_havner_i_pre_instrumentering(capsys):
    """Rader skrevet FOR model_rev-feltet fantes (shadow_schema.py) - her
    simulert som fravaerende felt, akkurat som eldre rader paa
    data-grenen faktisk er - skal samles i EN "pre-instrumentering"-
    epoke, ikke gjettes bakover til noe spesifikt."""
    pairs = [_pair_rev("hvasser_sando", r, 70) for r in (4, 4, 4, 0, 0, 0)]
    C.report(pairs)
    out = capsys.readouterr().out

    assert "hvasser_sando  [pre-instrumentering]" in out


def test_blank_model_rev_regnes_som_pre_instrumentering():
    """Tom streng (slik en gammel rad uten feltet ville lest via
    csv.DictReader hvis feltet FANTES men var tomt) skal ogsaa telle som
    "pre-instrumentering" - `or`-fallbacken i report() dekker baade
    None og tom streng."""
    r = _row(70, model_rev="")
    assert (r.get("model_rev") or "pre-instrumentering") == "pre-instrumentering"
