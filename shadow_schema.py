"""
Skjemaet for out/shadow.csv. ÉN konstant (FIELDS) alle skrive- og
lesesteder skal bruke - agent.py sin append_shadow_log() skriver mot
den, test_shadow_schema.py haandhever kontrakten under.

APPEND-ONLY-KONTRAKT: nye felt legges KUN til BAKERST. Ingen felt fjernes,
navngis om eller flytter posisjon. Grunnen: 33 000+ historiske rader paa
data-grenen (per 2026-09-02) er skrevet mot eldre, kortere versjoner av
denne lista (se FIELDS_HISTORY under), og csv.DictReader mapper
POSISJONELT naar en rad har faerre felt enn header - None for feltene
raden ikke naar. Det stemmer KUN naar hver tidligere liste er et STRIKT
PREFIKS av dagens - reorganiser eller sett inn et felt i midten, og alle
eldre rader tolkes feil, stille, uten at noe krasjer.
"""

FIELDS = [
    "run_at", "spot", "time", "score", "hs_eff", "tp_eff", "dir_eff",
    "wind_speed", "wind_from", "wind_label", "q_size", "q_period",
    "q_wind", "q_water", "local_hs", "prop_hs", "gate_hs", "gate_tp",
    "gate_energy_frac", "local_fetch_km", "local_duration_h", "source",
    # kalibreringsgrunnlag for swell/vindsjo-andel (se
    # physics.swell_fraction() - ikke i scoringen ennaa)
    "swell_hs", "windsea_hs", "swell_andel",
    # kalibreringsgrunnlag for ensemble.GLOBAL_MODEL_HS_REL_PENALTY - uten
    # denne kan paaslaget aldri etterproeves mot faktiske utfall, se
    # calibrate.py
    "partisjon_kilde",
    # kalibreringsgrunnlag for regional_wp_min/max (spots.yaml) - uten
    # disse kan porten aldri etterproeves mot faktiske utfall, se
    # agent.py sin score_hour()
    "regional_wp", "regional_gate_closed", "regional_gate_bypassed",
    # versjonsmerking (ordre 2026-09-02) - se agent.py sin _model_rev().
    # Uten denne drukner effekten av en scoring-endring (som porten over)
    # i rader skrevet med den GAMLE scoringen naar calibrate.py rapporterer
    # paa tvers av begge. calibrate.py grupperer paa
    # row.get("model_rev") or "pre-instrumentering", saa alle rader fra
    # FOR dette feltet fantes havner i EN epoke, ikke gjettes bakover.
    "model_rev",
]

# Tidligere versjoner av FIELDS, i vekstrekkefolge - REKONSTRUERT fra
# git-historikken til denne verdien (den laa i agent.py sin
# append_shadow_log() helt til den ble flyttet hit i denne commiten),
# IKKE fra hukommelse. De faktiske commitene der lista vokste:
#
#   c3bc658  "Add files via upload"                              22 felt (opprinnelig)
#   c49c927  "Ta inn swell/vindsjø-partisjoner fra Open-Meteo..." +3 -> 25 felt
#   60c6be5  "Legg partisjon_kilde i shadow.csv..."               +1 -> 26 felt
#
# (regional_wp/regional_gate_closed/regional_gate_bypassed kom i PR #16,
# etter 60c6be5, og gikk aldri i produksjon som noe annet enn dagens
# FIELDS over - derfor ingen egen historisk oppfoering for dem.)
FIELDS_HISTORY = [
    # c3bc658
    ["run_at", "spot", "time", "score", "hs_eff", "tp_eff", "dir_eff",
     "wind_speed", "wind_from", "wind_label", "q_size", "q_period",
     "q_wind", "q_water", "local_hs", "prop_hs", "gate_hs", "gate_tp",
     "gate_energy_frac", "local_fetch_km", "local_duration_h", "source"],
    # c49c927
    ["run_at", "spot", "time", "score", "hs_eff", "tp_eff", "dir_eff",
     "wind_speed", "wind_from", "wind_label", "q_size", "q_period",
     "q_wind", "q_water", "local_hs", "prop_hs", "gate_hs", "gate_tp",
     "gate_energy_frac", "local_fetch_km", "local_duration_h", "source",
     "swell_hs", "windsea_hs", "swell_andel"],
    # 60c6be5
    ["run_at", "spot", "time", "score", "hs_eff", "tp_eff", "dir_eff",
     "wind_speed", "wind_from", "wind_label", "q_size", "q_period",
     "q_wind", "q_water", "local_hs", "prop_hs", "gate_hs", "gate_tp",
     "gate_energy_frac", "local_fetch_km", "local_duration_h", "source",
     "swell_hs", "windsea_hs", "swell_andel", "partisjon_kilde"],
]
