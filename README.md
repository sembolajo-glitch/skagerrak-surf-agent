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

# 4. Valider FØR du stoler på tallene: avstand fra fire referansepunkter
#    til kystkontur (fanger en speilvendt/akse-byttet kontur - se under),
#    pluss en SVG av hele konturen + de 14 spottene til visuell kontroll.
python validate_geodata.py
```

`fetch_geodata.py` gjetter ikke på WFS-URL-en – det opplagte gjettet
(`wms.geonorge.no/skwms1/wfs.dybdedata2`, samme mønster som WMS-en) er
**bekreftet feil (404)**. I stedet slår skriptet opp riktig endepunkt i
[Geonorge sin kartkatalog-API](https://kartkatalog.geonorge.no/api/getdata/9e01fc8e-e1d3-4d11-8b9d-22e1d132ddfe)
for datasettet "Sjøkart – Dybdedata" (UUID
`9e01fc8e-e1d3-4d11-8b9d-22e1d132ddfe`), og leter rekursivt gjennom svaret
etter et WFS-felt uten å anta et eksakt skjema. `--wfs-url` overstyrer
oppslaget helt; de gamle gjettede kandidat-URL-ene er beholdt som siste
utvei hvis kartkatalog-oppslaget selv skulle feile. **Bekreftet riktig mot
den ekte tjenesten**: `https://wfs.geonorge.no/skwms1/wfs.dybdedata` (uten
en "2" på slutten, i motsetning til WMS-en).

Lagnavnene er heller ikke hardkodet – skriptet kjører `GetCapabilities` på
den funnede URL-en og matcher `FeatureType`-navn mot "kyst"/"dybde" i
stedet (bekreftet: `app:Kystkontur`/`app:Dybdekurve` blant 36 lag). Kjør
`python fetch_geodata.py --list-layers` for å bare se hvilke lag tjenesten
faktisk tilbyr, uten å prøve `GetFeature` i det hele tatt. Skriptet
paginerer med `count`/`startIndex`, og henter GML (se under for hvorfor
JSON ikke prøves) – begge deler bekreftet mot den levende tjenesten. Selve
geometritypene i GML-parseren (`gml_to_shapely`) er derimot kun testet mot
`LineString`, som er det Kystkontur/Dybdekurve faktisk leverer – `Polygon`/
`Multi*`-grenene er fortsatt bare testet mot håndlagde XML-eksempler.

Får et ellers riktig lag 0 features, er det som oftest bbox-en (feil
akserekkefølge eller feil CRS), ikke tjenesten. `python fetch_geodata.py
--probe` isolerer akkurat det spørsmålet uten å laste ned eller skrive
noe: den prøver (a) `GetFeature` helt uten bbox, (b) bbox `lon,lat` med
`srsName=EPSG:4326`, (c) bbox `lat,lon` med
`srsName=urn:ogc:def:crs:EPSG::4326` (WFS 2.0-regelen for geografiske
EPSG-koder), og (d) bbox i UTM33 (`EPSG:25833`, ofte native CRS for norske
datasett) – og viser rå geometri + bounds for første treff i hver variant,
så det er synlig med egne øyne hvilken kombinasjon som faktisk gir treff.

**Kjørt mot den ekte tjenesten 2026-08-30 – tre funn, alle låst inn i koden:**
1. **Variant (c) er riktig** – bbox som `lat,lon` med
   `srsName=urn:ogc:def:crs:EPSG::4326`. Kortformen `EPSG:4326` (lon,lat)
   og UTM33 ga begge 0 features. `fetch_features_gml()` sender nå kun
   denne varianten – ingen flere gjetterunder.
2. **JSON støttes ikke i det hele tatt** – `outputFormat=application/json`
   gir HTTP 400 *"This WFS is not configured to handle the output/input
   format"*. Skriptet prøver ikke lenger JSON i hovedpipelinen (kun
   `--probe` tester det fortsatt, som diagnostikk).
3. **Svargeometrien kommer i (breddegrad, lengdegrad)-rekkefølge** – også
   uten noen `srsName` i forespørselen. `resolve_axis_swap()` retter dette
   opp basert på (i prioritert rekkefølge) en eksplisitt `srsName` i selve
   svaret, ellers `srsName`-en vi ba om, ellers en bekreftet
   default-på-True. Som andrelinjeforsvar sjekker `validate_bounds()` at
   *ingen* feature havner utenfor 57–60 N / 8–12 Ø (Ytre Oslofjord-området)
   – gjør den det, feiler skriptet tydelig i stedet for å skrive filen.

`run_layer()` logger antall features og samlet bounding box for hvert lag
rett før filen skrives, slik at man kan se at området faktisk stemmer.

Skriptet skal **aldri fullføre stille**: hvert eneste HTTP-kall – også de
som feiler – logges til stderr (full URL, statuskode/unntak, første 500
tegn) og dumpes til `data/_raw/` (`NNN_<hva>.meta.txt` +
`NNN_<hva>.json|xml|bin`), uansett om kallet lykkes eller ikke. Enhver feil
gir en tydelig sluttmelding med henvisning til `data/_raw/` og exit-kode 1
– `--dump-raw` er beholdt som flagg for bakoverkompatibilitet, men gjør
ikke lenger noe (dumping er alltid på). Se rapporten skriptet skriver ut
til stderr; den sier eksplisitt hvilken URL/versjon/format som faktisk ble
brukt. Meld fra om noe i parsingen ikke stemmer, så rettes det opp – de
rene parse-funksjonene har enhetstester i `test_fetch_geodata.py`, men de
er skrevet mot håndlagde XML-eksempler etter OGC-spekken, ikke mot ekte
responser.

