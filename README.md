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

Har du et bølgeeffekt-/energitall fra en ekstern tjeneste (f.eks. surf-forecast) for samme økt, kan du logge det i `ekstern_wp`-kolonnen i `sessions.csv`. Det finnes ingen fast omregning til vår egen `wave_power()` — faktoren varierer med spot og forhold — så `calibrate.py` rapporterer bare forholdet mellom dem etter hvert som observasjoner samler seg opp, som en sanity-sjekk.

Sannsynligheten trenger en egen sjekk: grupper alle varsler i 10 %-bøtter og se om 70 %-varslene traff omtrent 70 % av gangene. Det er en reliabilitetskurve, og den krever minst 50 varsler før den betyr noe.

**ERA5-Ocean er stengt som kalibreringskilde (ordre 2026-09-02).** `backtest_sessions.py` ble bygget for å teste terskler mot elleve historiske økter via Open-Meteo sin ERA5-Ocean-reanalyse (eneste kilde med historikk før desember 2023), men grid-oppløsningen (~0,25°, ~28 km) er for grov for denne kysten: alle 14 spotene i `spots.yaml` faller i kun **fire** ERA5-gridceller, **åtte** av dem i én og samme celle. Kilden kan derfor verken si noe om lokal bølgehøyde ved en navngitt spot, eller om forskjeller mellom spots — bare et regionalt energitall (`regional_wp`), og selv det fra en celle 49–53 km ute i åpent Skagerrak, ikke fra kysten selv. Se `backtest_sessions.py` sin docstring for hele funnet. Det ene tallet forsøket ga: gulvet blant elleve positive økter var `regional_wp`=1,2 kW/m mot den (nå deaktiverte) terskelen 12,2 — ti ganger for høy, samme retning som feilfunnet som fikk porten slått av. `regional_wp_min`/`regional_wp_max` i `spots.yaml` forblir deaktivert (se kommentaren der). Neste kalibreringsforsøk på `regional_wp` må bruke `out/shadow.csv` fra faktisk drift — EWAM ~5 km / MET WW3 ~4 km, som faktisk oppløser kysten — ikke ERA5.

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
det legger bare til `fetch_km_72`, `fetch_km_72_effektiv`,
`fetch_km_72_endelig`, `fetch_km_72_kjegle`, `fetch_km_manuell` og
`dybde_20m_km`/`dybde_30m_km`/`dybde_50m_km` ved siden av. Om og hvordan
de nye tallene skal erstatte de håndlagde, er et eget steg.

**`fetch_km_72` vs. `fetch_km_72_effektiv` vs. `fetch_km_72_endelig`.** Rå
enkelt-stråleskyting kan gi 300 km i retninger der naboretningene er
under 1 km. `fetch_km_72_effektiv` (medianen av strålen og de fire naboene
i en ±10°-sektor) var det første forsøket på å fikse dette – men en
oppfølgende diagnose (`debug_fetch_rays.py`, se under) fant den egentlige
årsaken: `FETCH_MAX_KM` (300 km) er langt større enn diagonalen på det
nedlastede bbox-utsnittet (9,3–11,2 Ø / 58,7–59,5 N, diagonal ca. 150 km).
Alle klasse C-spottene ligger nær kanten av utsnittet. En stråle som ikke
treffer kystkontur *før* den forlater utsnittet finner rett og slett ikke
mer data, og faller tilbake på 300 km-taket – det er verken en reell
fjordåpning eller et smett mellom skjær, bare fravær av data lenger unna.
Ingen median kan fikse dette, uansett vindusbredde – medianen av fem
"kant"-verdier er fortsatt en kant-verdi.

