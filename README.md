# Skagerrak surf agent

Varsler når surfspots på Østlandet kan fungere. Bygget rundt én innsikt: **ingen operativ bølgemodell oppløser Vestfjorden**, så for spottene innenfor fjordmunningen må agenten regne lokal vindsjø og propagert swell selv.

```
pip install -r requirements.txt
export SURF_AGENT_UA="skagerrak-surf/0.1 (din@epost.no)"   # MET krever dette, ellers 403

python agent.py --mock storm      # syntetisk kuling, ingen nettverk
python agent.py --shadow          # ekte data, regn og logg, ikke varsle
python agent.py                   # ekte data + push
python agent.py --explain slagen  # full parametertabell time for time
python calibrate.py               # sammenlign mot egne økter
```

---

## De to tallene

Agenten leverer to variabler som svarer på to forskjellige spørsmål. De må holdes adskilt — slår du dem sammen mister du nettopp det som avgjør om du setter deg i bilen.

**`p_surf` (0–100 %) — blir det bølger i det hele tatt?**
Drives av *usikkerhet*, ikke av kvalitet. Agenten kjører 40 ensemblemedlemmer der den forstyrrer vindstyrke, vindretning, Hs og bølgeretning ved munningen, samt spotens egne strukturparametere (`transmission`, `sector_half_width`). Sannsynligheten er andelen medlemmer som klarer `min_hs`.

Spredningen vokser med varslingslengde, og — dette er den nyttige biten — **den vokser også når modellene er uenige**. Sier MET 2,0 m og EWAM 3,0 m på samme punkt, blåses spredningen opp og sannsynligheten faller, uansett hva medianen er. `model_spread` ligger i output så du ser når det skjer.

**`stars` (1–10) — hvis det blir bølger, hvor bra blir de?**
Betinget. Regnet som medianen over *bare de medlemmene som ga surf*. `stars_p10` og `stars_p90` viser spennet.

Konsekvensen av å skille dem: Bastøy odden kan vise **6,3 stjerner med 50 % sannsynlighet** — sjelden, men bra når det treffer. Det er en helt annen beskjed enn 3 stjerner med 100 % sannsynlighet, og en sammenslått score ville gjort begge til «middels».

**`confidence`** (hoy/middels/lav) er et tredje, uavhengig tall: hvor mye du skal stole på de to andre. Ukalibrerte spots får automatisk trekk.

Varsel sendes når **begge** terskler passeres: `alert_min_p_surf` (50 %) og `alert_min_stars` (5,5). Å kreve begge er poenget — høy sannsynlighet for dårlige bølger er ikke verdt et varsel, og 9 stjerner med 15 % sannsynlighet er en rekognosering, ikke en plan.

---

## Slik regner den

**Klasse A og B** — leser Hs/Tp/retning fra WW3/EWAM i et punkt 2–3 km utenfor spotten, vekter mot swellvinduet.

**Klasse C** (Slagen, Skallevold, Sletterøyene, Bastøy, Larkollen) — modellene duger ikke:

1. **Lokal vindsjø** — fetch- og varighetsbegrenset JONSWAP over en 16-punkts fetch-tabell. Går bakover i vindhistorikken så lenge retningen holder seg innenfor ±45°, bruker snittvind og faktisk varighet.
2. **Propagert swell fra munningen** — leser WW3 ved Færder, filtrerer bort alt utenfor fjordaksen med en cos^(2s)-spredningsintegral, ganger med `transmission`, forsinker med Cg = gT/4π.
3. Kvadratisk summering.

Score = 100 × q_størrelse × q_periode × q_vind^`wind_weight` × q_vannstand, med hard grense på `min_hs`.

---

## Produksjon

Alt kjører gratis på GitHub Actions + Pages. Ingen server.

**1. Push repoet til GitHub.** Slå på Pages under Settings → Pages → Source: GitHub Actions.

**2. Legg inn tre secrets** (Settings → Secrets → Actions):

| Secret | Verdi |
|---|---|
| `SURF_AGENT_UA` | `skagerrak-surf/1.0 (din@epost.no)` — MET avviser deg uten |
| `NTFY_TOPIC` | en lang tilfeldig streng, f.eks. `surf-a7f3k9m2q` |
| `DMI_API_KEY` | gratis fra dmi.dk/friedata — valgfri, men gir kryssjekk |

