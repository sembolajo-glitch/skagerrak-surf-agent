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
Vi kjenner ikke det eksakte WFS-endepunktet eller lagnavnene paa forhaand,
saa skriptet:
  1. proever en liste kandidat-URL-er + WFS-versjoner og kjoerer
     GetCapabilities paa hver, til en svarer.
  2. leser de faktiske FeatureType-navnene fra svaret og matcher dem mot
     "kyst"/"dybde"-noekkelord i stedet for aa anta faste navn.
  3. proever outputFormat=application/json foerst; faller tilbake til GML
     (standard WFS-output) hvis tjenesten ikke stoetter JSON.
  4. paginerer med count/startIndex (WFS 2.0.0) eller maxFeatures
     (1.0.0/1.1.0), og stopper naar en side gir faerre features enn spurt.

Alt dette er IKKE testet mot den levende tjenesten (naettverket i
utviklingsmiljoeet hvor dette ble skrevet er sperret mot geonorge.no).

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

WFS_URL_CANDIDATES = [
    "https://wfs.geonorge.no/skwms1/wfs.dybdedata2",
    "https://openwfs.geonorge.no/skwms1/wfs.dybdedata2",
    "https://wms.geonorge.no/skwms1/wfs.dybdedata2",
]

WFS_VERSIONS = ["2.0.0", "1.1.0", "1.0.0"]

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


# ------------------------------------------------------------ GetCapabilities


def discover_service(url_candidates, extra_url=None, dump_dir=None):
    """
    Proev kandidat-URL-er x versjoner til GetCapabilities svarer.
    Returnerer (base_url, version, {layer_name: xml_element}).
    """
    candidates = ([extra_url] if extra_url else []) + list(url_candidates)
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
    """Fallback naar tjenesten ikke gir GeoJSON. Parser raa GML."""
    lat_min, lon_min, lat_max, lon_max = bbox
    bbox_param = f"{lon_min},{lat_min},{lon_max},{lat_max}"

    start = 0
    page_no = 0
    while True:
        params = {
            "service": "WFS", "request": "GetFeature", "version": version,
            ("typeNames" if version == "2.0.0" else "typeName"): type_name,
            "srsName": "EPSG:4326",
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
            geom, props = _parse_gml_member(member)
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


def _parse_gml_member(member):
    """Et wfs:member inneholder ett feature-element med geometri + attributter."""
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

    use_json = supports_json(base_url, version, type_name, dump_dir=dump_dir)
    log(f"  format: {'GeoJSON' if use_json else 'GML (fallback)'}")

    if use_json:
        feats = list(fetch_features_json(base_url, version, type_name, bbox, dump_dir))
    else:
        raw = list(fetch_features_gml(base_url, version, type_name, bbox, dump_dir))
        # GML-koordinatrekkefoelgen avhenger av srsName paa selve elementet;
        # vi ba om srsName=EPSG:4326 (plain), som er lon/lat - ingen bytte
        # trengs normalt. Hvis punktene havner i havet ved Afrika, er dette
        # stedet aa bytte om: sett feats = [(fix_axis_order(g), p) for g, p in raw].
        feats = raw

    log(f"  hentet {len(feats)} raa features")
    if not feats:
        raise RuntimeError(f"Fikk 0 features for {type_name} - se advarslene over")

    feats, depth_key = normalize_depth_properties(feats) if layer_key == "dybdekurve" else (feats, None)

    n_coords_before = sum(_count_coords(g) for g, _ in feats)
    feats = simplify_all(feats, tolerance_m)
    n_coords_after = sum(_count_coords(g) for g, _ in feats)

    out_path.parent.mkdir(exist_ok=True)
    G.write_geojson(out_path, feats)
    size_kb = out_path.stat().st_size / 1024

    log(f"  koordinater: {n_coords_before} -> {n_coords_after} etter simplify({tolerance_m} m)")
    log(f"  skrev {out_path} ({size_kb:.0f} kB)")
    return {
        "layer": type_name, "format": "json" if use_json else "gml",
        "n_features": len(feats), "n_coords_before": n_coords_before,
        "n_coords_after": n_coords_after, "size_kb": size_kb,
        "depth_key": depth_key,
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
    log(f"Soeker WFS-endepunkt for --list-layers (kandidater: "
        f"{([args.wfs_url] if args.wfs_url else []) + WFS_URL_CANDIDATES}) ...")
    base_url, version, feature_types = discover_service(
        WFS_URL_CANDIDATES, extra_url=args.wfs_url, dump_dir=dump_dir,
    )
    log(f"\n{base_url} (WFS {version}) - {len(feature_types)} lag:")
    for name in sorted(feature_types):
        print(name)


def run_pipeline(args, out_dir, dump_dir):
    log(f"Soeker WFS-endepunkt (bbox={args.bbox}) ...")
    base_url, version, feature_types = discover_service(
        WFS_URL_CANDIDATES, extra_url=args.wfs_url, dump_dir=dump_dir,
    )
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
    ap.add_argument("--wfs-url", default=None, help="Override/legg til kandidat-URL foerst i lista")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--list-layers", action="store_true",
                     help="Bare kjor GetCapabilities og skriv ut alle tilgjengelige lagnavn, ikke hent features")
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
