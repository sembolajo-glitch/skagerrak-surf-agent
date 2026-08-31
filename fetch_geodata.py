#!/usr/bin/env python3
"""
Engangsskript: last ned Kystkontur og Dybdekurve fra Kartverkets sjoekart-WFS
(Geonorge) for Skagerrak-omraadet, forenkle geometrien og lagre som GeoJSON
i data/. Kjoeres MANUELT naar geodataene trenger oppdatering - IKKE i
workflowen (forecast.yml kjoerer aldri dette).

    pip install -r requirements-geodata.txt
    python fetch_geodata.py
    git add data/*.geojson && git commit -m "oppdater geodata fra Kartverket"

WMS-referansen oppgitt for datasettet er
    https://wms.geonorge.no/skwms1/wms.dybdedata2
Det AAPENBARE WFS-gjettet (samme vert/sti-monster) - wms.geonorge.no/skwms1/
wfs.dybdedata2 - gir 404. Riktig URL slaas derfor opp paa forhaand, ikke
gjettes: skriptet spoerr Geonorge sin kartkatalog-API om metadata for
datasettet "Sjøkart - Dybdedata" (UUID 9e01fc8e-e1d3-4d11-8b9d-22e1d132ddfe,
se DYBDEDATA_WFS_UUID) og leter gjennom svaret (rekursivt, uten aa anta
eksakt feltnavn) etter et WFS GetCapabilities-felt. Den URL-en brukes
direkte. --wfs-url overstyrer oppslaget helt; de gamle gjettede
kandidat-URL-ene (WFS_URL_CANDIDATES) er beholdt som siste utvei hvis
kartkatalog-oppslaget selv feiler.

Naar en base-URL er bestemt (uansett kilde):
  1. proever WFS-versjoner (2.0.0, 1.1.0, 1.0.0) med GetCapabilities til en
     svarer.
  2. leser de faktiske FeatureType-navnene fra svaret og matcher dem mot
     "kyst"/"dybde"-noekkelord i stedet for aa anta faste navn.
  3. henter GetFeature som GML. outputFormat=application/json er IKKE
     stottet av denne tjenesten (bekreftet 400 "not configured to handle
     the output/input format 'application/json'" - se BBOX_SRS_NAME-
     kommentaren og --probe under), saa det proeves ikke lenger.
  4. bbox sendes LAAST INN som lat,lon med srsName=BBOX_SRS_NAME (urn-
     formen) - bekreftet --probe 2026-08-30 som eneste variant som gir
     treff (kortform EPSG:4326 og UTM33/EPSG:25833 ga begge 0 features).
  5. paginerer med count/startIndex (WFS 2.0.0) eller maxFeatures
     (1.0.0/1.1.0), og stopper naar en side gir faerre features enn spurt.
  6. akserekkefolgen paa koordinatene i selve GML-svaret rettes opp via
     resolve_axis_swap() - denne tjenesten returnerer konsekvent
     (breddegrad, lengdegrad), bekreftet --probe, ogsaa naar ingen srsName
     ble sendt i det hele tatt.
  7. fornuftssjekk (validate_bounds): enhver geometri utenfor SANITY_BOUNDS
     (57-60 N, 8-12 O) feiler tydelig i stedet for aa skrives stille til
     fil - se validate_bounds().
  8. antall features + samlet bounding box logges FOER filen skrives.

Punkt 3-7 var opprinnelig ikke testet mot den levende tjenesten (naettverket
i utviklingsmiljoeet hvor dette ble skrevet er sperret mot geonorge.no) -
de er na verifisert via --probe-kjoringer 2026-08-30, se git-historikken for
de faktiske responsene som avdekket dette.

FEILSOEKING: hvert eneste HTTP-kall - ogsaa de som feiler - logges til
stderr (full URL, statuskode/unntak, forste 500 tegn) og dumpes til
data/_raw/ (ett par filer per kall: NNN_<tag>.meta.txt + NNN_<tag>.<ext>).
Dette skjer alltid, uavhengig av --dump-raw (flagget er beholdt kun for
bakoverkompatibilitet med eksisterende kall). Skriptet skal ALDRI fullfore
stille - enhver feil gir en tydelig sluttmelding og exit-kode 1.

Kjor med --list-layers for aa bare gjore GetCapabilities og skrive ut alle
tilgjengelige lagnavn, uten aa proeve GetFeature i det hele tatt:

    python fetch_geodata.py --list-layers
    python fetch_geodata.py --list-layers --wfs-url https://annen.url/wfs

Faar du 0 features for et lag som ellers matcher fint, er det som oftest
bbox-en (akserekkefolge eller feil CRS), ikke tjenesten eller lagnavnet.
Kjor --probe for aa diagnostisere det isolert, uten aa proeve aa laste ned
noe: den proever (a) uten bbox i det hele tatt, (b) bbox lon,lat i EPSG:4326,
(c) bbox lat,lon (WFS2.0-regelen for geografiske EPSG-koder skrevet som
urn:...), og (d) bbox i UTM33 (EPSG:25833, ofte tjenestens native CRS) - og
viser raa geometri + bounds for foerste treff i hver variant:

    python fetch_geodata.py --probe
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from shapely.geometry import shape

import geo_utils as G

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data"

# lat_min, lon_min, lat_max, lon_max - oppgitt i oppgaven som 58.7-59.5 N, 9.3-11.2 O
DEFAULT_BBOX = (58.7, 9.3, 59.5, 11.2)
DEFAULT_TOLERANCE_M = 20.0
PAGE_SIZE = 1000
TIMEOUT = 60

USER_AGENT = "skagerrak-surf-agent/fetch_geodata (kartverket-geodata-integration)"

# "Sjøkart - Dybdedata" i Geonorge sin kartkatalog. UUID-en er stabil selv
# om den faktiske tjeneste-URL-en skulle endre seg igjen.
DYBDEDATA_WFS_UUID = "9e01fc8e-e1d3-4d11-8b9d-22e1d132ddfe"
KARTKATALOG_API = "https://kartkatalog.geonorge.no/api/getdata/{uuid}"

# Siste utvei hvis oppslaget mot kartkatalogen selv feiler (nettverksfeil,
# uventet svarskjema). Forste kandidat er BEKREFTET RIKTIG (kartkatalog-
# oppslag 2026-08-30: https://wfs.geonorge.no/skwms1/wfs.dybdedata - MERK:
# uten "2" paa slutten, i motsetning til WMS-en). De opprinnelige gjettene
# under - alle med "wfs.dybdedata2" - er BEKREFTET FEIL (DNS-feil/404), men
# staar igjen som dokumentasjon paa hva som er proevd.
WFS_URL_CANDIDATES = [
    "https://wfs.geonorge.no/skwms1/wfs.dybdedata",
    "https://wfs.geonorge.no/skwms1/wfs.dybdedata2",
    "https://openwfs.geonorge.no/skwms1/wfs.dybdedata2",
    "https://wms.geonorge.no/skwms1/wfs.dybdedata2",
]

WFS_VERSIONS = ["2.0.0", "1.1.0", "1.0.0"]

# lat_min, lon_min, lat_max, lon_max - fornuftssjekk for Ytre Oslofjord-
# omraadet. Enhver geometri utenfor dette betyr med overveldende
# sannsynlighet feil akserekkefolge/CRS i parsingen, ikke at dataene
# faktisk er der - se validate_bounds(). Litt videre enn DEFAULT_BBOX for
# aa tolerere features som stikker ut over kant-bbox-en.
SANITY_BOUNDS = (57.0, 8.0, 60.0, 12.0)

# WFS-en her stotter KUN GML (outputFormat=application/json gir 400 "not
# configured to handle the output/input format" - bekreftet --probe
# 2026-08-30). bbox LAAST INN til lat,lon med urn-formen etter samme probe:
# kortform EPSG:4326 (lon,lat) og UTM33 (EPSG:25833) ga begge 0 features.
BBOX_SRS_NAME = "urn:ogc:def:crs:EPSG::4326"

LAYERS = {
    "kystkontur": ["kystkontur", "kyst"],
    "dybdekurve": ["dybdekurve", "dybdekurver", "dybdekote"],
}

DEPTH_PROPERTY_CANDIDATES = [
    "dybde", "dyp", "depth", "elevation", "verdi", "value", "kote", "z", "hoyde",
]

NS_GML = {"gml": "http://www.opengis.net/gml"}


def log(*a):
    print(*a, file=sys.stderr)


_call_seq = 0


def _slug(text, maxlen=50):
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return (s[:maxlen] or "call")


def _ext_for(content_type):
    ct = (content_type or "").lower()
    if "json" in ct:
        return ".json"
    if "xml" in ct or "gml" in ct:
        return ".xml"
    if "html" in ct:
        return ".html"
    return ".bin"


def _dump_call(dump_dir, seq, tag, url, status, error, content, content_type=None):
    """Skriv metadata + (evt. avkortet) body for ETT HTTP-kall. Kalles for
    hvert eneste forsok - ogsaa naar det feiler - saa data/_raw/ alltid gir
    noe aa feilsoeke paa."""
    if not dump_dir:
        return
    dump_dir.mkdir(parents=True, exist_ok=True)
    base = dump_dir / f"{seq:03d}_{_slug(tag)}"
    meta_lines = [f"url: {url}", f"status: {status if status is not None else '(ingen HTTP-respons)'}"]
    if error:
        meta_lines.append(f"error: {error}")
    if content is not None:
        meta_lines.append(f"bytes: {len(content)}")
        meta_lines.append(f"content-type: {content_type or '?'}")
    base.with_suffix(".meta.txt").write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
    if content:
        MAX_DUMP = 200_000  # feilsoekingsdump, ikke ment til parsing - hold artifacten liten
        body = content[:MAX_DUMP]
        base.with_suffix(_ext_for(content_type)).write_bytes(body)


def _get(url, params, dump_dir=None, tag="call"):
    """
    GET med User-Agent, som ALLTID logger til stderr og dumper til
    dump_dir - baade ved suksess og ved unntak/feilstatus. Kastes
    videre (requests.RequestException) etter logging, saa kallerne kan
    fortsatt proeve neste kandidat, men ingenting forsvinner stille.
    """
    global _call_seq
    _call_seq += 1
    seq = _call_seq

    prepared_url = requests.Request("GET", url, params=params).prepare().url
    log(f"  [{seq:03d}] GET {prepared_url}")

    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        log(f"  [{seq:03d}] FEIL (ingen respons): {type(exc).__name__}: {exc}")
        _dump_call(dump_dir, seq, tag, prepared_url, None, f"{type(exc).__name__}: {exc}", None)
        raise

    ctype = r.headers.get("Content-Type", "")
    preview = (r.text[:500].replace("\n", " ") if r.text else "")
    log(f"  [{seq:03d}] status {r.status_code}, {len(r.content)} bytes, content-type={ctype!r}")
    log(f"  [{seq:03d}] forste 500 tegn: {preview!r}")
    _dump_call(dump_dir, seq, tag, prepared_url, r.status_code, None, r.content, ctype)

    r.raise_for_status()
    return r


def _localname(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


# --------------------------------------------------------------- kartkatalog


def _walk_json(obj, path=""):
    """
    Rekursiv generator over en JSON-struktur: yielder (path, key, value) for
    hvert dict-felt (path er en '.'/'[i]'-notasjon for feilsoeking). Brukes
    til aa lete etter en WFS-URL i kartkatalog-metadata UTEN aa anta et
    eksakt skjema paa forhaand - vi vet ikke sikkert om feltet heter
    GetCapabilitiesUrl, DistributionUrl, eller ligger i en Distributions-liste.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            yield (new_path, k, v)
            yield from _walk_json(v, new_path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_json(v, f"{path}[{i}]")


def _iter_dicts(obj):
    """
    Rekursiv generator over ALLE dict-objekter i en JSON-struktur - ogsaa de
    som ligger som elementer i en liste (f.eks. hvert element i en
    Distributions-liste). _walk_json alene fanger ikke disse som en
    'value', siden listeelementer ikke har en dict-noekkel aa henge (path,
    key, value) paa.
    """
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _extract_wfs_url(data):
    """
    Ren funksjon (ingen nettverk): let gjennom kartkatalog-metadata etter en
    WFS GetCapabilities-URL. Returnerer base-URL-en (uten spoerrestreng -
    discover_service bygger sine egne service/request/version-parametre).
    Kaster RuntimeError med en liste over ALLE URL-er funnet i svaret hvis
    ingenting matcher, saa feilen er lett aa undersoeke manuelt.

    Prioritet:
      1. et felt hvis navn inneholder "getcapabilit" og hvis verdi nevner wfs
      2. et felt "protocol"/"type" = noe med "wfs" ved siden av en "url"
         eller tilsvarende i samme objekt (vanlig i Distributions-lister)
      3. enhver URL-streng som selv inneholder baade "wfs" og "getcapabilit"
      4. enhver URL-streng som inneholder "wfs" (svakest signal, sist)
    """
    candidates = []  # (prioritet, url, beskrivelse)

    for path, key, value in _walk_json(data):
        if isinstance(value, str) and value.startswith("http"):
            kl, vl = key.lower(), value.lower()
            if "getcapabilit" in kl:
                candidates.append((0, value, f"felt '{path}'"))
            elif "wfs" in vl and "getcapabilit" in vl:
                candidates.append((1, value, f"felt '{path}' (URL inneholder GetCapabilities)"))
            elif "wfs" in vl:
                candidates.append((3, value, f"felt '{path}' (URL inneholder 'wfs')"))

    # "protocol"/"type" = noe med "wfs" ved siden av en "url" i SAMME objekt -
    # vanlig i Distributions-lister. Maa skanne alle dict-er (ogsaa
    # listeelementer), ikke bare _walk_json sine (path, key, value)-treff.
    for d in _iter_dicts(data):
        proto = url = None
        for k2, v2 in d.items():
            if k2.lower() in ("protocol", "type", "protocolname") and isinstance(v2, str) and "wfs" in v2.lower():
                proto = v2
            if k2.lower() in ("url", "getcapabilitiesurl", "distributionurl") and isinstance(v2, str) and v2.startswith("http"):
                url = v2
        if proto and url:
            candidates.append((0, url, f"objekt (protocol={proto!r})"))

    if not candidates:
        all_urls = sorted({v for _, k, v in _walk_json(data) if isinstance(v, str) and v.startswith("http")})
        raise RuntimeError(
            "Fant ingen WFS-URL i kartkatalog-metadata. "
            f"Alle URL-er i svaret: {all_urls or '(ingen URL-er funnet i det hele tatt)'}"
        )

    candidates.sort(key=lambda c: c[0])
    best_score, best_url, desc = candidates[0]
    others = [c for c in candidates[1:] if c[1] != best_url]
    log(f"  fant WFS-URL via {desc}: {best_url}")
    if others:
        log(f"  ({len(others)} andre kandidat(er) vurdert og forkastet: "
            f"{[c[1] for c in others[:5]]})")

    return best_url.split("?")[0]


def lookup_wfs_url(uuid=DYBDEDATA_WFS_UUID, dump_dir=None):
    """
    Slaa opp WFS-endepunktet for datasettet i Geonorge sin kartkatalog-API
    i stedet for aa gjette paa URL-monster (det opplagte gjettet,
    wms.geonorge.no/skwms1/wfs.dybdedata2, er bekreftet aa gi 404).
    """
    url = KARTKATALOG_API.format(uuid=uuid)
    log(f"Slaar opp WFS-URL for datasett {uuid} i Geonorge kartkatalog ...")
    r = _get(url, {}, dump_dir=dump_dir, tag=f"kartkatalog_{uuid}")
    data = r.json()
    return _extract_wfs_url(data)


def build_candidates(args, dump_dir):
    """
    Bygg den ordnede lista av WFS-base-URL-er aa proeve:
      1. --wfs-url, hvis satt - overstyrer alt annet, INGEN kartkatalog-oppslag.
      2. ellers: URL-en slaatt opp i kartkatalogen for DYBDEDATA_WFS_UUID.
      3. de gamle gjettede kandidatene, som siste utvei hvis 1-2 feiler/mangler.
    """
    if args.wfs_url:
        log(f"--wfs-url overstyrer: {args.wfs_url} (hopper over kartkatalog-oppslag)")
        return [args.wfs_url] + list(WFS_URL_CANDIDATES)

    candidates = []
    try:
        looked_up = lookup_wfs_url(dump_dir=dump_dir)
        candidates.append(looked_up)
    except Exception as exc:  # noqa: BLE001
        log(f"  ADVARSEL: kartkatalog-oppslag feilet ({type(exc).__name__}: {exc})")
        log("  faller tilbake til gjettede kandidat-URL-er (se WFS_URL_CANDIDATES)")

    for c in WFS_URL_CANDIDATES:
        if c not in candidates:
            candidates.append(c)
    return candidates


# ------------------------------------------------------------ GetCapabilities


def discover_service(candidates, dump_dir=None):
    """
    Proev kandidat-URL-er (se build_candidates) x WFS-versjoner til
    GetCapabilities svarer. Returnerer (base_url, version, {layer_name: xml_element}).
    """
    last_err = None
    for base_url in candidates:
        for version in WFS_VERSIONS:
            tag = f"capabilities_{base_url}_{version}"
            try:
                r = _get(base_url, {
                    "service": "WFS", "request": "GetCapabilities", "version": version,
                }, dump_dir=dump_dir, tag=tag)
            except requests.RequestException as exc:
                last_err = exc
                log(f"  [feil] {base_url} v{version}: {exc}")
                continue

            try:
                root = ET.fromstring(r.content)
            except ET.ParseError as exc:
                last_err = exc
                log(f"  [feil] {base_url} v{version}: ugyldig XML ({exc})")
                continue

            feature_types = {}
            for ft in root.iter():
                if _localname(ft.tag) != "FeatureType":
                    continue
                name_el = next((c for c in ft if _localname(c.tag) == "Name"), None)
                if name_el is not None and name_el.text:
                    feature_types[name_el.text.strip()] = ft

            if feature_types:
                log(f"  [ok] {base_url} (WFS {version}) - {len(feature_types)} lag funnet")
                return base_url, version, feature_types

            last_err = RuntimeError("GetCapabilities svarte, men ingen FeatureType funnet")
            log(f"  [tomt] {base_url} v{version}: ingen FeatureType-elementer i svaret")

    raise RuntimeError(
        f"Fant ingen fungerende WFS-endepunkt blant {candidates}. Siste feil: {last_err}\n"
        "Sjekk https://kartkatalog.geonorge.no for riktig WFS-URL for "
        "'Sjøkart - Dybdedata' og kjoer paa nytt med --wfs-url."
    )


def match_layer(feature_type_names, keywords):
    names = list(feature_type_names)
    for kw in keywords:
        for n in names:
            local = n.split(":", 1)[-1]
            if kw.lower() == local.lower():
                return n
    for kw in keywords:
        for n in names:
            local = n.split(":", 1)[-1]
            if kw.lower() in local.lower():
                return n
    return None


# -------------------------------------------------------------- GetFeature


def supports_json(base_url, version, type_name, dump_dir=None):
    params = {
        "service": "WFS", "request": "GetFeature", "version": version,
        "typeNames" if version == "2.0.0" else "typeName": type_name,
        "outputFormat": "application/json",
        ("count" if version == "2.0.0" else "maxFeatures"): 1,
    }
    try:
        r = _get(base_url, params, dump_dir=dump_dir, tag=f"supports_json_{type_name}")
    except requests.RequestException:
        return False
    ctype = r.headers.get("Content-Type", "")
    if "json" not in ctype.lower():
        try:
            json.loads(r.text)
        except ValueError:
            return False
    try:
        data = r.json()
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("type") == "FeatureCollection"


def fetch_features_json(base_url, version, type_name, bbox, dump_dir=None):
    """bbox = (lat_min, lon_min, lat_max, lon_max). Yield (shapely_geom, props)."""
    lat_min, lon_min, lat_max, lon_max = bbox
    bbox_variants = [
        f"{lon_min},{lat_min},{lon_max},{lat_max}",  # lon,lat (vanligst for GeoServer JSON/CRS84)
        f"{lat_min},{lon_min},{lat_max},{lon_max}",  # lat,lon - fallback
    ]

    for bbox_param in bbox_variants:
        start = 0
        got_any = False
        page_no = 0
        while True:
            params = {
                "service": "WFS", "request": "GetFeature", "version": version,
                ("typeNames" if version == "2.0.0" else "typeName"): type_name,
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "bbox": bbox_param,
            }
            if version == "2.0.0":
                params["count"] = PAGE_SIZE
                params["startIndex"] = start
            else:
                params["maxFeatures"] = PAGE_SIZE
                if start:
                    # ikke standard for 1.0/1.1, men noen GeoServer-installasjoner stoetter den
                    params["startIndex"] = start

            page_no += 1
            r = _get(base_url, params, dump_dir=dump_dir,
                     tag=f"getfeature_json_{type_name}_p{page_no}")
            data = r.json()
            feats = data.get("features", [])
            log(f"    side {page_no}: {len(feats)} features (startIndex={start}, bbox={bbox_param})")

            if feats:
                got_any = True
            for f in feats:
                geom = f.get("geometry")
                if geom is None:
                    continue
                yield shape(geom), (f.get("properties") or {})

            if len(feats) < PAGE_SIZE:
                break
            start += len(feats)

        if got_any:
            return
        log(f"    ingen treff med bbox-rekkefolge '{bbox_param}', proever neste")

    log("    ADVARSEL: ingen features funnet for noen bbox-rekkefolge - "
        "sjekk om laget faktisk daekker omraadet, eller om bbox-CRS trenger justering")


# ------------------------------------------------------------------- GML


def fetch_features_gml(base_url, version, type_name, bbox, dump_dir=None):
    """
    Hent alle features for et lag som GML - eneste format denne tjenesten
    stotter (outputFormat=application/json gir HTTP 400 "not configured to
    handle the output/input format 'application/json'", bekreftet --probe
    2026-08-30).

    bbox sendes som lat,lon med srsName=BBOX_SRS_NAME (urn-formen) - LAAST
    INN etter samme probe: kortformen EPSG:4326 (lon,lat) og UTM33
    (EPSG:25833) ga begge 0 features for denne tjenesten, kun lat,lon+urn
    traff.
    """
    lat_min, lon_min, lat_max, lon_max = bbox
    bbox_param = f"{lat_min},{lon_min},{lat_max},{lon_max}"

    start = 0
    page_no = 0
    while True:
        params = {
            "service": "WFS", "request": "GetFeature", "version": version,
            ("typeNames" if version == "2.0.0" else "typeName"): type_name,
            "srsName": BBOX_SRS_NAME,
            "bbox": bbox_param,
        }
        if version == "2.0.0":
            params["count"] = PAGE_SIZE
            params["startIndex"] = start
        else:
            params["maxFeatures"] = PAGE_SIZE

        page_no += 1
        r = _get(base_url, params, dump_dir=dump_dir,
                 tag=f"getfeature_gml_{type_name}_p{page_no}")
        root = ET.fromstring(r.content)

        n = 0
        for member in _iter_members(root):
            geom, props = _parse_gml_member(member, requested_srs_name=BBOX_SRS_NAME)
            if geom is not None:
                n += 1
                yield geom, props

        log(f"    side {page_no}: {n} features (GML, startIndex={start})")
        if n < PAGE_SIZE:
            break
        start += n


def _iter_members(root):
    for el in root.iter():
        if _localname(el.tag) in ("member", "featureMember"):
            yield el


def _parse_gml_member(member, requested_srs_name=None):
    """
    Et wfs:member inneholder ett feature-element med geometri + attributter.
    `requested_srs_name` er srsName-en VI sendte i forespoerselen (brukes som
    fallback for akserekkefolge hvis svaret ikke selv oppgir en - se
    resolve_axis_swap()).
    """
    feature_el = list(member)[0] if len(member) else None
    if feature_el is None:
        return None, {}

    geom = None
    props = {}
    for child in feature_el:
        local = _localname(child.tag)
        geom_el = _find_geometry(child)
        if geom_el is not None:
            geom = gml_to_shapely(geom_el)
            if geom is not None and resolve_axis_swap(geom_el, requested_srs_name):
                geom = fix_axis_order(geom)
            continue
        if child.text and child.text.strip():
            props[local] = child.text.strip()
    return geom, props


_GEOM_TAGS = {
    "Point", "LineString", "Polygon", "MultiPoint", "MultiLineString",
    "MultiPolygon", "MultiCurve", "MultiSurface", "Curve", "Surface",
}


def _find_geometry(el):
    if _localname(el.tag) in _GEOM_TAGS:
        return el
    for child in el:
        if _localname(child.tag) in _GEOM_TAGS:
            return child
    return None


def _srs_dimension(el):
    for k, v in el.attrib.items():
        if _localname(k) == "srsDimension":
            try:
                return int(v)
            except ValueError:
                return 2
    return 2


def _parse_poslist(el):
    dim = _srs_dimension(el)
    nums = [float(x) for x in (el.text or "").split()]
    return [tuple(nums[i:i + dim][:2]) for i in range(0, len(nums), dim)]


def _parse_legacy_coordinates(el):
    """<gml:coordinates> - eldre format, "lon,lat lon,lat ..."."""
    text = (el.text or "").strip()
    pts = []
    for pair in text.split():
        parts = pair.split(",")
        if len(parts) >= 2:
            pts.append((float(parts[0]), float(parts[1])))
    return pts


def _ring_coords(ring_el):
    for child in ring_el.iter():
        local = _localname(child.tag)
        if local == "posList":
            return _parse_poslist(child)
        if local == "coordinates":
            return _parse_legacy_coordinates(child)
        if local == "pos":
            pass  # samles av posList-varianten under; enkeltpunkt haandteres ikke her
    return []


def gml_to_shapely(el):
    """
    Konverter et gml:*-geometrielement til shapely. Stoetter (Multi)LineString,
    (Multi)Polygon/Surface og (Multi)Curve - det som er aktuelt for kystkontur
    (polygon/linje) og dybdekurve (linje).

    Koordinater beholdes i den rekkefoelgen GML gir dem. Kall
    fix_axis_order() paa resultatet basert paa srsName foer bruk, siden
    "urn:ogc:def:crs:EPSG::4326/4258" har lat/lon-akserekkefoelge mens
    "EPSG:4326" (plain) har lon/lat.
    """
    local = _localname(el.tag)

    if local in ("LineString", "Curve"):
        coords = []
        for child in el.iter():
            cl = _localname(child.tag)
            if cl == "posList":
                coords = _parse_poslist(child)
                break
            if cl == "coordinates":
                coords = _parse_legacy_coordinates(child)
                break
        from shapely.geometry import LineString as SLS
        return SLS(coords) if len(coords) >= 2 else None

    if local == "MultiLineString" or local == "MultiCurve":
        from shapely.geometry import MultiLineString
        parts = []
        for member in el:
            for ls in member.iter():
                if _localname(ls.tag) in ("LineString", "Curve"):
                    g = gml_to_shapely(ls)
                    if g is not None:
                        parts.append(g)
        return MultiLineString(parts) if parts else None

    if local in ("Polygon", "Surface", "PolygonPatch"):
        from shapely.geometry import Polygon
        exterior = None
        interiors = []
        for child in el:
            cl = _localname(child.tag)
            if cl in ("exterior", "outerBoundaryIs"):
                ring = _find_linear_ring(child)
                exterior = _ring_coords(ring) if ring is not None else []
            elif cl in ("interior", "innerBoundaryIs"):
                ring = _find_linear_ring(child)
                if ring is not None:
                    interiors.append(_ring_coords(ring))
            elif cl == "patches":
                for patch in child.iter():
                    if _localname(patch.tag) == "PolygonPatch":
                        return gml_to_shapely_patch(patch)
        if not exterior:
            return None
        return Polygon(exterior, interiors)

    if local in ("MultiPolygon", "MultiSurface"):
        from shapely.geometry import MultiPolygon
        polys = []
        for member in el:
            for p in member.iter():
                if _localname(p.tag) in ("Polygon", "PolygonPatch"):
                    g = gml_to_shapely(p)
                    if g is not None:
                        polys.append(g)
        return MultiPolygon(polys) if polys else None

    return None


def gml_to_shapely_patch(patch_el):
    return gml_to_shapely(patch_el)


def _find_linear_ring(el):
    for child in el.iter():
        if _localname(child.tag) == "LinearRing":
            return child
    return None


def axis_order_is_latlon(srs_name):
    """
    OGC-regel: URN-formen for geografiske EPSG-koder (4326, 4258, 4230 ...)
    har lat/lon-rekkefoelge. Plain "EPSG:xxxx"-form (og alt prosjisert, som
    25832) har lon/lat (easting/northing).
    """
    if not srs_name:
        return False
    m = re.search(r"EPSG[:>]{1,2}[:]?(\d+)$", srs_name.strip())
    if not m:
        return False
    code = m.group(1)
    is_urn = srs_name.strip().lower().startswith("urn:")
    return is_urn and code in {"4326", "4258", "4230"}


def fix_axis_order(geom):
    """Bytt om x/y hvis koordinatene ble tolket i lat/lon-rekkefoelge."""
    return shp_swap(geom)


def shp_swap(geom):
    from shapely.ops import transform as _t
    return _t(lambda x, y, z=None: (y, x), geom)


def _explicit_srs_name(el):
    """
    Let etter en eksplisitt srsName-attributt paa geometrielementet selv
    eller et hvilket som helst barn (ulike servere/GML-varianter legger den
    paa forskjellige nivaaer - LineString, posList, ...).
    """
    for e in el.iter():
        for k, v in e.attrib.items():
            if _localname(k) == "srsName":
                return v
    return None


def resolve_axis_swap(geom_el, requested_srs_name):
    """
    Avgjoer om en raapasrset GML-geometri maa byttes om fra
    (breddegrad, lengdegrad) til shapely/GeoJSON sin (x=lengdegrad,
    y=breddegrad) rekkefolge.

    Prioritet:
      1. Eksplisitt srsName PAA SELVE SVARET (geometrielementet) - mest
         autoritativt, forteller hva serveren faktisk kodet ut fra.
      2. srsName-en VI BA OM i forespoerselen - en spesifikasjonsfoelgende
         WFS skal kode svaret i den CRS-ens akserekkefolge.
      3. Ingen av delene funnet: default TRUE. Bekreftet empirisk mot
         Kartverkets WFS (--probe, 2026-08-30): SELV UTEN noen srsName i
         forespoerselen (variant A, "uten bbox") kom koordinatene i
         lat/lon-rekkefolge - det er tydeligvis denne tjenestens
         standardoppfoersel for EPSG:4326-geometri, ikke noe vi styrer.
    """
    srs = _explicit_srs_name(geom_el) or requested_srs_name
    if srs is not None:
        return axis_order_is_latlon(srs)
    return True


def validate_bounds(features, layer_key, sanity_bounds=SANITY_BOUNDS):
    """
    Fornuftssjekk (lat_min, lon_min, lat_max, lon_max): enhver geometri
    utenfor dette omraadet betyr med overveldende sannsynlighet feil
    akserekkefolge/CRS i GML-parsingen - IKKE at dataene faktisk ligger
    der. Feiler tydelig i stedet for aa skrive dem stille til fil.

    Motivert direkte av --probe-funn 2026-08-30: variant A (uten bbox)
    returnerte geometri ved Skagen/Danmark (57.76 N, 6.04 O) fordi
    tjenesten samplet fra hele det nasjonale datasettet uten spatial
    filtrering - IKKE en akserekkefolge-feil i seg selv, men akkurat den
    typen "et sted i Norge, men ikke der vi tror" som denne sjekken skal
    fange for de features som faktisk SKAL vaere i Ytre Oslofjord.
    """
    lat_min, lon_min, lat_max, lon_max = sanity_bounds
    bad = []
    for i, (geom, _props) in enumerate(features):
        minx, miny, maxx, maxy = geom.bounds  # x=lon, y=lat (etter evt. akse-fiks)
        if not (lon_min <= minx <= lon_max and lon_min <= maxx <= lon_max
                and lat_min <= miny <= lat_max and lat_min <= maxy <= lat_max):
            bad.append((i, geom.bounds))
    if bad:
        raise RuntimeError(
            f"{layer_key}: {len(bad)}/{len(features)} features har geometri utenfor "
            f"fornuftsomraadet (lat {lat_min}-{lat_max}, lon {lon_min}-{lon_max}). "
            f"Dette betyr sannsynligvis feil akserekkefolge/CRS i GML-parsingen, IKKE "
            f"at dataene faktisk ligger der. Eksempler (indeks, bounds lon/lat): {bad[:5]}"
        )


def _union_bounds(geoms):
    """Samlet bounding box (minx, miny, maxx, maxy) over en liste geometrier, eller None."""
    xs_min, ys_min, xs_max, ys_max = [], [], [], []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        minx, miny, maxx, maxy = g.bounds
        xs_min.append(minx)
        ys_min.append(miny)
        xs_max.append(maxx)
        ys_max.append(maxy)
    if not xs_min:
        return None
    return (min(xs_min), min(ys_min), max(xs_max), max(ys_max))


# --------------------------------------------------------------- pipeline


def detect_depth_property(props_list):
    if not props_list:
        return None
    keys = set()
    for p in props_list[:200]:
        keys.update(p.keys())
    for cand in DEPTH_PROPERTY_CANDIDATES:
        for k in keys:
            if k.lower() == cand:
                return k
    for cand in DEPTH_PROPERTY_CANDIDATES:
        for k in keys:
            if cand in k.lower():
                return k
    return None


def normalize_depth_properties(features):
    props_list = [p for _, p in features]
    depth_key = detect_depth_property(props_list)
    if depth_key is None:
        log(f"  ADVARSEL: fant ingen dybdeattributt blant {sorted(set().union(*[set(p) for p in props_list])) if props_list else '(ingen)'}")
        return features, None

    out = []
    n_ok = 0
    for geom, props in features:
        props = dict(props)
        try:
            props["depth_m"] = abs(float(props[depth_key]))
            n_ok += 1
        except (KeyError, TypeError, ValueError):
            pass
        out.append((geom, props))
    log(f"  dybdeattributt: '{depth_key}' -> depth_m satt paa {n_ok}/{len(out)} features")
    return out, depth_key


def simplify_all(features, tolerance_m):
    out = []
    for geom, props in features:
        g_utm = G.reproject_geom(geom, G.WGS84, G.UTM32)
        g_simpl = g_utm.simplify(tolerance_m, preserve_topology=True)
        g_back = G.reproject_geom(g_simpl, G.UTM32, G.WGS84)
        out.append((g_back, props))
    return out


def run_layer(layer_key, keywords, base_url, version, feature_types, bbox,
              tolerance_m, out_path, dump_dir):
    log(f"\n=== {layer_key} ===")
    type_name = match_layer(feature_types.keys(), keywords)
    if type_name is None:
        raise RuntimeError(
            f"Fant ikke noe lag som matcher {keywords} blant: "
            f"{sorted(feature_types.keys())}"
        )
    log(f"  lag: {type_name}")

    # JSON droppet - tjenesten svarer 400 "not configured to handle the
    # output/input format 'application/json'" (bekreftet --probe
    # 2026-08-30). Gaar rett paa GML, ingen formatsondering.
    feats = list(fetch_features_gml(base_url, version, type_name, bbox, dump_dir))

    log(f"  hentet {len(feats)} raa features")
    if not feats:
        raise RuntimeError(f"Fikk 0 features for {type_name} - se advarslene over")

    validate_bounds(feats, layer_key)

    feats, depth_key = normalize_depth_properties(feats) if layer_key == "dybdekurve" else (feats, None)

    n_coords_before = sum(_count_coords(g) for g, _ in feats)
    feats = simplify_all(feats, tolerance_m)
    n_coords_after = sum(_count_coords(g) for g, _ in feats)

    bounds = _union_bounds(g for g, _ in feats)
    log(f"  {len(feats)} features, bounding box (lon,lat) FOR skriving: {bounds}")

    out_path.parent.mkdir(exist_ok=True)
    G.write_geojson(out_path, feats)
    size_kb = out_path.stat().st_size / 1024

    log(f"  koordinater: {n_coords_before} -> {n_coords_after} etter simplify({tolerance_m} m)")
    log(f"  skrev {out_path} ({size_kb:.0f} kB)")
    return {
        "layer": type_name, "format": "gml",
        "n_features": len(feats), "n_coords_before": n_coords_before,
        "n_coords_after": n_coords_after, "size_kb": size_kb,
        "depth_key": depth_key, "bounds": bounds,
    }


def _count_coords(geom):
    if geom is None or geom.is_empty:
        return 0
    if hasattr(geom, "geoms"):
        return sum(_count_coords(g) for g in geom.geoms)
    if hasattr(geom, "exterior"):
        return len(geom.exterior.coords) + sum(len(r.coords) for r in geom.interiors)
    if hasattr(geom, "coords"):
        return len(geom.coords)
    return 0


def list_layers(args, dump_dir):
    candidates = build_candidates(args, dump_dir)
    log(f"Soeker WFS-endepunkt for --list-layers (kandidater: {candidates}) ...")
    base_url, version, feature_types = discover_service(candidates, dump_dir=dump_dir)
    log(f"\n{base_url} (WFS {version}) - {len(feature_types)} lag:")
    for name in sorted(feature_types):
        print(name)


# --------------------------------------------------------------- --probe


def _geom_sample_coords(geom, n=3):
    """
    Ren funksjon: de forste n koordinatparene i en shapely-geometri, uansett
    type - brukt til aa vise raadata for CRS/akserekkefolge-diagnose.

    OBS: hasattr(geom, "coords") er SANN for alle shapely-geometrier (feltet
    finnes paa baseklassen) - men aa faktisk LESE .coords kaster
    NotImplementedError for Polygon/Multi*/GeometryCollection, siden de ikke
    er "koordinatsekvenser". Maa derfor proeve/fange, ikke bare hasattr().
    """
    if geom is None:
        return None
    try:
        coords = list(geom.coords)
        if coords:
            return coords[:n]
    except NotImplementedError:
        pass
    if hasattr(geom, "exterior") and geom.exterior is not None:
        return list(geom.exterior.coords)[:n]
    if hasattr(geom, "geoms") and len(geom.geoms):
        return _geom_sample_coords(geom.geoms[0], n)
    return None


def _guess_crs_hint(bounds):
    """Ren funksjon: svakt, uautoritativt hint om hva slags CRS et sett
    bounds sannsynligvis er i, ut fra rene stoerrelsesordener. Kun til hjelp
    i loggen - ikke brukt til noen beslutning i koden."""
    if not bounds:
        return ""
    minx, miny, maxx, maxy = bounds
    if 4 <= minx <= 32 and 4 <= maxx <= 32 and 55 <= miny <= 72 and 55 <= maxy <= 72:
        return "  <- ser ut som WGS84 lon,lat over Norge (riktig!)"
    if 55 <= minx <= 72 and 55 <= maxx <= 72 and 4 <= miny <= 32 and 4 <= maxy <= 32:
        return "  <- ser ut som WGS84 MEN med lat/lon byttet om (x=breddegrad her)"
    if abs(minx) > 1000 or abs(miny) > 1000:
        return "  <- ser ut som prosjiserte meterkoordinater (UTM e.l.)"
    return "  <- usikker, ikke i noen av de forventede omraadene"


def _probe_call(base_url, version, type_name, use_json, dump_dir, label, params_extra, count=10):
    """
    Ett enkelt, ikke-paginert GetFeature-kall for --probe: bygg params,
    hent, tell features, og vis geometrien til den forste - saa en kan se
    med egne oyne hvilket koordinatsystem/akserekkefolge svaret faktisk
    kommer i, i stedet for aa gjette videre.
    """
    params = {
        "service": "WFS", "request": "GetFeature", "version": version,
        ("typeNames" if version == "2.0.0" else "typeName"): type_name,
        ("count" if version == "2.0.0" else "maxFeatures"): count,
    }
    if use_json:
        params["outputFormat"] = "application/json"
    params.update(params_extra)

    tag = f"probe_{type_name}_{_slug(label)}"
    try:
        r = _get(base_url, params, dump_dir=dump_dir, tag=tag)
    except requests.RequestException as exc:
        log(f"  [{label}] FEIL: {exc}")
        return 0

    geoms = []
    if use_json:
        try:
            data = r.json()
        except ValueError:
            log(f"  [{label}] status {r.status_code}, ugyldig JSON")
            return 0
        for f in data.get("features", []):
            g = f.get("geometry")
            if g is not None:
                geoms.append(shape(g))
    else:
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as exc:
            log(f"  [{label}] status {r.status_code}, ugyldig XML: {exc}")
            return 0
        requested_srs = params_extra.get("srsName")
        for member in _iter_members(root):
            g, _props = _parse_gml_member(member, requested_srs_name=requested_srs)
            if g is not None:
                geoms.append(g)

    n = len(geoms)
    if n == 0:
        log(f"  [{label}] 0 features")
        return 0

    sample = _geom_sample_coords(geoms[0])
    hint = _guess_crs_hint(geoms[0].bounds)
    log(f"  [{label}] {n} features. Forste geometri: type={geoms[0].geom_type}, "
        f"koordinater={sample}, bounds={geoms[0].bounds}{hint}")
    return n


def probe(args, dump_dir):
    """
    --probe: diagnostiser bbox/akserekkefolge/projeksjon UTEN aa laste ned
    og skrive noe. Rekkefolge (etter brukerens instruks - gjor "uten bbox"
    forst, det halverer soekerommet raskest):
      A. count=10, INGEN bbox i det hele tatt - er tjenesten frisk?
      B. bbox lon,lat med srsName=EPSG:4326 (kortform, vanlig lon/lat-akse)
      C. bbox lat,lon med srsName=urn:ogc:def:crs:EPSG::4326 (WFS2.0-regelen
         for geografiske EPSG-koder: lat/lon-akse)
      D. bbox i UTM33 (EPSG:25833), siden norske geodata ofte er native der
    """
    candidates = build_candidates(args, dump_dir)
    log(f"--probe: soeker WFS-endepunkt (kandidater: {candidates}) ...")
    base_url, version, feature_types = discover_service(candidates, dump_dir=dump_dir)
    log(f"Bruker {base_url} (WFS {version})\n")

    lat_min, lon_min, lat_max, lon_max = args.bbox
    bbox_lonlat = f"{lon_min},{lat_min},{lon_max},{lat_max}"
    bbox_latlon = f"{lat_min},{lon_min},{lat_max},{lon_max}"

    e1, n1 = G.transform_point(lon_min, lat_min, "EPSG:25833")
    e2, n2 = G.transform_point(lon_min, lat_max, "EPSG:25833")
    e3, n3 = G.transform_point(lon_max, lat_min, "EPSG:25833")
    e4, n4 = G.transform_point(lon_max, lat_max, "EPSG:25833")
    e_min, e_max = min(e1, e2, e3, e4), max(e1, e2, e3, e4)
    n_min, n_max = min(n1, n2, n3, n4), max(n1, n2, n3, n4)
    bbox_utm33 = f"{e_min:.1f},{n_min:.1f},{e_max:.1f},{n_max:.1f}"
    log(f"bbox (lat/lon) {args.bbox} -> UTM33 {bbox_utm33}\n")

    any_hit = False
    for key, keywords in LAYERS.items():
        type_name = match_layer(feature_types.keys(), keywords)
        if type_name is None:
            log(f"=== {key}: FEIL - fant ikke noe lag som matcher {keywords} blant "
                f"{sorted(feature_types.keys())} ===\n")
            continue

        log(f"=== {key} ({type_name}) ===")
        use_json = supports_json(base_url, version, type_name, dump_dir=dump_dir)
        log(f"  format: {'GeoJSON' if use_json else 'GML'}")

        n_a = _probe_call(base_url, version, type_name, use_json, dump_dir,
                           "A: uten bbox", {})
        n_b = _probe_call(base_url, version, type_name, use_json, dump_dir,
                           "B: bbox lon,lat EPSG:4326",
                           {"bbox": bbox_lonlat, "srsName": "EPSG:4326"})
        n_c = _probe_call(base_url, version, type_name, use_json, dump_dir,
                           "C: bbox lat,lon urn:...:EPSG::4326",
                           {"bbox": bbox_latlon, "srsName": "urn:ogc:def:crs:EPSG::4326"})
        n_d = _probe_call(base_url, version, type_name, use_json, dump_dir,
                           "D: bbox UTM33 EPSG:25833",
                           {"bbox": bbox_utm33, "srsName": "EPSG:25833"})
        log("")
        any_hit = any_hit or any([n_a, n_b, n_c, n_d])

    log("=" * 70)
    if not any_hit:
        log("KONKLUSJON: 0 features i ALLE varianter, ogsaa uten bbox (A). "
            "Problemet er ikke bbox-en - det er lagnavnet, tjenesten, eller "
            "noe annet. Sjekk --list-layers og data/_raw/ for de raa svarene.")
    else:
        log("KONKLUSJON: se hvilken(e) variant(er) over som faktisk ga treff, "
            "og hvilket omraade 'bounds' pekte paa - det forteller hvilken "
            "bbox-rekkefolge/CRS tjenesten faktisk vil ha.")
    log("=" * 70)


def run_pipeline(args, out_dir, dump_dir):
    candidates = build_candidates(args, dump_dir)
    log(f"Soeker WFS-endepunkt (bbox={args.bbox}, kandidater: {candidates}) ...")
    base_url, version, feature_types = discover_service(candidates, dump_dir=dump_dir)
    log(f"Bruker {base_url} (WFS {version})")
    log(f"Tilgjengelige lag: {sorted(feature_types.keys())}")

    report = {}
    for key, keywords in LAYERS.items():
        out_path = out_dir / f"{key}.geojson"
        try:
            report[key] = run_layer(
                key, keywords, base_url, version, feature_types, args.bbox,
                args.tolerance_m, out_path, dump_dir,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  FEIL for {key}: {exc}")
            report[key] = {"error": str(exc)}

    log("\n" + "=" * 70)
    log("RAPPORT")
    log("=" * 70)
    log(f"WFS: {base_url} (versjon {version})")
    for key, r in report.items():
        log(f"\n{key}:")
        for k, v in r.items():
            log(f"  {k}: {v}")

    if any("error" in r for r in report.values()):
        raise RuntimeError(
            "Ett eller flere lag feilet - se RAPPORT over og data/_raw/ for hvert HTTP-kall som ble gjort."
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("LAT_MIN", "LON_MIN", "LAT_MAX", "LON_MAX"),
                     default=DEFAULT_BBOX)
    ap.add_argument("--tolerance-m", type=float, default=DEFAULT_TOLERANCE_M)
    ap.add_argument("--wfs-url", default=None,
                     help="Overstyr WFS-base-URL helt (hopper over kartkatalog-oppslaget). "
                          "Bruk denne hvis oppslaget mot kartkatalog.geonorge.no selv gir feil URL.")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--list-layers", action="store_true",
                     help="Bare kjor GetCapabilities og skriv ut alle tilgjengelige lagnavn, ikke hent features")
    ap.add_argument("--probe", action="store_true",
                     help="Diagnostiser bbox-rekkefolge/akse/projeksjon mot layerne (uten bbox, "
                          "lon/lat, lat/lon, UTM33) - ikke last ned eller skriv noe")
    ap.add_argument("--dump-raw", action="store_true",
                     help="Ingen effekt lenger - raadata dumpes na alltid til <out-dir>/_raw/. "
                          "Beholdt for bakoverkompatibilitet med eksisterende kall.")
    args = ap.parse_args()

    if args.dump_raw:
        log("Merk: --dump-raw er na alltid paa (hvert HTTP-kall dumpes uansett) - "
            "flagget selv gjor ikke lenger noe.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    dump_dir = out_dir / "_raw"
    dump_dir.mkdir(exist_ok=True)

    try:
        if args.list_layers:
            list_layers(args, dump_dir)
        elif args.probe:
            probe(args, dump_dir)
        else:
            run_pipeline(args, out_dir, dump_dir)
    except Exception as exc:  # noqa: BLE001
        log("\n" + "=" * 70)
        log("FEIL - skriptet stoppet uten aa fullfore")
        log("=" * 70)
        log(f"{type(exc).__name__}: {exc}")
        log(f"Hvert HTTP-kall som ble forsokt ligger i {dump_dir}/ "
            f"(NNN_<hva>.meta.txt + NNN_<hva>.<json|xml|bin>) - se der for full URL, "
            f"statuskode/unntak og respons per forsok.")
        sys.exit(1)


if __name__ == "__main__":
    main()