`fetch_km_72_endelig` er den faktiske fiksen: hver av de 72 strålene
klassifiseres (`classify_ray_category()`) som **kyst** (traff ekte
kystkontur innenfor utsnittet – bruker den målte lengden) eller
**bbox_kant** (forlot utsnittet uten å treffe noe). For `bbox_kant` brukes
en kjent, analytisk avstand i stedet for å laste ned mer kystlinje – neste
land sørover fra fjordmunningen er Danmark, og de avstandene er kjent uten
måling (`ANALYTIC_SECTORS` i `build_fetch.py`):

| Retning (grader) | Avstand | Sted |
|---|---|---|
| 160–200 | 145 km | Skagen |
| 200–230 | 200 km | Hirtshals |
| 230–250 | 240 km | Skagerrak-åpningen mot Nordsjøen |
| andre `bbox_kant`-retninger | 60 km | usikker – markert som sådan |

Avviksrapporten sammenligner nå **kun** de 16-punktsretningene der begge
5°-naborastrålene er ekte `kyst`-treff mot `fetch_km_manuell` – det er den
eneste sammenligningen der begge sider er et reelt tall. Retninger med
`bbox_kant` i nabolaget hoppes over og telles opp i loggen i stedet for å
gi et falskt avvik.

**Kjørt mot ekte data:** de resterende avvikene (8–20 km i snitt per spot,
størst for S/SSO-retningene) er nå en reell metodeforskjell, ikke et
databug – rå stråleskyting stopper ved *enhver* kystkontur-linje
(inkludert et lite skjær rett i siktelinjen), mens håndmålingene
tilsynelatende har sett forbi slike skjær til det som faktisk begrenser
bølgeenergien. Det er ikke noe skriptet kan avgjøre selv.

**`fetch_km_72_kjegle` – kjeglekasting i stedet for enkeltstråle.**
Forklaringen over ble bekreftet: en enkelt stråle måler geometri, ikke
bølgefetch. En holme på noen hundre meter stopper en stråle fullstendig,
men stopper ikke bølgeenergien – den diffrakterer rundt og bygger seg
videre. For hver av de 72 hovedretningene skytes nå 21 delstråler over
±10° (`CONE_HALF_WIDTH_DEG`/`CONE_N_RAYS` i `build_fetch.py`, 1°
mellomrom – bestillingen sa «21 stråler over ±10 grader (hvert halve
grad)», som er aritmetisk selvmotsigende: ±10° i 0,5°-skritt gir 41
stråler, ikke 21. Jeg har valgt det bokstavelige stråletallet, 21, og
flagget avviket her i stedet for å stille velge den ene eller den andre
tolkningen). `percentile()` beregner 80-persentilen av de delstrålene som
traff ekte kystkontur ("kyst") i kjeglen – ikke medianen og ikke maksimum
– slik at ett enkelt skjær rett i siktelinjen ikke lenger stopper hele
retningen, mens en sammenhengende kystlinje fortsatt gjør det. Er over
halvparten av kjeglens delstråler `bbox_kant`, markeres hele
hovedretningen som åpent hav og samme analytiske utfylling som
`fetch_km_72_endelig` brukes.

*Skjærgårdens betydning per spot* – `report_kjegle_skew()` måler hvor mye
80-persentilen avviker fra medianen i kjeglen, retning for retning.
**Kjørt mot ekte data 2026-08-31:**

| Spot | Snittavvik p80–median | Størst avvik |
|---|---|---|
| verdens_ende | 6.10 km | +37.46 km (245°) |
| orekroken | 3.21 km | +32.73 km (265°) |
| jomfruland_ost | 2.81 km | +42.01 km (65°) |
| bastoy_odden | 1.39 km | +12.77 km (150°) |
| molen | 1.27 km | +4.74 km (225°) |
| sletteroyene | 1.16 km | +9.57 km (160°) |
| slagen | 0.94 km | +11.53 km (30°) |
| saltstein | 0.80 km | +5.07 km (240°) |
| rakke, skallevold | 0.71 km | +15.32 / +10.30 km |
| hvasser_sando | 0.62 km | +4.28 km (0°) |
| larkollen | 0.52 km | +8.79 km (345°) |
| svenner, portor | ≤0.03 km | ≤0.4 km – praktisk talt null |