**3. Varsling.** Installer ntfy-appen (iOS/Android, gratis), abonner på samme topic-streng. Ferdig — ingen konto, ingen backend. Topicet er offentlig for den som gjetter navnet, så bruk en tilfeldig streng.

**4. Kjøreplan.** Workflowen går 03:20, 09:20, 15:20 og 21:20 UTC — omtrent tre timer etter modellkjøringene 00/06/12/18Z, som er når MEPS og ECMWF er ferdig distribuert.

**5. Tilstand.** `alert_state.json` og `shadow.csv` lagres på en `data`-gren mellom kjøringer. Uten det ville du fått varsel om samme vindu hver sjette time. Dedup-regelen: send én gang når vinduet dukker opp, og på nytt bare hvis kvaliteten flytter seg ≥1,5 stjerner eller sannsynligheten ≥20 prosentpoeng.

**6. `forecast.json`** publiseres til `https://<bruker>.github.io/<repo>/forecast.json`. GitHub Pages har åpen CORS, så Lovable kan hente den direkte.

Kjør de første ukene i skyggemodus: `workflow_dispatch` → huk av `shadow`. Da regner og logger den uten å sende noe.

---

## Frontend (Lovable)

Prompt du kan lime rett inn:

> Bygg et read-only dashboard som henter `https://<bruker>.github.io/<repo>/forecast.json` hvert 15. minutt. Ingen backend, ingen skriving.
>
> **Hovedvisning:** ett kort per spot, i den rekkefølgen `spots`-arrayet allerede har. Hvert kort viser:
> - Navn, `klasse` (A/B/C som liten etikett), `drive_min` som «1t 20m», båtikon hvis `boat: true`
> - **`best_stars`** stort, som 1–10 med utfylte/tomme stjerner (halve tillatt)
> - **`best_p_surf`** som prosentring ved siden av
> - `confidence` fra første vindu som farget prikk: hoy=grønn, middels=gul, lav=grå
> - Lite «verifisert»-merke hvis `kalibrert: true`
> - Rød advarselsboks hvis `access_warning` ikke er null
> - Kort uten `windows` vises nedtonet nederst
>
> **Vinduer:** under hvert kort, én rad per element i `windows` med `start`–`end` i lokal tid (dataene er UTC), `hours` varighet, `stars` med `stars_p10`–`stars_p90` som spennbånd, `p_surf` og `p_good` som to prosenttall, og hele `why`-strengen som forklaringstekst.
>
> **Detaljvisning ved klikk:** 72-timers diagram over `hours[]` med `stars` som linje, `p_surf` som skyggeområde bak, og `wind_speed` på sekundærakse. Under: tabell med `q_size`, `q_period`, `q_wind`, `q_water` per time, så man ser hvilket ledd som drar ned. For klasse C, vis også `local_hs` mot `prop_hs` som stablet areal — det viser om bølgen er lokal vindsjø eller swell utenfra. Vis `params` i en utslåbar boks nederst, så man ser hvilke terskler som faktisk ble brukt.
>
> **Varselsbanner** øverst hvis `mode` er `"shadow"` eller `"mock"`: «Skyggemodus — ingen varsler sendes».

---

## Parameterne du skal skru på

Alle ligger i `spots.yaml`.

**1. `min_hs`** — hard grense. Knappen som avgjør spam vs. taushet. Start for høyt.

**2. `gate.sector_half_width`** (klasse C) — hvor bred vinkelsektor fjorden slipper opp. Mest usikre parameter i modellen. Med 20° og en sjø fra 190° slipper 43 % av energien gjennom; med 12° faller det til 25 %.

**3. `gate.transmission`** — samleknapp for skjærgårdsblokkering og refraksjonstap. Start på 1.0.

**4. `wind_weight`** — hvor hardt dårlig vind straffes. Saltstein 0.55 (surfes rutinemessig i onshore chop), Skallevold 1.25 (sandvik som blir søppel med en gang).

**5. `uncertainty.*`** — styrer bredden på ensemblet, altså hvor fort `p_surf` faller med varslingslengde. Hvis agenten sier 80 % fem døgn fram og bommer systematisk, er `wind_rel` og `hs_rel` for lave.

`facing`, `swell_window` og `fetch_km` er geometri. Bommer modellen, sjekk dem mot sjøkartet — ikke tun dem bort.

