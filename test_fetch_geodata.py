"""
Enhetstester for de rene parse-funksjonene i fetch_geodata.py: GML->shapely,
akserekkefolge og dybdeattributt-gjenkjenning. Ingen nettverk involvert -
XML-utdragene er haandskrevet etter OGC GML/WFS-spesifikasjonen, IKKE hentet
fra den ekte tjenesten (som var utilgjengelig fra utviklingsmiljoet). Kjor
disse pa nytt mot ekte raadata (--dump-raw) foerste gang fetch_geodata.py
kjores mot Kartverket, og juster om noe her ikke stemmer med virkeligheten.
"""

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("shapely")

import fetch_geodata as F


def test_gml_linestring_poslist():
    xml = """<gml:LineString xmlns:gml="http://www.opengis.net/gml">
      <gml:posList srsDimension="2">9.5 58.8 9.6 58.85 9.7 58.9</gml:posList>
    </gml:LineString>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert g.geom_type == "LineString"
    assert list(g.coords) == [(9.5, 58.8), (9.6, 58.85), (9.7, 58.9)]


def test_gml_polygon_legacy_coordinates():
    xml = """<gml:Polygon xmlns:gml="http://www.opengis.net/gml">
      <gml:exterior><gml:LinearRing>
        <gml:coordinates>9.0,58.0 9.1,58.0 9.1,58.1 9.0,58.1 9.0,58.0</gml:coordinates>
      </gml:LinearRing></gml:exterior>
    </gml:Polygon>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert g.geom_type == "Polygon"
    assert g.exterior.coords[0] == (9.0, 58.0)
    assert len(list(g.exterior.coords)) == 5


def test_gml_multicurve():
    xml = """<gml:MultiCurve xmlns:gml="http://www.opengis.net/gml">
      <gml:curveMember><gml:LineString><gml:posList>9.0 58.0 9.1 58.1</gml:posList></gml:LineString></gml:curveMember>
      <gml:curveMember><gml:LineString><gml:posList>9.2 58.2 9.3 58.3</gml:posList></gml:LineString></gml:curveMember>
    </gml:MultiCurve>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert g.geom_type == "MultiLineString"
    assert len(g.geoms) == 2


def test_gml_srs_dimension_3_ignorerer_z():
    xml = """<gml:LineString xmlns:gml="http://www.opengis.net/gml">
      <gml:posList srsDimension="3">9.5 58.8 0 9.6 58.85 0</gml:posList>
    </gml:LineString>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert list(g.coords) == [(9.5, 58.8), (9.6, 58.85)]


def test_axis_order_urn_geografisk_er_latlon():
    assert F.axis_order_is_latlon("urn:ogc:def:crs:EPSG::4326") is True
    assert F.axis_order_is_latlon("urn:ogc:def:crs:EPSG::4258") is True


def test_axis_order_plain_epsg_er_lonlat():
    assert F.axis_order_is_latlon("EPSG:4326") is False
    assert F.axis_order_is_latlon("EPSG:25832") is False


def test_axis_order_urn_projisert_er_lonlat():
    assert F.axis_order_is_latlon("urn:ogc:def:crs:EPSG::25832") is False


def test_iter_members_og_parse_member():
    xml = """<?xml version="1.0"?>
    <wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
                            xmlns:gml="http://www.opengis.net/gml/3.2"
                            xmlns:app="http://example.org/app">
      <wfs:member>
        <app:Dybdekurve gml:id="dk.1">
          <app:dybde>20.0</app:dybde>
          <app:geometri><gml:LineString><gml:posList>9.40 58.90 9.45 58.95</gml:posList></gml:LineString></app:geometri>
        </app:Dybdekurve>
      </wfs:member>
    </wfs:FeatureCollection>"""
    root = ET.fromstring(xml)
    members = list(F._iter_members(root))
    assert len(members) == 1
    geom, props = F._parse_gml_member(members[0])
    assert geom.geom_type == "LineString"
    assert props["dybde"] == "20.0"


def test_detect_depth_property_finner_norsk_navn():
    props = [{"dybde": "20", "objtype": "Dybdekurve"}]
    assert F.detect_depth_property(props) == "dybde"


def test_detect_depth_property_ingen_treff():
    props = [{"objtype": "Kystkontur", "navn": "x"}]
    assert F.detect_depth_property(props) is None


def test_normalize_depth_properties_setter_depth_m():
    from shapely.geometry import LineString
    feats = [
        (LineString([(9, 58), (9.1, 58.1)]), {"dybde": "20"}),
        (LineString([(9, 58), (9.1, 58.1)]), {"dybde": "-30"}),  # negativ dyp-konvensjon
    ]
    out, key = F.normalize_depth_properties(feats)
    assert key == "dybde"
    assert out[0][1]["depth_m"] == 20.0
    assert out[1][1]["depth_m"] == 30.0  # abs()


def test_match_layer_eksakt_foretrekkes_over_delvis():
    names = ["app:Kystkontur", "app:Kystkontur_grovt"]
    assert F.match_layer(names, ["kystkontur", "kyst"]) == "app:Kystkontur"


def test_match_layer_delvis_naar_ikke_eksakt():
    names = ["app:DybdekurveGrunnkart"]
    assert F.match_layer(names, ["dybdekurve"]) == "app:DybdekurveGrunnkart"


def test_match_layer_ingen_treff():
    assert F.match_layer(["app:Noe_annet"], ["dybdekurve"]) is None