`molen`-raden gjelder det GAMLE `molen`-punktet (58.968/9.805), som ble
slått sammen inn i `molen_odden` (58.975217/9.812139, 284 m unna –
ordre 2026-09-02). Ikke kjørt på nytt for det nye punktet ennå – se
`molen_odden` sin `notes` i spots.yaml.

To grupper: for `svenner` og `portor` gjør skjærene nesten ingen forskjell
(kysten der er enten sammenhengende eller reelt åpen langs hele kjeglen).
For `verdens_ende`, `orekroken` og `jomfruland_ost` er avviket stort nok
(30–40+ km i enkeltretninger) til at skjærgården tydelig er dominerende
der – enkeltstråler mot disse spottene ville gitt et systematisk for lavt
fetch-tall i akkurat de retningene.

*Avviksrapporten mot `fetch_km_manuell`* – kjørt på nytt mot
`fetch_km_72_kjegle` for de seks spottene med håndlaget tabell (kun ekte
`kyst`-treff sammenlignet, samme regel som for `_endelig`):

| Spot | `_endelig` snitt|avvik| | `_kjegle` snitt|avvik| |
|---|---|---|
| hvasser_sando | 20.3 km | 20.3 km |
| slagen | 13.8 km | 12.9 km |
| skallevold | 12.3 km | 12.8 km |
| larkollen | 12.6 km | 12.6 km |
| sletteroyene | 9.1 km | 8.0 km |
| bastoy_odden | 8.1 km | 6.6 km |

Kjeglekasting bedrer avviket for tre av seks spotter (slagen,
sletteroyene, bastoy_odden), er uendret for to (hvasser_sando, larkollen)
og litt verre for én (skallevold). Det er en liten, ikke dramatisk,
forbedring – konsistent med at bare et fåtall av spottenes retninger
faktisk har et skjær rett i siktelinjen. De gjenværende store avvikene
(fortsatt 7–20 km i snitt, størst for S/SSO) er ikke løst av
kjeglekasting og er trolig en annen effekt enn punktvis diffraksjon rundt
enkeltskjær – se «Hva den ikke gjør» under.

### Konklusjon (ordre 2026-08-31): fetch erstattes ikke – dybdeprofilene gjør

Tre metoder – rå enkeltstråle (`fetch_km_72`), analytisk bbox-fiks
(`fetch_km_72_endelig`) og kjeglekasting (`fetch_km_72_kjegle`) – gir alle
8–20 km avvik mot håndmålingene. Det er **ikke** en feil som kan kodes
bort: alle tre er varianter av geometrisk siktelinje mot kystkontur, mens
håndmålingene måler noe annet – hvor langt åpent vann som faktisk driver
bølgevekst, der bølgeenergi diffrakterer rundt små hindre på en måte
ingen av disse metodene modellerer fullt ut. For fetch-begrenset
bølgevekst er det håndmålingen som er riktig, ikke strålegeometrien.

Derfor: **`fetch_km`/`local_fetch_km` – feltene `agent.py`/`physics.py`
faktisk bruker – forblir håndlaget og uendret.** Alle fire beregnede
variantene (`fetch_km_72`, `_effektiv`, `_endelig`, `_kjegle`) står igjen
i `spots.yaml` som referanse og diagnostikk, ikke som erstatning. Flere
korreksjonsforsøk på fetch-siden er ikke planlagt.

**`skjaergaard_indeks`** er derimot brukbar ut av dette arbeidet: samme
tall som i skjærgårds-tabellen over (gjennomsnittlig |80-persentil minus
median| over kjeglens hovedretninger), skrevet per spot i `spots.yaml`.
Den sier hvor skjærgårdsdominert en spot er – høy indeks betyr at
enkeltskjær i kjeglen varierer mye, altså at *enhver* geometrisk
fetch-måling for den spotten er mindre til å stole på. Tiltenkt å senke
`confidence` i `ensemble.py` for høy-indeks-spotter, men **ikke koblet
inn der ennå** – kun skrevet til `spots.yaml` som forberedelse.

