"""
Delte geometrihjelpere for fetch_geodata.py og build_fetch.py.

Alt her jobber i to CRS:
  - EPSG:4326 (WGS84 lon/lat) - lagringsformatet i GeoJSON-filene
  - EPSG:25832 (UTM sone 32N) - metrisk CRS for straaleskyting og
    forenkling. Hele bbox (9.3-11.2 O) ligger godt innenfor sone 32
    (6-12 O), saa en enkelt sone gir lav forvrengning over hele omraadet.

Bearing-konvensjon overalt: meteorologisk/kompass, 0 = nord, med klokka.
"""

import functools
import json
import math

import pyproj
from shapely.geometry import shape, mapping, LineString, Point, box
from shapely.ops import transform as shp_transform
from shapely.strtree import STRtree

WGS84 = "EPSG:4326"
UTM32 = "EPSG:25832"


@functools.lru_cache(maxsize=8)
def _transformer(from_crs, to_crs):
    return pyproj.Transformer.from_crs(from_crs, to_crs, always_xy=True)


def reproject_geom(geom, from_crs, to_crs):
    if from_crs == to_crs or geom is None:
        return geom
    t = _transformer(from_crs, to_crs)
    return shp_transform(t.transform, geom)


def to_utm(lon, lat):
    x, y = _transformer(WGS84, UTM32).transform(lon, lat)
    return x, y


def to_wgs84_xy(x, y):
    lon, lat = _transformer(UTM32, WGS84).transform(x, y)
    return lon, lat


def transform_point(lon, lat, to_crs, from_crs=WGS84):
    """Generisk punkt-transform til en vilkaarlig EPSG-kode (f.eks. for
    engangsdiagnostikk mot en annen projeksjon enn UTM32)."""
    x, y = _transformer(from_crs, to_crs).transform(lon, lat)
    return x, y


def bearing_vector(bearing_deg):
    """Enhetsvektor (dx, dy) i et easting/northing-plan for en kompassretning."""
    rad = math.radians(bearing_deg)
    return math.sin(rad), math.cos(rad)


# ------------------------------------------------------------- GeoJSON I/O


