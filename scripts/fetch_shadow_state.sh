#!/usr/bin/env bash
# Henter out/alert_state.json, out/spotgrid.json og out/shadow.csv fra
# data-grenen inn i arbeidskatalogen. Kjores fra repo-roten av
# forecast.yml sitt "Hent tilstand fra data-grenen"-steg (og av
# test_fetch_shadow_state.py, mot en test-fixture som peker "origin"
# et annet sted).
#
# shadow.csv har et strengere krav enn de to andre filene: den akkumulerer
# historikk (33 000+ rader per 2026-09-02) som ALDRI skal gaa tapt, saa
# denne skriptet skiller haardt mellom "data-grenen finnes ikke enna"
# (legitimt - forste kjoring noensinne, tom fil er ok) og "henting feilet"
# av andre grunner (skal feile HARDT, IKKE stille gi en tom fil).
#
# Rotaarsaken dette forhindrer: den gamle koden gjorde
#   git show data:out/shadow.csv > out/shadow.csv 2>/dev/null || true
# `>`-omdirigeringen oppretter/tommer maalfila FOR git show kjorer, ogsaa
# naar git show feiler - saa en forbigaaende git-feil (eller "grenen
# fantes ikke enna", som var tilfellet paa aller forste kjoring) gav en
# 0-byte lokal shadow.csv, som senere ble PUSHET OVER de eksisterende
# radene i "Publiser til data-grenen". Se agent.py sin append_shadow_log()
# for hvordan den konkrete, allerede-skjedde skaden (manglende header) ble
# reparert i etterkant - dette skriptet forhindrer at det skjer igjen,
# eller at det noen gang skjer et reelt datatap (ikke bare manglende
# header) paa samme vis.
#
# alert_state.json/spotgrid.json rammes ikke av det samme (ingen
# akkumulert historikk aa miste - alert_state.json gjenoppbygges fra
# scratch uansett hvis den mangler, spotgrid.json er en statisk build-
# artefakt), saa de beholder det gamle, tolerante monsteret: manglende
# fil paa data-grenen er ikke en feil.
set -euo pipefail

mkdir -p out

rows_before=0
if git ls-remote --exit-code --heads origin data > /dev/null 2>&1; then
    git fetch --depth=1 origin data

    git show origin/data:out/alert_state.json > out/alert_state.json 2>/dev/null || true
    git show origin/data:out/spotgrid.json    > out/spotgrid.json    2>/dev/null || true

    # INGEN "|| true" her - en feil her (git show finner grenen, men
    # feiler av andre grunner paa akkurat denne filen) skal stoppe
    # kjoringen, ikke stille gi en tom fil. set -e over gjor at dette
    # skriptet avbrytes umiddelbart hvis git show feiler.
    git show origin/data:out/shadow.csv > out/shadow.csv.tmp
    mv out/shadow.csv.tmp out/shadow.csv
    rows_before=$(wc -l < out/shadow.csv)
else
    echo "data-gren finnes ikke enna - starter med tomme filer" >&2
    : > out/shadow.csv
fi

echo "$rows_before" > out/.rows_before