**Dybdeprofilene (`dybde_20m_km`/`dybde_30m_km`/`dybde_50m_km`) er derimot
målte og pålitelige, og var aldri en del av denne uenigheten** – de er en
ren avstandsmåling langs `facing`-retningen til en gitt dybdekote, uten
den fetch-vs-siktelinje-tvetydigheten over. De ble lagt inn i `spots.yaml`
som de måles, uten etterbehandling, tidligere i dette arbeidet.

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
bounds 9,30–11,21 Ø / 58,72–59,50 N): alle fem referansepunkter består.
De tre kystnære består med tette, plausible marginer (Færder fyr 22 m,
Slagen 229 m, Bastøy sørspiss 238 m). De to åpne punktene ("Midt i
Vestfjorden" og "Åpent Skagerrak") feilet først – ikke fordi konturen var
feil, men fordi de opprinnelige tersklene (3000/10 000 m) var *gjettet*
uten å måle først. Målt: 2399 m og 7723 m, begge sammenhengende, lokal
geometri (bekreftet ved å slå opp nærmeste konturpunkt – ikke de vilkårlige
hoppene et speilvendt eller forskjøvet datasett ville gitt). Tersklene er
senket til 2000/6000 m – rett under de målte verdiene, så sjekken fortsatt
er reell og ikke bare tilpasset for å bestå. Det opprinnelige
Vestfjorden-punktet (59.200/10.600) lå enda nærmere – rett ved
Bolærne/Rauer, ikke i åpen fjord – og ble flyttet til 59.250/10.560 av
samme grunn.

**`debug_fetch_rays.py`** (engangsdiagnose, ikke i `geodata.yml`) – for de
fem klasse C-spottene: skriver ut alle 72 råe `fetch_km_72`-verdiene i en
tabell sammen med retning, og klassifiserer hver stråle som ekte
kysttreff ("kyst") eller som en stråle som forlot bbox-utsnittet før den
fant noe å treffe ("kant" – se over). Tegner én SVG per spot med alle 72
strålene oppå kystkonturen, farget etter kategori, pluss en stiplet ramme
som viser bbox-kanten. **Kjørt mot ekte data:** 52 av 360 stråler (alle
fem spot) er "kant". De ekte kysttreffene er gjennomgående under 20–30 km
– konsistent med indre fjord/skjærgård, ikke åpent Skagerrak. Dette
bekreftet at "uendret etter median"-mønsteret fra `fetch_km_72_effektiv`
ikke betyr en reell fjordåpning eller et smett som ikke bør telle – det
betyr at dataene rett og slett ikke rekker langt nok i den retningen.

---

## Hva den ikke gjør

- **Ingen refraksjon eller shoaling ved brytningen.** Hs er dypvannsverdi utenfor spotten, ikke bølgehøyden i ansiktet.
- **Ingen strøm.** Utgående brakkvann mot sørlig vind gjør fjordsjøen brattere enn fetchen tilsier.
- **Fetch-tabellene som faktisk brukes (`fetch_km`/`local_fetch_km`) er fortsatt håndlaget** fra kartlesing. Målte verdier finnes nå ved siden av som `fetch_km_72_kjegle`/`dybde_Xm_km` (se «Geodata fra Kartverket» over), men er ikke koblet inn i beregningen ennå.
- **`min_hs` for de ti uprøvde spottene er gjetning.** Ensemblet gir dem automatisk lavere `confidence`, men det fikser ikke en systematisk feil terskel. Behandle varslene deres som hypoteser.

Første sesong er datainnsamling. Andre sesong er den nyttig.