---

## Kalibrering

`out/shadow.csv` får én rad per spot per time per kjøring. Fyll ut `sessions.csv` (mal i `sessions.example.csv`) etter hver økt **og hver bomtur**. Bomturene er de mest verdifulle radene — de er de eneste som kan senke en for lav `min_hs`.

`calibrate.py` rapporterer treff/bom/miss, median Hs-bias med forslag til ny `transmission`, og hvilket ledd som faktisk skiller gode fra flate dager.

Sannsynligheten trenger en egen sjekk: grupper alle varsler i 10 %-bøtter og se om 70 %-varslene traff omtrent 70 % av gangene. Det er en reliabilitetskurve, og den krever minst 50 varsler før den betyr noe.

---

## Geodata fra Kartverket

`fetch_km` og `dybdekurve` var opprinnelig håndlagde tall fra kartlesing. To
engangsskript erstatter dem med målte verdier fra Kartverkets sjøkart-WFS
("Sjøkart – Dybdedata", Geonorge, CC BY 4.0). Ingen av dem kjører i
`forecast.yml` – kjør dem manuelt når geodataene trenger oppdatering:

```
pip install -r requirements-geodata.txt

# 1. Last ned Kystkontur + Dybdekurve for 58.7-59.5 N, 9.3-11.2 Ø,
#    forenkle (~20 m toleranse) og lagre som GeoJSON i data/.
python fetch_geodata.py
git add data/*.geojson
git commit -m "oppdater geodata fra Kartverket"

# 2+3. Skyt 72 stråler per spot mot kystkontur (fetch_km_72) og finn
#      avstand til 20/30/50-meterskoten langs facing (dybde_Xm_km).
#      Skriver rett inn i spots.yaml, rapporterer avvik mot de
#      håndlagde 16-punkts-tabellene (fetch_km_manuell) på stderr.
python build_fetch.py
```

`fetch_geodata.py` kjenner ikke det eksakte WFS-endepunktet eller
lagnavnene på forhånd – det prøver en liste kandidat-URL-er, kjører
`GetCapabilities`, og matcher `FeatureType`-navn mot "kyst"/"dybde" i
stedet for å anta faste navn. Det forventer at tjenesten kan kreve
paginering, sette et tak på antall features per kall, og levere GML i
stedet for GeoJSON – alt dette håndteres, men er **ikke verifisert mot den
levende tjenesten** (utviklingsmiljøet dette ble skrevet i har ikke
nettverkstilgang til geonorge.no). Kjør med `--dump-raw` første gang og se
rapporten skriptet skriver ut til stderr; den sier eksplisitt hvilken
URL/versjon/format som faktisk ble brukt. Meld fra om noe i parsingen ikke
stemmer, så rettes det opp – de rene parse-funksjonene har enhetstester i
`test_fetch_geodata.py`, men de er skrevet mot håndlagde XML-eksempler
etter OGC-spekken, ikke mot ekte responser.

`build_fetch.py` endrer **aldri** `physics.py`, `ensemble.py` eller de
eksisterende `fetch_km`/`local_fetch_km`-feltene agenten faktisk bruker –
det legger bare til `fetch_km_72`, `fetch_km_manuell` og
`dybde_20m_km`/`dybde_30m_km`/`dybde_50m_km` ved siden av. Om og hvordan de
nye tallene skal erstatte de håndlagde, er et eget steg.

---

## Hva den ikke gjør

- **Ingen refraksjon eller shoaling ved brytningen.** Hs er dypvannsverdi utenfor spotten, ikke bølgehøyden i ansiktet.
- **Ingen strøm.** Utgående brakkvann mot sørlig vind gjør fjordsjøen brattere enn fetchen tilsier.
- **Fetch-tabellene som faktisk brukes (`fetch_km`/`local_fetch_km`) er fortsatt håndlaget** fra kartlesing. Målte verdier finnes nå ved siden av som `fetch_km_72`/`dybde_Xm_km` (se «Geodata fra Kartverket» over), men er ikke koblet inn i beregningen ennå.
- **`min_hs` for de ti uprøvde spottene er gjetning.** Ensemblet gir dem automatisk lavere `confidence`, men det fikser ikke en systematisk feil terskel. Behandle varslene deres som hypoteser.

Første sesong er datainnsamling. Andre sesong er den nyttig.