def load_geojson(path):
    """Les en FeatureCollection. Returnerer liste av (shapely_geom, properties)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for feat in data.get("features", []):
        geom = feat.get("geometry")
        if geom is None:
            continue
        out.append((shape(geom), feat.get("properties", {}) or {}))
    return out


def write_geojson(path, features_geom_props, crs_name="urn:ogc:def:crs:OGC:1.3:CRS84"):
    """Skriv en FeatureCollection. features_geom_props: liste av (shapely_geom, dict)."""
    out = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs_name}},
        "features": [
            {"type": "Feature", "geometry": mapping(geom), "properties": props}
            for geom, props in features_geom_props
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------- kystlinje/boundary


def to_boundary_lines(geoms):
    """
    Reduser en liste shapely-geometrier til linjestykker vi kan skyte straaler
    mot. Polygoner -> boundary (yttergrense + hull), linjer beholdes som de er.
    """
    lines = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        gt = g.geom_type
        if gt in ("Polygon", "MultiPolygon"):
            b = g.boundary
            lines.extend(_flatten_lines(b))
        elif gt in ("LineString", "MultiLineString", "LinearRing"):
            lines.extend(_flatten_lines(g))
        else:
            continue
    return lines


def _flatten_lines(geom):
    if geom.geom_type == "LineString" or geom.geom_type == "LinearRing":
        return [LineString(geom.coords)]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        out = []
        for part in geom.geoms:
            out.extend(_flatten_lines(part))
        return out
    return []


def build_strtree(line_geoms):
    if not line_geoms:
        return None
    return STRtree(line_geoms)


def cast_ray_km(origin_lon, origin_lat, bearing_deg, max_km, tree, line_geoms):
    """
    Skyt en straale fra (origin_lon, origin_lat) i retning bearing_deg (kompass,
    0=N, med klokka), returner avstand i km til naermeste skjaering med
    linjene i `tree`/`line_geoms`, eller max_km hvis ingen skjaering.

    `tree` maa vaere bygget over `line_geoms` i UTM32 (meter). Origin gis i
    WGS84 og projiseres internt.
    """
    if tree is None:
        return float(max_km)

    ox, oy = to_utm(origin_lon, origin_lat)
    dx, dy = bearing_vector(bearing_deg)
    max_m = max_km * 1000.0
    ray = LineString([(ox, oy), (ox + dx * max_m, oy + dy * max_m)])

    # predicate="intersects" pushes the precise (not just bbox) filtering
    # into GEOS/C, instead of a Python-level ray.intersects() per candidate -
    # matters a lot here: a query bbox for a long ray through a dense skerry
    # cluster can otherwise pull in thousands of candidates per ray.
    idx = tree.query(ray, predicate="intersects")
    best_m = None
    for i in idx:
        cand = line_geoms[i]
        inter = ray.intersection(cand)
        for pt in _iter_points(inter):
            d = math.hypot(pt[0] - ox, pt[1] - oy)
            if best_m is None or d < best_m:
                best_m = d

    if best_m is None:
        return float(max_km)
    return best_m / 1000.0


def ray_crossing_count(origin_lon, origin_lat, bearing_deg, max_km, tree, line_geoms):
    """
    Tell antall kryssinger mellom en straale fra (origin_lon, origin_lat) i
    retning bearing_deg og linjene i `tree`/`line_geoms`, opptil max_km.

    Brukt til en paritetstest ("hvilken side av kystlinja ligger punktet
    paa" - se build_spotgrid.py sin in_sea()): kystkontur-laget er kun
    linjestykker, ikke lukkede polygoner, saa et vanlig point-in-polygon-
    oppslag er ikke mulig. Et ODDETALL kryssinger langs en fast straale mot
    et punkt med kjent klassifisering betyr punktet ligger paa MOTSATT side
    av det punktet; et PARTALL (inkludert null) betyr SAMME side.

    `tree` maa vaere bygget over `line_geoms` i UTM32 (meter). Origin gis i
    WGS84 og projiseres internt.
    """
    if tree is None:
        return 0

    ox, oy = to_utm(origin_lon, origin_lat)
    dx, dy = bearing_vector(bearing_deg)
    max_m = max_km * 1000.0
    ray = LineString([(ox, oy), (ox + dx * max_m, oy + dy * max_m)])

    idx = tree.query(ray, predicate="intersects")
    count = 0
    for i in idx:
        inter = ray.intersection(line_geoms[i])
        gt = inter.geom_type
        if gt == "MultiPoint":
            count += len(inter.geoms)
        else:
            # "Point" (vanlige tilfellet), eller et sjeldent kollineaert
            # linjeoverlapp - telles som en kryssing, godt nok for en
            # paritetstest.
            count += 1
    return count


def nearest_distance_km(lon, lat, tree, line_geoms):
    """
    Korteste avstand fra et punkt til naermeste linje i `tree`/`line_geoms`,
    i km. I motsetning til cast_ray_km (retningsavhengig, straaleskyting)
    er dette en ren "avstand til naermeste kystlinje uansett retning" -
    brukt til aa validere mot referansepunkter med kjent avstand til land.

    `tree` maa vaere bygget over `line_geoms` i UTM32 (meter). Punktet gis
    i WGS84 og projiseres internt. Returnerer None hvis treet er tomt.
    """
    if tree is None or not line_geoms:
        return None
    x, y = to_utm(lon, lat)
    pt = Point(x, y)
    idx = tree.nearest(pt)
    return pt.distance(line_geoms[idx]) / 1000.0


def bbox_edge_tree(bbox):
    """
    bbox = (lat_min, lon_min, lat_max, lon_max). Bygg en STRtree over
    bbox-rektangelets fire kanter i UTM. Brukt til aa finne avstanden til
    kanten av et nedlastet datautsnitt i en gitt retning (via cast_ray_km
    mot dette treet) - skiller "straalen fant ekte land" fra "straalen gikk
    tom fordi dataene ikke daekker lenger i den retningen".
    """
    lat_min, lon_min, lat_max, lon_max = bbox
    poly = box(lon_min, lat_min, lon_max, lat_max)
    poly_utm = reproject_geom(poly, WGS84, UTM32)
    lines = to_boundary_lines([poly_utm])
    return build_strtree(lines), lines


def _iter_points(geom):
    if geom.is_empty:
        return
    gt = geom.geom_type
    if gt == "Point":
        yield (geom.x, geom.y)
    elif gt in ("MultiPoint", "GeometryCollection"):
        for part in geom.geoms:
            yield from _iter_points(part)
    elif gt in ("LineString", "LinearRing"):
        for c in geom.coords:
            yield c
    elif gt == "MultiLineString":
        for part in geom.geoms:
            yield from _iter_points(part)
    # punkter-langs-linje daekker alle praktiske skjaeringstilfeller her
