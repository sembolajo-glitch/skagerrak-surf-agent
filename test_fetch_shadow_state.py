"""
Enhetstester for scripts/fetch_shadow_state.sh - skript-niva, ikke en
faktisk GitHub Actions-kjoring. Bygger et bart "origin"-git-repo som
fixture og kjorer skriptet mot det, akkurat slik forecast.yml sitt
"Hent tilstand fra data-grenen"-steg gjor i CI.

Kjernen i det som testes: skillet mellom "data-grenen finnes ikke enna"
(legitimt, tom fil er ok) og "henting feilet" (skal feile hardt) - se
skriptets egen docstring for rotaarsaken dette forhindrer.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "scripts" / "fetch_shadow_state.sh"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="skriptet mangler")


def _git(args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                           capture_output=True, text=True)


def _bare_origin(tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(["init", "--bare", "-q"], cwd=origin)
    return origin


def _seed_data_branch(tmp_path, origin, with_shadow):
    """Push en 'data'-gren til origin - MED eller UTEN out/shadow.csv paa
    den, avhengig av with_shadow. UTEN simulerer "grenen finnes, men
    git show paa akkurat denne fila feiler" (samme symptom som en
    forbigaaende git-feil ville gitt)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init", "-q"], cwd=seed)
    _git(["config", "user.email", "t@example.com"], cwd=seed)
    _git(["config", "user.name", "t"], cwd=seed)
    _git(["checkout", "-q", "-b", "data"], cwd=seed)
    (seed / "out").mkdir()
    if with_shadow:
        (seed / "out" / "shadow.csv").write_text(
            "run_at,spot,time\n2026-01-01T00:00:00+00:00,saltstein,x\n"
        )
    else:
        (seed / "out" / "alert_state.json").write_text("{}")
    _git(["add", "-A"], cwd=seed)
    _git(["commit", "-q", "-m", "seed"], cwd=seed)
    _git(["push", "-q", str(origin), "data"], cwd=seed)


def _run_script(tmp_path, origin):
    work = tmp_path / "work"
    work.mkdir()
    _git(["init", "-q"], cwd=work)
    _git(["remote", "add", "origin", str(origin)], cwd=work)
    result = subprocess.run([str(SCRIPT)], cwd=work, capture_output=True, text=True)
    return work, result


def test_data_grenen_finnes_ikke_gir_tom_fil_ikke_feil(tmp_path):
    """Ingen 'data'-gren i det hele tatt paa origin - legitimt (forste
    kjoring noensinne). Skal IKKE feile, og skal gi en tom shadow.csv
    med .rows_before=0."""
    origin = _bare_origin(tmp_path)

    work, result = _run_script(tmp_path, origin)

    assert result.returncode == 0, result.stderr
    assert (work / "out" / "shadow.csv").read_text() == ""
    assert (work / "out" / ".rows_before").read_text().strip() == "0"


def test_data_grenen_finnes_men_git_show_feiler_paa_shadow_csv(tmp_path):
    """git ls-remote finner 'data'-grenen (den finnes), men git show
    paa out/shadow.csv feiler (her: fila finnes ikke paa den grenen).
    Skriptet skal feile HARDT - IKKE stille skrive/beholde en tom
    shadow.csv, som var nettopp rotaarsaken til at 33 000+ rader mistet
    headeren sin (se skriptets docstring)."""
    origin = _bare_origin(tmp_path)
    _seed_data_branch(tmp_path, origin, with_shadow=False)

    work, result = _run_script(tmp_path, origin)

    assert result.returncode != 0, "skulle feilet hardt paa en mislykket henting"
    # `>`-omdirigeringen i git show ... > out/shadow.csv.tmp oppretter
    # .tmp-fila FOR git show kjorer - den fila kan derfor godt finnes
    # (tom). Det avgjorende er at mv-linja ALDRI kjorte: out/shadow.csv
    # selv (maalfila som ville blitt pushet videre) skal ikke ha blitt
    # skrevet til av dette forsoket.
    assert not (work / "out" / "shadow.csv").exists()


def test_data_grenen_finnes_og_shadow_csv_hentes_riktig(tmp_path):
    """Den normale, vellykkede stien: grenen finnes, fila finnes paa
    den, hentingen lykkes og .rows_before matcher det som faktisk stod
    der."""
    origin = _bare_origin(tmp_path)
    _seed_data_branch(tmp_path, origin, with_shadow=True)

    work, result = _run_script(tmp_path, origin)

    assert result.returncode == 0, result.stderr
    content = (work / "out" / "shadow.csv").read_text()
    assert content.startswith("run_at,spot,time")
    assert "saltstein" in content
    assert (work / "out" / ".rows_before").read_text().strip() == "2"
