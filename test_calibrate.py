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
