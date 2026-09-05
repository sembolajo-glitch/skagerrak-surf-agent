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
    # myk regional-energi-port (ordre 2026-09-02) - se
    # ensemble.bypass_weight()/log_energy_margin() og score_hour() i
    # agent.py. bypass_weight er selve vekten (0-1) gate-beregningen
    # brukte; log_energy_margin er r = log(E_lokal/E_prop) den ble regnet
    # fra, None naar en side manglet energi helt eller for klasse A/B
    # uten local_fetch.
    "bypass_weight", "log_energy_margin",
    # brattheit-justering av regional_wp_min (ordre 2026-09-02) - se
    # physics.wave_steepness()/gate_threshold_factor() og score_hour() i
    # agent.py. steepness er Hs/L0 ved Saltsteins offshore_point (samme
    # tidspunkt regional_wp er regnet fra); gate_factor er faktoren
    # regional_wp_min ble ganget med FOR porten ble sjekket denne timen.
    "steepness", "gate_factor",
    # lokal middelvind (ordre 2026-09-03 - Slagen-diagnosen: 0,59 mot
    # ventet 0,80 m ved 15 m/s/33 km fetch forklartes av at build_local_sea()
    # sin faktiske vindmiddelverdi laa lavere enn den observerte
    # punktvinden. Feltet fantes allerede per time (agent.py sin
    # evaluate_class_c(), noekkelen "local_wind_mean") og skrives allerede
    # til out/spots/<id>.json, men manglet i shadow.csv - uten det kan
    # klasse C-beregningen ikke etterproeves fra loggen alene.
    "local_wind_mean",
    # den retningsvektede hoeyden (ordre 2026-09-03, se rapport til bruker)
    # - hs_eff er RAA, uten retningsvekting; hs_vektet = hs_eff * wf er
    # tallet q_size faktisk er regnet fra (se score_hour() i agent.py).
    # For klasse C er wf alltid 1.0 - hs_eff har alt gaatt gjennom gate sin
    # retningsfiltrering, saa hs_vektet == hs_eff der. Uten dette feltet kan
    # score_hour() sin faktiske hs-grunnlag ikke etterproeves fra loggen
    # alene - kun hs_eff (foer vekting).
    "hs_vektet",
    # vannstand i cm (ordre 2026-09-05, se rapport til bruker - okten
    # 5. sept 2026 kunne ikke sjekkes mot vannstand fordi feltet ikke var
    # logget). Verdien fantes ALLEREDE i pipelinen - score_hour() sitt
    # returdict har hatt "water_cm" siden foer denne endringen (se
    # agent.py, water_cm-parameteren kommer fra sources.kartverket_water_level()
    # via gather()) - den naadde bare aldri shadow.csv fordi feltet ikke
    # stod i FIELDS. Ingen ny integrasjon, kun logging av et tall som
    # alt ble regnet ut og skrevet til out/spots/<id>.json hver kjoring.
    "water_cm",
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
    # sjekkpunkt lagt til 2026-09-05 (se rapport til bruker) - FIELDS slik
    # den sto RETT FOER "water_cm" ble lagt til bakerst. Ikke rekonstruert
    # fra en enkelt commit-SHA som de tre over (for mange smaa tillegg
    # mellom 60c6be5 og na til aa liste hver for seg) - selve poenget med
    # aa legge til dette sjekkpunktet NAA er at
    # test_hver_historisk_liste_er_et_strengt_prefiks_av_fields() da faar
    # noe FERSKT aa sjekke fremtidige felt mot (de tre gamle listene over
    # daekker bare de forste 26 posisjonene - et felt satt inn feil sted
    # blant posisjon 27-36 ville ha sluppet gjennom dem uoppdaget).
    ["run_at", "spot", "time", "score", "hs_eff", "tp_eff", "dir_eff",
     "wind_speed", "wind_from", "wind_label", "q_size", "q_period",
     "q_wind", "q_water", "local_hs", "prop_hs", "gate_hs", "gate_tp",
     "gate_energy_frac", "local_fetch_km", "local_duration_h", "source",
     "swell_hs", "windsea_hs", "swell_andel", "partisjon_kilde",
     "regional_wp", "regional_gate_closed", "regional_gate_bypassed",
     "model_rev", "bypass_weight", "log_energy_margin", "steepness",
     "gate_factor", "local_wind_mean", "hs_vektet"],
]