`build_fetch.py` endrer **aldri** `physics.py`, `ensemble.py` eller de
eksisterende `fetch_km`/`local_fetch_km`-feltene agenten faktisk bruker –
det legger bare til `fetch_km_72`, `fetch_km_72_effektiv`, `fetch_km_manuell`
og `dybde_20m_km`/`dybde_30m_km`/`dybde_50m_km` ved siden av. Om og hvordan
de nye tallene skal erstatte de håndlagde, er et eget steg.

**`fetch_km_72` vs. `fetch_km_72_effektiv`.** Rå enkelt-stråleskyting kan
smette gjennom en trang passasje mellom skjær og gi f.eks. 300 km i en
retning der naboretningene er blokkert på under en kilometer – det er en
metodeforskjell mot håndmålt "fetch i bølgeforstand" (som ser bort fra slike
smett), ikke en feil i strålene selv. `fetch_km_72_effektiv` tar medianen av
strålen og de fire naboene i en ±10°-sektor (samme 5°-oppløsning som
`fetch_km_72` selv – vinduet dekker akkurat ±10°), og er tabellen
avviksrapporten faktisk sammenligner mot `fetch_km_manuell`.

**Kjørt mot ekte data, effekten er blandet:** noen spot forbedres kraftig
(Bastøy odden: største avvik 144→33 km), andre er uendret (Larkollen:
298→298 km) eller til og med litt verre (Hvasser/Sandø: 88→180 km) – en
enkelt utligger blir borte i medianen, men et par av spottene har flere enn
fem sammenhengende 5°-punkter som smetter gjennom (eller det er reell fetch
gjennom en trang, men faktisk åpen renne som de håndlagde tabellene aldri
fanget). `fetch_km_72_effektiv` er ikke en fasit – bruk avviksrapporten til
å se hvilke spot som fortsatt trenger et lengre medianvindu eller manuell
gjennomgang.

**`validate_geodata.py`** – to sjekker før man stoler på en nedlasting:

1. *Referansepunkter.* Avstand fra `data/kystkontur.geojson` til fem punkter
   med kjent, grovt anslått avstand til land. De to viktigste er ute i åpent
   vann og skal være *langt* fra land (midt i Vestfjorden mellom
   Bolærne/Rauer, og åpent Skagerrak sørvest for Færder) – havner et av dem
   nær land, er konturen sannsynligvis speilvendt, akse-byttet eller
   forskjøvet/rotert, uansett hvor riktig de kystnære punktene ser ut isolert.
   Feiler (exit 1) hvis noe punkt bryter sin terskel – blokkerer
   committ-steget i `geodata.yml`.
2. *SVG-forhåndsvisning* – hele kystkonturen tegnet med de 14 spottene fra
   `spots.yaml` markert, skrevet til `out/kystkontur_preview.svg` og lastet
   opp som Actions-artifact (`kystkontur-preview`) uansett utfall, siden
   bildet er mest nyttig akkurat når noe har gått galt.

**Kjørt mot ekte nedlastet data 2026-08-30** (61 594 kystkontur-features,
bounds 9,30–11,21 Ø / 58,72–59,50 N): tre av fem referansepunkter besto med
tette, plausible marginer (Færder fyr 22 m, Slagen 229 m, Bastøy sørspiss
238 m). De to fjerneste punktene besto ikke, men er ikke i nærheten av å
være på land: "Midt i Vestfjorden" var 2399 m fra land (krav 3000 m,
nærmeste konturpunkt på nesten identisk breddegrad), "Åpent Skagerrak" var
7723 m (krav 10 000 m, nærmeste kontur rett nord). Begge er sammenhengende,
lokal geometri – ikke de vilkårlige hoppene et speilvendt eller forskjøvet
datasett ville gitt. Mest sannsynlig er terskelen satt en anelse strammere
enn den faktiske fjordbredden/kystavstanden der. Det opprinnelige
Vestfjorden-punktet (59.200/10.600) lå enda nærmere – rett ved
Bolærne/Rauer, ikke i åpen fjord – og ble flyttet til 59.250/10.560 av
samme grunn. Om terskelen eller punktene bør justeres videre er en
vurdering, ikke noe skriptet gjør automatisk.

---

## Hva den ikke gjør

- **Ingen refraksjon eller shoaling ved brytningen.** Hs er dypvannsverdi utenfor spotten, ikke bølgehøyden i ansiktet.
- **Ingen strøm.** Utgående brakkvann mot sørlig vind gjør fjordsjøen brattere enn fetchen tilsier.
- **Fetch-tabellene som faktisk brukes (`fetch_km`/`local_fetch_km`) er fortsatt håndlaget** fra kartlesing. Målte verdier finnes nå ved siden av som `fetch_km_72`/`dybde_Xm_km` (se «Geodata fra Kartverket» over), men er ikke koblet inn i beregningen ennå.
- **`min_hs` for de ti uprøvde spottene er gjetning.** Ensemblet gir dem automatisk lavere `confidence`, men det fikser ikke en systematisk feil terskel. Behandle varslene deres som hypoteser.

Første sesong er datainnsamling. Andre sesong er den nyttig.

